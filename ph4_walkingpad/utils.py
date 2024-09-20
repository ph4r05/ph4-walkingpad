import logging
import re
import sys


def try_fnc(fnc):
    try:
        return fnc()
    except Exception:
        pass


def defval(js, key, default=None):
    return js[key] if key in js else default


def setup_logging():
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.DEBUG)
    log.addHandler(h)
    return log


def parse_time_string(timx):
    mt = re.match(
        r"^(?:([\d.]+)(?:(?<=\d)h)?)?\s*\b(?:([\d.]+)(?:(?<=\d)m)?)?\s*\b(?:([\d.]+)(?:(?<=\d)s)?)?\s*$", timx.strip()
    )
    if ":" in timx:
        parts = timx.split(":")
        if len(parts) < 2 or len(parts) > 3:
            raise ValueError("Time format error")

        if len(parts) == 2:
            return float(parts[0]) * 3600 + float(parts[1]) * 60
        else:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

    elif mt:
        return float(mt.group(1) or 0) * 3600 + float(mt.group(2) or 0) * 60 + float(mt.group(3) or 0)

    else:
        return float(timx)
