import json
import logging
from typing import Optional

from ph4_walkingpad.profile import Profile, calories_walk2_minute, calories_rmrcb_minute
from ph4_walkingpad.reader import reverse_file

logger = logging.getLogger(__name__)


class StatsAnalysis:
    def __init__(self, profile=None, profile_file=None, stats_file=None):
        self.profile_file = profile_file
        self.stats_file = stats_file
        self.profile = profile

        self.last_record = None
        self.loaded_margins = []

    def load_profile(self):
        self.profile = Profile(age=30, male=True, weight=80, height=180)  # some random average person
        if self.profile_file:
            with open(self.profile_file, 'r') as fh:
                dt = json.load(fh)
                self.profile = Profile.from_data(dt)

    def load_stats(self, limit=None):
        for margins in self.parse_stats(limit):
            self.loaded_margins.append(margins)

    def parse_stats(self, limit=None):
        if not self.stats_file:
            return

        # Load margins - boundary speed changes. In order to determine segments of the same speed.
        last_rec = None
        last_rec_diff = None
        in_record = False
        num_done = 0
        margins = []

        with open(self.stats_file) as fh:
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

                if not self.last_record:
                    self.last_record = js

                if js['speed'] != 0:
                    in_record = True

                if not last_rec_diff or not in_record:
                    last_rec_diff = js
                    last_rec = js
                    if not in_record:
                        margins = [js]
                    else:
                        margins.append(js)
                    continue

                time_diff = last_rec['time'] - js['time']
                steps_diff = last_rec['steps'] - js['steps']
                dist_diff = last_rec['dist'] - js['dist']
                rtime_diff = last_rec['rec_time'] - js['rec_time']
                time_to_rtime = abs(time_diff - rtime_diff)

                breaking = time_diff < 0 or steps_diff < 0 or dist_diff < 0 or rtime_diff < 0 or time_to_rtime > 5*60
                stats_changed = False

                if breaking:
                    if margins:
                        mm = margins[-1]
                        mm['_breaking'] = breaking

                if not breaking \
                        and (last_rec_diff['speed'] != js['speed']
                        or (breaking and last_rec_diff['speed'] != 0)
                        or (js['speed'] == 0 and js['time'] == 0)):

                    js['_breaking'] = breaking
                    js['_ldiff'] = [time_diff, steps_diff, dist_diff, rtime_diff]
                    if margins:
                        mm = margins[-1]
                        mm['_segment_time'] = last_rec_diff['time'] - js['time']
                        mm['_segment_rtime'] = last_rec_diff['rec_time'] - js['rec_time']
                        mm['_segment_dist'] = last_rec_diff['dist'] - js['dist']
                        mm['_segment_steps'] = last_rec_diff['steps'] - js['steps']

                    margins.append(js)
                    stats_changed = True
                    last_rec_diff = js

                if (stats_changed and js['speed'] == 0 and js['time'] == 0) or breaking:
                    # print("done", breaking, time_to_rtime, time_diff, steps_diff, dist_diff, rtime_diff, js)
                    #logger.info(json.dumps(margins, indent=2))
                    if margins:
                        yield margins
                        num_done += 1
                        if limit and num_done >= limit:
                            return

                    margins = [js]
                    in_record = False
                    last_rec_diff = js
                    #last_rec = None

                # last inst.
                last_rec = js

    def comp_calories(self, margins):
        # logger.debug(json.dumps(margins, indent=2))
        # Calories segment computation
        if not self.profile:
            logger.debug('No profile loaded')
            return

        calorie_acc = []
        calorie_acc_net = []
        for exp in margins:
            if '_segment_time' not in exp:
                continue

            el_time = exp['_segment_time']
            speed = exp['speed'] / 10.

            ccal = (el_time / 60) * calories_walk2_minute(speed, self.profile.weight, 0.00)
            ccal_net = ccal - (el_time / 60) * calories_rmrcb_minute(self.profile.weight, self.profile.height,
                                                                     self.profile.age, self.profile.male)

            logger.info('Calories for time %5s, speed %4.1f, seg time: %4s, dist: %5.2f, steps: %5d, '
                        'cal: %7.2f, ncal: %7.2f'
                        % (exp['time'], speed, el_time, exp['_segment_dist'] / 100., exp['_segment_steps'],
                           ccal, ccal_net))

            calorie_acc.append(ccal)
            calorie_acc_net.append(ccal_net)

        logger.info('Calories burned so far this walk: %7.2f kcal, %7.2f kcal net'
                    % (sum(calorie_acc), sum(calorie_acc_net)))
        return calorie_acc, calorie_acc_net

    def load_last_stats(self):
        for margins in self.parse_stats(1):
            self.loaded_margins.append(margins)
            return self.comp_calories(margins)
