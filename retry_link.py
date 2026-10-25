import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from browser_sender import BrowserSender
from view_budget import MAX_TOTAL_VIEWS, ViewBudget, fetch_current_views

ACCOUNT_COUNT = 16
LINK_PATTERN = re.compile(r"^https://t\.me/hasadalyoum/\d+$")
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")
VIEW_BUDGET_PATH = os.path.join(STATE_DIR, "view_budget.json")
PAIR_DONE_PATH = os.path.join(STATE_DIR, "account_link_done.json")
LEGACY_SUCCESS_COUNTS_PATH = os.path.join(STATE_DIR, "success_counts.json")


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


def process_account(account, link, quantity):
    number, username, password = account
    try:
        with BrowserSender(username, password) as sender:
            status = sender(link, quantity=quantity)
    except Exception as error:
        log(f"الحساب {number}: تعذر تشغيل الطلب ({type(error).__name__}: {error})")
        status = "fail"
    log(f"الحساب {number}: نتيجة الرابط {link}: {status}")
    return number, status


def process_link(scheduled, link):
    results = {}
    with ThreadPoolExecutor(max_workers=len(scheduled)) as executor:
        futures = [
            executor.submit(process_account, account, link, quantity)
            for account, quantity in scheduled
        ]
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

    try:
        current_views = fetch_current_views(link)
        budget = ViewBudget(
            VIEW_BUDGET_PATH,
            legacy_pair_done_path=PAIR_DONE_PATH,
            legacy_done_links_path=DONE_PATH,
            legacy_success_counts_path=LEGACY_SUCCESS_COUNTS_PATH,
        )
        scheduled = []
        for account in accounts:
            quantity = budget.reserve(link, current_views)
            if quantity:
                scheduled.append((account, quantity))
    except Exception as error:
        log(f"تعذر قراءة العداد أو حجز السقف؛ لن يُرسل الطلب: {type(error).__name__}: {error}")
        return 1

    if not scheduled:
        log(f"بلغ العداد والحجوزات سقف {MAX_TOTAL_VIEWS}؛ لم يُرسل أي طلب للرابط {link}.")
        return 0
    planned = ", ".join(f"{account[0]}:{quantity}" for account, quantity in scheduled)
    log(f"الحجز قبل الإرسال: {link} | {len(scheduled)} طلبات | الحساب:الكمية {planned}")
    results = process_link(scheduled, link)
    successful = sum(status == "ok" for status in results.values())
    log(f"انتهى إعادة الرابط: نجح {successful} من أصل {len(scheduled)} طلبات مجدولة.")

    # Preserve the previous global completion marker if at least one order was accepted.
    if successful:
        mark_done(link)
    return 0 if successful == len(scheduled) else 1


if __name__ == "__main__":
    sys.exit(main())
