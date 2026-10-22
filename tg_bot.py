import asyncio
import logging
import re

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import bus
import config
import core

log = logging.getLogger("tg")
_loop = None
_app = None


def _send(chat_id, text):
    asyncio.run_coroutine_threadsafe(_app.bot.send_message(chat_id=int(chat_id), text=text), _loop).result(30)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m or not m.text or not update.effective_user or update.effective_user.is_bot:
        return
    chat = update.effective_chat
    group = chat.type in ("group", "supergroup")
    text = re.sub(r"^/(\w+)@\w+", r"/\1", m.text.strip())
    first = text.lower().split()[0] if text.split() else ""
    is_admin = False
    if group and (first.startswith("/") or first in core.RESET_WORDS):
        try:
            mem = await ctx.bot.get_chat_member(chat.id, update.effective_user.id)
            is_admin = mem.status in ("administrator", "creator")
        except Exception:
            pass
    msg = dict(platform="tg", chat_id=str(chat.id), chat_type="group" if group else "private",
               sender_id=str(update.effective_user.id), text=text, is_admin=is_admin)
    await asyncio.to_thread(core.handle, msg)


async def _post_init(app):
    global _loop, _app
    _loop, _app = asyncio.get_running_loop(), app
    bus.register("tg", _send, config.TG_SEND_GAP)


def run():
    app = Application.builder().token(config.TG_TOKEN).post_init(_post_init).build()
    app.add_handler(MessageHandler(filters.TEXT, on_text))
    app.run_polling(drop_pending_updates=True)
