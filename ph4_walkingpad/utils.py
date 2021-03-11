import logging
import sys


def try_fnc(fnc):
    try:
        return fnc()
    except:
        pass


def setup_logging():
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.DEBUG)
    log.addHandler(h)
    return log
