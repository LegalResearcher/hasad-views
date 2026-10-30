import os
import re
import sys

from account_rotation import select_rotating_account
from browser_sender import BrowserSender

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
TARGET_CHANNEL = "hasadalyoum"
LINK_PATTERN = re.compile(rf"^https://t\.me/{TARGET_CHANNEL}/\d+$")


def log(message):
    print(message, flush=True)


def process_manual_retry(link, sender):
    status = sender(link)
    if status == "ok":
        log("✅ نجح الرابط مرة واحدة.")
        return 0
    if status == "fatal":
        log("⏹️ توقف: تحقق من بيانات الدخول أو الرصيد.")
        return 1
    log("⚠️ لم ينجح الطلب عبر الموقع.")
    return 1


def main():
    link = os.environ.get("TARGET_LINK", "").strip()
    if not LINK_PATTERN.fullmatch(link):
        log(f"❌ الرابط غير صالح؛ يلزم رابط منشور من قناة {TARGET_CHANNEL}.")
        return 2
    try:
        account_number, username, password = select_rotating_account(os.environ, STATE_DIR)
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار حساب؛ تحقق من الأسرار وحالة account_rotation.json: {error}")
        return 1
    log(f"🚀 إعادة تشغيل يدوية للرابط المحدد: {link} | الحساب رقم {account_number}")
    with BrowserSender(username, password) as sender:
        return process_manual_retry(link, sender)


if __name__ == "__main__":
    sys.exit(main())
