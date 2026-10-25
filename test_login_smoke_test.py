import sys
import types
import unittest
from unittest.mock import patch

# Keep the login-runner tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import login_smoke_test


class LoginSmokeTestTests(unittest.TestCase):
    def _environment(self):
        environment = {}
        for number in range(1, 31):
            environment[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environment[f"SITE_PASSWORD_{number}"] = f"password-{number}"
        return environment

    def test_checks_only_accounts_one_through_ten_sequentially_without_orders(self):
        state = {"active": 0, "max_active": 0, "checked": []}

        class FakeSender:
            def __init__(self, username, _password):
                self.username = username

            def __enter__(self):
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                return self

            def __exit__(self, *_args):
                state["active"] -= 1

            def verify_login(self):
                state["checked"].append(self.username)
                return True

        with patch.object(login_smoke_test, "BrowserSender", FakeSender), \
             patch.object(login_smoke_test, "log"):
            result = login_smoke_test.run_login_test(self._environment())

        self.assertEqual(result, 0)
        self.assertEqual(state["checked"], [f"user-{number}" for number in range(1, 11)])
        self.assertEqual(state["max_active"], 1)
        self.assertEqual(state["active"], 0)

    def test_login_failure_does_not_submit_orders_or_skip_later_accounts(self):
        checked = []

        class FakeSender:
            def __init__(self, username, _password):
                self.username = username

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def verify_login(self):
                checked.append(self.username)
                return self.username != "user-3"

        with patch.object(login_smoke_test, "BrowserSender", FakeSender), \
             patch.object(login_smoke_test, "log"):
            result = login_smoke_test.run_login_test(self._environment())

        self.assertEqual(result, 1)
        self.assertEqual(checked, [f"user-{number}" for number in range(1, 11)])

    def test_missing_secret_stops_before_any_login_attempt(self):
        environment = self._environment()
        del environment["SITE_PASSWORD_4"]
        with patch.object(login_smoke_test, "BrowserSender") as sender, \
             patch.object(login_smoke_test, "log"):
            result = login_smoke_test.run_login_test(environment)

        self.assertEqual(result, 2)
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
