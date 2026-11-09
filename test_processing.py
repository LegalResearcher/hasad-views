import os
import tempfile
import unittest
from unittest.mock import call, patch

import main
import retry_link
from processing_state import load_successes


class FakeSender:
    def __init__(self, status="ok"):
        self.status = status
        self.calls = []

    def __call__(self, link):
        self.calls.append(link)
        return self.status


class BatchProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = self.temp.name
        self.old_paths = (
            main.LINKS_PATH,
            main.PROCESSED_PATH,
        )
        main.LINKS_PATH = os.path.join(self.state, "telegram_links.txt")
        main.PROCESSED_PATH = os.path.join(self.state, "processed_links.txt")
        for path in (main.LINKS_PATH, main.PROCESSED_PATH):
            open(path, "w", encoding="utf-8").close()

    def tearDown(self):
        (
            main.LINKS_PATH,
            main.PROCESSED_PATH,
        ) = self.old_paths
        self.temp.cleanup()

    def test_first_run_processes_all_new_links_once(self):
        batch = [f"https://t.me/aljnoubvoice/{number}" for number in range(1, 8)]
        with patch.object(main, "fetch_links", return_value=batch):
            selected = main.pull_once()
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, selected), 0)
        self.assertEqual(sender.calls, batch)
        self.assertEqual(main.read_lines(main.LINKS_PATH), [])

    def test_main_processes_queued_links_before_fetching_new_posts(self):
        pending = [
            "https://t.me/SanaaPressAgency/2073",
            "https://t.me/aljnoubvoice/9086",
        ]
        accounts = [(number, f"u{number}", f"p{number}") for number in range(1, 6)]
        main.write_lines(main.LINKS_PATH, pending)

        with patch.object(main, "load_accounts", return_value=accounts), \
                patch.object(main, "run_all_accounts", return_value=0) as run_all, \
                patch.object(main, "pull_once") as pull_once:
            self.assertEqual(main.main(), 0)

        run_all.assert_called_once_with(
            pending, accounts, state_dir=main.STATE_DIR,
            max_links=main.MAX_LINKS_PER_RUN,
        )
        pull_once.assert_not_called()

    def test_pull_once_collects_links_from_all_three_allowed_channels(self):
        channel_links = {
            "aljnoubvoice": ["https://t.me/aljnoubvoice/101"],
            "shmsannews": ["https://t.me/shmsannews/202"],
            "SanaaPressAgency": ["https://t.me/SanaaPressAgency/303"],
        }
        with patch.object(
            main, "fetch_links", side_effect=lambda channel: channel_links[channel]
        ) as fetch:
            selected = main.pull_once()

        self.assertEqual(fetch.call_args_list, [
            call("aljnoubvoice"), call("shmsannews"), call("SanaaPressAgency")
        ])
        self.assertEqual(selected, [
            "https://t.me/aljnoubvoice/101",
            "https://t.me/shmsannews/202",
            "https://t.me/SanaaPressAgency/303",
        ])

    def test_processed_links_from_shamsan_and_sanaa_are_not_new_again(self):
        processed = [
            "https://t.me/shmsannews/202",
            "https://t.me/SanaaPressAgency/303",
        ]
        main.write_lines(main.PROCESSED_PATH, processed)
        channel_links = {
            "aljnoubvoice": [],
            "shmsannews": [processed[0]],
            "SanaaPressAgency": [processed[1]],
        }
        with patch.object(
            main, "fetch_links", side_effect=lambda channel: channel_links[channel]
        ):
            self.assertEqual(main.pull_once(), [])

    def test_excluded_links_are_skipped_from_fallback_and_manual_retry(self):
        excluded = "https://t.me/aljnoubvoice/9232"
        main.write_lines(os.path.join(self.state, "processed_links.txt"), [excluded])
        main.write_lines(os.path.join(self.state, "excluded_links.txt"), [excluded])
        accounts = [(1, "u1", "p1")]
        with patch.object(main, "run_all_accounts") as run_all:
            self.assertEqual(main.run_previous_links_until_five(accounts, self.state), 0)
        run_all.assert_not_called()
        self.assertTrue(main.is_excluded_link(excluded, self.state))
        with patch.dict(os.environ, {"TARGET_LINK": excluded}), \
                patch.object(retry_link, "STATE_DIR", self.state):
            self.assertEqual(retry_link.main(), 2)

    def test_manual_retry_accepts_active_channels_but_rejects_hasadalyoum(self):
        self.assertIsNone(retry_link.LINK_PATTERN.fullmatch(
            "https://t.me/hasadalyoum/101"
        ))
        for channel in ("aljnoubvoice", "shmsannews", "SanaaPressAgency"):
            self.assertIsNotNone(retry_link.LINK_PATTERN.fullmatch(
                f"https://t.me/{channel}/101"
            ))
            self.assertTrue(main.is_target_link(f"https://t.me/{channel}/101"))
        self.assertFalse(main.is_target_link("https://t.me/hasadalyoum/101"))

    def test_old_hasadalyoum_queue_links_are_removed_not_resumed(self):
        old_link = "https://t.me/hasadalyoum/999"
        active_link = "https://t.me/aljnoubvoice/101"
        main.write_lines(main.LINKS_PATH, [old_link, active_link])
        with patch.object(main, "fetch_links", return_value=[]):
            self.assertEqual(main.pull_once(), [active_link])
        self.assertEqual(main.read_lines(main.LINKS_PATH), [active_link])

    def test_next_run_processes_only_new_links_not_previous_batch(self):
        first = ["https://t.me/aljnoubvoice/1", "https://t.me/aljnoubvoice/2"]
        second = ["https://t.me/aljnoubvoice/3", "https://t.me/aljnoubvoice/4"]
        with patch.object(main, "fetch_links", return_value=first):
            selected_first = main.pull_once()
        first_sender = FakeSender()
        main.run_browser_send(first_sender, selected_first)
        self.assertEqual(first_sender.calls, first)
        with patch.object(main, "fetch_links", return_value=first + second):
            selected_second = main.pull_once()
        self.assertEqual(selected_second, second)
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, selected_second), 0)
        self.assertEqual(sender.calls, second)

    def test_no_new_posts_does_not_replay_completed_links(self):
        batch = ["https://t.me/aljnoubvoice/11", "https://t.me/aljnoubvoice/12"]
        with patch.object(main, "fetch_links", return_value=batch):
            first_selection = main.pull_once()
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, first_selection), 0)
        self.assertEqual(sender.calls, batch)
        with patch.object(main, "fetch_links", return_value=batch):
            self.assertEqual(main.pull_once(), [])

    def test_only_unattempted_links_resume_after_fatal_stop(self):
        batch = [f"https://t.me/aljnoubvoice/{number}" for number in (21, 22, 23)]
        with patch.object(main, "fetch_links", return_value=batch):
            selected = main.pull_once()
        stopped = FakeSender("fatal")
        self.assertEqual(main.run_browser_send(stopped, selected), 1)
        self.assertEqual(stopped.calls, [batch[0]])

        with patch.object(main, "fetch_links", return_value=batch):
            pending = main.pull_once()
        self.assertEqual(pending, batch[1:])
        resumed = FakeSender()
        self.assertEqual(main.run_browser_send(resumed, pending), 0)
        self.assertEqual(resumed.calls, batch[1:])

    def test_no_new_links_returns_unattempted_queue_for_fallback_processing(self):
        batch = ["https://t.me/aljnoubvoice/31", "https://t.me/aljnoubvoice/32"]
        with patch.object(main, "fetch_links", return_value=batch):
            selected = main.pull_once()
        stopped = FakeSender("fatal")
        self.assertEqual(main.run_browser_send(stopped, selected), 1)
        self.assertEqual(stopped.calls, [batch[0]])

        with patch.object(main, "fetch_links", return_value=batch):
            fallback = main.pull_once()
        self.assertEqual(fallback, [batch[1]])
        resumed = FakeSender()
        self.assertEqual(main.run_browser_send(resumed, fallback), 0)
        self.assertEqual(resumed.calls, [batch[1]])

    def test_previous_links_collect_unlimited_successes_from_all_five_accounts(self):
        link = "https://t.me/aljnoubvoice/41"
        with open(os.path.join(self.state, "processed_links.txt"), "w", encoding="utf-8") as file:
            file.write(link + "\n")
        accounts = [(number, f"u{number}", f"p{number}") for number in range(1, 6)]

        def all_accounts_succeeded(account, links):
            return [(selected, "ok") for selected in links]

        with patch.object(main, "_send_account", side_effect=all_accounts_succeeded) as send:
            self.assertEqual(main.run_previous_links_until_five(accounts, self.state), 0)
            self.assertEqual(send.call_count, 5)
            self.assertEqual(load_successes(self.state)[link], 5)
            self.assertEqual(main.run_previous_links_until_five(accounts, self.state), 0)
            self.assertEqual(load_successes(self.state)[link], 10)

        with patch.object(main, "_send_account", side_effect=all_accounts_succeeded) as send:
            self.assertEqual(main.run_previous_links_until_five(accounts, self.state), 0)
            self.assertEqual(send.call_count, 5)
            self.assertEqual(load_successes(self.state)[link], 15)


if __name__ == "__main__":
    unittest.main()
