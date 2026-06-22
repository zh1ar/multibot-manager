#!/usr/bin/env python3
"""
main.py
نقطه‌ی ورود برنامه: ربات مدیر رو ران می‌کنه و رباتای فایل‌شیر فعال رو هم بالا میاره.
"""
import os
import asyncio
import signal
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from core_db import load_db, db_transaction
import manager

MANAGER_BOT_TOKEN = os.environ.get("MANAGER_BOT_TOKEN", "")
INITIAL_ADMIN_IDS = [
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]


def ensure_initial_admins():
    """آیدی‌های ADMIN_IDS رو یه بار به global_admins اضافه می‌کنه (اگه نبودن)"""
    if not INITIAL_ADMIN_IDS:
        return
    with db_transaction() as db:
        for uid in INITIAL_ADMIN_IDS:
            if uid not in db["global_admins"]:
                db["global_admins"].append(uid)


async def run():
    ensure_initial_admins()

    # ─── ربات مدیر ───
    mgr_app = Application.builder().token(MANAGER_BOT_TOKEN).build()
    mgr_app.add_handler(CommandHandler("start", manager.start))
    mgr_app.add_handler(MessageHandler(
        filters.PHOTO & ~filters.COMMAND,
        manager.handle_post_photo
    ))
    mgr_app.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE,
        manager.handle_file
    ))
    mgr_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manager.handle_text))
    mgr_app.add_handler(CallbackQueryHandler(manager.callback_router))

    await mgr_app.initialize()
    await mgr_app.start()
    await mgr_app.updater.start_polling(drop_pending_updates=True)
    print("🔧 ربات مدیر روشن شد.")

    # ─── ربات‌های فایل‌شیر فعال (که قبلاً اضافه شدن) ───
    await manager.restart_active_bots()

    print("✅ سیستم کامل بالا اومد. منتظر پیام‌ها...")

    # نگه‌داشتن برنامه زنده تا سیگنال توقف بیاد
    stop_event = asyncio.Event()

    def _stop(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass  # ویندوز یا بعضی محیط‌ها این رو ساپورت نمی‌کنن

    await stop_event.wait()

    # ─── خاموش کردن مرتب همه چی ───
    print("⏳ در حال خاموش کردن...")
    for bid, app in list(manager.running_bots.items()):
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    await mgr_app.updater.stop()
    await mgr_app.stop()
    await mgr_app.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
