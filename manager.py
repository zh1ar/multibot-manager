"""
manager.py
ربات مدیر - برای افزودن/مدیریت رباتای فایل‌شیر و آپلود متمرکز.
"""
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.error import TelegramError

from core_db import load_db, save_db, db_transaction, is_global_admin, empty_bot_data
from filebot import build_filebot_app

# نگه‌داری Application های در حال اجرای رباتای فایل‌شیر
running_bots = {}  # {bot_id: Application}


def is_admin(user_id):
    return is_global_admin(user_id)


# ═══════════════════════════════════════════
#   /start و پنل اصلی
# ═══════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔️ این ربات فقط برای مدیریته و دسترسی نداری.")
        return
    await update.message.reply_text(
        f"👋 سلام {user.first_name}!\n🔧 ربات مدیر در خدمتته.",
        reply_markup=main_menu()
    )


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن ربات جدید", callback_data="mb_add")],
        [InlineKeyboardButton("📋 لیست ربات‌ها", callback_data="mb_list")],
        [InlineKeyboardButton("📊 آمار کلی", callback_data="mb_stats")],
        [InlineKeyboardButton("👑 ادمین‌های سراسری", callback_data="mb_admins")],
    ])


def back_kb(target="mb_back"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data=target)]])


# ═══════════════════════════════════════════
#   افزودن ربات جدید
# ═══════════════════════════════════════════
async def add_bot_to_system(bot_id: str, token: str, channel_id: str, name: str):
    """ربات رو تو دیتابیس ثبت می‌کنه و فوراً اجراش می‌کنه."""
    with db_transaction() as db:
        db["bots"][bot_id] = {
            "bot_id": bot_id, "token": token, "channel_id": channel_id,
            "name": name, "active": True,
            "added_at": datetime.now().isoformat(),
        }
        db["bot_data"].setdefault(bot_id, empty_bot_data())

    app = build_filebot_app(bot_id, token, channel_id)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    running_bots[bot_id] = app


async def stop_bot(bot_id: str):
    app = running_bots.get(bot_id)
    if app:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        del running_bots[bot_id]


# ═══════════════════════════════════════════
#   روتر دکمه‌ها
# ═══════════════════════════════════════════
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("⛔️ دسترسی نداری", show_alert=True); return
    await q.answer()
    data = q.data
    db = load_db()

    if data == "mb_back":
        context.user_data.pop("awaiting", None)
        await q.edit_message_text("🔧 پنل مدیریت", reply_markup=main_menu()); return

    if data == "mb_add":
        context.user_data["awaiting"] = "add_token"
        await q.edit_message_text(
            "➕ افزودن ربات جدید\n\nتوکن ربات جدید رو بفرست (از BotFather):",
            reply_markup=back_kb()
        ); return

    if data == "mb_list":
        bots = db.get("bots", {})
        if not bots:
            await q.edit_message_text("هنوز ربات فایل‌شیری اضافه نشده.", reply_markup=back_kb()); return
        rows = []
        for bid, info in bots.items():
            status = "🟢" if info.get("active") else "🔴"
            rows.append([InlineKeyboardButton(f"{status} {info['name']}", callback_data=f"mb_view_{bid}")])
        rows.append([InlineKeyboardButton("🔙 برگشت", callback_data="mb_back")])
        await q.edit_message_text("📋 ربات‌های ثبت‌شده:", reply_markup=InlineKeyboardMarkup(rows)); return

    if data.startswith("mb_view_"):
        bid = data[len("mb_view_"):]
        info = db["bots"].get(bid)
        if not info:
            await q.edit_message_text("پیدا نشد.", reply_markup=back_kb("mb_list")); return
        bd = db.get("bot_data", {}).get(bid, {})
        status = "🟢 روشن" if info.get("active") else "🔴 خاموش"
        text = (
            f"🤖 {info['name']}\n\n"
            f"وضعیت: {status}\n"
            f"کانال ذخیره: {info['channel_id']}\n"
            f"👥 کاربران: {len(bd.get('users', {}))}\n"
            f"📁 فایل‌ها: {len(bd.get('files', {}))}\n"
        )
        toggle_label = "🔴 خاموش کن" if info.get("active") else "🟢 روشن کن"
        rows = [
            [InlineKeyboardButton(toggle_label, callback_data=f"mb_toggle_{bid}")],
            [InlineKeyboardButton("🗑 حذف کامل ربات", callback_data=f"mb_del_{bid}")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="mb_list")],
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows)); return

    if data.startswith("mb_toggle_"):
        bid = data[len("mb_toggle_"):]
        info = db["bots"].get(bid)
        if not info:
            await q.edit_message_text("پیدا نشد.", reply_markup=back_kb("mb_list")); return
        if info.get("active"):
            await stop_bot(bid)
            with db_transaction() as db2:
                db2["bots"][bid]["active"] = False
            await q.edit_message_text(f"🔴 ربات «{info['name']}» خاموش شد.", reply_markup=back_kb("mb_list"))
        else:
            with db_transaction() as db2:
                db2["bots"][bid]["active"] = True
            await add_bot_to_system(bid, info["token"], info["channel_id"], info["name"])
            await q.edit_message_text(f"🟢 ربات «{info['name']}» روشن شد.", reply_markup=back_kb("mb_list"))
        return

    if data.startswith("mb_del_"):
        bid = data[len("mb_del_"):]
        rows = [
            [InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"mb_delconfirm_{bid}")],
            [InlineKeyboardButton("❌ انصراف", callback_data=f"mb_view_{bid}")],
        ]
        await q.edit_message_text(
            "⚠️ مطمئنی؟ این کار ربات و همه‌ی داده‌هاش (کاربرا، فایل‌ها) رو پاک می‌کنه.",
            reply_markup=InlineKeyboardMarkup(rows)
        ); return

    if data.startswith("mb_delconfirm_"):
        bid = data[len("mb_delconfirm_"):]
        await stop_bot(bid)
        with db_transaction() as db2:
            db2["bots"].pop(bid, None)
            db2["bot_data"].pop(bid, None)
        await q.edit_message_text("✅ ربات کاملاً حذف شد.", reply_markup=back_kb("mb_list")); return

    if data == "mb_stats":
        bots = db.get("bots", {})
        total_users = sum(len(db.get("bot_data", {}).get(b, {}).get("users", {})) for b in bots)
        total_files = sum(len(db.get("bot_data", {}).get(b, {}).get("files", {})) for b in bots)
        active = sum(1 for b in bots.values() if b.get("active"))
        text = (
            f"📊 آمار کلی سیستم\n\n"
            f"🤖 تعداد ربات‌ها: {len(bots)} ({active} روشن)\n"
            f"👥 کل کاربران: {total_users}\n"
            f"📁 کل فایل‌ها: {total_files}"
        )
        await q.edit_message_text(text, reply_markup=back_kb()); return

    if data == "mb_admins":
        admins = db.get("global_admins", [])
        text = "👑 ادمین‌های سراسری:\n\nبرای حذف یه ادمین، روی دکمه‌ش بزن."
        rows = [[InlineKeyboardButton(f"❌ {a}", callback_data=f"mb_deladmin_{a}")] for a in admins]
        rows.append([InlineKeyboardButton("➕ افزودن ادمین", callback_data="mb_addadmin")])
        rows.append([InlineKeyboardButton("🔙 برگشت", callback_data="mb_back")])
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows)); return

    if data == "mb_addadmin":
        context.user_data["awaiting"] = "add_admin"
        await q.edit_message_text("آیدی عددی ادمین جدید رو بفرست:", reply_markup=back_kb("mb_admins")); return

    if data.startswith("mb_deladmin_"):
        uid = int(data[len("mb_deladmin_"):])
        rows = [
            [InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"mb_deladminconfirm_{uid}")],
            [InlineKeyboardButton("❌ انصراف", callback_data="mb_admins")],
        ]
        await q.edit_message_text(
            f"⚠️ مطمئنی می‌خوای ادمین `{uid}` رو حذف کنی؟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows)
        ); return

    if data.startswith("mb_deladminconfirm_"):
        uid = int(data[len("mb_deladminconfirm_"):])
        if uid == q.from_user.id:
            await q.edit_message_text("⛔️ نمی‌تونی خودتو از ادمین‌ها حذف کنی.", reply_markup=back_kb("mb_admins")); return
        with db_transaction() as db2:
            if uid in db2["global_admins"]:
                db2["global_admins"].remove(uid)
        await q.edit_message_text(f"✅ ادمین `{uid}` حذف شد.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb("mb_admins")); return

    # ─── انتخاب ربات مقصد برای آپلود فایل ───
    if data.startswith("upload_to_"):
        bid = data[len("upload_to_"):]
        pending = context.user_data.get("pending_upload")
        if not pending:
            await q.edit_message_text("❌ فایلی در انتظار آپلود نیست."); return
        info = db["bots"].get(bid)
        if not info:
            await q.edit_message_text("❌ این ربات پیدا نشد."); return
        try:
            fwd = await context.bot.forward_message(
                chat_id=info["channel_id"],
                from_chat_id=pending["chat_id"],
                message_id=pending["message_id"],
            )
            code = f"{pending['file_id'][-8:]}{fwd.message_id}"
            with db_transaction() as db2:
                bd = db2["bot_data"].setdefault(bid, empty_bot_data())
                bd.setdefault("files", {})[code] = {
                    "code": code, "name": pending["file_name"], "type": pending["file_type"],
                    "message_id": fwd.message_id, "uploaded_by": q.from_user.id,
                    "uploaded_at": datetime.now().isoformat(),
                    "downloads": 0, "liked_by": [], "disliked_by": [],
                    "custom_caption": pending.get("caption"),
                }
            target_app = running_bots.get(bid)
            bot_username = None
            if target_app:
                me = await target_app.bot.get_me()
                bot_username = me.username
            else:
                # ربات فعلاً روشن نیست؛ از توکن خودش یوزرنیم بگیر (یه بار)
                from telegram import Bot
                tmp_bot = Bot(token=info["token"])
                me = await tmp_bot.get_me()
                bot_username = me.username

            link = f"https://t.me/{bot_username}?start=file_{code}"
            await q.edit_message_text(
                f"✅ فایل به ربات «{info['name']}» اضافه شد!\n\n🔗 لینک:\n`{link}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except TelegramError as e:
            await q.edit_message_text(f"❌ خطا: {e}\n⚠️ ربات مدیر باید ادمین کانال ذخیره اون ربات باشه.")
        context.user_data.pop("pending_upload", None)
        return


# ═══════════════════════════════════════════
#   آپلود فایل به ربات مدیر → انتخاب مقصد
# ═══════════════════════════════════════════
def extract_file_info(msg):
    if msg.photo:
        return msg.photo[-1].file_id, "photo.jpg", "photo"
    if msg.video:
        return msg.video.file_id, msg.video.file_name or "video.mp4", "video"
    if msg.document:
        return msg.document.file_id, msg.document.file_name or "file", "document"
    if msg.audio:
        return msg.audio.file_id, msg.audio.file_name or "audio.mp3", "audio"
    if msg.voice:
        return msg.voice.file_id, "voice.ogg", "voice"
    return None, None, None


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔️ فقط ادمین می‌تونه آپلود کنه.")
        return
    msg = update.message
    file_id, file_name, file_type = extract_file_info(msg)
    if not file_id:
        await msg.reply_text("❌ نوع فایل پشتیبانی نمی‌شه."); return

    db = load_db()
    active_bots = {bid: info for bid, info in db.get("bots", {}).items() if info.get("active")}
    if not active_bots:
        await msg.reply_text("❌ هیچ ربات فعالی برای آپلود وجود نداره. اول یه ربات اضافه کن.")
        return

    context.user_data["pending_upload"] = {
        "chat_id": msg.chat_id, "message_id": msg.message_id,
        "file_id": file_id, "file_name": file_name, "file_type": file_type,
        "caption": msg.caption,
    }
    rows = [[InlineKeyboardButton(info["name"], callback_data=f"upload_to_{bid}")] for bid, info in active_bots.items()]
    await msg.reply_text("این فایل رو برای کدوم ربات بفرستم؟", reply_markup=InlineKeyboardMarkup(rows))


# ═══════════════════════════════════════════
#   پردازش متن (افزودن ربات، ادمین)
# ═══════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔️ دسترسی نداری.")
        return
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        await update.message.reply_text("از /start برای باز کردن پنل استفاده کن.")
        return

    text = update.message.text.strip()

    if awaiting == "add_token":
        context.user_data["new_bot_token"] = text
        context.user_data["awaiting"] = "add_channel"
        await update.message.reply_text("کانال ذخیره‌سازی این ربات رو بفرست (آیدی عددی یا @username):")
        return

    if awaiting == "add_channel":
        context.user_data["new_bot_channel"] = text
        context.user_data["awaiting"] = "add_name"
        await update.message.reply_text("یه اسم نمایشی برای این ربات بفرست:")
        return

    if awaiting == "add_name":
        token = context.user_data.pop("new_bot_token")
        channel = context.user_data.pop("new_bot_channel")
        name = text
        context.user_data.pop("awaiting", None)

        status = await update.message.reply_text("⏳ در حال راه‌اندازی...")
        try:
            from telegram import Bot
            tmp_bot = Bot(token=token)
            me = await tmp_bot.get_me()
            bot_id = f"bot_{me.id}"

            db = load_db()
            if bot_id in db.get("bots", {}):
                await status.edit_text("⚠️ این ربات قبلاً اضافه شده.")
                return

            await add_bot_to_system(bot_id, token, channel, name)
            await status.edit_text(
                f"✅ ربات «{name}» (@{me.username}) با موفقیت اضافه و راه‌اندازی شد!",
                reply_markup=main_menu()
            )
        except Exception as e:
            await status.edit_text(f"❌ خطا در راه‌اندازی: {e}\n\nمطمئن شو توکن درسته و ربات ادمین کانال ذخیره‌ست.")
        return

    if awaiting == "add_admin":
        if text.lstrip("-").isdigit():
            uid = int(text)
            with db_transaction() as db:
                if uid not in db["global_admins"]:
                    db["global_admins"].append(uid)
            await update.message.reply_text(f"✅ {uid} به ادمین‌های سراسری اضافه شد.", reply_markup=main_menu())
        else:
            await update.message.reply_text("❌ آیدی نامعتبره.")
        context.user_data.pop("awaiting", None)
        return


# ═══════════════════════════════════════════
#   راه‌اندازی همه‌ی ربات‌های فعال موقع استارت سیستم
# ═══════════════════════════════════════════
async def restart_active_bots():
    db = load_db()
    for bid, info in db.get("bots", {}).items():
        if info.get("active"):
            try:
                app = build_filebot_app(bid, info["token"], info["channel_id"])
                await app.initialize()
                await app.start()
                await app.updater.start_polling(drop_pending_updates=True)
                running_bots[bid] = app
                print(f"✅ ربات «{info['name']}» ({bid}) راه‌اندازی شد.")
            except Exception as e:
                print(f"❌ خطا تو راه‌اندازی ربات {bid}: {e}")
