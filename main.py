import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from browser_sender import BrowserSender

# ══════════════ الإعدادات ══════════════
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)

LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")
LEGACY_DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")
PAIR_DONE_PATH = os.path.join(STATE_DIR, "account_link_done.json")
PAIR_ATTEMPTS_PATH = os.path.join(STATE_DIR, "account_link_attempts.json")

TARGET_CHANNEL = "hasadalyoum"
ACCOUNT_COUNT = 11
MAX_ATTEMPTS_PER_PAIR = 3
MAX_LINKS_PER_RUN = 5

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
    """روابط المنشورات الظاهرة في القناة، من الأقدم للأحدث."""
    url = f"https://t.me/s/{TARGET_CHANNEL}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"كود الحالة {response.status_code}")
    pattern = rf"https://t\.me/{TARGET_CHANNEL}/(\d+)"
    ids = sorted({int(n) for n in re.findall(pattern, response.text)})
    return [f"https://t.me/{TARGET_CHANNEL}/{n}" for n in ids]


def pull_once():
    first_run = not os.path.exists(PROCESSED_PATH)
    discovered = fetch_links()
    if not discovered:
        log("لم يتم العثور على منشورات في الصفحة.")
        return

    processed = set(read_lines(PROCESSED_PATH))
    new_links = [link for link in discovered if link not in processed]
    if not new_links:
        log("لا توجد منشورات جديدة.")
        return

    if first_run:
        append_lines(LINKS_PATH, [new_links[-1]])
        append_lines(PROCESSED_PATH, new_links)
        log(f"أول تشغيل: أُضيف أحدث رابط فقط: {new_links[-1]}")
        return

    append_lines(LINKS_PATH, new_links)
    append_lines(PROCESSED_PATH, new_links)
    log(f"أُضيف {len(new_links)} رابط جديد إلى الطابور.")


def pair_key(account_number, link):
    return f"{account_number}|{link}"


def links_for_run(pair_done, attempts):
    legacy_done = set(read_lines(LEGACY_DONE_PATH))
    eligible = []
    for link in read_lines(LINKS_PATH):
        if link in legacy_done or link in eligible:
            continue
        # لا نبدأ رابطًا جديدًا إلا إذا بقيت له محاولة متاحة لحساب واحد على الأقل.
        unfinished = any(
            pair_key(number, link) not in pair_done
            and attempts.get(pair_key(number, link), 0) < MAX_ATTEMPTS_PER_PAIR
            for number in range(1, ACCOUNT_COUNT + 1)
        )
        if unfinished:
            eligible.append(link)
        if len(eligible) >= MAX_LINKS_PER_RUN:
            break
    return eligible


def process_account(account, links, pair_done, attempts):
    number, username, password = account
    results = {}
    work = [
        link for link in links
        if pair_key(number, link) not in pair_done
        and attempts.get(pair_key(number, link), 0) < MAX_ATTEMPTS_PER_PAIR
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


def run_parallel(accounts):
    pair_done = load_json(PAIR_DONE_PATH)
    attempts = load_json(PAIR_ATTEMPTS_PATH)
    links = links_for_run(pair_done, attempts)
    if not links:
        log("لا توجد روابط معلقة للتنفيذ.")
        return 0

    log(f"بدء {len(accounts)} حسابًا بالتوازي على الروابط نفسها: {len(links)} رابطًا (حتى {len(accounts) * len(links)} طلبًا).")
    results = {}
    with ThreadPoolExecutor(max_workers=ACCOUNT_COUNT) as executor:
        futures = [executor.submit(process_account, account, links, pair_done, attempts)
                   for account in accounts]
        for future in as_completed(futures):
            number, account_results = future.result()
            results[number] = account_results

    for number in range(1, ACCOUNT_COUNT + 1):
        for link in links:
            status = results.get(number, {}).get(link)
            if status == "ok":
                pair_done[pair_key(number, link)] = True
            elif status in ("fail", "fatal"):
                key = pair_key(number, link)
                attempts[key] = attempts.get(key, 0) + 1
                if attempts[key] >= MAX_ATTEMPTS_PER_PAIR:
                    log(f"تم إيقاف إعادة الرابط {link} للحساب {number} بعد {MAX_ATTEMPTS_PER_PAIR} إخفاقات.")

    save_json(PAIR_DONE_PATH, pair_done)
    save_json(PAIR_ATTEMPTS_PATH, attempts)

    completed = 0
    for link in links:
        if all(pair_key(number, link) in pair_done for number in range(1, ACCOUNT_COUNT + 1)):
            completed += 1
    log(f"انتهى التشغيل: اكتملت جميع الحسابات لـ {completed} من أصل {len(links)} روابط.")
    if completed == len(links):
        return 0
    return 1


def main():
    try:
        accounts = load_accounts(os.environ)
    except ValueError as error:
        log(f"تعذر بدء التشغيل؛ تحقق من أسرار الحسابات الـ11: {error}")
        return 1

    log(f"بدء التشغيل المتوازي | القناة: @{TARGET_CHANNEL} | الحسابات: {ACCOUNT_COUNT}")
    try:
        pull_once()
    except Exception as error:
        log(f"خطأ في سحب الروابط: {type(error).__name__}: {error}")

    return run_parallel(accounts)


if __name__ == "__main__":
    sys.exit(main())
