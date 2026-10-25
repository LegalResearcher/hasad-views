import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Keep the tests independent of a Playwright installation or external site.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import main
import retry_link
from provider_response import classify_provider_message
from view_budget import ActiveOrderDeferred, ViewBudget


class ActiveOrderClassificationTests(unittest.TestCase):
    def test_classifies_arabic_and_english_active_order_messages(self):
        arabic = "× لديك طلب نشط مع هذا الرابط. يرجى الانتظار حتى يتم الانتهاء من الطلب."
        english = "You have an active order with this link; please wait."
        self.assertEqual(classify_provider_message(arabic), "active_order")
        self.assertEqual(classify_provider_message(english), "active_order")
        self.assertIsNone(classify_provider_message("Invalid link"))


class ActiveOrderBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "view_budget.json")
        self.link = "https://t.me/hasadalyoum/12345"

    def tearDown(self):
        self.temp.cleanup()

    def read_state(self):
        with open(self.path, encoding="utf-8") as file:
            return json.load(file)

    def test_active_order_keeps_reservation_and_skips_until_cooldown_expires(self):
        budget = ViewBudget(self.path, active_order_cooldown=60)
        self.assertEqual(budget.reserve(self.link, 1_000, now=1_000), 100)
        until = budget.record_active_order(
            self.link, account_number=3, now=1_010
        )
        self.assertEqual(until, 1_070)

        with self.assertRaises(ActiveOrderDeferred) as raised:
            budget.reserve(self.link, 1_100, now=1_020)
        self.assertEqual(raised.exception.deferred_until, 1_070)

        state = self.read_state()[self.link]
        self.assertEqual(state["reserved"], 100)
        self.assertEqual(state["max_observed"], 1_100)
        self.assertEqual(state["active_order_count"], 1)
        self.assertEqual(state["last_active_order_account"], 3)

        self.assertEqual(budget.reserve(self.link, 1_100, now=1_070), 100)
        state = self.read_state()[self.link]
        self.assertEqual(state["reserved"], 200)
        self.assertNotIn("active_order_until", state)

    def test_main_records_deferred_status_without_success_or_attempt_marker(self):
        budget = ViewBudget(self.path, active_order_cooldown=60)
        link = self.link
        pair_done = {}
        attempts = {}

        class ActiveOrderSender:
            def __init__(self, username, password):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __call__(self, link, quantity=100):
                return "active_order"

        with patch.object(main, "BrowserSender", ActiveOrderSender), \
             patch.object(main, "log"):
            number, results = main.process_account(
                (1, "user-1", "password-1"), [link], pair_done, attempts,
                {link}, {link: 1_000}, budget,
            )

        self.assertEqual(number, 1)
        self.assertEqual(results[link], "active_order")
        self.assertEqual(pair_done, {})
        self.assertEqual(attempts, {})
        self.assertEqual(self.read_state()[link]["reserved"], 100)
        self.assertIn("active_order_until", self.read_state()[link])

    def test_main_skips_a_persisted_active_order_without_reserving_again(self):
        budget = ViewBudget(self.path, active_order_cooldown=3_600)
        budget.reserve(self.link, 1_000)
        budget.record_active_order(self.link)
        calls = []

        class RecordingSender:
            def __init__(self, username, password):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __call__(self, link, quantity=100):
                calls.append((link, quantity))
                return "ok"

        with patch.object(main, "BrowserSender", RecordingSender), \
             patch.object(main, "log"):
            _, results = main.process_account(
                (1, "user-1", "password-1"), [self.link], {}, {},
                {self.link}, {self.link: 1_100}, budget,
            )

        self.assertEqual(results[self.link], "active_order_wait")
        self.assertEqual(calls, [])
        self.assertEqual(self.read_state()[self.link]["reserved"], 100)

    def test_manual_retry_skips_persisted_active_order(self):
        budget = ViewBudget(self.path)
        budget.reserve(self.link, 1_000)
        budget.record_active_order(self.link)
        environ = {"TARGET_LINK": self.link}
        for number in range(1, 31):
            environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"

        with patch.dict(os.environ, environ, clear=True), \
             patch.object(retry_link, "VIEW_BUDGET_PATH", self.path), \
             patch.object(retry_link, "PAIR_DONE_PATH", os.path.join(self.temp.name, "pairs.json")), \
             patch.object(retry_link, "DONE_PATH", os.path.join(self.temp.name, "done.txt")), \
             patch.object(retry_link, "LEGACY_SUCCESS_COUNTS_PATH", os.path.join(self.temp.name, "counts.json")), \
             patch.object(retry_link, "fetch_current_views", return_value=1_100), \
             patch.object(retry_link, "process_link") as process, \
             patch.object(retry_link, "log"):
            result = retry_link.main()

        self.assertEqual(result, 0)
        process.assert_not_called()
        self.assertEqual(self.read_state()[self.link]["reserved"], 100)

    def test_manual_retry_does_not_mark_partial_active_order_as_done(self):
        environ = {"TARGET_LINK": self.link}
        for number in range(1, 31):
            environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"

        with patch.dict(os.environ, environ, clear=True), \
             patch.object(retry_link, "PAIR_DONE_PATH", os.path.join(self.temp.name, "pairs.json")), \
             patch.object(retry_link, "DONE_PATH", os.path.join(self.temp.name, "done.txt")), \
             patch.object(retry_link, "LEGACY_SUCCESS_COUNTS_PATH", os.path.join(self.temp.name, "counts.json")), \
             patch.object(retry_link, "VIEW_BUDGET_PATH", self.path), \
             patch.object(retry_link, "fetch_current_views", return_value=10_150), \
             patch.object(retry_link, "process_link", return_value={
                 1: "ok", 2: "active_order", 3: "ok"
             }), \
             patch.object(retry_link, "log"):
            result = retry_link.main()

        self.assertEqual(result, 0)
        self.assertFalse(os.path.exists(os.path.join(self.temp.name, "done.txt")))
        entry = self.read_state()[self.link]
        self.assertEqual(entry["reserved"], 250)
        self.assertIn("active_order_until", entry)


class ActiveOrderWorkflowOutcomeTests(unittest.TestCase):
    def test_active_order_alone_does_not_fail_main_workflow(self):
        link = "https://t.me/hasadalyoum/12345"
        results = {1: {link: "ok"}, 2: {link: "active_order"}}
        with patch.object(main, "log"):
            self.assertEqual(main.summarize_run_results(results, [link]), 0)

    def test_unreadable_counter_is_reported_separately_and_does_not_fail(self):
        link = "https://t.me/hasadalyoum/12345"
        results = {1: {link: "ok"}}
        results.update({number: {link: "view_unknown"} for number in range(2, 31)})

        with patch.object(main, "log") as mocked_log:
            self.assertEqual(main.summarize_run_results(results, [link]), 0)

        messages = " ".join(call.args[0] for call in mocked_log.call_args_list)
        self.assertIn("نجح 1 من أصل 1 أزواج حساب/رابط", messages)
        self.assertIn(
            "تخطّي بسبب تعذر قراءة العداد: 29 زوج حساب/رابط؛ عدد الروابط المتأثرة 1",
            messages,
        )

    def test_active_order_alone_does_not_fail_manual_retry(self):
        with patch.object(retry_link, "log"):
            self.assertEqual(
                retry_link.summarize_retry_results({1: "active_order"}, 1), 0
            )

    def test_other_failures_still_fail_the_workflow(self):
        link = "https://t.me/hasadalyoum/12345"
        main_results = {
            1: {link: "ok"},
            2: {link: "active_order"},
            3: {link: "fail"},
            4: {link: "view_unknown"},
        }
        with patch.object(main, "log"):
            self.assertEqual(main.summarize_run_results(main_results, [link]), 1)
        with patch.object(retry_link, "log"):
            self.assertEqual(
                retry_link.summarize_retry_results(
                    {1: "active_order", 2: "fail"}, 2
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
