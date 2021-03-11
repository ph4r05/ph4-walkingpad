#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import logging

import asyncio
import coloredlogs
from ph4_walkingpad.pad import Scanner, WalkingPad, WalkingPadCurStatus, WalkingPadLastStatus, Controller

logger = logging.getLogger(__name__)
coloredlogs.install(level=logging.INFO)


class WalkingPadControl:
    def __init__(self):
        self.args = None

    async def work(self):
        scanner = Scanner()
        await scanner.scan()

        logger.warning('Not implemented yet')

    async def main(self):
        logger.debug('App started')

        parser = self.argparser()
        self.args = parser.parse_args()
        if self.args.debug:
            coloredlogs.install(level=logging.DEBUG)

        await self.work()

    def argparser(self):
        parser = argparse.ArgumentParser(description='ph4 WalkingPad controller')

        parser.add_argument('--debug', dest='debug', action='store_const', const=True,
                            help='enables debug mode')
        return parser


def main():
    br = WalkingPadControl()
    return br.main()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.run_until_complete(main())

