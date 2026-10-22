"""Every text the bot says lives in replies.json. Empty text = the bot stays silent."""
import json
import os

_FILE = os.getenv("REPLIES_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "replies.json"))


class _Safe(dict):
    def __missing__(self, k):
        return ""


def get(key, **kw):
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    text = data.get(key, "")
    if not text:
        return ""
    return text.format_map(_Safe(kw))
