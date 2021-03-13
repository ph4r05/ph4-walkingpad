#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import binascii
import json
import logging
import sys
import threading
from collections import OrderedDict
from typing import Optional

import coloredlogs

from ph4_walkingpad.cmd import Ph4Cmd
from ph4_walkingpad.pad import Scanner, WalkingPad, WalkingPadCurStatus, WalkingPadLastStatus, Controller
from ph4_walkingpad.profile import Profile, calories_walk2_minute, calories_rmrcb_minute
from ph4_walkingpad.reader import reverse_file

logger = logging.getLogger(__name__)
coloredlogs.CHROOT_FILES = []
coloredlogs.install(level=logging.INFO)


class WalkingPadControl(Ph4Cmd):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.args = None
        self.args_src = None
        self.ctler = None  # type: Optional[Controller]
        self.profile = None
        self.loaded_margins = []

        self.worker_thread = None
        self.stats_thread = None
        self.stats_loop = None
        self.stats_task = None
        self.stats_collecting = False
        self.asked_status = False
        self.asked_status_beep = False

        self.last_speed = 0
        self.last_speed_change_rec = None  # type: Optional[WalkingPadCurStatus]
        self.last_time_steps = (0, 0)
        self.cur_cal = 0
        self.cur_cal_net = 0
        self.calorie_acc = []
        self.calorie_acc_net = []

    def __del__(self):
        self.submit_coro(self.disconnect())

    async def disconnect(self):
        logger.debug("Disconnecting coroutine")
        if self.ctler:
            await self.ctler.disconnect()

    async def connect(self, address):
        self.ctler = Controller(address=address, do_read_chars=False)
        self.ctler.log_messages_info = self.args.cmd
        self.ctler.handler_cur_status = self.on_status
        self.ctler.handler_last_status = self.on_last_record

        await self.ctler.run()
        await asyncio.sleep(1.5)  # needs to sleep a bit

        await self.ctler.ask_profile()
        await asyncio.sleep(1.5)
        await self.ask_beep()
        await asyncio.sleep(1.0)

    async def work(self):
        self.worker_loop = asyncio.new_event_loop()
        self.worker_thread = threading.Thread(
            target=self.looper, args=(self.worker_loop,)
        )
        self.worker_thread.setDaemon(True)
        self.worker_thread.start()

        address = await self.scan_address()
        if self.args.scan:
            return

        await self.connect(address)
        # await asyncio.wait_for(self.connect(address), None, loop=self.worker_loop)

        if self.args.stats:
            self.start_stats_fetching()

        res = None
        if not self.args.cmd:
            sys.argv = [self.args_src[0]]
            res = await self.entry()
            sys.argv = self.args_src

        if self.args.stats:
            try:
                while self.cmd_running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt as e:
                print("Terminating")

        self.stats_collecting = False
        await asyncio.sleep(1)

        logger.info('Terminating')
        return res

    async def scan_address(self):
        address = self.args.address
        if not address or self.args.scan:
            scanner = Scanner()
            await scanner.scan()

            if scanner.walking_belt_candidates:
                logger.info("WalkingPad candidates: %s" % (scanner.walking_belt_candidates,))
                if self.args.scan:
                    return
                address = scanner.walking_belt_candidates[0].address
        return address

    def init_stats_fetcher(self):
        self.stats_loop = asyncio.new_event_loop()
        self.stats_thread = threading.Thread(
            target=self.looper, args=(self.stats_loop,)
        )
        self.stats_thread.setDaemon(True)
        self.stats_thread.start()

    def start_stats_fetching(self):
        if self.stats_thread is None:
            self.init_stats_fetcher()

        logger.info("Starting stats fetching")
        self.stats_collecting = True
        self.submit_coro(self.stats_fetcher(), self.stats_loop)

    async def stats_fetcher(self):
        while self.stats_collecting:
            try:
                # await asyncio.wait_for(self.ctler.ask_stats(), None, loop=self.worker_loop)
                await self.ctler.ask_stats()
                await asyncio.sleep(max(500, self.args.stats or 0)/1000.0)
            except Exception as e:
                logger.info("Error in ask stats: %s" % (e,))

    async def entry(self):
        self.intro = (
                "-" * self.get_term_width()
                + "\n    WalkingPad controller\n"
                + "\n"
                + "-" * self.get_term_width()
        )

        await self.acmdloop()

    def on_status(self, sender, status: WalkingPadCurStatus):
        # Calories computation with respect to the last segment of the same speed.
        if self.last_time_steps[0] > status.time \
                or self.last_time_steps[1] > status.steps:
            logger.debug('Resetting calorie measurements')
            self.last_time_steps = (status.time, status.steps)
            self.last_speed_change_rec = None
            self.last_speed = 0
            self.cur_cal = 0
            self.calorie_acc = []
            self.calorie_acc_net = []

        if not self.last_speed_change_rec:
            self.compute_initial_cal(status)

        el_time, el_dist = 0, 0
        if self.profile:
            el_time = status.time - self.last_speed_change_rec.time
            el_dist = status.dist - self.last_speed_change_rec.dist

        ccal, ccal_net, ccal_sum, ccal_net_sum = None, None, None, None
        if el_time > 0 and el_dist > 0:
            ccal = (el_time/60) * calories_walk2_minute(self.last_speed_change_rec.speed/10., self.profile.weight, 0.00)
            ccal_net = ccal - (el_time/60) * calories_rmrcb_minute(self.profile.weight, self.profile.height,
                                                                   self.profile.age, self.profile.male)
            ccal_sum = sum(self.calorie_acc) + ccal
            ccal_net_sum = sum(self.calorie_acc_net) + ccal_net
            self.cur_cal = ccal
            self.cur_cal_net = ccal_net

        # on speed change accumulate calories and move to a new speed
        # with a new status.
        if self.last_speed_change_rec.speed != status.speed:
            if self.cur_cal:
                self.calorie_acc.append(self.cur_cal)
            if self.cur_cal_net:
                self.calorie_acc_net.append(self.cur_cal_net)

            self.cur_cal = 0
            self.cur_cal_net = 0
            self.last_speed_change_rec = status

        ccal_str = ''
        if ccal:
            ccal_str = ', cal: %6.2f, net: %6.2f, total: %6.2f, total net: %6.2f' \
                       % (ccal, ccal_net, ccal_sum, ccal_net_sum)

        if self.asked_status:
            self.asked_status = False
            print(str(status) + ccal_str)

        elif self.asked_status_beep:
            self.asked_status_beep = False
            print(str(status) + ccal_str)

        if not self.args.json_file:
            return

        js = OrderedDict()
        js["time"] = status.time
        js["dist"] = status.dist
        js["steps"] = status.steps
        js["speed"] = status.speed
        js["app_speed"] = status.app_speed
        js["belt_state"] = status.belt_state
        js["controller_button"] = status.controller_button
        js["manual_mode"] = status.manual_mode
        js["raw"] = binascii.hexlify(status.raw).decode('utf8')
        js["rec_time"] = status.rtime
        js["pid"] = self.profile.pid if self.profile else None
        js["ccal"] = round(ccal*1000)/1000 if ccal else None
        js["ccal_net"] = round(ccal_net*1000)/1000 if ccal_net else None
        js["ccal_sum"] = round(ccal_sum*1000)/1000 if ccal_sum else None
        js["ccal_net_sum"] = round(ccal_net_sum*1000)/1000 if ccal_net_sum else None
        with open(self.args.json_file, 'a+') as fh:
            json.dump(js, fh)
            fh.write("\n")

    def on_last_record(self, sender, status: WalkingPadLastStatus):
        print(status)

    def load_profile(self):
        if not self.args.profile:
            return
        with open(self.args.profile, 'r') as fh:
            dt = json.load(fh)
            self.profile = Profile.from_data(dt)

    def load_stats(self):
        """Compute last unfinished walk from the stats file (segments of the same speed)"""
        if not self.args.json_file:
            return

        # Load margins - boundary speed changes. In order to determine segments of the same speed.
        last_rec = None
        last_rec_diff = None
        margins = []
        with open(self.args.json_file) as fh:
            reader = reverse_file(fh)
            for line in reader:
                if line is None:
                    return
                if not line:
                    continue

                try:
                    js = json.loads(line)
                except Exception as e:
                    continue

                if not last_rec_diff:
                    last_rec_diff = js
                    last_rec = js
                    margins.append(js)
                    continue

                time_diff = last_rec['time'] - js['time']
                steps_diff = last_rec['steps'] - js['steps']
                dist_diff = last_rec['dist'] - js['dist']
                rtime_diff = last_rec['rec_time'] - js['rec_time']
                time_to_rtime = abs(time_diff - rtime_diff)

                breaking = time_diff < 0 or steps_diff < 0 or dist_diff < 0 or rtime_diff < 0 or time_to_rtime > 5*60
                if last_rec_diff['speed'] != js['speed'] \
                        or (breaking and last_rec_diff['speed'] != 0) \
                        or (js['speed'] == 0 and js['time'] == 0):

                    js['_breaking'] = breaking
                    js['_ldiff'] = [time_diff, steps_diff, dist_diff, rtime_diff]
                    if margins:
                        mm = margins[-1]
                        mm['_segment_time'] = last_rec_diff['time'] - js['time']
                        mm['_segment_rtime'] = last_rec_diff['rec_time'] - js['rec_time']
                        mm['_segment_dist'] = last_rec_diff['dist'] - js['dist']
                        mm['_segment_steps'] = last_rec_diff['steps'] - js['steps']
                    margins.append(js)

                    last_rec_diff = js
                    if (js['speed'] == 0 and js['time'] == 0) or breaking:
                        # print("done", breaking, time_to_rtime, time_diff, steps_diff, dist_diff, rtime_diff, js)
                        break

                # last inst.
                last_rec = js
        self.loaded_margins = margins
        logger.debug(json.dumps(margins, indent=2))

        # Calories segment computation
        if not self.profile:
            logger.debug('No profile loaded')
            return

        for exp in margins:
            if '_segment_time' not in exp:
                continue

            el_time = exp['_segment_time']
            speed = exp['speed'] / 10.

            ccal = (el_time / 60) * calories_walk2_minute(speed, self.profile.weight, 0.00)
            ccal_net = ccal - (el_time / 60) * calories_rmrcb_minute(self.profile.weight, self.profile.height,
                                                                     self.profile.age, self.profile.male)

            logger.debug('Calories for time %5s, speed %4.1f, seg time: %4s, dist: %5.2f, steps: %5d, '
                         'cal: %7.2f, ncal: %7.2f'
                         % (exp['time'], speed, el_time, exp['_segment_dist'] / 100., exp['_segment_steps'],
                            ccal, ccal_net))

            self.calorie_acc.append(ccal)
            self.calorie_acc_net.append(ccal_net)
        self.poutput('Calories burned so far this walk: %7.2f kcal, %7.2f kcal net'
                     % (sum(self.calorie_acc), sum(self.calorie_acc_net)))

    def compute_initial_cal(self, status: WalkingPadCurStatus):
        self.last_speed_change_rec = status  # default

        mgs = self.loaded_margins
        if not mgs \
                or status.time < mgs[0]['time'] \
                or status.dist < mgs[0]['dist'] \
                or status.rtime < mgs[0]['rec_time'] \
                or status.steps < mgs[0]['steps']:
            return

        nmg = mgs[0]
        time_to_rtime = abs((status.time - nmg['time']) - (status.rtime - nmg['rec_time']))

        # Last statistics from the file is probably too old, do not count it to the current walk.
        if time_to_rtime > 5*60:
            return

        # Last speed change. Calories for block will be counted from this onward.
        self.last_speed_change_rec = WalkingPadCurStatus()
        self.last_speed_change_rec.speed = status.speed
        self.last_speed_change_rec.dist = mgs[0]['dist']
        self.last_speed_change_rec.time = mgs[0]['time']
        self.last_speed_change_rec.rtime = mgs[0]['rec_time']
        self.last_speed_change_rec.steps = mgs[0]['steps']
        # if '_segment_time' in nmg:
        #     self.last_speed_change_rec.dist -= mgs[0]['_segment_dist']
        #     self.last_speed_change_rec.time -= mgs[0]['_segment_time']
        #     self.last_speed_change_rec.rtime -= mgs[0]['_segment_rtime']
        #     self.last_speed_change_rec.steps -= mgs[0]['_segment_steps']

    async def main(self):
        logger.debug('App started')

        parser = self.argparser()
        self.args_src = sys.argv
        self.args = parser.parse_args(args=self.args_src[1:])

        if self.args.debug:
            coloredlogs.install(level=logging.DEBUG)
        elif self.args.info or self.args.scan:
            coloredlogs.install(level=logging.INFO)
        else:
            coloredlogs.install(level=logging.WARNING)

        self.load_profile()

        try:
            self.load_stats()
        except Exception as e:
            logger.debug("Stats loading failed: %s" % (e,))

        await self.work()

    def argparser(self):
        parser = argparse.ArgumentParser(description='ph4 WalkingPad controller')

        parser.add_argument('--debug', dest='debug', action='store_const', const=True,
                            help='enables debug mode')
        parser.add_argument('--info', dest='info', action='store_const', const=True,
                            help='enables info logging mode')
        parser.add_argument('-s', '--scan', dest='scan', action='store_const', const=True,
                            help='Scan all BLE and exit')
        parser.add_argument('--cmd', dest='cmd', action='store_const', const=True,
                            help='Non-interactive mode')
        parser.add_argument('--stats', dest='stats', type=int, default=None,
                            help='Enable periodic stats collecting, interval in ms')
        parser.add_argument('-j', '--json-file', dest='json_file',
                            help='Write stats to a JSON file')
        parser.add_argument('-p', '--profile', dest='profile',
                            help='Profile JSON file')
        parser.add_argument('-a', '--address', dest='address',
                            help='Walking pad address (if none, scanner is used)')
        return parser

    async def stop_belt(self, to_standby=False):
        await self.ctler.stop_belt()
        if to_standby:
            await asyncio.sleep(1.5)
            await self.ctler.switch_mode(WalkingPad.MODE_STANDBY)

    async def start_belt(self, manual=True):
        if manual:
            await self.ctler.switch_mode(WalkingPad.MODE_MANUAL)
            await asyncio.sleep(1.5)
            await self.ctler.start_belt()
        else:
            await self.ctler.switch_mode(WalkingPad.MODE_AUTOMAT)
            await asyncio.sleep(1.5)
            await self.ctler.start_belt()

    async def switch_mode(self, mode):
        if mode == 'manual':
            await self.ctler.switch_mode(WalkingPad.MODE_MANUAL)
        elif mode == 'auto':
            await self.ctler.switch_mode(WalkingPad.MODE_AUTOMAT)
        elif mode == 'standby':
            await self.ctler.switch_mode(WalkingPad.MODE_STANDBY)
        else:
            print('Unknown mode: %s. Supported: manual, auto, standby' % (mode,))

    async def ask_beep(self):
        self.asked_status_beep = True
        await self.ctler.cmd_162_3_7()

    async def ask_status(self):
        self.asked_status = True
        await self.ctler.ask_stats()

    def do_quit(self, line):
        """Terminate the shell"""
        self.stats_collecting = True
        print("Terminating, please wait...")
        return super().do_quit(line)

    def do_tasks(self, arg):
        """Prints current tasks"""
        for task in asyncio.Task.all_tasks(loop=self.loop):
            print(task)

    def do_ask_stats(self, line):
        """Asks for the latest status, does not print anything"""
        self.submit_coro(self.ask_status())

    def do_ask_beep(self, line):
        """Asks for the latest status, does not print anything"""
        self.submit_coro(self.ask_beep())

    def do_ask_last(self, line):
        """Asks for the latest record, does not print anything"""
        self.submit_coro(self.ctler.ask_hist())

    def do_speed(self, line):
        """Change speed of the running belt. Enter as speed * 10, e.g. 20 for 2.0 km/h"""
        self.submit_coro(self.ctler.change_speed(int(line)))

    def do_start(self, line):
        """Start the belt in the manual mode"""
        self.submit_coro(self.start_belt(True))

    def do_stop(self, line):
        """Stop the belt, switch to standby"""
        self.submit_coro(self.stop_belt(True))

    def do_switch_mode(self, line):
        """Switch mode of the belt"""
        self.submit_coro(self.switch_mode(line.strip()))

    def do_status(self, line):
        """Print the last received status"""
        print(self.ctler.last_status)

    def do_s(self, line):
        """Print the next received status"""
        self.asked_status = True

    def do_profile(self, line):
        """Prints currently loaded profile"""
        print(self.profile)

    do_q = do_quit
    do_Q = do_quit


def main():
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    br = WalkingPadControl()
    loop.run_until_complete(br.main())


if __name__ == '__main__':
    main()

