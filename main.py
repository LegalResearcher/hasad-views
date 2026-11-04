import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import requests

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
ACCOUNT_COUNT = 5
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
    log(f"🆕 عُثر على {len(current_batch)} رابط جديد؛ ستعالجه الحسابات الخمسة بالتوازي.")
    return current_batch


def remove_from_queue(links):
    selected = set(links)
    remaining = [link for link in read_lines(LINKS_PATH) if link not in selected]
    write_lines(LINKS_PATH, remaining)


def load_accounts(environ):
    accounts = []
    missing = []
    for number in range(1, ACCOUNT_COUNT + 1):
        username = environ.get(f"SITE_USERNAME_{number}", "").strip()
        password = environ.get(f"SITE_PASSWORD_{number}", "")
        if not username:
            missing.append(f"SITE_USERNAME_{number}")
        if not password:
            missing.append(f"SITE_PASSWORD_{number}")
        accounts.append((number, username, password))
    if missing:
        raise ValueError("Missing required secrets: " + ", ".join(missing))
    return accounts


def _send_account(account, links):
    number, username, password = account
    results = []
    try:
        with BrowserSender(username, password) as sender:
            for link in links:
                log(f"الحساب {number}: معالجة {link}")
                try:
                    status = sender(link)
                except Exception as error:
                    log(f"الحساب {number}: تعذر التنفيذ ({type(error).__name__}: {error})")
                    status = "fail"
                results.append((link, status))
                if status == "fatal":
                    log(f"الحساب {number}: توقف بسبب مشكلة دخول أو رصيد.")
                    break
    except Exception as error:
        log(f"الحساب {number}: تعذر تشغيل المتصفح ({type(error).__name__}: {error})")
        # سجّل الفشل للروابط التي لم يبدأ هذا الحساب بمعالجتها.
        results.extend((link, "fail") for link in links[len(results):])
    return results


def run_all_accounts(links, accounts, state_dir=None, max_links=None):
    """Submit each link from all configured accounts using parallel workers."""
    if not links:
        log("💤 لا توجد روابط للتنفيذ.")
        return 0
    selected_links = links if max_links is None else links[:max_links]
    log(f"🚀 إرسال {len(selected_links)} روابط من الحسابات الخمسة بالتوازي.")
    with ThreadPoolExecutor(max_workers=ACCOUNT_COUNT) as pool:
        futures = [pool.submit(_send_account, account, selected_links) for account in accounts]
        account_results = [future.result() for future in futures]

    failures = 0
    # لا نحذف من الطابور إلا الروابط التي بدأتها الحسابات الخمسة، لتبقى الروابط
    # التي لم يصل إليها أحد الحسابات بعد توقفه المفاجئ قابلة للاستكمال.
    completed_prefix = min((len(result) for result in account_results), default=0)
    for link in selected_links[:completed_prefix]:
        statuses = [dict(result).get(link, "fail") for result in account_results]
        if state_dir is not None:
            successes = load_successes(state_dir)
            attempts = load_failures(state_dir)
            for status in statuses:
                if status == "fatal":
                    failures += 1
                else:
                    _, result = record_result(link, status, state_dir, successes, attempts)
                    if result != "success":
                        failures += status != "ok"
        else:
            failures += sum(status != "ok" for status in statuses)
        log(f"انتهى الرابط {link}: {sum(status == 'ok' for status in statuses)}/5 نجاحات.")
    remove_from_queue(selected_links[:completed_prefix])
    # لروابط الروابط السابقة نسجل النتائج حتى تظل حدود النجاح/الفشل قائمة.
    for result in account_results:
        if len(result) < len(selected_links):
            failures += 1
    log(f"اكتمل التشغيل؛ عولجت {completed_prefix} روابط من الحسابات الخمسة.")
    return 1 if failures else 0


def run_browser_send(sender, links):
    """Compatibility helper used by focused queue-processing tests."""
    if not links:
        log("💤 لا توجد روابط للتنفيذ.")
        return 0
    failures = 0
    processed_count = 0
    for link in links:
        processed_count += 1
        try:
            status = sender(link)
        except Exception as error:
            log(f"⚠️ تعذر تنفيذ الرابط ({type(error).__name__}: {error})")
            status = "fail"
        if status != "ok":
            failures += 1
        if status == "fatal":
            break
    remove_from_queue(links[:processed_count])
    return 1 if failures else 0


def run_previous_links_until_five(accounts, state_dir=None):
    """Retry previous links, submitting each eligible link from all accounts."""
    state_dir = STATE_DIR if state_dir is None else state_dir
    processed_path = os.path.join(state_dir, "processed_links.txt")
    queue_path = os.path.join(state_dir, "telegram_links.txt")
    attempts = load_failures(state_dir)
    successes = load_successes(state_dir)
    all_links = list(dict.fromkeys(
        list(reversed(read_lines(processed_path)))
        + list(reversed(read_lines(queue_path)))
    ))
    eligible = [link for link in all_links
                if can_process(link, state_dir, successes, attempts)]
    if not eligible:
        log("💤 لا توجد روابط سابقة مؤهلة للدفع.")
        return 0
    links = eligible[:5]
    log(f"🔁 إعادة محاولة {len(links)} روابط سابقة بالحسابات الخمسة بالتوازي.")
    return run_all_accounts(links, accounts, state_dir=state_dir)


def main():
    try:
        accounts = load_accounts(os.environ)
    except ValueError as error:
        log(f"❌ تعذر تحميل الحسابات؛ تحقق من الأسرار: {error}")
        return 1
    log(f"🚀 تشغيل حصاد | القناة: @{TARGET_CHANNEL} | الحسابات الخمسة بالتوازي")
    links = []
    processed_before_pull = set(read_lines(PROCESSED_PATH))
    fetch_failed = False
    try:
        links = pull_once()
    except Exception as error:
        fetch_failed = True
        log(f"❌ تعذر سحب الروابط الجديدة ({type(error).__name__}: {error})")
    has_new_links = any(link not in processed_before_pull for link in links)
    if fetch_failed or not has_new_links:
        return run_previous_links_until_five(accounts)
    return run_all_accounts(links, accounts, state_dir=STATE_DIR)


if __name__ == "__main__":
    sys.exit(main())
