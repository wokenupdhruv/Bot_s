"""Run once:  python login_tg_user.py   (asks for the phone number and the code Telegram sends)"""
import config

if not (config.TGU_API_ID and config.TGU_API_HASH):
    raise SystemExit("Put TGU_API_ID and TGU_API_HASH in the .env file first.")

from telethon.sync import TelegramClient

with TelegramClient(config.TGU_SESSION, int(config.TGU_API_ID), config.TGU_API_HASH) as client:
    me = client.get_me()
    print("Logged in as:", me.first_name, "| username:", me.username)
