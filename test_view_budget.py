import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from view_budget import (
    MAX_TOTAL_VIEWS,
    ViewBudget,
    fetch_current_views,
    fetch_view_counts,
    parse_view_count,
)


class ViewCountParsingTests(unittest.TestCase):
    def test_parses_compact_and_arabic_counts_conservatively(self):
        self.assertEqual(parse_view_count("1,234"), 1234)
        self.assertEqual(parse_view_count("1.49K"), 1500)
        self.assertEqual(parse_view_count("2.67K"), 2680)
        self.assertEqual(parse_view_count("٢٥٠"), 250)
        self.assertIsNone(parse_view_count("unknown"))

    def test_reads_only_the_requested_public_telegram_post(self):
        html = (
            '<div data-post="hasadalyoum/10"><span class="tgme_widget_message_views">2K</span></div>'
            '<div data-post="hasadalyoum/11"><span class="tgme_widget_message_views">2.67K</span></div>'
        )
        response = type("Response", (), {"status_code": 200, "text": html})()
        with patch("view_budget.requests.get", return_value=response):
            self.assertEqual(fetch_current_views("https://t.me/hasadalyoum/11"), 2680)

    def test_unreadable_count_is_returned_as_unknown_by_bulk_reader(self):
        with patch("view_budget.fetch_current_views", side_effect=RuntimeError("missing count")):
            self.assertEqual(
                fetch_view_counts(["https://t.me/hasadalyoum/12"]),
                {"https://t.me/hasadalyoum/12": None},
            )


class ViewBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "view_budget.json")

    def tearDown(self):
        self.temp.cleanup()

    def budget(self, **kwargs):
        return ViewBudget(self.path, **kwargs)

    def read_state(self):
        with open(self.path, encoding="utf-8") as file:
            return json.load(file)

    def test_reserves_only_the_remaining_quantity_below_ceiling(self):
        link = "https://t.me/hasadalyoum/1"
        budget = self.budget()
        self.assertEqual(budget.reserve(link, 10_350), 50)
        self.assertEqual(budget.reserve(link, 10_350), 0)
        self.assertEqual(MAX_TOTAL_VIEWS, 10_400)

    def test_concurrent_accounts_and_budget_instances_cannot_double_reserve(self):
        link = "https://t.me/hasadalyoum/2"

        def reserve_once(_):
            return ViewBudget(self.path).reserve(link, 10_000)

        with ThreadPoolExecutor(max_workers=20) as pool:
            quantities = list(pool.map(reserve_once, range(20)))
        self.assertEqual(sum(quantities), 400)
        state = self.read_state()[link]
        self.assertEqual(state["baseline"] + state["reserved"], 10_400)
        self.assertEqual(state["reserved"], 400)

    def test_at_or_above_ceiling_sends_no_budget(self):
        link = "https://t.me/hasadalyoum/3"
        budget = self.budget()
        self.assertEqual(budget.reserve(link, 10_400), 0)
        self.assertEqual(budget.reserve(link, 10_900), 0)

    def test_lower_later_counter_does_not_restore_reserved_capacity(self):
        link = "https://t.me/hasadalyoum/4"
        budget = self.budget()
        self.assertEqual(budget.reserve(link, 10_300), 100)
        self.assertEqual(budget.reserve(link, 1_000), 0)
        self.assertEqual(self.read_state()[link]["max_observed"], 10_300)

    def test_increased_counter_is_added_to_all_pending_reservations(self):
        link = "https://t.me/hasadalyoum/5"
        budget = self.budget()
        self.assertEqual(budget.reserve(link, 10_000), 100)
        self.assertEqual(budget.reserve(link, 10_200), 100)
        self.assertEqual(budget.reserve(link, 10_200), 0)
        state = self.read_state()[link]
        self.assertEqual(state["max_observed"], 10_200)
        self.assertEqual(state["reserved"], 200)

    def test_legacy_successful_account_pairs_are_reserved(self):
        link = "https://t.me/hasadalyoum/6"
        legacy_path = os.path.join(self.temp.name, "account_link_done.json")
        with open(legacy_path, "w", encoding="utf-8") as file:
            json.dump({f"{number}|{link}": True for number in (1, 3, 7)}, file)
        budget = self.budget(legacy_pair_done_path=legacy_path)
        self.assertEqual(budget.reserve(link, 10_000), 100)
        self.assertEqual(self.read_state()[link]["reserved"], 400)

    def test_old_global_success_marker_uses_full_conservative_run_reserve(self):
        link = "https://t.me/hasadalyoum/7"
        done_path = os.path.join(self.temp.name, "done_links.txt")
        with open(done_path, "w", encoding="utf-8") as file:
            file.write(link + "\n")
        budget = self.budget(legacy_done_links_path=done_path)
        self.assertEqual(budget.reserve(link, 9_000), 0)
        self.assertEqual(self.read_state()[link]["reserved"], 1_600)

    def test_corrupt_budget_fails_closed(self):
        with open(self.path, "w", encoding="utf-8") as file:
            file.write("not-json")
        with self.assertRaisesRegex(ValueError, "unreadable"):
            self.budget().reserve("https://t.me/hasadalyoum/8", 10)


if __name__ == "__main__":
    unittest.main()
