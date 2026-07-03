import asyncio
import random
import re
import sqlite3
import time
from telethon import TelegramClient, events
from telethon.errors import PersistentTimestampOutdatedError, FloodWaitError

# ===================== تنظیمات اصلی =====================
API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
print("test")
GROUP_ID = -1003979242735

EXTRA_GROUPS = [
]

SESSION_NAME = "my_account_session"
DB_FILE = "timers.db"

# ---- تنظیمات بخش رندوم میو ----
MEOW_INTERVAL_SECONDS = 285
MEOW_CHOICES = ["میو", "مع", "معو", "میو میو"]

# ---- تنظیمات بخش "پیشی" + کلیک روی دکمه ----
PISHI_INTERVAL_SECONDS = 40 * 60
PISHI_TEXT = "پیشی"
TARGET_BOT = "@MeowieQBot"
BUTTON_TEXT = "برداشت میو پوینت ها"
WAIT_FOR_BUTTON_SECONDS = 15

# ---- تنظیمات بخش "ماهیگیری" ----
FISHING_INTERVAL_SECONDS = 55 * 60
FISHING_TEXT = "ماهی"
SELL_FISH_BUTTON = "فروش ماهی"
GIVE_TO_CAT_BUTTON = "بده پیشی بخوره"
STOMACH_THRESHOLD = 8  # زیر 8 → بده پیشی بخوره | 8 به بالا → فروش ماهی

# ---- دکمه نجات پیشی ----
RESCUE_BUTTON_TEXT = "نجات پیشی خیابونی "

# ---- تنظیمات بخش "میوهام" + انتقال میو پوینت ----
MEOWHAM_INTERVAL_SECONDS = 60 * 60
MEOWHAM_TEXT = "میوهام"
TRANSFER_TARGET_USERNAME = "@Tung_Suhur"
WAIT_FOR_PROFILE_SECONDS = 15

# ---- تنظیمات بخش پیام وضعیت به آیدی خاص ----
STATUS_DM_INTERVAL_SECONDS = 2 * 60 * 60
STATUS_DM_TARGET_USER_ID = 7196274489
STATUS_DM_TEXT = "سلف فعال است✅"
# ==========================================================

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)


# ===================== دیتابیس =====================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            key TEXT PRIMARY KEY,
            last_run REAL NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cat_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            stomach INTEGER NOT NULL DEFAULT 0
        )
    """)
    c.execute("INSERT OR IGNORE INTO cat_stats (id, stomach) VALUES (1, 0)")
    conn.commit()
    conn.close()


def get_last_run(key: str) -> float:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT last_run FROM timers WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0.0


def set_last_run(key: str, ts: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO timers (key, last_run) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run
    """, (key, ts))
    conn.commit()
    conn.close()


def get_stomach() -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT stomach FROM cat_stats WHERE id = 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def set_stomach(value: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cat_stats SET stomach = ? WHERE id = 1", (value,))
    conn.commit()
    conn.close()
    print(f"[i] شکم ذخیره شد: {value}")


def seconds_until_next(key: str, interval: float) -> float:
    last = get_last_run(key)
    if last == 0.0:
        return 5.0
    elapsed = time.time() - last
    remaining = interval - elapsed
    return max(0.0, remaining)


# ===================== توابع کمکی =====================

async def safe_send(group_id, text, retries=3):
    for attempt in range(retries):
        try:
            await client.send_message(group_id, text)
            return True
        except FloodWaitError as e:
            print(f"[!] FloodWait: {e.seconds} ثانیه صبر می‌کنم...")
            await asyncio.sleep(e.seconds + 5)
        except PersistentTimestampOutdatedError:
            wait = 10 * (attempt + 1)
            print(f"[!] PersistentTimestampOutdated: {wait} ثانیه... ({attempt+1}/{retries})")
            await asyncio.sleep(wait)
        except Exception as e:
            print(f"[!] خطا در ارسال: {e}")
            await asyncio.sleep(5)
    return False


async def safe_iter_messages(group_id, limit=5):
    for attempt in range(3):
        try:
            messages = []
            async for msg in client.iter_messages(group_id, limit=limit):
                messages.append(msg)
            return messages
        except PersistentTimestampOutdatedError:
            wait = 10 * (attempt + 1)
            print(f"[!] PersistentTimestampOutdated هنگام خواندن: {wait} ثانیه...")
            await asyncio.sleep(wait)
        except Exception as e:
            print(f"[!] خطا در خواندن پیام: {e}")
            await asyncio.sleep(5)
    return []


def parse_stomach(text: str):
    """
    از متن پیام پیشی، عدد شکم رو استخراج میکنه.
    مثال: 🍖 شکم : 😻 عاشقتمیووو (`10` / `10`)
    عدد اول رو برمیگردونه (مقدار فعلی)
    """
    match = re.search(r"شکم\s*:.*?`(\d+)`\s*/\s*`\d+`", text)
    if match:
        return int(match.group(1))
    return None


def parse_meow_points(text: str):
    """
    از متن پیام پروفایل، عدد مقابل «💰 میو پوینت ها :» رو استخراج میکنه.
    مثال: 💰 میو پوینت ها : `86,405` 🪙  (عدد داخل بک‌تیک هم پشتیبانی میشه)
    ویرگول‌های احتمالی داخل عدد حذف میشن و مقدار به صورت int برگردونده میشه.
    """
    match = re.search(r"میو\s*پوینت\s*ها\s*:\s*`?([\d,]+)`?", text)
    if match:
        raw_number = match.group(1).replace(",", "")
        if raw_number.isdigit():
            return int(raw_number)
    return None


def is_target_bot(msg, sender) -> bool:
    sender_username = getattr(sender, "username", None)
    return (
        str(msg.sender_id) == str(TARGET_BOT).lstrip("@")
        or (sender_username and f"@{sender_username}" == TARGET_BOT)
    )


# ===================== حلقه‌های اصلی =====================

async def send_meow_loop():
    wait = seconds_until_next("meow", MEOW_INTERVAL_SECONDS)
    if wait > 0:
        print(f"[~] میو: {wait:.0f} ثانیه مونده...")
        await asyncio.sleep(wait)

    while True:
        text = random.choice(MEOW_CHOICES)
        ok = await safe_send(GROUP_ID, text)
        if ok:
            set_last_run("meow", time.time())
            print(f"[+] میو رندوم '{text}' ارسال شد.")
        await asyncio.sleep(MEOW_INTERVAL_SECONDS)


async def send_pishi_and_click_loop():
    wait = seconds_until_next("pishi", PISHI_INTERVAL_SECONDS)
    if wait > 0:
        print(f"[~] پیشی: {wait:.0f} ثانیه مونده...")
        await asyncio.sleep(wait)

    while True:
        try:
            ok = await safe_send(GROUP_ID, PISHI_TEXT)
            if ok:
                set_last_run("pishi", time.time())
                print(f"[+] پیام '{PISHI_TEXT}' ارسال شد. منتظر پاسخ بات...")

            clicked = False
            elapsed = 0
            while elapsed < WAIT_FOR_BUTTON_SECONDS and not clicked:
                await asyncio.sleep(1)
                elapsed += 1
                messages = await safe_iter_messages(GROUP_ID, limit=5)
                for msg in messages:
                    if msg.sender_id is None or not msg.buttons:
                        continue
                    sender = await msg.get_sender()
                    if not is_target_bot(msg, sender):
                        continue

                    # --- پارس و ذخیره شکم ---
                    msg_text = msg.text or ""
                    stomach_val = parse_stomach(msg_text)
                    if stomach_val is not None:
                        set_stomach(stomach_val)

                    # --- کلیک دکمه برداشت ---
                    for row in msg.buttons:
                        for button in row:
                            if button.text.strip() == BUTTON_TEXT:
                                await msg.click(text=BUTTON_TEXT)
                                print(f"[+] دکمه '{BUTTON_TEXT}' زده شد.")
                                clicked = True
                                break
                        if clicked:
                            break
                    if clicked:
                        break

            if not clicked:
                print("[!] دکمه پیشی توی این دور پیدا نشد.")

        except Exception as e:
            print(f"[!] خطا در حلقه پیشی: {e}")

        await asyncio.sleep(PISHI_INTERVAL_SECONDS)


async def send_fishing_loop():
    wait = seconds_until_next("fishing", FISHING_INTERVAL_SECONDS)
    if wait > 0:
        print(f"[~] ماهی: {wait:.0f} ثانیه مونده...")
        await asyncio.sleep(wait)

    while True:
        try:
            ok = await safe_send(GROUP_ID, FISHING_TEXT)
            if ok:
                set_last_run("fishing", time.time())
                print(f"[+] پیام '{FISHING_TEXT}' ارسال شد. منتظر پاسخ بات...")

            handled = False
            elapsed = 0
            while elapsed < WAIT_FOR_BUTTON_SECONDS and not handled:
                await asyncio.sleep(1)
                elapsed += 1
                messages = await safe_iter_messages(GROUP_ID, limit=5)
                for msg in messages:
                    if msg.sender_id is None or not msg.buttons:
                        continue
                    sender = await msg.get_sender()
                    if not is_target_bot(msg, sender):
                        continue

                    button_texts = {b.text.strip() for row in msg.buttons for b in row}
                    if not ({SELL_FISH_BUTTON, GIVE_TO_CAT_BUTTON} & button_texts):
                        continue

                    # --- تصمیم بر اساس شکم ---
                    stomach = get_stomach()
                    print(f"[i] شکم فعلی: {stomach}")

                    if stomach < STOMACH_THRESHOLD:
                        target_button = GIVE_TO_CAT_BUTTON
                        print(f"[i] شکم {stomach} < {STOMACH_THRESHOLD} → '{GIVE_TO_CAT_BUTTON}'")
                    else:
                        target_button = SELL_FISH_BUTTON
                        print(f"[i] شکم {stomach} >= {STOMACH_THRESHOLD} → '{SELL_FISH_BUTTON}'")

                    for row in msg.buttons:
                        for button in row:
                            if button.text.strip() == target_button:
                                await msg.click(text=target_button)
                                print(f"[+] کلیک '{target_button}' (شکم={stomach})")
                                handled = True
                                break
                        if handled:
                            break
                    if handled:
                        break

            if not handled:
                print("[!] پیام ماهیگیری توی این دور پیدا نشد.")

        except Exception as e:
            print(f"[!] خطا در حلقه ماهیگیری: {e}")

        await asyncio.sleep(FISHING_INTERVAL_SECONDS)


async def send_meowham_and_transfer_loop():
    """
    هر MEOWHAM_INTERVAL_SECONDS یکبار:
    1) پیام «میوهام» ارسال میشه.
    2) منتظر پیام پروفایل از TARGET_BOT میمونه.
    3) عدد مقابل «💰 میو پوینت ها :» استخراج و ذخیره میشه.
    4) بلافاصله پیام «انتقال میویی [عدد] @Tung_Suhur» ارسال میشه.
    """
    wait = seconds_until_next("meowham", MEOWHAM_INTERVAL_SECONDS)
    if wait > 0:
        print(f"[~] میوهام: {wait:.0f} ثانیه مونده...")
        await asyncio.sleep(wait)

    while True:
        try:
            ok = await safe_send(GROUP_ID, MEOWHAM_TEXT)
            if ok:
                set_last_run("meowham", time.time())
                print(f"[+] پیام '{MEOWHAM_TEXT}' ارسال شد. منتظر پروفایل...")

            meow_points = None
            elapsed = 0
            while elapsed < WAIT_FOR_PROFILE_SECONDS and meow_points is None:
                await asyncio.sleep(1)
                elapsed += 1
                messages = await safe_iter_messages(GROUP_ID, limit=5)
                for msg in messages:
                    if msg.sender_id is None:
                        continue
                    sender = await msg.get_sender()
                    if not is_target_bot(msg, sender):
                        continue

                    msg_text = msg.text or ""
                    extracted = parse_meow_points(msg_text)
                    if extracted is not None:
                        meow_points = extracted
                        print(f"[i] میو پوینت استخراج شد: {meow_points}")
                        break

            if meow_points is not None:
                transfer_text = f"انتقال میویی {meow_points} {TRANSFER_TARGET_USERNAME}"
                sent_transfer_msg = await client.send_message(GROUP_ID, transfer_text)
                sent_at = sent_transfer_msg.id
                print(f"[+] پیام انتقال ارسال شد: {transfer_text}")

                # منتظر پیام جدید از بات با اینلاین باتن میمونیم
                clicked = False
                elapsed = 0
                while elapsed < WAIT_FOR_BUTTON_SECONDS and not clicked:
                    await asyncio.sleep(1)
                    elapsed += 1
                    messages = await safe_iter_messages(GROUP_ID, limit=5)
                    for msg in messages:
                        # فقط پیام‌هایی که بعد از پیام خودمون اومدن رو بررسی کن
                        if msg.id <= sent_at:
                            continue
                        if msg.sender_id is None or not msg.buttons:
                            continue
                        sender = await msg.get_sender()
                        if not is_target_bot(msg, sender):
                            continue

                        try:
                            await msg.click(0, 0)
                            print("[+] دکمه تایید انتقال (پیام جدید بات) زده شد.")
                            clicked = True
                        except Exception as e:
                            print(f"[!] خطا در کلیک دکمه پیام انتقال: {e}")
                        break

                if not clicked:
                    print("[!] پیام جدید بات با دکمه تایید انتقال پیدا نشد.")
            else:
                print("[!] پیام پروفایل پیدا نشد یا فرمت عوض شده؛ انتقال این دور انجام نشد.")
        except Exception as e:
            print(f"[!] خطا در حلقه میوهام/انتقال: {e}")

        await asyncio.sleep(MEOWHAM_INTERVAL_SECONDS)


async def send_status_dm_loop():
    """
    هر STATUS_DM_INTERVAL_SECONDS یکبار به STATUS_DM_TARGET_USER_ID
    پیام خصوصی STATUS_DM_TEXT ارسال میکنه.
    """
    wait = seconds_until_next("status_dm", STATUS_DM_INTERVAL_SECONDS)
    if wait > 0:
        print(f"[~] پیام وضعیت: {wait:.0f} ثانیه مونده...")
        await asyncio.sleep(wait)

    while True:
        try:
            ok = await safe_send(STATUS_DM_TARGET_USER_ID, STATUS_DM_TEXT)
            if ok:
                set_last_run("status_dm", time.time())
                print(f"[+] پیام وضعیت به {STATUS_DM_TARGET_USER_ID} ارسال شد.")
            else:
                print(f"[!] ارسال پیام وضعیت ناموفق بود.")
        except Exception as e:
            print(f"[!] خطا در حلقه پیام وضعیت: {e}")

        await asyncio.sleep(STATUS_DM_INTERVAL_SECONDS)


# ===================== Rescue Listener (همه گروه‌ها) =====================

async def rescue_listener():
    """
    تو GROUP_ID + همه EXTRA_GROUPS گوش میده.
    اگه پیامی از TARGET_BOT با دکمه نجات پیشی اومد، سریع کلیک میکنه
    تا دکمه کاملاً از بین بره.
    """
    all_groups = [GROUP_ID] + EXTRA_GROUPS

    @client.on(events.NewMessage(chats=all_groups))
    async def handler(event):
        msg = event.message
        if not msg.buttons:
            return

        sender = await msg.get_sender()
        if not is_target_bot(msg, sender):
            return

        button_texts = {b.text.strip() for row in msg.buttons for b in row}
        if RESCUE_BUTTON_TEXT not in button_texts:
            return

        chat_id = event.chat_id
        print(f"[!] دکمه '{RESCUE_BUTTON_TEXT}' پیدا شد! (گروه: {chat_id}) شروع کلیک...")

        while True:
            try:
                fresh = await client.get_messages(chat_id, ids=msg.id)
                if not fresh or not fresh.buttons:
                    print(f"[+] دکمه '{RESCUE_BUTTON_TEXT}' دیگه نیست.")
                    break

                current_texts = {b.text.strip() for row in fresh.buttons for b in row}
                if RESCUE_BUTTON_TEXT not in current_texts:
                    print(f"[+] دکمه '{RESCUE_BUTTON_TEXT}' دیگه نیست.")
                    break

                await fresh.click(text=RESCUE_BUTTON_TEXT)
                print(f"[+] کلیک روی '{RESCUE_BUTTON_TEXT}' (گروه: {chat_id})")
                await asyncio.sleep(1.5)

            except Exception as e:
                print(f"[!] خطا در حلقه نجات: {e}")
                break

    await client.run_until_disconnected()


# ===================== Main =====================

async def main():
    init_db()
    await client.start()
    me = await client.get_me()
    print(f"[+] وارد شدی به عنوان: {me.first_name} (@{me.username})")
    print(f"[+] شکم فعلی تو DB: {get_stomach()}")
    print("[+] اسکریپت شروع شد. Ctrl+C برای توقف.\n")

    await asyncio.gather(
        send_meow_loop(),
        send_pishi_and_click_loop(),
        send_fishing_loop(),
        send_meowham_and_transfer_loop(),
        send_status_dm_loop(),
        rescue_listener(),
    )


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
