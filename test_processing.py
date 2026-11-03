import os
import tempfile
import unittest
from unittest.mock import patch

import main
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
        batch = [f"https://t.me/hasadalyoum/{number}" for number in range(1, 8)]
        with patch.object(main, "fetch_links", return_value=batch):
            selected = main.pull_once()
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, selected), 0)
        self.assertEqual(sender.calls, batch)
        self.assertEqual(main.read_lines(main.LINKS_PATH), [])

    def test_next_run_processes_only_new_links_not_previous_batch(self):
        first = ["https://t.me/hasadalyoum/1", "https://t.me/hasadalyoum/2"]
        second = ["https://t.me/hasadalyoum/3", "https://t.me/hasadalyoum/4"]
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
        batch = ["https://t.me/hasadalyoum/11", "https://t.me/hasadalyoum/12"]
        with patch.object(main, "fetch_links", return_value=batch):
            first_selection = main.pull_once()
        sender = FakeSender()
        self.assertEqual(main.run_browser_send(sender, first_selection), 0)
        self.assertEqual(sender.calls, batch)
        with patch.object(main, "fetch_links", return_value=batch):
            self.assertEqual(main.pull_once(), [])

    def test_only_unattempted_links_resume_after_fatal_stop(self):
        batch = [f"https://t.me/hasadalyoum/{number}" for number in (21, 22, 23)]
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
        batch = ["https://t.me/hasadalyoum/31", "https://t.me/hasadalyoum/32"]
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

    def test_previous_links_repeat_until_five_successes_when_no_new_links(self):
        link = "https://t.me/hasadalyoum/41"
        with open(os.path.join(self.state, "processed_links.txt"), "w", encoding="utf-8") as file:
            file.write(link + "\n")

        for expected_successes in range(1, 6):
            sender = FakeSender()
            self.assertEqual(main.run_previous_links_until_five(sender, self.state), 0)
            self.assertEqual(sender.calls, [link])
            self.assertEqual(load_successes(self.state)[link], expected_successes)

        finished = FakeSender()
        self.assertEqual(main.run_previous_links_until_five(finished, self.state), 0)
        self.assertEqual(finished.calls, [])


if __name__ == "__main__":
    unittest.main()
