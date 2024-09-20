#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import logging
from typing import Union

import coloredlogs

from ph4_walkingpad.profile import Profile, calories_rmrcb_minute, calories_walk2_minute
from ph4_walkingpad.utils import parse_time_string

logger = logging.getLogger(__name__)
coloredlogs.CHROOT_FILES = []
coloredlogs.install(level=logging.INFO)


class CalMeter:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.args = None
        self.profile = None

    def work(self):
        speed = self.args.speed
        timx: Union[str, int, float] = self.args.time
        dist = self.args.dist

        if timx:
            old_time = timx
            if isinstance(timx, str):
                timx = parse_time_string(timx)
                logger.debug(
                    "Time %s recomputed to %s seconds"
                    % (
                        old_time,
                        timx,
                    )
                )

        if not speed and timx and dist:
            speed = dist / (timx / 3600)

        if not timx and speed and dist:
            timx = 3600 * (dist / speed)

        if not dist and timx and speed:
            dist = (timx / 3600) * speed

        if not speed:
            raise ValueError("Missing speed")

        hrs = int(timx / 3600)
        mnts = int((timx - hrs * 3600) / 60)
        scnds = int(timx - hrs * 3600 - mnts * 60)

        ccal = (timx / 60) * calories_walk2_minute(speed, self.profile.weight, 0.00)
        ccal_net = ccal - (timx / 60) * calories_rmrcb_minute(
            self.profile.weight, self.profile.height, self.profile.age, self.profile.male
        )

        print(
            "Speed: %4.1f km/h, dist: %5.2f km, time: %5s s = %02s :%02s :%02s, cal: %7.2f, ncal: %7.2f"
            % (speed, dist, timx, hrs, mnts, scnds, ccal, ccal_net)
        )

        ccal = (timx / 60) * calories_walk2_minute(speed, self.profile.weight, 0.00)
        ccal_net = ccal - (timx / 60) * calories_rmrcb_minute(
            self.profile.weight, self.profile.height, self.profile.age, self.profile.male
        )

    # noinspection DuplicatedCode
    def load_profile(self):
        self.profile = Profile(age=30, male=True, weight=80, height=180)  # some random average person
        if self.args.profile:
            with open(self.args.profile, "r") as fh:
                dt = json.load(fh)
                self.profile = Profile.from_data(dt)

        if self.args.weight:
            self.profile.weight = self.args.weight
        if self.args.height:
            self.profile.height = self.args.height
        if self.args.female:
            self.profile.male = False
        if self.args.age:
            self.profile.age = self.args.age

    def main(self):
        parser = self.argparser()
        self.args = parser.parse_args()

        if self.args.debug:
            coloredlogs.install(level=logging.DEBUG)
        else:
            coloredlogs.install(level=logging.INFO)

        self.load_profile()
        self.work()

    def argparser(self):
        parser = argparse.ArgumentParser(description="ph4 Simple walking calories computation")

        parser.add_argument("--debug", dest="debug", action="store_const", const=True, help="enables debug mode")
        parser.add_argument("-p", "--profile", dest="profile", help="Profile JSON file")
        parser.add_argument(
            "-w", "--weight", dest="weight", type=float, default=None, help="Weight of the person in kg"
        )
        parser.add_argument("-H", "--height", dest="height", type=float, default=None, help="Height of the person in m")
        parser.add_argument("-a", "--age", dest="age", type=float, default=None, help="Age of the person")
        parser.add_argument(
            "-f", "--female", dest="female", action="store_const", const=True, help="Person is a female"
        )
        parser.add_argument("-s", "--speed", dest="speed", type=float, help="Speed of the walk in km/h")
        parser.add_argument("-t", "--time", dest="time", help="Time of the walk in hours h:m:s or 1h 2m 3s")
        parser.add_argument("-d", "--dist", dest="dist", help="Distance of the walk in km")
        return parser


def main():
    br = CalMeter()
    br.main()


if __name__ == "__main__":
    main()
