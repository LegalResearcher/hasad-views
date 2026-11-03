import os
import re
import sys
import tempfile
import time

import requests

from account_rotation import select_rotating_account
from browser_sender import BrowserSender
from processing_state import (
    MAX_FAILURES_PER_LINK,
    MAX_SUCCESSES_PER_LINK,
    can_process,
    load_failures,
    load_successes,
    record_result,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)
LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")
TARGET_CHANNEL = "hasadalyoum"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
}


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def write_lines(path, lines):
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp_path = tempfile.mkstemp(prefix=".state-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            for line in lines:
                file.write(line + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def append_unique(path, links):
    existing = read_lines(path)
    known = set(existing)
    additions = [link for link in links if link not in known]
    if additions:
        write_lines(path, existing + additions)


def fetch_links():
    response = requests.get(
        f"https://t.me/s/{TARGET_CHANNEL}", headers=HEADERS, timeout=30
    )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram returned HTTP {response.status_code}")
    ids = sorted({int(value) for value in re.findall(
        rf"/{re.escape(TARGET_CHANNEL)}/(\d+)", response.text
    )})
    return [f"https://t.me/{TARGET_CHANNEL}/{number}" for number in ids]


def pull_once():
    discovered = fetch_links()
    processed = set(read_lines(PROCESSED_PATH))
    current_batch = [link for link in discovered if link not in processed]
    if not current_batch:
        pending = read_lines(LINKS_PATH)
        if pending:
            log(f"🔁 استكمال {len(pending)} روابط لم يصلها التنفيذ في تشغيل سابق.")
            return pending
        log("🔎 لا توجد روابط جديدة.")
        return []

    append_unique(LINKS_PATH, current_batch)
    write_lines(PROCESSED_PATH, list(dict.fromkeys(read_lines(PROCESSED_PATH) + current_batch)))
    log(f"🆕 عُثر على {len(current_batch)} رابط جديد؛ سيُعالج كل رابط مرة واحدة فقط.")
    return current_batch


def remove_from_queue(links):
    selected = set(links)
    remaining = [link for link in read_lines(LINKS_PATH) if link not in selected]
    write_lines(LINKS_PATH, remaining)


def run_browser_send(sender, links):
    if not links:
        log("💤 لا توجد روابط للتنفيذ.")
        return 0
    failures = 0
    processed_count = 0
    for link in links:
        log(f"معالجة الرابط مرة واحدة في هذه الدفعة: {link}")
        processed_count += 1
        try:
            status = sender(link)
        except Exception as error:
            log(f"⚠️ تعذر تنفيذ الرابط ({type(error).__name__}: {error})")
            status = "fail"
        if status == "ok":
            log("✅ نجح الرابط.")
        elif status == "fatal":
            log("⏹️ توقف: تحقق من بيانات الدخول أو الرصيد.")
            failures += 1
            break
        else:
            log("⚠️ لم ينجح الطلب عبر الموقع.")
            failures += 1
    remove_from_queue(links[:processed_count])
    log(f"انتهى التشغيل بعد معالجة {processed_count} روابط.")
    return 1 if failures else 0


def run_previous_links_until_five(sender, state_dir=None):
    """إعادة دفع الروابط السابقة عند عدم ظهور روابط جديدة، مثل الجنوب."""
    state_dir = STATE_DIR if state_dir is None else state_dir
    processed_path = os.path.join(state_dir, "processed_links.txt")
    queue_path = os.path.join(state_dir, "telegram_links.txt")
    attempts = load_failures(state_dir)
    successes = load_successes(state_dir)

    # الأحدث أولًا، مع إبقاء روابط الطابور غير المنفذة ضمن الأولوية.
    all_links = list(dict.fromkeys(
        list(reversed(read_lines(processed_path)))
        + list(reversed(read_lines(queue_path)))
    ))
    eligible = [
        link for link in all_links
        if can_process(link, state_dir, successes, attempts)
    ]
    if not eligible:
        log("💤 لا توجد روابط سابقة مؤهلة للدفع.")
        return 0

    max_tries = 5
    log(f"🔁 لا توجد روابط جديدة؛ استكمال الروابط السابقة حتى {MAX_SUCCESSES_PER_LINK} نجاحات.")
    failures = 0
    tries = 0
    for link in eligible:
        if tries >= max_tries:
            break
        tries += 1
        log(f"معالجة سابقة ({successes.get(link, 0)}/{MAX_SUCCESSES_PER_LINK} نجاح): {link}")
        try:
            status = sender(link)
        except Exception as error:
            log(f"⚠️ تعذر تنفيذ الرابط ({type(error).__name__}: {error})")
            status = "fail"

        if status == "fatal":
            log("⏹️ توقف: تحقق من بيانات الدخول أو الرصيد.")
            return 1
        if status == "ok":
            _, result = record_result(link, status, state_dir, successes, attempts)
            if result == "success":
                log(f"✅ نجاح الرابط: {successes[link]}/{MAX_SUCCESSES_PER_LINK}.")
        else:
            _, result = record_result(link, status, state_dir, successes, attempts)
            failures += 1
            if result == "failure_limit":
                log(f"🚫 تم تخطي الرابط بعد {MAX_FAILURES_PER_LINK} محاولات فاشلة.")

    log(f"انتهى تشغيل الروابط السابقة بعد {tries} طلبات؛ الحد 5 طلبات.")
    return 1 if failures else 0



def main():
    try:
        account_number, username, password = select_rotating_account(os.environ, STATE_DIR)
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار الحساب؛ تحقق من الأسرار وحالة account_rotation.json: {error}")
        return 1

    log(
        f"🚀 تشغيل حصاد | القناة: @{TARGET_CHANNEL} | "
        f"الحساب رقم {account_number} من 3"
    )

    # مثل منطق الجنوب: فشل/غياب السحب لا يمنع استكمال الطابور الموجود.
    links = []
    processed_before_pull = set(read_lines(PROCESSED_PATH))
    fetch_failed = False
    try:
        links = pull_once()
    except Exception as error:
        fetch_failed = True
        log(f"❌ تعذر سحب الروابط الجديدة ({type(error).__name__}: {error})")

    with BrowserSender(username, password) as sender:
        has_new_links = any(link not in processed_before_pull for link in links)
        if fetch_failed or not has_new_links:
            return run_previous_links_until_five(sender)
        return run_browser_send(sender, links)


if __name__ == "__main__":
    sys.exit(main())
