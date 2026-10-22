"""Telegram USER-account helper (Telethon).
Why: a Telegram bot cannot see another bot's messages, so for providers whose support is a bot
(my.persiansmm.com) a real Telegram account has to talk to it. This account posts "<external id> <action>"
in the provider chat, reads the provider bot's answer, and sends the matching text to the customer."""
import asyncio
import json
import logging
import re
import threading

import bus
import config
import db
import messages as M

log = logging.getLogger("tg_user")
_loop = None
_client = None
_ready = threading.Event()
TIMEOUT = 300                       # seconds to wait for the provider bot's answer
TAIL_ID_RE = re.compile(r":\s*(\d+)\s*$")
RESULT_RE = re.compile(r"Results:\s*(.+?)\s*(?::\s*\d+\s*)?$", re.S | re.I)


def available():
    return _ready.is_set()


def customer_text(text, order_id):
    """Turns the provider bot's answer into the message the customer gets (rules: persian_replies.json)."""
    try:
        with open("persian_replies.json", encoding="utf-8") as f:
            rules = json.load(f)
    except Exception:
        rules = []
    for rule in rules:
        pattern = rule.get("match", "")
        m = re.search(pattern, text, re.I) if pattern else None
        if m:
            out = rule.get("reply", "")
            return out.replace("{1}", (m.group(1) or "") if m.groups() else "").replace("{order}", str(order_id))
    log.warning("NO RULE for provider bot answer (order %s): %s", order_id, re.sub(r"\s+", " ", text)[:300])
    return M.get("support_contact", order=order_id)


def start():
    """Starts the helper in the background. Returns False if it is not set up (no codes in .env)."""
    if not (config.TGU_API_ID and config.TGU_API_HASH):
        return False
    threading.Thread(target=_run, daemon=True, name="tg-user").start()
    return True


def _run():
    global _loop, _client
    from telethon import TelegramClient
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _client = TelegramClient(config.TGU_SESSION, int(config.TGU_API_ID), config.TGU_API_HASH)
    _loop.run_until_complete(_main())


async def _main():
    from telethon import events
    await _client.connect()
    if not await _client.is_user_authorized():
        log.error("Telegram helper is not logged in. Run:  python login_tg_user.py")
        return
    await _client.get_dialogs()                         # lets the helper find the provider chats
    _client.add_event_handler(_on_message, events.NewMessage(incoming=True))
    _client.add_event_handler(_on_message, events.MessageEdited(incoming=True))   # the bot may edit its message into the final answer
    _ready.set()
    log.info("Telegram helper account connected")
    await _client.run_until_disconnected()


async def _on_message(event):
    try:
        chat = event.chat_id
        text = event.raw_text or ""
        rid = None
        if event.reply_to_msg_id:
            rid = db.kv_get(f"tgu:{chat}:{event.reply_to_msg_id}")
        if rid is None:
            m = TAIL_ID_RE.search(text)
            if m:
                rid = db.kv_get(f"tguext:{chat}:{m.group(1)}")
        if rid is None:
            return
        req = db.request_get(int(rid))
        log.info("provider bot answered request %s (status %s): %s", rid, req and req["status"], re.sub(r"\s+", " ", text)[:120])
        if not req or req["status"] != "waiting":
            return
        low = text.lower()
        if "results" not in low:
            return          # Awaiting / Queued / Processing steps - only the "Results" message is the real answer
        db.request_set_status(req["id"], "done")
        out = customer_text(text, req["order_id"])
        if out:
            bus.send(req["platform"], req["chat_id"], f"{req['order_id']} - {out}")
    except Exception:
        log.exception("helper message failed")


def send_request(chat_id, ext_id, action_word, rid):
    """Posts '<external id> <action>' in the provider chat. Returns True if it was sent."""
    if not available():
        return False
    fut = asyncio.run_coroutine_threadsafe(_send(int(chat_id), str(ext_id), action_word, rid), _loop)
    try:
        return bool(fut.result(30))
    except Exception:
        log.exception("helper could not send")
        return False


def send_plain(chat_id, text):
    """Posts a plain message in a Telegram provider chat through the helper account. No answer is awaited."""
    if not available():
        return False
    fut = asyncio.run_coroutine_threadsafe(_post(int(chat_id), text), _loop)
    try:
        return bool(fut.result(30))
    except Exception:
        log.exception("helper could not post")
        return False


async def _post(chat_id, text):
    await _client.send_message(chat_id, text)
    log.info("helper posted (plain) in %s: %s", chat_id, text)
    return True


async def _send(chat_id, ext_id, action_word, rid):
    msg = await _client.send_message(chat_id, f"{ext_id} {action_word}")
    log.info("helper posted in %s: %s %s (request %s)", chat_id, ext_id, action_word, rid)
    db.kv_set(f"tgu:{chat_id}:{msg.id}", rid)
    db.kv_set(f"tguext:{chat_id}:{ext_id}", rid)
    db.request_set_status(rid, "waiting")
    _loop.call_later(TIMEOUT, lambda: asyncio.ensure_future(_timeout(rid)))
    return True


async def _timeout(rid):
    req = db.request_get(int(rid))
    if req and req["status"] == "waiting":
        db.request_set_status(rid, "timeout")
        log.warning("NOT PROCESSED order %s: provider bot did not answer in %s seconds", req["order_id"], TIMEOUT)
        bus.send(req["platform"], req["chat_id"], M.get("support_contact", order=req["order_id"]))
