"""WhatsApp via neonize (unofficial). API names can differ between neonize versions - if an
import/attribute error appears, send me the error text and I will adjust."""
import logging
import threading

import bus
import config
import core

log = logging.getLogger("wa")
START_TS = __import__("time").time()      # messages older than this start are ignored
_jids = {}      # remembers the exact WhatsApp address each chat came from (handles hidden-ID "LID" chats)


def _jid(chat_id):
    from neonize.utils import build_jid
    if "@" in chat_id:
        user, server = chat_id.split("@", 1)
        return build_jid(user, server)
    return build_jid(chat_id)


def start():
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, MessageEv

    client = NewClient("wa_session")

    @client.event(ConnectedEv)
    def on_connected(_, __):
        log.info("WhatsApp connected")

    @client.event(MessageEv)
    def on_message(c, ev):
        try:
            src = ev.Info.MessageSource
            if src.IsGroup and _p3_is_provider_group(src):
                return
            log.info("wa incoming: group=%s from_me=%s chat=%s sender=%s", src.IsGroup, src.IsFromMe, src.Chat.User, src.Sender.User)
            if src.IsFromMe:
                return
            try:
                ts = float(getattr(ev.Info, 'Timestamp', 0) or 0)
                ts = ts / 1000 if ts > 1e11 else ts
                if ts and ts < START_TS - 20:
                    log.info("wa dropped as old: message time %s, bot started %s", ts, START_TS)
                    return          # old message that WhatsApp delivered after the bot was offline
            except Exception:
                pass
            text = ev.Message.conversation or ev.Message.extendedTextMessage.text
            if not text:
                log.info("wa: message had no text (photo, sticker, reaction...) - ignored")
                return
            group = bool(src.IsGroup)
            server = src.Chat.Server or ""
            if group:
                chat_id = f"{src.Chat.User}@g.us"
            elif server in ("", "s.whatsapp.net"):
                chat_id = src.Chat.User
            else:
                chat_id = f"{src.Chat.User}@{server}"
            _jids[chat_id] = src.Chat
            msg = dict(platform="wa", chat_id=chat_id, chat_type="group" if group else "private",
                       sender_id=src.Sender.User, text=text, is_admin=False)
            threading.Thread(target=core.handle, args=(msg,), daemon=True).start()
        except Exception:
            log.exception("wa message failed")

    def send(chat_id, text):

        log.info("wa sending to %s (%s chars)", chat_id, len(text))
        (_p3_for(chat_id) or client).send_message(_jids.get(chat_id) or _jid(chat_id), text)

    bus.register("wa", send, config.WA_SEND_GAP)
    threading.Thread(target=client.connect, daemon=True, name="wa-connect").start()
    threading.Thread(target=lambda: (__import__('time').sleep(8), _p3_start()), daemon=True, name='wa3-start').start()


# ---------------- NUMBER3_PATCH ----------------
# Number 3 (session wa_session3): only writes to the provider groups and quietly reads the providers' replies.
# It never answers customers, staff or anyone else.
_P3 = {"client": None}


def _p3_group_id(chat_id):
    return chat_id if "@g.us" in chat_id else None


def _p3_for(chat_id):
    """Number 3's client when chat_id is a provider group, otherwise None (number 1 is used)."""
    gid = _p3_group_id(str(chat_id))
    if gid and core.provider_by_chat("wa", gid):
        c = _P3["client"]
        if c is None:
            raise RuntimeError("number 3 is not connected yet")
        return c
    return None


def _p3_is_provider_group(src):
    try:
        return bool(core.provider_by_chat("wa", f"{src.Chat.User}@g.us"))
    except Exception:
        return False


def _p3_start():
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, MessageEv

    c3 = NewClient("wa_session3")

    @c3.event(ConnectedEv)
    def on_connected3(_, __):
        _P3["client"] = c3
        log.info("WhatsApp number 3 (provider groups) connected")

    @c3.event(MessageEv)
    def on_message3(c, ev):
        try:
            src = ev.Info.MessageSource
            if src.IsFromMe or not src.IsGroup:
                return
            chat_id = f"{src.Chat.User}@g.us"
            prov = core.provider_by_chat("wa", chat_id)
            if not prov:
                return
            text = ev.Message.conversation or ev.Message.extendedTextMessage.text
            if not text:
                return
            log.info("wa3 provider message in %s (%s)", prov, chat_id)
            threading.Thread(target=core.provider_reply, args=(prov, text), daemon=True).start()
        except Exception:
            log.exception("wa3 message failed")

    _P3["client"] = c3
    threading.Thread(target=c3.connect, daemon=True, name="wa3-connect").start()
