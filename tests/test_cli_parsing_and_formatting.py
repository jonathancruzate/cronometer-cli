"""Offline unit tests for core non-network logic: argument parsing, validation, and food-entry formatting."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

import crono


class TestArgumentParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = crono.build_parser()

    def test_global_flags_and_auth_commands(self) -> None:
        args = self.parser.parse_args(
            [
                "--json",
                "--base-url",
                "https://example.test/api/v2/",
                "--session-file",
                "/tmp/test-session.json",
                "auth",
                "login",
                "--totp",
                "123456",
            ]
        )
        self.assertTrue(args.json)
        self.assertEqual(args.base_url, "https://example.test/api/v2/")
        self.assertEqual(args.session_file, "/tmp/test-session.json")
        self.assertEqual(args.group, "auth")
        self.assertEqual(args.command, "login")
        self.assertEqual(args.totp, "123456")

        web_args = self.parser.parse_args(["auth", "web-login"])
        self.assertEqual(web_args.group, "auth")
        self.assertEqual(web_args.command, "web-login")
        self.assertIsNone(web_args.totp)

        status_args = self.parser.parse_args(["auth", "status"])
        self.assertFalse(status_args.json)
        self.assertEqual(status_args.group, "auth")
        self.assertEqual(status_args.command, "status")

    def test_diary_commands_and_aliases(self) -> None:
        show_default = self.parser.parse_args(["diary", "show"])
        self.assertEqual(show_default.group, "diary")
        self.assertEqual(show_default.command, "show")
        self.assertIsNone(show_default.date)

        show_dated = self.parser.parse_args(["diary", "show", "--date", "2026-10-18"])
        self.assertEqual(show_dated.date, "2026-10-18")

        add_args = self.parser.parse_args(
            [
                "diary",
                "add",
                "--food-id",
                "464785",
                "--measure-id",
                "1072753",
                "--amount",
                "210.5",
                "--date",
                "2026-10-18",
                "--time",
                "08:30",
                "--group",
                "2",
                "--order",
                "10",
                "--dry-run",
            ]
        )
        self.assertEqual(add_args.food_id, 464785)
        self.assertEqual(add_args.measure_id, 1072753)
        self.assertEqual(add_args.amount, 210.5)
        self.assertEqual(add_args.date, "2026-10-18")
        self.assertEqual(add_args.time, "08:30")
        self.assertEqual(add_args.group_id, 2)
        self.assertEqual(add_args.order, 10)
        self.assertTrue(add_args.dry_run)

        del_args = self.parser.parse_args(
            ["diary", "delete", "--entry-id", "5312075123", "--date", "2026-10-18", "--dry-run"]
        )
        self.assertEqual(del_args.command, "delete")
        self.assertEqual(del_args.serving_id, 5312075123)
        self.assertEqual(del_args.date, "2026-10-18")
        self.assertTrue(del_args.dry_run)

        exp_args = self.parser.parse_args(
            ["diary", "export", "--from", "2026-10-01", "--to", "2026-10-07", "--format", "csv"]
        )
        self.assertEqual(exp_args.command, "export")
        self.assertEqual(exp_args.from_date, "2026-10-01")
        self.assertEqual(exp_args.to_date, "2026-10-07")
        self.assertEqual(exp_args.format, "csv")

    def test_food_targets_and_biometric_commands(self) -> None:
        fs_args = self.parser.parse_args(["food", "search", "greek yogurt"])
        self.assertEqual(fs_args.group, "food")
        self.assertEqual(fs_args.command, "search")
        self.assertEqual(fs_args.query, "greek yogurt")

        fc_args = self.parser.parse_args(["food", "custom"])
        self.assertEqual(fc_args.group, "food")
        self.assertEqual(fc_args.command, "custom")

        tgt_args = self.parser.parse_args(["targets", "show"])
        self.assertEqual(tgt_args.group, "targets")
        self.assertEqual(tgt_args.command, "show")

        bio_log = self.parser.parse_args(
            [
                "biometric",
                "log",
                "--metric-id",
                "1",
                "--value",
                "75.4",
                "--unit-id",
                "2",
                "--date",
                "2026-10-18",
                "--time",
                "07:00",
            ]
        )
        self.assertEqual(bio_log.group, "biometric")
        self.assertEqual(bio_log.command, "log")
        self.assertEqual(bio_log.metric_id, 1)
        self.assertEqual(bio_log.value, 75.4)
        self.assertEqual(bio_log.unit_id, 2)
        self.assertFalse(bio_log.dry_run)

        bio_search_empty = self.parser.parse_args(["biometric", "search"])
        self.assertIsNone(bio_search_empty.query)
        bio_search_q = self.parser.parse_args(["biometric", "search", "Weight"])
        self.assertEqual(bio_search_q.query, "Weight")

    def test_invalid_arguments_exit(self) -> None:
        invalid_cases = [
            [],
            ["diary"],
            ["diary", "export", "--format", "xml"],
            ["food", "search"],
            ["diary", "delete", "--serving-id", "123"],
        ]
        for argv in invalid_cases:
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit):
                    with redirect_stderr(io.StringIO()):
                        self.parser.parse_args(argv)


class TestValidationAndPayloads(unittest.TestCase):
    def test_validate_date(self) -> None:
        self.assertEqual(crono.validate_date("2024-02-29"), "2024-02-29")
        for bad in ("2026/10/18", "10-18-2026", "2026-02-30", "2025-02-29", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(crono.CronoError):
                    crono.validate_date(bad)

    def test_validate_time(self) -> None:
        self.assertEqual(crono.validate_time("08:30"), "08:30:00")
        self.assertEqual(crono.validate_time("23:59:59"), "23:59:59")
        self.assertEqual(crono.validate_time("00:00"), "00:00:00")
        for bad in ("8:30", "24:00", "12:60", "12:00:60", "noon"):
            with self.subTest(bad=bad):
                with self.assertRaises(crono.CronoError):
                    crono.validate_time(bad)

    def test_build_serving_object_group_and_order(self) -> None:
        with_group = crono.build_serving_object(
            food_id=10,
            measure_id=20,
            amount=150,
            date_str="2026-10-18",
            time_str="12:15",
            user_id=99,
            group_id=3,
        )
        self.assertEqual(with_group["groupId"], 3)
        self.assertEqual(with_group["order"], 3)
        self.assertEqual(with_group["time"], "12:15:00")

        with_explicit_order = crono.build_serving_object(
            food_id=10,
            measure_id=20,
            amount=150,
            date_str="2026-10-18",
            time_str="12:15:30",
            group_id=3,
            order=42,
        )
        self.assertEqual(with_explicit_order["groupId"], 3)
        self.assertEqual(with_explicit_order["order"], 42)
        self.assertIsNone(with_explicit_order["userId"])


class TestFoodEntryNormalizationAndFormatting(unittest.TestCase):
    def test_normalize_entry_with_foods_map_and_fallbacks(self) -> None:
        foods_map = {
            464785: {
                "id": 464785,
                "name": "Greek Yogurt, Plain",
                "measures": [{"id": 1072753, "name": "1 cup"}],
            }
        }
        entry = crono._normalize_entry(
            {
                "servingId": 555,
                "foodId": 464785,
                "measureId": 1072753,
                "grams": 210,
                "time": "08:00:00",
                "day": "2026-10-18",
            },
            foods_map=foods_map,
        )
        self.assertEqual(entry["id"], 555)
        self.assertEqual(entry["name"], "Greek Yogurt, Plain")
        self.assertEqual(entry["measure"], "1 cup")
        self.assertEqual(entry["amount"], 210)
        self.assertEqual(entry["type"], "Serving")

        bio_entry = crono._normalize_entry(
            {
                "biometricId": 777,
                "metricId": 1,
                "type": "Biometric",
                "value": 74.2,
                "unit": "kg",
                "timestamp": "07:00:00",
            }
        )
        self.assertEqual(bio_entry["id"], 777)
        self.assertEqual(bio_entry["amount"], 74.2)
        self.assertEqual(bio_entry["measure"], "kg")
        self.assertEqual(bio_entry["time"], "07:00:00")

    def test_parse_diary_response_alternate_macro_keys(self) -> None:
        raw = {
            "servings": [{"id": 1, "foodId": 2, "foodName": "Oats", "grams": 50}],
            "summary": {
                "consumed": {"calories": 190, "protein": 6.5, "carbohydrates": 34.0, "fat": 3.2},
                "burned": 250,
            },
        }
        parsed = crono.parse_diary_response("2026-10-18", raw)
        self.assertEqual(parsed["date"], "2026-10-18")
        self.assertEqual(parsed["totals"]["consumed"]["kcal"], 190)
        self.assertEqual(parsed["totals"]["consumed"]["protein_g"], 6.5)
        self.assertEqual(parsed["totals"]["consumed"]["carbs_g"], 34.0)
        self.assertEqual(parsed["totals"]["consumed"]["fat_g"], 3.2)
        self.assertEqual(parsed["totals"]["burned"], 250)
        self.assertEqual(parsed["entries"][0]["name"], "Oats")

    def test_format_diary_show_empty_and_populated(self) -> None:
        empty_buf = io.StringIO()
        with redirect_stdout(empty_buf):
            crono.format_diary_show({"date": "2026-10-18", "totals": {}, "entries": []})
        self.assertEqual(
            empty_buf.getvalue(),
            "Diary for 2026-10-18:\nEntries: (none)\n",
        )

        pop_buf = io.StringIO()
        data = {
            "date": "2026-10-18",
            "totals": {
                "consumed": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15}
            },
            "entries": [
                {
                    "servingId": 101,
                    "time": "08:30:00",
                    "type": "Serving",
                    "foodId": 464785,
                    "name": "Greek Yogurt",
                    "grams": 210,
                },
                {
                    "id": None,
                    "time": "",
                    "type": "Serving",
                    "foodId": 999,
                    "amount": 100,
                },
            ],
        }
        with redirect_stdout(pop_buf):
            crono.format_diary_show(data)
        out = pop_buf.getvalue()
        self.assertIn("Summary Consumed: kcal=500 | protein_g=40 | carbs_g=50 | fat_g=15", out)
        self.assertIn("  [101] 08:30:00 | type=Serving | foodId=464785 (Greek Yogurt) | grams=210", out)
        self.assertIn("  --:--:-- | type=Serving | foodId=999 | grams=100", out)

    def test_format_food_search_and_custom_foods(self) -> None:
        empty_search = io.StringIO()
        with redirect_stdout(empty_search):
            crono.format_food_search({"query": "xyz", "foods": []})
        self.assertEqual(empty_search.getvalue(), "No foods found for query 'xyz'.\n")

        search_buf = io.StringIO()
        with redirect_stdout(search_buf):
            crono.format_food_search(
                {
                    "query": "banana",
                    "foods": [
                        {
                            "id": 450856,
                            "name": "Banana, Fresh",
                            "source": "NCCDB",
                            "measureId": 998940,
                            "measureDisplayName": "1 medium (118g)",
                        }
                    ],
                }
            )
        self.assertEqual(
            search_buf.getvalue(),
            "- id=450856 | name=Banana, Fresh | source=NCCDB | measureId=998940 | measureDisplayName=1 medium (118g)\n",
        )

        empty_custom = io.StringIO()
        with redirect_stdout(empty_custom):
            crono.format_custom_foods({"foods": []})
        self.assertEqual(empty_custom.getvalue(), "No custom foods found.\n")

        custom_buf = io.StringIO()
        with redirect_stdout(custom_buf):
            crono.format_custom_foods(
                {"foods": [{"foodId": 888, "name": "Protein Shake", "type": "recipe"}]}
            )
        self.assertEqual(custom_buf.getvalue(), "- foodId=888: Protein Shake (recipe)\n")

    def test_format_write_result_and_print_output(self) -> None:
        dry_buf = io.StringIO()
        with redirect_stdout(dry_buf):
            crono.format_write_result({"dryRun": True, "payload": {"serving": {"foodId": 1}}})
        self.assertIn("DRY RUN", dry_buf.getvalue())
        self.assertIn('"foodId": 1', dry_buf.getvalue())

        del_buf = io.StringIO()
        with redirect_stdout(del_buf):
            crono.format_write_result({"servingId": 55, "deleted": True, "beforeCount": 3, "afterCount": 2})
        self.assertIn("Serving ID: 55", del_buf.getvalue())
        self.assertIn("Deleted: True (diary entries: 3 -> 2)", del_buf.getvalue())

        json_buf = io.StringIO()
        with redirect_stdout(json_buf):
            crono.print_output({"b": 2, "a": 1}, as_json=True, human_formatter=lambda _: None)
        self.assertEqual(json.loads(json_buf.getvalue()), {"a": 1, "b": 2})


if __name__ == "__main__":
    unittest.main()
