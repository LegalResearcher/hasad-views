import hashlib
import json
import os
import re
import sys
import tempfile
import time

import requests

from account_rotation import select_rotating_account
from browser_sender import BrowserSender

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)
LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")
BATCH_HISTORY_PATH = os.path.join(STATE_DIR, "previous_batch_links.json")
PREVIOUS_BATCH_FALLBACK_PATH = os.path.join(STATE_DIR, "previous_batch_fallback.json")
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


def save_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp_path = tempfile.mkstemp(prefix=".state-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def batch_fingerprint(batch):
    serialized = json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def fallback_already_processed(batch):
    if not os.path.exists(PREVIOUS_BATCH_FALLBACK_PATH):
        return False
    try:
        with open(PREVIOUS_BATCH_FALLBACK_PATH, "r", encoding="utf-8") as file:
            marker = json.load(file)
        return isinstance(marker, dict) and marker.get("batch_fingerprint") == batch_fingerprint(batch)
    except (OSError, ValueError):
        return False


def load_batch_history():
    if not os.path.exists(BATCH_HISTORY_PATH):
        return []
    try:
        with open(BATCH_HISTORY_PATH, "r", encoding="utf-8") as file:
            value = json.load(file)
        return [batch for batch in value if isinstance(batch, list)] if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


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


def pull_once(include_previous=True):
    discovered = fetch_links()
    processed = set(read_lines(PROCESSED_PATH))
    current_batch = [link for link in discovered if link not in processed]
    if not current_batch:
        pending = read_lines(LINKS_PATH)
        if pending:
            log(f"🔁 لا توجد روابط جديدة؛ استرجاع {len(pending)} روابط معلّقة من تشغيل سابق.")
            return pending
        if include_previous:
            history = load_batch_history()
            previous_batches = history[:2]
            previous_batch_links = list(dict.fromkeys(
                link for batch in previous_batches for link in batch
            ))
            if previous_batch_links and not fallback_already_processed(previous_batch_links):
                save_json(
                    PREVIOUS_BATCH_FALLBACK_PATH,
                    {"batch_fingerprint": batch_fingerprint(previous_batch_links)},
                )
                log(
                    f"🔁 لا توجد روابط جديدة؛ معالجة آخر دفعتين سابقتين مرة واحدة "
                    f"({len(previous_batch_links)} رابط)."
                )
                return previous_batch_links
        log("🔎 لا توجد روابط جديدة أو معلّقة أو دفعة سابقة متاحة لإعادة التشغيل.")
        return []

    history = load_batch_history()
    previous_batch = history[0] if history else []
    selected = list(dict.fromkeys(current_batch + (previous_batch if include_previous else [])))

    append_unique(LINKS_PATH, current_batch)
    write_lines(PROCESSED_PATH, list(dict.fromkeys(read_lines(PROCESSED_PATH) + current_batch)))
    save_json(BATCH_HISTORY_PATH, [current_batch] + history)
    log(
        f"🆕 الدفعة الجديدة: {len(current_batch)} رابط؛ "
        f"الدفعة السابقة المضافة: {len(previous_batch)} رابط؛ "
        f"الإجمالي لهذا التشغيل: {len(selected)} رابط."
    )
    return selected


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



def main():
    try:
        links = pull_once()
        if not links:
            return 0
        account_number, username, password = select_rotating_account(os.environ, STATE_DIR)
        log(
            f"🚀 تشغيل حصاد | القناة: @{TARGET_CHANNEL} | "
            f"الحساب رقم {account_number} من 9 | {len(links)} روابط"
        )
        with BrowserSender(username, password) as sender:
            return run_browser_send(sender, links)
    except (OSError, ValueError) as error:
        log(f"❌ تعذر اختيار الحساب؛ تحقق من الأسرار وحالة account_rotation.json: {error}")
        return 1
    except Exception as error:
        log(f"❌ خطأ في التشغيل: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
