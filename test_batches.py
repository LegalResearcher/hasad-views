import sys
import types
import unittest

# Keep these unit tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

from main import (
    ACCOUNT_COUNT,
    links_for_run,
    load_accounts,
    no_new_group_count,
    next_no_new_group_index,
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

    def test_combines_new_batch_with_both_previous_batches(self):
        previous = [f"old-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 6)]
        current = [f"new-{n}" for n in range(1, 6)]
        self.assertEqual(links_for_run(current, [previous, older]),
                         current + previous + older)
        self.assertEqual(links_for_run(["shared", "new"], [["shared", "old"], ["older"]]),
                         ["shared", "new", "old", "older"])

    def test_large_new_batch_is_not_truncated_when_combined(self):
        current = [f"new-{n}" for n in range(1, 18)]
        previous = [f"old-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 4)]
        self.assertEqual(links_for_run(current, [previous, older]),
                         current + previous + older)

    def test_no_new_links_repeats_three_previous_batches(self):
        newest = [f"newest-{n}" for n in range(1, 6)]
        middle = [f"middle-{n}" for n in range(1, 6)]
        oldest = [f"oldest-{n}" for n in range(1, 6)]
        self.assertEqual(links_for_run([], [newest, middle, oldest]),
                         newest + middle + oldest)

    def test_no_new_runs_rotate_older_batch_pairs_and_keep_newest_fixed(self):
        history = [[f"batch-{number}"] for number in range(10)]
        self.assertEqual(no_new_group_count(history), 5)
        expected = [
            ["batch-0", "batch-1", "batch-2"],
            ["batch-0", "batch-3", "batch-4"],
            ["batch-0", "batch-5", "batch-6"],
            ["batch-0", "batch-7", "batch-8"],
            ["batch-0", "batch-9"],
        ]
        for index, selected in enumerate(expected):
            self.assertEqual(select_no_new_batches(history, index), [[link] for link in selected])
        self.assertEqual(select_no_new_batches(history, 5), [["batch-0"], ["batch-1"], ["batch-2"]])
        self.assertEqual(next_no_new_group_index(history, 3), 4)
        self.assertEqual(next_no_new_group_index(history, 4), 0)

    def test_rotation_uses_legacy_archive_only_as_fifteen_link_backfill(self):
        history = [[f"batch-{number}"] for number in range(10)]
        archive = [f"legacy-{number}" for number in range(1, 30)]
        first = links_for_run([], history, archive, group_index=0)
        second = links_for_run([], history, archive, group_index=1)
        self.assertEqual(len(first), 15)
        self.assertEqual(len(second), 15)
        self.assertEqual(first[:3], ["batch-0", "batch-1", "batch-2"])
        self.assertEqual(second[:3], ["batch-0", "batch-3", "batch-4"])
        self.assertNotIn("batch-1", second)
        self.assertNotIn("batch-2", second)

    def test_no_new_links_backfills_from_archive_to_fifteen(self):
        newest = [f"newest-{n}" for n in range(1, 4)]
        older = [f"older-{n}" for n in range(1, 3)]
        archive = [f"archive-{n}" for n in range(1, 21)]
        selected = links_for_run([], [newest, older], archive)
        self.assertEqual(len(selected), 15)
        self.assertEqual(selected[:5], newest + older)
        self.assertEqual(selected[5:], list(reversed(archive[-10:])))

    def test_no_new_links_keeps_all_previous_when_they_exceed_fifteen(self):
        newest = [f"newest-{n}" for n in range(1, 9)]
        older = [f"older-{n}" for n in range(1, 10)]
        selected = links_for_run([], [newest, older], [])
        self.assertEqual(selected, newest + older)

    def test_uses_all_archive_if_fewer_than_fifteen_exist(self):
        archive = [f"archive-{n}" for n in range(1, 7)]
        selected = links_for_run([], [], archive)
        self.assertEqual(selected, list(reversed(archive)))

    def test_without_any_new_or_previous_links_returns_empty(self):
        self.assertEqual(links_for_run([], []), [])


if __name__ == "__main__":
    unittest.main()
