import os
import tempfile
import unittest
from unittest.mock import patch

import main


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
        self.sleep_patch = patch("main.time.sleep", return_value=None)
        self.sleep_patch.start()
        self.old_paths = (
            main.LINKS_PATH,
            main.PROCESSED_PATH,
            main.BATCH_HISTORY_PATH,
            main.PREVIOUS_BATCH_FALLBACK_PATH,
        )
        main.LINKS_PATH = os.path.join(self.state, "telegram_links.txt")
        main.PROCESSED_PATH = os.path.join(self.state, "processed_links.txt")
        main.BATCH_HISTORY_PATH = os.path.join(self.state, "previous_batch_links.json")
        main.PREVIOUS_BATCH_FALLBACK_PATH = os.path.join(self.state, "previous_batch_fallback.json")
        for path in (main.LINKS_PATH, main.PROCESSED_PATH):
            open(path, "w", encoding="utf-8").close()

    def tearDown(self):
        self.sleep_patch.stop()
        (
            main.LINKS_PATH,
            main.PROCESSED_PATH,
            main.BATCH_HISTORY_PATH,
            main.PREVIOUS_BATCH_FALLBACK_PATH,
        ) = self.old_paths
        self.temp.cleanup()

    def test_first_run_processes_all_new_links_once(self):
        batch = [f"https://t.me/hasadalyoum/{number}" for number in range(1, 8)]
        with patch.object(main, "fetch_links", return_value=batch):
            selected = main.pull_once()
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, selected, state_dir=self.state), 0)
        self.assertEqual(sender.calls, batch)
        self.assertEqual(main.read_lines(main.LINKS_PATH), [])

    def test_next_run_processes_new_batch_then_previous_batch(self):
        first = ["https://t.me/hasadalyoum/1", "https://t.me/hasadalyoum/2"]
        second = ["https://t.me/hasadalyoum/3", "https://t.me/hasadalyoum/4"]
        with patch.object(main, "fetch_links", return_value=first):
            selected_first = main.pull_once()
        main.run_browser_send(FakeSender(), selected_first, account_number=1, state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first + second):
            selected_second = main.pull_once()
        self.assertEqual(selected_second, second + first)
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, selected_second, account_number=2, state_dir=self.state), 0)
        self.assertEqual(sender.calls, second + first)

    def test_no_new_posts_reprocesses_last_two_batches_once(self):
        first = ["https://t.me/hasadalyoum/11", "https://t.me/hasadalyoum/12"]
        second = ["https://t.me/hasadalyoum/13"]
        with patch.object(main, "fetch_links", return_value=first):
            main.run_browser_send(FakeSender(), main.pull_once(), account_number=1, state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first + second):
            new_selection = main.pull_once()
        self.assertEqual(new_selection, second + first)
        main.run_browser_send(FakeSender(), new_selection, account_number=2, state_dir=self.state)

        with patch.object(main, "fetch_links", return_value=first + second):
            fallback_selection = main.pull_once()
        self.assertEqual(fallback_selection, second + first)
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, fallback_selection, account_number=3, state_dir=self.state), 0)
        self.assertEqual(sender.calls, second + first)

        with patch.object(main, "fetch_links", return_value=first + second):
            self.assertEqual(main.pull_once(), [])

    def test_new_batch_allows_one_fallback_again(self):
        first = ["https://t.me/hasadalyoum/21"]
        second = ["https://t.me/hasadalyoum/22"]
        with patch.object(main, "fetch_links", return_value=first):
            main.run_browser_send(FakeSender(), main.pull_once(), state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first):
            fallback = main.pull_once()
        self.assertEqual(fallback, first)
        main.run_browser_send(FakeSender(), fallback, account_number=2, state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first):
            self.assertEqual(main.pull_once(), [])

        with patch.object(main, "fetch_links", return_value=first + second):
            new_batch = main.pull_once()
        self.assertEqual(new_batch, second + first)
        main.run_browser_send(FakeSender(), new_batch, account_number=3, state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first + second):
            next_fallback = main.pull_once()
        self.assertEqual(next_fallback, second + first)
        with patch.object(main, "fetch_links", return_value=first + second):
            self.assertEqual(main.pull_once(), [])

    def test_third_run_uses_immediate_previous_batch_only(self):
        first = ["https://t.me/hasadalyoum/1"]
        second = ["https://t.me/hasadalyoum/2"]
        third = ["https://t.me/hasadalyoum/3"]
        with patch.object(main, "fetch_links", return_value=first):
            main.run_browser_send(FakeSender(), main.pull_once(), state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first + second):
            main.run_browser_send(FakeSender(), main.pull_once(), state_dir=self.state)
        with patch.object(main, "fetch_links", return_value=first + second + third):
            selected = main.pull_once()
        self.assertEqual(selected, third + second)
        self.assertNotIn(first[0], selected)

    def test_run_is_limited_to_nine_requests(self):
        batch = [f"https://t.me/hasadalyoum/{number}" for number in range(1, 12)]
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, batch, state_dir=self.state), 0)
        self.assertEqual(sender.calls, batch[:9])

    def test_scheduled_run_skips_success_and_failure_limits(self):
        batch = [f"https://t.me/hasadalyoum/{number}" for number in range(1, 4)]
        with open(os.path.join(self.state, "success_counts.json"), "w", encoding="utf-8") as file:
            file.write('{"' + batch[0] + '": 9}\n')
        with open(os.path.join(self.state, "failed_attempts.json"), "w", encoding="utf-8") as file:
            file.write('{"' + batch[1] + '": 3}\n')

        sender = FakeSender()
        main.run_browser_send(sender, batch, state_dir=self.state)
        self.assertEqual(sender.calls, [batch[2]])

    def test_same_account_does_not_receive_same_link_twice(self):
        link = "https://t.me/hasadalyoum/99"
        first = FakeSender()
        main.run_browser_send(first, [link], account_number=1, state_dir=self.state)
        repeated = FakeSender()
        main.run_browser_send(repeated, [link], account_number=1, state_dir=self.state)
        next_account = FakeSender()
        main.run_browser_send(next_account, [link], account_number=2, state_dir=self.state)
        self.assertEqual(first.calls, [link])
        self.assertEqual(repeated.calls, [])
        self.assertEqual(next_account.calls, [link])


if __name__ == "__main__":
    unittest.main()
