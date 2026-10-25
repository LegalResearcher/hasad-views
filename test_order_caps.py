import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Keep tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import main
from view_budget import VIEW_SAFEGUARDS_ENABLED, ViewBudget


class MainWorkflowBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.budget = ViewBudget(os.path.join(self.temp.name, "view_budget.json"))
        self.link = "https://t.me/hasadalyoum/12345"
        self.account = (1, "user-1", "password-1")
        self.calls = []

        calls = self.calls

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

        self.sender = RecordingSender

    def tearDown(self):
        self.temp.cleanup()

    def run_account(self, current_count):
        return main.process_account(
            self.account,
            [self.link],
            pair_done={},
            attempts={},
            forced_links={self.link},
            current_views={self.link: current_count},
            view_budget=self.budget,
        )

    def test_unreadable_count_does_not_call_sender(self):
        with patch.object(main, "BrowserSender", self.sender):
            number, results = self.run_account(None)
        self.assertEqual(number, 1)
        self.assertEqual(results[self.link], "view_unknown")
        self.assertEqual(self.calls, [])

    def test_at_cap_does_not_call_sender(self):
        with patch.object(main, "BrowserSender", self.sender):
            _, results = self.run_account(10_400)
        self.assertEqual(results[self.link], "capped")
        self.assertEqual(self.calls, [])

    def test_sender_receives_only_the_remaining_quantity(self):
        with patch.object(main, "BrowserSender", self.sender):
            _, results = self.run_account(10_350)
        self.assertEqual(results[self.link], "ok")
        self.assertEqual(self.calls, [(self.link, 50)])

    def test_disabled_safeguards_send_one_hundred_without_counter_or_reservation(self):
        self.assertFalse(VIEW_SAFEGUARDS_ENABLED)
        with patch.object(main, "BrowserSender", self.sender), patch.object(main, "log"):
            _, results = main.process_account(
                self.account, [self.link], {}, {}, {self.link}, {}, None
            )
        self.assertEqual(results[self.link], "ok")
        self.assertEqual(self.calls, [(self.link, 100)])
        self.assertFalse(os.path.exists(self.budget.path))


class AccountCountTests(unittest.TestCase):
    def test_main_default_is_thirty_accounts(self):
        self.assertEqual(main.ACCOUNT_COUNT, 30)


if __name__ == "__main__":
    unittest.main()
