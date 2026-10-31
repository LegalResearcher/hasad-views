import os
import re
import sys

from account_rotation import select_rotating_account
from browser_sender import BrowserSender
from processing_state import (
    MAX_SUCCESSES_PER_LINK,
    can_process,
    load_failures,
    load_successes,
    mark_account_handled,
    record_result,
)

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
TARGET_CHANNEL = "hasadalyoum"
LINK_PATTERN = re.compile(rf"^https://t\.me/{TARGET_CHANNEL}/\d+$")


def log(message):
    print(message, flush=True)


def process_manual_retry(link, sender, account_number=1, state_dir=STATE_DIR):
    successes = load_successes(state_dir)
    failures = load_failures(state_dir)
    if not can_process(link, state_dir, successes, failures):
        if successes.get(link, 0) >= MAX_SUCCESSES_PER_LINK:
            log(f"⛔ رُفض الطلب: بلغ الرابط الحد الأقصى ({MAX_SUCCESSES_PER_LINK} نجاحات).")
        else:
            log("⛔ رُفض الطلب: الرابط مكتمل في السجل القديم أو تم تخطيه بعد ثلاث إخفاقات.")
        return 2

    mark_account_handled(link, account_number, state_dir)
    status = sender(link)
    _, result = record_result(link, status, state_dir, successes, failures)
    if result == "success":
        log("✅ نجح الرابط مرة واحدة.")
        return 0
    if result == "failure_limit":
        log("🚫 تم تخطي الرابط بعد ثلاث محاولات فاشلة.")
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
        return process_manual_retry(link, sender, account_number, STATE_DIR)


if __name__ == "__main__":
    sys.exit(main())
