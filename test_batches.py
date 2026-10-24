import sys
import types
import unittest

# Keep these unit tests independent of Playwright/browser installation.
sys.modules.setdefault("browser_sender", types.SimpleNamespace(BrowserSender=object))

from main import links_for_run, select_current_batch


class BatchSelectionTests(unittest.TestCase):
    def test_selects_up_to_five_new_links_in_order(self):
        discovered = [f"link-{n}" for n in range(1, 9)]
        batch, marked = select_current_batch(discovered, {"link-1", "link-2"})
        self.assertEqual(batch, ["link-3", "link-4", "link-5", "link-6", "link-7"])
        self.assertEqual(marked, set(batch))

    def test_first_run_uses_latest_five_and_marks_visible_history(self):
        discovered = [f"link-{n}" for n in range(1, 9)]
        batch, marked = select_current_batch(discovered, set(), first_run=True)
        self.assertEqual(batch, ["link-4", "link-5", "link-6", "link-7", "link-8"])
        self.assertEqual(marked, set(discovered))

    def test_combines_new_batch_with_most_recent_previous_batch(self):
        previous = [f"old-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 6)]
        current = [f"new-{n}" for n in range(1, 6)]
        self.assertEqual(links_for_run(current, [previous, older]), current + previous)
        self.assertEqual(links_for_run(["shared", "new"], [["shared", "old"]]),
                         ["shared", "new", "old"])

    def test_no_new_links_repeats_both_previous_batches(self):
        newest = [f"newest-{n}" for n in range(1, 6)]
        older = [f"older-{n}" for n in range(1, 6)]
        self.assertEqual(links_for_run([], [newest, older]), newest + older)

    def test_without_any_new_or_previous_links_returns_empty(self):
        self.assertEqual(links_for_run([], []), [])


if __name__ == "__main__":
    unittest.main()
