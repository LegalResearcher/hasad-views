import unittest
from unittest.mock import MagicMock, patch

import main


class MainAccountSelectionTests(unittest.TestCase):
    @patch("main.run_browser_send", return_value=0)
    @patch("main.BrowserSender")
    @patch("main.select_rotating_account", return_value=(10, "user-10", "password-10"))
    @patch("main.pull_once", return_value=["https://t.me/hasadalyoum/101"])
    def test_main_uses_one_rotating_account_for_the_whole_run(
        self, pull_once, select_account, browser_sender, run_browser_send
    ):
        sender = MagicMock()
        browser_sender.return_value.__enter__.return_value = sender

        result = main.main()

        self.assertEqual(result, 0)
        pull_once.assert_called_once_with()
        select_account.assert_called_once()
        browser_sender.assert_called_once_with("user-10", "password-10")
        run_browser_send.assert_called_once_with(sender, ["https://t.me/hasadalyoum/101"])


if __name__ == "__main__":
    unittest.main()
