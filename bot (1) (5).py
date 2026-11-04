# -*- coding: utf-8 -*-
"""
بات شرط‌بندی الماس با Webhook - نسخه Supabase (کامل)
اضافه شده: پنل مدیریت، رفع باگ شرط
"""

import os
import random
import re
import string
import time
import threading
import itertools
import math
import collections
import logging
import json
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request
import telebot
from telebot import types
from supabase import create_client, Client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# ================== استایل رنگی دکمه‌های تلگرام ==================
def colored_button(*args, **kwargs):
    """ساخت دکمه Inline با یکی از سه استایل رسمی Telegram: primary/success/danger.
    رنگ بر اساس متن دکمه به‌صورت خودکار انتخاب می‌شود.

    icon_custom_emoji_id: در صورت پاس‌دادن، آیدی ایموجی سفارشی/پرمیومی که باید
    قبل از متن دکمه نمایش داده شود (فیلد رسمی Bot API 9.4، icon_custom_emoji_id).
    توجه: طبق مستندات تلگرام این فیلد فقط زمانی روی کلاینت واقعاً رندر می‌شود که
    مالک بات (اکانتی که از طریق BotFather بات رو ساخته) اشتراک Telegram Premium
    داشته باشد؛ در غیر این صورت تلگرام این فیلد را نادیده می‌گیرد و فقط متن دکمه دیده می‌شود.
    """
    icon_emoji_id = kwargs.pop("icon_custom_emoji_id", None)

    if "style" in kwargs:
        button = types.InlineKeyboardButton(*args, **kwargs)
    else:
        text = str(args[0] if args else kwargs.get("text", "")).lower()

        danger_words = (
            "لغو", "انصراف", "خیر", "بستن", "حذف", "پاک", "رد", "بازگشت", "مالیات",
            "cancel", "close", "delete", "no", "reject"
        )
        success_words = (
            "بله", "تأیید", "تایید", "خرید", "برداشت", "واریز", "دریافت",
            "شروع", "پیوستن", "ارتقا", "فعال", "قبول", "انجام", "ثبت",
            "جایزه", "برداشت الماس", "yes", "confirm", "buy", "start", "join",
            "upgrade", "claim"
        )

        if any(word in text for word in danger_words):
            style = "danger"
        elif any(word in text for word in success_words):
            style = "success"
        else:
            style = "primary"

        try:
            button = types.InlineKeyboardButton(*args, **kwargs)
            # در نسخه‌های جدید pyTelegramBotAPI این فیلد مستقیماً پشتیبانی می‌شود؛
            # در نسخه‌های قدیمی‌تر هم اضافه‌کردن attribute باعث می‌شود serializer آن را بفرستد.
            setattr(button, "style", style)
        except TypeError:
            # اگر نسخه قدیمی کتابخانه style را در سازنده قبول نکند، دکمه عادی ساخته می‌شود.
            # (برای فعال شدن رنگ‌ها، کتابخانه باید نسخه‌ای با پشتیبانی style داشته باشد.)
            button = types.InlineKeyboardButton(*args, **kwargs)

    if icon_emoji_id is not None:
        try:
            setattr(button, "icon_custom_emoji_id", str(icon_emoji_id))
        except Exception:
            pass

    return button


BACK_EMOJI_ID = "5832364184066614283"


def back_button(callback_data):
    """دکمه‌ی یکسان «بازگشت»: فقط ایموجی پرمیوم، بدون متن قابل‌دیدن.
    تلگرام متن خالی قبول نمی‌کند، پس یک کاراکتر نامرئی (Braille blank) می‌گذاریم."""
    button = colored_button("\u2800", callback_data=callback_data, icon_custom_emoji_id=BACK_EMOJI_ID)
    # رنگ قرمز قبلی (هم‌رنگ دکمه‌های «بازگشت» قدیمی) حفظ شود
    setattr(button, "style", "danger")
    return button


load_dotenv()

# ================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "توکن-خودت-رو-اینجا-بذار")
ADMIN_IDS = [8904869158, 8196150649,]  # آیدی عددی خود را اینجا قرار دهید
START_DIAMONDS = 25000
TAX_RATE = 0.10          # شرطبندی و دینامیت
TAX_RATE_CASINO = 0.10   # کازینو
TAX_RECEIVER_ID = ADMIN_IDS[0]
JOIN_TIMEOUT_SECONDS = 60

# ================== الماس تصادفی گروه ==================
DIAMOND_HUNT_MESSAGE_INTERVAL = 200
DIAMOND_HUNT_COSTS = [100, 200, 400]
DIAMOND_HUNT_WIN_CHANCES = [0.30, 0.50, 0.70]
DIAMOND_HUNT_REASONS = [
    "باعث شد الماس از دستش بیافته و بشکنه❌",
    "باعث شد الماس گم بشه❌",
    "باعث شد الماس از دستش لیز بخوره و بشکنه❌",
    "باعث شد الماس ناپدید بشه❌",
    "باعث شد الماس توسط یک نفر دزدیده بشه❌",
    "باعث شد الماس از بین بره❌",
]


# ================== سیستم سطح‌بندی (Leveling) ==================
# هر آیتم: (سطح، عنوان، XP لازم برای رسیدن به این سطح، پاداش الماس هنگام رسیدن به این سطح)
LEVELS = [
    (1,  "الماس‌یاب تازه‌کار",   0,          0),
    (2,  "الماس‌یاب مبتدی",      100,        500),
    (3,  "الماس‌یاب کارآموز",    250,        650),
    (4,  "الماس‌یاب نیمه‌حرفه‌ای", 500,        900),
    (5,  "الماس‌یاب حرفه‌ای",     800,        1_200),
    (6,  "الماس‌یاب خبره",       1_200,      1_600),
    (7,  "استاد الماس‌یابی",      1_800,      2_100),
    (8,  "افسانه الماس‌یابی",     2_500,      2_800),
    (9,  "سلطان الماس",          3_500,      3_700),
    (10, "پادشاه الماس",         5_000,      5_000),
]
MAX_LEVEL = LEVELS[-1][0]

XP_PER_BET_WIN = 10
XP_PER_CASINO_WIN = 15
XP_PER_RING_DIAMOND = 50
XP_PER_DAILY_GIFT = 5
XP_BONUS_PER_CASINO_100K = 1     # هر ۱۰۰,۰۰۰ الماس برد در کازینو ۱ XP اضافه
XP_BONUS_CASINO_CAP = 50

# ================== لقب‌های ویژه (جعبه شانس حذف شد؛ لقب‌ها نگه داشته شده‌اند) ==================
LOOTBOX_TAGS = ["🗣️ خوشتیپ", "🗿 کصخل", "🎲 قمارباز", "🤌 بچه مثبت", "💰 بچه پولدار", "❤️ عشق", "😍 خوشگله", "🧑‍🦯 بچه روبیکایی", "🤓 نمک"]

# ================== تنظیمات پیش‌فرض اعلان‌های گروه ==================
NOTIFY_DEFAULTS = {
    "level_up": True,
    "new_diamond": True,
    "daily_gift": False,
    "diamond_timer": True,
    "betting": False,
}
NOTIFY_LABELS = {
    "level_up": "🏆 ارتقا سطح",
    "new_diamond": "💎 الماس جدید",
    "daily_gift": "🎁 هدیه روزانه",
    "diamond_timer": "⏰ تایمر الماس",
    "betting": "🎲 شرط‌بندی",
}

# ================== تنظیمات بانک ==================
BANK_OPENING_FEE = 5_000
BANK_INTEREST_RATE = 0.03
BANK_DAILY_INTEREST_MAX = 1_000
BANK_CARD_FEE_RATE = 0.03          # کارمزد کارت به کارت (از پنل ادمین قابل تغییره)
TRANSFER_MAX_AMOUNT = 10_000       # سقف هر بار انتقال الماس
TRANSFER_COOLDOWN_SECONDS = 30     # فاصله‌ی بین دو انتقال الماس
TEHRAN_TZ = ZoneInfo("Asia/Tehran")

bot = telebot.TeleBot(BOT_TOKEN)

# ================== لاگ کندی‌ها (برای دیباک) ==================
_BOOT_TIME = time.time()
_PERF_EVENTS = collections.deque(maxlen=25)

def _perf_note(kind, secs, info=""):
    """کندی‌ها (آپدیت، دیتابیس، صف تلگرام) رو برای پنل دیباک و لاگ ثبت می‌کنه."""
    try:
        _PERF_EVENTS.append((time.time(), kind, round(float(secs), 2), str(info)[:90]))
        logging.warning("کندی [%s] %.2fs %s", kind, secs, str(info)[:90])
    except Exception:
        pass

# ================== ریپلای خودکار روی پیام کاربر ==================
# کاربر خواسته هر وقت چیزی می‌فرسته (مثلاً «موجودی») و بات پیام جدیدی در
# جواب می‌فرسته، اون پیام به‌صورت ریپلای روی پیام خودِ کاربر باشه (نه یه
# پیام جدای معلق توی چت). به‌جای دستکاری تک‌تک صدها جای فایل که
# bot.send_message صدا می‌زنن، خودِ متد send_message رو Wrap می‌کنیم: هر
# وقت داریم داخل پردازش یک «پیام متنی از کاربر» هستیم (نه یک callback
# دکمه)، و صدازننده صریحاً reply_to_message_id نداده، خودکار reply_to همون
# پیام کاربر ست می‌شه. برای هر ترد/ریکوئست جدا نگه داشته می‌شه تا در حالت
# threaded با هم قاطی نشن.
_reply_ctx = threading.local()

# Keep the original pyTelegramBotAPI methods. All Telegram traffic that can be
# generated by this file is routed through the small gate below. The goal is
# not to drop work: requests are serialized and a Telegram 429 is respected.
_original_send_message = bot.send_message
_original_edit_message_text = bot.edit_message_text
_original_edit_message_reply_markup = bot.edit_message_reply_markup
_original_delete_message = bot.delete_message
_original_answer_callback_query = bot.answer_callback_query

# ================== Telegram API durability / rate-limit gate ==================
# قبلاً یک RLock سراسری هم دور محاسبه‌ی نوبت‌دهی و هم دور *خودِ درخواست شبکه‌ای*
# تلگرام گرفته می‌شد؛ یعنی در تمام مدتی که منتظر پاسخ تلگرام بودیم (صد میلی‌ثانیه‌ها)
# هیچ پیام دیگری از هیچ کاربر/گروه دیگری هم نمی‌توانست ارسال/ویرایش شود — کل بات
# عملاً تک‌نفره می‌شد. الان لاک فقط دور بخش حسابداریِ نوبت‌دهی (چند خط، بدون I/O)
# گرفته می‌شود؛ خودِ sleep و خودِ درخواست شبکه‌ای بیرون از لاک انجام می‌شوند، در حالی
# که فاصله‌ی زمانی تضمین‌شده بین درخواست‌ها (global و per-chat) دقیقاً همان قبلی
# می‌ماند — یعنی محافظت در برابر 429 حفظ می‌شود، فقط دیگر کل بات را سریال نمی‌کند.
_telegram_state_lock = threading.Lock()
_telegram_next_allowed_at = 0.0
_telegram_last_request_by_chat = {}
_TELEGRAM_GLOBAL_MIN_INTERVAL = 0.055
_TELEGRAM_CHAT_MIN_INTERVAL = 0.18
_TELEGRAM_429_SAFETY_MARGIN = 1.0
_TELEGRAM_MAX_429_RETRIES = 12
_TELEGRAM_STATE_MAX = 10000

# قفل جداگانه به‌ازای هر پیام (chat_id, message_id) برای ویرایش‌ها: فقط تضمین
# می‌کند دو ویرایشِ *همان پیام* همدیگر را overtake نکنند؛ ویرایش پیام‌های دیگر
# (که قبلاً به‌خاطر لاک سراسری معطل می‌ماندند) الان آزادانه و موازی پیش می‌روند.
_telegram_edit_locks_meta_lock = threading.Lock()
_telegram_edit_locks = {}


def _get_telegram_edit_lock(chat_id, message_id):
    key = (chat_id, message_id)
    with _telegram_edit_locks_meta_lock:
        lock = _telegram_edit_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _telegram_edit_locks[key] = lock
            if len(_telegram_edit_locks) > _TELEGRAM_STATE_MAX:
                for k in list(_telegram_edit_locks)[:max(1, _TELEGRAM_STATE_MAX // 10)]:
                    _telegram_edit_locks.pop(k, None)
        return lock

# Successful edit fingerprints are intentionally bounded. This is only a
# duplicate-edit guard; it never replaces an edit that has not succeeded.
_edit_success_cache = {}
_EDIT_CACHE_MAX = 5000


def _edit_fingerprint(value):
    if value is None:
        return None
    try:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:
        return repr(value)


def _edit_cache_key(chat_id, message_id, kind):
    return (str(chat_id), int(message_id), kind)


def _remember_edit_success(key, fingerprint):
    _edit_success_cache[key] = fingerprint
    if len(_edit_success_cache) > _EDIT_CACHE_MAX:
        for old_key in list(_edit_success_cache.keys())[:max(1, _EDIT_CACHE_MAX // 10)]:
            _edit_success_cache.pop(old_key, None)


def _extract_retry_after(exc):
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            return max(1.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    text = str(exc).lower()
    match = re.search(r"retry\s+after\s+(\d+(?:\.\d+)?)", text)
    if match:
        return max(1.0, float(match.group(1)))
    return None


def _is_rate_limit_error(exc):
    text = str(exc).lower()
    return (_extract_retry_after(exc) is not None or
            "too many requests" in text or "429" in text)


def _is_message_not_modified(exc):
    return "message is not modified" in str(exc).lower()


def _is_permanent_message_error(exc):
    text = str(exc).lower()
    return (
        "message to edit not found" in text or
        "message can't be edited" in text or
        "message to delete not found" in text or
        "message not found" in text
    )


def _chat_id_from_call(args, kwargs):
    if "chat_id" in kwargs:
        return kwargs.get("chat_id")
    if args:
        return args[0]
    return None


def _wait_for_telegram_slot(chat_id=None):
    """Global + per-chat pacing. Never marks a request as successful.

    نوبت (زمان مجاز بعدی) به‌صورت اتمی و سریع رزرو می‌شود (زیر لاک، بدون I/O)،
    اما خودِ sleep بیرون از لاک انجام می‌شود؛ بنابراین چند thread می‌توانند
    هم‌زمان منتظر نوبتِ خودشان بمانند و درخواست شبکه‌ایشان را هم‌پوشان بزنند،
    درحالی‌که فاصله‌ی حداقلیِ تضمین‌شده بین *شروعِ* درخواست‌ها (global/per-chat)
    دقیقاً مثل قبل رعایت می‌شود."""
    global _telegram_next_allowed_at
    now = time.monotonic()
    with _telegram_state_lock:
        wait_until = max(now, _telegram_next_allowed_at)
        if chat_id is not None:
            wait_until = max(wait_until,
                             _telegram_last_request_by_chat.get(str(chat_id), 0.0))
        _telegram_next_allowed_at = wait_until + _TELEGRAM_GLOBAL_MIN_INTERVAL
        if chat_id is not None:
            _telegram_last_request_by_chat[str(chat_id)] = wait_until + _TELEGRAM_CHAT_MIN_INTERVAL
            if len(_telegram_last_request_by_chat) > _TELEGRAM_STATE_MAX:
                for k in list(_telegram_last_request_by_chat)[:max(1, _TELEGRAM_STATE_MAX // 10)]:
                    _telegram_last_request_by_chat.pop(k, None)

    delay = wait_until - now
    if delay > 0:
        if delay > 1.0:
            _perf_note("tg-queue", delay, f"chat={chat_id}")
        time.sleep(delay)


def _telegram_backlog():
    """چند ثانیه صف درخواست‌های تلگرام عقبه (برای رد کردن ادیت‌های غیرضروری)."""
    return max(0.0, _telegram_next_allowed_at - time.monotonic())


def _telegram_call_with_429_retry(func, *args, chat_id=None, max_429_retries=_TELEGRAM_MAX_429_RETRIES, **kwargs):
    """Serialize Telegram calls and honor Bot API retry_after exactly.

    We deliberately do NOT blindly retry arbitrary network errors for send_message:
    an unknown transport failure after Telegram accepted a request can otherwise
    create duplicate messages.  429 is safe to retry because Telegram explicitly
    tells us when to try again.
    """
    global _telegram_next_allowed_at
    retries_429 = 0

    while True:
        _wait_for_telegram_slot(chat_id)
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if not _is_rate_limit_error(exc):
                raise
            retries_429 += 1
            if retries_429 > max_429_retries:
                logging.error(
                    "Telegram 429 persisted after %s retries for %s: %s",
                    max_429_retries, getattr(func, "__name__", "api_call"), exc
                )
                raise
            retry_after = _extract_retry_after(exc) or 5.0
            wait_seconds = retry_after + _TELEGRAM_429_SAFETY_MARGIN
            # فقط اگر عقب‌تر از نوبت فعلی است جلو می‌بریم؛ به‌این‌ترتیب اگر چند
            # thread هم‌زمان 429 بگیرند، یکدیگر را عقب نمی‌کشند (بیشترین backoff
            # لازم اعمال می‌شود، نه هر backoff بعدی جایگزین قبلی).
            with _telegram_state_lock:
                _telegram_next_allowed_at = max(
                    _telegram_next_allowed_at, time.monotonic() + wait_seconds
                )
            logging.warning(
                "Telegram rate limit on %s; waiting %.1fs (retry %s/%s).",
                getattr(func, "__name__", "api_call"),
                wait_seconds, retries_429, max_429_retries
            )
            time.sleep(wait_seconds)
            # لاکی برای آزاد کردن نیست؛ پردازش سایر پیام‌ها همین الان هم جریان دارد.


def _start_daemon_timer(delay, callback, *args):
    timer = threading.Timer(delay, callback, args=args)
    timer.daemon = True
    timer.start()
    return timer


# ================== راست‌چین اجباری متن پیام‌ها ==================
_RLM = "\u200f"


def _force_rtl(text):
    if not text or not isinstance(text, str):
        return text
    return "\n".join(_RLM + line for line in text.split("\n"))


def _send_message_auto_reply(chat_id, text, *args, **kwargs):
    if isinstance(text, str) and "<tg-emoji" in text and kwargs.get("parse_mode") is None:
        kwargs["parse_mode"] = "HTML"
    text = _force_rtl(text)
    if kwargs.get("reply_to_message_id") is None:
        ctx_chat_id = getattr(_reply_ctx, "chat_id", None)
        ctx_message_id = getattr(_reply_ctx, "message_id", None)
        if ctx_message_id and ctx_chat_id is not None and str(ctx_chat_id) == str(chat_id):
            kwargs["reply_to_message_id"] = ctx_message_id
            kwargs.setdefault("allow_sending_without_reply", True)
    return _telegram_call_with_429_retry(
        _original_send_message, chat_id, text, *args, chat_id=chat_id, **kwargs
    )


def _edit_message_text_rtl(text, *args, **kwargs):
    # safe_edit_message performs duplicate suppression and its own edit retry;
    # this wrapper is retained for direct calls elsewhere in the file.
    text = _force_rtl(text)
    chat_id = kwargs.get("chat_id")
    if chat_id is None and len(args) >= 1:
        chat_id = args[0]
    return _telegram_call_with_429_retry(
        _original_edit_message_text, text, *args, chat_id=chat_id, **kwargs
    )


def _edit_message_reply_markup_safe(*args, **kwargs):
    chat_id = kwargs.get("chat_id")
    if chat_id is None and len(args) >= 1:
        chat_id = args[0]
    return _telegram_call_with_429_retry(
        _original_edit_message_reply_markup, *args, chat_id=chat_id, **kwargs
    )


def _delete_message_safe(*args, **kwargs):
    chat_id = _chat_id_from_call(args, kwargs)
    return _telegram_call_with_429_retry(
        _original_delete_message, *args, chat_id=chat_id, **kwargs
    )


def _answer_callback_query_safe(*args, **kwargs):
    # جواب دادن به دکمه جزو محدودیت پیام‌ها نیست؛ پشت صف ادیت/ارسال معطل نمی‌شه تا
    # دکمه‌ها سریع جواب بدن. فقط اگه تلگرام 429 داد، از مسیر صف‌دار تلاش مجدد می‌کنیم.
    try:
        return _original_answer_callback_query(*args, **kwargs)
    except Exception as exc:
        if not _is_rate_limit_error(exc):
            raise
        return _telegram_call_with_429_retry(
            _original_answer_callback_query, *args, chat_id=None, **kwargs
        )


bot.send_message = _send_message_auto_reply
bot.edit_message_text = _edit_message_text_rtl
bot.edit_message_reply_markup = _edit_message_reply_markup_safe
bot.delete_message = _delete_message_safe
bot.answer_callback_query = _answer_callback_query_safe

# ================== نرمال‌سازی متن دکمه‌ها/کلمات کلیدی ==================
# کیبورد فارسی/عربی گاهی هنگام تایپ یا اتوکامپلیت، کاراکترهای نامرئی مثل
# نیم‌فاصله (ZWNJ)، علائم جهت متن (RTL/LTR mark) یا فاصله‌ی اضافه اضافه
# می‌کنه. چون همه‌ی هندلرهای کلمه‌ی کلیدی (مثل "حفاری") با == دقیق مقایسه
# می‌شدن، همین یک کاراکتر نامرئی باعث می‌شد پیام اول match نشه و کاربر
# مجبور بشه دوباره (این بار بدون اون کاراکتر مخفی) بفرسته. این تابع همه‌ی
# این کاراکترهای نامرئی رو حذف و فاصله‌های تکراری رو یکی می‌کنه.
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]")

def normalize_text(text):
    if not text:
        return ""
    cleaned = _INVISIBLE_CHARS_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    # کیبوردهای عربی به‌جای «ی»/«ک» فارسی از «ي»/«ك» عربی استفاده می‌کنن
    cleaned = cleaned.replace("ي", "ی").replace("ك", "ک")
    return cleaned.strip()

def text_is(message, *options):
    t = getattr(message, "text", None)
    if not t:
        return False
    # صدها هندلر برای هر پیام text_is رو صدا می‌زنن؛ نرمال‌سازی فقط یک‌بار برای هر پیام انجام می‌شه.
    n = getattr(message, "_ntext", None)
    if n is None:
        n = normalize_text(t)
        try:
            message._ntext = n
        except Exception:
            pass
    return n in options

app = Flask(__name__)

# ================== جوین اجباری فقط در پیوی ==================
FORCE_JOIN_CHANNEL = "@Crypto_mohamad7"
def _force_join_url():
    return "https://t.me/" + str(FORCE_JOIN_CHANNEL).lstrip("@")


def is_force_join_exempt(user_id):
    return user_id in ADMIN_IDS


_force_join_ok = {}   # (channel, user_id) -> زمان انقضای کش (فقط نتیجه‌ی «عضو هست»)
_FORCE_JOIN_CACHE_SECONDS = 300

def is_user_joined_channel(user_id):
    """بررسی عضویت کاربر در کانال جوین اجباری (نتیجه‌ی مثبت ۵ دقیقه کش می‌شه تا هر پیام یه درخواست شبکه‌ای نباشه)."""
    ck = (str(FORCE_JOIN_CHANNEL), user_id)
    if _force_join_ok.get(ck, 0) > time.time():
        return True
    try:
        t0 = time.monotonic()
        member = bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        dt = time.monotonic() - t0
        if dt > 1.0:
            _perf_note("force-join", dt, user_id)
        ok = member.status in ("creator", "administrator", "member")
        if ok:
            if len(_force_join_ok) > 20000:
                _force_join_ok.clear()
            _force_join_ok[ck] = time.time() + _FORCE_JOIN_CACHE_SECONDS
        return ok
    except Exception as e:
        logging.error(f"خطا در بررسی عضویت کانال برای {user_id}: {e}")
        return False


def force_join_markup():
    markup = types.InlineKeyboardMarkup()
    markup.add(colored_button("📢 عضویت در کانال", url=_force_join_url()))
    markup.add(colored_button("✅ بررسی عضویت", callback_data="forcejoin|check"))
    return markup


def force_join_message(chat_id):
    return bot.send_message(
        chat_id,
        "⛔ برای استفاده از ربات ابتدا باید عضو کانال ما شوید.\n\n"
        "بعد از عضویت روی «✅ بررسی عضویت» بزنید.",
        reply_markup=force_join_markup(),
    )


def private_force_join_required(message):
    """فقط پیوی را قفل می‌کند؛ گروه‌ها و سوپرگروه‌ها تحت تأثیر نیستند."""
    if not message or getattr(getattr(message, "chat", None), "type", None) != "private":
        return False
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not user_id or is_force_join_exempt(user_id):
        return False
    return not is_user_joined_channel(user_id)


@bot.callback_query_handler(func=lambda call: call.data == "forcejoin|check")
def force_join_check_callback(call):
    user_id = call.from_user.id
    if is_force_join_exempt(user_id) or is_user_joined_channel(user_id):
        bot.answer_callback_query(call.id, "✅ عضویت شما تأیید شد.")
        try:
            safe_edit_message(
                "✅ عضویت شما تأیید شد.\nحالا می‌تونی از ربات استفاده کنی.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
    else:
        bot.answer_callback_query(
            call.id,
            "❌ هنوز عضو کانال نشدی. ابتدا عضو شو و دوباره بررسی کن.",
            show_alert=True,
        )


@bot.message_handler(func=private_force_join_required)
def force_join_message_guard(message):
    force_join_message(message.chat.id)


@bot.callback_query_handler(func=lambda call: (
    getattr(getattr(call, "message", None), "chat", None) is not None
    and getattr(call.message.chat, "type", None) == "private"
    and not is_force_join_exempt(call.from_user.id)
    and not is_user_joined_channel(call.from_user.id)
))
def force_join_callback_guard(call):
    bot.answer_callback_query(
        call.id,
        "⛔ ابتدا باید عضو کانال شوید.",
        show_alert=True,
    )

# ================== مهلت ورود عدد/کد ==================
# این مکانیزم به‌جای تکیه بر register_next_step_handler خودِ TeleBot (که فقط
# یک "منتظرِ پیام بعدی" در هر چت پشتیبانی می‌کند و با دو کاربر هم‌زمان در یک
# گروه تداخل پیدا می‌کند)، برای هر (چت، کاربر) به‌صورت جداگانه منتظر پیام
# بعدی می‌ماند. یعنی دو نفر می‌تونن هم‌زمان توی یه گروه، هرکدوم مرحله‌ی خودشون
# رو (مثلاً وارد کردن مبلغ) جلو ببرن بدون این‌که مزاحم همدیگه بشن.
# حداکثر ۶۰ ثانیه فعال است. بعد از آن، مرحله لغو و پیام اصلی ادیت می‌شود.
NEXT_STEP_TIMEOUT = 60
_next_step_pending = {}   # key: (chat_id, user_id) -> {"callback","args","kwargs","token","prompt_message_id","timer"}
_next_step_lock = threading.Lock()

# تأیید مبلغ بانک: تا قبل از زدن «بله» هیچ تغییری در موجودی انجام نمی‌شود.
_bank_confirmations = {}   # user_id -> {"action", "amount", "chat_id", "message_id"}
_bank_confirm_lock = threading.Lock()

# تأیید انتقال الماس: تا قبل از زدن «بله» هیچ انتقالی انجام نمی‌شود.
_transfer_confirmations = {}   # user_id -> {target_id, amount, chat_id, message_id}
_transfer_confirm_lock = threading.Lock()

def _transfer_confirmation_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("بله", callback_data=f"diamondtransfer|yes|{user_id}", icon_custom_emoji_id="5938109560249127910"),
        colored_button("خیر", callback_data=f"diamondtransfer|no|{user_id}", icon_custom_emoji_id="5819154526816444042"),
    )
    return markup

def _store_transfer_confirmation(user_id, target_id, amount, chat_id, message_id):
    with _transfer_confirm_lock:
        _transfer_confirmations[user_id] = {
            "target_id": int(target_id),
            "amount": int(amount),
            "chat_id": chat_id,
            "message_id": message_id,
        }

def _clear_transfer_confirmation(user_id):
    with _transfer_confirm_lock:
        return _transfer_confirmations.pop(user_id, None)

# ----- سقف و فاصله‌ی زمانی انتقال الماس -----
_transfer_last = {}   # user_id -> زمان آخرین انتقال (time.time)
_transfer_last_lock = threading.Lock()

def _transfer_max_text():
    return (f'حداکثر مبلغی که میتوان انتقال داد {TRANSFER_MAX_AMOUNT:,}{_gift_emoji(5940725397195853882, "💎")} '
            f'میباشد{_gift_emoji(5938490450833838343, "⚠️")}')

def _transfer_cooldown_text(remaining):
    e = _gift_emoji
    return (f'یکم به جیبت استراحت بده{e(5830338333892418460, "💰")}\n\n'
            f'{e(5938136227701067845, "⏳")}زمان باقیمانده: {int(remaining)} ثانیه{e(5825656411517886523, "⏰")}\n\n'
            f'میتونید از کارت به کارت در بانک هم استفاده کنید بدون محدودیت{e(5938109560249127910, "✅")}')

def _transfer_cooldown_remaining(user_id):
    if TRANSFER_COOLDOWN_SECONDS <= 0:
        return 0
    with _transfer_last_lock:
        last = _transfer_last.get(int(user_id))
    if not last:
        return 0
    return max(0, int(math.ceil(TRANSFER_COOLDOWN_SECONDS - (time.time() - last))))

def _reserve_transfer_slot(user_id):
    """قفل‌دار: اگه کاربر تو بازه‌ی انتظار نیست، همین الان انتقالش رو ثبت می‌کنه (جلوی دو تأیید هم‌زمان رو می‌گیره)."""
    user_id = int(user_id)
    now = time.time()
    with _transfer_last_lock:
        last = _transfer_last.get(user_id)
        if TRANSFER_COOLDOWN_SECONDS > 0 and last and now - last < TRANSFER_COOLDOWN_SECONDS:
            return False, int(math.ceil(TRANSFER_COOLDOWN_SECONDS - (now - last))), last
        _transfer_last[user_id] = now
        if len(_transfer_last) > 5000:
            for uid in [u for u, t in _transfer_last.items() if now - t > 3600]:
                _transfer_last.pop(uid, None)
        return True, 0, last

def _release_transfer_slot(user_id, previous):
    with _transfer_last_lock:
        if previous is None:
            _transfer_last.pop(int(user_id), None)
        else:
            _transfer_last[int(user_id)] = previous

def _show_transfer_confirmation(message, sender_id, target_id, amount):
    if amount > TRANSFER_MAX_AMOUNT:
        bot.reply_to(message, _transfer_max_text(), parse_mode="HTML")
        return None
    remaining = _transfer_cooldown_remaining(sender_id)
    if remaining > 0:
        bot.reply_to(message, _transfer_cooldown_text(remaining), parse_mode="HTML")
        return None
    confirmation = bot.reply_to(
        message,
        f'{_gift_emoji(5832240029446971049, "❓")} از انتقال الماس مطمئن هستید{_gift_emoji(5938506518306492774, "❔")}\n\n'
        f'{_gift_emoji(5823445637231814311, "💰")} مبلغ : {amount:,}{_gift_emoji(5940725397195853882, "💎")}',
        reply_markup=_transfer_confirmation_markup(sender_id),
        parse_mode="HTML",
    )
    _store_transfer_confirmation(
        sender_id, target_id, amount, confirmation.chat.id, confirmation.message_id
    )
    return confirmation

@bot.callback_query_handler(func=lambda call: call.data.startswith("diamondtransfer|"))
def diamond_transfer_confirmation_callback(call):
    parts = call.data.split("|")
    if len(parts) != 3:
        bot.answer_callback_query(call.id)
        return
    try:
        user_id = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id)
        return
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id)
        return

    with _transfer_confirm_lock:
        pending = _transfer_confirmations.get(user_id)
    if not pending:
        bot.answer_callback_query(call.id)
        return

    if parts[1] == "no":
        _clear_transfer_confirmation(user_id)
        bot.answer_callback_query(call.id)
        e = _gift_emoji
        safe_edit_message(
            f'انتقال الماس لغو شد.{e(5819154526816444042, "❌")}\n'
            f'{e(5830338333892418460, "💰")} هیچ مبلغی از موجودی شما کم نشد.{e(5832240029446971049, "✅")}',
            call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="HTML"
        )
        return

    if parts[1] != "yes":
        bot.answer_callback_query(call.id)
        return

    _clear_transfer_confirmation(user_id)
    target_id = int(pending["target_id"])
    amount = int(pending["amount"])
    ok, result_msg, sender_balance, target_balance, target_name = perform_transfer(user_id, target_id, amount)
    bot.answer_callback_query(call.id)
    safe_edit_message(
        result_msg, call.message.chat.id, call.message.message_id,
        reply_markup=None, parse_mode="HTML"
    )

def register_timed_next_step_handler(message, callback, *args, timeout=NEXT_STEP_TIMEOUT, expected_user_id=None, **kwargs):
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id is None:
        return

    # اگر expected_user_id صریحاً داده نشده، فقط وقتی از روی پیام حدس بزن که
    # آن پیام واقعاً از یک کاربر واقعی است (نه خودِ ربات). خیلی از فراخوانی‌ها
    # با پیامِ خودِ ربات (call.message یا خروجی bot.reply_to) صدا زده می‌شن که
    # اگر این‌جا از from_user آن استفاده می‌شد، آیدیِ ربات به‌جای کاربر واقعی
    # ذخیره می‌شد و پیام درستِ کاربر رد می‌شد (همون باگی که باعث می‌شد کاربر
    # مجبور بشه ۲-۳ بار مبلغ رو بفرسته).
    if expected_user_id is None:
        user = getattr(message, "from_user", None)
        if user is not None and not getattr(user, "is_bot", False):
            expected_user_id = getattr(user, "id", None)

    if expected_user_id is None:
        # هیچ کاربر مشخصی نداریم (مثلاً یه فلوی ادمین که «هر ادمینی» می‌تونه
        # جوابش رو بده). این حالت را دیگه از طریق این مکانیزم پشتیبانی نمی‌کنیم
        # چون بدون آیدی کاربر نمی‌شه کلید (چت، کاربر) ساخت؛ صدازننده باید
        # expected_user_id را صریح بده.
        logging.error("register_timed_next_step_handler: expected_user_id نامشخص است.")
        return

    key = (chat_id, expected_user_id)
    prompt_message_id = None
    try:
        if getattr(getattr(message, "from_user", None), "is_bot", False):
            prompt_message_id = message.message_id
    except Exception:
        pass

    def expire():
        with _next_step_lock:
            state = _next_step_pending.get(key)
            if not state or state.get("token") is not token:
                return
            _next_step_pending.pop(key, None)
        try:
            prompt_id = state.get("prompt_message_id")
            if prompt_id:
                safe_edit_message(
                    f'{_gift_emoji(5825656411517886523, "⏰")} زمان وارد کردن اطلاعات تمام شد{_gift_emoji(5818716826699307883, "⌛")}',
                    chat_id, prompt_id, reply_markup=None, parse_mode="HTML"
                )
        except Exception:
            pass

    timer = threading.Timer(timeout, expire)
    timer.daemon = True

    with _next_step_lock:
        token = object()
        previous = _next_step_pending.get(key)
        if previous:
            old_timer = previous.get("timer")
            if old_timer:
                try:
                    old_timer.cancel()
                except Exception:
                    pass
        _next_step_pending[key] = {
            "callback": callback,
            "args": args,
            "kwargs": kwargs,
            "token": token,
            "prompt_message_id": prompt_message_id or (previous or {}).get("prompt_message_id"),
            "timer": timer,
        }

    timer.start()

def cancel_pending_step(chat_id, user_id):
    """مرحله‌ی متنی در انتظار کاربر را فوراً لغو می‌کند.

    مهم: bot.clear_step_handler() فقط مکانیزم داخلی TeleBot را پاک می‌کند؛
    فلوی سفارشی _next_step_pending جدا از آن است و اگر پاک نشود، پیام‌های بعدی
    کاربر دوباره به مرحله‌ی قبلی (مثلاً مبلغ برداشت) تحویل داده می‌شوند و
    به‌نظر می‌رسد بات پیام‌های عادی را نادیده می‌گیرد.
    """
    if chat_id is None or user_id is None:
        return False
    key = (chat_id, user_id)
    with _next_step_lock:
        state = _next_step_pending.pop(key, None)
    if state:
        timer = state.get("timer")
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
        return True
    return False


def _consume_pending_step(message):
    """اگه برای این (چت، کاربر) مرحله‌ای در انتظار باشه، اجراش می‌کنه.
    خروجی True یعنی مصرف شد (دیگه هندلرهای دیگه نباید روی این پیام کاری کنن)."""
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if chat_id is None or user_id is None:
        return False
    key = (chat_id, user_id)
    with _next_step_lock:
        state = _next_step_pending.get(key)
    if not state:
        return False

    # تمام ورودی‌هایی که از یک پنل ربات شروع شده‌اند فقط با Reply مستقیم
    # به همان پیام پنل معتبر هستند. این کنترل مرکزی است تا هیچ فلویی
    # با پیام عادی کاربر اشتباهاً مصرف نشود.
    prompt_id = state.get("prompt_message_id")
    if prompt_id:
        reply = getattr(message, "reply_to_message", None)
        reply_user = getattr(reply, "from_user", None) if reply else None
        if (not reply or reply.message_id != int(prompt_id) or
                not reply_user or not getattr(reply_user, "is_bot", False)):
            try:
                safe_edit_message(
                    "↩️ برای ادامه، روی همین پیام پنل ریپلای کن و مقدار را بفرست.",
                    chat_id, int(prompt_id), reply_markup=None
                )
            except Exception:
                pass
            # مرحله مصرف نشود؛ تایمر و state باقی می‌مانند (چون هنوز pop نشده).
            return True

    # از این‌جا به بعد پیام معتبره؛ فقط الان state رو مصرف (pop) می‌کنیم.
    with _next_step_lock:
        state = _next_step_pending.pop(key, None)
    if not state:
        return False

    timer = state.get("timer")
    if timer:
        try:
            timer.cancel()
        except Exception:
            pass
    state["callback"](message, *state["args"], **state["kwargs"])
    return True


# ================== اتصال به Supabase ==================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== قفل برای کازینو ==================
casino_lock = threading.RLock()
bet_lock = threading.RLock()
mine_lock = threading.RLock()

# قفل‌های per-user برای جلوگیری از lost-update 
# این قفل‌ها مخصوصاً برای Flask threaded=True مهم‌اند.
_user_financial_meta_lock = threading.Lock()
_user_financial_locks = {}

def _get_user_financial_lock(user_id):
    user_id = int(user_id)
    with _user_financial_meta_lock:
        lock = _user_financial_locks.get(user_id)
        if lock is None:
            lock = threading.RLock()
            _user_financial_locks[user_id] = lock
        return lock

_mine_word_meta_lock = threading.Lock()
_mine_word_locks = {}

def _get_mine_word_lock(user_id):
    user_id = int(user_id)
    with _mine_word_meta_lock:
        lock = _mine_word_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _mine_word_locks[user_id] = lock
        return lock

active_casino_games = {}
active_mine_games = {}
MINE_GRID_SIZE = 9       # 3x3
MINE_MULTIPLIER_STEP = 0.25
casino_timers = {}

def _run_db_with_retry(func, *, retries=2, delay=0.3, label="db"):
    """اجرای یک عملیات دیتابیسی با چند بار تلاش مجدد در صورت خطای موقتی شبکه/Supabase.
    بدون این، یک قطعی کوتاه شبکه دقیقاً معادل «شکست قطعی عملیات مالی» می‌شد (مثل باگ
    گزارش‌شده‌ی «مبلغ کسر نشد» در انتقال الماس)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            _t0 = time.monotonic()
            _res = func()
            _dt = time.monotonic() - _t0
            if _dt > 1.0:
                _perf_note("db", _dt, label)
            return _res
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(delay)
                continue
    logging.error(f"خطا در {label} بعد از {retries + 1} تلاش: {last_err}")
    raise last_err

# ================== توابع دیتابیس (Supabase) ==================
def get_user(user_id):
    try:
        response = _run_db_with_retry(
            lambda: supabase.table("users").select("*").eq("user_id", user_id).execute(),
            label=f"get_user({user_id})",
        )
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در get_user: {e}")
        return None

def create_user(user_id, username):
    try:
        supabase.table("users").insert({
            "user_id": user_id,
            "username": username,
            "diamonds": START_DIAMONDS,
            "last_spin": 0,
            "bank_balance": 0,
            "bank_account_number": None,
            "bank_interest_date": None,
            "ring_diamonds": 0
        }).execute()
        return True
    except Exception as e:
        logging.error(f"خطا در create_user: {e}")
        try:
            return bool(get_user(user_id))
        except Exception:
            return False

def update_diamonds(user_id, amount):
    try:
        # عملیات read-modify-write
        # برای هر کاربر سریالی می‌شود تا lost update رخ ندهد.
        with _get_user_financial_lock(user_id):
            user = get_user(user_id)
            if user:
                new_balance = int(user.get('diamonds', 0) or 0) + int(amount)
                _run_db_with_retry(
                    lambda: supabase.table("users").update({"diamonds": new_balance}).eq("user_id", user_id).execute(),
                    label=f"update_diamonds({user_id})",
                )
                return new_balance
    except Exception as e:
        logging.error(f"خطا در update_diamonds: {e}")
        return None

# ================== سیستم الماس تصادفی گروه ==================
# قبلاً اینجا یک Lock سراسری (global) بود که یعنی پیام یک گروه، پردازش
# پیام‌های *همه‌ی گروه‌های دیگر* را هم متوقف می‌کرد. حالا هر چت قفل
# مخصوص خودش را دارد تا گروه‌ها مزاحم هم نشوند.
_diamond_hunt_meta_lock = threading.Lock()
_diamond_hunt_locks = {}
# کش حافظه‌ای وضعیت هر گروه، تا برای هر پیام معمولی گروه مجبور نباشیم
# با Supabase (که یک درخواست شبکه‌ای است) رفت‌وبرگشت بزنیم.
_diamond_hunt_cache = {}
# زمان شروع آخرین الماس فعال هر گروه (در حافظه)؛ برای تشخیص الماس‌های گیرکرده.
_diamond_hunt_started = {}
DIAMOND_HUNT_STALE_SECONDS = 150

def _get_diamond_hunt_lock(chat_id):
    with _diamond_hunt_meta_lock:
        lock = _diamond_hunt_locks.get(chat_id)
        if lock is None:
            lock = threading.Lock()
            _diamond_hunt_locks[chat_id] = lock
        return lock

def _get_diamond_hunt(chat_id):
    try:
        response = supabase.table("diamond_hunts").select("*").eq("chat_id", chat_id).execute()
        row = response.data[0] if response.data else None
        if row is not None:
            _diamond_hunt_cache[chat_id] = row
        return row
    except Exception as e:
        logging.error(f"خطا در _get_diamond_hunt: {e}")
        return None

def _ensure_diamond_hunt_row(chat_id):
    # اگر در کش داریم، همان را برگردان (بدون تماس شبکه‌ای) — این تنها
    # چیزی است که در مسیر «هر پیام گروه» صدا زده می‌شود.
    cached = _diamond_hunt_cache.get(chat_id)
    if cached is not None:
        return cached
    row = _get_diamond_hunt(chat_id)
    if row:
        return row
    try:
        response = supabase.table("diamond_hunts").insert({
            "chat_id": chat_id,
            "message_count": 0,
            "active": False,
            "hunt_message_id": None,
            "attempts": []
        }).execute()
        row = response.data[0] if response.data else None
        if row is not None:
            _diamond_hunt_cache[chat_id] = row
        return row
    except Exception as e:
        logging.error(f"خطا در ساخت وضعیت الماس گروه {chat_id}: {e}")
        return None

def _set_diamond_hunt(chat_id, **updates):
    try:
        supabase.table("diamond_hunts").update(updates).eq("chat_id", chat_id).execute()
    except Exception as e:
        logging.error(f"خطا در به‌روزرسانی وضعیت الماس گروه {chat_id}: {e}")
    # کش را هم همگام نگه می‌داریم تا خوانده‌های بعدی از کش، قدیمی نباشند.
    cached = _diamond_hunt_cache.get(chat_id)
    if cached is not None:
        cached.update(updates)

def _add_ring_diamond(user_id):
    """یک الماس انگشتر به موجودی قابل تبدیل کاربر اضافه می‌کند."""
    try:
        user = get_user(user_id)
        if not user:
            return False
        total_ring = int(user.get("ring_diamonds", 0) or 0) + 1
        supabase.table("users").update({"ring_diamonds": total_ring}).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logging.error(f"خطا در ثبت الماس انگشتر برای {user_id}: {e}")
        return False

_HUNT_ORD = ["اول", "دوم", "سوم"]

def _format_attempt_history(attempts):
    lines = []
    x = _gift_emoji(5839270298205035832, "❌")
    for idx, attempt in enumerate(attempts, 1):
        reason = str(attempt.get("reason", "")).replace("❌", "").strip()
        word = _HUNT_ORD[idx - 1] if idx <= 3 else str(idx)
        lines.append(f"در تلاش {word} {attempt['name']} {reason}{x}")
    return "\n".join(lines)

def _hunt_cost_line(attempt_no, cost):
    e = _gift_emoji
    word = _HUNT_ORD[attempt_no - 1] if 1 <= attempt_no <= 3 else str(attempt_no)
    if attempt_no == 1:
        label = "هزینه تلاش برای برداشتن الماس"
    else:
        label = f"هزینه تلاش {word} برای برداشتن الماس"
    return f'{e(5823445637231814311, "💰")}{label}: {cost:,} الماس{e(5940725397195853882, "💎")}'

def _hunt_start_caption():
    e = _gift_emoji
    return (
        f'{e(5886451690772636500, "💎")} یک الماس انگشتر در شهر پیدا شد{e(5818716826699307883, "❗")}\n\n'
        f'این الماس قابلیت تبدیل به انگشتر داره{e(5820903824046432799, "💍")}\n\n'
        f'{e(5938167138580741203, "✅")}تا از دست نرفته تلاش خودت رو برای بدست آوردنش بکن{e(5938264290740933445, "✅")}\n\n'
        f'از دکمه زیر برای برداشتن الماس استفاده کنید{e(5820903824046432799, "❗")}\n\n'
        + _hunt_cost_line(1, DIAMOND_HUNT_COSTS[0])
    )

def _hunt_fail_text(attempts, next_cost=None):
    e = _gift_emoji
    text = (f'{e(5819154526816444042, "❌")} نتونستی الماس رو بگیری علت{e(5820903824046432799, "👇")}\n\n'
            + _format_attempt_history(attempts))
    if next_cost is not None:
        text += "\n\n" + _hunt_cost_line(len(attempts) + 1, next_cost)
    return text

def _hunt_win_text(total_attempts, name):
    e = _gift_emoji
    return (f'الماس بعد از {total_attempts} تلاش با موفقیت نجات یافت و صاحب جدید پیدا کرد{e(5818866927216369505, "💎")}\n\n'
            f'{name} با موفقیت الماس رو بدست آورد.{e(5940725397195853882, "💎")}\n\n'
            f'{e(5819032824623144971, "🎁")} پاداش {e(5820903824046432799, "👇")}\n'
            f'\u200f┘─ یک الماس با قابلیت تبدیل به انگشتر{e(5852787947559985777, "💍")}')

def _diamond_hunt_markup(attempt_no, message_id):
    markup = types.InlineKeyboardMarkup()
    if attempt_no <= 3:
        markup.add(colored_button(
            "💎 برداشتن الماس",
            callback_data=f"dhunt|{message_id}|{attempt_no}"
        ))
    return markup

def _expire_diamond_hunt(chat_id, message_id):
    with _get_diamond_hunt_lock(chat_id):
        row = _get_diamond_hunt(chat_id)
        if not row:
            return
        if not row.get("active"):
            return
        if int(row.get("hunt_message_id") or 0) != int(message_id):
            return

        _set_diamond_hunt(
            chat_id,
            active=False,
            hunt_message_id=None,
            attempts=[]
        )

    try:
        bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption="⏰ زمان برداشتن الماس به پایان رسید و الماس از دست رفت! ❌💎",
            reply_markup=None
        )
    except Exception:
        try:
            safe_edit_message(
                "⏰ زمان برداشتن الماس به پایان رسید و الماس از دست رفت! ❌💎",
                chat_id,
                message_id,
                reply_markup=None
            )
        except Exception as e:
            logging.error(f"خطا در منقضی کردن الماس گروه {chat_id}: {e}")

def _start_diamond_hunt(chat_id):
    try:
        photo_url = "https://i.ibb.co/TDMk6NZz/file-00000000aa6481f7a219e1184b4dd639.jpg"
        
        hunt_caption = _hunt_start_caption()
        try:
            msg = bot.send_photo(chat_id, photo=photo_url, caption=hunt_caption, parse_mode="HTML")
        except Exception as photo_error:
            # اگر ارسال عکس ممکن نبود (لینک عکس باز نشد یا گروه ارسال رسانه را بسته)،
            # الماس به‌صورت متن ساده ارسال می‌شود تا گروه بی‌الماس نماند.
            logging.warning(f"ارسال عکس الماس در گروه {chat_id} ناموفق بود، متن ارسال می‌شود: {photo_error}")
            msg = bot.send_message(chat_id, hunt_caption)

        _diamond_hunt_started[chat_id] = time.time()
        _set_diamond_hunt(
            chat_id,
            active=True,
            hunt_message_id=msg.message_id,
            attempts=[]
        )

        safe_edit_message_reply_markup(
            chat_id=chat_id,
            message_id=msg.message_id,
            reply_markup=_diamond_hunt_markup(1, msg.message_id)
        )

        timer = threading.Timer(120, _expire_diamond_hunt, args=(chat_id, msg.message_id))
        timer.daemon = True
        timer.start()

    except Exception as e:
        logging.error(f"خطا در ارسال الماس تصادفی گروه {chat_id}: {e}")

def _reset_stale_diamond_hunts_on_boot():
    """تایمر انقضای الماس فقط در حافظه است و با ری‌استارت بات از بین می‌رود؛ اگر
    موقع ری‌استارت الماسی فعال بوده، آن گروه برای همیشه active می‌ماند و دیگر
    الماسی نمی‌گیرد. پس هنگام بالا آمدن بات، همه‌ی الماس‌های فعال بسته می‌شوند."""
    try:
        supabase.table("diamond_hunts").update(
            {"active": False, "hunt_message_id": None, "attempts": []}
        ).eq("active", True).execute()
    except Exception as e:
        logging.error(f"خطا در پاک‌کردن الماس‌های گیرکرده هنگام راه‌اندازی: {e}")


_reset_stale_diamond_hunts_on_boot()


def process_group_message_for_diamond_hunt(message):
    if not message or message.chat.type not in ("group", "supergroup"):
        return

    if getattr(message.from_user, "is_bot", False):
        return

    text = (message.text or "").strip()

    if text.replace("ي", "ی") == "ماین":
        try:
            if get_user(message.from_user.id):
                register_mine_word(message.from_user.id, message)
        except Exception as e:
            logging.error(f"خطا در ثبت پاداش کلمه ماین: {e}")

    chat_id = message.chat.id
    with _get_diamond_hunt_lock(chat_id):
        row = _ensure_diamond_hunt_row(chat_id)
        if not row:
            return

        # تا وقتی یک الماس فعال است، پیام‌ها برای چرخه بعدی حساب نمی‌شوند.
        # شمارش بعد از پایان/انقضای همین الماس دوباره ادامه پیدا می‌کند.
        if row.get("active"):
            started = _diamond_hunt_started.get(chat_id)
            if started is None:
                # الماس فعالی که این پروسه شروعش نکرده؛ از همین لحظه زمانش را می‌سنجیم.
                _diamond_hunt_started[chat_id] = time.time()
                return
            if time.time() - started < DIAMOND_HUNT_STALE_SECONDS:
                return
            # الماس بیش از زمان مجاز فعال مانده (تایمر انقضا گم شده): آزادش کن.
            _set_diamond_hunt(chat_id, active=False, hunt_message_id=None, attempts=[])
            _diamond_hunt_started.pop(chat_id, None)

        count = int(row.get("message_count", 0) or 0) + 1
        row["message_count"] = count

        # شمارنده را در Supabase هم ذخیره می‌کنیم تا با ری‌استارت Render
        # از بین نرود و مقدار واقعی جدول همیشه قابل مشاهده باشد.
        if count < DIAMOND_HUNT_MESSAGE_INTERVAL:
            # قبلاً برای «هر» پیام گروه یه نوشتن توی Supabase انجام می‌شد (کندترین بخش بات).
            # حالا شمارنده تو حافظه می‌مونه و فقط هر ۲۵ پیام یه‌بار ذخیره می‌شه.
            if count % 25 == 0:
                _set_diamond_hunt(chat_id, message_count=count)
            return

        # پیام شماره 200: شمارنده همان لحظه صفر می‌شود و الماس ارسال می‌گردد.
        _set_diamond_hunt(chat_id, message_count=0)
        _start_diamond_hunt(chat_id)

def _user_display_from_call(call):
    # فقط اسم (First Name) رو نشون بده
    return call.from_user.first_name or "کاربر"

@bot.callback_query_handler(func=lambda call: call.data.startswith("dhunt|"))
def diamond_hunt_callback(call):
    parts = call.data.split("|")
    if len(parts) != 3:
        bot.answer_callback_query(call.id)
        return

    try:
        message_id = int(parts[1])
        attempt_no = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id)
        return

    chat_id = call.message.chat.id

    with _get_diamond_hunt_lock(chat_id):
        row = _get_diamond_hunt(chat_id)

        if (
            not row
            or not row.get("active")
            or int(row.get("hunt_message_id") or 0) != message_id
        ):
            bot.answer_callback_query(call.id)
            return

        attempts = row.get("attempts") or []
        expected_attempt = len(attempts) + 1

        if attempt_no != expected_attempt or attempt_no > 3:
            bot.answer_callback_query(call.id)
            return

        user_id = call.from_user.id

        if not get_user(user_id):
            bot.answer_callback_query(
                call.id,
                "اول /start را در پیوی بات بزن.",
                show_alert=True
            )
            return

        cost = DIAMOND_HUNT_COSTS[attempt_no - 1]

        # قفل hunt گروه + قفل مالی کاربر:
        # کاربر نمی‌تواند هم‌زمان در دو گروه، موجودی یکسان را دوبار خرج کند.
        with _get_user_financial_lock(user_id):
            if get_balance(user_id) < cost:
                bot.answer_callback_query(
                    call.id,
                    f"برای این تلاش {cost:,} 💎 لازم داری.",
                    show_alert=True
                )
                return

            if update_diamonds(user_id, -cost) is None:
                bot.answer_callback_query(
                    call.id,
                    "❌ کسر هزینه انجام نشد؛ دوباره تلاش کن.",
                    show_alert=True
                )
                return

            name = display_name_with_tag(user_id, html.escape(_user_display_from_call(call)))
            won = random.random() < DIAMOND_HUNT_WIN_CHANCES[attempt_no - 1]

        if won:
            _add_ring_diamond(user_id)
            register_ring_diamond_win(user_id, chat_id, name)
            total_attempts = attempt_no

            _set_diamond_hunt(
                chat_id,
                active=False,
                hunt_message_id=None,
                attempts=[]
            )

            try:
                bot.answer_callback_query(
                    call.id,
                    "💍 الماس با موفقیت نجات پیدا کرد!",
                    show_alert=False
                )
                
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=_hunt_win_text(total_attempts, name),
                    reply_markup=None,
                    parse_mode="HTML"
                )
                
            except Exception as e:
                logging.error(f"خطا در نتیجه برد الماس: {e}")
                try:
                    safe_edit_message(
                        _hunt_win_text(total_attempts, name),
                        chat_id,
                        message_id,
                        reply_markup=None,
                        parse_mode="HTML"
                    )
                except Exception as e2:
                    logging.error(f"خطا در نتیجه برد الماس (روش جایگزین): {e2}")
            return

        reason = random.choice(DIAMOND_HUNT_REASONS)
        attempts.append({"name": name, "reason": reason})
        _set_diamond_hunt(chat_id, attempts=attempts)

        bot.answer_callback_query(
            call.id,
            f"تلاش {attempt_no} ناموفق بود ❌"
        )

        if attempt_no < 3:
            next_cost = DIAMOND_HUNT_COSTS[attempt_no]
            text = _hunt_fail_text(attempts, next_cost)
            
            try:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    reply_markup=_diamond_hunt_markup(attempt_no + 1, message_id),
                    parse_mode="HTML"
                )
            except Exception:
                safe_edit_message(
                    text,
                    chat_id,
                    message_id,
                    reply_markup=_diamond_hunt_markup(attempt_no + 1, message_id),
                    parse_mode="HTML"
                )
        else:
            text = _hunt_fail_text(attempts)
            
            try:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    reply_markup=None,
                    parse_mode="HTML"
                )
            except Exception:
                safe_edit_message(
                    text,
                    chat_id,
                    message_id,
                    reply_markup=None,
                    parse_mode="HTML"
                )
            
            _set_diamond_hunt(
                chat_id,
                active=False,
                hunt_message_id=None,
                attempts=[]
            )

def get_bank_balance(user_id):
    user = get_user(user_id)
    return int(user.get("bank_balance", 0) or 0) if user else 0

def get_bank_account_number(user_id):
    user = get_user(user_id)
    return user.get("bank_account_number") if user else None

def _today_tehran():
    return datetime.now(TEHRAN_TZ).date()

def generate_bank_account_number():
    while True:
        number = "6037" + "".join(str(random.randint(0, 9)) for _ in range(12))
        try:
            exists = supabase.table("users").select("user_id").eq("bank_account_number", number).execute()
            if not exists.data:
                return number
        except Exception as e:
            logging.error(f"خطا در تولید شماره حساب: {e}")
            return number

def apply_bank_interest(user_id):
    """سود روزهای گذشته را بعد از ساعت ۰۰:۰۰ محاسبه می‌کند؛ سود هر روز حداکثر ۱ میلیون است."""
    user = get_user(user_id)
    if not user or not user.get("bank_account_number"):
        return 0

    balance = int(user.get("bank_balance", 0) or 0)
    if balance <= 0:
        today = _today_tehran().isoformat()
        supabase.table("users").update({"bank_interest_date": today}).eq("user_id", user_id).execute()
        return 0

    today = _today_tehran()
    last_raw = user.get("bank_interest_date")
    if not last_raw:
        supabase.table("users").update({"bank_interest_date": today.isoformat()}).eq("user_id", user_id).execute()
        return 0

    try:
        last_date = datetime.fromisoformat(str(last_raw)[:10]).date()
    except Exception:
        last_date = today

    if last_date >= today:
        return 0

    total_interest = 0
    days = (today - last_date).days
    for _ in range(days):
        daily_interest = min(int(balance * BANK_INTEREST_RATE), BANK_DAILY_INTEREST_MAX)
        balance += daily_interest
        total_interest += daily_interest

    supabase.table("users").update({
        "bank_balance": balance,
        "bank_interest_date": today.isoformat()
    }).eq("user_id", user_id).execute()
    return total_interest

def open_bank_account(user_id):
    user = get_user(user_id)
    if not user:
        return False, "اول /start بزن."
    if user.get("bank_account_number"):
        return True, "حساب بانکی شما از قبل فعال است."
    if get_balance(user_id) < BANK_OPENING_FEE:
        return False, f"برای افتتاح حساب باید {BANK_OPENING_FEE:,} 💎 داشته باشی."

    account_number = generate_bank_account_number()
    today = _today_tehran().isoformat()
    try:
        if update_diamonds(user_id, -BANK_OPENING_FEE) is None:
            return False, "موجودی کافی نیست."
        supabase.table("users").update({
            "bank_balance": 0,
            "bank_account_number": account_number,
            "bank_interest_date": today
        }).eq("user_id", user_id).execute()
        return True, account_number
    except Exception as e:
        logging.error(f"خطا در open_bank_account: {e}")
        return False, "افتتاح حساب انجام نشد."

def change_bank_balance(user_id, delta):
    try:
        with _get_user_financial_lock(user_id):
            user = get_user(user_id)
            if not user or not user.get("bank_account_number"):
                return False
            apply_bank_interest(user_id)
            # apply_bank_interest می‌تواند user را تغییر دهد؛ دوباره بخوان.
            user = get_user(user_id)
            current = int((user or {}).get("bank_balance", 0) or 0)
            new_balance = current + int(delta)
            if new_balance < 0:
                return False
            supabase.table("users").update({"bank_balance": new_balance}).eq("user_id", user_id).execute()
            return True
    except Exception as e:
        logging.error(f"خطا در change_bank_balance: {e}")
        return False

def get_balance(user_id):
    user = get_user(user_id)
    return user.get('diamonds', 0) if user else 0

_bet_locks_meta_lock = threading.Lock()
_bet_locks = {}

def _get_bet_lock(bet_id):
    """قفل مخصوص هر شرط تا پیوستن/لغو/تایم‌اوت هم‌زمان روی یک شرط با هم تداخل نکنن
    (همون الگویی که برای بازی دینامیت استفاده شده)."""
    bet_id = int(bet_id)
    with _bet_locks_meta_lock:
        lock = _bet_locks.get(bet_id)
        if lock is None:
            lock = threading.RLock()
            _bet_locks[bet_id] = lock
        return lock

def create_bet(creator_id, creator_name, amount, chat_id, message_id):
    try:
        response = supabase.table("bets").insert({
            "creator_id": creator_id,
            "creator_name": creator_name,
            "amount": amount,
            "chat_id": chat_id,
            "message_id": message_id,
            "status": "pending"
        }).execute()
        return response.data[0]['bet_id'] if response.data else None
    except Exception as e:
        logging.error(f"خطا در create_bet: {e}")
        return None

def get_bet(bet_id):
    try:
        response = supabase.table("bets").select("*").eq("bet_id", bet_id).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در get_bet: {e}")
        return None

def set_bet_status(bet_id, status):
    try:
        supabase.table("bets").update({"status": status}).eq("bet_id", bet_id).execute()
    except Exception as e:
        logging.error(f"خطا در set_bet_status: {e}")

def get_top_users(limit=10):
    try:
        response = supabase.table("users").select("user_id, username, diamonds").order("diamonds", desc=True).limit(limit).execute()
        return [(u['user_id'], u['username'], u['diamonds']) for u in response.data] if response.data else []
    except Exception as e:
        logging.error(f"خطا در get_top_users: {e}")
        return []

def safe_html_name(user):
    """اسم کاربر برای پیام‌های HTML: ایموجی گیفت (اگر دارد) + نام escape‌شده."""
    name = html.escape((getattr(user, "first_name", None) or "کاربر").strip())
    uid = getattr(user, "id", None)
    if uid:
        try:
            g = display_gift_for_user(uid)
            if g:
                return f"{g} {name}"
        except Exception:
            pass
    return name


def get_display_name(user):
    name=(getattr(user,"first_name",None) or "کاربر").strip();uid=getattr(user,"id",None)
    if uid:
        try:
            g=display_gift_for_user(uid)
            if g:return f"{g} {name}"
        except Exception:pass
    return name

# ================== سیستم گیفت‌های پرمیوم ==================
GIFT_BOX_PRICE=100_000_000
GIFT_MAX_PRICE_UP=5_000_000
GIFT_MAX_PRICE_DOWN=5_000_000
GIFT_MARKET_INTERVAL_SECONDS=6*60*60
GIFT_MIN_VALUE=10_000_000
GIFT_MAX_VALUE=40_000_000
GIFT_TOTAL_DISPLAY=1

# ---------- کاتالوگ گیفت‌ها ----------
# هر «نوع» چند «مدل» داره و هر مدل یه ایموجی پرمیوم (gift_code) جداست.
# تگ (gift_id) برای هر مدل جدا از ۱ شروع می‌شه و به ترتیب بالا می‌ره.
# قیمت (min/max) و شانس (weight) هر نوع از اینجا تنظیم می‌شه.
GIFT_CATALOG = {
    "plush_pepe": {
        "name": "Plush Pepe", "min": 150_000_000, "max": 200_000_000, "weight": 0.15,
        "models": {
            5454286330487922571: "Sketchy",
            5456504676801338603: "Midas Pepe",
            5453887869192003779: "Frozen",
        },
    },
    "diamond_ring": {
        "name": "Diamond Ring", "min": 50_000_000, "max": 100_000_000, "weight": 0.5,
        "models": {
            5852783592463147155: "Vice City",
            5850247611843354746: "Twilight",
            5852457862143418180: "Sketch",
        },
    },
    "toy_bear": {
        "name": "Toy Bear", "min": 20_000_000, "max": 40_000_000, "weight": 0.35,
        "models": {
            5845768128457350256: "Snowman",
            5846006477667443242: "Blueprint",
            5846007366725673296: "Golden Cub",
            5845836989668008156: "Angel",
            5845901246673723045: "The Devil",
        },
    },
    "timeless_book": {
        "name": "Timeless Book", "min": 15_000_000, "max": 30_000_000, "weight": 1.0,
        "models": {
            5850333296440911189: "Death Note",
            5852690937133670577: "Library Scroll",
            5852758806206882465: "Secret Chapter",
            5850186069256969840: "Rocket Science",
            5850534180651277908: "Journal 1",
        },
    },
    "snoop_dog": {
        "name": "Snoop Dog", "min": 25_000_000, "max": 40_000_000, "weight": 1.0,
        "models": {
            5846078001757823356: "Goldizzle",
            5845876615036281264: "King Snoop",
            5846103900410618639: "Silver",
            5845827089768390239: "Chrome",
            5845684947825728788: "Woofee",
            5845757120456172182: "Black Gold",
        },
    },
    "heroic_helmet": {
        "name": "Heroic Helmet", "min": 50_000_000, "max": 100_000_000, "weight": 0.5,
        "models": {
            5879964524924114989: "Mercurial",
            5879840138376255988: "King Midas",
            5879465789026736965: "Unicorn",
        },
    },
}
GIFT_MODEL_INDEX = {
    code: (type_key, model_name)
    for type_key, cfg in GIFT_CATALOG.items()
    for code, model_name in cfg["models"].items()
}

def _gift_type(gift_code):
    try:
        return GIFT_MODEL_INDEX[int(gift_code)][0]
    except Exception:
        return None

def _gift_type_name(gift_code):
    typ = _gift_type(gift_code)
    return GIFT_CATALOG[typ]["name"] if typ else "-"

def _gift_model_name(gift_code):
    try:
        return GIFT_MODEL_INDEX[int(gift_code)][1]
    except Exception:
        return "-"

def _gift_price_range(gift_code):
    typ=_gift_type(gift_code)
    if typ: return GIFT_CATALOG[typ]["min"], GIFT_CATALOG[typ]["max"]
    return 10_000_000,35_000_000

def _random_gift_price(gift_code):
    lo, hi = _gift_price_range(gift_code)
    # قیمت کاملاً تصادفی است، ولی برای جلوگیری از قیمت‌های خیلی رُند،
    # ضرایب میلیون/ده‌هزار به‌صورت تصادفی شکسته می‌شوند.
    for _ in range(20):
        price = random.randint(lo, hi)
        if price % 1_000_000 != 0:
            return price
    return random.randint(lo, hi)

def _gift_emoji(emoji_id,fallback="🎁"):
    return f'<tg-emoji emoji-id="{int(emoji_id)}">{fallback}</tg-emoji>'

def _gift_value_from_id(gift_id,gift_code):
    return _random_gift_price(gift_code)

def _gift_ready():
    try:supabase.table("premium_gifts").select("instance_id").limit(1).execute();return True
    except Exception as e:logging.error(f"جدول premium_gifts در دسترس نیست: {e}");return False

def gift_box_text(uid):
    e = _gift_emoji
    return (
        f'{e(5927029738626359516, "🎁")} گیفت شانسی\n\n'
        f'{e(5823445637231814311, "💰")} قیمت گیفت باکس شانسی : {GIFT_BOX_PRICE:,}{e(5940725397195853882, "💎")}\n\n'
        f'{e(5825623013852192219, "💎")} موجودی شما : {get_balance(uid):,}{e(5940725397195853882, "💎")}\n\n'
        f'{e(5816913107938712568, "ℹ️")} جهت خرید گیفت باکس از دکمه خرید گیفت زیر استفاده کن{e(5820903824046432799, "👇")}'
    )

def count_owned_by_code(uid,gift_code):
    try:
        return len(supabase.table("premium_gifts").select("instance_id").eq("owner_id",int(uid)).eq("gift_code",int(gift_code)).execute().data or [])
    except Exception: return 0

def _gift_model_total(gift_code):
    """تعداد کل گیفت‌های ساخته‌شده‌ی این مدل (= بالاترین تگ)."""
    try:
        r = (supabase.table("premium_gifts").select("gift_id")
             .eq("gift_code", int(gift_code)).order("gift_id", desc=True).limit(1).execute())
        return int(r.data[0]["gift_id"]) if r.data else 0
    except Exception as e:
        logging.error(f"خطا در _gift_model_total: {e}")
        return 0

def _gift_pick_model():
    """نوع رو با شانس هر نوع انتخاب می‌کنه و یکی از مدل‌های فعالش رو یکنواخت (None اگه چیزی نباشه)."""
    disabled = {int(c) for c in (_SETTINGS.get("gift_custom", {}) or {}).get("disabled", [])}
    avail = {k: [m for m in cfg["models"] if m not in disabled] for k, cfg in GIFT_CATALOG.items()}
    keys = [k for k, v in avail.items() if v and GIFT_CATALOG[k]["weight"] > 0]
    if not keys:
        return None
    type_key = random.choices(keys, weights=[GIFT_CATALOG[k]["weight"] for k in keys], k=1)[0]
    return random.choice(avail[type_key])

_gift_mint_lock = threading.Lock()

GIFT_SLOT_PRICE = 5000
GIFT_SLOT_SETTING_KEY = "gift_slot_capacity"
_gift_slots_lock = threading.Lock()
_gift_slot_revert_tokens = {}


def _gift_slot_capacity(uid):
    """هر کاربر به‌صورت پیش‌فرض یک جای گیفت دارد؛ ظرفیت‌های خریداری‌شده پایدار ذخیره می‌شوند."""
    try:
        values = _SETTINGS.get(GIFT_SLOT_SETTING_KEY, {}) or {}
        return max(1, int(values.get(str(int(uid)), 1) or 1))
    except Exception:
        return 1


def _gift_slot_snapshot(uid):
    gifts = get_user_gifts(uid)
    capacity = _gift_slot_capacity(uid)
    return gifts, capacity, min(len(gifts), capacity)


def _gift_buy(uid):
    """خرید گیفت شانسی فقط در صورت وجود جای خالی انجام می‌شود."""
    if not _gift_ready():
        return False, "❌ جدول premium_gifts ساخته نشده است."
    with _gift_mint_lock:
        try:
            owned = get_user_gifts(uid)
            capacity = _gift_slot_capacity(uid)
            if len(owned) >= capacity:
                return False, "❌ جای خالی برای گیفت ندارید. ابتدا «خرید جای گیفت» را بزنید."
            if get_balance(uid) < GIFT_BOX_PRICE:
                return False, f"❌ موجودی کافی نیست. قیمت گیفت باکس {GIFT_BOX_PRICE:,} 💎 است."
            code = _gift_pick_model()
            if code is None:
                return False, "❌ فعلاً گیفتی برای باز کردن وجود نداره."
            value = _random_gift_price(code)
            try:
                if update_diamonds(uid, -GIFT_BOX_PRICE) is None:
                    return False, "❌ کسر مبلغ انجام نشد."
            except Exception as e:
                logging.error("gift buy (deduct): %s", e)
                return False, "❌ کسر مبلغ انجام نشد."

            last_err = None
            for _ in range(5):
                now_iso = datetime.now(TEHRAN_TZ).isoformat()
                try:
                    # ابتدا از تگ آزاد همین مدل استفاده کن تا گیفت‌های فروخته‌شده دوباره قابل استفاده باشند.
                    free = (supabase.table("premium_gifts").select("instance_id,gift_id")
                            .eq("gift_code", int(code)).is_("owner_id", "null")
                            .order("gift_id").limit(1).execute().data or [])
                    if free:
                        up = supabase.table("premium_gifts").update({
                            "owner_id": int(uid), "current_value": value, "base_value": value,
                            "acquired_price": value, "active": False, "acquired_at": now_iso,
                            "last_market_update": now_iso,
                        }).eq("instance_id", free[0]["instance_id"]).is_("owner_id", "null").execute()
                        if up.data:
                            return True, up.data[0]
                        continue
                    tag = _gift_model_total(code) + 1
                    up = supabase.table("premium_gifts").insert({
                        "gift_id": tag, "gift_code": int(code), "owner_id": int(uid),
                        "current_value": value, "base_value": value, "acquired_price": value,
                        "active": False, "acquired_at": now_iso, "last_market_update": now_iso,
                    }).execute()
                    if up.data:
                        return True, up.data[0]
                except Exception as e:
                    last_err = e
            logging.error("gift buy (insert): %s", last_err)
            update_diamonds(uid, GIFT_BOX_PRICE)
            return False, "❌ باز کردن گیفت ناموفق بود."
        except Exception as e:
            logging.error("gift buy error (%s): %s", uid, e)
            return False, "❌ خرید گیفت ناموفق بود. دوباره امتحان کن."

def get_user_gifts(uid):
    try:return supabase.table("premium_gifts").select("*").eq("owner_id",int(uid)).order("current_value",desc=True).execute().data or []
    except Exception as e:logging.error(f"خطا در get_user_gifts({uid}): {e}");return []

def get_active_gift(uid):
    try:
        r=supabase.table("premium_gifts").select("*").eq("owner_id",int(uid)).eq("active",True).limit(1).execute();return r.data[0] if r.data else None
    except Exception as e:logging.error(f"خطا در get_active_gift: {e}");return None

def display_gift_for_user(uid):
    g=get_active_gift(uid);return _gift_emoji(g["gift_code"],"✨") if g else ""

def set_active_gift(uid,iid):
    try:
        r=supabase.table("premium_gifts").select("*").eq("instance_id",int(iid)).eq("owner_id",int(uid)).limit(1).execute()
        if not r.data:return False,"این گیفت متعلق به شما نیست."
        supabase.table("premium_gifts").update({"active":False}).eq("owner_id",int(uid)).eq("active",True).execute();supabase.table("premium_gifts").update({"active":True}).eq("instance_id",int(iid)).execute();return True,r.data[0]
    except Exception as e:logging.error(f"خطا در set_active_gift: {e}");return False,"خطا در فعال‌سازی گیفت."

def sell_gift(uid,iid):
    try:
        r=supabase.table("premium_gifts").select("*").eq("instance_id",int(iid)).eq("owner_id",int(uid)).limit(1).execute()
        if not r.data:return False,"این گیفت متعلق به شما نیست."
        price=int(r.data[0].get("current_value",0))
        # آزادکردن گیفت شرطی روی صاحب فعلیه؛ اگه کلیک دوم/هم‌زمان باشه ردیفی آپدیت نمی‌شه و پولی داده نمی‌شه
        up=supabase.table("premium_gifts").update({"owner_id":None,"active":False}).eq("instance_id",int(iid)).eq("owner_id",int(uid)).execute()
        if not up.data:return False,"این گیفت متعلق به شما نیست."
        if update_diamonds(uid,price) is None:
            supabase.table("premium_gifts").update({"owner_id":int(uid)}).eq("instance_id",int(iid)).execute()
            return False,"فروش گیفت ناموفق بود."
        return True,price
    except Exception as e:logging.error(f"خطا در sell_gift: {e}");return False,"فروش گیفت ناموفق بود."

def transfer_gift(sender,target,iid):
    try:
        if int(sender)==int(target):return False,"نمی‌توانی گیفت را به خودت منتقل کنی."
        if not get_user(target):return False,"کاربر مقصد هنوز /start نزده است."
        target_gifts = get_user_gifts(target)
        if len(target_gifts) >= _gift_slot_capacity(target):
            return False,"جای خالی برای گیفت در حساب مقصد وجود ندارد."
        r=supabase.table("premium_gifts").select("*").eq("instance_id",int(iid)).eq("owner_id",int(sender)).limit(1).execute()
        if not r.data:return False,"این گیفت متعلق به شما نیست."
        supabase.table("premium_gifts").update({"owner_id":int(target),"active":False}).eq("instance_id",int(iid)).execute();return True,r.data[0]
    except Exception as e:logging.error(f"خطا در transfer_gift: {e}");return False,"انتقال گیفت ناموفق بود."

def update_gift_market():
    # قبلاً آپدیت گیفت‌ها یکی‌یکی و پشت سر هم بود (هزار گیفت = هزار درخواست پشت هم). حالا با ۶ ترد موازی.
    try:
        rows=supabase.table("premium_gifts").select("instance_id,gift_code,current_value").not_.is_("owner_id","null").execute().data or []
        stamp=datetime.now(TEHRAN_TZ).isoformat()
        def _one(r):
            try:
                code=int(r.get("gift_code"));lo,hi=_gift_price_range(code)
                cur=int(r.get("current_value") or _random_gift_price(code))
                new=max(lo,min(hi,cur+random.randint(-GIFT_MAX_PRICE_DOWN,GIFT_MAX_PRICE_UP)))
                supabase.table("premium_gifts").update({"current_value":new,"last_market_update":stamp}).eq("instance_id",r["instance_id"]).execute()
            except Exception as e:logging.error("gift market row %s: %s",r.get("instance_id"),e)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(_one,rows))
    except Exception as e:logging.error("gift market: %s",e)

try:
    premium_gift_scheduler=BackgroundScheduler(timezone=TEHRAN_TZ);premium_gift_scheduler.add_job(update_gift_market,"interval",seconds=GIFT_MARKET_INTERVAL_SECONDS,id="premium_gift_market",replace_existing=True,max_instances=1,coalesce=True);premium_gift_scheduler.start()
except Exception:premium_gift_scheduler=None

def gift_rarity_name(value):
    try:
        v = int(value)
    except Exception:
        v = GIFT_MIN_VALUE
    span = max(1, GIFT_MAX_VALUE - GIFT_MIN_VALUE)
    ratio = (v - GIFT_MIN_VALUE) / span
    if ratio >= 0.85:
        return "🔴 افسانه‌ای"
    if ratio >= 0.55:
        return "🟣 حماسی"
    if ratio >= 0.25:
        return "🔵 کمیاب"
    return "🟢 معمولی"

def gift_pnl_text(current_value, acquired_price):
    try:
        cur = int(current_value)
        base = int(acquired_price)
    except Exception:
        return "نامشخص"
    diff = cur - base
    if base <= 0:
        return "نامشخص"
    percent = (diff / base) * 100
    if diff > 0:
        return f"📈 سود {diff:,} 💎 (%{percent:.1f}+)"
    if diff < 0:
        return f"📉 ضرر {abs(diff):,} 💎 (%{abs(percent):.1f}-)"
    return "➖ بدون سود و ضرر"

def _safe_gift_id(g):
    try:
        return int(g.get("gift_id") or 0)
    except Exception:
        return 0

GIFT_LIST_SEPARATOR = "ـ" * 45
GIFT_PAGE_SIZE = 9


def gifts_text(uid, page=1):
    e = _gift_emoji
    title = f'{e(5830144944399981619, "🎁")} گیفت‌های من'
    gs = get_user_gifts(uid)
    capacity = _gift_slot_capacity(uid)
    visible = gs[:capacity]
    total_pages = max(1, (len(visible) + GIFT_PAGE_SIZE - 1) // GIFT_PAGE_SIZE)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * GIFT_PAGE_SIZE
    page_gifts = visible[start:start + GIFT_PAGE_SIZE]
    diamond = e(5940725397195853882, "💎")

    def line(i, g):
        return f"{i}. {e(g['gift_code'], '✨')} | تگ: {_safe_gift_id(g):,} | ارزش: {int(g.get('current_value') or 0):,}{diamond}"

    total_value = sum(int(g.get("current_value") or 0) for g in gs)
    parts = [title, ""]
    active_index = next((i for i, g in enumerate(visible, 1) if g.get("active")), None)
    if active_index:
        active_gift = visible[active_index - 1]
        parts += [f'گیفت درحال نمایش{e(5938388342281343001, "👁")}', line(active_index, active_gift), GIFT_LIST_SEPARATOR]
    if page_gifts:
        parts += [line(i, g) for i, g in enumerate(page_gifts, start + 1)]
    else:
        parts.append("هنوز هیچ گیفتی نداری.")
    parts += [GIFT_LIST_SEPARATOR,
              f'{e(5823592920250327036, "💰")} ارزش کل گیفت ها: {total_value:,}{diamond}',
              f'{e(5830338333892418460, "🎁")} تعداد کل گیفت ها: {len(visible)}/{capacity}']
    if total_pages > 1:
        parts.append(f'صفحه {page} از {total_pages}')
    return "\n".join(parts)


def _gift_mine_markup(uid, page=1):
    all_gifts = get_user_gifts(uid)
    capacity = _gift_slot_capacity(uid)
    visible = all_gifts[:capacity]
    total_pages = max(1, (len(visible) + GIFT_PAGE_SIZE - 1) // GIFT_PAGE_SIZE)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * GIFT_PAGE_SIZE
    page_gifts = visible[start:start + GIFT_PAGE_SIZE]
    m = types.InlineKeyboardMarkup()
    gift_buttons = [
        colored_button("\u2800", callback_data=f"giftshow|{g['instance_id']}|{page}|{uid}", icon_custom_emoji_id=str(g['gift_code']))
        for g in page_gifts
    ]
    for k in range(0, len(gift_buttons), 3):
        m.row(*gift_buttons[k:k + 3])
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(colored_button("قبلی", callback_data=f"giftminepage|{page - 1}|{uid}", icon_custom_emoji_id="5845874566336880660"))
        if page < total_pages:
            nav.append(colored_button("بعدی", callback_data=f"giftminepage|{page + 1}|{uid}", icon_custom_emoji_id="5843732253829503840"))
        if nav:
            m.row(*nav)
    m.row(colored_button("خرید جای گیفت", callback_data=f"giftbuy_slot|{uid}", icon_custom_emoji_id="5830338333892418460"))
    m.row(back_button(f"giftback|{uid}"))
    return m


def _gift_show_mine_panel(chat_id, message_id, uid, page=1):
    text = gifts_text(uid, page)
    markup = _gift_mine_markup(uid, page)
    safe_edit_message(text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")


def _gift_slot_revert(chat_id, message_id, uid, nonce):
    key = (chat_id, message_id)
    if _gift_slot_revert_tokens.get(key) != nonce:
        return
    _gift_slot_revert_tokens.pop(key, None)
    _gift_show_mine_panel(chat_id, message_id, uid, 1)

@bot.message_handler(func=lambda m:text_is(m,"گیفت","گیفت شانسی","گیفت باکس"))
def text_gift_box(message):
    if _feature_gate(message, "gift"):
        return
    uid=message.from_user.id
    if not get_user(uid):bot.reply_to(message,"اول /start بزن.");return
    m=_gift_home_markup(uid);show_or_edit_panel(message,uid,"giftbox",gift_box_text(uid),m,parse_mode="HTML")

def _gift_home_markup(uid):
    m=types.InlineKeyboardMarkup();m.row(colored_button("خرید گیفت باکس",callback_data=f"giftbuy|{uid}",icon_custom_emoji_id="5927029738626359516"));m.row(colored_button("گیفت های من",callback_data=f"giftmine|{uid}",icon_custom_emoji_id="5818976337213266965"))
    return m

@bot.callback_query_handler(func=lambda c:c.data.startswith("giftback|"))
def gift_back_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid=check_panel_owner(call)
    if uid is None:return
    cancel_pending_step(call.message.chat.id,uid)
    bot.answer_callback_query(call.id)
    safe_edit_message(gift_box_text(uid),call.message.chat.id,call.message.message_id,reply_markup=_gift_home_markup(uid),parse_mode="HTML")

@bot.callback_query_handler(func=lambda c:c.data.startswith("giftbuy|"))
def gift_buy_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid=check_panel_owner(call)
    if uid is None:return
    ok,g=_gift_buy(uid)
    if not ok:
        try: bot.answer_callback_query(call.id, str(g), show_alert=True)
        except Exception: bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    owned_count = count_owned_by_code(uid, g['gift_code'])
    e = _gift_emoji
    gcode = int(g["gift_code"])
    safe_send_message(
        uid,
        f'شما با موفقیت گیفت باکس خود را باز کردید{e(5938109560249127910, "🎉")}\n\n'
        f'{e(5929398662198206659, "🎁")} گیفت جدید{e(5820903824046432799, "👇")}\n\n'
        f'{e(gcode, "✨")}\n'
        f'┘─ {e(5818976337213266965, "🎁")} نوع گیفت: {_gift_type_name(gcode)}\n'
        f'┘─ {e(5938348905891632091, "🏷")} مدل گیفت: {_gift_model_name(gcode)}\n'
        f'┘─ {e(5819046718842347822, "🆔")} آیدی گیفت: {int(g["gift_id"]):,}\n'
        f'┘─ {e(5823592920250327036, "💰")} ارزش گیفت: {int(g["current_value"]):,}{e(5940725397195853882, "💎")}\n\n'
        f'{e(5830338333892418460, "✅")} شما تعداد {owned_count:,} از این گیفت داری{e(5938109560249127910, "🎉")}\n'
        f'{e(5832397371278892338, "📊")} درکل تعداد {max(_gift_model_total(gcode), int(g["gift_id"])):,} از این نوع گیفت وجود دارد{e(5938109560249127910, "🎉")}',
        parse_mode="HTML",
    )
    safe_edit_message(f'{_gift_emoji(5830144944399981619, "🎁")} گیفت باز شد پیوی خودت رو چک کن{_gift_emoji(5938311423712039050, "📩")}',call.message.chat.id,call.message.message_id,parse_mode="HTML")

@bot.callback_query_handler(func=lambda c:c.data.startswith("giftmine|"))
def gift_mine_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    cancel_pending_step(call.message.chat.id, uid)
    bot.answer_callback_query(call.id)
    _gift_show_mine_panel(call.message.chat.id, call.message.message_id, uid, 1)


@bot.callback_query_handler(func=lambda c:c.data.startswith("giftminepage|"))
def gift_mine_page_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        _, page_s, _uid = call.data.split("|", 2)
        page = max(1, int(page_s))
    except Exception:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    _gift_show_mine_panel(call.message.chat.id, call.message.message_id, uid, page)


@bot.callback_query_handler(func=lambda c:c.data.startswith("giftbuy_slot|"))
def gift_buy_slot_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    bot.answer_callback_query(call.id)
    e = _gift_emoji
    text = (
        f'{e(5832240029446971049, "🛍")}خرید جای اضافه گیفت{e(5830338333892418460, "🎁")}\n\n'
        f'قیمت: {GIFT_SLOT_PRICE:,}{e(5940725397195853882, "💎")}\n\n'
        f'آیا از خرید جا مطمئنید؟{e(5938506518306492774, "❓")}'
    )
    m = types.InlineKeyboardMarkup()
    m.row(
        colored_button("بله", callback_data=f"gift_slot_confirm|yes|{uid}", icon_custom_emoji_id="5938109560249127910"),
        colored_button("خیر", callback_data=f"gift_slot_confirm|no|{uid}", icon_custom_emoji_id="5938290000415167172"),
    )
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c:c.data.startswith("gift_slot_confirm|"))
def gift_slot_confirm_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        _, answer, _uid = call.data.split("|", 2)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    chat_id, message_id = call.message.chat.id, call.message.message_id
    if answer != "yes":
        bot.answer_callback_query(call.id)
        _gift_show_mine_panel(chat_id, message_id, uid, 1)
        return

    with _gift_slots_lock:
        balance = get_balance(uid)
        if balance < GIFT_SLOT_PRICE:
            bot.answer_callback_query(call.id, "موجودی الماس کافی نیست.", show_alert=True)
            return
        if update_diamonds(uid, -GIFT_SLOT_PRICE) is None:
            bot.answer_callback_query(call.id, "کسر مبلغ انجام نشد.", show_alert=True)
            return
        old_values = dict(_SETTINGS.get(GIFT_SLOT_SETTING_KEY, {}) or {})
        old_capacity = _gift_slot_capacity(uid)
        new_values = dict(old_values)
        new_values[str(uid)] = old_capacity + 1
        if not setting_save(GIFT_SLOT_SETTING_KEY, new_values):
            with _SETTINGS_LOCK:
                _SETTINGS[GIFT_SLOT_SETTING_KEY] = old_values
            update_diamonds(uid, GIFT_SLOT_PRICE)
            bot.answer_callback_query(call.id, "ذخیره جای گیفت انجام نشد؛ مبلغ برگردانده شد.", show_alert=True)
            return

    bot.answer_callback_query(call.id)
    success_text = f'خرید جای گیفت انجام شد{_gift_emoji(5938109560249127910, "✅")}'
    safe_edit_message(success_text, chat_id, message_id, reply_markup=None, parse_mode="HTML")
    nonce = time.time_ns()
    key = (chat_id, message_id)
    _gift_slot_revert_tokens[key] = nonce
    _start_daemon_timer(5, _gift_slot_revert, chat_id, message_id, uid, nonce)


@bot.callback_query_handler(func=lambda c:c.data.startswith("giftshow|"))
def gift_show_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        parts = call.data.split("|")
        iid = int(parts[1])
        page = max(1, int(parts[2])) if len(parts) >= 4 else 1
        gs = [g for g in get_user_gifts(uid)[:_gift_slot_capacity(uid)] if int(g["instance_id"]) == iid]
        if not gs:
            bot.answer_callback_query(call.id, "این گیفت در جایگاه‌های فعلی شما نمایش داده نمی‌شود.", show_alert=True)
            return
        g = gs[0]
        m = types.InlineKeyboardMarkup()
        m.row(colored_button("👤 نمایش کنار اسم", callback_data=f"giftactive|{iid}|{uid}"))
        m.row(colored_button("💰 فروش گیفت", callback_data=f"giftsell|{iid}|{uid}"), colored_button("🔄 انتقال گیفت", callback_data=f"gifttransfer|{iid}|{uid}"))
        m.row(back_button(f"giftminepage|{page}|{uid}"))
        acquired_price = g.get("acquired_price", g.get("base_value", g.get("current_value")))
        info_text = (
            f"{_gift_emoji(g['gift_code'],'✨')} گیفت شما\n\n"
            f"┘─ نام : {gift_rarity_name(g['current_value'])}\n"
            f"┘─ آیدی گیفت : {_safe_gift_id(g):,}\n"
            f"┘─ قیمت فعلی : {int(g['current_value']):,} 💎\n"
            f"┘─ قیمت خرید : {int(acquired_price):,} 💎\n"
            f"┘─ وضعیت سود/ضرر : {gift_pnl_text(g['current_value'], acquired_price)}\n"
            f"┘─ وضعیت نمایش : {'فعال کنار اسم ✅' if g.get('active') else 'غیرفعال'}"
        )
        bot.answer_callback_query(call.id)
        safe_edit_message(info_text, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception as e:
        logging.error(f"خطا در gift_show_callback({uid}): {e}")
        bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith("giftactive|"))
def gift_active_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid=check_panel_owner(call)
    if uid is None:return
    ok,r=set_active_gift(uid,int(call.data.split("|")[1]));bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith("giftsell|"))
def gift_sell_callback(call):
    if _feature_gate(call, "gift"):
        return
    uid=check_panel_owner(call)
    if uid is None:return
    ok,r=sell_gift(uid,int(call.data.split("|")[1]));bot.answer_callback_query(call.id)
    bm=types.InlineKeyboardMarkup();bm.row(back_button(f"giftmine|{uid}"))
    safe_edit_message(gifts_text(uid),call.message.chat.id,call.message.message_id,reply_markup=bm,parse_mode="HTML")

@bot.callback_query_handler(func=lambda c:c.data.startswith("gifttransfer|"))
def gift_transfer_prompt(call):
    if _feature_gate(call, "gift"):
        return
    uid=check_panel_owner(call)
    if uid is None:return
    iid=int(call.data.split("|")[1]);bm=types.InlineKeyboardMarkup();bm.row(back_button(f"giftmine|{uid}"));safe_edit_message("🔄 آیدی عددی گیرنده را در پاسخ همین پیام بفرست.",call.message.chat.id,call.message.message_id,reply_markup=bm);register_timed_next_step_handler(call.message,gift_transfer_step,uid,iid,call.message.message_id,expected_user_id=uid)

def gift_transfer_step(message,expected_user_id,instance_id,panel_msg_id):
    if message.from_user.id!=expected_user_id:return
    try:target=int(_normalize_digits(message.text.strip()))
    except Exception:safe_edit_message("❌ آیدی عددی معتبر نیست.",message.chat.id,panel_msg_id);return
    ok,r=transfer_gift(expected_user_id,target,instance_id);bm=types.InlineKeyboardMarkup();bm.row(back_button(f"giftmine|{expected_user_id}"));safe_edit_message("✅ گیفت با موفقیت منتقل شد." if ok else f"❌ {r}",message.chat.id,panel_msg_id,reply_markup=bm)
    if ok:
        try:safe_send_message(target,"🎁 یک گیفت برایت منتقل شد! پیوی خودت را چک کن.")
        except Exception:pass

# ================== سیستم سطح‌بندی، آمار و اعلان‌ها ==================
# نکته پیاده‌سازی: این بخش به جداول زیر در Supabase نیاز دارد که باید از قبل
# ساخته شده باشند (به فایل new_tables.sql مراجعه کن):
#   user_progress(user_id PK, xp int, level int, messages_count int,
#                  bets_won_count int, casino_wins_count int, cosmetics jsonb)
#   group_notify_settings(chat_id PK, level_up bool, new_diamond bool,
#                          daily_gift bool, diamond_timer bool, betting bool)

def get_progress(user_id):
    try:
        resp = supabase.table("user_progress").select("*").eq("user_id", user_id).execute()
        if resp.data:
            return resp.data[0]
        default = {
            "user_id": user_id, "xp": 0, "level": 1,
            "messages_count": 0, "bets_won_count": 0, "casino_wins_count": 0,
            "cosmetics": {}
        }
        supabase.table("user_progress").insert(default).execute()
        return default
    except Exception as e:
        logging.error(f"خطا در get_progress: {e}")
        try:
            resp = supabase.table("user_progress").select("*").eq("user_id", user_id).execute()
            if resp.data:
                return resp.data[0]
        except Exception as read_error:
            logging.error(f"خطا در بازخوانی get_progress: {read_error}")
        return {"user_id": user_id, "xp": 0, "level": 1, "messages_count": 0,
                "bets_won_count": 0, "casino_wins_count": 0, "cosmetics": {}}

def level_info(level):
    for lvl, title, xp_needed, reward in LEVELS:
        if lvl == level:
            return lvl, title, xp_needed, reward
    return LEVELS[0]

def xp_needed_for_next_level(level):
    if level >= MAX_LEVEL:
        return None
    for lvl, _title, xp_needed, _reward in LEVELS:
        if lvl == level + 1:
            return xp_needed
    return None

def _notify_group_if_enabled(chat_id, key, text):
    if not chat_id:
        return
    try:
        settings = get_notify_settings(chat_id)
        if settings.get(key, True):
            bot.send_message(chat_id, text)
    except Exception as e:
        logging.error(f"خطا در ارسال اعلان گروه: {e}")

def add_xp(user_id, amount, chat_id=None, display_name=None):
    """XP اضافه می‌کند، در صورت ارتقای سطح پاداش می‌دهد و اعلان می‌فرستد."""
    if amount <= 0:
        return
    try:
        progress = get_progress(user_id)
        new_xp = int(progress.get("xp", 0) or 0) + amount
        old_level = int(progress.get("level", 1) or 1)
        new_level = old_level
        while new_level < MAX_LEVEL:
            need = xp_needed_for_next_level(new_level)
            if need is not None and new_xp >= need:
                new_level += 1
            else:
                break
        supabase.table("user_progress").update(
            {"xp": new_xp, "level": new_level}
        ).eq("user_id", user_id).execute()

        if new_level > old_level:
            name = display_name or str(user_id)
            for lvl in range(old_level + 1, new_level + 1):
                _, title, _need, reward = level_info(lvl)
                if reward:
                    update_diamonds(user_id, reward)
                try:
                    bot.send_message(
                        user_id,
                        f"🏆 تبریک! به سطح {lvl} رسیدی: {title}\n💎 پاداش: {reward:,} الماس"
                    )
                except Exception:
                    pass
                _notify_group_if_enabled(
                    chat_id, "level_up",
                    f"🏆 {name} به سطح {lvl} ({title}) رسید! 🎉"
                )
    except Exception as e:
        logging.error(f"خطا در add_xp: {e}")


def _format_mmss(seconds):
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


# ================== کلمه «ماین» ==================
# هر کاربر هر ۵ دقیقه یک بار با گفتن «ماین» در گروه الماس می‌گیرد.
# پاداش پله‌پله و کم‌کم بالا می‌رود: (از دفعه‌ی، پاداش پایه، کمترین افزایش، بیشترین افزایش)
MINE_WORD_COOLDOWN_SECONDS = 5 * 60
MINE_WORD_STAGES = [
    (1, 50, 2, 7),
    (11, 100, 5, 15),
    (21, 200, 5, 15),
    (50, 500, 5, 15),
    (100, 1000, 0, 0),
]


def _mine_word_floor(claim_no):
    """کمترین پاداشی که در این دفعه حتماً باید داده شود."""
    base = MINE_WORD_STAGES[0][1]
    for start, stage_base, _lo, _hi in MINE_WORD_STAGES:
        if claim_no >= start:
            base = stage_base
    return base


def _mine_word_next_reward(current, next_claim_no):
    """پاداش دفعه‌ی بعد: کمی بیشتر از فعلی، بدون عبور از پایه‌ی پله‌ی بعدی."""
    idx = 0
    for i, stage in enumerate(MINE_WORD_STAGES):
        if current >= stage[1]:
            idx = i
    stage = MINE_WORD_STAGES[idx]
    if idx == len(MINE_WORD_STAGES) - 1:
        return stage[1]
    cap = MINE_WORD_STAGES[idx + 1][1]
    nxt = min(current + random.randint(stage[2], stage[3]), cap)
    return max(nxt, _mine_word_floor(next_claim_no))


def register_mine_word(user_id, message=None):
    try:
        with _get_mine_word_lock(user_id):
            progress = get_progress(user_id)
            now_ts = time.time()
            try:
                last_ts = float(progress.get("last_mine_word_at") or 0)
            except (TypeError, ValueError):
                last_ts = 0
            elapsed = now_ts - last_ts
            if elapsed < MINE_WORD_COOLDOWN_SECONDS:
                if message is not None:
                    e = _gift_emoji
                    bot.reply_to(
                        message,
                        f'تازه ماین کردی{e(5818716826699307883, "❗")}\n'
                        f'{e(5825656411517886523, "⏳")} تا ماین بعدی: {_format_mmss(MINE_WORD_COOLDOWN_SECONDS - elapsed)}{e(5830144944399981619, "⏱")}',
                        parse_mode="HTML",
                    )
                return

            count = int(progress.get("mine_word_count") or 0)
            stored = int(progress.get("mine_word_reward") or 0)
            reward = max(stored, _mine_word_floor(count + 1))
            next_reward = _mine_word_next_reward(reward, count + 2)

            # اول پیشرفت و زمان ذخیره می‌شود؛ اگر ذخیره نشد (مثلاً ستون‌ها ساخته نشده)
            # پاداشی داده نمی‌شود تا کولداون دور زده نشود.
            try:
                supabase.table("user_progress").update({
                    "mine_word_count": count + 1,
                    "mine_word_reward": next_reward,
                    "last_mine_word_at": now_ts,
                }).eq("user_id", user_id).execute()
            except Exception as e:
                logging.error(f"ذخیره‌ی پیشرفت ماین برای {user_id} ناموفق بود (ستون‌ها ساخته شده؟): {e}")
                return

            new_balance = update_diamonds(user_id, reward)
            if new_balance is None:
                logging.error(f"پاداش ماین برای {user_id} ثبت نشد؛ پیشرفت برگردانده می‌شود.")
                try:
                    supabase.table("user_progress").update({
                        "mine_word_count": count,
                        "mine_word_reward": stored,
                        "last_mine_word_at": last_ts,
                    }).eq("user_id", user_id).execute()
                except Exception as revert_error:
                    logging.error(f"برگرداندن پیشرفت ماین ناموفق بود: {revert_error}")
                return

            if message is not None:
                e = _gift_emoji
                if not isinstance(new_balance, int):
                    new_balance = get_balance(user_id)
                bot.reply_to(
                    message,
                    f'{reward:,} الماس{e(5940725397195853882, "💎")} گرفتی{e(5938109560249127910, "✅")}\n'
                    f'{e(5823592920250327036, "💰")} الماس هات : {int(new_balance or 0):,}{e(5940725397195853882, "💎")}\n'
                    f'{e(5818984798298841943, "⏰")} بعد از {MINE_WORD_COOLDOWN_SECONDS // 60} دقیقه میتونی دوباره ماین کنی{e(5938024562846340196, "⛏")}',
                    parse_mode="HTML",
                )
    except Exception as e:
        logging.error(f"خطا در register_mine_word: {e}")


def register_ring_diamond_win(user_id, chat_id, display_name):
    add_xp(user_id, XP_PER_RING_DIAMOND, chat_id=chat_id, display_name=display_name)
    user = get_user(user_id)
    _notify_group_if_enabled(chat_id, "new_diamond",
                             f'{_gift_emoji(5940725397195853882, "💎")} {display_name} یک الماس انگشتر پیدا کرد{_gift_emoji(5938109560249127910, "✅")}')

def register_bet_win(user_id, chat_id, display_name):
    try:
        progress = get_progress(user_id)
        count = int(progress.get("bets_won_count", 0) or 0) + 1
        supabase.table("user_progress").update({"bets_won_count": count}).eq("user_id", user_id).execute()
        add_xp(user_id, XP_PER_BET_WIN, chat_id=chat_id, display_name=display_name)
    except Exception as e:
        logging.error(f"خطا در register_bet_win: {e}")

def register_casino_win(user_id, chat_id, display_name, amount_won=0):
    try:
        progress = get_progress(user_id)
        count = int(progress.get("casino_wins_count", 0) or 0) + 1
        supabase.table("user_progress").update({"casino_wins_count": count}).eq("user_id", user_id).execute()
        bonus = min(XP_BONUS_CASINO_CAP, (amount_won // 100_000) * XP_BONUS_PER_CASINO_100K)
        add_xp(user_id, XP_PER_CASINO_WIN + bonus, chat_id=chat_id, display_name=display_name)
    except Exception as e:
        logging.error(f"خطا در register_casino_win: {e}")


def register_daily_gift(user_id, chat_id, display_name):
    add_xp(user_id, XP_PER_DAILY_GIFT, chat_id=chat_id, display_name=display_name)
    _notify_group_if_enabled(chat_id, "daily_gift", f"🎁 {display_name} هدیه روزانه گرفت!")

def stats_text_for_user(user_id, display_name):
    user = get_user(user_id) or {}
    progress = get_progress(user_id)
    bets_won = int(progress.get("bets_won_count", 0) or 0)
    casino_wins = int(progress.get("casino_wins_count", 0) or 0)
    diamonds = user.get("diamonds", 0)
    return (
        "◈ ━━━━━ 𝗦𝘁𝗮𝘁𝗶𝘀𝘁𝗶𝗰𝘀 ━━━━━ ◈\n"
        "⏣ | آمار شما\n\n"
        f"❖ | شرط‌های برده: {bets_won:,}\n"
        f"❖ | بازی‌های کازینو برده: {casino_wins:,}\n"
        f"$ | موجودی فعلی: {diamonds:,}\n"
        "◈ ━━━━━ 𝗦𝘁𝗮𝘁𝗶𝘀𝘁𝗶𝗰𝘀 ━━━━━ ◈"
    )

# ---------- تنظیمات اعلان‌های گروه ----------
def get_notify_settings(chat_id):
    try:
        resp = supabase.table("group_notify_settings").select("*").eq("chat_id", chat_id).execute()
        if resp.data:
            row = dict(resp.data[0])
            for k, v in NOTIFY_DEFAULTS.items():
                if k not in row or row[k] is None:
                    row[k] = v
            return row
        default = {"chat_id": chat_id, **NOTIFY_DEFAULTS}
        supabase.table("group_notify_settings").insert(default).execute()
        return default
    except Exception as e:
        logging.error(f"خطا در get_notify_settings: {e}")
        return {"chat_id": chat_id, **NOTIFY_DEFAULTS}

def set_notify_setting(chat_id, key, value):
    try:
        supabase.table("group_notify_settings").update({key: value}).eq("chat_id", chat_id).execute()
    except Exception as e:
        logging.error(f"خطا در set_notify_setting: {e}")

def notify_settings_markup(chat_id):
    settings = get_notify_settings(chat_id)
    markup = types.InlineKeyboardMarkup()
    for key, label in NOTIFY_LABELS.items():
        state = "✅" if settings.get(key, True) else "❌"
        markup.add(colored_button(
            f"{state} {label}", callback_data=f"notifytoggle|{key}"
        ))
    markup.add(colored_button("🏠 بستن", callback_data="notifyclose"))
    return markup

def notify_settings_text(chat_id):
    settings = get_notify_settings(chat_id)
    lines = ["⚙️ تنظیمات اعلان‌های گروه", "─────────────────────"]
    for key, label in NOTIFY_LABELS.items():
        state = "فعال" if settings.get(key, True) else "غیرفعال"
        mark = "✅" if settings.get(key, True) else "❌"
        lines.append(f"{mark} {label}: {state}")
    return "\n".join(lines)

@bot.message_handler(commands=["notifysettings"])
@bot.message_handler(func=lambda m: text_is(m, "تنظیمات اعلان"))
def cmd_notify_settings(message):
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "این دستور فقط داخل گروه کار می‌کند.")
        return
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        is_admin = member.status in ("administrator", "creator") or message.from_user.id in ADMIN_IDS
    except Exception:
        is_admin = message.from_user.id in ADMIN_IDS
    if not is_admin:
        bot.reply_to(message, "⛔ فقط ادمین‌های گروه می‌توانند تنظیمات اعلان را تغییر دهند.")
        return
    bot.reply_to(message, notify_settings_text(message.chat.id), reply_markup=notify_settings_markup(message.chat.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("notifytoggle|"))
def notify_toggle(call):
    if call.message.chat.type not in ("group", "supergroup"):
        bot.answer_callback_query(call.id)
        return
    try:
        member = bot.get_chat_member(call.message.chat.id, call.from_user.id)
        is_admin = member.status in ("administrator", "creator") or call.from_user.id in ADMIN_IDS
    except Exception:
        is_admin = call.from_user.id in ADMIN_IDS
    if not is_admin:
        bot.answer_callback_query(call.id, "⛔ فقط ادمین‌های گروه اجازه دارند.", show_alert=True)
        return
    key = call.data.split("|", 1)[1]
    if key not in NOTIFY_DEFAULTS:
        bot.answer_callback_query(call.id, "تنظیم نامعتبر است.", show_alert=True)
        return
    settings = get_notify_settings(call.message.chat.id)
    new_value = not settings.get(key, True)
    set_notify_setting(call.message.chat.id, key, new_value)
    bot.answer_callback_query(call.id)
    safe_edit_message(
        notify_settings_text(call.message.chat.id),
        call.message.chat.id, call.message.message_id,
        reply_markup=notify_settings_markup(call.message.chat.id)
    )

@bot.callback_query_handler(func=lambda call: call.data == "notifyclose")
def notify_close(call):
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

LAQAB_DURATION_SECONDS = 7 * 86400  # هر لقب ویژه ۱ هفته اعتبار داره

def _migrate_legacy_tags(cosmetics):
    """اگه کاربر از سیستم قدیمی (تک‌لقب) یه لقب داشته باشه، به فرمت لیست جدید تبدیلش می‌کنه."""
    if "tags" not in cosmetics and cosmetics.get("tag"):
        cosmetics["tags"] = [{"name": cosmetics["tag"], "expires": cosmetics.get("tag_expires") or 0}]
    cosmetics.setdefault("tags", [])
    return cosmetics

def _purge_expired_tags(cosmetics):
    """لقب‌های منقضی‌شده رو از لیست حذف می‌کنه؛ اگه لقب فعال هم منقضی شده بود پاکش می‌کنه."""
    now = int(time.time())
    tags = [t for t in cosmetics.get("tags", []) if int(t.get("expires") or 0) > now]
    removed = len(tags) != len(cosmetics.get("tags", []))
    cosmetics["tags"] = tags
    if cosmetics.get("active_tag") and not any(t.get("name") == cosmetics["active_tag"] for t in tags):
        cosmetics["active_tag"] = None
        removed = True
    return cosmetics, removed

def _apply_cosmetic(user_id, cosmetic_type, value):
    try:
        progress = get_progress(user_id)
        cosmetics = dict(progress.get("cosmetics") or {})
        cosmetics = _migrate_legacy_tags(cosmetics)
        cosmetics, _ = _purge_expired_tags(cosmetics)
        if cosmetic_type == "tag":
            tags = cosmetics["tags"]
            new_expires = int(time.time()) + LAQAB_DURATION_SECONDS
            existing = next((t for t in tags if t.get("name") == value), None)
            if existing:
                existing["expires"] = new_expires
            else:
                tags.append({"name": value, "expires": new_expires})
            cosmetics["tags"] = tags
            # توجه: لقب جدید خودکار فعال نمی‌شه، کاربر باید خودش از بخش لقب‌ها انتخابش کنه.
            cosmetics.pop("tag", None)
            cosmetics.pop("tag_expires", None)
        supabase.table("user_progress").update({"cosmetics": cosmetics}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در _apply_cosmetic: {e}")

def get_user_tags(user_id):
    """همه‌ی لقب‌های هنوز-معتبرِ کاربر رو برمی‌گردونه (منقضی‌شده‌ها خودکار حذف می‌شن)."""
    try:
        progress = get_progress(user_id)
        cosmetics = _migrate_legacy_tags(dict(progress.get("cosmetics") or {}))
        cosmetics, changed = _purge_expired_tags(cosmetics)
        if changed:
            supabase.table("user_progress").update({"cosmetics": cosmetics}).eq("user_id", user_id).execute()
        return cosmetics.get("tags", []), cosmetics.get("active_tag")
    except Exception as e:
        logging.error(f"خطا در get_user_tags: {e}")
        return [], None

def set_active_tag(user_id, index):
    """یکی از لقب‌های معتبر کاربر رو به‌عنوان لقب نمایشی فعال انتخاب می‌کنه."""
    try:
        progress = get_progress(user_id)
        cosmetics = _migrate_legacy_tags(dict(progress.get("cosmetics") or {}))
        cosmetics, _ = _purge_expired_tags(cosmetics)
        tags = cosmetics.get("tags", [])
        if index < 0 or index >= len(tags):
            return False, "لقب پیدا نشد."
        entry = tags[index]
        cosmetics["active_tag"] = entry["name"]
        supabase.table("user_progress").update({"cosmetics": cosmetics}).eq("user_id", user_id).execute()
        return True, entry["name"]
    except Exception as e:
        logging.error(f"خطا در set_active_tag: {e}")
        return False, "خطایی پیش اومد."

def get_active_tag(user_id):
    """لقب فعال کاربر رو برمی‌گردونه، یا None اگه نداره/منقضی شده."""
    try:
        tags, active_name = get_user_tags(user_id)
        if not active_name:
            return None
        if not any(t.get("name") == active_name for t in tags):
            return None
        return active_name
    except Exception as e:
        logging.error(f"خطا در get_active_tag: {e}")
        return None

def _format_remaining(expires_ts):
    remaining = int(expires_ts) - int(time.time())
    if remaining <= 0:
        return "⌛ منقضی شده"
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    if days > 0:
        return f"⏳ {days} روز و {hours} ساعت"
    if hours > 0:
        return f"⏳ {hours} ساعت و {minutes} دقیقه"
    return f"⏳ {minutes} دقیقه"

def display_name_with_tag(user_id, display_name):
    name=(display_name or "کاربر").strip()
    if "<tg-emoji" in name:
        return name
    if "🤖" in name or name=="ربات":return "🤖 ربات" if "ربات" in name else name
    try:
        g=display_gift_for_user(user_id)
        if g:return f"{g} {name}"
    except Exception:pass
    return name

def casino_name_with_tag(player):
    # سازگاری با کدهای قدیمی؛ عمداً هیچ لقبی اضافه نمی‌کند.
    return display_name_with_tag(player.get("id"), player.get("name"))

# ================== پارس کردن مقادیر با کا/میل ==================
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
def _normalize_digits(text):
    """ارقام فارسی/عربی رو به انگلیسی تبدیل می‌کنه."""
    table = str.maketrans(PERSIAN_DIGITS + "٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(table)

# ترتیب مهمه: عبارت‌های بلندتر (میلیون/هزار) قبل از کوتاه‌ترها (م/ک) چک بشن
_AMOUNT_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(میلیون|میل|million|m|هزار|کا|کی|ک|k)?$",
    re.IGNORECASE
)

# برای استفاده داخل رجکس‌های تشخیص پیام (تریگر دستورات متنی)؛ عدد + پسوند اختیاری کا/کی/میل
AMOUNT_TOKEN = r"[\d۰-۹]+(?:[.,،][\d۰-۹]+)?\s*(?:میلیون|میل|million|m|هزار|کا|کی|ک|k)?"

def parse_amount(raw_text):
    """
    ورودی مثل '120', '120k', '120 k', '120کا', '120 کا', '12میل', '12 میلیون'
    رو به عدد صحیح تبدیل می‌کنه. کا/k = ضرب در هزار، میل/میلیون/m = ضرب در میلیون.
    اگه فرمت نامعتبر بود None برمی‌گردونه.
    """
    if not raw_text:
        return None
    text = _normalize_digits(raw_text.strip().lower())
    text = text.replace("،", "").replace(",", "")  # جداکننده هزارگان رو حذف کن
    m = _AMOUNT_RE.match(text)
    if not m:
        return None
    number = float(m.group(1))
    suffix = m.group(2)
    if suffix in ("هزار", "کا", "کی", "ک", "k"):
        number *= 1_000
    elif suffix in ("میلیون", "میل", "million", "m"):
        number *= 1_000_000
    result = int(round(number))
    if result <= 0:
        return None
    return result

def extract_amount_from_text(text, pattern_before):
    """
    برای پیام‌هایی مثل 'شرط بندی 120کا' یا 'انتقال الماس 12 میل':
    pattern_before رگ‌اکسی برای قسمت قبل از عدده (مثلاً 'شرط\\s*بندی?\\s+').
    عدد+واحد بعدش رو با parse_amount می‌خونه.
    """
    unit = r"(?:میلیون|میل|هزار|کا|ک|million|k|m)"
    match = re.search(
        pattern_before + r"([۰-۹0-9,،.]+\s*" + unit + r"?)",
        text, re.IGNORECASE
    )
    if not match:
        return None
    return parse_amount(match.group(1))

def get_effective_tax_rate(user_id, context="bet"):
    """نرخ مالیات (کازینو جدا از شرطبندی/دینامیت؛ از پنل مدیریت قابل تغییره)."""
    return TAX_RATE_CASINO if context == "casino" else TAX_RATE

def calculate_payout(winner_id, pool, context="bet"):
    tax_rate = get_effective_tax_rate(winner_id, context)
    admin_tax = int(pool * tax_rate)
    return max(0, pool - admin_tax), admin_tax, 0, tax_rate, 0


# ================== واریز خودکار سود بانک ==================
def apply_all_bank_interest():
    """هر روز در ساعت ۰۰:۰۰ تهران سود همه حساب‌های بانکی را واریز می‌کند."""
    try:
        response = supabase.table("users").select(
            "user_id,bank_account_number,bank_balance,bank_interest_date"
        ).not_.is_("bank_account_number", "null").execute()

        for user in (response.data or []):
            user_id = user.get("user_id")
            try:
                apply_bank_interest(user_id)
            except Exception as e:
                logging.error(f"خطا در واریز سود بانک برای {user_id}: {e}")

        logging.info("سود روزانه بانک‌ها در ساعت ۰۰:۰۰ تهران بررسی و واریز شد.")
    except Exception as e:
        logging.error(f"خطا در اجرای سود خودکار بانک: {e}")


# Scheduler timezone must be Tehran so 00:00 is Iranian local midnight.
bank_scheduler = BackgroundScheduler(timezone=TEHRAN_TZ)
bank_scheduler.add_job(
    apply_all_bank_interest,
    "cron",
    hour=0,
    minute=0,
    id="daily_bank_interest",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
bank_scheduler.start()


def get_gift_code(code):
    """دریافت اطلاعات یک کد هدیه بر اساس متن کد."""
    try:
        resp = supabase.table("gift_codes").select("*").eq("code", code).execute()
        return resp.data[0] if resp.data else None
    except Exception as e:
        logging.error(f"خطا در get_gift_code: {e}")
        return None

def generate_unique_gift_code():
    """تولید یک کد هدیه یکتای ۸ کاراکتری (حروف بزرگ انگلیسی + عدد)."""
    try:
        for _ in range(50):
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            if not get_gift_code(code):
                return code
        return None
    except Exception as e:
        logging.error(f"خطا در generate_unique_gift_code: {e}")
        return None

def create_gift_code(admin_id, amount, capacity):
    """ساخت کد هدیه جدید با مقدار جایزه و ظرفیت مشخص و ذخیره‌ی آن در دیتابیس."""
    code = generate_unique_gift_code()
    if not code:
        return None
    try:
        supabase.table("gift_codes").insert({
            "code": code,
            "amount": int(amount),
            "capacity": int(capacity),
            "redeemed_count": 0,
            "created_by": admin_id,
        }).execute()
        return code
    except Exception as e:
        logging.error(f"خطا در create_gift_code: {e}")
        return None

_gift_code_lock = threading.Lock()

def redeem_gift_code(user_id, code):
    """
    تلاش برای فعال‌سازی یک کد هدیه توسط یک کاربر.
    خروجی: (success, message, amount)
    اگر کد اصلاً وجود نداشته باشد، (False, None, None) برمی‌گردد تا پیامی
    برای پیام‌های عادی گروه که تصادفاً شبیه کد هستند نمایش داده نشود.
    """
    with _gift_code_lock:
        try:
            gift = get_gift_code(code)
            if not gift:
                return False, None, None

            already = (
                supabase.table("gift_code_redemptions")
                .select("id").eq("code", code).eq("user_id", user_id).execute()
            )
            if already.data:
                return False, "⚠️ شما قبلاً این کد هدیه را فعال کرده‌اید.", None

            if int(gift.get("redeemed_count", 0) or 0) >= int(gift.get("capacity", 0) or 0):
                supabase.table("gift_codes").delete().eq("code", code).execute()
                return False, "❌ ظرفیت این کد هدیه تکمیل شده است.", None

            amount = int(gift.get("amount", 0) or 0)
            supabase.table("gift_code_redemptions").insert({
                "code": code,
                "user_id": user_id,
            }).execute()
            new_redeemed = int(gift.get("redeemed_count", 0) or 0) + 1
            supabase.table("gift_codes").update({"redeemed_count": new_redeemed}).eq("code", code).execute()
            update_diamonds(user_id, amount)

            if new_redeemed >= int(gift.get("capacity", 0) or 0):
                supabase.table("gift_codes").delete().eq("code", code).execute()

            return True, "✅", amount
        except Exception as e:
            logging.error(f"خطا در redeem_gift_code: {e}")
            return False, "❌ خطایی رخ داد.", None


# ================== دکمه‌ها و توابع کمکی ==================
MAIN_MENU_CAPTION = (
    '<tg-emoji emoji-id="6028360176191411591">🌹</tg-emoji> به بات الماسی خوش آمدید.\n\n'
    '<tg-emoji emoji-id="5889002570633977838">🤖</tg-emoji> من رو تو گروهتون اد کنید و الماس جمع کنید.\n\n'
    '<tg-emoji emoji-id="5825623013852192219">💎</tg-emoji> با گفتن کلمه «ماین» تو گروه الماس رایگان بگیرید.'
)


def main_menu_markup(user_id=None):
    # منوی اصلی فقط یک دکمه دارد: افزودن بات به گروه.
    markup = types.InlineKeyboardMarkup()
    markup.row(colored_button(
        "افزودن من به گروهتون",
        url="https://t.me/FreePremiiumBot?startgroup=true",
        icon_custom_emoji_id="5938185469501116312",
    ))
    return markup


def account_menu_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(colored_button("لقب‌ها", callback_data=f"showtags|{user_id}", icon_custom_emoji_id="5937997075055644696"))
    markup.row(colored_button("آمار من", callback_data=f"showstats|{user_id}", icon_custom_emoji_id="5938072524746137114"))
    return markup

# ================== سیستم پنل‌های تک‌پیامی ==================
# هر بخش یک پیام پنل دارد و تمام تغییرات همان پیام را ویرایش می‌کنند.
# برای دوام بعد از ری‌استارت، message_id پنل در جدول bot_panels ذخیره می‌شود.
_panel_cache = {}
_panel_cache_lock = threading.Lock()


def _panel_key(chat_id, user_id, panel):
    return (int(chat_id), int(user_id), str(panel))


def get_saved_panel_message_id(chat_id, user_id, panel):
    key = _panel_key(chat_id, user_id, panel)
    with _panel_cache_lock:
        cached = _panel_cache.get(key)
    if cached:
        return cached
    try:
        res = supabase.table("bot_panels").select("message_id").eq("chat_id", chat_id).eq("user_id", user_id).eq("panel_key", panel).limit(1).execute()
        if res.data:
            mid = int(res.data[0]["message_id"])
            with _panel_cache_lock:
                _panel_cache[key] = mid
            return mid
    except Exception as e:
        logging.warning(f"bot_panels خوانده نشد (ممکنه migration هنوز اجرا نشده باشه): {e}")
    return None


def save_panel_message_id(chat_id, user_id, panel, message_id):
    key = _panel_key(chat_id, user_id, panel)
    with _panel_cache_lock:
        _panel_cache[key] = int(message_id)
    try:
        supabase.table("bot_panels").upsert({
            "chat_id": int(chat_id),
            "user_id": int(user_id),
            "panel_key": str(panel),
            "message_id": int(message_id),
        }, on_conflict="chat_id,user_id,panel_key").execute()
    except Exception as e:
        logging.warning(f"bot_panels ذخیره نشد: {e}")


def show_or_edit_panel(message, user_id, panel, text, reply_markup=None, parse_mode=None):
    """برای ورودی متنی همیشه پنل جدید بساز و همیشه روی همون پیامی که کاربر فرستاده
    ریپلای بزن. این باعث می‌شه وقتی چند نفر هم‌زمان یک دستور مشابه می‌فرستن (مثلاً
    «موجودی»)، مشخص باشه هر پاسخ برای کدوم پیام/کاربره و موجودی‌ها با هم قاطی نشه.
    پنل قبلی دست‌نخورده بماند؛ ادیت پنل فقط در callback handlerهای دکمه‌ها انجام می‌شود.
    """
    chat_id = message.chat.id
    sent = safe_send_message(
        chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode,
        reply_to_message_id=message.message_id,
    )
    if sent:
        save_panel_message_id(chat_id, user_id, panel, sent.message_id)
        return sent.message_id
    return None


def get_pending_prompt_message_id(chat_id, user_id):
    with _next_step_lock:
        state = _next_step_pending.get((chat_id, user_id))
        return state.get("prompt_message_id") if state else None


def is_reply_to_panel(message, panel_message_id):
    """قبلاً ورودی مرحله‌ای فقط با ریپلای مستقیم روی پنل معتبر بود؛ همین باعث
    می‌شد کاربرهایی که فقط عدد رو تایپ می‌کردن (بدون ریپلای کردن)، پیامشون
    نادیده گرفته بشه (باگ «تو بانک واریز/برداشت نمیشه»). چون هر مرحله
    الان بر اساس (چت، کاربر) جدا نگه داشته می‌شه، دیگه نیازی به ریپلای برای
    تشخیص هویت نیست؛ فقط اگر کاربر عمداً به پیام دیگه‌ای (غیر از پنل) ریپلای
    کرده باشه، اون رو نامعتبر می‌دونیم."""
    reply = getattr(message, "reply_to_message", None)
    if not reply or panel_message_id is None:
        return True
    if reply.message_id != int(panel_message_id):
        return False
    sender = getattr(reply, "from_user", None)
    return bool(sender and getattr(sender, "is_bot", False))


def require_reply_to_panel(message, panel_message_id, instruction="لطفاً روی همین پیام پنل ریپلای کن و مقدار را بفرست."):
    if is_reply_to_panel(message, panel_message_id):
        return True
    # پیام جدیدی نمی‌فرستیم؛ همان پنل را راهنمایی می‌کنیم.
    if panel_message_id:
        text = instruction if "<tg-emoji" in instruction else f"↩️ {instruction}"
        safe_edit_message(text, message.chat.id, panel_message_id, reply_markup=None)
    return False

def safe_edit_message(text, chat_id, message_id, reply_markup=None, parse_mode=None, retries=2):
    """Reliable edit: suppress only a known-successful duplicate; never drop a new edit."""
    if isinstance(text, str) and "<tg-emoji" in text and parse_mode is None:
        parse_mode = "HTML"
    key = _edit_cache_key(chat_id, message_id, "text")
    fingerprint = _edit_fingerprint({
        "text": _force_rtl(text),
        "reply_markup": reply_markup,
        "parse_mode": parse_mode,
    })

    with _get_telegram_edit_lock(chat_id, message_id):
        if _edit_success_cache.get(key) == fingerprint:
            return True

        network_attempts = 0
        while True:
            try:
                _wait_for_telegram_slot(chat_id)
                _original_edit_message_text(
                    _force_rtl(text),
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                _remember_edit_success(key, fingerprint)
                return True
            except Exception as exc:
                if _is_message_not_modified(exc):
                    _remember_edit_success(key, fingerprint)
                    return True
                if _is_permanent_message_error(exc):
                    logging.warning("پیام %s قابل ویرایش نیست: %s", message_id, exc)
                    return False
                if _is_rate_limit_error(exc):
                    retry_after = _extract_retry_after(exc) or 5.0
                    # This call itself owns the lock; sleeping here guarantees
                    # no other edit can overtake this edit.
                    wait_seconds = retry_after + _TELEGRAM_429_SAFETY_MARGIN
                    logging.warning(
                        "Telegram rate limit هنگام ویرایش پیام %s؛ %.1f ثانیه صبر می‌کنیم.",
                        message_id, wait_seconds
                    )
                    time.sleep(wait_seconds)
                    continue
                network_attempts += 1
                if network_attempts <= retries:
                    time.sleep(min(0.5 * network_attempts, 2.0))
                    continue
                logging.error("خطا در ویرایش پیام %s: %s", message_id, exc)
                return False


def safe_edit_message_reply_markup(chat_id, message_id, reply_markup=None, retries=2):
    key = _edit_cache_key(chat_id, message_id, "markup")
    fingerprint = _edit_fingerprint(reply_markup)
    with _get_telegram_edit_lock(chat_id, message_id):
        if _edit_success_cache.get(key) == fingerprint:
            return True
        network_attempts = 0
        while True:
            try:
                _wait_for_telegram_slot(chat_id)
                _original_edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup,
                )
                _remember_edit_success(key, fingerprint)
                return True
            except Exception as exc:
                if _is_message_not_modified(exc):
                    _remember_edit_success(key, fingerprint)
                    return True
                if _is_permanent_message_error(exc):
                    logging.warning("کیبورد پیام %s قابل ویرایش نیست: %s", message_id, exc)
                    return False
                if _is_rate_limit_error(exc):
                    retry_after = _extract_retry_after(exc) or 5.0
                    wait_seconds = retry_after + _TELEGRAM_429_SAFETY_MARGIN
                    logging.warning(
                        "Telegram rate limit هنگام ویرایش کیبورد پیام %s؛ %.1f ثانیه صبر می‌کنیم.",
                        message_id, wait_seconds
                    )
                    time.sleep(wait_seconds)
                    continue
                network_attempts += 1
                if network_attempts <= retries:
                    time.sleep(min(0.5 * network_attempts, 2.0))
                    continue
                logging.error("خطا در ویرایش کیبورد پیام %s: %s", message_id, exc)
                return False


def safe_send_message(chat_id, text, reply_markup=None, parse_mode=None, reply_to_message_id=None):
    """Send without unsafe duplicate fallback; 429 is retried by the API gate."""
    try:
        return bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as exc:
        # Only remove reply_to when Telegram explicitly says the replied-to
        # message is unavailable. Never resend after an arbitrary exception.
        err = str(exc).lower()
        logging.error("خطا در ارسال پیام به chat_id=%s: %s", chat_id, exc)
        if reply_to_message_id is not None and (
            "reply message not found" in err or
            "message to reply not found" in err or
            "message identifier is not specified" in err
        ):
            try:
                logging.warning(
                    "ریپلای به پیام %s ممکن نبود؛ همان پیام بدون ریپلای ارسال می‌شود.",
                    reply_to_message_id,
                )
                return bot.send_message(
                    chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode
                )
            except Exception as exc2:
                logging.error("خطا در ارسال پیام بدون ریپلای: %s", exc2)
                return None
        logging.error("خطا در ارسال پیام: %s", exc)
        return None

def _auto_edit_after_delay(chat_id, message_id, delay, text, reply_markup, parse_mode=None):
    """بعد از delay ثانیه، پیام نتیجه را به پنل مربوطه برمی‌گرداند."""
    def _job():
        try:
            safe_edit_message(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logging.error(f"خطا در برگشت خودکار پنل: {e}")
    timer = threading.Timer(delay, _job)
    timer.daemon = True
    timer.start()


def auto_return_to_main(chat_id, message_id, user_id, delay=5):
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        MAIN_MENU_CAPTION,
        main_menu_markup(user_id),
        parse_mode="HTML"
    )


def auto_return_to_bank(chat_id, message_id, user_id, delay=5):
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        bank_text(user_id),
        bank_markup(user_id),
        parse_mode="HTML"
    )


def auto_return_to_casino(chat_id, message_id, user_id, delay=5):
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        CASINO_MENU_TEXT,
        casino_games_keyboard(user_id),
        parse_mode="HTML"
    )

def extract_owner_id(call_data):
    """از callback_data شناسه صاحب پنل را استخراج می‌کند. همیشه آخرین بخش
    جدا‌شده با '|' است (پشتیبانی از فرمت‌های چندبخشی مثل cbet|game|amount|owner)."""
    try:
        return int(call_data.split("|")[-1])
    except (IndexError, ValueError):
        return None

def check_panel_owner(call):
    """اگر کاربری غیر از صاحب پنل روی دکمه بزند، بی‌صدا کلیک را نادیده می‌گیرد
    (بدون هیچ پیام popup) و None برمی‌گرداند. این جلوی سوءاستفاده‌ای را می‌گیرد
    که کاربر دیگری در گروه بتواند پنل شخصی شخص دیگر را با کلیک، تغییر بدهد
    یا از آن استفاده کند."""
    owner_id = extract_owner_id(call.data)
    if owner_id is None or call.from_user.id != owner_id:
        bot.answer_callback_query(call.id)  # فقط ack خالی؛ هیچ اتفاقی نمی‌افتد
        return None
    return owner_id

def build_account_text(user_id, user, tg_user):
    diamonds = user.get('diamonds', 0)
    ring_diamonds = user.get('ring_diamonds', 0) or 0
    progress = get_progress(user_id)
    level = int(progress.get("level", 1) or 1)
    xp = int(progress.get("xp", 0) or 0)
    _, title, _need, _reward = level_info(level)
    next_need = xp_needed_for_next_level(level)
    xp_line = f"{xp} / {next_need}" if next_need is not None else f"{xp} (حداکثر سطح)"

    # نام کاربر باید برای HTML escape شود؛ ایموجی گیفت (tg-emoji) دست‌نخورده می‌ماند.
    name = html.escape((getattr(tg_user, "first_name", None) or "کاربر").strip())
    try:
        g = display_gift_for_user(user_id)
        if g:
            name = f"{g} {name}"
    except Exception:
        pass

    return (
        '<tg-emoji emoji-id="5938508592775696902">👤</tg-emoji> پروفایل شما\n\n'
        f'<tg-emoji emoji-id="5843973755545590553">👤</tg-emoji> نام: {name}\n'
        f'<tg-emoji emoji-id="5938398023137628324">🆔</tg-emoji> آیدی عددی : {user_id}\n'
        f'<tg-emoji emoji-id="6028360176191411591">💎</tg-emoji> موجودی الماس : {int(diamonds or 0):,}\n'
        f'<tg-emoji emoji-id="5805686638952584277">💍</tg-emoji> الماس انگشتر: {int(ring_diamonds or 0):,}\n'
        f'<tg-emoji emoji-id="5938515112536051725">⭐</tg-emoji> سطح : {level} ({html.escape(str(title))})\n'
        f'<tg-emoji emoji-id="5897595485933280320">🏆</tg-emoji> امتیاز : {xp_line}'
    )


# ================== هندلر start ==================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_id = message.from_user.id
    username = get_display_name(message.from_user)
    is_new = not get_user(user_id)

    if is_new:
        create_user(user_id, username)

    safe_send_message(message.chat.id, MAIN_MENU_CAPTION, reply_markup=main_menu_markup(user_id), parse_mode="HTML", reply_to_message_id=message.message_id)

# ================== کالبک منوی اصلی ==================
@bot.callback_query_handler(func=lambda call: call.data == "mainmenu" or call.data.startswith("mainmenu|"))
def main_menu(call):
    # اگه این دکمه داخل یه پنل شخصی (مثل بانک، حساب، رفرال، راهنما) قرار
    # داشته باشه، شناسه صاحبش رو حمل می‌کنه؛ در این حالت فقط خودش می‌تونه بزنتش
    # و کلیک بقیه بی‌صدا نادیده گرفته میشه. روی پیام‌های عمومی/گروهی (نتیجه شرط،
    # رتبه‌بندی و ...) این دکمه owner نداره و برای همه باز می‌مونه.
    if "|" in call.data:
        clicker_id = check_panel_owner(call)
        if clicker_id is None:
            return
    else:
        clicker_id = call.from_user.id
    bot.answer_callback_query(call.id)

    # فقط clear_step_handler کافی نیست؛ ما یک سیستم next-step سفارشی هم داریم.
    # اگر کاربر وسط برداشت/واریز «منوی اصلی» را بزند، باید آن مرحله هم
    # همان لحظه لغو شود تا پیام بعدی او دوباره توسط مرحله‌ی قدیمی بلعیده نشود.
    cancel_pending_step(call.message.chat.id, clicker_id)
    bot.clear_step_handler(call.message)

    caption = MAIN_MENU_CAPTION
    # همون پیام قبلی ویرایش میشه (نه پیام جدید). این کار امنه چون بالاتر مالکیت
    # پنل‌های شخصی چک شده؛ فقط اگه ویرایش به هر دلیلی (مثلاً پیام اصلی عکس بود)
    # شکست بخوره، به‌عنوان فالبک یه پیام تازه فرستاده میشه.
    edited = safe_edit_message(caption, call.message.chat.id, call.message.message_id, main_menu_markup(clicker_id))
    if not edited:
        safe_send_message(call.message.chat.id, caption, reply_markup=main_menu_markup(clicker_id), parse_mode="HTML")

# ================== دستور /account ==================
@bot.message_handler(commands=["account"])
def cmd_account(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.reply_to(message, "اول /start بزن.")
        return

    text = build_account_text(user_id, user, message.from_user)
    show_or_edit_panel(message, user_id, "account", text, account_menu_markup(user_id), parse_mode="HTML")

# دسترسی متنی منوی اصلی
@bot.message_handler(func=lambda m: text_is(m, "حساب کاربری"))
def text_account(message):
    cmd_account(message)

@bot.message_handler(func=lambda m: text_is(m, "راهنما", "کمک"))
def text_help(message):
    show_or_edit_panel(message, message.from_user.id, "help", HELP_MENU_TEXT, help_main_markup(message.from_user.id), parse_mode="HTML")

# ================== بخش حساب کاربری ==================
@bot.callback_query_handler(func=lambda call: call.data == "showaccount" or call.data.startswith("showaccount|"))
def handle_show_account(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    text = build_account_text(user_id, user, call.from_user)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=account_menu_markup(user_id), parse_mode="HTML")

# ================== بخش آمار من ==================
@bot.callback_query_handler(func=lambda call: call.data == "showstats" or call.data.startswith("showstats|"))
def handle_show_stats(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    markup = types.InlineKeyboardMarkup()
    markup.row(back_button(callback_data=f"showaccount|{user_id}"))
    safe_edit_message(
        stats_text_for_user(user_id, get_display_name(call.from_user)),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# ================== بخش لقب‌های من ==================
def tags_menu_markup(user_id, tags, active_name):
    markup = types.InlineKeyboardMarkup()
    for i, entry in enumerate(tags):
        name = entry.get("name", "")
        label = f"✅ {name}" if name == active_name else name
        markup.row(
            colored_button(label, callback_data=f"selecttag|{user_id}|{i}"),
            colored_button(_format_remaining(entry.get("expires") or 0), callback_data=f"tagtime|{user_id}|{i}")
        )
    markup.row(back_button(callback_data=f"showaccount|{user_id}"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == "showtags" or call.data.startswith("showtags|"))
def handle_show_tags(call):
    if _feature_gate(call, "tags"):
        return
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    tags, active_name = get_user_tags(user_id)
    if not tags:
        text = "🏷 لقب‌ها\n\nهنوز هیچ لقب ویژه‌ای نگرفتی!"
        markup = types.InlineKeyboardMarkup()
        markup.row(back_button(callback_data=f"showaccount|{user_id}"))
    else:
        text = "🏷 لقب‌های شما\n\nروی یه لقب بزن تا به‌عنوان لقب نمایشی فعالت انتخاب بشه:"
        markup = tags_menu_markup(user_id, tags, active_name)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("selecttag|"))
def handle_select_tag(call):
    if _feature_gate(call, "tags"):
        return
    _, owner_id_str, idx_str = call.data.split("|")
    owner_id = int(owner_id_str)
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    ok, result = set_active_tag(owner_id, int(idx_str))
    if not ok:
        bot.answer_callback_query(call.id, f"❌ {result}", show_alert=True)
        return
    bot.answer_callback_query(call.id, f"✅ لقب فعال شد: {result}")
    tags, active_name = get_user_tags(owner_id)
    safe_edit_message(
        "🏷 لقب‌های شما\n\nروی یه لقب بزن تا به‌عنوان لقب نمایشی فعالت انتخاب بشه:",
        call.message.chat.id, call.message.message_id,
        reply_markup=tags_menu_markup(owner_id, tags, active_name)
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("tagtime|"))
def handle_tag_time(call):
    if _feature_gate(call, "tags"):
        return
    _, owner_id_str, idx_str = call.data.split("|")
    owner_id = int(owner_id_str)
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    tags, _ = get_user_tags(owner_id)
    idx = int(idx_str)
    if idx < 0 or idx >= len(tags):
        bot.answer_callback_query(call.id, "لقب پیدا نشد.", show_alert=True)
        return
    entry = tags[idx]
    bot.answer_callback_query(call.id, f"{entry.get('name')}\n{_format_remaining(entry.get('expires') or 0)}", show_alert=True)

# ================== بخش زیرمجموعه ==================
# ================== بخش بانک ==================
# نکته امنیتی: تمام دکمه‌های زیر شناسه صاحب پنل را داخل callback_data حمل می‌کنند
# (owner|user_id) تا کاربر دیگری در گروه نتواند با زدن دکمه، پنل شخص دیگر را
# به پنل خودش تغییر بدهد. هندلرهای مربوطه این شناسه را با کاربری که کلیک کرده
# مقایسه می‌کنند (به همان الگوی بقیه پنل‌ها).
def bank_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("کارت به کارت", callback_data=f"bankcard|{user_id}", icon_custom_emoji_id="5888625992196431593")
    )
    markup.row(
        colored_button("واریز", callback_data=f"bankdeposit|{user_id}", icon_custom_emoji_id="5938558852482994666"),
        colored_button("برداشت", callback_data=f"bankwithdraw|{user_id}", icon_custom_emoji_id="5936151695112278046")
    )
    markup.row(
        colored_button("تراکنش ها", callback_data=f"banktx|{user_id}", icon_custom_emoji_id="5888937012253171131")
    )
    return markup


def bank_back_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(back_button(callback_data=f"bankmenu|{user_id}"))
    return markup

def bank_open_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(colored_button("افتتاح حساب", callback_data=f"bankopen|{user_id}"))
    return markup

def _bank_err(text):
    return f'{text} {_gift_emoji(5938290000415167172, "❌")}'


def bank_open_text(user_id):
    e = _gift_emoji
    return (
        f'{e(5886285995229323603, "🏦")} بانک الماس\n\n'
        f'برای اولین بار باید حساب بانکی خودت رو با پرداخت {BANK_OPENING_FEE:,} {e(5940725397195853882, "💎")} افتتاح کنی.\n\n'
        f'{e(5823445637231814311, "💰")} موجودی فعلی: {get_balance(user_id):,} {e(5940725397195853882, "💎")}'
    )

def bank_text(user_id):
    apply_bank_interest(user_id)
    user = get_user(user_id)
    bank_balance = get_bank_balance(user_id)
    account_number = user.get("bank_account_number")
    display_name = html.escape(str(user.get('username') or user_id))
    today_interest = min(int(bank_balance * BANK_INTEREST_RATE), BANK_DAILY_INTEREST_MAX)
    e = _gift_emoji
    rate_emoji = e(5938489471581293873, "📈")
    return (
        f'{e(5886285995229323603, "🏦")} بانک الماس\n\n'
        f'{e(5888625992196431593, "💳")} شماره حساب : <code>{account_number}</code>\n'
        f'{e(5938272154826052187, "👤")} به نام : {display_name}\n\n'
        f'{e(5823445637231814311, "💰")} موجودی حساب : {bank_balance:,} الماس {e(5940725397195853882, "💎")}\n\n'
        " سود بانکی\n"
        f'┘─ {rate_emoji} درصد سود : {BANK_INTEREST_RATE * 100:g}% {rate_emoji}\n'
        f'┘─ {e(5889002570633977838, "💸")} سود روزانه : {today_interest:,} الماس {rate_emoji}\n'
        f'┘─  {e(5818984798298841943, "⏰")} زمان واریز : 00:00\n\n'
        f'{e(5938167138580741203, "ℹ️")} برای مدیریت حساب بانکی از گزینه های زیر استفاده کنید. {e(5820903824046432799, "👇")}'
    )

@bot.callback_query_handler(func=lambda call: call.data == "bankmenu" or call.data.startswith("bankmenu|"))
def bank_menu(call):
    if _feature_gate(call, "bank"):
        return
    # سازگاری با دکمه‌ای که owner ندارد (مثلاً وقتی از منوی اصلی بدون owner ساخته شده)
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)

    # ورود به بانک یک navigation action است؛ اگر مرحله‌ی قبلی (برداشت/واریز)
    # هنوز فعال باشد، باید آن را ببندیم.
    cancel_pending_step(call.message.chat.id, user_id)
    bot.clear_step_handler(call.message)

    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    if not user.get("bank_account_number"):
        safe_edit_message(
            bank_open_text(user_id),
            call.message.chat.id, call.message.message_id, bank_open_markup(user_id), parse_mode="HTML"
        )
        return
    safe_edit_message(bank_text(user_id), call.message.chat.id, call.message.message_id, bank_markup(user_id), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankopen|"))
def bank_open(call):
    if _feature_gate(call, "bank"):
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    ok, result = open_bank_account(user_id)
    if not ok:
        safe_edit_message(f"❌ {result}", call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    safe_edit_message(
        f'حساب بانکی با موفقیت افتتاح شد! {_gift_emoji(5938109560249127910, "✅")}\n\n' + bank_text(user_id),
        call.message.chat.id, call.message.message_id, bank_markup(user_id), parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankdeposit|"))
def bank_deposit_prompt(call):
    if _feature_gate(call, "bank"):
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    if not get_bank_account_number(user_id):
        safe_edit_message(_bank_err("اول حساب بانکی خودت رو افتتاح کن."), call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    safe_edit_message(
        f'{_gift_emoji(5938558852482994666, "📥")} واریز به بانک الماس\n\n'
        f'{_gift_emoji(5823445637231814311, "💰")} موجودی حساب : {get_bank_balance(user_id):,} الماس {_gift_emoji(5940725397195853882, "💎")}\n\n'
        f'{_gift_emoji(5938290532991111041, "ℹ️")} شما درحال واریز الماس به حساب بانکی خود میباشید.\n\n'
        f'{_gift_emoji(5938311423712039050, "✍️")} لطفا مبلغ مورد نظر جهت واریز رو در جواب همین پنل ارسال کنید.',
        call.message.chat.id, call.message.message_id, bank_back_markup(user_id), parse_mode="HTML"
    )
    register_timed_next_step_handler(call.message, bank_deposit_step, user_id, call.message.message_id, expected_user_id=user_id)

def _bank_confirmation_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("تایید", callback_data=f"bankconfirm|yes|{user_id}", icon_custom_emoji_id="5938109560249127910"),
        colored_button("لغو", callback_data=f"bankconfirm|no|{user_id}", icon_custom_emoji_id="5938290000415167172"),
    )
    return markup

def _store_bank_confirmation(user_id, action, amount, chat_id, message_id):
    with _bank_confirm_lock:
        _bank_confirmations[user_id] = {"action": action, "amount": int(amount), "chat_id": chat_id, "message_id": message_id}

def _clear_bank_confirmation(user_id):
    with _bank_confirm_lock:
        return _bank_confirmations.pop(user_id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankconfirm|"))
def bank_confirmation_callback(call):
    parts = call.data.split("|")
    if len(parts) != 3:
        bot.answer_callback_query(call.id)
        return
    try:
        user_id = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id)
        return
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id)
        return
    with _bank_confirm_lock:
        pending = _bank_confirmations.get(user_id)
    if not pending:
        bot.answer_callback_query(call.id)
        return
    action, amount = pending["action"], int(pending["amount"])
    if parts[1] == "no":
        _clear_bank_confirmation(user_id)
        bot.answer_callback_query(call.id)
        safe_edit_message(_bank_err("عملیات لغو شد"), call.message.chat.id, call.message.message_id, reply_markup=None)
        auto_return_to_bank(call.message.chat.id, call.message.message_id, user_id, 3)
        return
    if parts[1] != "yes":
        bot.answer_callback_query(call.id)
        return

    if action == "deposit":
        # هر دو leg واریز زیر یک lock هستند. اگر ثبت در بانک شکست بخورد،
        # الماسِ کسرشده از کیف پول با عملیات جبرانی برگردانده می‌شود.
        with _get_user_financial_lock(user_id):
            apply_bank_interest(user_id)
            if get_balance(user_id) < amount:
                ok = False
            elif update_diamonds(user_id, -amount) is None:
                ok = False
            elif change_bank_balance(user_id, amount):
                ok = True
            else:
                if update_diamonds(user_id, amount) is None:
                    logging.critical(
                        f"ROLLBACK FAILED: bank deposit user={user_id} amount={amount}"
                    )
                ok = False
        if not ok:
            _clear_bank_confirmation(user_id)
            bot.answer_callback_query(call.id)
            safe_edit_message(_bank_err("ثبت واریز انجام نشد و موجودی شما تغییر نکرد"), call.message.chat.id, call.message.message_id, reply_markup=None)
            auto_return_to_bank(call.message.chat.id, call.message.message_id, user_id, 3)
            return
        result_text = (f'تعداد {amount:,} الماس {_gift_emoji(5940725397195853882, "💎")} به حساب بانکی واریز شد. {_gift_emoji(5938109560249127910, "✅")}\n\n'
                       f'{_gift_emoji(5823445637231814311, "💰")} موجودی بانک: {get_bank_balance(user_id):,} {_gift_emoji(5940725397195853882, "💎")}')
    else:
        # هر دو leg انتقال زیر یک lock هستند. اگر leg دوم شکست بخورد،
        # leg اول با عملیات جبرانی برگردانده می‌شود.
        with _get_user_financial_lock(user_id):
            apply_bank_interest(user_id)
            if get_bank_balance(user_id) < amount:
                ok = False
            else:
                bank_debited = change_bank_balance(user_id, -amount)
                wallet_credited = False
                if bank_debited:
                    wallet_credited = update_diamonds(user_id, amount) is not None
                if bank_debited and not wallet_credited:
                    # rollback: پول بانک را برگردان.
                    rollback_ok = change_bank_balance(user_id, amount)
                    if not rollback_ok:
                        logging.critical(
                            f"ROLLBACK FAILED: bank withdraw user={user_id} amount={amount}"
                        )
                    ok = False
                else:
                    ok = bool(bank_debited and wallet_credited)
        if not ok:
            _clear_bank_confirmation(user_id)
            bot.answer_callback_query(call.id)
            safe_edit_message(_bank_err("ثبت برداشت انجام نشد و موجودی شما تغییر نکرد"), call.message.chat.id, call.message.message_id, reply_markup=None)
            auto_return_to_bank(call.message.chat.id, call.message.message_id, user_id, 3)
            return
        result_text = (f'تعداد {amount:,} الماس {_gift_emoji(5940725397195853882, "💎")} از حساب بانکی برداشت شد. {_gift_emoji(5938109560249127910, "✅")}\n\n'
                       f'{_gift_emoji(5823445637231814311, "💰")} موجودی بانک: {get_bank_balance(user_id):,} {_gift_emoji(5940725397195853882, "💎")}')
    _clear_bank_confirmation(user_id)
    _record_bank_tx(user_id, action, amount)
    bot.answer_callback_query(call.id)
    safe_edit_message(result_text, call.message.chat.id, call.message.message_id, reply_markup=None)
    auto_return_to_bank(call.message.chat.id, call.message.message_id, user_id, 5)

def bank_deposit_step(message, expected_user_id, panel_msg_id):
    if not require_reply_to_panel(message, panel_msg_id, "برای واریز، روی همین پنل بانک ریپلای کن و مبلغ را بفرست."):
        register_timed_next_step_handler(message, bank_deposit_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    if message.from_user.id != expected_user_id:
        register_timed_next_step_handler(message, bank_deposit_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    amount = parse_amount(message.text)
    if amount is None:
        safe_edit_message(_bank_err("فرمت پیام درست نیست روی همین پیام ریپلای کن. مثال: 5000 یا 5کا."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        register_timed_next_step_handler(message, bank_deposit_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    if amount <= 0:
        safe_edit_message(_bank_err("مبلغ باید بیشتر از صفر باشه مشتی روی همین پیام ریپلای کن و دوباره عدد بفرست."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        register_timed_next_step_handler(message, bank_deposit_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    if get_balance(expected_user_id) < amount:
        safe_edit_message(_bank_err("موجودی الماس شما کافی نیست."), message.chat.id, panel_msg_id, reply_markup=bank_markup(expected_user_id))
        return
    safe_edit_message(f'{_gift_emoji(5938558852482994666, "📥")} آیا از واریز اطمینان دارید؟\n\nمبلغ درحال واریز: {amount:,} {_gift_emoji(5940725397195853882, "💎")}', message.chat.id, panel_msg_id, reply_markup=_bank_confirmation_markup(expected_user_id))
    _store_bank_confirmation(expected_user_id, "deposit", amount, message.chat.id, panel_msg_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankwithdraw|"))
def bank_withdraw_prompt(call):
    if _feature_gate(call, "bank"):
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    if not get_bank_account_number(user_id):
        safe_edit_message(_bank_err("اول حساب بانکی خودت رو افتتاح کن."), call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    apply_bank_interest(user_id)
    safe_edit_message(
        f'{_gift_emoji(5936151695112278046, "📤")} برداشت از بانک الماس\n\n'
        f'{_gift_emoji(5823445637231814311, "💰")} موجودی قابل برداشت : {get_bank_balance(user_id):,} الماس {_gift_emoji(5938489471581293873, "📈")}\n\n'
        f'{_gift_emoji(5936151695112278046, "📤")} شما درحال برداشت الماس از حساب بانکی خود میباشید.\n\n'
        f'{_gift_emoji(5938348905891632091, "✍️")} لطفا مبلغ مورد نظر جهت برداشت رو در جواب همین پنل ارسال کنید.',
        call.message.chat.id, call.message.message_id, bank_back_markup(user_id), parse_mode="HTML"
    )
    register_timed_next_step_handler(call.message, bank_withdraw_step, user_id, call.message.message_id, expected_user_id=user_id)

def bank_withdraw_step(message, expected_user_id, panel_msg_id):
    if not require_reply_to_panel(message, panel_msg_id, "برای برداشت، روی همین پنل بانک ریپلای کن و مبلغ را بفرست."):
        register_timed_next_step_handler(message, bank_withdraw_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    if message.from_user.id != expected_user_id:
        register_timed_next_step_handler(message, bank_withdraw_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    amount = parse_amount(message.text)
    if amount is None:
        safe_edit_message(_bank_err("فرمت پیام درست نیست روی همین پیام ریپلای کن. مثال: 5000 یا 5کا."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        register_timed_next_step_handler(message, bank_withdraw_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    if amount <= 0:
        safe_edit_message(_bank_err("مبلغ باید بیشتر از صفر باشه مشتی روی همین پیام ریپلای کن و دوباره عدد بفرست."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        register_timed_next_step_handler(message, bank_withdraw_step, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    apply_bank_interest(expected_user_id)
    if get_bank_balance(expected_user_id) < amount:
        safe_edit_message(_bank_err("موجودی بانک برای این برداشت کافی نیست."), message.chat.id, panel_msg_id, reply_markup=bank_markup(expected_user_id))
        return
    safe_edit_message(f'{_gift_emoji(5936151695112278046, "📤")} آیا از برداشت اطمینان دارید؟\n\nمبلغ درحال برداشت: {amount:,} {_gift_emoji(5940725397195853882, "💎")}', message.chat.id, panel_msg_id, reply_markup=_bank_confirmation_markup(expected_user_id))
    _store_bank_confirmation(expected_user_id, "withdraw", amount, message.chat.id, panel_msg_id)


# ================== کارت به کارت و تراکنش‌های بانک ==================
# جدول تراکنش‌ها (یک‌بار تو Supabase بساز):
#   create table if not exists bank_transactions (
#     id bigserial primary key, user_id bigint not null, kind text not null,
#     amount bigint not null, fee bigint not null default 0, target_name text,
#     created_at timestamptz not null default now());
#   create index if not exists bank_transactions_user_idx on bank_transactions (user_id, id desc);
# اگه جدول نباشه فقط تاریخچه ثبت نمی‌شه؛ خودِ کارت به کارت/واریز/برداشت درست کار می‌کنه.
_card_confirmations = {}   # user_id -> {target_id, account, name, amount, fee, chat_id, message_id}
_card_confirm_lock = threading.Lock()

class _PanelStub:
    """شبیه پیام پنل (فرستنده‌اش بات)؛ برای این‌که مرحله‌ی بعدی هم فقط با ریپلای روی همین پنل پذیرفته بشه."""
    def __init__(self, chat, message_id):
        self.chat = chat
        self.message_id = message_id
        self.from_user = type("_BotUser", (), {"id": 0, "is_bot": True})()

def _card_fee(amount):
    rate = float(BANK_CARD_FEE_RATE or 0)
    if rate <= 0 or amount <= 0:
        return 0
    return max(1, int(math.ceil(round(amount * rate, 6))))

def _record_bank_tx(user_id, kind, amount, target_name=None, fee=0):
    """ثبت تراکنش در پس‌زمینه تا پاسخ ربات کند نشه؛ خطا فقط لاگ می‌شه."""
    row = {"user_id": int(user_id), "kind": kind, "amount": int(amount), "fee": int(fee or 0), "target_name": target_name}
    def _job():
        try:
            supabase.table("bank_transactions").insert(row).execute()
        except Exception as ex:
            logging.error(f"ثبت تراکنش بانک ناموفق بود (جدول bank_transactions ساخته نشده؟): {ex}")
    threading.Thread(target=_job, daemon=True).start()

def _recent_bank_tx(user_id, limit=5):
    try:
        res = _run_db_with_retry(
            lambda: supabase.table("bank_transactions").select("kind,amount,target_name")
            .eq("user_id", int(user_id)).order("id", desc=True).limit(limit).execute(),
            label=f"bank_tx({user_id})",
        )
        return res.data or []
    except Exception as ex:
        logging.error(f"خواندن تراکنش‌های بانک ناموفق بود: {ex}")
        return []

def _bank_tx_text(user_id):
    e = _gift_emoji
    rows = _recent_bank_tx(user_id, 5)
    head = f'تراکنش های اخیر شما{e(5820903824046432799, "👇")}'
    if not rows:
        return head + "\n\nهنوز تراکنشی انجام نشده."
    blocks = []
    for r in rows:
        amount = int(r.get("amount") or 0)
        kind = r.get("kind")
        amount_line = f'\u200f┘─ مبلغ: {amount:,}{e(5940725397195853882, "💎")}'
        if kind == "card":
            name = html_escape_keep_emoji(r.get("target_name") or "کاربر")
            blocks.append(
                f'کارت به کارت{e(5888625992196431593, "💳")}\n'
                f'\u200f┘─ به نام: {name}{e(5843911663203392756, "👤")}\n'
                f'{amount_line}'
            )
        elif kind == "withdraw":
            blocks.append(f'برداشت{e(5936151695112278046, "📤")}\n{amount_line}')
        else:
            blocks.append(f'واریز{e(5938558852482994666, "📥")}\n{amount_line}')
    return head + "\n\n" + "\n\n".join(blocks)

def _card_amount_prompt_text():
    e = _gift_emoji
    return (f'کارت به کارت{e(5888625992196431593, "💳")}\n\n'
            f'{e(5888594273862950655, "ℹ️")}مبلغی که میخواد کارت به کارت کنید رو ارسال کنید{e(5820903824046432799, "👇")}')

def _card_dest_prompt_text():
    e = _gift_emoji
    return (f'کارت به کارت{e(5888625992196431593, "💳")}\n\n'
            f'{e(5843973755545590553, "👤")}شماره حساب کارت مقصد رو ارسال کنید{e(5820903824046432799, "👇")}')

def _card_confirm_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("بله", callback_data=f"cardconfirm|yes|{user_id}", icon_custom_emoji_id="5938109560249127910"),
        colored_button("خیر", callback_data=f"cardconfirm|no|{user_id}", icon_custom_emoji_id="5938290000415167172"),
    )
    return markup

def _card_confirm_text(account, name, amount, fee):
    e = _gift_emoji
    return (
        f'شماره حساب مقصد: <code>{account}</code>{e(5888625992196431593, "💳")}\n\n'
        f'به نام: {html_escape_keep_emoji(name)}{e(5843911663203392756, "👤")}\n\n'
        f'{e(5888594273862950655, "ℹ️")}مبلغ در حال انتقال: {amount:,}{e(5940725397195853882, "💎")}\n\n'
        f'کارمزد کارت به کارت: {fee:,}{e(5832240029446971049, "💸")}\n\n'
        f'آیا اطلاعات و کارت به کارت رو تایید میکنید؟{e(5886285995229323603, "🏦")}'
    )

def _card_success_text(account, name, amount, fee):
    e = _gift_emoji
    return (
        f'کارت به کارت با موفقیت انجام شد{e(5888937012253171131, "✅")}\n\n'
        f'شماره حساب مقصد: <code>{account}</code>{e(5888625992196431593, "💳")}\n\n'
        f'به نام: {html_escape_keep_emoji(name)}{e(5843911663203392756, "👤")}\n\n'
        f'مبلغ: {amount:,}{e(5940725397195853882, "💎")}\n\n'
        f'کارمزد: {fee:,}{e(5832240029446971049, "💸")}'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("banktx|"))
def bank_transactions_panel(call):
    if _feature_gate(call, "bank"):
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    cancel_pending_step(call.message.chat.id, user_id)
    if not get_bank_account_number(user_id):
        safe_edit_message(_bank_err("اول حساب بانکی خودت رو افتتاح کن."), call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    safe_edit_message(_bank_tx_text(user_id), call.message.chat.id, call.message.message_id, bank_back_markup(user_id), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankcard|"))
def bank_card_prompt(call):
    if _feature_gate(call, "bank"):
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    if not get_bank_account_number(user_id):
        safe_edit_message(_bank_err("اول حساب بانکی خودت رو افتتاح کن."), call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    safe_edit_message(_card_amount_prompt_text(), call.message.chat.id, call.message.message_id, bank_back_markup(user_id), parse_mode="HTML")
    register_timed_next_step_handler(call.message, card_amount_step, user_id, call.message.message_id, expected_user_id=user_id)

def _card_retry(message, step, user_id, panel_msg_id, *extra):
    register_timed_next_step_handler(_PanelStub(message.chat, panel_msg_id), step, user_id, panel_msg_id, *extra, expected_user_id=user_id)

def card_amount_step(message, expected_user_id, panel_msg_id):
    if message.from_user.id != expected_user_id:
        _card_retry(message, card_amount_step, expected_user_id, panel_msg_id)
        return
    amount = parse_amount(message.text)
    if amount is None or amount <= 0:
        safe_edit_message(_bank_err("فرمت پیام درست نیست روی همین پیام ریپلای کن. مثال: 5000 یا 5کا."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        _card_retry(message, card_amount_step, expected_user_id, panel_msg_id)
        return
    apply_bank_interest(expected_user_id)
    fee = _card_fee(amount)
    if get_bank_balance(expected_user_id) < amount + fee:
        safe_edit_message(_bank_err("موجودی بانک شما برای این مبلغ و کارمزد کافی نیست."), message.chat.id, panel_msg_id, reply_markup=bank_markup(expected_user_id))
        return
    safe_edit_message(_card_dest_prompt_text(), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id), parse_mode="HTML")
    _card_retry(message, card_dest_step, expected_user_id, panel_msg_id, amount)

def card_dest_step(message, expected_user_id, panel_msg_id, amount):
    if message.from_user.id != expected_user_id:
        _card_retry(message, card_dest_step, expected_user_id, panel_msg_id, amount)
        return
    digits = re.sub(r"\D", "", _normalize_digits(message.text or ""))
    target = None
    if len(digits) == 16:
        try:
            res = _run_db_with_retry(
                lambda: supabase.table("users").select("user_id,username,bank_account_number")
                .eq("bank_account_number", digits).limit(1).execute(),
                label="card_dest_lookup",
            )
            target = res.data[0] if res.data else None
        except Exception as ex:
            logging.error(f"خطا در پیدا کردن حساب مقصد: {ex}")
    if not target:
        safe_edit_message(_bank_err("شماره حساب مقصد پیدا نشد روی همین پیام ریپلای کن و دوباره بفرست."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        _card_retry(message, card_dest_step, expected_user_id, panel_msg_id, amount)
        return
    if int(target["user_id"]) == int(expected_user_id):
        safe_edit_message(_bank_err("نمیشه به حساب خودت کارت به کارت کنی روی همین پیام ریپلای کن و شماره حساب دیگه‌ای بفرست."), message.chat.id, panel_msg_id, reply_markup=bank_back_markup(expected_user_id))
        _card_retry(message, card_dest_step, expected_user_id, panel_msg_id, amount)
        return
    apply_bank_interest(expected_user_id)
    fee = _card_fee(amount)
    if get_bank_balance(expected_user_id) < amount + fee:
        safe_edit_message(_bank_err("موجودی بانک شما برای این مبلغ و کارمزد کافی نیست."), message.chat.id, panel_msg_id, reply_markup=bank_markup(expected_user_id))
        return
    name = str(target.get("username") or f"کاربر {target['user_id']}")
    with _card_confirm_lock:
        _card_confirmations[expected_user_id] = {
            "target_id": int(target["user_id"]), "account": digits, "name": name,
            "amount": int(amount), "fee": int(fee),
            "chat_id": message.chat.id, "message_id": panel_msg_id,
        }
    safe_edit_message(_card_confirm_text(digits, name, amount, fee), message.chat.id, panel_msg_id,
                      reply_markup=_card_confirm_markup(expected_user_id), parse_mode="HTML")

def _execute_card_transfer(sender_id, target_id, amount, fee):
    """خروجی: 'ok' | 'balance' | 'fail'. کسر از فرستنده و واریز به مقصد زیر قفل هر دو نفر؛ اگه واریز شکست بخوره مبلغ برمی‌گرده."""
    total = int(amount) + int(fee)
    first, second = sorted([int(sender_id), int(target_id)])
    with _get_user_financial_lock(first):
        with _get_user_financial_lock(second):
            apply_bank_interest(sender_id)
            if get_bank_balance(sender_id) < total:
                return "balance"
            if not get_bank_account_number(target_id):
                return "fail"
            if not change_bank_balance(sender_id, -total):
                return "fail"
            if not change_bank_balance(target_id, int(amount)):
                if not change_bank_balance(sender_id, total):
                    logging.critical(f"ROLLBACK FAILED: card transfer user={sender_id} total={total}")
                return "fail"
    if fee > 0:
        # کارمزد مثل مالیات بازی‌ها به حساب دریافت‌کننده‌ی مالیات می‌ره (بیرون از قفل‌ها تا قفل‌ها قاطی نشن).
        if update_diamonds(TAX_RECEIVER_ID, int(fee)) is None:
            logging.error(f"واریز کارمزد کارت به کارت به {TAX_RECEIVER_ID} انجام نشد: {fee}")
    return "ok"

@bot.callback_query_handler(func=lambda call: call.data.startswith("cardconfirm|"))
def card_confirmation_callback(call):
    parts = call.data.split("|")
    if len(parts) != 3 or parts[1] not in ("yes", "no"):
        bot.answer_callback_query(call.id)
        return
    try:
        user_id = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id)
        return
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id)
        return
    with _card_confirm_lock:
        pending = _card_confirmations.get(user_id)
        if (not pending or pending["message_id"] != call.message.message_id
                or pending["chat_id"] != call.message.chat.id):
            pending = None
        else:
            del _card_confirmations[user_id]   # از همین‌جا فقط یک نفر/یک کلیک اجازه‌ی ادامه داره
    bot.answer_callback_query(call.id)
    if not pending:
        return
    chat_id, message_id = call.message.chat.id, call.message.message_id
    if parts[1] == "no":
        safe_edit_message(_bank_err("عملیات لغو شد"), chat_id, message_id, reply_markup=None)
        auto_return_to_bank(chat_id, message_id, user_id, 3)
        return
    status = _execute_card_transfer(user_id, pending["target_id"], pending["amount"], pending["fee"])
    if status == "balance":
        safe_edit_message(_bank_err("موجودی بانک شما برای این مبلغ و کارمزد کافی نیست"), chat_id, message_id, reply_markup=None)
        auto_return_to_bank(chat_id, message_id, user_id, 3)
        return
    if status != "ok":
        safe_edit_message(_bank_err("کارت به کارت انجام نشد و موجودی شما تغییر نکرد"), chat_id, message_id, reply_markup=None)
        auto_return_to_bank(chat_id, message_id, user_id, 3)
        return
    _record_bank_tx(user_id, "card", pending["amount"], pending["name"], pending["fee"])
    safe_edit_message(_card_success_text(pending["account"], pending["name"], pending["amount"], pending["fee"]),
                      chat_id, message_id, reply_markup=None, parse_mode="HTML")
    auto_return_to_bank(chat_id, message_id, user_id, 5)

# ================== دستورات متنی بانک ==================
@bot.message_handler(func=lambda m: text_is(m, "بانک", "بانک الماس"))
def text_bank(message):
    if _feature_gate(message, "bank"):
        return
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول باید یه‌بار /start بزنی (توی پیوی بات).")
        return
    user = get_user(user_id)
    if not user.get("bank_account_number"):
        show_or_edit_panel(
            message, user_id, "bank",
            bank_open_text(user_id),
            bank_open_markup(user_id),
            parse_mode="HTML"
        )
        return
    show_or_edit_panel(message, user_id, "bank", bank_text(user_id), bank_markup(user_id), parse_mode="HTML")

# ================== بخش راهنما ==================
# ================== راهنمای شیشه‌ای ==================
HELP_MENU_TEXT = 'هر بخشی که نیاز به راهنمایی دارید رو انتخاب کنید\u200c.<tg-emoji emoji-id="5942718468179628637">🌹</tg-emoji>'


def help_main_markup(user_id):
    def topic(label, emoji_id, key):
        return colored_button(label, callback_data=f"help_topic|{user_id}|{key}", icon_custom_emoji_id=emoji_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        topic("موجودی", "5825783216132334124", "balance"),
        topic("موجودی دیگران", "5825783216132334124", "other_balance")
    )
    markup.row(topic("گیفت", "5927029738626359516", "gift"))
    markup.row(
        topic("بانک الماس", "5886285995229323603", "bank"),
        topic("کازینو", "5938026800524301051", "casino")
    )
    markup.row(
        topic("شرطبندی", "5904440688845527733", "bet"),
        topic("معدن الماس", "5940725397195853882", "mine")
    )
    markup.row(
        topic("دینامیت", "5938458195629445238", "dynamite"),
        topic("جواهری", "5805686638952584277", "jewelry")
    )
    leaderboard_btn = topic("لیدربرد", "5935933089866846598", "rank")
    # «لیدربرد» شامل «رد» است و خودکار قرمز می‌شد؛ دستی آبی می‌کنیم.
    setattr(leaderboard_btn, "style", "primary")
    markup.row(leaderboard_btn)
    return markup

HELP_TOPIC_TEXTS = {
    "balance": f'{_gift_emoji(5904440688845527733, "💰")} موجودی:\nکلمه «موجودی» را در چت ارسال کنید.',
    "other_balance": f'{_gift_emoji(5825783216132334124, "💰")} موجودی دیگران:\nکلمه «موجودی» را در چت به همراه ریپلای روی پیام شخص ارسال کنید.',
    "gift": f'{_gift_emoji(5927029738626359516, "🎁")} گیفت شانسی:\nکلمه «گیفت» را در گروه ارسال کنید.',
    "bank": f'{_gift_emoji(5886285995229323603, "🏦")} بانک الماس:\nکلمه «بانک» را ارسال کنید.',
    "casino": f'{_gift_emoji(5938026800524301051, "🎰")} کازینو:\nکلمه «کازینو» را در چت ارسال کنید.',
    "bet": f'{_gift_emoji(5904440688845527733, "🎲")} شرطبندی:\nبرای شروع شرط بنویسید «شرطبندی عدد دلخواه»',
    "mine": f'{_gift_emoji(5940725397195853882, "💎")} معدن الماس:\nکلمه «معدن الماس» را ارسال کنید.',
    "dynamite": f'{_gift_emoji(5938458195629445238, "💣")} دینامیت:\nبرای شروع بنویسید «دینامیت عدد دلخواه»',
    "jewelry": f'{_gift_emoji(5805686638952584277, "💍")} جواهری:\nکلمه «جواهری» را ارسال کنید.',
    "rank": f'{_gift_emoji(5935933089866846598, "🏆")} رتبه‌بندی:\nکلمه «رنک» یا «لیدربرد» را ارسال کنید.',
}

@bot.callback_query_handler(func=lambda call: call.data == "showhelp" or call.data.startswith("showhelp|"))
def handle_show_help(call):
    owner_id = check_panel_owner(call) if "|" in call.data else call.from_user.id
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    safe_edit_message(
        HELP_MENU_TEXT,
        call.message.chat.id, call.message.message_id,
        reply_markup=help_main_markup(owner_id),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("help_topic|"))
def handle_help_topic(call):
    parts = call.data.split("|", 2)
    if len(parts) != 3:
        return
    owner_id = int(parts[1])
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id)
        return
    topic = parts[2]
    text = HELP_TOPIC_TEXTS.get(topic, "راهنمای این بخش پیدا نشد.")
    bot.answer_callback_query(call.id)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)


# ================== بخش انتقال الماس ==================


# ================== دستورات ادمین (قدیمی) ==================
@bot.message_handler(func=lambda m: m.text and re.search(r"افزودن\s*الماس\s*(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_add_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس اضافه کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nافزودن الماس <مقدار>\nمثال: افزودن الماس 50 یا افزودن الماس 50k")
        return

    match = re.search(r"افزودن\s*الماس\s*(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, "❌ مبلغ نامعتبره.")
        return
    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return

    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return

    update_diamonds(target_id, amount)
    target_name = get_display_name(message.reply_to_message.from_user)
    new_balance = get_balance(target_id)
    markup = types.InlineKeyboardMarkup()
    markup.add(colored_button(f"$ | موجودی جدید کاربر : {int(new_balance or 0):,} الماس", callback_data="pending", style="primary"))
    bot.reply_to(message, f"✓ | {amount} الماس به کاربر {target_name} اضافه شد.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and re.search(r"کم\s*کردن\s*الماس\s*(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_remove_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس کم کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nکم کردن الماس <مقدار>\nمثال: کم کردن الماس 50 یا کم کردن الماس 50k")
        return

    match = re.search(r"کم\s*کردن\s*الماس\s*(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, "❌ مبلغ نامعتبره.")
        return
    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return

    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return

    deduct = min(amount, get_balance(target_id))
    update_diamonds(target_id, -deduct)
    bot.reply_to(message, f"✅ {deduct} 💎 از کاربر {target_id} کم شد.\nموجودی فعلی: {get_balance(target_id)} 💎")

def perform_transfer(sender_id, target_id, amount):
    if amount > TRANSFER_MAX_AMOUNT:
        return False, _transfer_max_text(), None, None, None
    slot_ok, remaining, previous = _reserve_transfer_slot(sender_id)
    if not slot_ok:
        return False, _transfer_cooldown_text(remaining), None, None, None
    result = _perform_transfer_core(sender_id, target_id, amount)
    if not result[0]:
        _release_transfer_slot(sender_id, previous)
    return result

def _perform_transfer_core(sender_id, target_id, amount):
    if amount <= 0:
        return False, _casino_err("مقدار باید بزرگتر از صفر باشه"), None, None, None
    if target_id == sender_id:
        return False, _casino_err("نمیشه به خودت انتقال بدی"), None, None, None
    target_user = get_user(target_id)
    if not target_user:
        return False, _casino_err("کاربر مقصد هنوز /start نزده"), None, None, None
    target_name = (target_user.get("username") or f"کاربر {target_id}").strip()
    try:
        if get_balance(sender_id) < amount:
            return False, _casino_err("موجودی کافی نداری"), None, None, None
        if update_diamonds(sender_id, -amount) is None:
            return False, _casino_err("کسر از موجودی انجام نشد"), None, None, None
        if update_diamonds(target_id, amount) is None:
            update_diamonds(sender_id, amount)
            return False, _casino_err("انتقال کامل نشد و مبلغ برگشت داده شد"), None, None, None
        sender_balance = get_balance(sender_id)
        target_balance = get_balance(target_id)
        return True, f'{_gift_emoji(5888594273862950655, "✅")} مقدار {amount:,} الماس{_gift_emoji(5940725397195853882, "💎")} به {html_escape_keep_emoji(target_name)} منتقل شد.{_gift_emoji(5938109560249127910, "✅")}', sender_balance, target_balance, target_name
    except Exception as e:
        logging.error(f"خطا در perform_transfer: {e}")
        return False, _casino_err("انتقال انجام نشد"), None, None, None

@bot.message_handler(commands=["transfer"])
def cmd_transfer(message):
    sender_id = message.from_user.id
    if not get_user(sender_id):
        bot.reply_to(message, "اول /start بزن.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, _casino_err("روی پیام مقصد ریپلای کن: /transfer <مقدار>"))
        return
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) >= 2 else None
    if amount is None:
        bot.reply_to(message, _casino_err("مثال: /transfer 20 یا /transfer 20k"))
        return
    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, _casino_err("کاربر مقصد هنوز /start نزده"))
        return
    _show_transfer_confirmation(message, sender_id, target_id, amount)

@bot.message_handler(func=lambda m: m.text and re.search(r"انتقال\s+الماس\s+(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_transfer(message):
    sender_id = message.from_user.id
    if not get_user(sender_id):
        bot.reply_to(message, _casino_err("اول باید یه‌بار /start بزنی (توی پیوی بات)"))
        return
    if not message.reply_to_message:
        bot.reply_to(message, _casino_err("روی پیام کاربر مقصد ریپلای کن و بنویس:\nانتقال الماس <مقدار>\nمثال: انتقال الماس 200"))
        return

    match = re.search(r"انتقال\s+الماس\s+(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, _casino_err("فرمت اشتباه است"))
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, _casino_err("مبلغ نامعتبره"))
        return
    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, _casino_err("کاربر مقصد هنوز /start نزده"))
        return
    _show_transfer_confirmation(message, sender_id, target_id, amount)

# ================== شرط متنی ==================
def check_bet_timeout(bet_id):
    with _get_bet_lock(bet_id):
        bet = get_bet(bet_id)
        if not bet:
            return
        creator_id = bet.get('creator_id')
        creator_name = bet.get('creator_name')
        amount = bet.get('amount')
        chat_id = bet.get('chat_id')
        message_id = bet.get('message_id')
        status = bet.get('status')
        if status != "pending":
            return

        update_diamonds(creator_id, amount)
        set_bet_status(bet_id, "timeout")
    e = _gift_emoji
    safe_edit_message(
        f'{e(5825656411517886523, "⏰")} زمان تموم شد{e(5818716826699307883, "⌛")}\n'
        f'{e(5830338333892418460, "💰")} {amount:,} الماس {e(5940725397195853882, "💎")} به سازنده برگردونده شد.{e(5830144944399981619, "✅")}',
        chat_id=chat_id, message_id=message_id, reply_markup=None, parse_mode="HTML",
    )

def start_bet_flow(message, amount):
    if _feature_gate(message, "bet"):
        return
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, _start_first_err())
        return

    if amount <= 0:
        bot.reply_to(message, _casino_err("مقدار باید بزرگتر از صفر باشه"))
        return
    if get_balance(user_id) < amount:
        bot.reply_to(message, _casino_err("موجودی الماس کافی نداری"))
        return

    creator_name = get_display_name(message.from_user)
    update_diamonds(user_id, -amount)

    e = _gift_emoji
    panel_text = (
        f'{e(5818704981179505821, "🎲")} شرطبندی {e(5857454223368657979, "🎯")}\n\n'
        f'{e(5823445637231814311, "💰")} مقدار الماس : {amount:,}{e(5940725397195853882, "💎")}\n'
        f'{e(5843973755545590553, "👤")} سازنده : {safe_html_name(message.from_user)}'
    )
    sent = safe_send_message(
        message.chat.id,
        panel_text,
        parse_mode="HTML",
        reply_to_message_id=message.message_id,
    )
    if not sent:
        # پیام ساخته نشد؛ مبلغ رزرو شده باید فوراً برگردد.
        update_diamonds(user_id, amount)
        bot.reply_to(message, _casino_err("ارسال پنل شرط انجام نشد و مبلغ شرط به موجودی شما برگشت داده شد"))
        return

    bet_id = create_bet(user_id, creator_name, amount, message.chat.id, sent.message_id)
    if bet_id is None:
        # رکورد شرط ساخته نشد؛ نگذاریم مبلغ کاربر گم شود.
        update_diamonds(user_id, amount)
        safe_edit_message(
            _casino_err("نمایش دکمه‌های شرط با مشکل مواجه شد و مقداری که شرط بسته بودید به حساب شما برگشت"),
            chat_id=message.chat.id, message_id=sent.message_id, reply_markup=None
        )
        return

    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("پیوستن", callback_data=f"join|{bet_id}", icon_custom_emoji_id="5938109560249127910"),
        colored_button("لغو", callback_data=f"cancel|{bet_id}", icon_custom_emoji_id="5819154526816444042"),
    )
    edited_ok = safe_edit_message(panel_text, chat_id=message.chat.id, message_id=sent.message_id, reply_markup=markup, parse_mode="HTML")
    if not edited_ok:
        # دکمه‌ها اضافه نشدن؛ نگذاریم شرط بدون امکان لغو/پیوستن باقی بمونه و پول بلوکه بشه.
        with _get_bet_lock(bet_id):
            fresh = get_bet(bet_id)
            if fresh and fresh.get("status") == "pending":
                update_diamonds(user_id, amount)
                set_bet_status(bet_id, "cancelled")
        safe_edit_message(
            _casino_err("نمایش دکمه‌های شرط با مشکل مواجه شد و مقداری که شرط بسته بودید به حساب شما برگشت"),
            chat_id=message.chat.id, message_id=sent.message_id, reply_markup=None,
        )
        return

    _start_daemon_timer(JOIN_TIMEOUT_SECONDS, check_bet_timeout, bet_id)

@bot.message_handler(commands=["bet", "شرط"])
def cmd_bet(message):
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 else None
    if amount is None:
        bot.reply_to(message, "استفاده درست: /bet <مقدار>\nمثال: /bet 20 یا /bet 20k")
        return
    start_bet_flow(message, amount)

@bot.message_handler(func=lambda m: m.text and re.search(r"(?:شرط\s*بندی?|بازی)\s+(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_bet(message):
    match = re.search(r"(?:شرط\s*بندی?|بازی)\s+(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, _casino_err("فرمت اشتباه است. مثال: شرط بندی 20 یا شرط بندی 20k"))
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, _casino_err("مبلغ نامعتبره"))
        return
    start_bet_flow(message, amount)

def resolve_bet(bet_id, opponent_id, opponent_name, is_bot=False):
    bet = get_bet(bet_id)
    creator_id = bet.get('creator_id')
    creator_name = bet.get('creator_name')
    amount = bet.get('amount')
    chat_id = bet.get('chat_id')
    message_id = bet.get('message_id')
    status = bet.get('status')

    winner_is_creator = random.random() < 0.5
    pool = amount if is_bot else 2 * amount

    if winner_is_creator:
        winner_name, winner_id = creator_name, creator_id
        loser_name, loser_id = opponent_name, opponent_id
    else:
        winner_name, winner_id = opponent_name, opponent_id
        loser_name, loser_id = creator_name, creator_id

    real_winner_id = None if (winner_id is None) else winner_id
    if real_winner_id is not None:
        payout, tax, loan_repay, tax_rate_used, loan_rate_used = calculate_payout(real_winner_id, pool, context="bet")
        update_diamonds(real_winner_id, payout)
        _credit_tax(chat_id, tax)
        if get_user(real_winner_id):
            register_bet_win(real_winner_id, chat_id, winner_name)
    else:
        payout = 0
        tax = int(pool * TAX_RATE)
        loan_repay = 0
        tax_rate_used = TAX_RATE
        loan_rate_used = 0

    set_bet_status(bet_id, "finished")

    winner_display = html_escape_keep_emoji(display_name_with_tag(winner_id, winner_name or "کاربر"))
    loser_display = html_escape_keep_emoji(display_name_with_tag(loser_id, loser_name or "کاربر"))
    e = _gift_emoji
    text = (
        f'{e(5818704981179505821, "🎲")} نتیجه باز\u200cی {e(5888937012253171131, "🏁")}\n\n'
        f'{e(6014651259057873388, "🏆")} برنده: {winner_display}\n'
        f'{e(5839270298205035832, "🥈")} بازنده: {loser_display}'
    )
    result_amount = payout if real_winner_id is not None else 0
    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button(
            f"مبلغ برد: {result_amount:,}",
            callback_data="bet_result_noop",
            icon_custom_emoji_id="5823445637231814311",
        ),
        colored_button(
            f"مالیات: {tax:,}",
            callback_data="bet_result_noop",
            icon_custom_emoji_id="5889002570633977838",
        ),
    )
    safe_edit_message(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "bet_result_noop")
def bet_result_noop_callback(call):
    # دکمه‌های مبلغ برد و مالیات صرفاً نمایشی هستند و هیچ عملیاتی انجام نمی‌دهند.
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(
    func=lambda call: call.data == "pending" or call.data.split("|")[0] in ("cancel", "join")
)
def handle_callback(call):
    if call.data == "pending":
        bot.answer_callback_query(call.id)
        return

    action, bet_id_str = call.data.split("|")
    bet_id = int(bet_id_str)
    clicker_id = call.from_user.id
    clicker_name = get_display_name(call.from_user)

    # همه‌ی مراحل «چک کردن وضعیت» و «تغییر وضعیت» زیر یک قفل مخصوص همین شرط انجام
    # می‌شن؛ همون الگویی که در بازی دینامیت باعث شد کلیک‌های هم‌زمان (پیوستن/لغو/تایم‌اوت)
    # با هم تداخل نکنن و پیام هم درست ادیت بشه.
    outcome = None
    with _get_bet_lock(bet_id):
        bet = get_bet(bet_id)

        if not bet:
            bot.answer_callback_query(call.id)
            return

        creator_id = bet.get('creator_id')
        creator_name = bet.get('creator_name')
        amount = bet.get('amount')
        chat_id = bet.get('chat_id')
        message_id = bet.get('message_id')
        status = bet.get('status')

        # ========== بهبود پیام‌های خطا برای وضعیت‌های مختلف ==========
        if status != "pending":
            if status == "timeout":
                msg = "⏱ زمان انتظار به پایان رسید و شرط لغو شد."
            elif status == "cancelled":
                msg = "❌ این شرط توسط سازنده لغو شده است."
            elif status == "finished":
                msg = "🏁 این شرط قبلاً به پایان رسیده است."
            else:
                msg = "این شرط دیگر فعال نیست."
            bot.answer_callback_query(call.id)
            return

        if action == "cancel":
            if clicker_id != creator_id:
                bot.answer_callback_query(call.id)
                return
            update_diamonds(creator_id, amount)
            set_bet_status(bet_id, "cancelled")
            outcome = ("cancel", amount, chat_id, message_id)

        elif action == "join":
            if clicker_id == creator_id:
                bot.answer_callback_query(call.id)
                return
            if not get_user(clicker_id):
                bot.answer_callback_query(call.id, "شما باید ابتدا در بات /start را بزنید.", show_alert=True)
                return
            try:
                if get_balance(clicker_id) < amount:
                    bot.answer_callback_query(call.id)
                    return
                if update_diamonds(clicker_id, -amount) is None:
                    bot.answer_callback_query(call.id)
                    return
                # بلافاصله وضعیت رو از pending خارج می‌کنیم تا یک کلیک هم‌زمان دیگه
                # (لغو یا پیوستن دوباره) نتونه همین شرط رو دوباره پردازش کنه.
                set_bet_status(bet_id, "finished")
            except Exception as e:
                logging.error(f"خطا در claim شرط {bet_id}: {e}")
                bot.answer_callback_query(call.id)
                return
            outcome = ("join", clicker_id, clicker_name)

    # از اینجا به بعد قفل آزاد شده؛ عملیات کند (ادیت پیام/محاسبه‌ی نتیجه) دیگه بقیه‌ی
    # کلیک‌ها روی همین شرط رو بلاک نمی‌کنه.
    if outcome is None:
        return
    if outcome[0] == "cancel":
        _, amount, chat_id, message_id = outcome
        e = _gift_emoji
        safe_edit_message(
            f'{e(5818704981179505821, "🎲")} بازی لغو شد.{e(5819154526816444042, "❌")}\n'
            f'{e(5830338333892418460, "💰")} {amount:,} الماس {e(5940725397195853882, "💎")} برگردونده شد.{e(5830144944399981619, "✅")}',
            chat_id=chat_id, message_id=message_id, reply_markup=None, parse_mode="HTML",
        )
        bot.answer_callback_query(call.id)
    elif outcome[0] == "join":
        _, join_clicker_id, join_clicker_name = outcome
        resolve_bet(bet_id, join_clicker_id, join_clicker_name, is_bot=False)
        bot.answer_callback_query(call.id)

# ================== بازی دینامیت (۲ نفره، ۳ در ۳) ==================
DYNAMITE_GRID_SIZE = 9  # 3x3
TURN_TIMEOUT_SECONDS = 60  # هر نوبت ۶۰ ثانیه وقت داره
dynamite_lock = threading.RLock()
active_dynamites = {}  # message_id -> game dict
_dynamite_turn_seq = itertools.count(1)

# آیدی ایموجی‌های پرمیوم بازی دینامیت
DYN_DIAMOND_EMOJI_ID = 5940725397195853882  # الماس (خونه‌ی امن)
DYN_BOMB_EMOJI_ID = 5929177175029718803     # بمب

def _dyn_name(user_id, name):
    """اسم بازیکن برای پیام HTML (escape شده؛ ایموجی گیفت دست‌نخورده)."""
    return html_escape_keep_emoji(display_name_with_tag(user_id, name))

# ---------- پایداری روی دیتابیس (برای اینکه با ری‌استارت شدن ربات از بین نره) ----------
def _dynamite_db_upsert(message_id, game):
    try:
        supabase.table("dynamite_games").upsert({
            "message_id": int(message_id),
            "chat_id": int(game["chat_id"]),
            "creator_id": int(game["creator_id"]),
            "creator_name": game["creator_name"],
            "opponent_id": int(game["opponent_id"]) if game["opponent_id"] else None,
            "opponent_name": game["opponent_name"],
            "amount": int(game["amount"]),
            "status": game["status"],
            "bomb_index": game["bomb_index"],
            "revealed": sorted(game["revealed"]),
            "turn_id": int(game["turn_id"]) if game["turn_id"] else None,
            "safe_remaining": game["safe_remaining"],
        }, on_conflict="message_id").execute()
    except Exception as e:
        logging.error(f"خطا در ذخیره دیتابیس دینامیت {message_id}: {e}")

def _dynamite_db_delete(message_id):
    try:
        supabase.table("dynamite_games").delete().eq("message_id", int(message_id)).execute()
    except Exception as e:
        logging.error(f"خطا در حذف دیتابیس دینامیت {message_id}: {e}")

def rehydrate_dynamite_games():
    """بعد از هر بار بالا اومدن ربات، بازی‌های نیمه‌کاره دینامیت رو از دیتابیس برمی‌گردونه."""
    try:
        rows = supabase.table("dynamite_games").select("*").in_("status", ["pending", "active"]).execute().data or []
    except Exception as e:
        logging.error(f"خطا در بازیابی بازی‌های دینامیت: {e}")
        return
    with dynamite_lock:
        for row in rows:
            message_id = row["message_id"]
            game = {
                "chat_id": row["chat_id"],
                "creator_id": row["creator_id"],
                "creator_name": row["creator_name"],
                "opponent_id": row.get("opponent_id"),
                "opponent_name": row.get("opponent_name"),
                "amount": row["amount"],
                "status": row["status"],
                "bomb_index": row.get("bomb_index"),
                "revealed": set(row.get("revealed") or []),
                "turn_id": row.get("turn_id"),
                "safe_remaining": row.get("safe_remaining"),
                "turn_token": None,
            }
            active_dynamites[message_id] = game
            if game["status"] == "pending":
                # به‌صورت ساده یک تایمر کامل جدید برای پیوستن در نظر می‌گیریم.
                _start_daemon_timer(JOIN_TIMEOUT_SECONDS, dynamite_timeout, message_id)
            elif game["status"] == "active":
                start_turn_timer(message_id, game)

def dynamite_pending_text(amount, creator_id, creator_name):
    e = _gift_emoji
    return (
        f'{e(5818704981179505821, "🎲")} دینامیت {e(5929177175029718803, "💣")}\n\n'
        f'{e(5823445637231814311, "💰")} مقدار الماس : {amount:,}{e(5940725397195853882, "💎")}\n'
        f'{e(5843973755545590553, "👤")} سازنده: {_dyn_name(creator_id, creator_name)}'
    )

def dynamite_pending_markup():
    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("پیوستن", callback_data="dynjoin", icon_custom_emoji_id="5830144944399981619"),
        colored_button("لغو", callback_data="dyncancel", icon_custom_emoji_id="5819154526816444042"),
    )
    return markup

def dynamite_active_text(game):
    e = _gift_emoji
    creator_display = _dyn_name(game["creator_id"], game["creator_name"])
    opponent_display = _dyn_name(game["opponent_id"], game["opponent_name"])
    turn_name = creator_display if game["turn_id"] == game["creator_id"] else opponent_display
    return (
        f'{e(5821026698765803333, "🎲")} دینامیت {e(5929177175029718803, "💣")}\n\n'
        f'{e(5823445637231814311, "💰")} مقدار الماس : {game["amount"]:,}{e(5940725397195853882, "💎")}\n'
        f'{e(5843973755545590553, "👤")} سازنده : {creator_display}\n'
        f'{e(5859432880442187580, "👥")} شرکت کننده : {opponent_display}\n\n'
        f'{e(5938348905891632091, "⏳")} نوبت: {turn_name}\n'
        f'{e(5938098930205069821, "🛡")} جاهای امن باقی‌مانده: {game["safe_remaining"]}{e(5830144944399981619, "✅")}\n\n'
        f'{e(5819051035284479206, "👇")} روی دکمه های زیر کلیک کنید.{e(5820903824046432799, "👇")}'
    )

def dynamite_board_markup(game):
    markup = types.InlineKeyboardMarkup()
    for row in range(3):
        buttons = []
        for col in range(3):
            idx = row * 3 + col
            if idx in game["revealed"]:
                buttons.append(colored_button("\u2800", callback_data=f"dyncell|{idx}",
                                              icon_custom_emoji_id=str(DYN_DIAMOND_EMOJI_ID)))
            else:
                buttons.append(colored_button(" ", callback_data=f"dyncell|{idx}"))
        markup.row(*buttons)
    return markup

def dynamite_result_markup(payout, tax, game=None, reveal_bomb=False):
    """صفحه‌ی ۳×۳ بعد از پایان بازی هم روی پیام می‌مونه (دکمه‌ها بی‌اثر)؛ الماس‌های
    باز‌شده می‌مونن و اگه بازی با انفجار تموم شده باشه، فقط دکمه‌ی بمب قرمز می‌شه."""
    markup = types.InlineKeyboardMarkup()
    if game is not None:
        for row in range(3):
            buttons = []
            for col in range(3):
                idx = row * 3 + col
                if reveal_bomb and idx == game.get("bomb_index"):
                    buttons.append(colored_button("\u2800", callback_data="dynamite_result_noop",
                                                  style="danger",
                                                  icon_custom_emoji_id=str(DYN_BOMB_EMOJI_ID)))
                elif idx in game["revealed"]:
                    buttons.append(colored_button("\u2800", callback_data="dynamite_result_noop",
                                                  icon_custom_emoji_id=str(DYN_DIAMOND_EMOJI_ID)))
                else:
                    buttons.append(colored_button(" ", callback_data="dynamite_result_noop"))
            markup.row(*buttons)
    markup.row(
        colored_button(f"مبلغ برد: {payout:,}", callback_data="dynamite_result_noop",
                       icon_custom_emoji_id="5823445637231814311"),
        colored_button(f"مالیات: {tax:,}", callback_data="dynamite_result_noop",
                       icon_custom_emoji_id="5889002570633977838"),
    )
    return markup

@bot.callback_query_handler(func=lambda call: call.data == "dynamite_result_noop")
def dynamite_result_noop_callback(call):
    # دکمه‌های مبلغ برد و مالیات صرفاً نمایشی هستند و هیچ عملیاتی انجام نمی‌دهند.
    bot.answer_callback_query(call.id)

def _dynamite_lazy_load(message_id):
    """اگه بازی تو حافظه‌ی همین پردازش نبود (مثلاً به‌خاطر ری‌استارت یا چند-worker بودن هاست)،
    یه بار از دیتابیس می‌خونیمش تا بازی الکی «تموم‌شده» اعلام نشه."""
    try:
        rows = supabase.table("dynamite_games").select("*").eq("message_id", int(message_id)).execute().data
    except Exception as e:
        logging.error(f"خطا در لود دیتابیس دینامیت {message_id}: {e}")
        return None
    if not rows:
        return None
    row = rows[0]
    if row["status"] not in ("pending", "active"):
        return None
    game = {
        "chat_id": row["chat_id"],
        "creator_id": row["creator_id"],
        "creator_name": row["creator_name"],
        "opponent_id": row.get("opponent_id"),
        "opponent_name": row.get("opponent_name"),
        "amount": row["amount"],
        "status": row["status"],
        "bomb_index": row.get("bomb_index"),
        "revealed": set(row.get("revealed") or []),
        "turn_id": row.get("turn_id"),
        "safe_remaining": row.get("safe_remaining"),
        "turn_token": None,
        "turn_timer": None,
    }
    active_dynamites[message_id] = game
    if game["status"] == "pending":
        _start_daemon_timer(JOIN_TIMEOUT_SECONDS, dynamite_timeout, message_id)
    elif game["status"] == "active":
        start_turn_timer(message_id, game)
    return game

def dynamite_timeout(message_id):
    with dynamite_lock:
        game = active_dynamites.get(message_id)
        if not game or game["status"] != "pending":
            return
        update_diamonds(game["creator_id"], game["amount"])
        del active_dynamites[message_id]
        chat_id = game["chat_id"]
        amount = game["amount"]
    _dynamite_db_delete(message_id)
    e = _gift_emoji
    safe_edit_message(
        f'{e(5825656411517886523, "⏰")} زمان تموم شد{e(5818716826699307883, "⌛")}\n'
        f'{e(5830338333892418460, "💰")} {amount:,} الماس {e(5940725397195853882, "💎")} به سازنده برگردونده شد.{e(5830144944399981619, "✅")}',
        chat_id=chat_id, message_id=message_id, reply_markup=None, parse_mode="HTML",
    )

def start_turn_timer(message_id, game):
    """برای نوبت فعلیِ game یک توکن جدید و یک تایمر ۶۰ ثانیه‌ای می‌سازد."""
    old_timer = game.get("turn_timer")
    if old_timer is not None:
        try:
            old_timer.cancel()
        except Exception:
            pass
    token = next(_dynamite_turn_seq)
    game["turn_token"] = token
    timer = threading.Timer(TURN_TIMEOUT_SECONDS, dynamite_turn_timeout, args=[message_id, token])
    game["turn_timer"] = timer
    timer.start()

def dynamite_turn_timeout(message_id, token):
    with dynamite_lock:
        game = active_dynamites.get(message_id)
        if not game or game["status"] != "active" or game.get("turn_token") != token:
            return  # نوبت قبلاً عوض شده یا بازی تموم شده
        loser_id = game["turn_id"]
        if loser_id == game["creator_id"]:
            loser_name, winner_id, winner_name = game["creator_name"], game["opponent_id"], game["opponent_name"]
        else:
            loser_name, winner_id, winner_name = game["opponent_name"], game["creator_id"], game["creator_name"]
        e = _gift_emoji
        reason = (
            f'{e(5825656411517886523, "⏰")} زمان تموم شد\n'
            f'{e(5872823922751185495, "⚠️")} بازنده به دلیل انتخاب نکردن در تایم تعیین شده باخت.{e(5875037024909532569, "❌")}'
        )
        _dynamite_finish(message_id, game, winner_id, winner_name, loser_id, loser_name, reason)

def start_dynamite_flow(message, amount):
    if _feature_gate(message, "dynamite"):
        return
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, _start_first_err())
        return
    if amount <= 0:
        bot.reply_to(message, _casino_err("مقدار باید بزرگتر از صفر باشه"))
        return
    if get_balance(user_id) < amount:
        bot.reply_to(message, _casino_err("موجودی الماس کافی نداری"))
        return

    creator_name = get_display_name(message.from_user)
    update_diamonds(user_id, -amount)

    sent = safe_send_message(
        message.chat.id,
        dynamite_pending_text(amount, user_id, creator_name),
        parse_mode="HTML",
        reply_to_message_id=message.message_id,
    )
    if not sent:
        update_diamonds(user_id, amount)
        bot.reply_to(message, _casino_err("ارسال پنل دینامیت انجام نشد و مبلغ شرط به موجودی شما برگشت داده شد"))
        return

    with dynamite_lock:
        game = {
            "chat_id": message.chat.id,
            "creator_id": user_id,
            "creator_name": creator_name,
            "opponent_id": None,
            "opponent_name": None,
            "amount": amount,
            "status": "pending",
            "bomb_index": None,
            "revealed": set(),
            "turn_id": None,
            "safe_remaining": DYNAMITE_GRID_SIZE - 1,
            "turn_token": None,
            "turn_timer": None,
        }
        active_dynamites[sent.message_id] = game

    _dynamite_db_upsert(sent.message_id, game)
    safe_edit_message(
        dynamite_pending_text(amount, user_id, creator_name),
        chat_id=message.chat.id, message_id=sent.message_id, reply_markup=dynamite_pending_markup(),
        parse_mode="HTML",
    )
    _start_daemon_timer(JOIN_TIMEOUT_SECONDS, dynamite_timeout, sent.message_id)

@bot.message_handler(commands=["dynamite"])
def cmd_dynamite(message):
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 else None
    if amount is None:
        bot.reply_to(message, "استفاده درست: /dynamite <مقدار>\nمثال: /dynamite 20 یا /dynamite 20k")
        return
    start_dynamite_flow(message, amount)

@bot.message_handler(func=lambda m: m.text and re.search(r"دینامیت\s+(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_dynamite(message):
    match = re.search(r"دینامیت\s+(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, _casino_err("فرمت اشتباه است. مثال: دینامیت 20 یا دینامیت 20k"))
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, _casino_err("مبلغ نامعتبره"))
        return
    start_dynamite_flow(message, amount)

@bot.callback_query_handler(func=lambda call: call.data == "dyncancel")
def dynamite_cancel_callback(call):
    with dynamite_lock:
        message_id = call.message.message_id
        game = active_dynamites.get(message_id) or _dynamite_lazy_load(message_id)
        if not game:
            bot.answer_callback_query(call.id)
            return
        if game["status"] != "pending":
            bot.answer_callback_query(call.id)
            return
        if call.from_user.id != game["creator_id"]:
            bot.answer_callback_query(call.id)
            return
        update_diamonds(game["creator_id"], game["amount"])
        amount = game["amount"]
        chat_id = game["chat_id"]
        del active_dynamites[message_id]
    _dynamite_db_delete(message_id)
    e = _gift_emoji
    safe_edit_message(
        f'{e(5818704981179505821, "🎲")} بازی لغو شد.{e(5819154526816444042, "❌")}\n'
        f'{e(5830338333892418460, "💰")} {amount:,} الماس {e(5940725397195853882, "💎")} برگردونده شد.{e(5830144944399981619, "✅")}',
        chat_id=chat_id, message_id=message_id, reply_markup=None, parse_mode="HTML",
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "dynjoin")
def dynamite_join_callback(call):
    with dynamite_lock:
        message_id = call.message.message_id
        game = active_dynamites.get(message_id) or _dynamite_lazy_load(message_id)
        if not game:
            bot.answer_callback_query(call.id)
            return
        if game["status"] != "pending":
            bot.answer_callback_query(call.id)
            return
        clicker_id = call.from_user.id
        if clicker_id == game["creator_id"]:
            bot.answer_callback_query(call.id)
            return
        if not get_user(clicker_id):
            bot.answer_callback_query(call.id)
            return
        if get_balance(clicker_id) < game["amount"]:
            bot.answer_callback_query(call.id)
            return
        if update_diamonds(clicker_id, -game["amount"]) is None:
            bot.answer_callback_query(call.id)
            return

        clicker_name = get_display_name(call.from_user)
        game["opponent_id"] = clicker_id
        game["opponent_name"] = clicker_name
        game["status"] = "active"
        game["bomb_index"] = random.randint(0, DYNAMITE_GRID_SIZE - 1)
        game["revealed"] = set()
        game["safe_remaining"] = DYNAMITE_GRID_SIZE - 1
        game["turn_id"] = game["creator_id"]
        chat_id = game["chat_id"]
        start_turn_timer(message_id, game)
        text = dynamite_active_text(game)
        markup = dynamite_board_markup(game)

    _dynamite_db_upsert(message_id, game)
    safe_edit_message(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("dyncell|"))
def dynamite_cell_reveal(call):
    with dynamite_lock:
        _dynamite_cell_reveal_locked(call)

def _dynamite_finish(message_id, game, winner_id, winner_name, loser_id, loser_name, middle_text, reveal_bomb=False):
    """تسویه‌ی مبلغ بازی (چه با بمب، چه با تموم شدن وقت نوبت) و ادیت پیام نتیجه."""
    old_timer = game.get("turn_timer")
    if old_timer is not None:
        try:
            old_timer.cancel()
        except Exception:
            pass

    pool = game["amount"] * 2
    payout, tax, loan_repay, tax_rate_used, loan_rate_used = calculate_payout(winner_id, pool, context="bet")
    update_diamonds(winner_id, payout)
    _credit_tax(game["chat_id"], tax)
    register_bet_win(winner_id, game["chat_id"], winner_name)

    chat_id = game["chat_id"]
    game["status"] = "finished"
    active_dynamites.pop(message_id, None)
    _dynamite_db_delete(message_id)

    e = _gift_emoji
    text = (
        f'{e(5818704981179505821, "🎲")} نتیجه بازی {e(5888937012253171131, "🏁")}\n\n'
        f'{middle_text}\n'
        f'{e(5837050654811496370, "🏆")} برنده: {_dyn_name(winner_id, winner_name)}\n'
        f'{e(5856946687083290613, "🥈")} بازنده: {_dyn_name(loser_id, loser_name)}'
    )
    safe_edit_message(text, chat_id=chat_id, message_id=message_id,
                      reply_markup=dynamite_result_markup(payout, tax, game, reveal_bomb), parse_mode="HTML")

def _dynamite_cell_reveal_locked(call):
    message_id = call.message.message_id
    game = active_dynamites.get(message_id) or _dynamite_lazy_load(message_id)
    if not game or game["status"] != "active":
        bot.answer_callback_query(call.id)
        return

    clicker_id = call.from_user.id
    if clicker_id not in (game["creator_id"], game["opponent_id"]):
        bot.answer_callback_query(call.id)
        return
    if clicker_id != game["turn_id"]:
        bot.answer_callback_query(call.id)
        return

    idx = int(call.data.split("|")[1])
    if idx in game["revealed"]:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)

    if idx == game["bomb_index"]:
        loser_id = clicker_id
        if clicker_id == game["creator_id"]:
            loser_name, winner_id, winner_name = game["creator_name"], game["opponent_id"], game["opponent_name"]
        else:
            loser_name, winner_id, winner_name = game["opponent_name"], game["creator_id"], game["creator_name"]
        e = _gift_emoji
        middle_text = (
            f'{e(5839270298205035832, "💥")} بازیکن {_dyn_name(loser_id, loser_name)} منفجر شد'
            f'{e(5938458195629445238, "💣")}\u200f'
        )
        _dynamite_finish(message_id, game, winner_id, winner_name, loser_id, loser_name, middle_text, reveal_bomb=True)
        return

    game["revealed"].add(idx)
    game["safe_remaining"] -= 1
    game["turn_id"] = game["opponent_id"] if clicker_id == game["creator_id"] else game["creator_id"]
    start_turn_timer(message_id, game)

    _dynamite_db_upsert(message_id, game)
    text = dynamite_active_text(game)
    markup = dynamite_board_markup(game)
    safe_edit_message(text, chat_id=game["chat_id"], message_id=message_id, reply_markup=markup, parse_mode="HTML")

# این تابع همین‌جا (در زمان import شدن فایل) صدا زده می‌شود، نه فقط داخل
# if __name__=="__main__"، چون بعضی سرویس‌های هاست (مثلاً gunicorn) فایل رو
# مستقیماً اجرا نمی‌کنن بلکه import می‌کنن؛ اینجوری همیشه بازی‌های نیمه‌کاره برمی‌گردن.
rehydrate_dynamite_games()

# ================== بخش کازینو ==================
CASINO_GAMES = {
    "dice": "🎲",
    "dart": "🎯",
    "basket": "🏀",
    "football": "⚽",
    "bowling": "🎳",
    "slot": "🎰",
}
CASINO_GAME_NAMES = {
    "dice": "تاس",
    "dart": "دارت",
    "basket": "بسکتبال",
    "football": "فوتبال",
    "bowling": "بولینگ",
    "slot": "اسلات",
}
CASINO_MENU_TEXT = (
    f'{_gift_emoji(6012438204144165493, "🎰")} به بخش کازینو خوش اومدی!\n\n'
    f'{_gift_emoji(5818704981179505821, "🎮")} یکی از بازی‌ها رو انتخاب کن{_gift_emoji(5820903824046432799, "👇")}'
)
# آیکون پرمیوم دکمه‌های منوی کازینو (فوتبال آیکون ندارد و همان ⚽ می‌ماند)
CASINO_ICON_IDS = {
    "dice": "5875483791702630534",
    "dart": "5832335747088130009",
    "basket": "5929547568714359397",
    "bowling": "5929469353064931405",
    "slot": "5875360259853263196",
}
def _casino_err(text):
    return f'{html.escape(text)} {_gift_emoji(5819154526816444042, "❌")}'

def _start_first_err():
    return _casino_err("اول باید بات رو استارت کنی")


def html_escape_keep_emoji(text):
    """متن را برای HTML امن می‌کند ولی تگ‌های <tg-emoji> (ایموجی گیفت) را دست‌نخورده می‌گذارد."""
    parts = re.split(r'(<tg-emoji[^>]*>.*?</tg-emoji>)', str(text or ""))
    return "".join(p if p.startswith("<tg-emoji") else html.escape(p) for p in parts)


def casino_player_html(player):
    """اسم بازیکن برای پیام HTML (escape شده؛ ایموجی گیفت دست‌نخورده)."""
    if player.get("html_name"):
        return player["html_name"]
    return html_escape_keep_emoji(player.get("name") or "کاربر")


CASINO_SEND_PHRASES = {
    "basket": "بسکتبال های خودتون رو ارسال کنید.",
    "dice": "تاس های خودتون رو ارسال کنید.",
    "bowling": "بولینگ های خودتون رو ارسال کنید.",
    "slot": "اسلات های خودتون رو ارسال کنید.",
    "dart": "دارت های خودتون رو ارسال کنید.",
    "football": "توپ های فوتبال خودتون رو ارسال کنید.",
}


def casino_game_icon(game_key):
    e = _gift_emoji
    return e(int(CASINO_ICON_IDS[game_key]), CASINO_GAMES[game_key]) if game_key in CASINO_ICON_IDS else CASINO_GAMES[game_key]


def casino_game_title(game_key):
    """خط اول صفحه‌های کازینو: ایموجی + اسم بازی + آیکون بازی."""
    e = _gift_emoji
    return f'{e(5818704981179505821, "🎮")} {CASINO_PAGE_NAMES[game_key]} {casino_game_icon(game_key)}'


# اسم بازی‌ها برای صفحه‌ی «ارسال مبلغ شرط»
CASINO_PAGE_NAMES = {
    "dice": "تاس",
    "dart": "دارت",
    "basket": "بسکت",
    "football": "فوتبال",
    "bowling": "بولینگ",
    "slot": "اسلات",
}
CASINO_BET_PRESETS = [100000, 500000, 1000000, 5000000, 10000000]

def casino_games_keyboard(owner_id):
    markup = types.InlineKeyboardMarkup()
    games = [g for g in CASINO_GAMES.items() if game_on(g[0])]
    for i in range(0, len(games), 3):
        row = []
        for key, emoji in games[i:i + 3]:
            icon_id = CASINO_ICON_IDS.get(key)
            if icon_id:
                row.append(colored_button("\u2800", callback_data=f"cgame|{key}|{owner_id}", icon_custom_emoji_id=icon_id))
            else:
                row.append(colored_button(emoji, callback_data=f"cgame|{key}|{owner_id}"))
        markup.row(*row)
    return markup

@bot.callback_query_handler(func=lambda call: call.data == "casinomenu" or call.data.startswith("casinomenu|"))
def casino_from_main_menu(call):
    if _feature_gate(call, "casino"):
        return
    if "|" in call.data:
        owner_id = check_panel_owner(call)
        if owner_id is None:
            return
    else:
        owner_id = call.from_user.id
    bot.answer_callback_query(call.id)
    safe_edit_message(
        CASINO_MENU_TEXT,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=casino_games_keyboard(owner_id),
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: text_is(m, "کازینو"))
def casino_panel(message):
    if _feature_gate(message, "casino"):
        return
    if not get_user(message.from_user.id):
        bot.reply_to(message, _start_first_err())
        return
    show_or_edit_panel(
        message, message.from_user.id, "casino",
        CASINO_MENU_TEXT,
        casino_games_keyboard(message.from_user.id),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cgame|"))
def casino_game_select(call):
    if _feature_gate(call, "casino"):
        return
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    if not get_user(owner_id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    game_key = call.data.split("|")[1]
    if not game_on(game_key):
        return

    e = _gift_emoji
    game_icon = e(int(CASINO_ICON_IDS[game_key]), CASINO_GAMES[game_key]) if game_key in CASINO_ICON_IDS else CASINO_GAMES[game_key]
    back_markup = types.InlineKeyboardMarkup()
    back_markup.row(back_button(callback_data=f"cback|{owner_id}"))
    safe_edit_message(
        f'{e(5818704981179505821, "🎮")} {CASINO_PAGE_NAMES[game_key]} {game_icon}\n\n'
        f'{e(5938311423712039050, "✍️")} مقدار الماسی که میخواید شرط ببندید رو ارسال کنید {e(5820903824046432799, "👇")}',
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=back_markup, parse_mode="HTML"
    )
    register_timed_next_step_handler(call.message, casino_custom_amount_step, game_key, owner_id, call.message.message_id, expected_user_id=owner_id)

@bot.callback_query_handler(func=lambda call: call.data == "cback" or call.data.startswith("cback|"))
def casino_back(call):
    if "|" in call.data:
        owner_id = check_panel_owner(call)
        if owner_id is None:
            return
    else:
        owner_id = call.from_user.id
    bot.answer_callback_query(call.id)
    # اگر کاربر از صفحه‌ی «ارسال مبلغ» برگشت، منتظرماندن برای مبلغ را لغو کن
    with _next_step_lock:
        pending = _next_step_pending.pop((call.message.chat.id, owner_id), None)
    if pending and pending.get("timer"):
        try:
            pending["timer"].cancel()
        except Exception:
            pass
    safe_edit_message(
        CASINO_MENU_TEXT,
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=casino_games_keyboard(owner_id), parse_mode="HTML"
    )

def cancel_casino_timer(msg_id):
    with casino_lock:
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

def check_casino_timeout(msg_id):
    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game or game["player2"] is not None:
            return
        if update_diamonds(game["player1"]["id"], game["bet"]) is None:
            logging.error(f"بازگرداندن مبلغ کازینو {msg_id} انجام نشد")
            return
        del active_casino_games[msg_id]
        if msg_id in casino_timers:
            del casino_timers[msg_id]

    markup = None
    safe_edit_message(
        f'{_gift_emoji(5825656411517886523, "⏰")} زمان تموم شد {_gift_emoji(5938490450833838343, "⌛")}\n'
        f'{_gift_emoji(5830338333892418460, "💰")} {game["bet"]} الماس {_gift_emoji(5940725397195853882, "💎")} به سازنده برگردونده شد.',
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup, parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cbet|"))
def casino_bet_select(call):
    if _feature_gate(call, "casino"):
        return
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    _, game_key, amount, _owner = call.data.split("|")
    if not game_on(game_key):
        return
    amount = int(amount)
    user = call.from_user  # == owner_id, تضمین‌شده توسط چک بالا

    if not get_user(user.id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    if get_balance(user.id) < amount:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    reserved = update_diamonds(user.id, -amount)
    if reserved is None:
        bot.answer_callback_query(call.id, "💎 موجودی کافی نیست یا رزرو شرط انجام نشد.", show_alert=True)
        return

    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("لغو ⊗", callback_data=f"ccancel|{call.message.message_id}"),
        colored_button("پیوستن ✓", callback_data="cjoin"),
    )

    text = (
        f"◈ ━━━━━ 𝗕𝗲𝘁 ━━━━━━ ◈\n"
        f"☻ | بازی : {CASINO_GAME_NAMES[game_key]}\n\n"
        f"$ | مقدار الماس : {amount}\n"
        f"♛ | سازنده : {display_name_with_tag(user.id, get_display_name(user))}\n"
        f"◈ ━━━━━ 𝗕𝗲𝘁 ━━━━━━ ◈"
    )
    safe_edit_message(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    msg_id = call.message.message_id

    with casino_lock:
        active_casino_games[msg_id] = {
            "game": game_key,
            "bet": amount,
            "chat_id": call.message.chat.id,
            "player1": {"id": user.id, "name": get_display_name(user)},
            "player2": None,
            "score1": None,
            "score2": None,
            "vs_bot": False,
        }
        timer = threading.Timer(JOIN_TIMEOUT_SECONDS, check_casino_timeout, args=[msg_id])
        casino_timers[msg_id] = timer
        timer.start()

@bot.callback_query_handler(func=lambda call: call.data.startswith("ccancel|"))
def casino_cancel(call):
    msg_id = int(call.data.split("|")[1])
    user_id = call.from_user.id

    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game:
            bot.answer_callback_query(call.id)
            return
        if game["player2"] is not None:
            bot.answer_callback_query(call.id)
            return
        if user_id != game["player1"]["id"]:
            bot.answer_callback_query(call.id)
            return

        if update_diamonds(game["player1"]["id"], game["bet"]) is None:
            logging.error(f"بازگرداندن شرط کازینو {msg_id} انجام نشد")
            return
        del active_casino_games[msg_id]
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

    bot.answer_callback_query(call.id)
    markup = None
    safe_edit_message(
        f'{_gift_emoji(5818704981179505821, "🎮")} بازی لغو شد. {_gift_emoji(5819154526816444042, "❌")}\n'
        f'{_gift_emoji(5830338333892418460, "💰")} {game["bet"]} الماس {_gift_emoji(5940725397195853882, "💎")} برگردونده شد.',
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup, parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "casinoback")
def casino_back_to_list(call):
    bot.answer_callback_query(call.id)
    safe_edit_message(
        CASINO_MENU_TEXT,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=casino_games_keyboard(call.from_user.id),
        parse_mode="HTML"
    )

# ================== معدن الماس (مین‌زدن تکی) ==================
MINE_DIAMOND_EMOJI_ID = 5940725397195853882  # الماس
MINE_BOMB_EMOJI_ID = 5929177175029718803     # بمب

def _mine_title():
    e = _gift_emoji
    return f'{e(5818704981179505821, "⛏")} معدن الماس{e(5929177175029718803, "💣")}'

def mine_board_text(bet, found, multiplier, footer_line=None):
    e = _gift_emoji
    payout = int(bet * multiplier)
    not_found = (MINE_GRID_SIZE - 1) - found
    mult_str = f"{multiplier:.2f}x" if multiplier > 0 else ""
    if footer_line is None:
        footer_line = f'{e(5940725397195853882, "💎")} الماس هارو پیدا کن{e(5820903824046432799, "👇")}'
    return (
        f'{_mine_title()}\n\n'
        f'{e(5823445637231814311, "💰")} مبلغ ورودی : {bet:,}\n'
        f'{e(5940582340425159463, "💵")} مبلغ دریافتی : {payout:,} ({mult_str})\n\n'
        f'{e(5940396943866859681, "✅")} الماس های پیدا شده : {found}\n'
        f'{e(5942718468179628637, "❔")} الماس های پیدا نشده : {not_found}\n\n'
        f'{footer_line}'
    )

def mine_board_markup(revealed, owner_id):
    markup = types.InlineKeyboardMarkup()
    for row in range(3):
        buttons = []
        for col in range(3):
            idx = row * 3 + col
            if idx in revealed:
                buttons.append(colored_button("\u2800", callback_data=f"mine|{idx}|{owner_id}",
                                              icon_custom_emoji_id=str(MINE_DIAMOND_EMOJI_ID)))
            else:
                buttons.append(colored_button(" ", callback_data=f"mine|{idx}|{owner_id}"))
        markup.row(*buttons)
    markup.row(colored_button("برداشت الان", callback_data=f"minecashout|{owner_id}",
                              icon_custom_emoji_id="5830144944399981619"))
    return markup

def mine_final_markup(game, reveal_bomb=False):
    """صفحه‌ی ۳×۳ بعد از پایان بازی روی پیام می‌مونه (مثل دینامیت): الماس‌های باز‌شده
    می‌مونن و فقط در صورت انفجار، دکمه‌ی بمب قرمز می‌شه. دکمه‌ها بی‌اثرن."""
    markup = types.InlineKeyboardMarkup()
    for row in range(3):
        buttons = []
        for col in range(3):
            idx = row * 3 + col
            if reveal_bomb and idx == game["bomb_index"]:
                buttons.append(colored_button("\u2800", callback_data="noop", style="danger",
                                              icon_custom_emoji_id=str(MINE_BOMB_EMOJI_ID)))
            elif idx in game["revealed"]:
                buttons.append(colored_button("\u2800", callback_data="noop",
                                              icon_custom_emoji_id=str(MINE_DIAMOND_EMOJI_ID)))
            else:
                buttons.append(colored_button(" ", callback_data="noop"))
        markup.row(*buttons)
    return markup

@bot.message_handler(func=lambda m: text_is(m, "معدن الماس", "معدن"))
def mine_panel_entry(message):
    if _feature_gate(message, "mine"):
        return
    if not get_user(message.from_user.id):
        bot.reply_to(message, _start_first_err())
        return
    e = _gift_emoji
    panel_id = show_or_edit_panel(
        message, message.from_user.id, "mine",
        f'{_mine_title()}\n\n'
        f'{e(5816913107938712568, "✏️")} مقدار الماسی که میخواید شرط ببندید رو ارسال کنید{e(5820903824046432799, "👇")}',
        parse_mode="HTML",
    )
    if panel_id:
        # مرحله ورودی با خود پنل ثبت می‌شود تا نتیجه هم همان پیام را ادیت کند.
        class _PanelMessage:
            pass
        panel = _PanelMessage()
        panel.chat = message.chat
        panel.message_id = panel_id
        from types import SimpleNamespace
        panel.from_user = SimpleNamespace(id=0, is_bot=True)
        register_timed_next_step_handler(panel, mine_bet_step, message.from_user.id, panel_id, expected_user_id=message.from_user.id)

def mine_bet_step(message, expected_user_id, prompt_msg_id):
    if not require_reply_to_panel(message, prompt_msg_id, _casino_err("برای شروع معدن، روی همین پنل ریپلای کن و مبلغ را بفرست")):
        register_timed_next_step_handler(message, mine_bet_step, expected_user_id, prompt_msg_id, expected_user_id=expected_user_id)
        return
    if message.from_user.id != expected_user_id:
        # پیام از یه نفر دیگه بود؛ نادیده می‌گیریم ولی منتظر پیام خودِ کاربر می‌مونیم
        register_timed_next_step_handler(message, mine_bet_step, expected_user_id, prompt_msg_id, expected_user_id=expected_user_id)
        return
    amount = parse_amount(message.text)
    if amount is None:
        safe_edit_message(_casino_err("مبلغ نامعتبره. روی همین پنل ریپلای کن و یه عدد درست بفرست (مثلاً 100000 یا 100k)"), message.chat.id, prompt_msg_id, reply_markup=None)
        register_timed_next_step_handler(message, mine_bet_step, expected_user_id, prompt_msg_id, expected_user_id=expected_user_id)
        return
    if get_balance(expected_user_id) < amount:
        safe_edit_message(_casino_err("موجودی کافی نیست. روی همین پنل ریپلای کن و مبلغ دیگه‌ای بفرست"), message.chat.id, prompt_msg_id, reply_markup=None)
        register_timed_next_step_handler(message, mine_bet_step, expected_user_id, prompt_msg_id, expected_user_id=expected_user_id)
        return

    update_diamonds(expected_user_id, -amount)
    bomb_index = random.randint(0, MINE_GRID_SIZE - 1)
    text = mine_board_text(amount, 0, 0.0)
    markup = mine_board_markup(set(), expected_user_id)
    # همون پیام اول (که مبلغ رو ازش پرسیده بودیم) ویرایش میشه، پیام جدید فرستاده نمیشه
    safe_edit_message(text, message.chat.id, prompt_msg_id, reply_markup=markup, parse_mode="HTML")
    active_mine_games[prompt_msg_id] = {
        "chat_id": message.chat.id,
        "owner_id": expected_user_id,
        "bet": amount,
        "bomb_index": bomb_index,
        "revealed": set(),
        "diamonds_found": 0,
    }

@bot.callback_query_handler(func=lambda call: call.data == "noop")
def noop_callback(call):
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mine|"))
def mine_cell_reveal(call):
    with mine_lock:
        _mine_cell_reveal_locked(call)

def _mine_cell_reveal_locked(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    game = active_mine_games.get(call.message.message_id)
    if not game:
        bot.answer_callback_query(call.id)
        return
    idx = int(call.data.split("|")[1])
    if idx in game["revealed"]:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)

    if idx == game["bomb_index"]:
        del active_mine_games[call.message.message_id]
        e = _gift_emoji
        not_found = (MINE_GRID_SIZE - 1) - game['diamonds_found']
        text = (
            f'{_mine_title()}\n\n'
            f'{e(5823445637231814311, "💰")} مبلغ ورودی : {game["bet"]:,} {e(5940725397195853882, "💎")}\n'
            f'{e(5940582340425159463, "💵")} مبلغ دریافتی : 0 {e(5940725397195853882, "💎")} (0.00x)\n\n'
            f'{e(5940396943866859681, "✅")} الماس های پیدا شده : {game["diamonds_found"]}\n'
            f'{e(5942718468179628637, "❔")} الماس های پیدا نشده : {not_found}\n\n'
            f'{e(5884330316230827477, "💥")} بووووممم{e(5929177175029718803, "💣")}\n\n'
            f'{e(5856946687083290613, "❌")} باختی کل الماس شرط از دست رفت {e(5848202125078699135, "💸")}'
        )
        safe_edit_message(text, call.message.chat.id, call.message.message_id,
                          reply_markup=mine_final_markup(game, reveal_bomb=True), parse_mode="HTML")
        return

    game["revealed"].add(idx)
    game["diamonds_found"] += 1
    multiplier = game["diamonds_found"] * MINE_MULTIPLIER_STEP

    if game["diamonds_found"] >= MINE_GRID_SIZE - 1:
        # همه‌ی خونه‌های غیر بمب پیدا شدن؛ برد کامل و برداشت خودکار
        payout = int(game["bet"] * multiplier)
        update_diamonds(owner_id, payout)
        del active_mine_games[call.message.message_id]
        e = _gift_emoji
        text = mine_board_text(
            game["bet"], game["diamonds_found"], multiplier,
            footer_line=(f'{e(5830338333892418460, "💰")} بنازم همه‌ی الماس‌ها رو پیدا کردی مبلغ {payout:,} '
                         f'الماس{e(5940725397195853882, "💎")} به حسابت اضافه شد.{e(5830144944399981619, "✅")}')
        )
        safe_edit_message(text, call.message.chat.id, call.message.message_id,
                          reply_markup=mine_final_markup(game), parse_mode="HTML")
        return

    text = mine_board_text(game["bet"], game["diamonds_found"], multiplier)
    markup = mine_board_markup(game["revealed"], owner_id)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("minecashout|"))
def mine_cash_out(call):
    with mine_lock:
        _mine_cash_out_locked(call)

def _mine_cash_out_locked(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    game = active_mine_games.get(call.message.message_id)
    if not game or game["diamonds_found"] == 0:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    multiplier = game["diamonds_found"] * MINE_MULTIPLIER_STEP
    payout = int(game["bet"] * multiplier)
    update_diamonds(owner_id, payout)
    del active_mine_games[call.message.message_id]
    e = _gift_emoji
    text = mine_board_text(
        game["bet"], game["diamonds_found"], multiplier,
        footer_line=(f'{e(5830338333892418460, "💰")} برداشت انجام شد {payout:,} '
                     f'الماس{e(5940725397195853882, "💎")} به حسابت اضافه شد.{e(5830144944399981619, "✅")}')
    )
    safe_edit_message(text, call.message.chat.id, call.message.message_id,
                      reply_markup=mine_final_markup(game), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("ccustom|"))
def casino_custom_amount_prompt(call):
    if _feature_gate(call, "casino"):
        return
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    game_key = call.data.split("|")[1]
    if not game_on(game_key):
        return
    if not get_user(owner_id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "✏️ لطفاً مبلغ شرط رو به عدد بفرست (مثلاً 250 یا 250k):",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None
    )
    register_timed_next_step_handler(call.message, casino_custom_amount_step, game_key, owner_id, call.message.message_id, expected_user_id=owner_id)

def casino_custom_amount_step(message, game_key, expected_user_id, panel_msg_id):
    if message.from_user.id != expected_user_id:
        register_timed_next_step_handler(message, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return
    if not require_reply_to_panel(message, panel_msg_id, _casino_err("برای ثبت مبلغ، روی همین پیام کازینو ریپلای کن و مبلغ را بفرست")):
        register_timed_next_step_handler(message, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return

    amount = parse_amount(message.text)
    if amount is None:
        safe_edit_message(_casino_err("مبلغ نامعتبره. روی همین پنل ریپلای کن و مبلغ درست بفرست؛ مثال: 250 یا 250k"), message.chat.id, panel_msg_id, reply_markup=None)
        register_timed_next_step_handler(message, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return

    if amount <= 0:
        safe_edit_message(_casino_err("مبلغ باید بزرگتر از صفر باشه. روی همین پنل ریپلای کن و دوباره بفرست"), message.chat.id, panel_msg_id, reply_markup=None)
        register_timed_next_step_handler(message, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id, expected_user_id=expected_user_id)
        return

    user = message.from_user
    if get_balance(user.id) < amount:
        safe_edit_message(_casino_err("موجودی الماس شما کافی نیست"), message.chat.id, panel_msg_id, reply_markup=None)
        return

    update_diamonds(user.id, -amount)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        colored_button("پیوستن", callback_data="cjoin", icon_custom_emoji_id="5938109560249127910"),
        colored_button("لغو", callback_data=f"ccancel|{panel_msg_id}", icon_custom_emoji_id="5819154526816444042"),
    )

    e = _gift_emoji
    text = (
        f'{casino_game_title(game_key)}\n\n'
        f'{e(5823445637231814311, "💰")} مقدار الماس : {amount} الماس {e(5940725397195853882, "💎")}\n\n'
        f'{e(5843973755545590553, "👤")} سازنده : {safe_html_name(user)}'
    )
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    safe_edit_message(text, chat_id=message.chat.id, message_id=panel_msg_id, reply_markup=markup)
    msg_id = panel_msg_id

    with casino_lock:
        active_casino_games[msg_id] = {
            "game": game_key,
            "bet": amount,
            "chat_id": message.chat.id,
            "player1": {"id": user.id, "name": get_display_name(user), "html_name": safe_html_name(user)},
            "player2": None,
            "score1": None,
            "score2": None,
            "vs_bot": False,
        }
        timer = threading.Timer(JOIN_TIMEOUT_SECONDS, check_casino_timeout, args=[msg_id])
        casino_timers[msg_id] = timer
        timer.start()

@bot.callback_query_handler(func=lambda call: call.data == "cjoin")
def casino_join(call):
    msg_id = call.message.message_id
    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game:
            bot.answer_callback_query(call.id)
            return
        if game["player2"] is not None:
            bot.answer_callback_query(call.id)
            return

        user = call.from_user
        if user.id == game["player1"]["id"]:
            bot.answer_callback_query(call.id)
            return
        if not get_user(user.id):
            bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
            return
        if get_balance(user.id) < game["bet"]:
            bot.answer_callback_query(call.id)
            return

        reserved = update_diamonds(user.id, -game["bet"])
        if reserved is None:
            bot.answer_callback_query(call.id)
            return
        game["player2"] = {"id": user.id, "name": get_display_name(user), "html_name": safe_html_name(user)}
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

    bot.answer_callback_query(call.id)
    e = _gift_emoji
    safe_edit_message(
        f'{casino_game_title(game["game"])}\n\n'
        f'{e(5823445637231814311, "💰")} مقدار الماس : {game["bet"]} الماس{e(5940725397195853882, "💎")}\n'
        f'{e(5843973755545590553, "👤")} سازنده : {casino_player_html(game["player1"])}\n'
        f'{e(5859432880442187580, "👥")} شرکت کننده : {casino_player_html(game["player2"])}\n\n'
        f'{e(5857454223368657979, "🎯")} {CASINO_SEND_PHRASES[game["game"]]} {e(5820903824046432799, "👇")}',
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=None, parse_mode="HTML"
    )

@bot.message_handler(content_types=["dice"])
def handle_dice_throw(message):
    to_finalize = None
    bot_throw_needed = None

    with casino_lock:
        for msg_id, game in active_casino_games.items():
            if game["chat_id"] != message.chat.id or game["player2"] is None:
                continue
            emoji = CASINO_GAMES[game["game"]]
            if message.dice.emoji != emoji:
                continue

            # تاس/دارت/بسکتبال/فوتبال/بولینگ/اسلات فقط با ریپلای مستقیم
            # به همان پیام پنل بازی ثبت می‌شود. پیام عادی کاملاً نادیده گرفته می‌شود.
            reply = getattr(message, "reply_to_message", None)
            if not reply or reply.message_id != msg_id:
                continue
            reply_sender = getattr(reply, "from_user", None)
            if not reply_sender or not getattr(reply_sender, "is_bot", False):
                continue

            user_id = message.from_user.id
            vs_bot = game.get("vs_bot", False)

            if user_id == game["player1"]["id"] and game["score1"] is None:
                game["score1"] = message.dice.value
            elif (not vs_bot) and user_id == game["player2"]["id"] and game["score2"] is None:
                game["score2"] = message.dice.value
            else:
                continue

            if vs_bot and game["score1"] is not None and game["score2"] is None:
                bot_throw_needed = (msg_id, game["chat_id"], emoji)
            elif game["score1"] is not None and game["score2"] is not None:
                to_finalize = msg_id
            break

    if bot_throw_needed:
        b_msg_id, b_chat_id, b_emoji = bot_throw_needed
        bot_dice = bot.send_dice(b_chat_id, emoji=b_emoji)
        with casino_lock:
            game = active_casino_games.get(b_msg_id)
            if game and game["score2"] is None:
                game["score2"] = bot_dice.dice.value
                if game["score1"] is not None and game["score2"] is not None:
                    to_finalize = b_msg_id

    if to_finalize:
        finalize_casino_game(to_finalize)

def finalize_casino_game(msg_id):
    with casino_lock:
        game = active_casino_games.pop(msg_id, None)
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]
    if not game:
        return

    emoji = CASINO_GAMES[game["game"]]
    chat_id = game["chat_id"]
    player1, player2 = game["player1"], game["player2"]
    score1, score2 = game["score1"], game["score2"]
    bet = game["bet"]
    vs_bot = game.get("vs_bot", False)

    score1 = score1 if score1 is not None else 0
    score2 = score2 if score2 is not None else 0
    result_markup = None

    if score1 == score2:
        update_diamonds(player1["id"], bet)
        if not vs_bot:
            update_diamonds(player2["id"], bet)
        e = _gift_emoji
        p1_display = casino_player_html(player1)
        p2_display = casino_player_html(player2)
        result_text = (
            f'{e(5818704981179505821, "🎮")} شرط تموم شد {casino_game_icon(game["game"])}\n\n'
            f'{e(5823445637231814311, "💰")} مبلغ: {bet} الماس{e(5940725397195853882, "💎")}\n\n'
            f'{e(5816571237131885795, "⚔️")} {p1_display} در برابر {p2_display}\n'
            f'{e(5818716826699307883, "🤝")} بازی مساوی شد\n'
            f'{e(5938348905891632091, "📊")} امتیاز {p1_display} : {score1}\n'
            f'{e(5938348905891632091, "📊")} امتیاز {p2_display} : {score2}\n\n'
            f'{e(5823445637231814311, "💰")} مبلغ شرط به هر دو نفر برگشت داده شد. {e(5830144944399981619, "✅")}'
        )
    else:
        if score1 > score2:
            winner, loser, w_score, l_score = player1, player2, score1, score2
        else:
            winner, loser, w_score, l_score = player2, player1, score2, score1

        total_pot = bet if vs_bot else bet * 2
        winner_id = winner["id"]

        if winner_id is not None:
            final_amount, tax, loan_repay, tax_rate_used, loan_rate_used = calculate_payout(winner_id, total_pot, context="casino")
            update_diamonds(winner_id, final_amount)
            _credit_tax(chat_id, tax)
            if get_user(winner_id):
                register_casino_win(winner_id, chat_id, winner["name"], amount_won=final_amount)
        else:
            final_amount = None
            loan_repay = 0
            tax = int(total_pot * TAX_RATE)
            tax_rate_used = TAX_RATE
            loan_rate_used = 0

        e = _gift_emoji
        p1_display = casino_player_html(player1)
        p2_display = casino_player_html(player2)
        winner_display = casino_player_html(winner)
        loser_display = casino_player_html(loser)
        result_text = (
            f'{e(5818704981179505821, "🎮")} بازی تموم شد {casino_game_icon(game["game"])}\n\n'
            f'{e(5816571237131885795, "⚔️")} {p1_display} در برابر {p2_display}\n\n'
            f'{e(6014651259057873388, "🏆")} برنده با {w_score} امتیاز : {winner_display}\n'
            f'{e(5839270298205035832, "🥈")} بازنده با {l_score} امتیاز : {loser_display}'
        )
        result_markup = types.InlineKeyboardMarkup()
        win_amount = final_amount if final_amount is not None else 0
        result_markup.row(colored_button(f"مبلغ برد: {win_amount:,}", callback_data="bet_result_noop", icon_custom_emoji_id="5823445637231814311"))
        result_markup.row(colored_button(f"مالیات: {tax:,}", callback_data="bet_result_noop", icon_custom_emoji_id="5889002570633977838"))

    safe_edit_message(result_text, chat_id=chat_id, message_id=msg_id, reply_markup=result_markup, parse_mode="HTML")


# ================== لیدربرد جهانی میویی ==================
RANK_PAGE_SIZE = 5


def _rank_rows():
    try:
        response = (supabase.table("users").select("user_id, username, diamonds")
                    .order("diamonds", desc=True).execute())
        return response.data or []
    except Exception as e:
        logging.error(f"خطا در دریافت لیدربرد: {e}")
        return []


def _rank_name(row):
    # ستون username در این بات همان نام اکانت ذخیره‌شده است؛ یوزرنیم تلگرام نمایش داده نمی‌شود.
    return (row.get("username") or f"کاربر {row.get('user_id', '')}").strip()


def _user_global_rank(user_id, rows=None):
    rows = rows if rows is not None else _rank_rows()
    for idx, row in enumerate(rows, 1):
        if int(row.get("user_id", 0)) == int(user_id):
            return idx
    return "نامشخص"


RANK_EMOJI_IDS = {1: 5832692422647226240, 2: 5834620746999012948, 3: 5832346686369832003}
RANK_DEFAULT_EMOJI_ID = 5836866392124563486
RANK_FALLBACK_EMOJIS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _rank_emoji(idx):
    return _gift_emoji(RANK_EMOJI_IDS.get(idx, RANK_DEFAULT_EMOJI_ID), RANK_FALLBACK_EMOJIS.get(idx, "🏅"))


def _rank_header(subtitle):
    e = _gift_emoji
    return f'{e(5818913299978263825, "🏆")} رتبه بندی جهانی {e(5938398023137628324, "🌍")}\n\n{subtitle}\n\n'


def _rank_menu_text():
    e = _gift_emoji
    return (
        f'{e(5818913299978263825, "🏆")} رتبه بندی جهانی {e(5938398023137628324, "🌍")}\n\n'
        f'لطفا نوع لیدربرد را انتخاب کنید{e(5820903824046432799, "👇")}\n\n'
        f'{e(5940725397195853882, "💎")} رتبه بندی ثروتمند ترین ها\n\n'
        f'{e(5927029738626359516, "🎁")} رتبه بندی گیفت ها'
    )


def _rank_menu_markup(uid):
    m = types.InlineKeyboardMarkup()
    m.row(colored_button("رتبه بندی ثروتمند ترین ها", callback_data=f"ranktype|wealth|{uid}", icon_custom_emoji_id="5940725397195853882"))
    m.row(colored_button("رتبه بندی گیفت ها", callback_data=f"ranktype|gifts|{uid}", icon_custom_emoji_id="5927029738626359516"))
    return m


def _rank_page_text(page, user_id):
    e = _gift_emoji
    rows = _rank_rows()
    total = len(rows)
    max_page = max(1, (total + RANK_PAGE_SIZE - 1) // RANK_PAGE_SIZE)
    page = max(1, min(int(page), max_page))
    start = (page - 1) * RANK_PAGE_SIZE
    page_rows = rows[start:start + RANK_PAGE_SIZE]
    text = _rank_header(f'{e(5938515112536051725, "💎")} رتبه بندی ثروتمند ترین ها')
    for idx, row in enumerate(page_rows, start + 1):
        name = html_escape_keep_emoji(display_name_with_tag(row.get("user_id"), _rank_name(row)))
        diamonds = int(row.get("diamonds") or 0)
        text += f'{_rank_emoji(idx)} {name}\n'
        text += f'┘─ {e(5823445637231814311, "💰")} الماس ها : {diamonds:,} {e(5940725397195853882, "💎")}\n\n'
    my_rank = _user_global_rank(user_id, rows)
    text += f'{e(5845858434439716957, "🎖")} رتبه شما : {my_rank}'
    return text, total


def _gift_rank_rows():
    """رتبه‌بندی دارندگان گیفت بر اساس ارزش کل گیفت‌های متعلق به آن‌ها."""
    try:
        gifts = (supabase.table("premium_gifts")
                 .select("owner_id,current_value")
                 .not_.is_("owner_id", "null").execute().data or [])
        agg = {}
        for gift in gifts:
            try:
                uid = int(gift.get("owner_id"))
            except (TypeError, ValueError):
                continue
            row = agg.setdefault(uid, {"user_id": uid, "gift_count": 0, "gift_value": 0})
            row["gift_count"] += 1
            row["gift_value"] += int(gift.get("current_value") or 0)
        # نام ذخیره‌شده‌ی کاربران را یک‌بار می‌خوانیم تا برای هر کاربر query جداگانه نزنیم.
        try:
            user_rows = supabase.table("users").select("user_id,username").execute().data or []
            user_names = {int(u.get("user_id")): (u.get("username") or "") for u in user_rows if u.get("user_id") is not None}
        except Exception:
            user_names = {}
        for uid, row in agg.items():
            row["username"] = user_names.get(uid) or f"کاربر {uid}"
        return sorted(agg.values(), key=lambda r: (r["gift_value"], r["gift_count"], r["user_id"]), reverse=True)
    except Exception as e:
        logging.error("خطا در دریافت لیدربرد گیفت‌ها: %s", e)
        return []


def _gift_rank_page_text(page, user_id):
    e = _gift_emoji
    rows = _gift_rank_rows()
    total = len(rows)
    max_page = max(1, (total + RANK_PAGE_SIZE - 1) // RANK_PAGE_SIZE)
    page = max(1, min(int(page), max_page))
    start = (page - 1) * RANK_PAGE_SIZE
    page_rows = rows[start:start + RANK_PAGE_SIZE]
    subtitle = f'{e(5938515112536051725, "💎")} رتبه بندی ثروتمند ترین کلکسیونر های گیفت{e(5927029738626359516, "🎁")}'
    text = _rank_header(subtitle)
    for idx, row in enumerate(page_rows, start + 1):
        name = html_escape_keep_emoji(display_name_with_tag(row.get("user_id"), _rank_name(row)))
        count = int(row.get("gift_count") or 0)
        value = int(row.get("gift_value") or 0)
        text += f'{_rank_emoji(idx)} {name}\n'
        text += f'┘┘─ {e(5927029738626359516, "🎁")} تعداد کل گیفت ها: {count}{e(5830338333892418460, "🎉")}\n'
        text += f'┘─ {e(5823445637231814311, "💰")} ارزش کل گیفت ها : {value:,} {e(5940725397195853882, "💎")}\n\n'
    my_rank = next((i for i, row in enumerate(rows, 1) if int(row.get("user_id", 0)) == int(user_id)), "نامشخص")
    text += f'{e(5845858434439716957, "🎖")} رتبه شما : {my_rank}'
    return text, total


def _rank_pagination_markup(page, total, user_id, mode="wealth"):
    markup = types.InlineKeyboardMarkup()
    max_page = max(1, (total + RANK_PAGE_SIZE - 1) // RANK_PAGE_SIZE)
    prefix = "rankpage" if mode == "wealth" else "giftrankpage"
    nav = []
    if page > 1:
        nav.append(colored_button("قبلی", callback_data=f"{prefix}|{page - 1}|{user_id}", icon_custom_emoji_id="5845874566336880660"))
    if page < max_page:
        nav.append(colored_button("بعدی", callback_data=f"{prefix}|{page + 1}|{user_id}", icon_custom_emoji_id="5843732253829503840"))
    if nav:
        markup.row(*nav)
    markup.row(back_button(f"rankback|{user_id}"))
    return markup


def _send_rank_menu(message):
    if _feature_gate(message, "rank"):
        return
    show_or_edit_panel(message, message.from_user.id, "rank", _rank_menu_text(), _rank_menu_markup(message.from_user.id), parse_mode="HTML")


def _show_rank_menu_callback(call, uid):
    safe_edit_message(_rank_menu_text(), call.message.chat.id, call.message.message_id,
                      reply_markup=_rank_menu_markup(uid), parse_mode="HTML")


@bot.message_handler(commands=["rank", "رتبه‌بندی"])
def cmd_rank(message):
    _send_rank_menu(message)


@bot.message_handler(func=lambda m: text_is(m, "رنک", "رتبه بندی", "رتبه‌بندی", "لیدربرد", "لیدر برد"))
def text_rank(message):
    _send_rank_menu(message)


@bot.callback_query_handler(func=lambda call: call.data.startswith("ranktype|"))
def rank_type_callback(call):
    if _feature_gate(call, "rank"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        _, mode, _uid = call.data.split("|", 2)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    if mode == "wealth":
        rows = _rank_rows()
        if not rows:
            safe_edit_message("هنوز کاربری ثبت‌نام نکرده.", call.message.chat.id, call.message.message_id,
                              reply_markup=_rank_pagination_markup(1, 0, uid, "wealth"))
            return
        text, total = _rank_page_text(1, uid)
        safe_edit_message(text, call.message.chat.id, call.message.message_id,
                          reply_markup=_rank_pagination_markup(1, total, uid, "wealth"), parse_mode="HTML")
    elif mode == "gifts":
        rows = _gift_rank_rows()
        if not rows:
            safe_edit_message(_rank_header(f'{_gift_emoji(5938515112536051725, "💎")} رتبه بندی ثروتمند ترین کلکسیونر های گیفت{_gift_emoji(5927029738626359516, "🎁")}') + "هنوز گیفتی ثبت نشده.",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=_rank_pagination_markup(1, 0, uid, "gifts"), parse_mode="HTML")
            return
        text, total = _gift_rank_page_text(1, uid)
        safe_edit_message(text, call.message.chat.id, call.message.message_id,
                          reply_markup=_rank_pagination_markup(1, total, uid, "gifts"), parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("rankback|"))
def rank_back_callback(call):
    if _feature_gate(call, "rank"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    bot.answer_callback_query(call.id)
    _show_rank_menu_callback(call, uid)


@bot.callback_query_handler(func=lambda call: call.data.startswith("rankpage|"))
def rank_page_callback(call):
    if _feature_gate(call, "rank"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        _, page_s, _uid = call.data.split("|", 2)
        page = max(1, int(page_s))
    except Exception:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    text, total = _rank_page_text(page, uid)
    safe_edit_message(text, call.message.chat.id, call.message.message_id,
                      reply_markup=_rank_pagination_markup(page, total, uid, "wealth"), parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("giftrankpage|"))
def gift_rank_page_callback(call):
    if _feature_gate(call, "rank"):
        return
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        _, page_s, _uid = call.data.split("|", 2)
        page = max(1, int(page_s))
    except Exception:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    text, total = _gift_rank_page_text(page, uid)
    safe_edit_message(text, call.message.chat.id, call.message.message_id,
                      reply_markup=_rank_pagination_markup(page, total, uid, "gifts"), parse_mode="HTML")

# ================== موجودی ==================
@bot.message_handler(commands=["balance", "موجودی"])
def cmd_balance(message):
    show_balance(message)

@bot.message_handler(func=lambda m: text_is(m, "موجودی"))
def text_balance(message):
    show_balance(message)

def _glass_balance_button(balance):
    """دکمه‌ی آبی با عدد موجودی (با جداکننده‌ی هزارگان) و ایموجی پرمیوم الماس."""
    return colored_button(f"{int(balance or 0):,}", callback_data="pending",
                          style="primary", icon_custom_emoji_id="5940725397195853882")

def show_balance(message):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, _start_first_err())
        return

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        if not get_user(target_id):
            bot.reply_to(message, "این کاربر هنوز /start نزده، نمی‌تونم موجودیش رو ببینم.")
            return
        target_name = get_display_name(message.reply_to_message.from_user)
        balance = get_balance(target_id)
        e = _gift_emoji
        text = f'{e(5823445637231814311, "💰")} موجودی الماس{e(5940725397195853882, "💎")} {html_escape_keep_emoji(target_name)}'
        markup = types.InlineKeyboardMarkup()
        markup.add(_glass_balance_button(balance))
        show_or_edit_panel(message, user_id, "balance", text, markup, parse_mode="HTML")
    else:
        balance = get_balance(user_id)
        e = _gift_emoji
        text = f'{e(5823445637231814311, "💰")} موجودی الماس{e(5940725397195853882, "💎")} شما'
        markup = types.InlineKeyboardMarkup()
        markup.add(_glass_balance_button(balance))
        show_or_edit_panel(message, user_id, "balance", text, markup, parse_mode="HTML")


# ================== تنظیمات پویا + پنل مدیریت کامل ==================
# تنظیمات تو جدول bot_settings (key text primary key, value text) ذخیره می‌شن و بعد از ری‌استارت
# دوباره اعمال می‌شن. اگه جدول نباشه، تغییرات تا ری‌استارت بعدی اعمال می‌شن.
import json as _json
import copy as _copy

_SETTINGS = {}
_SETTINGS_LOCK = threading.Lock()

def _settings_load_all():
    try:
        rows = supabase.table("bot_settings").select("key,value").execute().data or []
    except Exception as e:
        logging.error(f"خواندن bot_settings ناموفق بود (جدول ساخته نشده؟): {e}")
        return False
    for r in rows:
        try:
            _SETTINGS[r["key"]] = _json.loads(r["value"])
        except Exception:
            continue
    return True

def setting_save(key, value):
    """مقدار رو تو حافظه و دیتابیس ذخیره می‌کنه. خروجی False یعنی ذخیره‌ی دائمی انجام نشد."""
    with _SETTINGS_LOCK:
        _SETTINGS[key] = value
    try:
        supabase.table("bot_settings").upsert({"key": key, "value": _json.dumps(value, ensure_ascii=False)}).execute()
        return True
    except Exception as e:
        logging.error(f"ذخیره‌ی bot_settings({key}) ناموفق بود: {e}")
        return False

# ---------- بخش‌ها (روشن/مخفی) ----------
FEATURE_LABELS = {
    "casino": "کازینو", "bet": "شرطبندی", "dynamite": "دینامیت", "mine": "معدن",
    "jewelry": "جواهری", "bank": "بانک", "gift": "گیفت", "rank": "رنک (لیدربرد)", "tags": "لقب‌ها",
    "heist": "دزدی", "heist_gear": "نیاز به وسایل دزدی", "market": "مارکت", "drill": "حفاری",
}

def feature_on(key):
    return bool(_SETTINGS.get("feat_" + key, True))

def game_on(game_key):
    return bool(_SETTINGS.get("game_" + game_key, True))

def _feature_gate(obj, key):
    """اگه بخش مخفی باشه True برمی‌گردونه (پیام: جواب خطا، دکمه: بی‌صدا نادیده)."""
    if feature_on(key):
        return False
    try:
        if isinstance(obj, types.CallbackQuery):
            bot.answer_callback_query(obj.id)
        else:
            bot.reply_to(obj, _casino_err("این بخش فعلاً غیرفعاله"))
    except Exception:
        pass
    return True

# ---------- تنظیمات عددی ----------
_ADM_DEFS = {}
_ADM_DEFAULTS = {}

def _S(key, section, label, kind, g, **kw):
    _ADM_DEFS[key] = dict(key=key, section=section, label=label, kind=kind, g=g, **kw)

HEIST_POLICE_SECONDS = 90
HEIST_CAMERAS = 5
HEIST_CAMERA_SECONDS = 4
HEIST_GEMS = 6
HEIST_GEM_SECONDS = 5
HEIST_FINE = 25_000
HEIST_PROFIT = 100_000
HEIST_RING_REWARD = 2
HEIST_COOLDOWN_SECONDS = 28800
HEIST_PRICE_HACKER = 25_000
HEIST_PRICE_THIEF = 25_000
_S("heist_police", "heist", "زمان رسیدن پلیس (ثانیه)", "int", "HEIST_POLICE_SECONDS", min=20, max=1800)
_S("heist_cam", "heist", "زمان هک هر دوربین (ثانیه؛ ۵ دوربین)", "int", "HEIST_CAMERA_SECONDS", min=1, max=60)
_S("heist_gem", "heist", "زمان برداشتن هر الماس (ثانیه؛ ۶ الماس)", "int", "HEIST_GEM_SECONDS", min=1, max=60)
_S("heist_cooldown", "heist", "قفل بعد از هر سرقت (ساعت؛ برای هکر و دزد)", "int", "HEIST_COOLDOWN_SECONDS", scale=3600, min=0, max=240)
_S("heist_price_h", "heist", "قیمت وسایل هکر", "int", "HEIST_PRICE_HACKER", min=0)
_S("heist_price_t", "heist", "قیمت وسایل دزد", "int", "HEIST_PRICE_THIEF", min=0)
_S("heist_fine", "heist", "جریمه‌ی دستگیری (برای هر نفر)", "int", "HEIST_FINE", min=0)
_S("heist_profit", "heist", "سود کل سرقت (الماس؛ نصف هر نفر)", "int", "HEIST_PROFIT", min=0)
_S("heist_rings", "heist", "الماس انگشتر کل سرقت (نصف هر نفر)", "int", "HEIST_RING_REWARD", min=0)
_S("tax_rate", "tax", "مالیات شرطبندی و دینامیت", "pct", "TAX_RATE", max=90)
_S("tax_casino", "tax", "مالیات کازینو", "pct", "TAX_RATE_CASINO", max=90)
_S("tax_receiver", "tax", "آیدی دریافت‌کننده‌ی مالیات", "int", "TAX_RECEIVER_ID", min=1)
_S("join_timeout", "bet", "زمان انتظار پیوستن (ثانیه)", "int", "JOIN_TIMEOUT_SECONDS", min=10, max=3600)
_S("xp_bet", "bet", "XP هر برد شرطبندی", "int", "XP_PER_BET_WIN", min=0)
_S("xp_casino", "casino", "XP هر برد کازینو", "int", "XP_PER_CASINO_WIN", min=0)
_S("turn_timeout", "dynamite", "زمان هر نوبت (ثانیه)", "int", "TURN_TIMEOUT_SECONDS", min=10, max=600)
_S("mine_step", "mine", "ضریب هر الماس", "float", "MINE_MULTIPLIER_STEP", min=0.01, max=5)
_S("xp_ring", "jewelry", "XP هر الماس انگشتر", "int", "XP_PER_RING_DIAMOND", min=0)
SHOP_RING_SECONDS = 30 * 60        # زمان تبدیل هر الماس انگشتر تو مغازه جواهری
SHOP_KW_PER_HOUR = 1.0             # مصرف برق مغازه (KW) به ازای هر ساعت تولید
_S("shop_ring_min", "jewelry", "مغازه: زمان تبدیل هر الماس انگشتر (دقیقه)", "int", "SHOP_RING_SECONDS", scale=60, min=1, max=1440)
_S("shop_kw_hour", "jewelry", "مغازه: مصرف برق در هر ساعت (KW)", "float", "SHOP_KW_PER_HOUR", min=0.01, max=1000)
SHOP_RING_SELL_PRICE = 15000       # قیمت ثابت فروش (صادرات) هر انگشتر
SHOP_IMPORT_PRICE = 4000           # قیمت واردات هر الماس انگشتر
SHOP_IMPORT_MIN = 20               # حداقل تعداد سفارش واردات
SHOP_EXPORT_MIN = 20               # حداقل تعداد صادرات
SHOP_TRADE_FEE = 0.03              # هزینه‌ی اضافه‌ی واردات/صادرات (کرایه و باربری)
SHOP_IMPORT_SECONDS = 60 * 60      # زمان رسیدن بار واردات
SHOP_EXPORT_SECONDS = 60 * 60      # زمان ارسال بار صادرات
SHOP_RENT_SECONDS = 24 * 3600      # مدت هر دوره‌ی اجاره
SHOP_RENT_WARN_SECONDS = 6 * 3600  # چند ساعت مونده به پایان اجاره پیام تمدید بیاد
CITY_DEFAULT_RING_PRICE = 5000     # قیمت پیش‌فرض الماس انگشتر تو مارکت (تا شهردار قیمت بذاره)
PANEL_REVERT_SECONDS = 5           # بعد از پیام‌های موقت چند ثانیه بعد برمی‌گرده به پنل
_S("shop_sell_price", "jewelry", "مغازه: قیمت فروش هر انگشتر (صادرات)", "int", "SHOP_RING_SELL_PRICE", min=1)
_S("shop_import_price", "jewelry", "مغازه: قیمت واردات هر الماس انگشتر", "int", "SHOP_IMPORT_PRICE", min=1)
_S("shop_import_min", "jewelry", "مغازه: حداقل تعداد سفارش واردات", "int", "SHOP_IMPORT_MIN", min=1)
_S("shop_export_min", "jewelry", "مغازه: حداقل تعداد صادرات", "int", "SHOP_EXPORT_MIN", min=1)
_S("shop_trade_fee", "jewelry", "مغازه: هزینه‌ی اضافه‌ی واردات/صادرات", "pct", "SHOP_TRADE_FEE", max=90)
_S("shop_import_min_time", "jewelry", "مغازه: زمان رسیدن بار واردات (دقیقه)", "int", "SHOP_IMPORT_SECONDS", scale=60, min=1, max=10080)
_S("shop_rent_hours", "jewelry", "مغازه: مدت هر دوره‌ی اجاره (ساعت)", "int", "SHOP_RENT_SECONDS", scale=3600, min=1, max=720)
_S("shop_rent_warn_hours", "jewelry", "مغازه: چند ساعت مونده به پایان اجاره پیام تمدید بیاد (ساعت)", "int", "SHOP_RENT_WARN_SECONDS", scale=3600, min=1, max=720)
_S("city_ring_default", "general", "قیمت پیش‌فرض الماس انگشتر تو مارکت (تا شهردار تنظیم کنه)", "int", "CITY_DEFAULT_RING_PRICE", min=1)
_S("panel_revert", "general", "برگشت خودکار پنل بعد از پیام‌های موقت (ثانیه)", "int", "PANEL_REVERT_SECONDS", min=1, max=60)
_S("shop_export_min_time", "jewelry", "مغازه: زمان ارسال بار صادرات (دقیقه)", "int", "SHOP_EXPORT_SECONDS", scale=60, min=1, max=10080)
_S("bank_fee", "bank", "هزینه‌ی افتتاح حساب", "int", "BANK_OPENING_FEE", min=0)
_S("bank_rate", "bank", "درصد سود روزانه", "pct", "BANK_INTEREST_RATE", max=100)
_S("bank_max", "bank", "سقف سود روزانه", "int", "BANK_DAILY_INTEREST_MAX", min=0)
_S("bank_card_fee", "bank", "کارمزد کارت به کارت", "pct", "BANK_CARD_FEE_RATE", max=50)
_S("transfer_max", "bank", "سقف هر انتقال الماس", "int", "TRANSFER_MAX_AMOUNT", min=1)
_S("transfer_cd", "bank", "فاصله‌ی بین دو انتقال الماس (ثانیه)", "int", "TRANSFER_COOLDOWN_SECONDS", min=0, max=3600)
_S("gift_price", "gift", "قیمت گیفت‌باکس", "int", "GIFT_BOX_PRICE", min=0)
_S("gift_slot_price", "gift", "قیمت خرید جای گیفت", "int", "GIFT_SLOT_PRICE", min=0)
_S("gift_up", "gift", "حداکثر افزایش قیمت بازار", "int", "GIFT_MAX_PRICE_UP", min=0)
_S("gift_down", "gift", "حداکثر کاهش قیمت بازار", "int", "GIFT_MAX_PRICE_DOWN", min=0)
_S("gift_interval", "gift", "هر چند ساعت قیمت بازار تغییر کنه (ساعت)", "int", "GIFT_MARKET_INTERVAL_SECONDS", scale=3600, min=1, max=720)
_S("gift_min", "gift", "حداقل ارزش مرجع گیفت", "int", "GIFT_MIN_VALUE", min=0)
_S("gift_max", "gift", "حداکثر ارزش مرجع گیفت", "int", "GIFT_MAX_VALUE", min=1)
_S("start_diamonds", "general", "الماس اولیه‌ی کاربر جدید", "int", "START_DIAMONDS", min=0)
_S("xp_daily", "general", "XP هدیه‌ی روزانه", "int", "XP_PER_DAILY_GIFT", min=0)
_S("laqab_days", "general", "مدت اعتبار لقب (روز)", "int", "LAQAB_DURATION_SECONDS", scale=86400, min=1)
_S("force_channel", "general", "کانال عضویت اجباری", "str", "FORCE_JOIN_CHANNEL")
_S("hunt_interval", "general", "شکار الماس: فاصله‌ی پیام‌ها", "int", "DIAMOND_HUNT_MESSAGE_INTERVAL", min=1)
_S("hunt_costs", "general", "شکار الماس: هزینه‌ی ۳ مرحله", "intlist", "DIAMOND_HUNT_COSTS", n=3)
_S("hunt_chances", "general", "شکار الماس: شانس ۳ مرحله", "pctlist", "DIAMOND_HUNT_WIN_CHANCES", n=3)

# ---------- یارانه‌ی روزانه‌ی شهر ----------
SUBSIDY_PERCENT = 0.10          # چند درصد از موجودی خزانه‌ی شهر هر شب بین ثبت‌نام‌شده‌ها تقسیم بشه
SUBSIDY_MAX_WEALTH = 100_000    # دارایی کل (الماس + بانک + ارزش گیفت‌ها) باید کمتر از این باشه
SUBSIDY_HOUR = 0                # ساعت واریز (به وقت تهران)
SUBSIDY_MINUTE = 0              # دقیقه‌ی واریز
_S("subsidy_pct", "subsidy", "درصد یارانه از موجودی خزانه‌ی شهر", "pct", "SUBSIDY_PERCENT", min=0, max=100)
_S("subsidy_max", "subsidy", "سقف دارایی کل برای دریافت یارانه (باید کمتر از این باشه)", "int", "SUBSIDY_MAX_WEALTH", min=1)
_S("subsidy_hour", "subsidy", "ساعت واریز یارانه (۰ تا ۲۳)", "int", "SUBSIDY_HOUR", min=0, max=23)
_S("subsidy_minute", "subsidy", "دقیقه‌ی واریز یارانه (۰ تا ۵۹)", "int", "SUBSIDY_MINUTE", min=0, max=59)

# ---------- حفاری ----------
DRILL_MAX_LEVEL = 10
DRILL_HIRE_PRICE = 30_000
DRILL_SPEED = [0.4, 0.6, 0.9, 1.2, 1.6, 2.0, 2.5, 3.0, 3.8, 5.0]                    # الماس در ثانیه، به ازای هر لول
DRILL_CAPACITY = [6_000, 10_000, 16_000, 24_000, 34_000, 46_000, 60_000, 76_000, 100_000, 130_000]
DRILL_KW = [0.1, 0.2, 0.3, 0.5, 0.6, 0.8, 0.9, 1, 1.2, 1.5]                                          # مصرف برق (KW) در هر ساعت کار، به ازای هر لول
DRILL_UPGRADE = [25_000, 40_000, 55_000, 75_000, 100_000, 130_000, 170_000, 220_000, 300_000]   # هزینه‌ی رسیدن به لول ۲ تا ۱۰
_S("drill_hire", "drill", "هزینه‌ی استخدام مدیر", "int", "DRILL_HIRE_PRICE", min=0)
_S("drill_speed", "drill", "سرعت ماین هر لول (الماس در ثانیه؛ ۱۰ عدد)", "floatlist", "DRILL_SPEED", n=10)
_S("drill_cap", "drill", "ظرفیت انبار هر لول (۱۰ عدد)", "intlist", "DRILL_CAPACITY", n=10)
_S("drill_kw", "drill", "مصرف برق هر لول (KW در ساعت؛ ۱۰ عدد)", "floatlist", "DRILL_KW", n=10)
DRILL_BILL_SECONDS = 60 * 60   # هر چند وقت کارکرد، یک بار قبض برق به بدهی اضافه می‌شه
_S("drill_bill", "drill", "هر چند دقیقه کارکرد قبض برق اضافه بشه (دقیقه؛ پیش‌فرض ۶۰ = هر ساعت)", "int", "DRILL_BILL_SECONDS", scale=60, min=1, max=1440)
_S("drill_up", "drill", "هزینه‌ی ارتقاء به لول ۲ تا ۱۰ (۹ عدد)", "intlist", "DRILL_UPGRADE", n=9)

def _adm_current(d):
    v = globals()[d["g"]]
    if d["kind"] == "weights":
        return [w for _, w in v]
    return list(v) if d["kind"] in ("intlist", "pctlist", "floatlist") else v

for _d in _ADM_DEFS.values():
    _ADM_DEFAULTS[_d["key"]] = _copy.deepcopy(_adm_current(_d))

def _adm_apply(d, stored):
    g = d["g"]
    if d["kind"] == "weights":
        names = [n for n, _ in globals()[g]]
        globals()[g] = [(n, w) for n, w in zip(names, stored)]
    elif d["kind"] in ("intlist", "pctlist", "floatlist"):
        globals()[g] = list(stored)
    else:
        globals()[g] = stored

def _adm_fmt(d, v):
    k = d["kind"]
    if k == "int":
        return f"{int(v) // d.get('scale', 1):,}"
    if k == "float":
        return f"{v:g}"
    if k == "pct":
        return f"{v * 100:g}٪"
    if k == "str":
        return str(v)
    if k == "intlist":
        return " / ".join(f"{int(x):,}" for x in v)
    if k == "pctlist":
        return " / ".join(f"{x * 100:g}٪" for x in v)
    return " / ".join(f"{x:g}" for x in v)

def _adm_num(text):
    t = _normalize_digits((text or "").strip().lower()).replace(",", "").replace("٬", "")
    mult = 1
    if t.endswith("k"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    elif t.endswith("b"):
        mult, t = 1_000_000_000, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        raise ValueError("باید عدد باشه")

def _adm_nums(text):
    return [_adm_num(p) for p in re.split(r"[\s,،]+", (text or "").strip()) if p]

def _adm_parse(d, text):
    """متن ورودی رو به مقدار ذخیره‌شده تبدیل می‌کنه؛ ValueError با پیام فارسی اگه نامعتبر باشه."""
    k = d["kind"]
    lo, hi = d.get("min"), d.get("max")
    if k == "str":
        v = (text or "").strip()
        if not re.fullmatch(r"@[A-Za-z0-9_]{4,}", v):
            raise ValueError("باید مثل @mychannel باشه")
        return v
    if k in ("int", "float", "pct"):
        x = _adm_num(text)
        if (lo is not None and x < lo) or (hi is not None and x > hi):
            raise ValueError(f"عدد باید بین {lo if lo is not None else '…'} و {hi if hi is not None else '…'} باشه")
        if k == "int":
            return int(round(x)) * d.get("scale", 1)
        return x / 100 if k == "pct" else x
    nums = _adm_nums(text)
    if len(nums) != d["n"]:
        raise ValueError(f"دقیقاً {d['n']} عدد با فاصله بفرست")
    if any(x < 0 for x in nums):
        raise ValueError("عدد منفی مجاز نیست")
    if k == "intlist":
        return [int(round(x)) for x in nums]
    if k == "pctlist":
        if any(x > 100 for x in nums):
            raise ValueError("درصد نباید بیشتر از ۱۰۰ باشه")
        return [x / 100 for x in nums]
    if k == "floatlist":
        return [float(x) for x in nums]
    if sum(nums) <= 0:
        raise ValueError("جمع شانس‌ها باید بیشتر از صفر باشه")
    return nums

def _adm_load_apply_all():
    for d in _ADM_DEFS.values():
        if d["key"] in _SETTINGS:
            try:
                _adm_apply(d, _SETTINGS[d["key"]])
            except Exception as e:
                logging.error(f"اعمال تنظیم {d['key']} ناموفق بود: {e}")

def _gift_market_reschedule():
    """بعد از تغییر بازه‌ی بازار، جاب زمان‌بندی رو با بازه‌ی جدید دوباره تنظیم می‌کنه."""
    try:
        if premium_gift_scheduler is not None:
            premium_gift_scheduler.reschedule_job("premium_gift_market", trigger="interval",
                                                  seconds=int(GIFT_MARKET_INTERVAL_SECONDS))
    except Exception as e:
        logging.error(f"تغییر بازه‌ی بازار گیفت ناموفق بود: {e}")

# ---------- کاتالوگ گیفت (قابل ویرایش از پنل) ----------
_GIFT_BASE_CATALOG = _copy.deepcopy(GIFT_CATALOG)

def _gift_rebuild_catalog():
    global GIFT_MODEL_INDEX
    custom = _SETTINGS.get("gift_custom", {}) or {}
    GIFT_CATALOG.clear()
    GIFT_CATALOG.update(_copy.deepcopy(_GIFT_BASE_CATALOG))
    for k, cfg in (custom.get("types") or {}).items():
        GIFT_CATALOG[k] = {"name": cfg["name"], "min": int(cfg["min"]), "max": int(cfg["max"]),
                           "weight": float(cfg["weight"]), "models": {}}
    for k, ov in (custom.get("overrides") or {}).items():
        if k in GIFT_CATALOG:
            GIFT_CATALOG[k].update({kk: vv for kk, vv in ov.items() if kk in ("min", "max", "weight")})
    for code, pair in (custom.get("models") or {}).items():
        if pair[0] in GIFT_CATALOG:
            GIFT_CATALOG[pair[0]]["models"][int(code)] = pair[1]
    GIFT_MODEL_INDEX = {c: (k, n) for k, cfg in GIFT_CATALOG.items() for c, n in cfg["models"].items()}

def _gift_custom():
    c = _SETTINGS.get("gift_custom") or {}
    for k in ("types", "overrides", "models"):
        c.setdefault(k, {})
    c.setdefault("disabled", [])
    return c

# ---------- پنل ----------
ADM_SECTIONS = [
    ("casino", "🎰", "کازینو"), ("bet", "🎲", "شرطبندی"), ("dynamite", "💣", "دینامیت"),
    ("mine", "⛏", "معدن"), ("jewelry", "💍", "جواهری"), ("bank", "🏦", "بانک"),
    ("gift", "🎁", "گیفت"), ("tax", "💸", "مالیات"), ("heist", "🕵️", "دزدی"),
    ("subsidy", "🏙", "یارانه شهر"), ("drill", "🪨", "حفاری"),
    ("general", "⚙️", "عمومی و ابزارها"),
]
_ADM_SEC_FEATURE = {"casino": "casino", "bet": "bet", "dynamite": "dynamite", "mine": "mine",
                    "jewelry": "jewelry", "bank": "bank", "gift": "gift", "heist": "heist", "drill": "drill"}

def _adm_is_admin(uid):
    return uid in ADMIN_IDS

def _adm_main_markup():
    markup = types.InlineKeyboardMarkup()
    for i in range(0, len(ADM_SECTIONS), 3):
        row = []
        for key, icon, title in ADM_SECTIONS[i:i + 3]:
            off = key in _ADM_SEC_FEATURE and not feature_on(_ADM_SEC_FEATURE[key])
            row.append(colored_button(f"{'⛔ ' if off else icon + ' '}{title}", callback_data=f"adm_sec|{key}"))
        markup.row(*row)
    markup.row(colored_button("🛠 دیباک", callback_data="adm_debug"))
    return markup

_ADM_MAIN_TEXT = "⚙️ پنل مدیریت\n\nبخشی که می‌خوای مدیریت کنی رو انتخاب کن:\n(⛔ یعنی اون بخش الان مخفیه)"

def _adm_render(sec, note=None):
    title = next((f"{ic} {t}" for k, ic, t in ADM_SECTIONS if k == sec), sec)
    markup = types.InlineKeyboardMarkup()
    lines = [title, ""]
    feat = _ADM_SEC_FEATURE.get(sec)
    if feat:
        on = feature_on(feat)
        lines.append(f"وضعیت: {'✅ فعال' if on else '⛔ مخفی'}")
        markup.row(colored_button("⛔ مخفی کردن" if on else "✅ نمایش دوباره", callback_data=f"adm_feat|{feat}"))
    if sec == "casino":
        row = []
        for gk, emoji in CASINO_GAMES.items():
            row.append(colored_button(f"{emoji} {CASINO_GAME_NAMES[gk]} {'✅' if game_on(gk) else '⛔'}",
                                      callback_data=f"adm_game|{gk}"))
            if len(row) == 3:
                markup.row(*row)
                row = []
        if row:
            markup.row(*row)
        lines.append("بازی‌های مخفی تو منوی کازینو نمایش داده نمی‌شن.")
    if sec == "tax":
        markup.row(colored_button("💸 مالیات همه‌ی بازی‌ها (یکجا)", callback_data="adm_taxall"))
    if sec == "heist":
        markup.row(*[colored_button(f"{FEATURE_LABELS[fk]} {'✅' if feature_on(fk) else '⛔'}", callback_data=f"adm_feat|{fk}")
                     for fk in ("heist_gear", "market")])
        lines.append("اگه «نیاز به وسایل» خاموش باشه، همه بدون خرید وسایل می‌تونن دزدی کنن.")
    if sec == "general":
        row = []
        for fk in ("rank", "tags"):
            row.append(colored_button(f"{FEATURE_LABELS[fk]} {'✅' if feature_on(fk) else '⛔'}", callback_data=f"adm_feat|{fk}"))
        markup.row(*row)
    for d in _ADM_DEFS.values():
        if d["section"] == sec:
            markup.row(colored_button(f"{d['label']}: {_adm_fmt(d, _adm_current(d))}", callback_data=f"adm_set|{d['key']}"))
    if sec == "gift":
        markup.row(colored_button("📦 نوع‌ها و مدل‌ها", callback_data="adm_gtypes"))
    if sec == "general":
        markup.row(colored_button("📢 پیام همگانی", callback_data="admin_broadcast"))
        markup.row(colored_button("➕ الماس به یک نفر", callback_data="admin_add_diamond"),
                   colored_button("➖ الماس از یک نفر", callback_data="admin_remove_diamond"))
        markup.row(colored_button("➕ الماس به همه", callback_data="admin_add_diamond_all"),
                   colored_button("➖ الماس از همه", callback_data="admin_remove_diamond_all"))
        markup.row(colored_button("🎁 ساخت کد هدیه", callback_data="admin_create_giftcode"))
    markup.row(colored_button("🔙 پنل اصلی", callback_data="adm_home"))
    if note:
        lines = [note, ""] + lines
    return "\n".join(lines), markup

def _adm_edit(chat_id, message_id, text, markup, parse_mode=None):
    ok = safe_edit_message(text, chat_id, message_id, reply_markup=markup, parse_mode=parse_mode) \
        if parse_mode else safe_edit_message(text, chat_id, message_id, reply_markup=markup)
    if not ok:
        safe_send_message(chat_id, text, reply_markup=markup, **({"parse_mode": parse_mode} if parse_mode else {}))

def _adm_show_sec(chat_id, message_id, sec, note=None):
    text, markup = _adm_render(sec, note)
    _adm_edit(chat_id, message_id, text, markup)

def _adm_open(message):
    if message.chat.type != "private" or not _adm_is_admin(message.from_user.id):
        return
    safe_send_message(message.chat.id, _ADM_MAIN_TEXT, reply_markup=_adm_main_markup(),
                      reply_to_message_id=message.message_id)

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    _adm_open(message)

@bot.message_handler(func=lambda m: text_is(m, "پنل مدیریت", "مدیریت"))
def text_admin(message):
    _adm_open(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_panel" or call.data.startswith("admin_panel|"))
def admin_panel(call):
    if not _adm_is_admin(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    _adm_edit(call.message.chat.id, call.message.message_id, _ADM_MAIN_TEXT, _adm_main_markup())

def _adm_back_markup(back):
    m = types.InlineKeyboardMarkup()
    m.row(colored_button("🔙 بازگشت", callback_data=back))
    return m

def _adm_ask(call, text, back, handler, *args, parse_mode=None):
    cancel_pending_step(call.message.chat.id, call.from_user.id)
    _adm_edit(call.message.chat.id, call.message.message_id, text, _adm_back_markup(back), parse_mode)
    register_timed_next_step_handler(call.message, handler, *args, call.message.message_id,
                                     expected_user_id=call.from_user.id)

def _adm_retry(message, err, handler, *args):
    bot.reply_to(message, _casino_err(err))
    register_timed_next_step_handler(message, handler, *args, expected_user_id=message.from_user.id)

def _adm_cleanup(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

_ADM_HINTS = {
    "int": "یه عدد بفرست (مثلاً 5000 یا 5k)", "float": "یه عدد اعشاری بفرست (مثلاً 0.25)",
    "pct": "درصد رو به عدد بفرست (مثلاً 10)", "str": "مثلاً @mychannel",
    "intlist": "عددها رو با فاصله بفرست (مثلاً 100 200 400)",
    "pctlist": "درصدها رو با فاصله بفرست (مثلاً 30 50 70)",
    "floatlist": "عددها رو با فاصله بفرست (مثلاً 0.4 0.6 0.9)",
    "weights": "۴ عدد با فاصله بفرست: معمولی نادر حماسی افسانه‌ای (مثلاً 50 30 15 5)",
}

def _adm_setting_step(message, key, panel_id):
    if not _adm_is_admin(message.from_user.id):
        return
    d = _ADM_DEFS[key]
    try:
        stored = _adm_parse(d, message.text)
    except ValueError as e:
        _adm_retry(message, f"مقدار نامعتبره: {e}", _adm_setting_step, key, panel_id)
        return
    except Exception:
        _adm_retry(message, "مقدار نامعتبره. یه عدد درست بفرست", _adm_setting_step, key, panel_id)
        return
    _adm_apply(d, stored)
    if key == "gift_interval":
        _gift_market_reschedule()
    if key in ("subsidy_hour", "subsidy_minute"):
        _subsidy_reschedule()
    saved = setting_save(key, stored)
    _adm_cleanup(message)
    note = f"✅ «{d['label']}» شد: {_adm_fmt(d, _adm_current(d))}"
    if not saved:
        note += "\n⚠️ ذخیره‌ی دائمی انجام نشد (جدول bot_settings ساخته نشده؟). تا ری‌استارت اعمال شد."
    _adm_show_sec(message.chat.id, panel_id, d["section"], note)

def _adm_taxall_step(message, panel_id):
    if not _adm_is_admin(message.from_user.id):
        return
    d1, d2 = _ADM_DEFS["tax_rate"], _ADM_DEFS["tax_casino"]
    try:
        stored = _adm_parse(d1, message.text)
    except ValueError as e:
        _adm_retry(message, f"مقدار نامعتبره: {e}", _adm_taxall_step, panel_id)
        return
    except Exception:
        _adm_retry(message, "مقدار نامعتبره. یه عدد درست بفرست", _adm_taxall_step, panel_id)
        return
    _adm_apply(d1, stored)
    _adm_apply(d2, stored)
    saved = setting_save("tax_rate", stored) and setting_save("tax_casino", stored)
    _adm_cleanup(message)
    note = f"✅ مالیات همه‌ی بازی‌ها شد: {_adm_fmt(d1, _adm_current(d1))}"
    if not saved:
        note += "\n⚠️ ذخیره‌ی دائمی انجام نشد (جدول bot_settings ساخته نشده؟). تا ری‌استارت اعمال شد."
    _adm_show_sec(message.chat.id, panel_id, "tax", note)

def _debug_text():
    up = int(time.time() - _BOOT_TIME)
    lines = ["🛠 دیباک", "",
             f"⏱ آپتایم: {up // 3600}h {(up % 3600) // 60}m",
             f"🧵 تردهای فعال: {threading.active_count()}",
             f"📨 صف تلگرام: {_telegram_backlog():.2f}s",
             f"⌨️ مرحله‌های منتظر جواب: {len(_next_step_pending)}",
             f"🕵️ دزدی‌های فعال: {len(_heists)}"]
    try:
        t0 = time.monotonic()
        supabase.table("users").select("user_id").limit(1).execute()
        lines.append(f"🗄 پینگ دیتابیس: {(time.monotonic() - t0) * 1000:.0f}ms")
    except Exception as e:
        lines.append(f"🗄 دیتابیس: خطا ({str(e)[:60]})")
    try:
        t0 = time.monotonic()
        _original_get_me = bot.get_me()
        lines.append(f"📡 پینگ تلگرام: {(time.monotonic() - t0) * 1000:.0f}ms")
    except Exception as e:
        lines.append(f"📡 تلگرام: خطا ({str(e)[:60]})")
    lines.append("")
    if _PERF_EVENTS:
        lines.append("آخرین کندی‌ها (بیشتر از حد مجاز):")
        for ts, kind, secs, info in list(_PERF_EVENTS)[-10:][::-1]:
            hhmm = datetime.fromtimestamp(ts, TEHRAN_TZ).strftime("%H:%M:%S")
            lines.append(f"{hhmm}  {kind}  {secs}s  {info}")
    else:
        lines.append("✅ کندی ثبت‌شده‌ای نیست.")
    return "\n".join(lines)

@bot.message_handler(commands=["debug"])
def cmd_debug(message):
    if message.chat.type != "private" or not _adm_is_admin(message.from_user.id):
        return
    safe_send_message(message.chat.id, _debug_text())

# ---------- مدیریت نوع‌ها و مدل‌های گیفت ----------
def _adm_gtypes_render(note=None):
    markup = types.InlineKeyboardMarkup()
    lines = ["📦 نوع‌های گیفت", ""]
    for k, cfg in GIFT_CATALOG.items():
        markup.row(colored_button(f"{cfg['name']} ({len(cfg['models'])} مدل)", callback_data=f"adm_gt|{k}"))
    markup.row(colored_button("➕ نوع جدید", callback_data="adm_gtnew"))
    markup.row(colored_button("🔙 گیفت", callback_data="adm_sec|gift"))
    if note:
        lines = [note, ""] + lines
    return "\n".join(lines), markup

def _adm_gt_render(tk, note=None):
    cfg = GIFT_CATALOG[tk]
    disabled = {int(c) for c in _gift_custom()["disabled"]}
    markup = types.InlineKeyboardMarkup()
    lines = [f"📦 {html_escape_keep_emoji(cfg['name'])}", "",
             f"قیمت: {cfg['min']:,} تا {cfg['max']:,}", f"شانس نوع: {cfg['weight']:g}", "", "مدل‌ها:"]
    if not cfg["models"]:
        lines.append("— هنوز مدلی نداره (تا مدل اضافه نکنی تو گیفت‌باکس نمیاد)")
    row = []
    for code, name in cfg["models"].items():
        off = code in disabled
        lines.append(f"{'⛔' if off else '✅'} {_gift_emoji(code, '✨')} {html_escape_keep_emoji(name)}")
        row.append(colored_button(f"{'⛔ ' if off else ''}{name}", callback_data=f"adm_gm|{code}",
                                  icon_custom_emoji_id=str(code)))
        if len(row) == 2:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    markup.row(colored_button("✏️ قیمت و شانس", callback_data=f"adm_gted|{tk}"),
               colored_button("➕ مدل جدید", callback_data=f"adm_gmnew|{tk}"))
    markup.row(colored_button("🔙 نوع‌ها", callback_data="adm_gtypes"))
    if note:
        lines = [html_escape_keep_emoji(note), ""] + lines
    return "\n".join(lines), markup

def _adm_gift_edit_overrides(tk, **vals):
    c = _gift_custom()
    if tk in c["types"]:
        c["types"][tk].update(vals)
    else:
        c["overrides"].setdefault(tk, {}).update(vals)
    return setting_save("gift_custom", c)

def _adm_gted_step(message, tk, panel_id):
    if not _adm_is_admin(message.from_user.id):
        return
    try:
        nums = _adm_nums(message.text)
        assert len(nums) == 3
        lo, hi, wt = int(nums[0]), int(nums[1]), float(nums[2])
        assert 0 <= lo <= hi and wt >= 0
    except Exception:
        _adm_retry(message, "فرمت اشتباهه. مثال: 20m 40m 1  (حداقل قیمت، حداکثر قیمت، شانس)", _adm_gted_step, tk, panel_id)
        return
    saved = _adm_gift_edit_overrides(tk, min=lo, max=hi, weight=wt)
    _gift_rebuild_catalog()
    _adm_cleanup(message)
    text, markup = _adm_gt_render(tk, "✅ ذخیره شد" + ("" if saved else " (⚠️ فقط تا ری‌استارت؛ ذخیره‌ی دائمی نشد)"))
    _adm_edit(message.chat.id, panel_id, text, markup, "HTML")

def _adm_gmnew_step(message, tk, panel_id):
    if not _adm_is_admin(message.from_user.id):
        return
    parts = (message.text or "").strip().split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit() or len(parts[0]) < 10:
        _adm_retry(message, "فرمت اشتباهه. مثال: 5454286330487922571 Sketchy", _adm_gmnew_step, tk, panel_id)
        return
    code, name = int(parts[0]), parts[1].strip()[:40]
    if code in GIFT_MODEL_INDEX:
        _adm_retry(message, "این ایموجی قبلاً به یه مدل داده شده", _adm_gmnew_step, tk, panel_id)
        return
    c = _gift_custom()
    c["models"][str(code)] = [tk, name]
    saved = setting_save("gift_custom", c)
    _gift_rebuild_catalog()
    _adm_cleanup(message)
    text, markup = _adm_gt_render(tk, f"✅ مدل «{name}» اضافه شد" + ("" if saved else " (⚠️ فقط تا ری‌استارت)"))
    _adm_edit(message.chat.id, panel_id, text, markup, "HTML")

def _adm_gtnew_step(message, panel_id):
    if not _adm_is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in re.split(r"[|｜]", message.text or "")]
    try:
        assert len(parts) == 4 and parts[0]
        name = parts[0][:40]
        lo, hi, wt = int(_adm_num(parts[1])), int(_adm_num(parts[2])), float(_adm_num(parts[3]))
        assert 0 <= lo <= hi and wt >= 0
    except Exception:
        _adm_retry(message, "فرمت اشتباهه. مثال: Loot Bag | 20m | 40m | 1", _adm_gtnew_step, panel_id)
        return
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "type"
    tk, n = base, 2
    while tk in GIFT_CATALOG:
        tk, n = f"{base}_{n}", n + 1
    c = _gift_custom()
    c["types"][tk] = {"name": name, "min": lo, "max": hi, "weight": wt}
    saved = setting_save("gift_custom", c)
    _gift_rebuild_catalog()
    _adm_cleanup(message)
    text, markup = _adm_gt_render(tk, "✅ نوع ساخته شد؛ حالا یه مدل بهش اضافه کن" + ("" if saved else " (⚠️ فقط تا ری‌استارت)"))
    _adm_edit(message.chat.id, panel_id, text, markup, "HTML")

# ---------- مسیر‌یاب کالبک‌ها ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def adm_router(call):
    if not _adm_is_admin(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    cid, mid = call.message.chat.id, call.message.message_id
    cancel_pending_step(cid, call.from_user.id)
    parts = call.data.split("|")
    act, arg = parts[0], (parts[1] if len(parts) > 1 else "")

    if act == "adm_home":
        _adm_edit(cid, mid, _ADM_MAIN_TEXT, _adm_main_markup())
    elif act == "adm_sec":
        _adm_show_sec(cid, mid, arg)
    elif act == "adm_feat":
        if arg in FEATURE_LABELS:
            new = not feature_on(arg)
            saved = setting_save("feat_" + arg, new)
            sec = next((s_ for s_, f_ in _ADM_SEC_FEATURE.items() if f_ == arg), {"heist_gear": "heist", "market": "heist"}.get(arg, "general"))
            note = f"{'✅ نمایش داده می‌شه' if new else '⛔ مخفی شد'}: {FEATURE_LABELS[arg]}"
            if not saved:
                note += "\n⚠️ ذخیره‌ی دائمی نشد (جدول bot_settings؟)"
            _adm_show_sec(cid, mid, sec, note)
    elif act == "adm_game":
        if arg in CASINO_GAMES:
            new = not game_on(arg)
            saved = setting_save("game_" + arg, new)
            note = f"{'✅ نمایش داده می‌شه' if new else '⛔ مخفی شد'}: {CASINO_GAME_NAMES[arg]}"
            if not saved:
                note += "\n⚠️ ذخیره‌ی دائمی نشد (جدول bot_settings؟)"
            _adm_show_sec(cid, mid, "casino", note)
    elif act == "adm_set":
        d = _ADM_DEFS.get(arg)
        if d:
            text = (f"✏️ {d['label']}\n\nمقدار فعلی: {_adm_fmt(d, _adm_current(d))}\n"
                    f"پیش‌فرض: {_adm_fmt(d, _ADM_DEFAULTS[arg])}\n\n{_ADM_HINTS[d['kind']]}")
            _adm_ask(call, text, f"adm_sec|{d['section']}", _adm_setting_step, arg)
    elif act == "adm_taxall":
        text = (f"✏️ مالیات همه‌ی بازی‌ها\n\nشرطبندی و دینامیت: {TAX_RATE * 100:g}٪\nکازینو: {TAX_RATE_CASINO * 100:g}٪\n\n"
                "یه درصد بفرست تا مالیات همه‌ی بازی‌ها یکی بشه (مثلاً 10)")
        _adm_ask(call, text, "adm_sec|tax", _adm_taxall_step)
    elif act == "adm_debug":
        m = types.InlineKeyboardMarkup()
        m.row(colored_button("🔄 تازه‌سازی", callback_data="adm_debug"))
        m.row(colored_button("🔙 پنل اصلی", callback_data="adm_home"))
        _adm_edit(cid, mid, _debug_text(), m)
    elif act == "adm_gtypes":
        text, markup = _adm_gtypes_render()
        _adm_edit(cid, mid, text, markup)
    elif act == "adm_gt" and arg in GIFT_CATALOG:
        text, markup = _adm_gt_render(arg)
        _adm_edit(cid, mid, text, markup, "HTML")
    elif act == "adm_gm":
        try:
            code = int(arg)
        except ValueError:
            return
        tk = GIFT_MODEL_INDEX.get(code, (None,))[0]
        if tk:
            c = _gift_custom()
            dis = {int(x) for x in c["disabled"]}
            dis.symmetric_difference_update({code})
            c["disabled"] = sorted(dis)
            saved = setting_save("gift_custom", c)
            text, markup = _adm_gt_render(tk, ("⛔ غیرفعال شد" if code in dis else "✅ دوباره فعال شد") + ("" if saved else " (⚠️ فقط تا ری‌استارت)"))
            _adm_edit(cid, mid, text, markup, "HTML")
    elif act == "adm_gted" and arg in GIFT_CATALOG:
        cfg = GIFT_CATALOG[arg]
        _adm_ask(call, f"✏️ قیمت و شانس «{cfg['name']}»\n\nفعلی: {cfg['min']:,} / {cfg['max']:,} / {cfg['weight']:g}\n\n"
                       "سه عدد با فاصله بفرست: حداقل قیمت، حداکثر قیمت، شانس\nمثال: 20m 40m 1",
                 f"adm_gt|{arg}", _adm_gted_step, arg)
    elif act == "adm_gmnew" and arg in GIFT_CATALOG:
        _adm_ask(call, f"➕ مدل جدید برای «{GIFT_CATALOG[arg]['name']}»\n\nآیدی ایموجی پرمیوم و اسم مدل رو بفرست\n"
                       "مثال: 5454286330487922571 Sketchy", f"adm_gt|{arg}", _adm_gmnew_step, arg)
    elif act == "adm_gtnew":
        _adm_ask(call, "➕ نوع جدید گیفت\n\nبه این شکل بفرست:\nنام | حداقل قیمت | حداکثر قیمت | شانس\n"
                       "مثال: Loot Bag | 20m | 40m | 1", "adm_gtypes", _adm_gtnew_step)

# ================== دزدی از جواهری ==================
# کلمه‌ی «دزدی» تو گروه پنل رو باز می‌کنه. دو نفر لازمه: هکر (سازنده‌ی پنل) و دزد.
# وضعیت دزدی‌ها فقط تو حافظه نگه داشته می‌شه؛ پول فقط آخر کار (موفقیت یا دستگیری) جابه‌جا می‌شه.
# پنلی که دزد نگرفته بعد از JOIN_TIMEOUT_SECONDS (مثل بقیه‌ی بازی‌ها) خودش بسته می‌شه
HEIST_IDLE_TICK = 5            # هر چند ثانیه شمارنده‌ی پلیس تو مرحله‌های انتظار تازه می‌شه
HEIST_BUSY_BACKLOG = 1.5       # اگه صف تلگرام از این بیشتر بود، ادیت‌های میانی رد می‌شن

_heists = {}          # (chat_id, message_id) -> state
_heist_users = {}     # user_id -> key
_heist_lock = threading.RLock()

def _hx(emoji_id, fallback):
    return _gift_emoji(emoji_id, fallback)

def _heist_fmt(sec):
    sec = max(0, int(math.ceil(sec)))
    return f"{sec // 60:02d}:{sec % 60:02d}"

_hprof_cache = {}   # user_id -> {"gear": {"hacker": n, "thief": n}, "locked_until": ts}

def _hprof(user_id):
    """پروفایل دزدی کاربر (تعداد وسایل + زمان قفل). فقط بار اول از دیتابیس خونده می‌شه."""
    p = _hprof_cache.get(user_id)
    if p is None:
        try:
            cos = dict(get_progress(user_id).get("cosmetics") or {})
        except Exception:
            cos = {}
        raw = dict(cos.get("heist") or {})
        gear = dict(raw.get("gear") or {})
        p = {"gear": {"hacker": int(gear.get("hacker", 0) or 0), "thief": int(gear.get("thief", 0) or 0)},
             "locked_until": float(raw.get("locked_until", 0) or 0)}
        if len(_hprof_cache) > 20000:
            _hprof_cache.clear()
        _hprof_cache[user_id] = p
    return p

def _hprof_persist(user_id):
    try:
        p = _hprof_cache.get(user_id)
        if p is None:
            return True
        progress = get_progress(user_id)
        cos = dict(progress.get("cosmetics") or {})
        cos["heist"] = {"gear": dict(p["gear"]), "locked_until": p["locked_until"]}
        supabase.table("user_progress").update({"cosmetics": cos}).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logging.error(f"خطا در ذخیره‌ی پروفایل دزدی {user_id}: {e}")
        return False

def _hprof_persist_async(user_id):
    threading.Thread(target=_hprof_persist, args=(user_id,), daemon=True).start()

def heist_grant_gear(user_id, role, count=1):
    """به کاربر وسیله‌ی دزدی اضافه می‌کنه (role: 'hacker' یا 'thief'). مارکت همین رو صدا می‌زنه."""
    with _get_user_financial_lock(user_id):
        p = _hprof(user_id)
        p["gear"][role] = p["gear"].get(role, 0) + int(count)
        return _hprof_persist(user_id)

def _heist_has_gear(user_id, role):
    if not feature_on("heist_gear"):
        return True
    return _hprof(user_id)["gear"].get(role, 0) > 0

def _heist_locked_left(user_id):
    return max(0.0, _hprof(user_id)["locked_until"] - time.time())

def _heist_consume(user_id, role):
    """وسیله‌ی نقش رو یکی کم می‌کنه و کاربر رو برای مدت قفل، از دزدی بیرون می‌ذاره."""
    with _get_user_financial_lock(user_id):
        p = _hprof(user_id)
        if feature_on("heist_gear"):
            p["gear"][role] = max(0, p["gear"].get(role, 0) - 1)
        p["locked_until"] = time.time() + int(HEIST_COOLDOWN_SECONDS)
    _hprof_persist_async(user_id)

def _heist_fmt_long(sec):
    sec = max(0, int(math.ceil(sec)))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"

def _heist_buy(user_id, role, price=None):
    """خرید یک وسیله. برمی‌گردونه (موجودی جدید, None) یا (None, 'funds'|'start'|'fail')."""
    if price is None:
        price = int(HEIST_PRICE_HACKER if role == "hacker" else HEIST_PRICE_THIEF)
    price = int(price)
    with _get_user_financial_lock(user_id):
        user = get_user(user_id)
        if not user:
            return None, "start"
        if int(user.get("diamonds", 0) or 0) < price:
            return None, "funds"
        if price > 0 and update_diamonds(user_id, -price) is None:
            return None, "fail"
        p = _hprof(user_id)
        p["gear"][role] = p["gear"].get(role, 0) + 1
        if not _hprof_persist(user_id):
            p["gear"][role] -= 1
            if price > 0:
                update_diamonds(user_id, price)
            return None, "fail"
        return p["gear"][role], None

def _heist_no_gear_exit(h):
    chat_id, message_id = h["chat_id"], h["message_id"]
    _heist_cleanup(h)
    safe_edit_message(f'اول وسایل مورد نیاز دزدی رو بخر {_hx(5819154526816444042, "❌")}',
                      chat_id, message_id, reply_markup=None, parse_mode="HTML")

def _heist_head():
    return f'{_hx(5818704981179505821, "🕵️")} دزدی از جواهری {_hx(6014651259057873388, "💎")}'

def _heist_started():
    return f'{_hx(5818704981179505821, "🕵️")} دزدی شروع شد {_hx(5830144944399981619, "🚨")}'

def _heist_police_line(h):
    return f'{_hx(5938136227701067845, "🚨")} تا رسیدن پلیس ها: {_heist_fmt(h["police_at"] - time.time())}'

def _heist_hacker_icon():
    return _hx(5938115019152560803, "💻")

def _heist_thief_icon():
    return _hx(5823445637231814311, "🥷")

def _heist_text(h):
    ph = h["phase"]
    if ph == "intro":
        return (f'{_heist_head()}\n\n'
                f'{_hx(5938290532991111041, "👥")} برای دزدی نیاز به دو نفر هست {_hx(5859432880442187580, "👥")}\n'
                f'هکر {_heist_hacker_icon()} و دزد {_heist_thief_icon()}\n\n'
                f'هر کدوم باید وسایل ضروری برای دزدی رو خریده باشن {_hx(5818716826699307883, "🛠")}')
    if ph == "wait":
        return (f'{_heist_head()}\n\n'
                f'هکر: {h["hacker"]["name"]} {_heist_hacker_icon()}\n\n'
                f'منتظر دزد برای شروع سرقت {_hx(5830144944399981619, "🚨")}')
    if ph == "ready":
        return (f'{_heist_head()}\n\n'
                f'هکر: {h["hacker"]["name"]} {_heist_hacker_icon()}\n'
                f'دزد: {h["thief"]["name"]} {_heist_thief_icon()}')
    if ph == "hack_wait":
        return (f'{_heist_started()}\n\n'
                f'نوبت: هکر {_heist_hacker_icon()}\n\n'
                f'{_heist_police_line(h)}')
    if ph == "hacking":
        left = (HEIST_CAMERAS - h["i"]) * int(HEIST_CAMERA_SECONDS)
        return (f'{_heist_started()}\n\n'
                f'نوبت: هکر {_heist_hacker_icon()}\n\n'
                f'{_hx(5825656411517886523, "⏳")} تا پایان هک: {_heist_fmt(left)}\n\n'
                f'دوربین های هک شده: {h["i"]}/{HEIST_CAMERAS}\n\n'
                f'{_heist_police_line(h)}')
    if ph == "thief_wait":
        return (f'{_heist_started()}\n\n'
                f'هکر همه دوربین ها رو هک کرده {_hx(5938098930205069821, "📷")}\n\n'
                f'نوبت: دزد {_heist_thief_icon()}\n\n'
                f'هکر دوربین ها رو هک کرده نوبت تو هست وارد جواهری شو و الماس ها رو بدزد {_hx(5938074049459523495, "💎")}\n\n'
                f'{_heist_police_line(h)}')
    if ph == "robbing":
        left = (HEIST_GEMS - h["i"]) * int(HEIST_GEM_SECONDS)
        return (f'{_heist_started()}\n\n'
                f'نوبت: دزد {_heist_thief_icon()}\n\n'
                f'{_hx(5940725397195853882, "💎")} تا برداشتن همه الماس ها: {_heist_fmt(left)}\n\n'
                f'الماس های برداشته شده: {h["i"]}/{HEIST_GEMS}\n\n'
                f'{_heist_police_line(h)}')
    return ""

def _heist_markup(h):
    ph = h["phase"]
    m = types.InlineKeyboardMarkup()
    if ph == "intro":
        m.row(colored_button("شروع دزدی", callback_data="heist|start", icon_custom_emoji_id="6012438204144165493"))
    elif ph in ("wait", "ready"):
        if ph == "wait":
            m.row(colored_button("پیوستن به دزدی", callback_data="heist|join", icon_custom_emoji_id="5938109560249127910"))
        else:
            m.row(colored_button("شروع سرقت", callback_data="heist|begin", icon_custom_emoji_id="5823445637231814311"))
        m.row(colored_button("انصراف از سرقت", callback_data="heist|cancel", icon_custom_emoji_id="5938290000415167172"))
    elif ph == "hack_wait":
        m.row(colored_button("شروع هک دوربین ها", callback_data="heist|hack", icon_custom_emoji_id="5938098930205069821"))
    elif ph == "thief_wait":
        m.row(colored_button("وارد شدن به جواهری", callback_data="heist|enter", icon_custom_emoji_id="5940396943866859681"))
    else:
        return None
    return m

def _heist_render(h, important=True):
    """پنل رو ادیت می‌کنه. ادیت‌های غیرضروری وقتی صف تلگرام شلوغه رد می‌شن."""
    if not important and _telegram_backlog() > HEIST_BUSY_BACKLOG:
        return
    safe_edit_message(_heist_text(h), h["chat_id"], h["message_id"],
                      reply_markup=_heist_markup(h), parse_mode="HTML")

def _heist_cleanup(h):
    h["done"] = True
    h["gen"] += 1
    for t in h.get("timers", []):
        try:
            t.cancel()
        except Exception:
            pass
    with _heist_lock:
        for p in (h["hacker"], h.get("thief")):
            if p and _heist_users.get(p["id"]) == h["key"]:
                _heist_users.pop(p["id"], None)
        _heists.pop(h["key"], None)

def _heist_schedule(h, delay, func, *args):
    t = _start_daemon_timer(max(0.0, delay), func, *args)
    h["timers"] = [x for x in h.get("timers", []) if x.is_alive()] + [t]

def _heist_schedule_tick(h, delay):
    _heist_schedule(h, delay, _heist_tick, h["key"], h["gen"])

def _heist_add_rings(user_id, n):
    if n <= 0:
        return True
    try:
        user = get_user(user_id)
        if not user:
            return False
        total = int(user.get("ring_diamonds", 0) or 0) + int(n)
        supabase.table("users").update({"ring_diamonds": total}).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logging.error(f"خطا در ثبت الماس انگشتر دزدی برای {user_id}: {e}")
        return False

def _heist_success(h):
    _heist_cleanup(h)
    profit = int(HEIST_PROFIT)
    share = profit // 2
    ring_share = int(HEIST_RING_REWARD) // 2
    for p in (h["hacker"], h["thief"]):
        try:
            if share > 0 and update_diamonds(p["id"], share) is None:
                logging.error(f"واریز سهم دزدی برای {p['id']} ناموفق بود")
            _heist_add_rings(p["id"], ring_share)
        except Exception as e:
            logging.error(f"خطا در پرداخت دزدی برای {p['id']}: {e}")
    d = _hx(5940725397195853882, "💎")
    r = _hx(5852787947559985777, "💍")
    text = (f'دزدی کامل شد {_hx(5938109560249127910, "✅")}\n\n'
            f'شما با موفقیت همه الماس ها رو دزدیدید {d}\n\n'
            f'{_hx(5819151717907832367, "💰")} سود کلی این سرقت {_hx(5820903824046432799, "💰")}\n'
            f'\u200f┘─ {share * 2:,} الماس {d}\n'
            f'\u200f┘─ {ring_share * 2} الماس انگشتر {r}\n\n'
            f'{_hx(5859432880442187580, "👥")} سهم هر شرکت کننده {_hx(5832240029446971049, "👥")}\n'
            f'\u200f┘─{share:,} الماس {d}\n'
            f'\u200f┘─ {ring_share} الماس انگشتر {r}')
    safe_edit_message(text, h["chat_id"], h["message_id"], reply_markup=None, parse_mode="HTML")

def _heist_caught(h):
    _heist_cleanup(h)
    fine = int(HEIST_FINE)
    for p in (h["hacker"], h["thief"]):
        try:
            # کل جریمه کسر می‌شه؛ اگه موجودی کم بود، موجودی منفی می‌شه.
            if fine > 0:
                update_diamonds(p["id"], -fine)
        except Exception as e:
            logging.error(f"خطا در کسر جریمه‌ی دزدی از {p['id']}: {e}")
    text = (f'دستگیر شدید {_hx(5856986522904959860, "🚔")}\n\n'
            f'{_hx(5859432880442187580, "🚔")} پلیس شما رو حین دزدی دستگیر کرد و شما رو به زندان منتقل کرد {_hx(5856986522904959860, "🚔")}\n\n'
            f'اما شما با پس دادن الماس ها و جریمه نقدی آزاد شدید {_hx(5857308847315624215, "🔓")}\n\n'
            f'جریمه نقدی: {fine:,} الماس {_hx(5940725397195853882, "💎")}')
    safe_edit_message(text, h["chat_id"], h["message_id"], reply_markup=None, parse_mode="HTML")

def _heist_police_arrive(key):
    h = _heists.get(key)
    if not h:
        return
    with h["lock"]:
        if h["done"]:
            return
        _heist_caught(h)

def _heist_expire(key, token=0):
    h = _heists.get(key)
    if not h:
        return
    with h["lock"]:
        if h["done"] or h["phase"] not in ("intro", "wait", "ready"):
            return
        if h.get("exp", 0) != token:
            return  # تایمر قدیمی؛ بعدش تایمر تازه گذاشته شده
        chat_id, message_id = h["chat_id"], h["message_id"]
        had_thief = bool(h.get("thief"))
        _heist_cleanup(h)
    text = (f'{_hx(5825656411517886523, "⏰")} زمان تموم شد{_hx(5818716826699307883, "⌛")}\n'
            + ("دزدی شروع نشد." if had_thief else "کسی به عنوان دزد نپیوست، دزدی لغو شد.")
            + f' {_hx(5819154526816444042, "❌")}')
    try:
        safe_edit_message(text, chat_id, message_id, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

def _heist_restart_expire(h):
    """تایمر بسته‌شدن پنل رو از نو شروع می‌کنه (تایمرهای قبلی بی‌اثر می‌شن)."""
    h["exp"] = h.get("exp", 0) + 1
    _heist_schedule(h, int(JOIN_TIMEOUT_SECONDS), _heist_expire, h["key"], h["exp"])

def _heist_tick(key, gen):
    h = _heists.get(key)
    if not h:
        return
    with h["lock"]:
        if h["done"] or h["gen"] != gen:
            return
        now = time.time()
        if now >= h["police_at"]:
            _heist_caught(h)
            return
        ph = h["phase"]
        if ph == "hacking":
            h["i"] += 1
            if h["i"] >= HEIST_CAMERAS:
                h["phase"] = "thief_wait"
                h["gen"] += 1
                _heist_render(h)
                _heist_schedule_tick(h, HEIST_IDLE_TICK)
            else:
                _heist_render(h, important=False)
                _heist_schedule_tick(h, h["phase_at"] + (h["i"] + 1) * int(HEIST_CAMERA_SECONDS) - now)
        elif ph == "robbing":
            h["i"] += 1
            if h["i"] >= HEIST_GEMS:
                _heist_success(h)
            else:
                _heist_render(h, important=False)
                _heist_schedule_tick(h, h["phase_at"] + (h["i"] + 1) * int(HEIST_GEM_SECONDS) - now)
        elif ph in ("hack_wait", "thief_wait"):
            _heist_render(h, important=False)
            _heist_schedule_tick(h, HEIST_IDLE_TICK)

@bot.message_handler(func=lambda m: text_is(m, "دزدی"))
def text_heist(message):
    if _feature_gate(message, "heist"):
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, _casino_err("دزدی فقط تو گروه کار می‌کنه"))
        return
    uid = message.from_user.id
    if not get_user(uid):
        bot.reply_to(message, _start_first_err())
        return
    left = _heist_locked_left(uid)
    if left > 0:
        bot.reply_to(message,
                     f'{_hx(5818736497649524154, "🌙")} هنوز شب نشده و زمان مناسبی برای دزدی نیست {_hx(5819154526816444042, "❌")}\n\n'
                     f'{_hx(5818984798298841943, "🌆")} تا شب: {_heist_fmt_long(left)} {_hx(5825656411517886523, "⏳")}',
                     parse_mode="HTML")
        return
    with _heist_lock:
        if uid in _heist_users:
            return
        _heist_users[uid] = None  # رزرو؛ جلوی دوبار ساخته شدن با دو پیام پشت سر هم
    h = {"phase": "intro", "chat_id": message.chat.id, "message_id": None, "key": None,
         "hacker": {"id": uid, "name": safe_html_name(message.from_user)}, "thief": None,
         "lock": threading.RLock(), "done": False, "gen": 0, "i": 0,
         "police_at": 0.0, "phase_at": 0.0, "timers": [], "exp": 0}
    try:
        sent = bot.send_message(message.chat.id, _heist_text(h), reply_markup=_heist_markup(h), parse_mode="HTML")
    except Exception as e:
        logging.error(f"ارسال پنل دزدی ناموفق بود: {e}")
        with _heist_lock:
            _heist_users.pop(uid, None)
        return
    key = (message.chat.id, sent.message_id)
    h["message_id"], h["key"] = sent.message_id, key
    with _heist_lock:
        _heists[key] = h
        _heist_users[uid] = key
    _heist_schedule(h, int(JOIN_TIMEOUT_SECONDS), _heist_expire, key, 0)

@bot.callback_query_handler(func=lambda call: call.data.startswith("heist|"))
def heist_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    if _feature_gate(call, "heist"):
        return
    act = call.data.split("|")[1]
    key = (call.message.chat.id, call.message.message_id)
    h = _heists.get(key)
    if not h:
        return
    uid = call.from_user.id
    with h["lock"]:
        if h["done"]:
            return
        ph = h["phase"]
        is_hacker = uid == h["hacker"]["id"]
        is_thief = bool(h["thief"]) and uid == h["thief"]["id"]
        if h["police_at"] and time.time() >= h["police_at"]:
            return

        if act == "start" and ph == "intro" and is_hacker:
            if not _heist_has_gear(uid, "hacker"):
                _heist_no_gear_exit(h)
                return
            h["phase"] = "wait"
            _heist_restart_expire(h)
            _heist_render(h)

        elif act == "join" and ph == "wait" and not is_hacker:
            with _heist_lock:
                if uid in _heist_users:
                    return
            if not get_user(uid) or _heist_locked_left(uid) > 0 or not _heist_has_gear(uid, "thief"):
                return
            with _heist_lock:
                if uid in _heist_users:
                    return
                _heist_users[uid] = key
            h["thief"] = {"id": uid, "name": safe_html_name(call.from_user)}
            h["phase"] = "ready"
            _heist_restart_expire(h)
            _heist_render(h)

        elif act == "cancel" and ph in ("wait", "ready"):
            if is_hacker:
                if h["thief"]:
                    with _heist_lock:
                        if _heist_users.get(h["thief"]["id"]) == key:
                            _heist_users.pop(h["thief"]["id"], None)
                h["thief"] = None
                h["phase"] = "intro"
                _heist_restart_expire(h)
                _heist_render(h)
            elif is_thief:
                with _heist_lock:
                    if _heist_users.get(uid) == key:
                        _heist_users.pop(uid, None)
                h["thief"] = None
                h["phase"] = "wait"
                _heist_restart_expire(h)
                _heist_render(h)

        elif act == "begin" and ph == "ready" and is_hacker and h["thief"]:
            hid, tid = h["hacker"]["id"], h["thief"]["id"]
            if not _heist_has_gear(hid, "hacker"):
                _heist_no_gear_exit(h)
                return
            if not _heist_has_gear(tid, "thief"):
                with _heist_lock:
                    if _heist_users.get(tid) == key:
                        _heist_users.pop(tid, None)
                h["thief"] = None
                h["phase"] = "wait"
                _heist_render(h)
                return
            _heist_consume(hid, "hacker")
            _heist_consume(tid, "thief")
            now = time.time()
            h["phase"] = "hack_wait"
            h["gen"] += 1
            h["police_at"] = now + int(HEIST_POLICE_SECONDS)
            _heist_schedule(h, int(HEIST_POLICE_SECONDS), _heist_police_arrive, key)
            _heist_render(h)
            _heist_schedule_tick(h, HEIST_IDLE_TICK)

        elif act == "hack" and ph == "hack_wait" and is_hacker:
            now = time.time()
            h["phase"] = "hacking"
            h["gen"] += 1
            h["i"] = 0
            h["phase_at"] = now
            _heist_render(h)
            _heist_schedule_tick(h, int(HEIST_CAMERA_SECONDS))

        elif act == "enter" and ph == "thief_wait" and is_thief:
            now = time.time()
            h["phase"] = "robbing"
            h["gen"] += 1
            h["i"] = 0
            h["phase_at"] = now
            _heist_render(h)
            _heist_schedule_tick(h, int(HEIST_GEM_SECONDS))



# ================== مارکت ==================
_mkt_tokens = {}   # (chat_id, message_id) -> nonce آخرین ادیت موقت (برای برگشت خودکار بعد ۵ ثانیه)

def _mkt_markup_home(owner):
    m = types.InlineKeyboardMarkup()
    m.row(colored_button("دزدی", callback_data=f"mkt|heist|{owner}", icon_custom_emoji_id="5935933089866846598"))
    m.row(colored_button("الماس انگشتر", callback_data=f"mkt|ring|{owner}", icon_custom_emoji_id="5852787947559985777"))
    return m

def _mkt_text_home():
    return (f'مارکت {_hx(5823445637231814311, "🛒")}\n\n'
            f'اینجا فروشگاهه هرچیزی لازم داری بخر {_hx(6012438204144165493, "🛍")}\n\n'
            f'وسایل موجود {_hx(5820903824046432799, "📦")}')

def _mkt_text_heist(chat_id=None):
    d = _hx(5940725397195853882, "💎")
    c = _city_get(chat_id) if chat_id is not None else {"hacker_price": int(HEIST_PRICE_HACKER), "thief_price": int(HEIST_PRICE_THIEF)}
    return (f'بخش وسایل دزدی فروشگاه {_hx(5820903824046432799, "📦")}\n\n'
            f'وسایل موجود برای دزدی {_hx(5820903824046432799, "📦")}\n\n'
            f'وسایل مورد نیاز هکر {_hx(5938072524746137114, "💻")}\n'
            f'\u200f┘─ قیمت {int(c["hacker_price"]):,} الماس {d}\n\n'
            f'وسایل مورد نیاز دزد {_hx(5823445637231814311, "🥷")}\n'
            f'\u200f┘─ قیمت {int(c["thief_price"]):,} الماس {d}\n\n'
            f'برای خرید روی وسیله مد نظر کلیک کنید {_hx(5830144944399981619, "👇")}')

def _mkt_markup_heist(owner):
    m = types.InlineKeyboardMarkup()
    m.row(colored_button("وسایل هکر", callback_data=f"mkt|buy|hacker|{owner}", icon_custom_emoji_id="5938072524746137114"),
          colored_button("وسایل دزد", callback_data=f"mkt|buy|thief|{owner}", icon_custom_emoji_id="5823445637231814311"))
    m.row(back_button(f"mkt|home|{owner}"))
    return m

def _mkt_text_ring(chat_id):
    d = _hx(5940725397195853882, "💎")
    c = _city_get(chat_id)
    return (f'بخش الماس انگشتر فروشگاه {_hx(5820903824046432799, "📦")}\n\n'
            f'موجودی الماس انگشتر مارکت: {c["rings"]:,}{_hx(5820903824046432799, "💍")}\n'
            f'\u200f┘─ قیمت {c["ring_price"]:,} الماس {d}\n\n'
            f'برای خرید روی وسیله مد نظر کلیک کنید {_hx(5830144944399981619, "👇")}')

def _mkt_markup_ring(owner):
    m = types.InlineKeyboardMarkup()
    m.row(colored_button("خرید الماس انگشتر", callback_data=f"mkt|ringbuy|{owner}", icon_custom_emoji_id="5820903824046432799"))
    m.row(back_button(f"mkt|home|{owner}"))
    return m

def _mkt_buy_ring(chat_id, uid):
    """خرید یک الماس انگشتر از موجودی مارکت (خزانه‌ی شهر). برمی‌گردونه (True, None) یا (False, 'stock'|'funds'|'start'|'fail')."""
    with _city_lock:
        d = _city_get(chat_id)
        price = int(d["ring_price"])
        if d["rings"] <= 0:
            return False, "stock"
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user:
                return False, "start"
            if int(user.get("diamonds", 0) or 0) < price:
                return False, "funds"
            if update_diamonds(uid, -price) is None:
                return False, "fail"
            try:
                have = int(user.get("ring_diamonds", 0) or 0)
                supabase.table("users").update({"ring_diamonds": have + 1}).eq("user_id", uid).execute()
            except Exception as ex:
                logging.error(f"خرید الماس انگشتر از مارکت ({uid}): {ex}")
                update_diamonds(uid, price)
                return False, "fail"
        d["rings"] -= 1
        d["diamonds"] += price
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی خزانه‌ی شهر {chat_id} بعد از خرید الماس انگشتر ناموفق بود (فقط تو حافظه)")
    return True, None

def _mkt_revert(key, nonce, owner, section="heist"):
    if _mkt_tokens.get(key) != nonce:
        return
    _mkt_tokens.pop(key, None)
    if section == "ring":
        safe_edit_message(_mkt_text_ring(key[0]), key[0], key[1], reply_markup=_mkt_markup_ring(owner), parse_mode="HTML")
    else:
        safe_edit_message(_mkt_text_heist(key[0]), key[0], key[1], reply_markup=_mkt_markup_heist(owner), parse_mode="HTML")

@bot.message_handler(func=lambda m: text_is(m, "مارکت"))
def text_market(message):
    if _feature_gate(message, "market"):
        return
    uid = message.from_user.id
    safe_send_message(message.chat.id, _mkt_text_home(), reply_markup=_mkt_markup_home(uid),
                      parse_mode="HTML", reply_to_message_id=message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mkt|"))
def market_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    if _feature_gate(call, "market"):
        return
    owner = extract_owner_id(call.data)
    if owner is None or call.from_user.id != owner:
        return
    parts = call.data.split("|")
    act = parts[1]
    chat_id, message_id = call.message.chat.id, call.message.message_id
    key = (chat_id, message_id)

    def _flash(text, section):
        nonce = time.time()
        _mkt_tokens[key] = nonce
        if len(_mkt_tokens) > 5000:
            _mkt_tokens.clear()
            _mkt_tokens[key] = nonce
        safe_edit_message(text, chat_id, message_id, reply_markup=None, parse_mode="HTML")
        _start_daemon_timer(PANEL_REVERT_SECONDS, _mkt_revert, key, nonce, owner, section)

    if act == "home":
        _mkt_tokens.pop(key, None)
        safe_edit_message(_mkt_text_home(), chat_id, message_id, reply_markup=_mkt_markup_home(owner), parse_mode="HTML")
    elif act == "heist":
        _mkt_tokens.pop(key, None)
        safe_edit_message(_mkt_text_heist(chat_id), chat_id, message_id, reply_markup=_mkt_markup_heist(owner), parse_mode="HTML")
    elif act == "ring":
        _mkt_tokens.pop(key, None)
        safe_edit_message(_mkt_text_ring(chat_id), chat_id, message_id, reply_markup=_mkt_markup_ring(owner), parse_mode="HTML")
    elif act == "ringbuy":
        ok, err = _mkt_buy_ring(chat_id, owner)
        if ok:
            text = f'الماس انگشتر خریده شد {_hx(5830144944399981619, "✅")}'
        else:
            text = {"funds": "موجودی الماس کافی نیست", "stock": "موجودی الماس انگشتر مارکت کافی نیست",
                    "start": "اول باید بات رو استارت کنی"}.get(err, "خرید انجام نشد، دوباره امتحان کن")
            text = f'{html.escape(text)} {_hx(5819154526816444042, "❌")}'
        _flash(text, "ring")
    elif act == "buy" and len(parts) >= 4 and parts[2] in ("hacker", "thief"):
        role = parts[2]
        price = _city_get(chat_id)["hacker_price" if role == "hacker" else "thief_price"]
        count, err = _heist_buy(owner, role, price)
        if err:
            text = {"funds": "موجودی الماس کافی نیست", "start": "اول باید بات رو استارت کنی"}.get(err, "خرید انجام نشد، دوباره امتحان کن")
            text = f'{html.escape(text)} {_hx(5819154526816444042, "❌")}'
        else:
            name = "هکر" if role == "hacker" else "دزد"
            text = (f'وسایل {name} خریداری شد {_hx(5938109560249127910, "✅")}\n\n'
                    f'موجودی از این وسایل: {count} {_hx(5938027397524754858, "📦")}')
        _flash(text, "heist")


# ================== شهر ==================
# کلمه‌ی «شهر» تو گروه پنل شهر رو باز می‌کنه. اطلاعات هر گروه جدا تو bot_settings (کلید city_<chat_id>) ذخیره می‌شه.
# قیمت مغازه، قیمت برق و تعداد مغازه‌ها فعلاً صفره و بعداً از پنل/دستور تنظیم می‌شن.
_city_lock = threading.RLock()
_city_busy = set()        # (chat_id, message_id) تاییدهایی که همین الان تو حال پردازشن (جلوگیری از دوبار زدن)
_city_mayor_cache = {}    # chat_id -> (expire_ts, user_id, name_html)
_city_tokens = {}         # (chat_id, message_id) -> nonce برای برگشت خودکار به پنل اصلی


def _city_key(chat_id):
    return f"city_{int(chat_id)}"

def _city_get(chat_id):
    raw = _SETTINGS.get(_city_key(chat_id)) or {}
    data = {
        "diamonds": int(raw.get("diamonds", 0) or 0),
        "rings": int(raw.get("rings", 0) or 0),
        "shops": int(raw.get("shops", 0) or 0),
        "shop_price": int(raw.get("shop_price", 0) or 0),
        "kw_price": int(raw.get("kw_price", 0) or 0),
        "rent_price": int(raw.get("rent_price", 0) or 0),
        "shops_data": {str(k): dict(v) for k, v in (raw.get("shops_data") or {}).items()},
        "ring_price": int(raw.get("ring_price", 0) or 0) or CITY_DEFAULT_RING_PRICE,
        "hacker_price": int(raw.get("hacker_price", 0) or 0) or int(HEIST_PRICE_HACKER),
        "thief_price": int(raw.get("thief_price", 0) or 0) or int(HEIST_PRICE_THIEF),
        "donors_d": dict(raw.get("donors_d") or {}),
        "donors_r": dict(raw.get("donors_r") or {}),
        "subsidy_regs": {str(k): str(v) for k, v in (raw.get("subsidy_regs") or {}).items()},
        "shops_archive": {str(k): dict(v) for k, v in (raw.get("shops_archive") or {}).items()},
        "drills_data": {str(k): dict(v) for k, v in (raw.get("drills_data") or {}).items()},
    }
    data["shops"] = len(data["shops_data"])   # تعداد مغازه‌های فعال
    return data

def _credit_tax(chat_id, tax):
    """مالیات بازی‌های یه گپ می‌ره تو خزانه‌ی شهر همون گپ."""
    try:
        tax = int(tax or 0)
        if tax <= 0 or chat_id is None:
            return
        with _city_lock:
            d = _city_get(chat_id)
            d["diamonds"] += tax
            if not _city_save(chat_id, d):
                logging.error(f"ذخیره‌ی مالیات {tax} برای خزانه‌ی شهر {chat_id} ناموفق بود (فقط تو حافظه)")
    except Exception as e:
        logging.error(f"خطا در واریز مالیات به خزانه‌ی شهر {chat_id}: {e}")

def _city_save(chat_id, data):
    return setting_save(_city_key(chat_id), data)

def _city_mayor(chat_id):
    """(آیدی, اسم HTML) مالک گروه = شهردار. ۱۰ دقیقه کش می‌شه تا هر بار به تلگرام درخواست نره."""
    now = time.time()
    cached = _city_mayor_cache.get(chat_id)
    if cached and cached[0] > now:
        return cached[1], cached[2]
    mid, name = None, "نامشخص"
    try:
        for adm in bot.get_chat_administrators(chat_id):
            if getattr(adm, "status", "") == "creator":
                mid, name = adm.user.id, safe_html_name(adm.user)
                break
    except Exception as e:
        logging.error(f"خطا در گرفتن مالک گروه {chat_id}: {e}")
        return None, name
    if len(_city_mayor_cache) > 2000:
        _city_mayor_cache.clear()
    _city_mayor_cache[chat_id] = (now + 600, mid, name)
    return mid, name

def _city_mayor_html(chat_id):
    return _city_mayor(chat_id)[1]

def _city_btn(text, callback_data, icon_id, style="primary"):
    b = colored_button(text, callback_data=callback_data, icon_custom_emoji_id=icon_id)
    setattr(b, "style", style)
    return b

def _city_home_text(chat_id):
    e = _hx
    d = _city_get(chat_id)
    dia = e(5940725397195853882, "💎")
    ring = e(5852787947559985777, "💍")
    return (
        f'{e(5843493805835165294, "🏙")}به شهر خوش اومدید{e(5832433801191498078, "🎉")}\n\n'
        f'شهردار: {_city_mayor_html(chat_id)}{e(5830144944399981619, "👑")}\n\n'
        f'{e(5823592920250327036, "🏪")} مغازه های شهر: {d["shops"]:,}\n\n'
        f'{e(5823445637231814311, "💰")} قیمت هر مغازه: {d["shop_price"]:,}{dia}\n\n'
        f'{e(5816739230482701944, "⚡")} قیمت هر KW برق: {d["kw_price"]:,}{dia}\n\n'
        f'قیمت ها توسط شهردار تنظیم میشود.{e(5818716826699307883, "ℹ️")}\n\n'
        f'{e(5832240029446971049, "🏦")}موجودی خزانه شهر{e(5820903824046432799, "👇")}\n'
        f'\u200f┘─ {d["diamonds"]:,} الماس{dia}\n'
        f'\u200f┘─ {d["rings"]:,} الماس انگشتر{ring}'
    )

def _city_home_markup(uid):
    m = types.InlineKeyboardMarkup()
    m.row(_city_btn("کمک به خزانه شهر", f"city|help|{uid}", "5818976337213266965"))
    m.row(_city_btn("لیدر برد کمک به شهر", f"city|lb|{uid}", "5836866392124563486"))
    return m

def _city_show_home(chat_id, message_id, uid):
    safe_edit_message(_city_home_text(chat_id), chat_id, message_id,
                      reply_markup=_city_home_markup(uid), parse_mode="HTML")

def _city_revert(key, nonce, uid):
    if _city_tokens.get(key) != nonce:
        return
    _city_tokens.pop(key, None)
    _city_show_home(key[0], key[1], uid)

def _city_kind_texts(kind):
    e = _hx
    if kind == "r":
        return "الماس انگشتر", e(5852787947559985777, "💍")
    return "الماس", e(5940725397195853882, "💎")

@bot.message_handler(func=lambda m: text_is(m, "شهر"))
def text_city(message):
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, _casino_err("شهر فقط تو گروه کار می‌کنه"), parse_mode="HTML")
        return
    uid = message.from_user.id
    if not get_user(uid):
        bot.reply_to(message, _start_first_err(), parse_mode="HTML")
        return
    safe_send_message(message.chat.id, _city_home_text(message.chat.id),
                      reply_markup=_city_home_markup(uid), parse_mode="HTML",
                      reply_to_message_id=message.message_id)

def _city_leaderboard_text(chat_id, uid, kind):
    e = _hx
    d = _city_get(chat_id)
    donors = d["donors_r" if kind == "r" else "donors_d"]
    ranked = sorted(donors.items(), key=lambda kv: int(kv[1].get("amt", 0) or 0), reverse=True)
    name_word, icon = _city_kind_texts(kind)
    medals = [5832692422647226240, 5834620746999012948, 5832346686369832003]
    lines = [f'{e(5818913299978263825, "🏆")} رتبه بندی شهر {e(5938398023137628324, "🏆")}', "",
             f'{e(5938515112536051725, "📊")} رتبه بندی بیشترین {name_word} اهدا شده{icon}', ""]
    if not ranked:
        lines += ["هنوز کسی کمک نکرده.", ""]
    for i, (_, info) in enumerate(ranked[:3]):
        lines.append(f'{e(medals[i], "🏅")} {html.escape(str(info.get("name") or "کاربر"))}')
        lines.append(f'\u200f┘─ {e(5823445637231814311, "💰")} {name_word} های اهدا شده : {int(info.get("amt", 0) or 0):,} {icon}')
        lines.append("")
    rank = next((i for i, (k, _) in enumerate(ranked, 1) if k == str(uid)), None)
    lines.append(f'{e(5845858434439716957, "👤")} رتبه شما : {rank if rank else "—"}')
    return "\n".join(lines)

def _city_donate(chat_id, from_user, kind, amount):
    """برمی‌گردونه (True, None) یا (False, پیام خطا). اول از کاربر کم می‌کنه، بعد به خزانه اضافه می‌کنه."""
    uid = from_user.id
    with _get_user_financial_lock(uid):
        user = get_user(uid)
        if not user:
            return False, "اول باید بات رو استارت کنی"
        if kind == "r":
            have = int(user.get("ring_diamonds", 0) or 0)
            if have < amount:
                return False, f"الماس انگشتر کافی نداری. موجودی: {have:,}"
            try:
                supabase.table("users").update({"ring_diamonds": have - amount}).eq("user_id", uid).execute()
            except Exception as ex:
                logging.error(f"کسر الماس انگشتر برای اهدا به شهر ({uid}): {ex}")
                return False, "اهدا انجام نشد، دوباره امتحان کن"
        else:
            have = int(user.get("diamonds", 0) or 0)
            if have < amount:
                return False, f"الماس کافی نداری. موجودی: {have:,}"
            if update_diamonds(uid, -amount) is None:
                return False, "اهدا انجام نشد، دوباره امتحان کن"
    with _city_lock:
        d = _city_get(chat_id)
        d["rings" if kind == "r" else "diamonds"] += amount
        board = d["donors_r" if kind == "r" else "donors_d"]
        prev = int((board.get(str(uid)) or {}).get("amt", 0) or 0)
        board[str(uid)] = {"name": (getattr(from_user, "first_name", None) or "کاربر").strip()[:40], "amt": prev + amount}
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی خزانه‌ی شهر {chat_id} در دیتابیس ناموفق بود (فقط تو حافظه)")
    return True, None

def _city_amount_step(message, kind, panel_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    name_word, icon = _city_kind_texts(kind)
    amount = parse_amount(message.text.strip() if message.text else "")
    if amount is None:
        msg = bot.reply_to(message, _casino_err("مقدار نامعتبره. یه عدد مثبت بفرست"), parse_mode="HTML")
        register_timed_next_step_handler(msg, _city_amount_step, kind, panel_mid, expected_user_id=uid)
        return
    m = types.InlineKeyboardMarkup()
    m.row(_city_btn("بله", f"city|ok|{kind}|{amount}|{uid}", "5938109560249127910", "success"),
          _city_btn("خیر", f"city|no|{uid}", "5819154526816444042", "danger"))
    safe_edit_message(f'آیا از اهدا {amount:,} {name_word}{icon} مطمئنید؟{_hx(5818976337213266965, "❓")}',
                      chat_id, panel_mid, reply_markup=m, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("city|"))
def city_callback(call):
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    parts = call.data.split("|")
    act = parts[1]
    chat_id, mid = call.message.chat.id, call.message.message_id
    e = _hx
    cancel_pending_step(chat_id, uid)
    _city_tokens.pop((chat_id, mid), None)

    if act == "home" or act == "no":
        _city_show_home(chat_id, mid, uid)

    elif act == "help":
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("الماس", f"city|pick|d|{uid}", "5940725397195853882"),
              _city_btn("الماس انگشتر", f"city|pick|r|{uid}", "5852787947559985777"))
        m.row(back_button(f"city|home|{uid}"))
        safe_edit_message(f'خیلی ممنون برای کمک به شهر{e(5832433801191498078, "🎉")}\n\n'
                          f'چه چیزی میخواید به خزانه اهدا کنید؟{e(5820903824046432799, "👇")}',
                          chat_id, mid, reply_markup=m, parse_mode="HTML")

    elif act == "pick" and len(parts) >= 4 and parts[2] in ("d", "r"):
        kind = parts[2]
        name_word, icon = _city_kind_texts(kind)
        m = types.InlineKeyboardMarkup()
        m.row(back_button(f"city|help|{uid}"))
        safe_edit_message(f'{e(5832240029446971049, "🏦")}مقدار {name_word}ی که میخواید به خزانه اهدا کنید رو ارسال کنید{e(5820903824046432799, "👇")}',
                          chat_id, mid, reply_markup=m, parse_mode="HTML")
        register_timed_next_step_handler(call.message, _city_amount_step, kind, mid, expected_user_id=uid)

    elif act == "ok" and len(parts) >= 5 and parts[2] in ("d", "r"):
        kind = parts[2]
        try:
            amount = int(parts[3])
        except ValueError:
            return
        if amount <= 0:
            return
        key = (chat_id, mid)
        with _city_lock:
            if key in _city_busy:
                return
            _city_busy.add(key)
        try:
            ok, err = _city_donate(chat_id, call.from_user, kind, amount)
        finally:
            with _city_lock:
                _city_busy.discard(key)
        name_word, icon = _city_kind_texts(kind)
        if ok:
            nonce = time.time()
            if len(_city_tokens) > 5000:
                _city_tokens.clear()
            _city_tokens[key] = nonce
            safe_edit_message(f'مقدار {amount:,} {name_word}{icon} اهدا شد{e(5832433801191498078, "🎉")}',
                              chat_id, mid, reply_markup=None, parse_mode="HTML")
            _start_daemon_timer(PANEL_REVERT_SECONDS, _city_revert, key, nonce, uid)
        else:
            m = types.InlineKeyboardMarkup()
            m.row(back_button(f"city|home|{uid}"))
            safe_edit_message(f'{html.escape(err or "اهدا انجام نشد")} {e(5819154526816444042, "❌")}',
                              chat_id, mid, reply_markup=m, parse_mode="HTML")

    elif act == "lb":
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("بیشترین الماس اهدا شده", f"city|top|d|{uid}", "5940725397195853882"))
        m.row(_city_btn("بیشترین الماس انگشتر اهدا شده", f"city|top|r|{uid}", "5852787947559985777"))
        m.row(back_button(f"city|home|{uid}"))
        safe_edit_message(f'لیدر برد به دو بخش تقسیم میشه{e(5818716826699307883, "ℹ️")}\n\n'
                          f'لیدر برد بیشترین الماس انگشتر اهدا شده{e(5852787947559985777, "💍")}\n\n'
                          f'لیدر برد بیشترین الماس اهدا شده{e(5940725397195853882, "💎")}',
                          chat_id, mid, reply_markup=m, parse_mode="HTML")

    elif act == "top" and len(parts) >= 4 and parts[2] in ("d", "r"):
        m = types.InlineKeyboardMarkup()
        m.row(back_button(f"city|lb|{uid}"))
        safe_edit_message(_city_leaderboard_text(chat_id, uid, parts[2]), chat_id, mid,
                          reply_markup=m, parse_mode="HTML")


# ---------- پنل شهردار ----------
_MAYOR_FIELDS = {
    "shop":   ("shop_price",   "مغازه",         "5832397371278892338"),
    "rent":   ("rent_price",   "اجاره مغازه",   "6012438204144165493"),
    "kw":     ("kw_price",     "برق",           "5816739230482701944"),
    "ring":   ("ring_price",   "الماس انگشتر",  "5852787947559985777"),
    "hacker": ("hacker_price", "وسایل هکر",     "5938072524746137114"),
    "thief":  ("thief_price",  "وسایل دزد",     "5823445637231814311"),
}

def _mayor_text(chat_id):
    e = _hx
    d = _city_get(chat_id)
    dia = e(5940725397195853882, "💎")
    return (
        f'شهردار خوش اومدید{e(5818866927216369505, "👑")}\n\n'
        f'این پنل برای مدیریت شهر هست{e(5832397371278892338, "🏙")}\n\n'
        f'{e(5816913107938712568, "🏪")}قیمت هر مغازه: {d["shop_price"]:,}{dia}\n'
        f'{e(6012438204144165493, "🏠")}قیمت روزانه اجاره مغازه: {d["rent_price"]:,}{dia}\n'
        f'{e(5816739230482701944, "⚡")}قیمت برق فعلی: {d["kw_price"]:,}{dia}\n'
        f'{e(5852787947559985777, "💍")}قیمت الماس انگشتر: {d["ring_price"]:,}{dia}\n'
        f'{e(5823445637231814311, "🥷")}قیمت وسایل دزدی: هکر {d["hacker_price"]:,} | دزد {d["thief_price"]:,}{dia}'
    )

def _mayor_markup(uid):
    m = types.InlineKeyboardMarkup()
    for k, (_, label, icon) in _MAYOR_FIELDS.items():
        m.row(_city_btn(f"تنظیم قیمت {label}", f"mayor|set|{k}|{uid}", icon))
    return m

@bot.message_handler(func=lambda m: text_is(m, "شهردار"))
def text_mayor(message):
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, _casino_err("این بخش فقط تو گروه کار می‌کنه"), parse_mode="HTML")
        return
    uid = message.from_user.id
    mayor_id, _ = _city_mayor(message.chat.id)
    if mayor_id is None or uid != mayor_id:
        bot.reply_to(message, f'فقط شهردار به این قسمت دسترسی داره{_hx(5819154526816444042, "❌")}', parse_mode="HTML")
        return
    safe_send_message(message.chat.id, _mayor_text(message.chat.id), reply_markup=_mayor_markup(uid),
                      parse_mode="HTML", reply_to_message_id=message.message_id)

def _mayor_price_step(message, field_key, panel_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    field, label, _ = _MAYOR_FIELDS[field_key]
    amount = parse_amount(message.text.strip() if message.text else "")
    if amount is None:
        msg = bot.reply_to(message, _casino_err("قیمت نامعتبره. یه عدد مثبت بفرست"), parse_mode="HTML")
        register_timed_next_step_handler(msg, _mayor_price_step, field_key, panel_mid, expected_user_id=uid)
        return
    mayor_id, _ = _city_mayor(chat_id)
    if mayor_id != uid:
        return
    with _city_lock:
        d = _city_get(chat_id)
        d[field] = amount
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی قیمت شهر {chat_id} در دیتابیس ناموفق بود (فقط تو حافظه)")
    safe_edit_message(_mayor_text(chat_id), chat_id, panel_mid, reply_markup=_mayor_markup(uid), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("mayor|"))
def mayor_callback(call):
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    parts = call.data.split("|")
    act = parts[1]
    chat_id, mid = call.message.chat.id, call.message.message_id
    cancel_pending_step(chat_id, uid)
    if _city_mayor(chat_id)[0] != uid:
        return
    if act == "home":
        safe_edit_message(_mayor_text(chat_id), chat_id, mid, reply_markup=_mayor_markup(uid), parse_mode="HTML")
    elif act == "set" and len(parts) >= 4 and parts[2] in _MAYOR_FIELDS:
        key = parts[2]
        label = _MAYOR_FIELDS[key][1]
        m = types.InlineKeyboardMarkup()
        m.row(back_button(f"mayor|home|{uid}"))
        safe_edit_message(f'برای تنظیم قیمت {label} قیمت جدید را ارسال کنید{_hx(5820903824046432799, "👇")}',
                          chat_id, mid, reply_markup=m, parse_mode="HTML")
        register_timed_next_step_handler(call.message, _mayor_price_step, key, mid, expected_user_id=uid)


# ================== مغازه جواهری ==================
# کلمه‌ی «جواهری» -> اگه تو این شهر مغازه داری پنل مغازه، وگرنه پنل خرید/اجاره.
# اطلاعات مغازه‌ها تو داده‌ی شهر همون گپ (shops_data) ذخیره می‌شه.
# پول خرج‌شده تو مغازه (خرید، اجاره، قبض برق، واردات، هزینه‌ی اضافه‌ی صادرات) می‌ره تو خزانه‌ی شهر.
_shop_views = {}   # (chat_id, uid) -> {"mid":..., "view": "panel"|"other"}  (فقط تو حافظه)

def _shop_now():
    return datetime.now(TEHRAN_TZ)

def _shop_today():
    return _shop_now().date().isoformat()

def _shop_parse(iso):
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=TEHRAN_TZ)
    except Exception:
        return None

def _fmt_kw(x):
    return f"{float(x):,.2f}".rstrip("0").rstrip(".")

def _fmt_minutes(seconds):
    m = max(1, int(round(seconds / 60)))
    if m < 60:
        return f"{m} دقیقه"
    h, r = divmod(m, 60)
    return f"{h} ساعت" if r == 0 else f"{h} ساعت و {r} دقیقه"

def _fmt_pct(x):
    return f"{float(x) * 100:g}"

def _shop_debt(sh):
    return int(math.ceil(float(sh.get("debt", 0) or 0) - 1e-9))

def _shop_set_view(chat_id, uid, mid, view):
    if len(_shop_views) > 5000:
        _shop_views.clear()
    _shop_views[(chat_id, uid)] = {"mid": mid, "view": view}

def _shop_choose_text():
    e = _hx
    return (f'مغازه جواهری هنوز نداری{e(5935933089866846598, "🏪")}\n\n'
            f'یه مغازه بخر یا اجاره کن{e(5818866927216369505, "💡")}\n\n'
            f'روش خرید مغازه خود را انتخاب کنید{e(5820903824046432799, "👇")}')

def _shop_choose_markup(uid):
    m = types.InlineKeyboardMarkup()
    m.row(_city_btn("خرید", f"shop|buy|{uid}", "6012628067468451853"),
          _city_btn("اجاره", f"shop|rent|{uid}", "6012438204144165493"))
    return m

def _shop_panel_text(chat_id, uid):
    e = _hx
    sh = _city_get(chat_id)["shops_data"].get(str(uid)) or {}
    ring = e(5852787947559985777, "💍")
    dia = e(5940725397195853882, "💎")
    return (f'مغازه جواهری شما{e(5820903824046432799, "🏪")}\n\n'
            f'الماس انگشتر موجود در انبار: {int(sh.get("rings", 0) or 0):,}{ring}\n\n'
            f'انگشترهای موجود برای فروش: {int(sh.get("produced", 0) or 0):,}{ring}\n\n'
            f'برق مصرف شده: {_fmt_kw(sh.get("kw", 0) or 0)} KW {e(5830381159011326872, "🔌")}\n\n'
            f'بدهی قبض برق: {_shop_debt(sh):,}{dia}\n\n'
            f'انگشترهای فروخته شده: {int(sh.get("sold", 0) or 0):,}{ring}\n\n'
            f'قیمت فروش هر انگشتر: {int(SHOP_RING_SELL_PRICE):,}{dia}')

def _shop_panel_markup(chat_id, uid):
    sh = _city_get(chat_id)["shops_data"].get(str(uid)) or {}
    m = types.InlineKeyboardMarkup()
    if sh.get("prod"):
        m.row(_city_btn("در حال تولید", f"shop|do|prod|{uid}", "5938109560249127910", "success"))
    else:
        m.row(_city_btn("شروع تولید", f"shop|do|start|{uid}", "5940505632309252816"))
    m.row(_city_btn("اضافه کردن الماس انگشتر", f"shop|do|add|{uid}", "5940582340425159463"))
    m.row(_city_btn("قبض برق", f"shop|do|bill|{uid}", "5816739230482701944"))
    m.row(_city_btn("صادرات", f"shop|ex|{uid}", "5888594273862950655"),
          _city_btn("واردات", f"shop|im|{uid}", "5889002570633977838"))
    if sh.get("mode") == "rent":
        m.row(_city_btn("قرارداد", f"shop|contract|{uid}", "5888937012253171131"))
    return m

def _shop_contract_text(sh):
    e = _hx
    exp = _shop_parse(sh.get("rent_expires"))
    left = max(0.0, (exp - _shop_now()).total_seconds()) if exp else 0.0
    return (f'قرارداد شما{e(5888937012253171131, "🧾")}\n\n'
            f'مهلت باقیمانده اجاره شما: {_fmt_minutes(left)}{e(5825656411517886523, "⏰")}\n\n'
            f'{e(5819051035284479206, "❗")}در صورتی که مهلت اجاره شما تموم بشه و تمدید نکنید قرارداد رو مغازه خودتون رو از دست خواهی داد{e(5938167138580741203, "⚠️")}')

def _shop_confirm_text(chat_id, mode):
    e = _hx
    d = _city_get(chat_id)
    if mode == "buy":
        title, icon, price, q = "خرید مغازه جواهری", 6014651259057873388, d["shop_price"], "آیا از خرید مغازه مطمئنی؟"
        extra = ""
    else:
        title, icon, price, q = "اجاره مغازه جواهری", 6012438204144165493, d["rent_price"], "آیا از اجاره مغازه مطمئنی؟"
        extra = f'اجاره به صورت روزانه هست و هر روز باید اجاره رو پرداخت کنید{e(5818716826699307883, "ℹ️")}\n\n'
    return (f'{title}{e(icon, "🏪")}\n\n'
            f'قیمت: {price:,}{e(5929272673627546181, "💎")}\n\n'
            f'{extra}'
            f'{e(5819051035284479206, "❓")}{q}{e(5820903824046432799, "👇")}')

def _shop_prod_text(sh, remaining=False):
    e = _hx
    pr = sh.get("prod") or {}
    n = int(pr.get("remaining", 0) or 0) if remaining else int(pr.get("total", 0) or 0)
    label = "تعداد الماس های باقی مانده" if remaining else "تعداد الماس ها"
    return (f'{e(5938348905891632091, "💎")}الماس ها رفتن برای تراشکاری{e(5830144944399981619, "✅")}\n\n'
            f'{e(5818984798298841943, "⏱")}زمان آماده شدن هر الماس: {_fmt_minutes(SHOP_RING_SECONDS)}{e(5825656411517886523, "⏰")}\n\n'
            f'{label}: {n:,}{e(5940725397195853882, "💎")}')

def _shop_bill_text(sh):
    e = _hx
    return (f'{e(5938206901387924708, "🧾")}قبض برق{e(5888937012253171131, "🧾")}\n\n'
            f'{e(5830381159011326872, "⚡")}در هر ساعت استفاده از برق {_fmt_kw(SHOP_KW_PER_HOUR)}KW برق مصرف میشه{e(5830144944399981619, "ℹ️")}\n\n'
            f'{e(5888937012253171131, "🧾")}قبض برق فعلی: {_shop_debt(sh):,}{e(5940725397195853882, "💎")}')

def _shop_trade_split(base):
    """(هزینه/قیمت پایه, ۳ درصد اضافه) با رُند به بالا."""
    extra = int(math.ceil(base * float(SHOP_TRADE_FEE) - 1e-9))
    return base, extra

def _shop_trade_status_text(typ, o):
    e = _hx
    dia = e(5940725397195853882, "💎")
    qty = int(o.get("qty", 0))
    if _shop_order_remaining(o) > 0:
        if typ == "import":
            title, tl = "شما در حال وارد کردن الماس انگشتر می‌باشید", "زمان باقیمانده تا رسیدن بار"
        else:
            title, tl = "شما در حال صادرات انگشتر می‌باشید", "زمان باقیمانده تا ارسال بار"
        return (f'{title}{e(5938109560249127910, "✅")}\n\n'
                f'{tl}: {_fmt_minutes(_shop_order_remaining(o))}{e(5938136227701067845, "⏳")}\n\n'
                f'تعداد: {qty:,}{dia}')
    if typ == "import":
        return (f'الماس های انگشتر شما وارد شد{e(5852787947559985777, "💍")}\n\n'
                f'تعداد: {qty:,}{e(5830338333892418460, "📦")}\n\n'
                f'برای تحویل روی دکمه زیر بزنید{e(5820903824046432799, "👇")}')
    return (f'انگشتر های شما صادر شد{e(5852787947559985777, "💍")}\n\n'
            f'تعداد: {qty:,}{e(5830338333892418460, "📦")}\n\n'
            f'سود شما از این بار: {int(o.get("payout", 0)):,}{dia}\n\n'
            f'برای دریافت سود روی دکمه زیر بزنید{e(5820903824046432799, "👇")}')

def _shop_import_text():
    e = _hx
    dia = e(5940725397195853882, "💎")
    return (f'واردات{e(5889002570633977838, "📥")}\n\n'
            f'میتونید الماس وارد کنید از کشور های خارجی{e(5830338333892418460, "🌍")}\n\n'
            f'با قیمت کمتر و به تعداد انبوه{e(5823592920250327036, "📦")}\n\n'
            f'حداقل تعداد قابل سفارش: {int(SHOP_IMPORT_MIN):,}{dia}\n\n'
            f'قیمت هر الماس وارداتی: {int(SHOP_IMPORT_PRICE):,}{dia}\n\n'
            f'جهت واردات الماس انگشتر از دکمه زیر استفاده کنید{e(5820903824046432799, "👇")}')

def _shop_export_text():
    e = _hx
    dia = e(5940725397195853882, "💎")
    return (f'صادرات{e(5889002570633977838, "📤")}\n\n'
            f'میتونید الماس صادر کنید به کشور های خارجی{e(5830338333892418460, "🌍")}\n\n'
            f'به تعداد انبوه{e(5823592920250327036, "📦")}\n\n'
            f'حداقل تعداد مورد نیاز برای صادرات: {int(SHOP_EXPORT_MIN):,}{dia}\n\n'
            f'قیمت فروش هر انگشتر: {int(SHOP_RING_SELL_PRICE):,}{dia}\n\n'
            f'جهت صادرات کردن انگشتر از دکمه زیر استفاده کنید{e(5820903824046432799, "👇")}')

def _shop_qty_prompt_text(kind):
    e = _hx
    dia = e(5940725397195853882, "💎")
    pct = _fmt_pct(SHOP_TRADE_FEE)
    if kind == "i":
        return (f'تعداد الماسی که میخواید سفارش بدید رو ارسال کنید{e(5859602531650380713, "✏️")}\n\n'
                f'وارد کردن الماس شامل {pct} درصد هزینه اضافی میشود{e(5818716826699307883, "ℹ️")}\n\n'
                f'{pct} درصد هزینه اضافی شامل کرایه راننده، باربری و... میشود{e(5830144944399981619, "🚚")}\n\n'
                f'قیمت هر الماس انگشتر: {int(SHOP_IMPORT_PRICE):,}{dia}')
    return (f'تعداد الماسی که میخواید صادر کنید رو ارسال کنید{e(5859602531650380713, "✏️")}\n\n'
            f'صادر کردن الماس شامل {pct} درصد هزینه اضافی میشود{e(5818716826699307883, "ℹ️")}\n\n'
            f'{pct} درصد هزینه اضافی شامل کرایه راننده، باربری و... میشود{e(5830144944399981619, "🚚")}\n\n'
            f'قیمت هر انگشتر: {int(SHOP_RING_SELL_PRICE):,}{dia}')

def _shop_import_confirm_text(qty):
    e = _hx
    dia = e(5940725397195853882, "💎")
    base, extra = _shop_trade_split(qty * int(SHOP_IMPORT_PRICE))
    return (f'هزینه کلی برای سفارش تعداد {qty:,} الماس انگشتر{e(5820903824046432799, "🧾")}\n\n'
            f'هزینه الماس های انگشتر: {base:,}{dia}\n\n'
            f'{_fmt_pct(SHOP_TRADE_FEE)} درصد هزینه اضافه: {extra:,}{dia}\n\n'
            f'هزینه کلی: {base + extra:,}{dia}')

def _shop_export_confirm_text(qty):
    e = _hx
    dia = e(5940725397195853882, "💎")
    base, extra = _shop_trade_split(qty * int(SHOP_RING_SELL_PRICE))
    return (f'سود کلی شما از صادرات تعداد {qty:,} انگشتر{e(5820903824046432799, "🧾")}\n\n'
            f'قیمت کلی انگشتر ها: {base:,}{dia}\n\n'
            f'{_fmt_pct(SHOP_TRADE_FEE)} درصد هزینه اضافه: {extra:,}{dia}\n\n'
            f'سود کلی: {base - extra:,}{dia}')

def _shop_order_text(kind, seconds, qty):
    e = _hx
    if kind == "i":
        title, tl = "سفارش الماس انگشتر ثبت شد", "زمان باقیمانده تا رسیدن بار"
    else:
        title, tl = "سفارش صادرات انگشتر ثبت شد", "زمان باقیمانده تا ارسال بار"
    return (f'{title}{e(5938109560249127910, "✅")}\n\n'
            f'{tl}: {_fmt_minutes(seconds)}{e(5938136227701067845, "⏳")}\n\n'
            f'تعداد: {qty:,}{e(5940725397195853882, "💎")}')

def _shop_show(chat_id, mid, uid, target):
    """target: panel | choose | bill | import | export | prod"""
    # تایمرهای برگشت پنل هم ممکن است بعد از انقضای اجاره اجرا شوند؛ پیش از
    # نمایش هر صفحه دوباره تاریخ اجاره را کنترل می‌کنیم تا پنل فعال برنگردد.
    try:
        _shop_sync_user(chat_id, uid)
    except Exception as ex:
        logging.exception("کنترل انقضای اجاره هنگام نمایش پنل %s در شهر %s ناموفق بود: %s", uid, chat_id, ex)
    sh = _city_get(chat_id)["shops_data"].get(str(uid))
    if sh is None and target != "choose":
        target = "choose"
    if sh is not None and target in ("import", "export"):
        typ = "import" if target == "import" else "export"
        o = _shop_find_order(sh, typ)
        if o:
            m = types.InlineKeyboardMarkup()
            if _shop_order_remaining(o) <= 0:
                label, cb = ("تحویل بار", f"shop|icol|{uid}") if typ == "import" else ("دریافت سود", f"shop|ecol|{uid}")
                m.row(_city_btn(label, cb, "5938109560249127910", "success"))
            m.row(back_button(f"shop|home|{uid}"))
            _shop_set_view(chat_id, uid, mid, "other")
            safe_edit_message(_shop_trade_status_text(typ, o), chat_id, mid, reply_markup=m, parse_mode="HTML")
            return
    _shop_set_view(chat_id, uid, mid, "panel" if target == "panel" else "other")
    if target == "panel":
        safe_edit_message(_shop_panel_text(chat_id, uid), chat_id, mid,
                          reply_markup=_shop_panel_markup(chat_id, uid), parse_mode="HTML")
    elif target == "bill":
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("تسویه بدهی", f"shop|bill|{uid}", "5888937012253171131"))
        m.row(back_button(f"shop|home|{uid}"))
        safe_edit_message(_shop_bill_text(sh), chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif target == "import":
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("سفارش الماس", f"shop|iord|{uid}", "5889002570633977838"))
        m.row(back_button(f"shop|home|{uid}"))
        safe_edit_message(_shop_import_text(), chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif target == "export":
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("صادرات انگشتر", f"shop|eord|{uid}", "5889002570633977838"))
        m.row(back_button(f"shop|home|{uid}"))
        safe_edit_message(_shop_export_text(), chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif target == "contract":
        m = types.InlineKeyboardMarkup()
        m.row(back_button(f"shop|home|{uid}"))
        safe_edit_message(_shop_contract_text(sh), chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif target == "prod":
        m = types.InlineKeyboardMarkup()
        m.row(back_button(f"shop|home|{uid}"))
        safe_edit_message(_shop_prod_text(sh, remaining=True), chat_id, mid, reply_markup=m, parse_mode="HTML")
    else:
        safe_edit_message(_shop_choose_text(), chat_id, mid,
                          reply_markup=_shop_choose_markup(uid), parse_mode="HTML")

def _shop_flash(chat_id, mid, uid, text, target):
    """پیام موقت، بعد ۵ ثانیه برگشت به صفحه‌ی مقصد."""
    key = (chat_id, mid)
    nonce = time.time()
    if len(_city_tokens) > 5000:
        _city_tokens.clear()
    _city_tokens[key] = nonce
    _shop_set_view(chat_id, uid, mid, "other")
    safe_edit_message(text, chat_id, mid, reply_markup=None, parse_mode="HTML")
    _start_daemon_timer(PANEL_REVERT_SECONDS, _shop_revert, key, nonce, uid, target)

def _shop_revert(key, nonce, uid, target):
    if _city_tokens.get(key) != nonce:
        return
    _city_tokens.pop(key, None)
    _shop_show(key[0], key[1], uid, target)

def _shop_rent_status(sh, now=None):
    """وضعیت اجاره را fail-closed بررسی می‌کند.

    خروجی: (is_rent, expires_at, expired). اجاره‌ای که تاریخ پایانش خالی یا
    خراب باشد نباید برای همیشه فعال بماند؛ چنین رکوردی منقضی محسوب می‌شود.
    """
    if not sh or sh.get("mode") != "rent":
        return False, None, False
    exp = _shop_parse(sh.get("rent_expires"))
    if exp is None:
        return True, None, True
    now = now or _shop_now()
    return True, exp, now >= exp


def _shop_sync_user(chat_id, uid):
    """اجاره‌ی منقضی یا دارای تاریخ خراب را فوراً بایگانی/متوقف می‌کند.
    True یعنی کاربر هنوز مغازه‌ی فعال دارد.
    """
    with _city_lock:
        d = _city_get(chat_id)
        uid_s = str(uid)
        sh = d["shops_data"].get(uid_s)
        if not sh:
            return False
        is_rent, exp, expired = _shop_rent_status(sh)
        if is_rent and expired:
            # اگر تاریخ معتبر باشد، وضعیت دقیقاً در لحظه‌ی پایان اجاره فریز می‌شود؛
            # اگر تاریخ مفقود/خراب باشد، از همین الان جلوی ادامه‌ی فعالیت گرفته می‌شود.
            freeze_at = exp or _shop_now()
            if exp is None:
                logging.error(
                    "اجاره‌ی مغازه‌ی %s در شهر %s تاریخ پایان معتبر ندارد؛ برای جلوگیری از فعالیت رایگان بایگانی شد",
                    uid, chat_id,
                )
            d["shops_archive"][uid_s] = _shop_freeze(sh, freeze_at)
            del d["shops_data"][uid_s]
            if not _city_save(chat_id, d):
                logging.error(f"ذخیره‌ی بایگانی مغازه‌ی {uid} تو شهر {chat_id} ناموفق بود (فقط تو حافظه)")
            return False
        return True

def _shop_freeze(sh, ref):
    """مغازه‌ی تموم‌شده‌ی اجاره رو یخ می‌کنه: زمان باقی‌مونده‌ی تولید و سفارش‌ها (نسبت به ref) ذخیره می‌شه."""
    out = dict(sh)
    pr = sh.get("prod")
    if pr:
        pr = dict(pr)
        nxt = _shop_parse(pr.pop("next_at", None))
        pr["next_in"] = max(0.0, (nxt - ref).total_seconds()) if nxt else 0.0
        out["prod"] = pr
    orders = []
    for o in (sh.get("orders") or []):
        o = dict(o)
        ra = _shop_parse(o.pop("ready_at", None))
        o["left"] = max(0.0, (ra - ref).total_seconds()) if ra else 0.0
        orders.append(o)
    out["orders"] = orders
    return out

def _shop_thaw(sh, now):
    """برعکس _shop_freeze: زمان‌های باقی‌مونده از لحظه‌ی now دوباره شروع می‌شن."""
    out = dict(sh)
    pr = sh.get("prod")
    if pr:
        pr = dict(pr)
        pr["next_at"] = (now + timedelta(seconds=float(pr.pop("next_in", 0) or 0))).isoformat()
        out["prod"] = pr
    orders = []
    for o in (sh.get("orders") or []):
        o = dict(o)
        o["ready_at"] = (now + timedelta(seconds=float(o.pop("left", 0) or 0))).isoformat()
        orders.append(o)
    out["orders"] = orders
    return out

def _shop_acquire(chat_id, from_user, mode):
    """خرید/اجاره‌ی مغازه. برمی‌گردونه (True, None) یا (False, 'price'|'funds'|'have'|'start'|'fail')."""
    uid = from_user.id
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        if str(uid) in d["shops_data"]:
            return False, "have"
        price = int(d["shop_price"] if mode == "buy" else d["rent_price"])
        if price <= 0:
            return False, "price"
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user:
                return False, "start"
            if int(user.get("diamonds", 0) or 0) < price:
                return False, "funds"
            if update_diamonds(uid, -price) is None:
                return False, "fail"
            now = _shop_now()
            new_shop = {
                "type": "jewelry", "mode": mode, "since": now.isoformat(),
                "owner_name": (getattr(from_user, "first_name", None) or "کاربر").strip()[:40],
                "rent_expires": (now + timedelta(seconds=SHOP_RENT_SECONDS)).isoformat() if mode == "rent" else None,
                "rent_notified": "",
                "rings": 0, "kw": 0.0, "debt": 0.0, "prod": None,
                "produced": 0, "sold": 0, "orders": [],
            }
            old = d["shops_archive"].pop(str(uid), None)
            if old:   # مغازه‌ی قبلی که اجاره‌ش تموم شده بود: همون اطلاعات (با زمان‌های باقی‌مونده) برمی‌گرده
                restored = _shop_thaw(old, now)
                for k in ("mode", "since", "owner_name", "rent_expires", "rent_notified"):
                    restored[k] = new_shop[k]
                new_shop = restored
            d["shops_data"][str(uid)] = new_shop
            d["diamonds"] += price
            if not _city_save(chat_id, d):
                update_diamonds(uid, price)   # ذخیره نشد؛ پول برگردونده می‌شه
                logging.error(f"ثبت مغازه‌ی {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, None

def _shop_settle(chat_id, from_user):
    """تسویه‌ی قبض برق. برمی‌گردونه (True, مبلغ) یا (False, 'nodebt'|'funds'|'noshop'|'fail')."""
    uid = from_user.id
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        if not sh:
            return False, "noshop"
        amount = _shop_debt(sh)
        if amount <= 0:
            return False, "nodebt"
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user or int(user.get("diamonds", 0) or 0) < amount:
                return False, "funds"
            if update_diamonds(uid, -amount) is None:
                return False, "fail"
            sh["debt"] = 0.0
            sh["kw"] = 0.0
            d["diamonds"] += amount
            if not _city_save(chat_id, d):
                update_diamonds(uid, amount)
                logging.error(f"ذخیره‌ی تسویه‌ی قبض {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, amount

def _shop_renew(chat_id, from_user):
    """تمدید اجاره برای ۲۴ ساعت بعدی. برمی‌گردونه (True, None) یا (False, 'lost'|'debt'|'funds'|'fail')."""
    uid = from_user.id
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        exp = _shop_parse(sh.get("rent_expires")) if sh and sh.get("mode") == "rent" else None
        if not sh or not exp or _shop_now() >= exp:
            return False, "lost"
        if _shop_debt(sh) > 0:
            return False, "debt"
        price = int(d["rent_price"])
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user or int(user.get("diamonds", 0) or 0) < price:
                return False, "funds"
            if price > 0 and update_diamonds(uid, -price) is None:
                return False, "fail"
            sh["rent_expires"] = (exp + timedelta(seconds=SHOP_RENT_SECONDS)).isoformat()
            sh["rent_notified"] = ""
            d["diamonds"] += price
            if not _city_save(chat_id, d):
                if price > 0:
                    update_diamonds(uid, price)
                logging.error(f"ذخیره‌ی تمدید اجاره {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, None

def _shop_start_prod(chat_id, uid):
    """برمی‌گردونه (True, تعداد) یا (False, 'noshop'|'busy'|'debt'|'empty'|'fail')."""
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        if not sh:
            return False, "noshop"
        if sh.get("prod"):
            return False, "busy"
        if _shop_debt(sh) > 0:
            return False, "debt"
        n = int(sh.get("rings", 0) or 0)
        if n <= 0:
            return False, "empty"
        sh["rings"] = 0
        sh["prod"] = {"remaining": n, "total": n,
                      "next_at": (_shop_now() + timedelta(seconds=int(SHOP_RING_SECONDS))).isoformat()}
        if not _city_save(chat_id, d):
            return False, "fail"
    return True, n

def _shop_find_order(sh, typ):
    for o in (sh.get("orders") or []):
        if o.get("type") == typ:
            return o
    return None

def _shop_order_remaining(o):
    ra = _shop_parse(o.get("ready_at")) if o else None
    return max(0.0, (ra - _shop_now()).total_seconds()) if ra else 0.0

def _shop_import_pay(chat_id, from_user, qty):
    """پرداخت و ثبت سفارش واردات. برمی‌گردونه (True, ثانیه) یا (False, 'min'|'funds'|'noshop'|'fail')."""
    uid = from_user.id
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        if not sh:
            return False, "noshop"
        if _shop_find_order(sh, "import"):
            return False, "busy"
        if qty < int(SHOP_IMPORT_MIN):
            return False, "min"
        base, extra = _shop_trade_split(qty * int(SHOP_IMPORT_PRICE))
        total = base + extra
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user or int(user.get("diamonds", 0) or 0) < total:
                return False, "funds"
            if update_diamonds(uid, -total) is None:
                return False, "fail"
            secs = int(SHOP_IMPORT_SECONDS)
            sh["orders"] = list(sh.get("orders") or []) + [{
                "type": "import", "qty": int(qty), "secs": secs,
                "ready_at": (_shop_now() + timedelta(seconds=secs)).isoformat()}]
            d["diamonds"] += total
            if not _city_save(chat_id, d):
                update_diamonds(uid, total)
                logging.error(f"ثبت سفارش واردات {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, secs

def _shop_export_confirm(chat_id, uid, qty):
    """ثبت سفارش صادرات: انگشترها از موجودی کم می‌شن و سود بعد از زمان ارسال واریز می‌شه."""
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        if not sh:
            return False, "noshop"
        if _shop_find_order(sh, "export"):
            return False, "busy"
        if qty < int(SHOP_EXPORT_MIN):
            return False, "min"
        if int(sh.get("produced", 0) or 0) < qty:
            return False, "stock"
        base, extra = _shop_trade_split(qty * int(SHOP_RING_SELL_PRICE))
        secs = int(SHOP_EXPORT_SECONDS)
        sh["produced"] = int(sh.get("produced", 0)) - qty
        d["diamonds"] += extra
        sh["orders"] = list(sh.get("orders") or []) + [{
            "type": "export", "qty": int(qty), "payout": int(base - extra), "secs": secs,
            "ready_at": (_shop_now() + timedelta(seconds=secs)).isoformat()}]
        if not _city_save(chat_id, d):
            return False, "fail"
    return True, secs

def _shop_collect(chat_id, from_user, typ):
    """تحویل بار واردات یا دریافت سود صادرات (فقط وقتی زمانش رسیده). برمی‌گردونه (True, عدد) یا (False, 'none'|'notready'|'fail')."""
    uid = from_user.id
    with _city_lock:
        _shop_sync_user(chat_id, uid)
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        o = _shop_find_order(sh, typ) if sh else None
        if not o:
            return False, "none"
        if _shop_order_remaining(o) > 0:
            return False, "notready"
        qty, payout = int(o.get("qty", 0)), int(o.get("payout", 0))
        left = [x for x in (sh.get("orders") or []) if x is not o]
        if typ == "import":
            sh["rings"] = int(sh.get("rings", 0) or 0) + qty
            sh["orders"] = left
            if not _city_save(chat_id, d):
                logging.error(f"ذخیره‌ی تحویل بار واردات {uid} تو شهر {chat_id} ناموفق بود (فقط تو حافظه)")
            return True, qty
        # صادرات: اول واریز، بعد حذف سفارش
        if payout > 0 and update_diamonds(uid, payout) is None:
            return False, "fail"
        sh["sold"] = int(sh.get("sold", 0) or 0) + qty
        sh["orders"] = left
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی دریافت سود صادرات {uid} تو شهر {chat_id} ناموفق بود (فقط تو حافظه)")
        return True, payout

def _shop_qty_step(message, kind, panel_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    qty = parse_amount(message.text.strip() if message.text else "")
    _shop_sync_user(chat_id, uid)
    sh = _city_get(chat_id)["shops_data"].get(str(uid))
    if not sh:
        return

    def again(err):
        msg = bot.reply_to(message, _casino_err(err), parse_mode="HTML")
        register_timed_next_step_handler(msg, _shop_qty_step, kind, panel_mid, expected_user_id=uid)

    if qty is None:
        return again("تعداد نامعتبره. یه عدد مثبت بفرست")
    if kind == "i":
        if qty < int(SHOP_IMPORT_MIN):
            return again(f"حداقل تعداد سفارش {int(SHOP_IMPORT_MIN):,} الماس انگشتر هست")
        text = _shop_import_confirm_text(qty)
        pay_cb, cancel_cb, pay_label = f"shop|ipay|{qty}|{uid}", f"shop|im|{uid}", "پرداخت"
    else:
        if qty < int(SHOP_EXPORT_MIN):
            return again(f"حداقل تعداد صادرات {int(SHOP_EXPORT_MIN):,} انگشتر هست")
        if qty > int(sh.get("produced", 0) or 0):
            return again(f"انگشتر کافی برای فروش نداری. موجودی: {int(sh.get('produced', 0) or 0):,}")
        text = _shop_export_confirm_text(qty)
        pay_cb, cancel_cb, pay_label = f"shop|epay|{qty}|{uid}", f"shop|ex|{uid}", "تایید"
    m = types.InlineKeyboardMarkup()
    m.row(_city_btn(pay_label, pay_cb, "5938109560249127910", "success"),
          _city_btn("لغو", cancel_cb, "5938290000415167172", "danger"))
    _shop_set_view(chat_id, uid, panel_mid, "other")
    safe_edit_message(text, chat_id, panel_mid, reply_markup=m, parse_mode="HTML")

def _shop_add_qty_step(message, panel_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    qty = parse_amount(message.text.strip() if message.text else "")

    def again(err):
        reply = bot.reply_to(message, _casino_err(err), parse_mode="HTML")
        register_timed_next_step_handler(reply, _shop_add_qty_step, panel_mid, expected_user_id=uid)

    if qty is None or qty <= 0:
        return again("تعداد نامعتبره. یه عدد مثبت بفرست")

    with _get_user_financial_lock(uid):
        user = get_user(uid)
        if not user:
            _shop_flash(chat_id, panel_mid, uid, f'اول باید بات رو استارت کنی {_hx(5819154526816444042, "❌")}', "panel")
            return
        have = int(user.get("ring_diamonds", 0) or 0)
        if have < qty:
            return again(f"الماس انگشتر کافی نداری. موجودی: {have:,}")
        with _city_lock:
            _shop_sync_user(chat_id, uid)
            data = _city_get(chat_id)
            shop = data["shops_data"].get(str(uid))
            if not shop:
                _shop_flash(chat_id, panel_mid, uid, f'مغازه جواهری شما پیدا نشد {_hx(5819154526816444042, "❌")}', "choose")
                return
            try:
                update_result = (supabase.table("users").update({"ring_diamonds": have - qty})
                                .eq("user_id", uid).execute())
            except Exception as exc:
                logging.error("خطا در کسر الماس انگشتر از کاربر %s: %s", uid, exc)
                return again("عملیات انجام نشد، دوباره امتحان کن")

            old_rings = int(shop.get("rings", 0) or 0)
            shop["rings"] = old_rings + qty
            if not _city_save(chat_id, data):
                # ذخیرهٔ شهر ناموفق بود؛ موجودی‌ها را تا جای ممکن به حالت قبل برگردان.
                shop["rings"] = old_rings
                _city_save(chat_id, data)
                try:
                    supabase.table("users").update({"ring_diamonds": have}).eq("user_id", uid).execute()
                except Exception as rollback_exc:
                    logging.error("برگرداندن الماس انگشتر کاربر %s ناموفق بود: %s", uid, rollback_exc)
                return _shop_flash(chat_id, panel_mid, uid,
                                   f'ذخیره انبار انجام نشد؛ دوباره امتحان کن {_hx(5819154526816444042, "❌")}', "panel")

    _shop_flash(chat_id, panel_mid, uid,
                f'تعداد {qty:,} الماس انگشتر به انبار اضافه شد{_hx(5938109560249127910, "✅")}', "panel")


# ---------- پردازش دوره‌ای: تولید، هشدار اجاره، از دست رفتن مغازه ----------
def _shop_refresh_panel(chat_id, uid):
    v = _shop_views.get((chat_id, uid))
    if not v or v.get("view") != "panel":
        return
    try:
        safe_edit_message(_shop_panel_text(chat_id, uid), chat_id, v["mid"],
                          reply_markup=_shop_panel_markup(chat_id, uid), parse_mode="HTML")
    except Exception as ex:
        logging.error(f"رفرش پنل مغازه {uid}: {ex}")

def _shop_rent_form(chat_id, uid, sh, d):
    """متن و دکمه‌ی پیام هشدار/تمدید اجاره (تو پیوی)."""
    e = _hx
    dia = e(6012438204144165493, "🏠")
    debt = _shop_debt(sh)
    text = (f'{e(5938388342281343001, "⏰")}مهلت اجاره مغازه شما رو به اتمام هست{e(5938167138580741203, "⚠️")}\n\n'
            f'{e(5819051035284479206, "❗")}در صورت تمدید نکردن قرارداد مغازه خودتون رو از دست خواهی داد{e(5819154526816444042, "❌")}\n\n'
            f'هزینه اجاره: {d["rent_price"]:,}{dia}\n\n')
    if debt > 0:
        text += f'قبض برق پرداخت نشده: {debt:,}{e(5940725397195853882, "💎")}\n\n'
    text += f'{e(5888937012253171131, "🧾")}جهت تمدید قرار داد روی دکمه زیر کلیک کنید{e(5820903824046432799, "👇")}'
    m = types.InlineKeyboardMarkup()
    if debt > 0:
        m.row(_city_btn("تسویه بدهی", f"shopren|settle|{chat_id}|{uid}", "5888937012253171131"))
    else:
        m.row(_city_btn("تمدید قرارداد", f"shopren|renew|{chat_id}|{uid}", "5888937012253171131"))
    return text, m

_shop_warn_tries = {}   # (chat_id, uid, rent_expires) -> تعداد تلاش ناموفق برای ارسال پیام تمدید

def _shop_send_warning(chat_id, uid):
    """پیام تمدید رو تو پیوی می‌فرسته. True یعنی ارسال شد (یا دیگه لازم نیست)، False یعنی نشد و باید دوباره امتحان بشه."""
    d = _city_get(chat_id)
    sh = d["shops_data"].get(str(uid))
    if not sh:
        return True
    tries_key = (chat_id, uid, sh.get("rent_expires"))
    try:
        text, m = _shop_rent_form(chat_id, uid, sh, d)
        bot.send_message(uid, text, reply_markup=m, parse_mode="HTML")
        _shop_warn_tries.pop(tries_key, None)
        return True
    except Exception as ex:
        logging.exception(f"ارسال پیام تمدید اجاره به {uid} (شهر {chat_id}) ناموفق بود: {ex}")
        err = str(ex).lower()
        permanent = any(w in err for w in ("forbidden", "blocked", "can't initiate", "chat not found", "deactivated"))
        n = _shop_warn_tries.get(tries_key, 0) + 1
        if len(_shop_warn_tries) > 5000:
            _shop_warn_tries.clear()
        _shop_warn_tries[tries_key] = n
        if permanent or n >= 5:
            # پیوی بسته‌ست یا بارها شکست خورده: تو همون گروه به خود کاربر خبر بده
            _shop_warn_tries.pop(tries_key, None)
            try:
                name = html.escape(str(sh.get("owner_name") or "کاربر"))
                safe_send_message(
                    chat_id,
                    f'<a href="tg://user?id={int(uid)}">{name}</a> مهلت اجاره مغازه جواهری شما رو به اتمام هست. '
                    f'برای دریافت پیام تمدید، ربات رو تو پیوی استارت کنید و از بخش جواهری > قرارداد وضعیتش رو ببینید.',
                    parse_mode="HTML")
            except Exception:
                pass
            return True
        return False

_shop_run_count = 0

def process_shops(only_chat=None):
    global _shop_run_count
    try:
        _shop_run_count += 1
        if only_chat is None and _shop_run_count % 90 == 1:
            logging.info("process_shops در حال اجراست (اجرای شماره %s)", _shop_run_count)
        now = _shop_now()
        T = max(60, int(SHOP_RING_SECONDS))
        notify = []   # (kind, chat_id, uid)
        payouts = []  # (uid, amount) سود صادرات
        for key in list(_SETTINGS.keys()):
            if not key.startswith("city_"):
                continue
            try:
                chat_id = int(key[5:])
            except ValueError:
                continue
            if only_chat is not None and chat_id != int(only_chat):
                continue
            if not (_SETTINGS.get(key) or {}).get("shops_data"):
                continue
            try:
              with _city_lock:
                d = _city_get(chat_id)
                changed = False
                for uid_s, sh in list(d["shops_data"].items()):
                    uid = int(uid_s)
                    # --- اجاره: تاریخ پایان خالی/خراب هم منقضی است؛ هرگز اجازه نده
                    # یک مغازه‌ی اجاره‌ای به‌خاطر rent_expires نامعتبر رایگان فعال بماند.
                    is_rent, exp, expired = _shop_rent_status(sh, now)
                    if is_rent and expired:
                        freeze_at = exp or now
                        if exp is None:
                            logging.error(
                                "اجاره‌ی مغازه‌ی %s در شهر %s تاریخ پایان معتبر ندارد؛ برای جلوگیری از فعالیت رایگان بایگانی شد",
                                uid, chat_id,
                            )
                        # اجاره تمام شد: تولید، سفارش‌ها و موجودی در لحظه‌ی انقضا فریز می‌شوند.
                        d["shops_archive"][uid_s] = _shop_freeze(sh, freeze_at)
                        del d["shops_data"][uid_s]
                        changed = True
                        notify.append(("lost", chat_id, uid))
                        continue
                    if is_rent and exp is not None:
                        if (exp - now).total_seconds() <= SHOP_RENT_WARN_SECONDS and sh.get("rent_notified") != sh.get("rent_expires"):
                            # علامت «اطلاع داده شد» فقط بعد از ارسال موفق زده می‌شه
                            notify.append(("warn", chat_id, uid))
                    # --- تولید
                    pr = sh.get("prod")
                    nxt = _shop_parse(pr.get("next_at")) if pr else None
                    if pr and nxt and now >= nxt:
                        pr = dict(pr)
                        k = min(int(pr.get("remaining", 0)), int((now - nxt).total_seconds() // T) + 1)
                        if k > 0:
                            kw = k * (T / 3600.0) * float(SHOP_KW_PER_HOUR)
                            sh["produced"] = int(sh.get("produced", 0) or 0) + k
                            sh["kw"] = float(sh.get("kw", 0) or 0) + kw
                            sh["debt"] = float(sh.get("debt", 0) or 0) + kw * d["kw_price"]
                            pr["remaining"] = int(pr["remaining"]) - k
                            if pr["remaining"] <= 0:
                                sh["prod"] = None
                            else:
                                pr["next_at"] = (nxt + timedelta(seconds=k * T)).isoformat()
                                sh["prod"] = pr
                            changed = True
                            notify.append(("tick", chat_id, uid))
                if changed and not _city_save(chat_id, d):
                    logging.error(f"ذخیره‌ی پردازش مغازه‌های شهر {chat_id} ناموفق بود (فقط تو حافظه)")
            except Exception as ex:   # خرابی اطلاعات یه شهر نباید پردازش بقیه رو متوقف کنه
                logging.exception(f"خطا در پردازش مغازه‌های شهر {chat_id}: {ex}")
        for uid, amount in payouts:
            if amount > 0 and update_diamonds(uid, amount) is None:
                logging.error(f"واریز سود صادرات {amount} به {uid} ناموفق بود")
        for kind, chat_id, uid in notify:
            if kind == "warn":
                try:
                    done = _shop_send_warning(chat_id, uid)
                except Exception as ex:
                    logging.exception(f"خطا در پیام تمدید اجاره {uid}: {ex}")
                    done = False
                if done:
                    with _city_lock:
                        d2 = _city_get(chat_id)
                        sh2 = d2["shops_data"].get(str(uid))
                        if sh2 and sh2.get("rent_notified") != sh2.get("rent_expires"):
                            sh2["rent_notified"] = sh2.get("rent_expires")
                            _city_save(chat_id, d2)
            elif kind == "tick":
                _shop_refresh_panel(chat_id, uid)
            elif kind == "lost":
                v = _shop_views.pop((chat_id, uid), None)
                if v:
                    try:
                        safe_edit_message(_shop_choose_text(), chat_id, v["mid"],
                                          reply_markup=_shop_choose_markup(uid), parse_mode="HTML")
                    except Exception:
                        pass
    except Exception as ex:
        logging.exception(f"خطا در پردازش مغازه‌ها: {ex}")

try:
    shop_scheduler = BackgroundScheduler(timezone=TEHRAN_TZ)
    shop_scheduler.add_job(process_shops, "interval", seconds=20, id="shop_process",
                           replace_existing=True, max_instances=1, coalesce=True)
    try:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES
        shop_scheduler.add_listener(
            lambda ev: logging.error("زمان‌بند مغازه‌ها: مشکل در اجرای جاب (%s): %s", getattr(ev, "code", "?"), getattr(ev, "exception", "")),
            EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
    except Exception as _lex:
        logging.error(f"افزودن listener زمان‌بند مغازه‌ها ناموفق بود: {_lex}")
    shop_scheduler.start()
except Exception as _ex:
    shop_scheduler = None
    logging.error(f"راه‌اندازی زمان‌بند مغازه‌ها ناموفق بود: {_ex}")

# ---------- هندلرها ----------
@bot.message_handler(func=lambda m: text_is(m, "جواهری"))
def text_shop(message):
    if _feature_gate(message, "jewelry"):
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, _casino_err("مغازه فقط تو گروه کار می‌کنه"), parse_mode="HTML")
        return
    uid = message.from_user.id
    if not get_user(uid):
        bot.reply_to(message, _start_first_err(), parse_mode="HTML")
        return
    cid = message.chat.id
    try:
        process_shops(cid)
    except Exception as ex:
        logging.exception(f"همگام‌سازی مغازه‌ها ناموفق بود: {ex}")
    has = str(uid) in _city_get(cid)["shops_data"]
    if has:
        text, markup = _shop_panel_text(cid, uid), _shop_panel_markup(cid, uid)
    else:
        text, markup = _shop_choose_text(), _shop_choose_markup(uid)
    sent = safe_send_message(cid, text, reply_markup=markup, parse_mode="HTML",
                             reply_to_message_id=message.message_id)
    if has and sent is not None:
        _shop_set_view(cid, uid, sent.message_id, "panel")

def _shop_price_step(message, panel_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    amount = parse_amount(message.text.strip() if message.text else "")
    if amount is None:
        msg = bot.reply_to(message, _casino_err("قیمت نامعتبره. یه عدد مثبت بفرست"), parse_mode="HTML")
        register_timed_next_step_handler(msg, _shop_price_step, panel_mid, expected_user_id=uid)
        return
    with _city_lock:
        d = _city_get(chat_id)
        sh = d["shops_data"].get(str(uid))
        if not sh:
            return
        sh["ring_price"] = amount
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی قیمت انگشتر مغازه {uid} تو شهر {chat_id} ناموفق بود (فقط تو حافظه)")
    _shop_show(chat_id, panel_mid, uid, "price")

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop|"))
def shop_callback(call):
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    parts = call.data.split("|")
    act = parts[1]
    chat_id, mid = call.message.chat.id, call.message.message_id
    e = _hx
    cancel_pending_step(chat_id, uid)
    _city_tokens.pop((chat_id, mid), None)
    try:
        process_shops(chat_id)   # اجاره‌ی تموم‌شده/تولید عقب‌افتاده همین الان بررسی بشه، نه فقط با زمان‌بند
    except Exception as ex:
        logging.exception(f"همگام‌سازی مغازه‌ها هنگام کلیک ناموفق بود: {ex}")
    has_shop = str(uid) in _city_get(chat_id)["shops_data"]

    if act in ("buy", "rent", "ok", "no"):
        if has_shop:
            _shop_show(chat_id, mid, uid, "panel")
            return
    elif not has_shop:
        _shop_show(chat_id, mid, uid, "choose")   # مغازه نیست (مثلاً اجاره تموم شده)
        return

    if act == "home":
        _shop_show(chat_id, mid, uid, "panel")

    elif act in ("buy", "rent"):
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("بله", f"shop|ok|{act}|{uid}", "5830144944399981619", "success"),
              _city_btn("خیر", f"shop|no|{act}|{uid}", "5819154526816444042", "danger"))
        _shop_set_view(chat_id, uid, mid, "other")
        safe_edit_message(_shop_confirm_text(chat_id, act), chat_id, mid, reply_markup=m, parse_mode="HTML")

    elif act == "no" and len(parts) >= 4 and parts[2] in ("buy", "rent"):
        word = "خرید" if parts[2] == "buy" else "اجاره"
        _shop_flash(chat_id, mid, uid, f'{word} مغازه لغو شد{e(5819154526816444042, "❌")}', "choose")

    elif act == "ok" and len(parts) >= 4 and parts[2] in ("buy", "rent"):
        mode = parts[2]
        key = (chat_id, mid)
        with _city_lock:
            if key in _city_busy:
                return
            _city_busy.add(key)
        try:
            ok, err = _shop_acquire(chat_id, call.from_user, mode)
        finally:
            with _city_lock:
                _city_busy.discard(key)
        if ok:
            word = "خریده" if mode == "buy" else "اجاره"
            _shop_flash(chat_id, mid, uid, f'مغازه جواهری {word} شد{e(5832433801191498078, "🎉")}', "panel")
        elif err == "have":
            _shop_show(chat_id, mid, uid, "panel")
        else:
            msg = {"funds": "موجودی الماس کافی نیست",
                   "price": ("قیمت مغازه هنوز توسط شهردار تنظیم نشده" if mode == "buy" else "قیمت اجاره هنوز توسط شهردار تنظیم نشده"),
                   "start": "اول باید بات رو استارت کنی"}.get(err, "عملیات انجام نشد، دوباره امتحان کن")
            _shop_flash(chat_id, mid, uid, f'{html.escape(msg)} {e(5819154526816444042, "❌")}', "choose")

    elif act == "do" and len(parts) >= 4:
        sub = parts[2]
        if sub == "start":
            ok, res = _shop_start_prod(chat_id, uid)
            if ok:
                sh = _city_get(chat_id)["shops_data"].get(str(uid)) or {}
                _shop_flash(chat_id, mid, uid, _shop_prod_text(sh), "panel")
            elif res == "busy":
                _shop_show(chat_id, mid, uid, "prod")
            else:
                msg = {"debt": "اول قبض برق رو تسویه کن", "empty": "الماس انگشتری برای تولید نداری"}.get(res, "عملیات انجام نشد، دوباره امتحان کن")
                _shop_flash(chat_id, mid, uid, f'{msg} {e(5819154526816444042, "❌")}', "panel")
        elif sub == "prod":
            sh = _city_get(chat_id)["shops_data"].get(str(uid)) or {}
            _shop_show(chat_id, mid, uid, "prod" if sh.get("prod") else "panel")
        elif sub == "bill":
            _shop_show(chat_id, mid, uid, "bill")
        elif sub == "add":
            e = _hx
            prompt = (
                f'{e(5940725397195853882, "💎")}مقدار الماس انگشتری که میخواید به انبار مغازه اضافه بشه رو ارسال کنید{e(5940582340425159463, "➕")}\n\n'
                f'الماس های انگشتر از موجودی شما کم میشود{e(5938290532991111041, "💍")}'
            )
            m = types.InlineKeyboardMarkup()
            m.row(back_button(f"shop|home|{uid}"))
            _shop_set_view(chat_id, uid, mid, "other")
            safe_edit_message(prompt, chat_id, mid, reply_markup=m, parse_mode="HTML")
            register_timed_next_step_handler(call.message, _shop_add_qty_step, mid, expected_user_id=uid)

    elif act == "bill":
        ok, res = _shop_settle(chat_id, call.from_user)
        if ok:
            _shop_flash(chat_id, mid, uid, f'قبض برق تسویه شد {e(5938109560249127910, "✅")}', "panel")
        else:
            msg = {"nodebt": "قبض برقی برای تسویه نداری", "funds": "موجودی الماس کافی نیست"}.get(res, "عملیات انجام نشد، دوباره امتحان کن")
            _shop_flash(chat_id, mid, uid, f'{msg} {e(5819154526816444042, "❌")}', "bill")

    elif act in ("im", "ex"):
        _shop_show(chat_id, mid, uid, "import" if act == "im" else "export")

    elif act == "contract":
        sh = _city_get(chat_id)["shops_data"].get(str(uid)) or {}
        _shop_show(chat_id, mid, uid, "contract" if sh.get("mode") == "rent" else "panel")

    elif act in ("iord", "eord") and _shop_find_order(_city_get(chat_id)["shops_data"].get(str(uid)) or {}, "import" if act == "iord" else "export"):
        _shop_show(chat_id, mid, uid, "import" if act == "iord" else "export")

    elif act in ("icol", "ecol"):
        typ = "import" if act == "icol" else "export"
        key = (chat_id, mid)
        with _city_lock:
            if key in _city_busy:
                return
            _city_busy.add(key)
        try:
            ok, res = _shop_collect(chat_id, call.from_user, typ)
        finally:
            with _city_lock:
                _city_busy.discard(key)
        if ok and typ == "import":
            _shop_flash(chat_id, mid, uid, f'بار تحویل گرفته شد و به انبار فرستاده شد{e(5830338333892418460, "📦")}', "panel")
        elif ok:
            _shop_flash(chat_id, mid, uid,
                        f'مبلغ {res:,} الماس{e(5940725397195853882, "💎")} به موجودی شما اضافه شد{e(5940582340425159463, "✅")}', "panel")
        else:
            _shop_show(chat_id, mid, uid, "import" if typ == "import" else "export")

    elif act in ("iord", "eord"):
        kind = "i" if act == "iord" else "e"
        m = types.InlineKeyboardMarkup()
        m.row(back_button(f"shop|{'im' if kind == 'i' else 'ex'}|{uid}"))
        _shop_set_view(chat_id, uid, mid, "other")
        safe_edit_message(_shop_qty_prompt_text(kind), chat_id, mid, reply_markup=m, parse_mode="HTML")
        register_timed_next_step_handler(call.message, _shop_qty_step, kind, mid, expected_user_id=uid)

    elif act in ("ipay", "epay") and len(parts) >= 4:
        try:
            qty = int(parts[2])
        except ValueError:
            return
        if qty <= 0:
            return
        key = (chat_id, mid)
        with _city_lock:
            if key in _city_busy:
                return
            _city_busy.add(key)
        try:
            if act == "ipay":
                ok, res = _shop_import_pay(chat_id, call.from_user, qty)
            else:
                ok, res = _shop_export_confirm(chat_id, uid, qty)
        finally:
            with _city_lock:
                _city_busy.discard(key)
        kind = "i" if act == "ipay" else "e"
        if ok:
            _shop_flash(chat_id, mid, uid, _shop_order_text(kind, res, qty), "panel")
        elif res == "busy":
            _shop_show(chat_id, mid, uid, "import" if kind == "i" else "export")
        else:
            msg = {"funds": "موجودی الماس کافی نیست", "stock": "انگشتر کافی برای فروش نداری",
                   "min": "تعداد کمتر از حداقل مجاز هست"}.get(res, "عملیات انجام نشد، دوباره امتحان کن")
            _shop_flash(chat_id, mid, uid, f'{msg} {e(5819154526816444042, "❌")}', "import" if kind == "i" else "export")

@bot.callback_query_handler(func=lambda call: call.data.startswith("shopren|"))
def shop_rent_callback(call):
    """دکمه‌های پیام هشدار اجاره (تو پیوی). فرمت: shopren|settle/renew|<chat_id>|<uid>"""
    uid = check_panel_owner(call)
    if uid is None:
        return
    parts = call.data.split("|")
    try:
        act, cid = parts[1], int(parts[2])
    except (IndexError, ValueError):
        return
    pm_chat, pm_mid = call.message.chat.id, call.message.message_id
    e = _hx

    def alert(text):
        try:
            bot.answer_callback_query(call.id, text, show_alert=True)
        except Exception:
            pass

    def show_form():
        d = _city_get(cid)
        sh = d["shops_data"].get(str(uid))
        if not sh:
            safe_edit_message(f'مغازه شما از دست رفته {e(5819154526816444042, "❌")}', pm_chat, pm_mid,
                              reply_markup=None, parse_mode="HTML")
            return
        text, m = _shop_rent_form(cid, uid, sh, d)
        safe_edit_message(text, pm_chat, pm_mid, reply_markup=m, parse_mode="HTML")

    key = (pm_chat, pm_mid)
    with _city_lock:
        if key in _city_busy:
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            return
        _city_busy.add(key)
    try:
        if act == "settle":
            ok, res = _shop_settle(cid, call.from_user)
            if not ok and res == "funds":
                alert("موجودی الماس کافی نیست")
                return
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            show_form()
        elif act == "renew":
            ok, res = _shop_renew(cid, call.from_user)
            if ok:
                try:
                    bot.answer_callback_query(call.id)
                except Exception:
                    pass
                safe_edit_message(f'قرارداد مغازه شما تمدید شد{e(5832433801191498078, "🎉")}', pm_chat, pm_mid,
                                  reply_markup=None, parse_mode="HTML")
            elif res == "funds":
                alert("موجودی الماس کافی نیست")
            else:
                try:
                    bot.answer_callback_query(call.id)
                except Exception:
                    pass
                show_form()   # بدهی برق دارد یا مغازه از دست رفته
    finally:
        with _city_lock:
            _city_busy.discard(key)

# ================== حفاری ==================
# کلمه‌ی «حفاری» -> اگه تو این شهر (گروه) مدیر استخدام کرده باشی پنل معدن حفاری، وگرنه پنل استخدام مدیر.
# اطلاعات هر معدن تو داده‌ی شهر همون گپ (drills_data) ذخیره می‌شه.
# تولید «تنبل» حساب می‌شه: هر بار که پنل باز می‌شه یا دکمه‌ای زده می‌شه، از آخرین زمان محاسبه تا الان حساب می‌شه؛
# پس تایمر/جاب جدا نداره و بات رو سنگین نمی‌کنه. وقتی انبار پر شد، تولید و مصرف برق متوقف می‌شه.
# فقط پول قبض برق می‌ره تو خزانه‌ی شهر؛ هزینه‌ی استخدام مدیر و ارتقاء جایی نمی‌ره (فقط از کاربر کم می‌شه).

def _drill_pick(lst, level, default=0):
    try:
        i = max(1, min(int(level), int(DRILL_MAX_LEVEL))) - 1
        return lst[min(i, len(lst) - 1)]
    except Exception:
        return default

def _drill_vals(level):
    """(سرعت در ثانیه، ظرفیت انبار، مصرف برق KW در ساعت) برای یه لول."""
    return (float(_drill_pick(DRILL_SPEED, level)),
            int(_drill_pick(DRILL_CAPACITY, level)),
            float(_drill_pick(DRILL_KW, level)))

def _drill_upgrade_cost(level):
    """هزینه‌ی رسیدن از این لول به لول بعدی؛ تو لول آخر None."""
    level = int(level)
    if level >= int(DRILL_MAX_LEVEL):
        return None
    try:
        return int(DRILL_UPGRADE[min(level - 1, len(DRILL_UPGRADE) - 1)])
    except Exception:
        return None

def _drill_debt(dr):
    return int(math.ceil(float(dr.get("debt", 0) or 0) - 1e-9))

def _drill_blocked(dr):
    """تا قبض برق پرداخت‌نشده هست، برداشت و ارتقاء بسته‌ست. چون قبض فقط هر دوره (پیش‌فرض یک ساعت) یک بار اضافه می‌شه،
    بعد از تسویه تا دوره‌ی بعد قبض جدیدی جلوت رو نمی‌گیره."""
    return _drill_debt(dr) > 0

def _drill_accrue(dr, kw_price, now=None):
    """تولید و برق مصرفی از آخرین محاسبه تا الان رو روی خود dr اعمال می‌کنه (ذخیره نمی‌کنه)."""
    now = now or _shop_now()
    last = _shop_parse(dr.get("last_at")) or now
    elapsed = max(0.0, (now - last).total_seconds())
    dr["last_at"] = now.isoformat()
    speed, cap, kwh = _drill_vals(dr.get("level", 1))
    stock = float(dr.get("stock", 0) or 0)
    if elapsed <= 0 or speed <= 0 or cap <= 0 or stock >= cap - 1e-9:
        return
    run = min(elapsed, (cap - stock) / speed)      # ثانیه‌هایی که واقعاً کار کرده (تا پر شدن انبار)
    gain = speed * run
    dr["stock"] = min(float(cap), stock + gain)
    # قبض پله‌ایه: فقط با تموم شدن هر دوره‌ی کارکرد (پیش‌فرض ۱ ساعت) مصرف همون دوره به بدهی اضافه می‌شه
    # و کارکرد ناقصِ دوره‌ی جاری تا دوره‌ی بعد نگه داشته می‌شه (bill_acc).
    period = max(1.0, float(DRILL_BILL_SECONDS))
    acc = float(dr.get("bill_acc", 0) or 0) + run
    n = int(acc // period)
    dr["bill_acc"] = acc - n * period
    if n > 0:
        used = kwh * (period / 3600.0) * n
        dr["kw"] = float(dr.get("kw", 0) or 0) + used
        dr["debt"] = float(dr.get("debt", 0) or 0) + used * int(kw_price or 0)

def _drill_get(chat_id, uid):
    """(داده‌ی شهر، معدن کاربر یا None) با تولید به‌روز؛ چیزی ذخیره نمی‌شه."""
    d = _city_get(chat_id)
    dr = d["drills_data"].get(str(uid))
    if dr is not None:
        _drill_accrue(dr, d["kw_price"])
    return d, dr

def _drill_intro_text():
    e = _hx
    return (f'به بخش حفاری خوش اومدید{e(5818866927216369505, "⛏")}\n\n'
            f'این بخش رو باز کنید و الماس رایگان بگیرید{e(5830338333892418460, "💎")}\n\n'
            f'برای باز شدن این بخش شما نیاز به یک مدیر دارید{e(5818736497649524154, "👤")}\n\n'
            f'هزینه استخدام مدیر: {int(DRILL_HIRE_PRICE):,}{e(5940725397195853882, "💎")}\n\n'
            f'برای استخدام مدیر از دکمه زیر استفاده کنید{e(5820903824046432799, "👇")}')

def _drill_intro_markup(uid):
    m = types.InlineKeyboardMarkup()
    m.row(_city_btn("استخدام مدیر", f"drill|hire|{uid}", "5938109560249127910"))
    return m

def _drill_panel_text(dr):
    e = _hx
    dia = e(5940725397195853882, "💎")
    lvl = int(dr.get("level", 1) or 1)
    speed, cap, _kwh = _drill_vals(lvl)
    stock = int(float(dr.get("stock", 0) or 0))
    if cap > 0:
        stock = min(stock, cap)
    text = (f'معدن حفاری شما{e(5819151717907832367, "⛏")}\n\n'
            f'لول معدن: {lvl}/{int(DRILL_MAX_LEVEL)}{e(5938517659451658781, "📊")}\n\n'
            f'برق مصرف شده (KW): {_fmt_kw(dr.get("kw", 0) or 0)}{e(5938206901387924708, "🔌")}\n\n'
            f'الماس های ماین شده: {stock:,}{dia}\n\n'
            f'سرعت ماین الماس در ثانیه: {speed:g}{dia}\n\n'
            f'انبار: {cap:,}{e(5830338333892418460, "📦")}')
    cost = _drill_upgrade_cost(lvl)
    if cost is not None:
        text += f'\n\nهزینه ارتقاء لول: {cost:,}{dia}'
    return text

def _drill_panel_markup(dr, uid):
    m = types.InlineKeyboardMarkup()
    m.row(_city_btn("برداشت الماس ها", f"drill|harvest|{uid}", "5819151717907832367"))
    if _drill_upgrade_cost(int(dr.get("level", 1) or 1)) is not None:
        m.row(_city_btn("ارتقاء لول", f"drill|up|{uid}", "5816711927375602899"))
    m.row(_city_btn("قبض برق", f"drill|bill|{uid}", "5938206901387924708"))
    return m

def _drill_bill_text(dr):
    e = _hx
    _s, _c, kwh = _drill_vals(dr.get("level", 1))
    return (f'{e(5938206901387924708, "🧾")}قبض برق{e(5888937012253171131, "🧾")}\n\n'
            f'{e(5830381159011326872, "⚡")}در هر ساعت استفاده از برق {_fmt_kw(kwh)}KW برق مصرف میشه{e(5830144944399981619, "ℹ️")}\n\n'
            f'{e(5888937012253171131, "🧾")}قبض برق فعلی: {_drill_debt(dr):,}{e(5940725397195853882, "💎")}')

def _drill_show(chat_id, mid, uid, target):
    """target: panel | intro | bill"""
    d, dr = _drill_get(chat_id, uid)
    if dr is None:
        target = "intro"
    if target == "intro":
        safe_edit_message(_drill_intro_text(), chat_id, mid, reply_markup=_drill_intro_markup(uid), parse_mode="HTML")
    elif target == "bill":
        m = types.InlineKeyboardMarkup()
        m.row(_city_btn("تسویه بدهی", f"drill|pay|{uid}", "5888937012253171131"))
        m.row(back_button(f"drill|home|{uid}"))
        safe_edit_message(_drill_bill_text(dr), chat_id, mid, reply_markup=m, parse_mode="HTML")
    else:
        safe_edit_message(_drill_panel_text(dr), chat_id, mid,
                          reply_markup=_drill_panel_markup(dr, uid), parse_mode="HTML")

def _drill_flash(chat_id, mid, uid, text, target):
    """پیام موقت، بعد ۵ ثانیه برگشت به صفحه‌ی مقصد."""
    key = (chat_id, mid)
    nonce = time.time()
    if len(_city_tokens) > 5000:
        _city_tokens.clear()
    _city_tokens[key] = nonce
    safe_edit_message(text, chat_id, mid, reply_markup=None, parse_mode="HTML")
    _start_daemon_timer(PANEL_REVERT_SECONDS, _drill_revert, key, nonce, uid, target)

def _drill_revert(key, nonce, uid, target):
    if _city_tokens.get(key) != nonce:
        return
    _city_tokens.pop(key, None)
    _drill_show(key[0], key[1], uid, target)

def _drill_hire(chat_id, from_user):
    """استخدام مدیر. برمی‌گردونه (True, None) یا (False, 'have'|'start'|'funds'|'fail')."""
    uid = from_user.id
    with _city_lock:
        d = _city_get(chat_id)
        if str(uid) in d["drills_data"]:
            return False, "have"
        price = int(DRILL_HIRE_PRICE)
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user:
                return False, "start"
            if int(user.get("diamonds", 0) or 0) < price:
                return False, "funds"
            if price > 0 and update_diamonds(uid, -price) is None:
                return False, "fail"
            now = _shop_now().isoformat()
            d["drills_data"][str(uid)] = {"level": 1, "hired_at": now, "last_at": now,
                                          "stock": 0.0, "kw": 0.0, "debt": 0.0}
            if not _city_save(chat_id, d):
                if price > 0:
                    update_diamonds(uid, price)   # ذخیره نشد؛ پول برگردونده می‌شه
                logging.error(f"ثبت مدیر حفاری {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, None

def _drill_harvest(chat_id, from_user):
    """برداشت انبار. برمی‌گردونه (True, تعداد) یا (False, 'nodrill'|'debt'|'empty'|'fail')."""
    uid = from_user.id
    with _city_lock:
        d = _city_get(chat_id)
        dr = d["drills_data"].get(str(uid))
        if not dr:
            return False, "nodrill"
        _drill_accrue(dr, d["kw_price"])
        if _drill_blocked(dr):
            return False, "debt"
        amount = int(math.floor(float(dr.get("stock", 0) or 0) + 1e-9))
        if amount <= 0:
            return False, "empty"
        with _get_user_financial_lock(uid):
            if update_diamonds(uid, amount) is None:
                return False, "fail"
            dr["stock"] = max(0.0, float(dr.get("stock", 0) or 0) - amount)
            if not _city_save(chat_id, d):
                update_diamonds(uid, -amount)
                logging.error(f"ذخیره‌ی برداشت حفاری {uid} تو شهر {chat_id} ناموفق بود؛ برگشت داده شد")
                return False, "fail"
    return True, amount

def _drill_upgrade(chat_id, from_user):
    """ارتقاء لول. برمی‌گردونه (True, لول جدید) یا (False, 'nodrill'|'max'|'debt'|'funds'|'fail')."""
    uid = from_user.id
    with _city_lock:
        d = _city_get(chat_id)
        dr = d["drills_data"].get(str(uid))
        if not dr:
            return False, "nodrill"
        lvl = int(dr.get("level", 1) or 1)
        cost = _drill_upgrade_cost(lvl)
        if cost is None:
            return False, "max"
        _drill_accrue(dr, d["kw_price"])    # تولید تا همین لحظه با لول قبلی حساب می‌شه
        if _drill_blocked(dr):
            return False, "debt"
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user or int(user.get("diamonds", 0) or 0) < cost:
                return False, "funds"
            if cost > 0 and update_diamonds(uid, -cost) is None:
                return False, "fail"
            dr["level"] = lvl + 1
            if not _city_save(chat_id, d):
                if cost > 0:
                    update_diamonds(uid, cost)
                logging.error(f"ذخیره‌ی ارتقاء حفاری {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, lvl + 1

def _drill_settle(chat_id, from_user):
    """تسویه‌ی قبض برق. برمی‌گردونه (True, مبلغ) یا (False, 'nodrill'|'nodebt'|'funds'|'fail')."""
    uid = from_user.id
    with _city_lock:
        d = _city_get(chat_id)
        dr = d["drills_data"].get(str(uid))
        if not dr:
            return False, "nodrill"
        _drill_accrue(dr, d["kw_price"])
        amount = _drill_debt(dr)
        if amount <= 0:
            return False, "nodebt"
        with _get_user_financial_lock(uid):
            user = get_user(uid)
            if not user or int(user.get("diamonds", 0) or 0) < amount:
                return False, "funds"
            if update_diamonds(uid, -amount) is None:
                return False, "fail"
            dr["debt"] = 0.0
            dr["kw"] = 0.0
            d["diamonds"] += amount      # فقط پول برق می‌ره تو خزانه‌ی شهر
            if not _city_save(chat_id, d):
                update_diamonds(uid, amount)
                logging.error(f"ذخیره‌ی تسویه‌ی قبض حفاری {uid} تو شهر {chat_id} ناموفق بود؛ پول برگشت داده شد")
                return False, "fail"
    return True, amount

@bot.message_handler(func=lambda m: text_is(m, "حفاری"))
def text_drill(message):
    if _feature_gate(message, "drill"):
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, _casino_err("حفاری فقط تو گروه کار می‌کنه"), parse_mode="HTML")
        return
    uid = message.from_user.id
    if not get_user(uid):
        bot.reply_to(message, _start_first_err(), parse_mode="HTML")
        return
    cid = message.chat.id
    d, dr = _drill_get(cid, uid)
    if dr is not None:
        text, markup = _drill_panel_text(dr), _drill_panel_markup(dr, uid)
    else:
        text, markup = _drill_intro_text(), _drill_intro_markup(uid)
    safe_send_message(cid, text, reply_markup=markup, parse_mode="HTML",
                      reply_to_message_id=message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("drill|"))
def drill_callback(call):
    uid = check_panel_owner(call)
    if uid is None:
        return
    if _feature_gate(call, "drill"):
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    parts = call.data.split("|")
    act = parts[1] if len(parts) > 1 else ""
    chat_id, mid = call.message.chat.id, call.message.message_id
    e = _hx
    cancel_pending_step(chat_id, uid)
    _city_tokens.pop((chat_id, mid), None)
    err_icon = e(5819154526816444042, "❌")
    has = str(uid) in _city_get(chat_id)["drills_data"]

    if act == "hire":
        if has:
            _drill_show(chat_id, mid, uid, "panel")
            return
    elif not has:
        _drill_show(chat_id, mid, uid, "intro")
        return

    if act == "home":
        _drill_show(chat_id, mid, uid, "panel")
    elif act == "bill":
        _drill_show(chat_id, mid, uid, "bill")
    elif act in ("hire", "harvest", "up", "pay"):
        key = (chat_id, mid)
        with _city_lock:
            if key in _city_busy:
                return
            _city_busy.add(key)
        try:
            if act == "hire":
                ok, res = _drill_hire(chat_id, call.from_user)
            elif act == "harvest":
                ok, res = _drill_harvest(chat_id, call.from_user)
            elif act == "up":
                ok, res = _drill_upgrade(chat_id, call.from_user)
            else:
                ok, res = _drill_settle(chat_id, call.from_user)
        finally:
            with _city_lock:
                _city_busy.discard(key)
        generic = "عملیات انجام نشد، دوباره امتحان کن"
        if act == "hire":
            if ok:
                _drill_flash(chat_id, mid, uid, f'مدیر با موفقیت استخدام شد{e(5938109560249127910, "✅")}', "panel")
            elif res == "have":
                _drill_show(chat_id, mid, uid, "panel")
            else:
                msg = {"funds": "موجودی الماس کافی نیست", "start": "اول باید بات رو استارت کنی"}.get(res, generic)
                _drill_flash(chat_id, mid, uid, f'{html.escape(msg)} {err_icon}', "intro")
        elif act == "harvest":
            if ok:
                _drill_flash(chat_id, mid, uid,
                             f'موجودی انبار {res:,} الماس بود و به موجودی شما اضافه شد{e(5940725397195853882, "💎")}', "panel")
            elif res == "nodrill":
                _drill_show(chat_id, mid, uid, "intro")
            else:
                msg = {"debt": "اول قبض برق رو تسویه کن", "empty": "انبار خالیه"}.get(res, generic)
                _drill_flash(chat_id, mid, uid, f'{html.escape(msg)} {err_icon}', "panel")
        elif act == "up":
            if ok:
                _drill_flash(chat_id, mid, uid,
                             f'ارتقاء لول با موفقیت انجام شد لول جدید معدن: {res}{e(5935933089866846598, "🎉")}', "panel")
            elif res in ("max", "nodrill"):
                _drill_show(chat_id, mid, uid, "panel" if res == "max" else "intro")
            else:
                msg = {"debt": "اول قبض برق رو تسویه کن", "funds": "موجودی الماس کافی نیست"}.get(res, generic)
                _drill_flash(chat_id, mid, uid, f'{html.escape(msg)} {err_icon}', "panel")
        else:
            if ok:
                _drill_flash(chat_id, mid, uid, f'قبض برق تسویه شد {e(5938109560249127910, "✅")}', "panel")
            elif res == "nodrill":
                _drill_show(chat_id, mid, uid, "intro")
            else:
                msg = {"nodebt": "قبض برقی برای تسویه نداری", "funds": "موجودی الماس کافی نیست"}.get(res, generic)
                _drill_flash(chat_id, mid, uid, f'{html.escape(msg)} {err_icon}', "bill")

# ================== یارانه‌ی روزانه‌ی شهر ==================
_subsidy_tokens = {}   # (chat_id, message_id) -> nonce برای برگشت خودکار به پنل یارانه

def _subsidy_wealth(uid):
    """(الماس, بانک, ارزش گیفت‌ها, دارایی کل)"""
    user = get_user(uid) or {}
    dia = int(user.get("diamonds", 0) or 0)
    bank = int(user.get("bank_balance", 0) or 0)
    gifts = 0
    try:
        for g in get_user_gifts(uid):
            gifts += int(g.get("current_value", 0) or 0)
    except Exception as ex:
        logging.error(f"خطا در محاسبه‌ی ارزش گیفت‌ها برای یارانه ({uid}): {ex}")
    return dia, bank, gifts, dia + bank + gifts

def _subsidy_panel(chat_id, uid):
    e = _hx
    dia = e(5940725397195853882, "💎")
    dv, bank, gifts, total = _subsidy_wealth(uid)
    limit = int(SUBSIDY_MAX_WEALTH)
    eligible = total < limit
    registered = str(uid) in _city_get(chat_id)["subsidy_regs"]
    text = (
        f'برای ثبت نام در یارانه روزانه شما باید کمتر از {limit:,} الماس{dia} موجودی داشته باشید{e(5938490450833838343, "✨")}\n\n'
        f'موجودی الماس شما: {dv:,}{dia}\n\n'
        f'موجودی حساب بانکی: {bank:,}{dia}\n\n'
        f'ارزش کلی گیفت های شما: {gifts:,}{dia}\n\n'
        f'دارایی کلی: {total:,}{dia}\n\n'
    )
    if eligible:
        text += f'شما واجد شرایط برای یارانه می‌باشید{e(5938109560249127910, "✅")}\n\n'
    else:
        text += f'شما واجد شرایط برای یارانه نمی‌باشید{e(5938290000415167172, "❌")}\n\n'
    text += f'جهت ثبت نام از دکمه زیر استفاده کنید{e(5820903824046432799, "👇")}'
    m = types.InlineKeyboardMarkup()
    if registered:
        m.row(_city_btn("لغو یارانه", f"sub|cancel|{uid}", "5819154526816444042", "danger"))
    else:
        m.row(_city_btn("ثبت نام در یارانه", f"sub|reg|{uid}", "5832240029446971049", "success"))
    return text, m

def _subsidy_show_panel(chat_id, message_id, uid):
    text, m = _subsidy_panel(chat_id, uid)
    safe_edit_message(text, chat_id, message_id, reply_markup=m, parse_mode="HTML")

def _subsidy_revert(key, nonce, uid):
    if _subsidy_tokens.get(key) != nonce:
        return
    _subsidy_tokens.pop(key, None)
    _subsidy_show_panel(key[0], key[1], uid)

@bot.message_handler(func=lambda m: text_is(m, "یارانه"))
def text_subsidy(message):
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, _casino_err("یارانه فقط تو گروه کار می‌کنه"), parse_mode="HTML")
        return
    uid = message.from_user.id
    if not get_user(uid):
        bot.reply_to(message, _start_first_err(), parse_mode="HTML")
        return
    text, m = _subsidy_panel(message.chat.id, uid)
    safe_send_message(message.chat.id, text, reply_markup=m, parse_mode="HTML",
                      reply_to_message_id=message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("sub|"))
def subsidy_callback(call):
    uid = check_panel_owner(call)
    if uid is None:
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    parts = call.data.split("|")
    act = parts[1] if len(parts) > 1 else ""
    chat_id, mid = call.message.chat.id, call.message.message_id
    key = (chat_id, mid)
    e = _hx
    if act not in ("reg", "cancel"):
        return
    with _city_lock:
        if key in _city_busy:
            return
        _city_busy.add(key)
    try:
        _subsidy_tokens.pop(key, None)
        if act == "reg":
            if not get_user(uid):
                return
            total = _subsidy_wealth(uid)[3]
            if total >= int(SUBSIDY_MAX_WEALTH):
                result = f'شما واجد شرایط دریافت یارانه نیستید{e(5819154526816444042, "❌")}'
            else:
                name = (getattr(call.from_user, "first_name", None) or "کاربر").strip()[:40] or "کاربر"
                with _city_lock:
                    d = _city_get(chat_id)
                    d["subsidy_regs"][str(uid)] = name
                    if not _city_save(chat_id, d):
                        logging.error(f"ذخیره‌ی ثبت‌نام یارانه‌ی شهر {chat_id} در دیتابیس ناموفق بود (فقط تو حافظه)")
                result = f'ثبت نام موفقیت آمیز شما هر روز یارانه دریافت خواهید کرد{e(5830144944399981619, "👑")}'
        else:
            with _city_lock:
                d = _city_get(chat_id)
                if d["subsidy_regs"].pop(str(uid), None) is not None:
                    if not _city_save(chat_id, d):
                        logging.error(f"ذخیره‌ی لغو یارانه‌ی شهر {chat_id} در دیتابیس ناموفق بود (فقط تو حافظه)")
            result = f'یارانه روزانه شما لغو شد{e(5819154526816444042, "❌")}'
        safe_edit_message(result, chat_id, mid, reply_markup=None, parse_mode="HTML")
        nonce = time.time()
        if len(_subsidy_tokens) > 5000:
            _subsidy_tokens.clear()
        _subsidy_tokens[key] = nonce
        _start_daemon_timer(PANEL_REVERT_SECONDS, _subsidy_revert, key, nonce, uid)
    finally:
        with _city_lock:
            _city_busy.discard(key)

def _subsidy_send_list(chat_id, paid, share):
    e = _hx
    dia = e(5940725397195853882, "💎")
    head = (f'{e(5820903824046432799, "👇")}لیست دریافت کنندگان یارانه امروز\n'
            f'{e(5832240029446971049, "🏦")}یارانه از خزانه شهر واریز شده است\n\n')
    foot = f'سهم هر نفر: {share:,}{dia}'
    chunks, cur = [], head
    for _uid, name in paid:
        block = (f'کاربر: {html.escape(name)}{e(5843973755545590553, "👤")}\n'
                 f'\u200f┘─ مبلغ دریافتی: {share:,}{dia}\n\n')
        if len(cur) + len(block) > 3000:
            chunks.append(cur)
            cur = ""
        cur += block
    if len(cur) + len(foot) > 3500:
        chunks.append(cur)
        cur = ""
    chunks.append(cur + foot)
    for c in chunks:
        safe_send_message(chat_id, c, parse_mode="HTML")

def _subsidy_payout_chat(chat_id):
    # ثبت‌نام‌ها همون لحظه پاک می‌شن تا هر نفر هر روز دوباره مجبور به ثبت‌نام باشه (و واریز دوبار انجام نشه)
    with _city_lock:
        d = _city_get(chat_id)
        regs = dict(d["subsidy_regs"])
        if not regs:
            return
        d["subsidy_regs"] = {}
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی پاک‌شدن ثبت‌نام‌های یارانه‌ی شهر {chat_id} ناموفق بود (فقط تو حافظه)")
    limit = int(SUBSIDY_MAX_WEALTH)
    eligible = []
    for uid_s, name in regs.items():
        try:
            uid = int(uid_s)
        except ValueError:
            continue
        try:
            if _subsidy_wealth(uid)[3] < limit:   # اگه دارایی‌ش رفته بالاتر، واجد شرایط نیست
                eligible.append((uid, name))
        except Exception as ex:
            logging.error(f"خطا در بررسی شرایط یارانه برای {uid_s}: {ex}")
    if not eligible:
        return
    with _city_lock:
        d = _city_get(chat_id)
        pool = int(d["diamonds"] * float(SUBSIDY_PERCENT))
        share = pool // len(eligible)
        if share <= 0:
            return
        d["diamonds"] -= share * len(eligible)
        if not _city_save(chat_id, d):
            logging.error(f"ذخیره‌ی کسر یارانه از خزانه‌ی شهر {chat_id} ناموفق بود (فقط تو حافظه)")
    paid, failed = [], 0
    for uid, name in eligible:
        if update_diamonds(uid, share) is None:
            failed += 1
        else:
            paid.append((uid, name))
    if failed:   # واریزهای ناموفق به خزانه برمی‌گردن
        with _city_lock:
            d = _city_get(chat_id)
            d["diamonds"] += share * failed
            _city_save(chat_id, d)
    if paid:
        _subsidy_send_list(chat_id, paid, share)

def process_subsidies():
    try:
        for key in list(_SETTINGS.keys()):
            if not key.startswith("city_"):
                continue
            try:
                chat_id = int(key[5:])
            except ValueError:
                continue
            if not (_SETTINGS.get(key) or {}).get("subsidy_regs"):
                continue
            try:
                _subsidy_payout_chat(chat_id)
            except Exception as ex:
                logging.exception(f"خطا در واریز یارانه‌ی شهر {chat_id}: {ex}")
    except Exception as ex:
        logging.exception(f"خطا در اجرای یارانه‌ی روزانه: {ex}")

try:
    subsidy_scheduler = BackgroundScheduler(timezone=TEHRAN_TZ)
    subsidy_scheduler.add_job(process_subsidies, "cron", hour=int(SUBSIDY_HOUR), minute=int(SUBSIDY_MINUTE),
                              id="city_subsidy", replace_existing=True, max_instances=1, coalesce=True)
    subsidy_scheduler.start()
except Exception as _ex:
    subsidy_scheduler = None
    logging.error(f"راه‌اندازی زمان‌بند یارانه ناموفق بود: {_ex}")

def _subsidy_reschedule():
    """بعد از تغییر ساعت/دقیقه از پنل مدیریت، زمان واریز رو دوباره تنظیم می‌کنه."""
    try:
        if subsidy_scheduler is not None:
            subsidy_scheduler.reschedule_job("city_subsidy", trigger="cron",
                                             hour=int(SUBSIDY_HOUR), minute=int(SUBSIDY_MINUTE))
    except Exception as ex:
        logging.error(f"تغییر زمان واریز یارانه ناموفق بود: {ex}")

# ================== شناسه‌ی نسخه (برای اطمینان از اینکه کد جدید رو سرور اجرا می‌کنه) ==================
BUILD_TAG = "city-shop-rent-expiry-v8-enforced"
logging.info("BUILD_TAG=%s", BUILD_TAG)

@bot.message_handler(func=lambda m: text_is(m, "نسخه بات") and m.from_user and _adm_is_admin(m.from_user.id))
def text_build_tag(message):
    bot.reply_to(message, f"نسخه‌ی در حال اجرا: {BUILD_TAG}")

# بارگذاری تنظیمات ذخیره‌شده و اعمالشون
_settings_load_all()
_adm_load_apply_all()
_gift_rebuild_catalog()
_gift_market_reschedule()
_subsidy_reschedule()

# زمان‌بند جواهری بالاتر از بارگذاری bot_settings ساخته می‌شود؛ یک اجرای فوری بعد
# از بارگذاری کامل تنظیمات تضمین می‌کند اجاره‌های منقضی حتی پس از ری‌استارت،
# قبل از اولین تعامل کاربر از حالت فعال خارج شوند.
try:
    process_shops()
except Exception as _shop_boot_ex:
    logging.exception("بررسی اولیه‌ی انقضای اجاره‌های جواهری ناموفق بود: %s", _shop_boot_ex)

# ---------- پیام همگانی ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "📢 لطفاً پیام همگانی خود را بنویسید (متن یا با فرمت HTML):"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    register_timed_next_step_handler(call.message, admin_send_broadcast, expected_user_id=call.from_user.id)

def admin_send_broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    broadcast_text = message.text
    # دریافت تمام کاربران
    try:
        users = supabase.table("users").select("user_id").execute()
        if not users.data:
            bot.reply_to(message, "هیچ کاربری یافت نشد.")
            return
        count = 0
        for user in users.data:
            try:
                bot.send_message(user['user_id'], broadcast_text)
                count += 1
                time.sleep(0.05)  # جلوگیری از محدودیت
            except Exception:
                continue
        bot.reply_to(message, f"✅ پیام به {count} کاربر ارسال شد.")
    except Exception as e:
        bot.reply_to(message, f"❌ خطا در ارسال پیام همگانی: {e}")

# ---------- افزودن الماس (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_diamond")
def admin_add_diamond_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "➕ لطفاً آیدی عددی کاربر و مقدار الماس را به صورت زیر وارد کنید:\n`<user_id> <amount>`\nمثال: `123456789 100`"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    register_timed_next_step_handler(call.message, admin_add_diamond_execute, expected_user_id=call.from_user.id)

def admin_add_diamond_execute(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 and parts[0].isdigit() else None
    if len(parts) != 2 or not parts[0].isdigit() or amount is None:
        bot.reply_to(message, "❌ فرمت نامعتبر. مجدداً تلاش کنید. مثال: 123456789 100 یا 123456789 100k")
        return
    user_id = int(parts[0])
    if not get_user(user_id):
        bot.reply_to(message, "کاربر یافت نشد.")
        return
    update_diamonds(user_id, amount)
    bot.reply_to(message, f"✅ {amount} الماس به کاربر {user_id} اضافه شد. موجودی جدید: {get_balance(user_id)}")

# ---------- کم کردن الماس (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_remove_diamond")
def admin_remove_diamond_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "➖ لطفاً آیدی عددی کاربر و مقدار الماس را به صورت زیر وارد کنید:\n`<user_id> <amount>`\nمثال: `123456789 50`"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    register_timed_next_step_handler(call.message, admin_remove_diamond_execute, expected_user_id=call.from_user.id)

def admin_remove_diamond_execute(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 and parts[0].isdigit() else None
    if len(parts) != 2 or not parts[0].isdigit() or amount is None:
        bot.reply_to(message, "❌ فرمت نامعتبر. مجدداً تلاش کنید. مثال: 123456789 50 یا 123456789 50k")
        return
    user_id = int(parts[0])
    if not get_user(user_id):
        bot.reply_to(message, "کاربر یافت نشد.")
        return
    balance = get_balance(user_id)
    deduct = min(amount, balance)
    update_diamonds(user_id, -deduct)
    bot.reply_to(message, f"✅ {deduct} الماس از کاربر {user_id} کم شد. موجودی جدید: {get_balance(user_id)}")

# ---------- افزودن الماس به همه کاربران (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_diamond_all")
def admin_add_diamond_all_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "➕ لطفاً مقدار الماسی که می‌خواهید به همه‌ی کاربران اضافه شود را وارد کنید:\nمثال: `100` یا `100k`"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    register_timed_next_step_handler(call.message, admin_add_diamond_all_execute, expected_user_id=call.from_user.id)

def admin_add_diamond_all_execute(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    amount = parse_amount(message.text.strip())
    if amount is None or amount <= 0:
        bot.reply_to(message, "❌ مقدار نامعتبر. یک عدد مثبت وارد کن. مثال: 100 یا 100k")
        return
    try:
        users = supabase.table("users").select("user_id, diamonds").execute()
        rows = users.data or []
    except Exception as e:
        logging.error(f"خطا در دریافت لیست کاربران برای افزودن الماس همگانی: {e}")
        bot.reply_to(message, "❌ خطا در دریافت لیست کاربران.")
        return
    count = 0
    for row in rows:
        try:
            new_balance = int(row.get("diamonds", 0) or 0) + amount
            supabase.table("users").update({"diamonds": new_balance}).eq("user_id", row["user_id"]).execute()
            count += 1
        except Exception as e:
            logging.error(f"خطا در افزودن الماس همگانی به {row.get('user_id')}: {e}")
            continue
    bot.reply_to(message, f"✅ {amount:,} 💎 به {count} کاربر اضافه شد.")

# ---------- کم کردن الماس از همه کاربران (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_remove_diamond_all")
def admin_remove_diamond_all_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = (
        "➖ لطفاً مقدار الماسی که می‌خواهید از همه‌ی کاربران کم شود را وارد کنید:\n"
        "مثال: `50` یا `50k`\n"
        "(اگر موجودی کسی کمتر از این مقدار باشد، فقط تا صفر کم می‌شود.)"
    )
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    register_timed_next_step_handler(call.message, admin_remove_diamond_all_execute, expected_user_id=call.from_user.id)

def admin_remove_diamond_all_execute(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    amount = parse_amount(message.text.strip())
    if amount is None or amount <= 0:
        bot.reply_to(message, "❌ مقدار نامعتبر. یک عدد مثبت وارد کن. مثال: 50 یا 50k")
        return
    try:
        users = supabase.table("users").select("user_id, diamonds").execute()
        rows = users.data or []
    except Exception as e:
        logging.error(f"خطا در دریافت لیست کاربران برای کم کردن الماس همگانی: {e}")
        bot.reply_to(message, "❌ خطا در دریافت لیست کاربران.")
        return
    count = 0
    for row in rows:
        try:
            balance = int(row.get("diamonds", 0) or 0)
            deduct = min(amount, balance)
            if deduct <= 0:
                continue
            new_balance = balance - deduct
            supabase.table("users").update({"diamonds": new_balance}).eq("user_id", row["user_id"]).execute()
            count += 1
        except Exception as e:
            logging.error(f"خطا در کم کردن الماس همگانی از {row.get('user_id')}: {e}")
            continue
    bot.reply_to(message, f"✅ حداکثر {amount:,} 💎 از {count} کاربر کم شد.")

# ---------- ساخت کد هدیه (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_create_giftcode")
def admin_create_giftcode_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "🎁 لطفاً مقدار جایزه‌ی هر کد هدیه را وارد کنید:\nمثال: `1000` یا `1میل`"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    register_timed_next_step_handler(call.message, admin_giftcode_amount_step, expected_user_id=call.from_user.id)

def admin_giftcode_amount_step(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    amount = parse_amount(message.text.strip() if message.text else "")
    if amount is None:
        msg = bot.reply_to(message, "❌ مقدار نامعتبر. یک عدد مثبت وارد کن. مثال: 1000 یا 1میل")
        register_timed_next_step_handler(msg, admin_giftcode_amount_step, expected_user_id=message.from_user.id)
        return
    msg = bot.reply_to(message, "👤 برای چند نفر قابل فعال‌سازی باشد؟\nمثال: 50")
    register_timed_next_step_handler(msg, admin_giftcode_capacity_step, amount, expected_user_id=message.from_user.id)

def admin_giftcode_capacity_step(message, amount):
    if message.from_user.id not in ADMIN_IDS:
        return
    capacity = parse_amount(message.text.strip() if message.text else "")
    if capacity is None:
        msg = bot.reply_to(message, "❌ عدد نامعتبر. یک عدد صحیح مثبت وارد کن. مثال: 50")
        register_timed_next_step_handler(msg, admin_giftcode_capacity_step, amount, expected_user_id=message.from_user.id)
        return
    code = create_gift_code(message.from_user.id, amount, capacity)
    if not code:
        bot.reply_to(message, "❌ خطا در ساخت کد هدیه. دوباره تلاش کنید.")
        return
    text = (
        "کد هدیه جدید ساخته شد.🎁\n\n"
        f"کد : `{code}`\n"
        f"جایزه : {amount:,} الماس💎\n"
        f"ظرفیت : {capacity:,} 👤"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

# ---------- فعال‌سازی کد هدیه (توسط کاربران) ----------
_GIFT_CODE_PATTERN = re.compile(r"^[A-Z0-9]{8}$")

def _looks_like_gift_code(message):
    if not getattr(message, "text", None):
        return False
    if getattr(message, "from_user", None) is None or getattr(message.from_user, "is_bot", False):
        return False
    # اگر کاربر در وسط یک مرحله‌ی دیگر (مثلاً وارد کردن مبلغ بانک) است،
    # اولویت با همان مرحله است؛ کد هدیه را بررسی نمی‌کنیم.
    if _has_pending_step(message):
        return False
    text = message.text.strip().upper()
    return bool(_GIFT_CODE_PATTERN.match(text))

@bot.message_handler(func=_looks_like_gift_code)
def handle_gift_code_message(message):
    code = message.text.strip().upper()
    user_id = message.from_user.id
    if not get_user(user_id):
        create_user(user_id, get_display_name(message.from_user))
    success, msg, amount = redeem_gift_code(user_id, code)
    if success:
        bot.reply_to(message, f"کد هدیه ثبت شد😉\nجایزه شما {amount:,} الماس💎")
    elif msg:
        bot.reply_to(message, msg)
    # اگر msg هم None باشد یعنی اصلاً کدی با این متن وجود نداشته (پیام عادی
    # کاربر بوده)، پس هیچ پاسخی داده نمی‌شود.

# ================== مصرف‌کننده‌ی نهایی مراحل در انتظار ==================
# این هندلر باید آخرین @bot.message_handler ثبت‌شده در کل فایل باشد (به همین
# دلیل اینجا، درست قبل از بخش Webhook، قرار گرفته). چون TeleBot برای هر پیام
# فقط اولین هندلرِ منطبق را اجرا می‌کند، این هندلر فقط زمانی اجرا می‌شود که
# هیچ‌کدام از هندلرهای اختصاصی‌تر بالا (دستورات، عبارات متنی خاص و...) روی آن
# پیام match نشده باشند؛ یعنی دقیقاً برای پیام‌های «آزاد» مثل یک عدد ساده که
# قرار است مبلغ یک مرحله‌ی در انتظار (شرط، بانک، کازینو و...) باشد.
def _has_pending_step(message):
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if chat_id is None or user_id is None:
        return False
    with _next_step_lock:
        return (chat_id, user_id) in _next_step_pending

@bot.message_handler(func=_has_pending_step)
def _pending_step_catch_all(message):
    _consume_pending_step(message)

# ================== Webhook / Render ==================
@app.route("/", methods=["GET", "HEAD"])
def health_check():
    return "Bot is running!", 200

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    # GET is intentionally supported so the Render URL can be checked in a browser.
    if request.method == "GET":
        return "Webhook endpoint is alive", 200

    if TELEGRAM_WEBHOOK_SECRET:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if provided != TELEGRAM_WEBHOOK_SECRET:
            return "Forbidden", 403

    try:
        update_json = request.get_json(silent=True)
        if not update_json:
            return "Bad Request", 400

        update = telebot.types.Update.de_json(update_json)

        # اول خود آپدیت را به TeleBot بده تا هندلرهای کلمات و دکمه‌ها
        # بدون وابستگی به سیستم شمارش الماس اجرا شوند.
        incoming_message = getattr(update, "message", None)
        if incoming_message is not None and getattr(incoming_message, "text", None):
            _reply_ctx.chat_id = incoming_message.chat.id
            _reply_ctx.message_id = incoming_message.message_id
        else:
            _reply_ctx.chat_id = None
            _reply_ctx.message_id = None

        _t0 = time.monotonic()
        try:
            bot.process_new_updates([update])
        finally:
            _dt = time.monotonic() - _t0
            if _dt > 1.5:
                _cb = getattr(update, "callback_query", None)
                _desc = ("cb:" + str(getattr(_cb, "data", ""))) if _cb is not None else \
                        ("msg:" + str(getattr(incoming_message, "text", ""))[:40])
                _perf_note("update", _dt, _desc)
            _reply_ctx.chat_id = None
            _reply_ctx.message_id = None

        # شمارش پیام‌های گروه بعد از پردازش اصلی بات؛ اگر این بخش خطا بدهد
        # دریافت و اجرای هندلرهای اصلی بات مختل نمی‌شود.
        if incoming_message is not None:
            try:
                process_group_message_for_diamond_hunt(incoming_message)
            except Exception as e:
                logging.error(f"خطا در شمارش پیام گروه برای الماس: {e}")

        return "OK", 200
    except Exception as e:
        logging.exception("Telegram webhook update processing failed: %s", e)
        # خطای داخلی نباید باعث retry بی‌نهایت Telegram شود.
        return "OK", 200

# ================== اجرا ==================
def _configure_webhook():
    # فقط URL همین سرویس فعلی Render استفاده می‌شود؛ هیچ URL قدیمی/ثابتی در کد نیست.
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not render_url:
        render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip().strip("/")
        if render_host:
            render_url = f"https://{render_host}"

    configured_url = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")
    if configured_url:
        WEBHOOK_URL = configured_url
    elif render_url:
        WEBHOOK_URL = f"{render_url}/webhook"
    else:
        raise RuntimeError(
            "Render URL پیدا نشد. WEBHOOK_URL یا RENDER_EXTERNAL_URL "
            "یا RENDER_EXTERNAL_HOSTNAME را تنظیم کنید."
        )

    try:
        # هر webhook قبلی حذف می‌شود تا فقط آدرس سرویس فعلی باقی بماند.
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=TELEGRAM_WEBHOOK_SECRET or None,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            max_connections=40,
        )
        logging.info("Telegram webhook configured successfully: %s", WEBHOOK_URL)
    except Exception as e:
        logging.exception("Failed to configure Telegram webhook: %s", e)
        raise


if __name__ == "__main__":
    _configure_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
elif os.environ.get("RENDER") or os.environ.get("WEBHOOK_URL"):
    # اجرا با gunicorn (gunicorn bot7:app --workers 1 --threads 32 --timeout 120)
    _configure_webhook()
