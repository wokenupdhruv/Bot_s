import logging

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

if __name__ == "__main__":
    if config.WA_ENABLED:
        import wa_bot
        wa_bot.start()          # first run: scan the QR code shown in this window
    try:
        import tg_user
        if tg_user.start():
            logging.info("Telegram helper account starting...")
    except Exception as e:
        logging.warning("Telegram helper not started: %s", e)
    if not config.TG_TOKEN:
        logging.info("No Telegram bot token - running WhatsApp only")
        import time
        while True:
            time.sleep(3600)
    import tg_bot
    tg_bot.run()
