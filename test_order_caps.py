import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Keep tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import main
from view_budget import ViewBudget


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


class AccountCountOptionTests(unittest.TestCase):
    def test_default_and_full_run_use_sixteen_accounts(self):
        self.assertEqual(main.parse_account_count(None), 16)
        self.assertEqual(main.parse_account_count(""), 16)
        self.assertEqual(main.parse_account_count("16"), 16)

    def test_one_selects_only_the_first_account(self):
        self.assertEqual(main.parse_account_count("1"), 1)
        env = {"SITE_USERNAME_1": "user-1", "SITE_PASSWORD_1": "password-1"}
        with patch.object(main, "ACCOUNT_COUNT", 1):
            self.assertEqual(main.load_accounts(env), [(1, "user-1", "password-1")])

    def test_rejects_unsupported_account_counts(self):
        for value in ("0", "2", "15", "17", "invalid"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "1 or 16"):
                    main.parse_account_count(value)


if __name__ == "__main__":
    unittest.main()
