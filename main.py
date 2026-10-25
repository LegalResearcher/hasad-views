import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from browser_sender import BrowserSender
from view_budget import (
    ActiveOrderDeferred,
    MAX_TOTAL_VIEWS,
    ViewBudget,
    fetch_view_counts,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
os.makedirs(STATE_DIR, exist_ok=True)

LINKS_PATH = os.path.join(STATE_DIR, "telegram_links.txt")
PROCESSED_PATH = os.path.join(STATE_DIR, "processed_links.txt")
LEGACY_DONE_PATH = os.path.join(STATE_DIR, "done_links.txt")
PAIR_DONE_PATH = os.path.join(STATE_DIR, "account_link_done.json")
PAIR_ATTEMPTS_PATH = os.path.join(STATE_DIR, "account_link_attempts.json")
PREVIOUS_BATCH_PATH = os.path.join(STATE_DIR, "previous_batch_links.json")
NO_NEW_BATCH_CURSOR_PATH = os.path.join(STATE_DIR, "no_new_batch_cursor.json")
VIEW_BUDGET_PATH = os.path.join(STATE_DIR, "view_budget.json")

TARGET_CHANNEL = "hasadalyoum"
ACCOUNT_COUNT = 30
MAX_ATTEMPTS_PER_PAIR = 3
MIN_REPEATED_LINKS_WITHOUT_NEW = 15

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
        return [batch for batch in value if isinstance(batch, list)]
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


def select_current_batch(discovered, processed):
    """اختر كل الروابط الجديدة المكتشفة في هذا التشغيل."""
    new_links = [link for link in discovered if link not in processed]
    return new_links, set(new_links)


def pull_once():
    discovered = fetch_links()
    if not discovered:
        log("لم يتم العثور على منشورات في الصفحة.")
        return []

    processed = set(read_lines(PROCESSED_PATH))
    current_batch, mark_processed = select_current_batch(discovered, processed)
    if not current_batch:
        log("لا توجد روابط جديدة.")
        return []

    append_lines(LINKS_PATH, current_batch)
    append_lines(PROCESSED_PATH, sorted(mark_processed))
    log(f"أُضيفت {len(current_batch)} روابط جديدة لهذه الدفعة.")
    return current_batch


def pair_key(account_number, link):
    return f"{account_number}|{link}"


def deferred_until_text(timestamp):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(timestamp))


def summarize_run_results(results, links):
    active_statuses = {"active_order", "active_order_wait"}
    successful = sum(
        results.get(number, {}).get(link) == "ok"
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
    )
    deferred = sum(
        results.get(number, {}).get(link) in active_statuses
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
    )
    deferred_links = {
        link
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
        if results.get(number, {}).get(link) in active_statuses
    }
    unreadable = sum(
        results.get(number, {}).get(link) == "view_unknown"
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
    )
    unreadable_links = {
        link
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
        if results.get(number, {}).get(link) == "view_unknown"
    }
    expected = sum(
        results.get(number, {}).get(link)
        not in (None, "capped", "active_order", "active_order_wait", "view_unknown")
        for number in range(1, ACCOUNT_COUNT + 1)
        for link in links
    )
    log(
        f"انتهى التشغيل: نجح {successful} من أصل {expected} أزواج حساب/رابط؛ "
        f"مؤجل بسبب طلب نشط {deferred} زوجًا عبر {len(deferred_links)} روابط؛ "
        f"تخطّي بسبب تعذر قراءة العداد: {unreadable} زوج حساب/رابط؛ "
        f"عدد الروابط المتأثرة {len(unreadable_links)}."
    )
    return 0 if successful == expected else 1


def no_new_group_count(previous_batches):
    older_batch_count = max(0, len(previous_batches) - 1)
    return max(1, (older_batch_count + 1) // 2)


def next_no_new_group_index(previous_batches, group_index):
    return (group_index + 1) % no_new_group_count(previous_batches)


def select_no_new_batches(previous_batches, group_index=0):
    """Keep the newest batch fixed and rotate through older batches in pairs."""
    if not previous_batches:
        return []
    if len(previous_batches) == 1:
        return previous_batches[:1]

    group_count = no_new_group_count(previous_batches)
    group_index = group_index % group_count
    start = 1 + (group_index * 2)
    return [previous_batches[0]] + previous_batches[start:start + 2]


def current_no_new_group_index(previous_batches):
    state = load_json(NO_NEW_BATCH_CURSOR_PATH)
    index = state.get("next_group", 0)
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        index = 0
    return index % no_new_group_count(previous_batches)


def links_for_run(current_batch, previous_batches, archived_links=(), group_index=0):
    # New links pair with the two latest batches. With no new links, keep the
    # latest batch fixed and rotate pairs of older batches; use legacy archive
    # links only to meet the minimum of 15.
    selected_previous = (
        previous_batches[:2]
        if current_batch
        else select_no_new_batches(previous_batches, group_index)
    )
    combined = list(dict.fromkeys(
        current_batch + [link for batch in selected_previous for link in batch]
    ))
    if not current_batch:
        known_batch_links = {
            link for batch in previous_batches for link in batch
        }
        legacy_archive = [
            link for link in archived_links if link not in known_batch_links
        ]
        for link in reversed(legacy_archive):
            if link not in combined:
                combined.append(link)
            if len(combined) >= MIN_REPEATED_LINKS_WITHOUT_NEW:
                break
    return combined


def process_account(account, links, pair_done, attempts, forced_links,
                    current_views, view_budget):
    number, username, password = account
    results = {}
    eligible = [
        link for link in links
        if link in forced_links
        or (
            pair_key(number, link) not in pair_done
            and attempts.get(pair_key(number, link), 0) < MAX_ATTEMPTS_PER_PAIR
        )
    ]
    work = []
    for link in eligible:
        if current_views.get(link) is None:
            results[link] = "view_unknown"
            log(f"الحساب {number}: تخطي {link}؛ تعذر قراءة العداد.")
        else:
            work.append(link)
    if not work:
        return number, results

    try:
        with BrowserSender(username, password) as sender:
            for index, link in enumerate(work):
                current_count = current_views[link]
                try:
                    quantity = view_budget.reserve(link, current_count)
                except ActiveOrderDeferred as error:
                    results[link] = "active_order_wait"
                    log(
                        f"الحساب {number}: تخطي {link}؛ يوجد طلب نشط، "
                        f"ويعاد النظر بعد {deferred_until_text(error.deferred_until)}."
                    )
                    continue
                except Exception as error:
                    results[link] = "budget_error"
                    log(f"الحساب {number}: تعذر حجز ميزانية {link}: {type(error).__name__}: {error}")
                    continue
                if quantity <= 0:
                    results[link] = "capped"
                    log(f"الحساب {number}: تخطي {link}؛ بلغ العداد والحجوزات سقف {MAX_TOTAL_VIEWS}.")
                    continue
                log(f"الحساب {number}: معالجة {link}")
                status = sender(link, quantity=quantity)
                if status == "active_order":
                    try:
                        deferred_until = view_budget.record_active_order(
                            link, account_number=number
                        )
                    except Exception as error:
                        results[link] = "budget_error"
                        log(
                            f"الحساب {number}: تعذر حفظ تأجيل الطلب النشط لـ{link}: "
                            f"{type(error).__name__}: {error}"
                        )
                    else:
                        results[link] = "active_order"
                        log(
                            f"الحساب {number}: أُجّل {link} بسبب طلب نشط حتى "
                            f"{deferred_until_text(deferred_until)}."
                        )
                    continue
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
    archive = read_lines(LINKS_PATH)
    group_index = current_no_new_group_index(previous_batches)
    links = links_for_run(current_batch, previous_batches, archive, group_index)
    if not links:
        log("لا توجد روابط جديدة أو من الدفعة السابقة للتنفيذ.")
        return 0

    current_views = fetch_view_counts(links, TARGET_CHANNEL)
    view_budget = ViewBudget(
        VIEW_BUDGET_PATH,
        legacy_pair_done_path=PAIR_DONE_PATH,
        legacy_done_links_path=LEGACY_DONE_PATH,
        legacy_success_counts_path=os.path.join(STATE_DIR, "success_counts.json"),
    )
    readable = sum(count is not None for count in current_views.values())
    log(f"قُرئ عداد المشاهدات لـ{readable} من {len(links)} روابط؛ غير المقروءة لن تُرسل.")

    selected_previous = (
        previous_batches[:2]
        if current_batch
        else select_no_new_batches(previous_batches, group_index)
    )
    forced_links = (
        {link for batch in selected_previous for link in batch}
        if current_batch else set(links)
    )
    if not current_batch and len(links) < MIN_REPEATED_LINKS_WITHOUT_NEW:
        log(f"تنبيه: الأرشيف يحتوي على {len(links)} روابط سابقة فقط؛ ستُعالج كلها.")
    log(
        f"بدء {len(accounts)} حسابًا بالتوازي على {len(links)} روابط: "
        f"{len(current_batch)} جديدة + {len(forced_links & set(links))} من الدفعات السابقة "
        f"(حتى {len(accounts) * len(links)} طلبًا)."
    )
    results = {}
    with ThreadPoolExecutor(max_workers=ACCOUNT_COUNT) as executor:
        futures = [
            executor.submit(
                process_account, account, links, pair_done, attempts, forced_links,
                current_views, view_budget,
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
    # Keep every verifiable batch so no-new runs can rotate through history.
    updated_batches = [current_batch] + previous_batches if current_batch else previous_batches
    save_json(PREVIOUS_BATCH_PATH, updated_batches)
    next_group = (
        0
        if current_batch else next_no_new_group_index(previous_batches, group_index)
    )
    save_json(NO_NEW_BATCH_CURSOR_PATH, {"next_group": next_group})

    return summarize_run_results(results, links)


def main():
    try:
        accounts = load_accounts(os.environ)
    except ValueError as error:
        log(f"تعذر بدء التشغيل؛ تحقق من أسرار الحسابات الـ{ACCOUNT_COUNT}: {error}")
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
