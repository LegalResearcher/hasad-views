import sys
import threading
import types
import unittest
from unittest.mock import patch

# Keep tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import retry_link


class ParallelRetryTests(unittest.TestCase):
    def test_process_link_uses_all_sixteen_accounts_concurrently(self):
        barrier = threading.Barrier(16)
        calls = []
        calls_lock = threading.Lock()

        class BarrierSender:
            def __init__(self, username, password):
                self.username = username

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __call__(self, link):
                barrier.wait(timeout=10)
                with calls_lock:
                    calls.append((self.username, link))
                return "ok"

        accounts = [
            (number, f"user-{number}", f"password-{number}")
            for number in range(1, 17)
        ]
        target = "https://t.me/hasadalyoum/12345"
        with patch.object(retry_link, "BrowserSender", BarrierSender):
            results = retry_link.process_link(accounts, target)

        self.assertEqual(set(results), set(range(1, 17)))
        self.assertTrue(all(status == "ok" for status in results.values()))
        self.assertEqual(len(calls), 16)
        self.assertEqual({link for _, link in calls}, {target})
        self.assertEqual({username for username, _ in calls},
                         {f"user-{number}" for number in range(1, 17)})

    def test_manual_retry_requires_secrets_for_all_sixteen_accounts(self):
        environ = {}
        for number in range(1, 16):
            environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"
        with self.assertRaisesRegex(ValueError, "SITE_USERNAME_16, SITE_PASSWORD_16"):
            retry_link.load_accounts(environ)


if __name__ == "__main__":
    unittest.main()
