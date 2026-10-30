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
        self.assertEqual(main.run_browser_send(sender, selected), 0)
        self.assertEqual(sender.calls, batch)
        self.assertEqual(main.read_lines(main.LINKS_PATH), [])

    def test_next_run_processes_new_batch_then_previous_batch(self):
        first = ["https://t.me/hasadalyoum/1", "https://t.me/hasadalyoum/2"]
        second = ["https://t.me/hasadalyoum/3", "https://t.me/hasadalyoum/4"]
        with patch.object(main, "fetch_links", return_value=first):
            selected_first = main.pull_once()
        main.run_browser_send(FakeSender(), selected_first)
        with patch.object(main, "fetch_links", return_value=first + second):
            selected_second = main.pull_once()
        self.assertEqual(selected_second, second + first)
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, selected_second), 0)
        self.assertEqual(sender.calls, second + first)

    def test_no_new_posts_reprocesses_last_two_batches_once(self):
        first = ["https://t.me/hasadalyoum/11", "https://t.me/hasadalyoum/12"]
        second = ["https://t.me/hasadalyoum/13"]
        with patch.object(main, "fetch_links", return_value=first):
            main.run_browser_send(FakeSender(), main.pull_once())
        with patch.object(main, "fetch_links", return_value=first + second):
            new_selection = main.pull_once()
        self.assertEqual(new_selection, second + first)
        main.run_browser_send(FakeSender(), new_selection)

        with patch.object(main, "fetch_links", return_value=first + second):
            fallback_selection = main.pull_once()
        self.assertEqual(fallback_selection, second + first)
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, fallback_selection), 0)
        self.assertEqual(sender.calls, second + first)

        with patch.object(main, "fetch_links", return_value=first + second):
            self.assertEqual(main.pull_once(), [])

    def test_new_batch_allows_one_fallback_again(self):
        first = ["https://t.me/hasadalyoum/21"]
        second = ["https://t.me/hasadalyoum/22"]
        with patch.object(main, "fetch_links", return_value=first):
            main.run_browser_send(FakeSender(), main.pull_once())
        with patch.object(main, "fetch_links", return_value=first):
            fallback = main.pull_once()
        self.assertEqual(fallback, first)
        main.run_browser_send(FakeSender(), fallback)
        with patch.object(main, "fetch_links", return_value=first):
            self.assertEqual(main.pull_once(), [])

        with patch.object(main, "fetch_links", return_value=first + second):
            new_batch = main.pull_once()
        self.assertEqual(new_batch, second + first)
        main.run_browser_send(FakeSender(), new_batch)
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
            main.run_browser_send(FakeSender(), main.pull_once())
        with patch.object(main, "fetch_links", return_value=first + second):
            main.run_browser_send(FakeSender(), main.pull_once())
        with patch.object(main, "fetch_links", return_value=first + second + third):
            selected = main.pull_once()
        self.assertEqual(selected, third + second)
        self.assertNotIn(first[0], selected)


if __name__ == "__main__":
    unittest.main()
