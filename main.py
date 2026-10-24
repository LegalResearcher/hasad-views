import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from browser_sender import BrowserSender

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)

LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")
LEGACY_DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")
PAIR_DONE_PATH = os.path.join(STATE_DIR, "account_link_done.json")
PAIR_ATTEMPTS_PATH = os.path.join(STATE_DIR, "account_link_attempts.json")
PREVIOUS_BATCH_PATH = os.path.join(STATE_DIR, "previous_batch_links.json")

TARGET_CHANNEL = "hasadalyoum"
ACCOUNT_COUNT = 11
MAX_ATTEMPTS_PER_PAIR = 3
MAX_LINKS_PER_BATCH = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def append_lines(path, lines):
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def save_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_previous_batches():
    if not os.path.exists(PREVIOUS_BATCH_PATH):
        return []
    try:
        with open(PREVIOUS_BATCH_PATH, "r", encoding="utf-8") as f:
            value = json.load(f)
        if not isinstance(value, list):
            return []
        # Migrate the previous flat-list format to a list of batches.
        if value and all(isinstance(link, str) for link in value):
            return [value]
        return [batch for batch in value if isinstance(batch, list)][:2]
    except (ValueError, OSError):
        return []


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


def fetch_links():
    """روابط المنشورات الظاهرة في القناة، من الأقدم إلى الأحدث."""
    url = f"https://t.me/s/{TARGET_CHANNEL}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"كود الحالة {response.status_code}")
    pattern = rf"https://t\.me/{TARGET_CHANNEL}/(\d+)"
    ids = sorted({int(n) for n in re.findall(pattern, response.text)})
    return [f"https://t.me/{TARGET_CHANNEL}/{n}" for n in ids]


def select_current_batch(discovered, processed, first_run=False):
    """اختر حتى خمسة روابط جديدة؛ عند أول تشغيل تجاهل المنشورات الأقدم الظاهرة."""
    new_links = [link for link in discovered if link not in processed]
    if first_run:
        return new_links[-MAX_LINKS_PER_BATCH:], set(new_links)
    batch = new_links[:MAX_LINKS_PER_BATCH]
    return batch, set(batch)


def pull_once():
    first_run = not os.path.exists(PROCESSED_PATH)
    discovered = fetch_links()
    if not discovered:
        log("لم يتم العثور على منشورات في الصفحة.")
        return []

    processed = set(read_lines(PROCESSED_PATH))
    current_batch, mark_processed = select_current_batch(
        discovered, processed, first_run
    )
    if not current_batch:
        log("لا توجد روابط جديدة.")
        return []

    append_lines(LINKS_PATH, current_batch)
    append_lines(PROCESSED_PATH, sorted(mark_processed))
    log(f"أُضيفت {len(current_batch)} روابط جديدة لهذه الدفعة.")
    return current_batch


def pair_key(account_number, link):
    return f"{account_number}|{link}"


def links_for_run(current_batch, previous_batches):
    # With new links, pair them with the most recent batch. With none,
    # repeat the two most recent batches until a new batch arrives.
    selected_previous = previous_batches[:1] if current_batch else previous_batches[:2]
    combined = list(dict.fromkeys(
        current_batch + [link for batch in selected_previous for link in batch]
    ))
    return combined[:2 * MAX_LINKS_PER_BATCH]


def process_account(account, links, pair_done, attempts, forced_links):
    number, username, password = account
    results = {}
    work = [
        link for link in links
        if link in forced_links
        or (
            pair_key(number, link) not in pair_done
            and attempts.get(pair_key(number, link), 0) < MAX_ATTEMPTS_PER_PAIR
        )
    ]
    if not work:
        return number, results

    try:
        with BrowserSender(username, password) as sender:
            for index, link in enumerate(work):
                log(f"الحساب {number}: معالجة {link}")
                status = sender(link)
                results[link] = status
                if status == "fatal":
                    for pending in work[index + 1:]:
                        results[pending] = "fatal"
                    break
    except Exception as error:
        log(f"الحساب {number}: تعذر تشغيل المتصفح ({type(error).__name__}: {error})")
        for link in work:
            results.setdefault(link, "fail")
    return number, results


def run_parallel(accounts, current_batch):
    pair_done = load_json(PAIR_DONE_PATH)
    attempts = load_json(PAIR_ATTEMPTS_PATH)
    previous_batches = load_previous_batches()
    links = links_for_run(current_batch, previous_batches)
    if not links:
        log("لا توجد روابط جديدة أو من الدفعة السابقة للتنفيذ.")
        return 0

    selected_previous = previous_batches[:1] if current_batch else previous_batches[:2]
    forced_links = {
        link for batch in selected_previous for link in batch
    }
    log(
        f"بدء {len(accounts)} حسابًا بالتوازي على {len(links)} روابط: "
        f"{len(current_batch)} جديدة + {len(forced_links & set(links))} من الدفعات السابقة "
        f"(حتى {len(accounts) * len(links)} طلبًا)."
    )
    results = {}
    with ThreadPoolExecutor(max_workers=ACCOUNT_COUNT) as executor:
        futures = [
            executor.submit(
                process_account, account, links, pair_done, attempts, forced_links
            )
            for account in accounts
        ]
        for future in as_completed(futures):
            number, account_results = future.result()
            results[number] = account_results

    for number in range(1, ACCOUNT_COUNT + 1):
        for link in links:
            status = results.get(number, {}).get(link)
            key = pair_key(number, link)
            if status == "ok":
                pair_done[key] = True
            elif status in ("fail", "fatal") and link not in forced_links:
                attempts[key] = attempts.get(key, 0) + 1
                if attempts[key] >= MAX_ATTEMPTS_PER_PAIR:
                    log(f"تم إيقاف إعادة الرابط {link} للحساب {number} بعد {MAX_ATTEMPTS_PER_PAIR} إخفاقات.")

    save_json(PAIR_DONE_PATH, pair_done)
    save_json(PAIR_ATTEMPTS_PATH, attempts)
    # Keep both recent batches while no fresh links arrive. A fresh batch
    # becomes newest and shifts the former newest batch into second place.
    updated_batches = (
        [current_batch] + previous_batches[:1]
        if current_batch else previous_batches[:2]
    )
    save_json(PREVIOUS_BATCH_PATH, updated_batches)

    successful_pairs = sum(
        1
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
        if results.get(number, {}).get(link) == "ok"
    )
    expected_pairs = ACCOUNT_COUNT * len(links)
    log(f"انتهى التشغيل: نجح {successful_pairs} من أصل {expected_pairs} أزواج حساب/رابط.")
    return 0 if successful_pairs == expected_pairs else 1


def main():
    try:
        accounts = load_accounts(os.environ)
    except ValueError as error:
        log(f"تعذر بدء التشغيل؛ تحقق من أسرار الحسابات الـ11: {error}")
        return 1

    log(f"بدء التشغيل المتوازي | القناة: @{TARGET_CHANNEL} | الحسابات: {ACCOUNT_COUNT}")
    try:
        current_batch = pull_once()
    except Exception as error:
        log(f"خطأ في سحب الروابط: {type(error).__name__}: {error}")
        return 1

    return run_parallel(accounts, current_batch)


if __name__ == "__main__":
    sys.exit(main())
