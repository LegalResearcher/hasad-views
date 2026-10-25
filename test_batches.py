import sys
import types
import unittest

# Keep these unit tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

from main import links_for_run, select_current_batch


class BatchSelectionTests(unittest.TestCase):
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

    def test_no_new_links_repeats_both_previous_batches(self):
        newest = [f"newest-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 6)]
        self.assertEqual(links_for_run([], [newest, older]), newest + older)

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
