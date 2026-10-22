"""Outbox: every message the bot sends goes through here, one queue per platform.
The small pause between messages is what keeps the bot calm (and the WhatsApp number safe)."""
import logging
import queue
import threading
import time

log = logging.getLogger("bus")
_senders = {}
_queues = {}


def register(platform, fn, gap):
    q = queue.Queue()
    _senders[platform] = fn
    _queues[platform] = q

    def worker():
        while True:
            chat_id, text = q.get()
            try:
                fn(chat_id, text)
            except Exception as e:
                log.error("send failed %s %s: %s", platform, chat_id, e)
            time.sleep(gap)

    threading.Thread(target=worker, daemon=True, name="send-" + platform).start()


def send(platform, chat_id, text):
    q = _queues.get(platform)
    if q is None:
        log.warning("platform %s is not running, message dropped: %s", platform, text[:60])
        return False
    q.put((str(chat_id), text))
    return True
