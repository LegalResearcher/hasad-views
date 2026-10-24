import os
import re
import sys

from account_rotation import select_rotating_account
from browser_sender import BrowserSender

LINK_PATTERN = re.compile(r"^https://t\.me/hasadalyoum/\d+$")
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")


def log(message):
    print(message, flush=True)


def mark_done(link):
    os.makedirs(os.path.dirname(DONE_PATH), exist_ok=True)
    existing = []
    if os.path.exists(DONE_PATH):
        with open(DONE_PATH, "r", encoding="utf-8") as file:
            existing = [line.strip() for line in file if line.strip()]
    if link not in existing:
        with open(DONE_PATH, "a", encoding="utf-8") as file:
            file.write(link + "\n")


def main():
    link = os.environ.get("TARGET_LINK", "").strip()
    if not LINK_PATTERN.fullmatch(link):
        log("❌ الرابط غير صالح؛ يلزم رابط منشور من قناة hasadalyoum.")
        return 2

    try:
        account_number, username, password = select_rotating_account(os.environ, STATE_DIR)
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار حساب؛ تحقق من الأسرار الثلاثة وحالة account_rotation.json: {error}")
        return 1

    log(f"🚀 إعادة تشغيل يدوية للرابط المحدد: {link} | الحساب رقم {account_number}")
    with BrowserSender(username, password) as sender:
        status = sender(link)

    if status == "ok":
        log("✅ تم الطلب عبر الموقع.")
        mark_done(link)
        return 0
    log("⚠️ لم ينجح الطلب عبر الموقع.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
