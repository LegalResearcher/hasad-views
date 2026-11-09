import os
import tempfile
import unittest
from unittest.mock import patch

import main


class MainAccountSelectionTests(unittest.TestCase):
    def test_main_runs_all_nine_accounts_every_time(self):
        with tempfile.TemporaryDirectory() as state_dir:
            environ = {}
            for number in range(1, 10):
                environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
                environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"
            first = main.load_accounts(environ, state_dir)
            second = main.load_accounts(environ, state_dir)
            expected = list(range(1, 10))
            self.assertEqual([account[0] for account in first], expected)
            self.assertEqual([account[0] for account in second], expected)
            with open(os.path.join(state_dir, main.RUN_NUMBER_FILENAME), encoding="utf-8") as file:
                self.assertEqual(file.read().strip(), '{"next_run": 3}')
    @patch("main.run_all_accounts", return_value=0)
    @patch("main.load_accounts", return_value=[
        (1, "user-1", "password-1"),
        (2, "user-2", "password-2"),
        (3, "user-3", "password-3"),
        (4, "user-4", "password-4"),
        (5, "user-5", "password-5"),
        (6, "user-6", "password-6"),
        (7, "user-7", "password-7"),
        (8, "user-8", "password-8"),
        (9, "user-9", "password-9"),
    ])
    @patch("main.pull_once", return_value=["https://t.me/aljnoubvoice/101"])
    @patch("main.read_lines", return_value=[])
    def test_main_runs_all_nine_accounts_on_same_links(
        self, read_lines, pull_once, load_accounts, run_all_accounts
    ):
        with patch("main.consume_new_links_only_flag", return_value=False):
            result = main.main()
        self.assertEqual(result, 0)
        pull_once.assert_called_once_with()
        load_accounts.assert_called_once()
        run_all_accounts.assert_called_once_with(
            ["https://t.me/aljnoubvoice/101"], load_accounts.return_value,
            state_dir=main.STATE_DIR, max_links=main.MAX_LINKS_PER_RUN,
        )

    @patch("main.run_previous_links_until_five", return_value=0)
    @patch("main.pull_once", return_value=[])
    @patch("main.load_accounts", return_value=[
        (1, "user-1", "password-1"),
        (2, "user-2", "password-2"),
        (3, "user-3", "password-3"),
        (4, "user-4", "password-4"),
        (5, "user-5", "password-5"),
        (6, "user-6", "password-6"),
        (7, "user-7", "password-7"),
        (8, "user-8", "password-8"),
        (9, "user-9", "password-9"),
    ])
    @patch("main.read_lines", return_value=[])
    def test_main_retries_previous_links_when_no_new_links(
        self, read_lines, load_accounts, pull_once, run_previous_links
    ):
        with patch("main.consume_new_links_only_flag", return_value=False):
            result = main.main()
        self.assertEqual(result, 0)
        pull_once.assert_called_once_with()
        run_previous_links.assert_called_once_with(load_accounts.return_value)


if __name__ == "__main__":
    unittest.main()
