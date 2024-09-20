import hashlib
import json
import logging

import requests

logger = logging.getLogger(__name__)


def upload_record(tok, did, cal, timex, dur, distance, step, **kwargs):
    """
    Uploads record to your account
    tok = your JWT token obtained from the app.
    did = device ID, MAC address ff:ff:ff:ff:ff:ff
    """
    url = "https://eu.app.walkingpad.com/user/api/v2/record"
    cookies = {"user": tok}
    js = {
        "did": did,
        "cal": cal,
        "time": timex,
        "dur": dur,
        "distance": distance,
        "step": step,
        "sid": None,
        "model": "A1",
    }
    logger.info(
        "Upload record: %s" % (json.dumps(js, indent=2)),
    )
    return requests.post(url, json=js, cookies=cookies, **kwargs)


def get_records(tok, page=1, per_page=10000, timestamp=None, **kwargs):
    url = "https://eu.app.walkingpad.com/user/api/v2/record?page=%d&per_page=%d" % (page, per_page)
    if timestamp:
        url += "&timestamp=%d" % timestamp
    cookies = {"user": tok}
    return requests.get(url, cookies=cookies, **kwargs)


def login(email, password=None, password_md5=None, **kwargs):
    """
    Logs in to the walking pad service, returns tuple (jwt-token, response)
    """
    url = "https://eu.app.walkingpad.com/user/api/v2/login"
    password_md5 = password_md5 if password_md5 else hashlib.md5(password.encode("utf8")).hexdigest()
    js = {"email": email, "password": password_md5}

    r = requests.post(url, json=js, **kwargs)
    r.raise_for_status()
    return r.cookies.get_dict()["user"], r
