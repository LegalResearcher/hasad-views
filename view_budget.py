import json
import math
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_UN, flock
from html.parser import HTMLParser

import requests

MAX_TOTAL_VIEWS = 10_400
MAX_ORDER_QUANTITY = 100
LEGACY_ACCOUNT_COUNT = 16
ACTIVE_ORDER_COOLDOWN_SECONDS = 60 * 60


class ActiveOrderDeferred(RuntimeError):
    """Raised when a post is still inside its persisted active-order cooldown."""

    def __init__(self, deferred_until):
        self.deferred_until = int(deferred_until)
        super().__init__(f"Active order cooldown until {self.deferred_until}")


class _TargetViewParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, target_post):
        super().__init__(convert_charrefs=True)
        self.target_post = target_post
        self.stack = []
        self.target_depth = None
        self.views_depth = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)
        depth = len(self.stack)
        if attrs.get("data-post") == self.target_post:
            self.target_depth = depth
        classes = set((attrs.get("class") or "").split())
        if (tag == "span" and "tgme_widget_message_views" in classes
                and self.target_depth is not None):
            self.views_depth = depth

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.views_depth is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        depth = len(self.stack)
        if tag == "span" and self.views_depth == depth:
            self.views_depth = None
        if tag == "div" and self.target_depth == depth:
            self.target_depth = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                del self.stack[index:]
                break


def parse_view_count(text):
    """Parse Telegram compact counts conservatively, rounding their upper bound up."""
    normalized = text.translate(str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"
    ))
    normalized = normalized.strip().replace("\u00a0", "").replace(" ", "")
    match = re.fullmatch(r"([0-9][0-9.,]*)([KkMm])?", normalized)
    if not match:
        return None
    number_text, suffix = match.groups()
    if suffix is None:
        try:
            return int(number_text.replace(",", "").replace(".", ""))
        except ValueError:
            return None
    number_text = number_text.replace(",", ".")
    if number_text.count(".") > 1:
        return None
    decimals = len(number_text.partition(".")[2])
    try:
        number = float(number_text)
    except ValueError:
        return None
    scale = 1_000 if suffix.lower() == "k" else 1_000_000
    quantum = max(1, scale // (10 ** decimals))
    return math.ceil(number * scale) + quantum


def fetch_current_views(link, channel="hasadalyoum", timeout=30):
    """Fetch the requested public Telegram post and its conservative view count."""
    match = re.fullmatch(rf"https://t\.me/{re.escape(channel)}/(\d+)", link)
    if not match:
        raise ValueError(f"Unsupported Telegram post link: {link}")
    post_id = match.group(1)
    target = f"{channel}/{post_id}"
    response = requests.get(
        f"https://t.me/s/{channel}/{post_id}",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram returned HTTP {response.status_code} for {link}")
    parser = _TargetViewParser(target)
    parser.feed(response.text)
    count = parse_view_count("".join(parser.parts))
    if count is None:
        raise RuntimeError(f"Could not read Telegram view count for {link}")
    return count


class ViewBudget:
    """Persist conservative per-post reservations across accounts and workflow runs."""

    def __init__(self, path, legacy_pair_done_path=None, legacy_done_links_path=None,
                 legacy_success_counts_path=None, max_total=MAX_TOTAL_VIEWS,
                 max_order=MAX_ORDER_QUANTITY,
                 active_order_cooldown=ACTIVE_ORDER_COOLDOWN_SECONDS):
        self.path = path
        self.lock_path = path + ".lock"
        self.legacy_pair_done_path = legacy_pair_done_path
        self.legacy_done_links_path = legacy_done_links_path
        self.legacy_success_counts_path = legacy_success_counts_path
        self.max_total = max_total
        self.max_order = max_order
        if (not isinstance(active_order_cooldown, int)
                or isinstance(active_order_cooldown, bool)
                or active_order_cooldown <= 0):
            raise ValueError("active_order_cooldown must be a positive integer")
        self.active_order_cooldown = active_order_cooldown
        self.lock = threading.Lock()

    @contextmanager
    def _exclusive_lock(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self.lock:
            lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                flock(lock_fd, LOCK_EX)
                yield
            finally:
                flock(lock_fd, LOCK_UN)
                os.close(lock_fd)

    def _read(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                state = json.load(file)
        except (ValueError, OSError) as error:
            raise ValueError("view_budget.json is unreadable; refusing to reserve") from error
        if not isinstance(state, dict):
            raise ValueError("view_budget.json must contain an object")
        return state

    def _legacy_reservation(self, link):
        """Convert old successful-order records to conservative 100-view reservations."""
        order_count = 0
        if self.legacy_pair_done_path and os.path.exists(self.legacy_pair_done_path):
            try:
                with open(self.legacy_pair_done_path, "r", encoding="utf-8") as file:
                    pairs = json.load(file)
                if not isinstance(pairs, dict):
                    return self.max_total
                suffix = "|" + link
                order_count = len({
                    key.split("|", 1)[0]
                    for key, succeeded in pairs.items()
                    if isinstance(key, str) and key.endswith(suffix) and succeeded is True
                })
            except (ValueError, OSError):
                return self.max_total

        if self.legacy_success_counts_path and os.path.exists(self.legacy_success_counts_path):
            try:
                with open(self.legacy_success_counts_path, "r", encoding="utf-8") as file:
                    counts = json.load(file)
                if not isinstance(counts, dict):
                    return self.max_total
                legacy_count = counts.get(link, 0)
                if (not isinstance(legacy_count, int) or isinstance(legacy_count, bool)
                        or legacy_count < 0):
                    return self.max_total
                order_count = max(order_count, legacy_count)
            except (ValueError, OSError):
                return self.max_total

        if (order_count == 0 and self.legacy_done_links_path
                and os.path.exists(self.legacy_done_links_path)):
            try:
                with open(self.legacy_done_links_path, "r", encoding="utf-8") as file:
                    if link in {line.strip() for line in file if line.strip()}:
                        # The old global marker does not say how many accounts
                        # succeeded, so reserve the full legacy run conservatively.
                        order_count = LEGACY_ACCOUNT_COUNT
            except OSError:
                return self.max_total

        return min(self.max_total, order_count * self.max_order)

    def _save(self, state):
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, temp_path = tempfile.mkstemp(prefix=".view-budget-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def reserve(self, link, current_views, now=None):
        """Reserve one order or raise ActiveOrderDeferred during an active cooldown."""
        if (not isinstance(current_views, int) or isinstance(current_views, bool)
                or current_views < 0):
            raise ValueError("A valid current view count is required")
        now = int(time.time()) if now is None else int(now)
        with self._exclusive_lock():
            state = self._read()
            entry = state.get(link)
            if entry is None:
                entry = {
                    "baseline": current_views,
                    "max_observed": current_views,
                    "reserved": self._legacy_reservation(link),
                }
            if not isinstance(entry, dict):
                raise ValueError(f"Invalid view budget for {link}")
            baseline = entry.get("baseline")
            max_observed = entry.get("max_observed")
            reserved = entry.get("reserved")
            counters = (baseline, max_observed, reserved)
            if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                   for value in counters):
                raise ValueError(f"Invalid view budget counters for {link}")

            max_observed = max(max_observed, current_views)
            entry["max_observed"] = max_observed
            deferred_until = entry.get("active_order_until")
            if deferred_until is not None:
                if (not isinstance(deferred_until, int)
                        or isinstance(deferred_until, bool) or deferred_until < 0):
                    raise ValueError(f"Invalid active-order deferral for {link}")
                if deferred_until > now:
                    state[link] = entry
                    self._save(state)
                    raise ActiveOrderDeferred(deferred_until)
                entry.pop("active_order_until", None)

            remaining = max(0, self.max_total - max_observed - reserved)
            quantity = min(self.max_order, remaining)
            if quantity:
                entry["reserved"] = reserved + quantity
            state[link] = entry
            self._save(state)
            return quantity

    def record_active_order(self, link, account_number=None, now=None):
        """Persist a one-hour link-level pause without releasing the reserved amount."""
        now = int(time.time()) if now is None else int(now)
        with self._exclusive_lock():
            state = self._read()
            entry = state.get(link)
            if not isinstance(entry, dict):
                raise ValueError(f"No budget reservation exists for active order {link}")
            deferred_until = entry.get("active_order_until", 0)
            if (not isinstance(deferred_until, int) or isinstance(deferred_until, bool)
                    or deferred_until < 0):
                raise ValueError(f"Invalid active-order deferral for {link}")
            count = entry.get("active_order_count", 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(f"Invalid active-order count for {link}")
            entry["last_active_order_at"] = now
            entry["active_order_until"] = max(
                deferred_until, now + self.active_order_cooldown
            )
            entry["active_order_count"] = count + 1
            if account_number is not None:
                if (not isinstance(account_number, int)
                        or isinstance(account_number, bool) or account_number < 1):
                    raise ValueError("account_number must be a positive integer")
                entry["last_active_order_account"] = account_number
            state[link] = entry
            self._save(state)
            return entry["active_order_until"]


def fetch_view_counts(links, channel="hasadalyoum", max_workers=8):
    """Fetch view counts concurrently; unknown counts remain fail-closed."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not links:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(links))) as executor:
        futures = {
            executor.submit(fetch_current_views, link, channel): link
            for link in links
        }
        for future in as_completed(futures):
            link = futures[future]
            try:
                results[link] = future.result()
            except Exception as error:
                print(f"[views] تعذر قراءة مشاهدات {link}: {type(error).__name__}: {error}", flush=True)
                results[link] = None
    return results
