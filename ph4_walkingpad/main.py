#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import binascii
import json
import logging
import sys
import asyncio
import threading
import time
from collections import OrderedDict
from typing import Optional
import coloredlogs

from ph4_walkingpad.cmd import Ph4Cmd
from ph4_walkingpad.pad import Scanner, WalkingPad, WalkingPadCurStatus, WalkingPadLastStatus, Controller

logger = logging.getLogger(__name__)
coloredlogs.CHROOT_FILES = []
coloredlogs.install(level=logging.INFO)


class WalkingPadControl(Ph4Cmd):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.args = None
        self.args_src = None
        self.ctler = None  # type: Optional[Controller]
        self.worker_thread = None
        self.stats_thread = None
        self.stats_loop = None
        self.stats_task = None
        self.stats_collecting = False

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
        # logger.debug("on status: %s" % (status,))
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
        js["rec_time"] = time.time()
        with open(self.args.json_file, 'a+') as fh:
            json.dump(js, fh)
            fh.write("\n")

    def on_last_record(self, sender, status: WalkingPadLastStatus):
        pass

    async def main(self):
        logger.debug('App started')

        parser = self.argparser()
        self.args_src = sys.argv
        self.args = parser.parse_args(args=self.args_src[1:])

        if self.args.debug:
            coloredlogs.install(level=logging.DEBUG)
        elif self.args.info:
            coloredlogs.install(level=logging.INFO)
        else:
            coloredlogs.install(level=logging.WARNING)

        await self.work()

    def argparser(self):
        parser = argparse.ArgumentParser(description='ph4 WalkingPad controller')

        parser.add_argument('--debug', dest='debug', action='store_const', const=True,
                            help='enables debug mode')
        parser.add_argument('--info', dest='info', action='store_const', const=True,
                            help='enables info logging mode')
        parser.add_argument('--scan', dest='scan', action='store_const', const=True,
                            help='Scan all BLE and exit')
        parser.add_argument('--cmd', dest='cmd', action='store_const', const=True,
                            help='Non-interactive mode')
        parser.add_argument('--stats', dest='stats', type=int, default=None,
                            help='Enable periodic stats collecting, interval in ms')
        parser.add_argument('--json-file', dest='json_file',
                            help='Write stats to a JSON file')
        parser.add_argument('--address', dest='address',
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
        self.submit_coro(self.ctler.ask_stats())

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

    do_q = do_quit
    do_Q = do_quit


def main():
    br = WalkingPadControl()
    return br.main()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.run_until_complete(main())

