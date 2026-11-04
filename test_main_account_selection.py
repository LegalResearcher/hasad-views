import unittest
from unittest.mock import patch

import main


class MainAccountSelectionTests(unittest.TestCase):
    @patch("main.run_all_accounts", return_value=0)
    @patch("main.load_accounts", return_value=[
        (1, "user-1", "password-1"),
        (2, "user-2", "password-2"),
        (3, "user-3", "password-3"),
        (4, "user-4", "password-4"),
        (5, "user-5", "password-5"),
    ])
    @patch("main.pull_once", return_value=["https://t.me/aljnoubvoice/101"])
    @patch("main.read_lines", return_value=[])
    def test_main_runs_all_five_accounts_on_same_links(
        self, read_lines, pull_once, load_accounts, run_all_accounts
    ):
        result = main.main()
        self.assertEqual(result, 0)
        pull_once.assert_called_once_with()
        load_accounts.assert_called_once()
        run_all_accounts.assert_called_once_with(
            ["https://t.me/aljnoubvoice/101"], load_accounts.return_value,
            state_dir=main.STATE_DIR,
        )


if __name__ == "__main__":
    unittest.main()
