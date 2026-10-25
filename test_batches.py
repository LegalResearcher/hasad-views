import sys
import types
import unittest
from unittest.mock import patch

# Keep these unit tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

import main
from main import (
    ACCOUNT_COUNT,
    LINKS_PATH,
    NO_NEW_BATCH_CURSOR_PATH,
    PAIR_ATTEMPTS_PATH,
    PAIR_DONE_PATH,
    PREVIOUS_BATCH_PATH,
    PROCESSED_PATH,
    batch_fingerprint,
    links_for_run,
    load_accounts,
    select_current_batch,
    select_no_new_batches,
)


class BatchSelectionTests(unittest.TestCase):
    def test_main_workflow_loads_all_thirty_accounts(self):
        environ = {}
        for number in range(1, 31):
            environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"
        accounts = load_accounts(environ)
        self.assertEqual(ACCOUNT_COUNT, 30)
        self.assertEqual(len(accounts), 30)
        self.assertEqual(accounts[-1], (30, "user-30", "password-30"))

    def test_main_workflow_requires_thirtieth_account_secrets(self):
        environ = {}
        for number in range(1, 30):
            environ[f"SITE_USERNAME_{number}"] = f"user-{number}"
            environ[f"SITE_PASSWORD_{number}"] = f"password-{number}"
        with self.assertRaisesRegex(ValueError, "SITE_USERNAME_30, SITE_PASSWORD_30"):
            load_accounts(environ)

    def test_selects_all_new_links_in_order_without_a_five_link_cap(self):
        discovered = [f"link-{n}" for n in range(1, 9)]
        batch, marked = select_current_batch(discovered, {"link-1", "link-2"})
        self.assertEqual(batch, [f"link-{n}" for n in range(3, 9)])
        self.assertEqual(marked, set(batch))

    def test_first_run_also_selects_all_discovered_links(self):
        discovered = [f"link-{n}" for n in range(1, 9)]
        batch, marked = select_current_batch(discovered, set())
        self.assertEqual(batch, discovered)
        self.assertEqual(marked, set(discovered))

    def test_discovery_does_not_mark_new_links_processed_before_batch_checkpoint(self):
        links = ["link-1", "link-2"]
        writes = []
        with patch.object(main, "fetch_links", return_value=links), \
             patch.object(main, "read_lines", return_value=[]), \
             patch.object(
                 main, "append_lines",
                 side_effect=lambda path, values: writes.append((path, list(values))),
             ), \
             patch.object(main, "log"):
            self.assertEqual(main.pull_once(), links)

        self.assertEqual(writes, [(LINKS_PATH, links)])

    def test_new_batch_pairs_only_with_its_immediate_predecessor(self):
        previous = [f"old-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 6)]
        current = [f"new-{n}" for n in range(1, 6)]
        self.assertEqual(links_for_run(current, [previous, older]), current + previous)
        self.assertEqual(
            links_for_run(["shared", "new"], [["shared", "old"], ["older"]]),
            ["shared", "new", "old"],
        )

    def test_checkpoint_precedes_processing_and_marks_current_batch(self):
        history = [["previous-1"]]
        current = ["new-1", "new-2"]
        stored = {PAIR_DONE_PATH: {}, PAIR_ATTEMPTS_PATH: {}, NO_NEW_BATCH_CURSOR_PATH: {}}
        saved = {}
        events = []
        processed_calls = []
        observed = []
        accounts = [(n, f"user-{n}", f"password-{n}") for n in range(1, 31)]

        def save_state(path, value):
            events.append(("save", path))
            saved[path] = value

        def append_state(path, values):
            events.append(("append", path))
            if path == PROCESSED_PATH:
                processed_calls.append(list(values))

        def fake_process(account, links, _pair_done, _attempts, forced_links, *_args):
            events.append(("process", account[0]))
            observed.append((list(links), set(forced_links)))
            return account[0], {link: "ok" for link in links}

        with patch.object(main, "load_json", side_effect=lambda path: stored[path]), \
             patch.object(main, "load_previous_batches", return_value=history), \
             patch.object(main, "save_json", side_effect=save_state), \
             patch.object(main, "append_lines", side_effect=append_state), \
             patch.object(main, "fetch_view_counts") as fetch_counts, \
             patch.object(main, "process_account", side_effect=fake_process), \
             patch.object(main, "log"):
            result = main.run_parallel(accounts, current)

        expected = current + history[0]
        checkpoint_cursor = events.index(("save", NO_NEW_BATCH_CURSOR_PATH))
        checkpoint_history = events.index(("save", PREVIOUS_BATCH_PATH))
        checkpoint_processed = events.index(("append", PROCESSED_PATH))
        first_process = next(index for index, event in enumerate(events) if event[0] == "process")
        self.assertEqual(result, 0)
        self.assertEqual(checkpoint_cursor < checkpoint_history < checkpoint_processed < first_process, True)
        self.assertEqual(processed_calls, [current])
        self.assertEqual(len(observed), 30)
        self.assertTrue(all(links == expected for links, _ in observed))
        self.assertTrue(all(forced == set(history[0]) for _, forced in observed))
        self.assertEqual(saved[PREVIOUS_BATCH_PATH], [current] + history)
        self.assertEqual(saved[NO_NEW_BATCH_CURSOR_PATH], {"backfilled_batch_id": None})
        fetch_counts.assert_not_called()

    def test_recovery_of_checkpointed_current_batch_does_not_pair_it_with_itself(self):
        current = ["new-1", "new-2"]
        history = [current, ["previous-1"], ["older-1"]]
        stored = {PAIR_DONE_PATH: {}, PAIR_ATTEMPTS_PATH: {}, NO_NEW_BATCH_CURSOR_PATH: {}}
        saved = {}
        observed = []
        accounts = [(n, f"user-{n}", f"password-{n}") for n in range(1, 31)]

        def fake_process(account, links, _pair_done, _attempts, forced_links, *_args):
            observed.append((list(links), set(forced_links)))
            return account[0], {link: "ok" for link in links}

        with patch.object(main, "load_json", side_effect=lambda path: stored[path]), \
             patch.object(main, "load_previous_batches", return_value=history), \
             patch.object(main, "save_json", side_effect=lambda path, value: saved.__setitem__(path, value)), \
             patch.object(main, "append_lines"), \
             patch.object(main, "fetch_view_counts"), \
             patch.object(main, "process_account", side_effect=fake_process), \
             patch.object(main, "log"):
            result = main.run_parallel(accounts, current)

        expected = current + history[1]
        self.assertEqual(result, 0)
        self.assertEqual(len(observed), 30)
        self.assertTrue(all(links == expected for links, _ in observed))
        self.assertTrue(all(forced == set(history[1]) for _, forced in observed))
        self.assertEqual(saved[PREVIOUS_BATCH_PATH], history)

    def test_large_new_batch_is_not_truncated_and_uses_one_previous_batch(self):
        current = [f"new-{n}" for n in range(1, 18)]
        previous = [f"old-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 4)]
        self.assertEqual(links_for_run(current, [previous, older]), current + previous)

    def test_idle_backfill_selects_two_latest_batches_once_per_latest_batch(self):
        history = [[f"batch-{number}"] for number in range(10)]
        self.assertEqual(select_no_new_batches(history, {}), history[:2])

        saved_cursor = {"backfilled_batch_id": batch_fingerprint(history[0])}
        self.assertEqual(select_no_new_batches(history, saved_cursor), [])

        next_history = [["new-batch"], *history]
        self.assertEqual(select_no_new_batches(next_history, saved_cursor), next_history[:2])

    def test_idle_backfill_discards_legacy_cursor_and_does_not_add_archive(self):
        history = [["newest-1", "newest-2"], ["previous-1"], ["older-1"]]
        self.assertEqual(select_no_new_batches(history, {"next_group": 4}), history[:2])
        self.assertEqual(links_for_run([], history), ["newest-1", "newest-2", "previous-1"])

    def test_idle_backfill_persists_latest_batch_marker_and_only_sends_two_batches(self):
        history = [["newest-1", "newest-2"], ["previous-1"], ["older-1"]]
        cursor = {"next_group": 0}
        stored = {
            PAIR_DONE_PATH: {},
            PAIR_ATTEMPTS_PATH: {},
            PREVIOUS_BATCH_PATH: history,
            NO_NEW_BATCH_CURSOR_PATH: cursor,
        }
        saved = {}
        processed_links = []
        accounts = [(n, f"user-{n}", f"password-{n}") for n in range(1, 31)]

        def fake_process(account, links, *_args):
            processed_links.append(list(links))
            return account[0], {link: "ok" for link in links}

        with patch.object(main, "load_json", side_effect=lambda path: stored[path]), \
             patch.object(main, "load_previous_batches", return_value=history), \
             patch.object(main, "save_json", side_effect=lambda path, value: saved.__setitem__(path, value)), \
             patch.object(main, "fetch_view_counts") as fetch_counts, \
             patch.object(main, "process_account", side_effect=fake_process), \
             patch.object(main, "log"):
            result = main.run_parallel(accounts, [])

        expected_links = history[0] + history[1]
        self.assertEqual(result, 0)
        self.assertEqual(len(processed_links), 30)
        self.assertTrue(all(links == expected_links for links in processed_links))
        fetch_counts.assert_not_called()
        self.assertEqual(
            saved[NO_NEW_BATCH_CURSOR_PATH],
            {"backfilled_batch_id": batch_fingerprint(history[0])},
        )
        self.assertEqual(saved[PREVIOUS_BATCH_PATH], history)

    def test_second_no_new_run_skips_everything_until_new_batch_arrives(self):
        history = [["newest-1"], ["previous-1"], ["older-1"]]
        stored = {
            PAIR_DONE_PATH: {},
            PAIR_ATTEMPTS_PATH: {},
            PREVIOUS_BATCH_PATH: history,
            NO_NEW_BATCH_CURSOR_PATH: {
                "backfilled_batch_id": batch_fingerprint(history[0])
            },
        }
        with patch.object(main, "load_json", side_effect=lambda path: stored[path]), \
             patch.object(main, "load_previous_batches", return_value=history), \
             patch.object(main, "save_json") as save_state, \
             patch.object(main, "fetch_view_counts") as fetch_counts, \
             patch.object(main, "process_account") as process, \
             patch.object(main, "log"):
            result = main.run_parallel([], [])

        self.assertEqual(result, 0)
        fetch_counts.assert_not_called()
        process.assert_not_called()
        save_state.assert_not_called()

    def test_no_new_links_does_not_pull_from_unbatched_archive(self):
        self.assertEqual(links_for_run([], []), [])


if __name__ == "__main__":
    unittest.main()
