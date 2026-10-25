import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from browser_sender import BrowserSender

ACCOUNT_COUNT = 16
LINK_PATTERN = re.compile(r"^https://t\.me/hasadalyoum/\d+$")
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")


def log(message):
    print(message, flush=True)


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


def mark_done(link):
    os.makedirs(os.path.dirname(DONE_PATH), exist_ok=True)
    existing = []
    if os.path.exists(DONE_PATH):
        with open(DONE_PATH, "r", encoding="utf-8") as file:
            existing = [line.strip() for line in file if line.strip()]
    if link not in existing:
        with open(DONE_PATH, "a", encoding="utf-8") as file:
            file.write(link + "\n")


def process_account(account, link):
    number, username, password = account
    try:
        with BrowserSender(username, password) as sender:
            status = sender(link)
    except Exception as error:
        log(f"الحساب {number}: تعذر تشغيل الطلب ({type(error).__name__}: {error})")
        status = "fail"
    log(f"الحساب {number}: نتيجة الرابط {link}: {status}")
    return number, status


def process_link(accounts, link):
    results = {}
    with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
        futures = [executor.submit(process_account, account, link) for account in accounts]
        for future in as_completed(futures):
            number, status = future.result()
            results[number] = status
    return results


def main():
    link = os.environ.get("TARGET_LINK", "").strip()
    if not LINK_PATTERN.fullmatch(link):
        log("الرابط غير صالح؛ يلزم رابط منشور من قناة hasadalyoum.")
        return 2

    try:
        accounts = load_accounts(os.environ)
    except ValueError as error:
        log(f"تعذر تحميل أسرار الحسابات الـ{ACCOUNT_COUNT}: {error}")
        return 1

    log(f"بدء إعادة الرابط المحدد عبر {len(accounts)} حسابًا بالتوازي (حتى {len(accounts)} طلبًا): {link}")
    results = process_link(accounts, link)
    successful = sum(status == "ok" for status in results.values())
    log(f"انتهى إعادة الرابط: نجح {successful} من أصل {len(accounts)} حسابًا.")

    # Preserve the previous global completion marker if at least one order was accepted.
    if successful:
        mark_done(link)
    return 0 if successful == len(accounts) else 1


if __name__ == "__main__":
    sys.exit(main())
