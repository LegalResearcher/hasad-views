import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

# Keep tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import retry_link


class ParallelRetryTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            (number, f"user-{number}", f"password-{number}")
            for number in range(1, 31)
        ]
        self.target = "https://t.me/hasadalyoum/12345"
        self.environ = {"TARGET_LINK": self.target}
        for number in range(1, 31):
            self.environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            self.environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"

    def run_main_with_temp_state(self, temp, current_views, process_result=None):
        pair_done = os.path.join(temp, "account_link_done.json")
        done_links = os.path.join(temp, "done_links.txt")
        budget = os.path.join(temp, "view_budget.json")
        with patch.dict(os.environ, self.environ, clear=True), \
             patch.object(retry_link, "fetch_current_views", return_value=current_views) as fetch, \
             patch.object(retry_link, "PAIR_DONE_PATH", pair_done), \
             patch.object(retry_link, "DONE_PATH", done_links), \
             patch.object(retry_link, "LEGACY_SUCCESS_COUNTS_PATH", os.path.join(temp, "success_counts.json")), \
             patch.object(retry_link, "VIEW_BUDGET_PATH", budget), \
             patch.object(retry_link, "process_link", return_value=process_result or {}) as process:
            result = retry_link.main()
        return result, process, fetch

    def test_process_link_uses_all_scheduled_accounts_concurrently(self):
        barrier = threading.Barrier(30)
        calls = []
        calls_lock = threading.Lock()

        class BarrierSender:
            def __init__(self, username, password):
                self.username = username

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __call__(self, link, quantity=100):
                barrier.wait(timeout=10)
                with calls_lock:
                    calls.append((self.username, link, quantity))
                return "ok"

        scheduled = [(account, 25) for account in self.accounts]
        with patch.object(retry_link, "BrowserSender", BarrierSender):
            results = retry_link.process_link(scheduled, self.target)

        self.assertEqual(set(results), set(range(1, 31)))
        self.assertTrue(all(status == "ok" for status in results.values()))
        self.assertEqual(len(calls), 30)
        self.assertEqual({link for _, link, _ in calls}, {self.target})
        self.assertEqual({quantity for _, _, quantity in calls}, {25})
        self.assertEqual({username for username, _, _ in calls},
                         {f"user-{number}" for number in range(1, 31)})

    def test_manual_retry_does_not_read_counter_when_safeguards_are_disabled(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, self.environ, clear=True), \
             patch.object(retry_link, "fetch_current_views", side_effect=RuntimeError("unreadable")) as fetch, \
             patch.object(retry_link, "DONE_PATH", os.path.join(temp, "done_links.txt")), \
             patch.object(retry_link, "VIEW_BUDGET_PATH", os.path.join(temp, "view_budget.json")), \
             patch.object(retry_link, "process_link", return_value={n: "ok" for n in range(1, 31)}) as process:
            self.assertEqual(retry_link.main(), 0)
        fetch.assert_not_called()
        scheduled = process.call_args.args[0]
        self.assertEqual(len(scheduled), 30)
        self.assertEqual({quantity for _, quantity in scheduled}, {100})

    def test_manual_retry_ignores_old_total_ceiling_but_keeps_per_order_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            result, process, fetch = self.run_main_with_temp_state(
                temp, 50_000, process_result={n: "ok" for n in range(1, 31)}
            )
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        scheduled = process.call_args.args[0]
        self.assertEqual(len(scheduled), 30)
        self.assertEqual({quantity for _, quantity in scheduled}, {100})

    def test_manual_retry_uses_one_hundred_for_every_account_above_old_ceiling(self):
        with tempfile.TemporaryDirectory() as temp:
            result, process, fetch = self.run_main_with_temp_state(
                temp, 10_350, process_result={n: "ok" for n in range(1, 31)}
            )
        self.assertEqual(result, 0)
        fetch.assert_not_called()
        scheduled = process.call_args.args[0]
        self.assertEqual(len(scheduled), 30)
        self.assertEqual({quantity for _, quantity in scheduled}, {100})

    def test_manual_retry_requires_secrets_for_all_thirty_accounts(self):
        environ = {}
        for number in range(1, 30):
            environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"
        with self.assertRaisesRegex(ValueError, "SITE_USERNAME_30, SITE_PASSWORD_30"):
            retry_link.load_accounts(environ)


if __name__ == "__main__":
    unittest.main()
