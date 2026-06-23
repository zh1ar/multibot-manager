"""
core/filebot.py
کارخانه‌ی ساخت ربات فایل‌شیر - با bot_id مشخص می‌شه کدوم ربات داده‌ش کجاست.
از این تابع برای هر ربات فایل‌شیر یه Application جدا می‌سازیم.
"""
import asyncio
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.error import TelegramError

from core_db import load_db, save_db, db_transaction, is_global_admin

DEFAULT_TEXTS = {
    "start": "👋 سلام {name}!\n\n🤖 به ربات اشتراک‌گذاری فایل خوش اومدی.\n📥 برای دریافت فایل، روی لینک مربوطه کلیک کن.",
    "start_admin_suffix": "\n\n🔧 پنل ادمین: /admin",
    "file_caption": "📁 {name}",
    "file_sent_extra": "",
    "join_required": "⛔️ برای دریافت فایل، اول باید تو کانال/گروه‌های زیر عضو بشی:\n\n{channels}\n\nبعد روی «✅ عضو شدم» بزن.",
    "join_check_btn": "✅ عضو شدم",
    "join_still_missing": "❌ هنوز تو همه‌ی موارد عضو نشدی. لطفاً عضو شو و دوباره امتحان کن.",
    "task_required": "🔗 برای دریافت فایل، اول باید رو لینک زیر بزنی:\n\n{link}\n\nبعد روی «✅ انجام دادم» بزن.",
    "task_btn": "✅ انجام دادم",
    "file_not_found": "❌ فایل پیدا نشد یا حذف شده.",
    "banned": "⛔️ شما توسط ادمین مسدود شده‌اید.",
    "not_admin": "⛔️ فقط ادمین‌ها می‌تونن فایل آپلود کنن.",
    "no_access": "⛔️ دسترسی ندارید.",
    "processing": "⏳ در حال پردازش...",
    "unsupported": "❌ نوع فایل پشتیبانی نمی‌شه.",
    "default_reply": "📎 برای دریافت فایل روی لینک کلیک کن.",
}

BTN_UPLOAD_SINGLE = "📤 آپلود تکی"
BTN_UPLOAD_GROUP  = "📦 آپلود گروهی"
BTN_GROUP_FINISH  = "✅ پایان آپلود گروهی"


def build_filebot_app(bot_id: str, token: str, channel_id: str):
    """
    یه Application کامل تلگرام برای ربات فایل‌شیر با bot_id مشخص می‌سازه.
    همه‌ی هندلرها از طریق closure به bot_id و channel_id همین ربات گره خوردن.
    """

    # ─── کمکی‌های مخصوص این ربات ───
    def bot_data():
        db = load_db()
        return db.get("bot_data", {}).get(bot_id, {
            "users": {}, "files": {}, "banned": [], "texts": {},
            "join_channels": [], "required_task": None, "packs": {},
        })

    def get_text(key, **kwargs):
        bd = bot_data()
        text = bd.get("texts", {}).get(key, DEFAULT_TEXTS.get(key, ""))
        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def is_admin(user_id):
        return is_global_admin(user_id)

    def register_user(user):
        with db_transaction() as db:
            bd = db["bot_data"].setdefault(bot_id, {
                "users": {}, "files": {}, "banned": [], "texts": {},
                "join_channels": [], "required_task": None, "packs": {},
            })
            uid = str(user.id)
            if uid not in bd["users"]:
                bd["users"][uid] = {
                    "id": user.id, "name": user.full_name,
                    "username": user.username or "",
                    "joined": datetime.now().isoformat(),
                    "downloads": 0
                }

    def is_banned(user_id):
        bd = bot_data()
        return user_id in bd.get("banned", [])

    # ─── عضویت اجباری ───
    async def check_join(bot, user_id):
        bd = bot_data()
        channels = bd.get("join_channels", [])
        if not channels:
            return True, []
        missing = []
        for ch in channels:
            try:
                member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
                if member.status in ("left", "kicked"):
                    missing.append(ch)
            except TelegramError:
                missing.append(ch)
        return len(missing) == 0, missing

    def join_keyboard(channels, file_code=None, is_pack=False):
        rows = []
        for ch in channels:
            url = ch["id"]
            if str(url).startswith("@"):
                url = f"https://t.me/{url[1:]}"
            rows.append([InlineKeyboardButton(f"📢 {ch['title']}", url=url)])
        prefix = "pcheckjoin_" if is_pack else "checkjoin_"
        rows.append([InlineKeyboardButton(get_text("join_check_btn"), callback_data=f"{prefix}{file_code or ''}")])
        return InlineKeyboardMarkup(rows)

    def task_keyboard(link, file_code=None, is_pack=False):
        prefix = "pchecktask_" if is_pack else "checktask_"
        rows = [
            [InlineKeyboardButton("🔗 رفتن به لینک", url=link)],
            [InlineKeyboardButton(get_text("task_btn"), callback_data=f"{prefix}{file_code or ''}")],
        ]
        return InlineKeyboardMarkup(rows)

    # ─── لایک/دیسلایک ───
    def file_reaction_keyboard(file_code, likes=0, dislikes=0):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"👍 {likes}", callback_data=f"like_{file_code}"),
                InlineKeyboardButton(f"👎 {dislikes}", callback_data=f"dislike_{file_code}"),
            ],
            [InlineKeyboardButton("💬 ارسال نظر", callback_data=f"comment_{file_code}")],
        ])

    async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        action, code = q.data.split("_", 1)
        with db_transaction() as db:
            bd = db["bot_data"].get(bot_id, {})
            if code not in bd.get("files", {}):
                await q.answer("فایل پیدا نشد", show_alert=True); return
            f = bd["files"][code]
            f.setdefault("liked_by", []); f.setdefault("disliked_by", [])
            uid = q.from_user.id
            if action == "like":
                if uid in f["liked_by"]:
                    f["liked_by"].remove(uid)
                else:
                    f["liked_by"].append(uid)
                    if uid in f["disliked_by"]: f["disliked_by"].remove(uid)
            else:
                if uid in f["disliked_by"]:
                    f["disliked_by"].remove(uid)
                else:
                    f["disliked_by"].append(uid)
                    if uid in f["liked_by"]: f["liked_by"].remove(uid)
            likes, dislikes = len(f["liked_by"]), len(f["disliked_by"])
        try:
            await q.edit_message_reply_markup(reply_markup=file_reaction_keyboard(code, likes, dislikes))
        except TelegramError:
            pass
        await q.answer("✅ ثبت شد")

    async def callback_comment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        file_code = q.data.split("_", 1)[1]
        context.user_data["awaiting"] = "user_comment"
        context.user_data["comment_file"] = file_code
        await q.answer()
        await context.bot.send_message(chat_id=q.from_user.id, text="💬 نظرت رو بنویس و بفرست تا برای ادمین ارسال بشه:")

    async def receive_user_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        file_code = context.user_data.pop("comment_file", None)
        context.user_data.pop("awaiting", None)
        bd = bot_data()
        file_name = bd["files"].get(file_code, {}).get("name", "نامشخص") if file_code else "—"
        text = (
            f"💬 نظر جدید از کاربر (ربات: {bot_id})\n\n"
            f"👤 {user.full_name} (@{user.username or '—'})\n"
            f"🆔 `{user.id}`\n"
            f"📁 فایل: {file_name}\n\n"
            f"📝 متن نظر:\n{update.message.text}"
        )
        db = load_db()
        for admin_id in db.get("global_admins", []):
            try:
                await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        await update.message.reply_text("✅ نظرت برای ادمین ارسال شد. ممنون از بازخوردت!")

    # ─── ارسال فایل ───
    async def deliver_file(update_or_query, context, file_code, chat_id):
        bd = bot_data()
        if file_code not in bd["files"]:
            await context.bot.send_message(chat_id=chat_id, text=get_text("file_not_found"))
            return
        info = bd["files"][file_code]
        caption = info.get("custom_caption") or get_text("file_caption", name=info.get("name", ""))

        # تایمر حذف خودکار
        auto_del = bd.get("auto_delete_seconds", 0)
        if auto_del and auto_del > 0:
            if auto_del < 60:
                timer_text = f"{auto_del} ثانیه"
            elif auto_del < 3600:
                timer_text = f"{auto_del // 60} دقیقه"
            else:
                timer_text = f"{auto_del // 3600} ساعت"
            caption = f"⚠️ این پیام {timer_text} دیگه حذف می‌شه، سیو کنید!\n\n{caption}"

        kb = file_reaction_keyboard(file_code, len(info.get("liked_by", [])), len(info.get("disliked_by", [])))
        try:
            sent = await context.bot.copy_message(
                chat_id=chat_id, from_chat_id=channel_id,
                message_id=info["message_id"], caption=caption, reply_markup=kb,
            )
            with db_transaction() as db:
                bdt = db["bot_data"].get(bot_id, {})
                if file_code in bdt.get("files", {}):
                    bdt["files"][file_code]["downloads"] = bdt["files"][file_code].get("downloads", 0) + 1
            extra = get_text("file_sent_extra")
            if extra:
                await context.bot.send_message(chat_id=chat_id, text=extra)

            # تایمر حذف
            if auto_del and auto_del > 0:
                async def _delete_later(msg_id, cid, delay):
                    await asyncio.sleep(delay)
                    try:
                        await context.bot.delete_message(chat_id=cid, message_id=msg_id)
                    except TelegramError:
                        pass
                asyncio.create_task(_delete_later(sent.message_id, chat_id, auto_del))

            return sent
        except TelegramError as e:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ خطا در ارسال فایل: {e}")

    async def deliver_pack(update_or_query, context, pack_code, chat_id):
        bd = bot_data()
        if pack_code not in bd.get("packs", {}):
            await context.bot.send_message(chat_id=chat_id, text=get_text("file_not_found"))
            return
        pack = bd["packs"][pack_code]
        auto_del = bd.get("auto_delete_seconds", 0)

        if auto_del and auto_del > 0:
            if auto_del < 60:
                timer_text = f"{auto_del} ثانیه"
            elif auto_del < 3600:
                timer_text = f"{auto_del // 60} دقیقه"
            else:
                timer_text = f"{auto_del // 3600} ساعت"
            warn_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ این پیام‌ها {timer_text} دیگه حذف می‌شن، سیو کنید!\n\n📦 ارسال پک ({len(pack['files'])} فایل)..."
            )
        else:
            warn_msg = await context.bot.send_message(chat_id=chat_id, text=f"📦 ارسال پک ({len(pack['files'])} فایل)...")

        sent_ids = [warn_msg.message_id]
        for code in pack["files"]:
            sent = await deliver_file(update_or_query, context, code, chat_id)
            if sent:
                sent_ids.append(sent.message_id)
            await asyncio.sleep(0.3)

        with db_transaction() as db:
            bdt = db["bot_data"].get(bot_id, {})
            if pack_code in bdt.get("packs", {}):
                bdt["packs"][pack_code]["downloads"] = bdt["packs"][pack_code].get("downloads", 0) + 1

        if auto_del and auto_del > 0:
            async def _delete_pack_later(msg_ids, cid, delay):
                await asyncio.sleep(delay)
                for mid in msg_ids:
                    try:
                        await context.bot.delete_message(chat_id=cid, message_id=mid)
                    except TelegramError:
                        pass
            asyncio.create_task(_delete_pack_later(sent_ids, chat_id, auto_del))

    # ─── جریان گیت‌ها: بن → عضویت → وظیفه → ارسال ───
    async def try_deliver_with_gates(update: Update, context: ContextTypes.DEFAULT_TYPE, file_code, is_pack=False):
        user = update.effective_user
        chat_id = update.effective_chat.id

        if is_banned(user.id):
            await context.bot.send_message(chat_id=chat_id, text=get_text("banned"))
            return

        ok, missing = await check_join(context.bot, user.id)
        if not ok:
            context.user_data["pending_file"] = file_code
            context.user_data["pending_is_pack"] = is_pack
            await context.bot.send_message(
                chat_id=chat_id,
                text=get_text("join_required", channels="\n".join(f"• {c['title']}" for c in missing)),
                reply_markup=join_keyboard(missing, file_code, is_pack),
            )
            return

        bd = bot_data()
        task = bd.get("required_task")
        if task and str(user.id) not in task.get("done_by", []):
            context.user_data["pending_file"] = file_code
            context.user_data["pending_is_pack"] = is_pack
            await context.bot.send_message(
                chat_id=chat_id,
                text=get_text("task_required", link=task["link"]),
                reply_markup=task_keyboard(task["link"], file_code, is_pack),
            )
            return

        if is_pack:
            await deliver_pack(update, context, file_code, chat_id)
        else:
            await deliver_file(update, context, file_code, chat_id)

    # ─── /start ───
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        register_user(user)

        if is_banned(user.id):
            await update.message.reply_text(get_text("banned"))
            return

        if context.args and context.args[0].startswith("file_"):
            await try_deliver_with_gates(update, context, context.args[0][5:])
            return
        if context.args and context.args[0].startswith("pack_"):
            await try_deliver_with_gates(update, context, context.args[0][5:], is_pack=True)
            return

        text = get_text("start", name=user.first_name)
        if is_admin(user.id):
            text += get_text("start_admin_suffix")
            await update.message.reply_text(text, reply_markup=admin_reply_keyboard())
        else:
            await update.message.reply_text(text)

    async def callback_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE, is_pack=False):
        q = update.callback_query
        file_code = q.data.split("_", 1)[1]
        ok, missing = await check_join(context.bot, q.from_user.id)
        if not ok:
            await q.answer(get_text("join_still_missing"), show_alert=True)
            return
        await q.answer("✅")
        try:
            await q.message.delete()
        except TelegramError:
            pass
        if file_code:
            await try_deliver_with_gates(update, context, file_code, is_pack=is_pack)

    async def callback_check_task(update: Update, context: ContextTypes.DEFAULT_TYPE, is_pack=False):
        q = update.callback_query
        file_code = q.data.split("_", 1)[1]
        with db_transaction() as db:
            bd = db["bot_data"].get(bot_id, {})
            task = bd.get("required_task")
            if task:
                task.setdefault("done_by", [])
                uid = str(q.from_user.id)
                if uid not in task["done_by"]:
                    task["done_by"].append(uid)
        await q.answer("✅")
        try:
            await q.message.delete()
        except TelegramError:
            pass
        if file_code:
            await try_deliver_with_gates(update, context, file_code, is_pack=is_pack)

    # ─── آپلود تکی/گروهی توسط ادمین ───
    def admin_reply_keyboard():
        return ReplyKeyboardMarkup([[BTN_UPLOAD_SINGLE, BTN_UPLOAD_GROUP]], resize_keyboard=True)

    def group_active_keyboard():
        return ReplyKeyboardMarkup([[BTN_GROUP_FINISH]], resize_keyboard=True)

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

    async def start_single_upload(update, context):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(get_text("no_access")); return
        context.user_data["upload_mode"] = "single"
        context.user_data.pop("group_pack", None)
        await update.message.reply_text("📤 حالت آپلود تکی فعاله.", reply_markup=admin_reply_keyboard())

    async def start_group_upload(update, context):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(get_text("no_access")); return
        context.user_data["upload_mode"] = "group"
        context.user_data["group_pack"] = []
        await update.message.reply_text(
            "📦 حالت آپلود گروهی فعاله. فایل‌ها رو بفرست، آخرش «✅ پایان آپلود گروهی» رو بزن.",
            reply_markup=group_active_keyboard()
        )

    async def finish_group_upload(update, context):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text(get_text("no_access")); return
        pack = context.user_data.get("group_pack", [])
        if not pack:
            await update.message.reply_text("هنوز فایلی نفرستادی.", reply_markup=group_active_keyboard())
            return
        pack_code = f"pack{pack[0]}{len(pack)}{int(datetime.now().timestamp())%100000}"
        with db_transaction() as db:
            bd = db["bot_data"].setdefault(bot_id, {"packs": {}})
            bd.setdefault("packs", {})[pack_code] = {
                "code": pack_code, "files": pack, "uploaded_by": user.id,
                "uploaded_at": datetime.now().isoformat(), "downloads": 0,
            }
        me = (await context.bot.get_me()).username
        link = f"https://t.me/{me}?start=pack_{pack_code}"
        context.user_data["upload_mode"] = "single"
        context.user_data.pop("group_pack", None)
        await update.message.reply_text(
            f"✅ پک ساخته شد!\n📦 فایل‌ها: {len(pack)}\n🔗 لینک:\n`{link}`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_reply_keyboard()
        )

    async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text(get_text("not_admin"))
            return
        msg = update.message
        file_id, file_name, file_type = extract_file_info(msg)
        if not file_id:
            await msg.reply_text(get_text("unsupported")); return

        custom_caption = msg.caption if msg.caption else None
        mode = context.user_data.get("upload_mode", "single")

        try:
            fwd = await msg.forward(chat_id=channel_id)
            code = f"{file_id[-8:]}{fwd.message_id}"
            with db_transaction() as db:
                bd = db["bot_data"].setdefault(bot_id, {"files": {}})
                bd.setdefault("files", {})[code] = {
                    "code": code, "name": file_name, "type": file_type,
                    "message_id": fwd.message_id, "uploaded_by": user.id,
                    "uploaded_at": datetime.now().isoformat(),
                    "downloads": 0, "liked_by": [], "disliked_by": [],
                    "custom_caption": custom_caption,
                }

            if mode == "group":
                context.user_data.setdefault("group_pack", []).append(code)
                count = len(context.user_data["group_pack"])
                await msg.reply_text(f"➕ اضافه شد ({count} فایل تا الان).")
            else:
                me = (await context.bot.get_me()).username
                link = f"https://t.me/{me}?start=file_{code}"
                await msg.reply_text(
                    f"✅ آپلود شد!\n📁 {file_name}\n🔑 کد: `{code}`\n\n🔗 لینک:\n`{link}`",
                    parse_mode=ParseMode.MARKDOWN
                )
        except TelegramError as e:
            await msg.reply_text(f"❌ خطا: {e}\n⚠️ ربات باید ادمین کانال ذخیره باشه.")

    # ─── پنل ادمین ───
    def main_admin_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 فایل‌ها", callback_data="m_files"),
             InlineKeyboardButton("👥 کاربران", callback_data="m_users")],
            [InlineKeyboardButton("🔒 عضویت اجباری", callback_data="m_join"),
             InlineKeyboardButton("🔗 وظیفه اجباری", callback_data="m_task")],
            [InlineKeyboardButton("📢 پیام همگانی", callback_data="m_broadcast"),
             InlineKeyboardButton("✉️ پیام به کاربر", callback_data="m_dm")],
            [InlineKeyboardButton("✏️ متن‌ها", callback_data="m_texts"),
             InlineKeyboardButton("📊 آمار", callback_data="m_stats")],
        ])

    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(get_text("no_access")); return
        await update.message.reply_text(f"🔧 پنل مدیریت ربات «{bot_id}»", reply_markup=main_admin_menu())

    def back_kb(target="m_back"):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data=target)]])

    def files_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 لیست فایل‌ها", callback_data="f_list")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="m_back")],
        ])

    def users_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 لیست کاربران", callback_data="u_list")],
            [InlineKeyboardButton("🚫 بن کردن", callback_data="u_ban")],
            [InlineKeyboardButton("✅ آنبن کردن", callback_data="u_unban")],
            [InlineKeyboardButton("📋 لیست مسدودها", callback_data="u_banlist")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="m_back")],
        ])

    def join_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن کانال/گروه", callback_data="j_add")],
            [InlineKeyboardButton("📋 لیست فعلی", callback_data="j_list")],
            [InlineKeyboardButton("🗑 حذف یکی", callback_data="j_remove")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="m_back")],
        ])

    def task_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ تنظیم/تغییر لینک", callback_data="t_set")],
            [InlineKeyboardButton("🗑 حذف وظیفه", callback_data="t_remove")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="m_back")],
        ])

    def texts_menu():
        keys = [
            ("start", "متن استارت"), ("file_caption", "کپشن پیش‌فرض"),
            ("file_sent_extra", "پیام بعد ارسال"),
            ("join_required", "متن عضویت اجباری"), ("task_required", "متن وظیفه اجباری"),
        ]
        rows = [[InlineKeyboardButton(label, callback_data=f"tx_{key}")] for key, label in keys]
        rows.append([InlineKeyboardButton("🔙 برگشت", callback_data="m_back")])
        return InlineKeyboardMarkup(rows)

    async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if not is_admin(q.from_user.id):
            await q.answer(get_text("no_access"), show_alert=True); return
        await q.answer()
        data = q.data
        bd = bot_data()

        if data == "m_back":
            context.user_data.pop("awaiting", None)
            await q.edit_message_text(f"🔧 پنل مدیریت ربات «{bot_id}»", reply_markup=main_admin_menu()); return

        if data == "m_files":
            await q.edit_message_text("📁 مدیریت فایل‌ها", reply_markup=files_menu()); return

        if data == "f_list":
            files = list(bd["files"].values())[-20:]
            if not files:
                text = "هیچ فایلی ثبت نشده."
            else:
                text = "📋 فایل‌ها:\n\n" + "\n".join(
                    f"• {f['name']} | `{f['code']}` | ⬇️{f.get('downloads',0)} | 👍{len(f.get('liked_by',[]))} 👎{len(f.get('disliked_by',[]))}"
                    for f in files
                )
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb("m_files")); return

        if data == "m_users":
            await q.edit_message_text("👥 مدیریت کاربران", reply_markup=users_menu()); return

        if data == "u_list":
            users = list(bd["users"].values())[-20:]
            text = f"👥 کاربران ({len(bd['users'])} نفر):\n\n" + "\n".join(
                f"• {u['name']} | `{u['id']}` | ⬇️{u.get('downloads',0)}" for u in users)
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb("m_users")); return

        if data == "u_banlist":
            banned = bd.get("banned", [])
            text = "🚫 مسدودها:\n\n" + ("\n".join(f"• `{b}`" for b in banned) if banned else "خالیه.")
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb("m_users")); return

        if data == "u_ban":
            context.user_data["awaiting"] = "ban"
            await q.edit_message_text("🚫 آیدی عددی کاربر:", reply_markup=back_kb("m_users")); return

        if data == "u_unban":
            context.user_data["awaiting"] = "unban"
            await q.edit_message_text("✅ آیدی عددی کاربر:", reply_markup=back_kb("m_users")); return

        if data == "m_join":
            await q.edit_message_text("🔒 عضویت اجباری", reply_markup=join_menu()); return

        if data == "j_add":
            context.user_data["awaiting"] = "join_add"
            await q.edit_message_text("➕ آیدی کانال/گروه (مثلاً @mychannel):", reply_markup=back_kb("m_join")); return

        if data == "j_list":
            channels = bd.get("join_channels", [])
            text = "📋 کانال‌ها:\n\n" + ("\n".join(f"• {c['title']} ({c['id']})" for c in channels) if channels else "خالیه.")
            await q.edit_message_text(text, reply_markup=back_kb("m_join")); return

        if data == "j_remove":
            channels = bd.get("join_channels", [])
            if not channels:
                await q.edit_message_text("لیست خالیه.", reply_markup=back_kb("m_join")); return
            rows = [[InlineKeyboardButton(f"🗑 {c['title']}", callback_data=f"jrm_{i}")] for i, c in enumerate(channels)]
            rows.append([InlineKeyboardButton("🔙 برگشت", callback_data="m_join")])
            await q.edit_message_text("کدوم رو حذف کنم؟", reply_markup=InlineKeyboardMarkup(rows)); return

        if data.startswith("jrm_"):
            idx = int(data.split("_")[1])
            with db_transaction() as db:
                bdt = db["bot_data"].get(bot_id, {})
                channels = bdt.get("join_channels", [])
                if 0 <= idx < len(channels):
                    removed = channels.pop(idx)
            await q.edit_message_text(f"✅ حذف شد.", reply_markup=back_kb("m_join")); return

        if data == "m_task":
            task = bd.get("required_task")
            status = f"فعلی: {task['link']}" if task else "فعلاً تنظیم نشده."
            await q.edit_message_text(f"🔗 وظیفه اجباری\n\n{status}", reply_markup=task_menu()); return

        if data == "t_set":
            context.user_data["awaiting"] = "task_set"
            await q.edit_message_text("🔗 لینک وظیفه رو بفرست:", reply_markup=back_kb("m_task")); return

        if data == "t_remove":
            with db_transaction() as db:
                db["bot_data"].setdefault(bot_id, {})["required_task"] = None
            await q.edit_message_text("✅ حذف شد.", reply_markup=back_kb("m_task")); return

        if data == "m_broadcast":
            context.user_data["awaiting"] = "broadcast"
            await q.edit_message_text("📢 پیام رو بفرست:", reply_markup=back_kb("m_back")); return

        if data == "m_dm":
            context.user_data["awaiting"] = "dm_id"
            await q.edit_message_text("✉️ آیدی عددی کاربر مقصد:", reply_markup=back_kb("m_back")); return

        if data == "m_texts":
            await q.edit_message_text("✏️ کدوم متن؟", reply_markup=texts_menu()); return

        if data.startswith("tx_"):
            key = data[3:]
            context.user_data["awaiting"] = f"text_{key}"
            current = get_text(key)
            await q.edit_message_text(f"متن فعلی:\n\n{current}\n\n— متن جدید رو بفرست:", reply_markup=back_kb("m_texts")); return

        if data == "m_stats":
            text = (
                f"📊 آمار ربات «{bot_id}»\n\n"
                f"👥 کاربران: {len(bd['users'])}\n"
                f"🚫 مسدودها: {len(bd.get('banned',[]))}\n"
                f"📁 فایل‌ها: {len(bd['files'])}\n"
                f"📥 دانلودها: {sum(f.get('downloads',0) for f in bd['files'].values())}"
            )
            await q.edit_message_text(text, reply_markup=back_kb("m_back")); return

    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        register_user(user)
        text_raw = update.message.text.strip() if update.message.text else ""
        awaiting = context.user_data.get("awaiting")

        if is_admin(user.id):
            if text_raw == BTN_UPLOAD_SINGLE:
                await start_single_upload(update, context); return
            if text_raw == BTN_UPLOAD_GROUP:
                await start_group_upload(update, context); return
            if text_raw == BTN_GROUP_FINISH:
                await finish_group_upload(update, context); return

        if awaiting == "user_comment" and not is_admin(user.id):
            await receive_user_comment(update, context); return

        if not is_admin(user.id) or not awaiting:
            if is_banned(user.id):
                await update.message.reply_text(get_text("banned")); return
            await update.message.reply_text(get_text("default_reply"))
            return

        text = text_raw

        if awaiting == "ban":
            if text.lstrip("-").isdigit():
                uid = int(text)
                with db_transaction() as db:
                    bdt = db["bot_data"].setdefault(bot_id, {"banned": []})
                    bdt.setdefault("banned", [])
                    if uid not in bdt["banned"]:
                        bdt["banned"].append(uid)
                await update.message.reply_text(f"🚫 {uid} بن شد.", reply_markup=main_admin_menu())
            else:
                await update.message.reply_text("❌ آیدی نامعتبره.")
            context.user_data.pop("awaiting", None); return

        if awaiting == "unban":
            if text.lstrip("-").isdigit():
                uid = int(text)
                with db_transaction() as db:
                    bdt = db["bot_data"].setdefault(bot_id, {"banned": []})
                    if uid in bdt.get("banned", []):
                        bdt["banned"].remove(uid)
                await update.message.reply_text(f"✅ {uid} آنبن شد.", reply_markup=main_admin_menu())
            else:
                await update.message.reply_text("❌ آیدی نامعتبره.")
            context.user_data.pop("awaiting", None); return

        if awaiting == "join_add":
            context.user_data["join_add_id"] = text
            context.user_data["awaiting"] = "join_add_title"
            await update.message.reply_text("اسم نمایشی رو بفرست:")
            return

        if awaiting == "join_add_title":
            ch_id = context.user_data.pop("join_add_id", text)
            with db_transaction() as db:
                bdt = db["bot_data"].setdefault(bot_id, {"join_channels": []})
                bdt.setdefault("join_channels", []).append({"id": ch_id, "title": text})
            await update.message.reply_text(f"✅ اضافه شد: {text}", reply_markup=main_admin_menu())
            context.user_data.pop("awaiting", None); return

        if awaiting == "task_set":
            with db_transaction() as db:
                db["bot_data"].setdefault(bot_id, {})["required_task"] = {"link": text, "done_by": []}
            await update.message.reply_text("✅ تنظیم شد.", reply_markup=main_admin_menu())
            context.user_data.pop("awaiting", None); return

        if awaiting == "broadcast":
            bd2 = bot_data()
            users = list(bd2["users"].keys())
            sent = failed = 0
            status = await update.message.reply_text(f"⏳ ارسال به {len(users)} کاربر...")
            for uid in users:
                try:
                    await context.bot.send_message(chat_id=int(uid), text=text)
                    sent += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    failed += 1
            await status.edit_text(f"✅ ارسال شد!\n✔️ موفق: {sent}\n❌ ناموفق: {failed}", reply_markup=main_admin_menu())
            context.user_data.pop("awaiting", None); return

        if awaiting == "dm_id":
            if text.lstrip("-").isdigit():
                context.user_data["dm_target"] = int(text)
                context.user_data["awaiting"] = "dm_text"
                await update.message.reply_text("متن پیام رو بفرست:")
            else:
                await update.message.reply_text("❌ آیدی نامعتبره.")
            return

        if awaiting == "dm_text":
            target = context.user_data.pop("dm_target", None)
            context.user_data.pop("awaiting", None)
            if target:
                try:
                    await context.bot.send_message(chat_id=target, text=text)
                    await update.message.reply_text("✅ ارسال شد.", reply_markup=main_admin_menu())
                except Exception as e:
                    await update.message.reply_text(f"❌ خطا: {e}", reply_markup=main_admin_menu())
            return

        if awaiting and awaiting.startswith("text_"):
            key = awaiting[5:]
            with db_transaction() as db:
                bdt = db["bot_data"].setdefault(bot_id, {"texts": {}})
                bdt.setdefault("texts", {})[key] = text
            await update.message.reply_text("✅ متن آپدیت شد.", reply_markup=main_admin_menu())
            context.user_data.pop("awaiting", None); return

    async def delfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(get_text("no_access")); return
        if not context.args:
            await update.message.reply_text("استفاده: /delfile کد_فایل"); return
        code = context.args[0]
        with db_transaction() as db:
            bdt = db["bot_data"].get(bot_id, {})
            if code in bdt.get("files", {}):
                del bdt["files"][code]
                await update.message.reply_text("✅ حذف شد.")
            else:
                await update.message.reply_text("❌ پیدا نشد.")

    async def caption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(get_text("no_access")); return
        if len(context.args) < 2:
            await update.message.reply_text("استفاده: /caption کد_فایل متن جدید"); return
        code = context.args[0]
        new_caption = " ".join(context.args[1:])
        with db_transaction() as db:
            bdt = db["bot_data"].get(bot_id, {})
            if code in bdt.get("files", {}):
                bdt["files"][code]["custom_caption"] = new_caption
                await update.message.reply_text("✅ کپشن آپدیت شد.")
            else:
                await update.message.reply_text("❌ پیدا نشد.")

    async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if q.data.startswith("like_") or q.data.startswith("dislike_"):
            await handle_reaction(update, context)
        elif q.data.startswith("comment_"):
            await callback_comment_start(update, context)
        elif q.data.startswith("pcheckjoin_"):
            await callback_check_join(update, context, is_pack=True)
        elif q.data.startswith("checkjoin_"):
            await callback_check_join(update, context, is_pack=False)
        elif q.data.startswith("pchecktask_"):
            await callback_check_task(update, context, is_pack=True)
        elif q.data.startswith("checktask_"):
            await callback_check_task(update, context, is_pack=False)
        else:
            await admin_router(update, context)

    # ─── ساخت Application ───
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("delfile", delfile_cmd))
    app.add_handler(CommandHandler("caption", caption_cmd))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE,
        handle_file
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_router))

    return app
