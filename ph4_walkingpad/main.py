#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import binascii
import json
import logging
import shutil
import sys
import threading
import re
import os
import time
from collections import OrderedDict
from typing import Optional

import coloredlogs
from aioconsole import ainput, get_standard_streams

from ph4_walkingpad.cmd_helper import Ph4Cmd
from ph4_walkingpad.pad import Scanner, WalkingPad, WalkingPadCurStatus, WalkingPadLastStatus, Controller
from ph4_walkingpad.profile import Profile, calories_walk2_minute, calories_rmrcb_minute
from ph4_walkingpad.analysis import StatsAnalysis
from ph4_walkingpad.upload import upload_record, login as svc_login

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
        self.analysis = None  # type: Optional[StatsAnalysis]
        self.loaded_margins = []
        self.streams = None

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

    async def disconnect(self):
        logger.debug("Disconnecting coroutine")
        if self.ctler:
            await self.ctler.disconnect()

    async def connect(self, address):
        if self.args.no_bt:
            return

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
        self.worker_thread.daemon = True
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
        if not self.args.no_bt:
            await asyncio.sleep(1)

        logger.info('Terminating')
        return res

    async def scan_address(self):
        if self.args.no_bt:
            return

        address = self.args.address
        if address and Scanner.is_darwin():
            logger.warning('Specifying address does not work on OSX 12+. '
                           'If connection cannot be made, omit -a parameter')

        if address:
            return address

        if not address or self.args.scan:
            scanner = Scanner()
            await scanner.scan(timeout=self.args.scan_timeout)

            if scanner.walking_belt_candidates:
                candidates = scanner.walking_belt_candidates
                logger.info("WalkingPad candidates: %s" % (candidates,))
                if self.args.scan:
                    return None

                if self.args.address_filter:
                    candidates = [x for x in candidates if str(x.address).startswith(self.args.address_filter)]
                return candidates[0] if candidates else None
        return None

    def init_stats_fetcher(self):
        self.stats_loop = asyncio.new_event_loop()
        self.stats_thread = threading.Thread(
            target=self.looper, args=(self.stats_loop,)
        )
        self.stats_thread.daemon = True
        self.stats_thread.start()

    def start_stats_fetching(self):
        if self.args.no_bt:
            return

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
        aux = " (bluetooth disabled)" if self.args.no_bt else ""
        self.intro = (
                "-" * self.get_term_width()
                + "\n    WalkingPad controller" + aux + "\n"
                + "\n"
                + "-" * self.get_term_width()
        )

        # if self.args.no_bt:
        #     self.cmdloop()
        # else:
        await self.acmdloop()

    def on_status(self, sender, status: WalkingPadCurStatus):
        # Calories computation with respect to the last segment of the same speed.
        # TODO: refactor to analysis file
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

    def save_profile(self):
        if not self.args.profile or not self.profile:
            return

        tmp_fname = self.args.profile + '.tmp'
        bak_fname = self.args.profile + '.backup'
        with open(tmp_fname, 'w+') as fh:
            json.dump(self.profile.dump(), fh, indent=2)

        if not os.path.exists(bak_fname):
            shutil.copy(self.args.profile, bak_fname)
        os.rename(tmp_fname, self.args.profile)

    def login(self):
        if not self.args.profile or not self.profile:
            raise ValueError('Could not login, no profile')

        res = svc_login(self.profile.email, password=self.profile.password, password_md5=self.profile.password_md5)
        tok = res[0]

        if not tok:
            raise ValueError('Could not login')

        self.profile.token = tok
        self.save_profile()
        return res

    def load_stats(self):
        """Compute last unfinished walk from the stats file (segments of the same speed)"""
        if not self.args.json_file:
            return

        self.analysis = StatsAnalysis(profile=self.profile, stats_file=self.args.json_file)
        accs = self.analysis.load_last_stats(5)
        self.loaded_margins = self.analysis.loaded_margins

        self.calorie_acc = accs[0]
        self.calorie_acc_net = accs[1]

        if accs[0] or accs[1]:
            self.poutput('Calories burned so far this walk: %7.2f kcal, %7.2f kcal net'
                         % (sum(self.calorie_acc), sum(self.calorie_acc_net)))

    def compute_initial_cal(self, status: WalkingPadCurStatus):
        self.last_speed_change_rec = status  # default

        mgs = self.loaded_margins
        if not mgs or not mgs[0] or not mgs[0][0] \
                or status.time < mgs[0][0]['time'] \
                or status.dist < mgs[0][0]['dist'] \
                or status.rtime < mgs[0][0]['rec_time'] \
                or status.steps < mgs[0][0]['steps']:
            return

        nmg = mgs[0][0]
        time_to_rtime = abs((status.time - nmg['time']) - (status.rtime - nmg['rec_time']))

        # Last statistics from the file is probably too old, do not count it to the current walk.
        if time_to_rtime > 5*60:
            return

        # Last speed change. Calories for block will be counted from this onward.
        self.last_speed_change_rec = WalkingPadCurStatus()
        self.last_speed_change_rec.speed = status.speed
        self.last_speed_change_rec.dist = nmg['dist']
        self.last_speed_change_rec.time = nmg['time']
        self.last_speed_change_rec.rtime = nmg['rec_time']
        self.last_speed_change_rec.steps = nmg['steps']
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

        try:
            await self.work()
        except Exception as e:
            logger.error('Exception in the main entry point: %s' % (e,), exc_info=e)
        finally:
            await self.disconnect()

    def argparser(self):
        parser = argparse.ArgumentParser(description='ph4 WalkingPad controller')

        parser.add_argument('-d', '--debug', dest='debug', action='store_const', const=True,
                            help='enables debug mode')
        parser.add_argument('--no-bt', dest='no_bt', action='store_const', const=True,
                            help='Do not use Bluetooth, no belt interaction enabled')
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
                            help='Walking pad address (if none, scanner is used). OSX 12 have to scan first, do not use')
        parser.add_argument('--filter', dest='address_filter',
                            help='Walking pad address filter, if scanning and multiple devices are found')
        parser.add_argument('--scan-timeout', dest='scan_timeout', type=float, default=3.0,
                            help='Scan timeout in seconds, double')
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

    async def upload_record(self, line):
        if not self.profile or not self.profile.did or not self.profile.token:
            self.poutput("Profile is not properly loaded (token, did)")
            return

        mt_int = re.match(r'^(\d+)$', line.strip())

        # re_float = r'[+-]?(?:[0-9]+(?:[.][0-9]*)?|[.][0-9]+)'
        # mt_data = re.match(r'^(?:(%s)\s*m)\s+(?:(\d+)\s*s)\s+(?:(%s)\s*m)\s+(?:(%s)\s*m)\s+(?:(%s)\s*m)\s+$')
        cal_acc, timex, dur, dist, steps = 0, 0, 0, 0, 0
        if mt_int:
            idx = int(line)
            mm = [x for x in self.loaded_margins[idx] if '_segment_dist' in x and x['_segment_dist'] > 0]
            oldest = min(mm, key=lambda x: x['rec_time'])
            newest = min(mm, key=lambda x: -x['rec_time'])

            cal_acc = 0
            for r in mm:
                el_time = r['_segment_rtime']
                ccal = (el_time / 60) * calories_walk2_minute(r['speed'] / 10., self.profile.weight, 0.00)
                ccal_net = ccal - (el_time / 60) * calories_rmrcb_minute(self.profile.weight, self.profile.height,
                                                                         self.profile.age, self.profile.male)
                cal_acc += ccal_net
            timex = int(oldest['rec_time'])
            dur, dist, steps = newest['time'], newest['dist'], newest['steps']

        elif ',' in line:
            p = [x.strip() for x in line.split(',')]
            dist, dur, steps, timex, cal_acc = int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4])

        else:
            dist = int(await self.ask_prompt("Distance: "))
            dur = int(await self.ask_prompt("Duration: "))
            steps = int(await self.ask_prompt("Steps: "))
            timex = int(await self.ask_prompt("Time: (0 for current)"))
            cal_acc = int(await self.ask_prompt("Calories: "))
            if timex == 0:
                timex = int(time.time() - dur - 60)

        if steps == 0:
            self.poutput('No record to upload')
            return

        # cal, timex, dur, distance, step
        self.poutput('Adding record: Duration=%5d, distance=%5d, steps=%5d, cal=%5d, time: %d'
                     % (dur, dist, steps, cal_acc, timex))

        res = await self.ask_yn()
        if not res:
            return

        self.poutput('Uploading...')
        r = upload_record(self.profile.token, self.profile.did,
                          cal=int(cal_acc), timex=timex, dur=dur, distance=dist, step=steps)
        r.raise_for_status()
        self.poutput('Response: %s, data: %s' % (r, r.json()))

    async def ask_prompt(self, prompt="", is_int=False):
        ret_val = None
        self.switch_reader(False)
        self.remove_reader()
        try:
            while True:
                await asyncio.sleep(0)
                ret_val = await ainput(prompt, loop=self.loop)
                if not is_int:
                    break
                if is_int and re.match(r'^(\d+)$', ret_val):
                    break

        except Exception as e:
            logger.warning('Exception: %s' % (e,))
        finally:
            await asyncio.sleep(0)
            self.switch_reader(True)
            self.reset_reader()
        return ret_val

    async def ask_yn(self):
        ret_val = None
        self.switch_reader(False)
        self.remove_reader()
        if not self.streams:
            self.streams = await get_standard_streams(use_stderr=False, loop=self.loop)

        try:
            while True:
                await asyncio.sleep(0)
                yn = await ainput("Do you confirm? (y/n): ", loop=self.loop, streams=self.streams)
                yn2 = yn.lower().strip()
                if yn2 in ['y', 'n']:
                    ret_val = yn2 == 'y'
                    break
        except Exception as e:
            logger.warning('Exception: %s' % (e,))
        finally:
            await asyncio.sleep(0)
            self.remove_reader()
            self.switch_reader(True)
            self.reset_reader()
        return ret_val

    def do_quit(self, line):
        """Terminate the shell"""
        self.stats_collecting = True
        self.cmd_running = False
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

    def do_upload(self, line):
        """Uploads records to the app server. Format: dist, dur, steps, timex, cal_acc.
        Alternatively, use upload <margin_index>"""
        self.submit_coro(self.upload_record(line), loop=self.loop)

    def do_login(self, line):
        """Login to the walkingpad service, refreshes JWT token for record upload (logs of the application)
        Preferably, use `adb logcat | grep 'user='` when logging in with the Android app to capture JWT"""
        try:
            self.poutput('Logging in...')
            r = self.login()
            self.poutput('Logged in. Response: %s' % (r,))
        except Exception as e:
            logger.error('Could not login: %s' % (e,), exc_info=e)

    def do_margins(self, line):
        target = int(line) if line else None
        for i, m in enumerate(self.loaded_margins):
            if target is not None and i != target:
                continue
            print('='*80, 'Margin %2d, records: %3d' % (i, len(m)))
            print(json.dumps(self.analysis.remove_records([m])[0], indent=2))
            print('- ' * 40, 'Margin %2d, records: %3d' % (i, len(m)))
        print('Num margins: %s' % (len(self.loaded_margins),))

    do_q = do_quit
    do_Q = do_quit


def main():
    try:
        loop = asyncio.get_running_loop()
    except:
        loop = asyncio.new_event_loop()

    loop.set_debug(True)
    br = WalkingPadControl()
    loop.run_until_complete(br.main())

    # Alternatively
    # asyncio.run(br.main())


if __name__ == '__main__':
    main()

