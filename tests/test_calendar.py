from __future__ import annotations

import csv
from datetime import date, timedelta
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.living_portfolio.calendar import (
    CalendarDataError,
    OUTPUT_NAMES,
    render_calendar,
)


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def write_calendar_fixture(path: Path) -> None:
    start = date(2025, 10, 26)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("date", "contributions"))
        for index in range(370):
            count = (index % 4) + 1 if index % 2 == 0 else 0
            writer.writerow(((start + timedelta(days=index)).isoformat(), count))


class CalendarRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "daily.csv"
        self.output = self.root / "rendered"
        write_calendar_fixture(self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def render(self):
        return render_calendar(self.source, self.output, "selected_public_repo_commits")

    def bundle_bytes(self, result) -> dict[str, bytes]:
        return {name: (result["bundle"] / name).read_bytes() for name in OUTPUT_NAMES}

    def test_370_day_fixture_renders_complete_asset_bundle(self) -> None:
        result = self.render()
        self.assertEqual(set(OUTPUT_NAMES), {path.name for path in result["bundle"].iterdir()})
        manifest = json.loads(result["calendar-manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["observed_interval"]["dated_days"], 370)
        self.assertEqual(manifest["commit_activity"]["total_commits"], 369)
        self.assertEqual(manifest["commit_activity"]["active_days"], 185)
        self.assertEqual(manifest["commit_activity"]["zero_days"], 185)
        self.assertEqual(manifest["observed_interval"]["start"], "2025-10-26")
        self.assertEqual(manifest["observed_interval"]["end"], "2026-10-30")
        self.assertEqual(manifest["source_snapshot_date"], "2026-10-30")
        self.assertNotIn("capture_date", manifest)
        self.assertEqual(manifest["source_scope"], "selected_public_repo_commits")
        self.assertIn("subset of GitHub activity", manifest["source_scope_description"])
        self.assertEqual(manifest["source_snapshot_date_basis"], "Latest dated row in the validated CSV.")
        self.assertEqual(manifest["level_intervals"], [
            {"level": 0, "minimum": 0, "maximum": 0},
            {"level": 1, "minimum": 1, "maximum": 5},
            {"level": 2, "minimum": 6, "maximum": 12},
            {"level": 3, "minimum": 13, "maximum": 24},
            {"level": 4, "minimum": 25, "maximum": None},
        ])
        self.assertEqual(set(manifest["artifact_sha256"]), set(OUTPUT_NAMES) - {"calendar-manifest.json"})

        with result["calendar-normalized"].open(encoding="utf-8") as handle:
            normalised = list(csv.DictReader(handle))
        self.assertEqual(len(normalised), 370)
        self.assertEqual(normalised[0], {"date": "2025-10-26", "contributions": "1"})
        self.assertEqual(normalised[-1], {"date": "2026-10-30", "contributions": "0"})

        for name in OUTPUT_NAMES[:4]:
            root = ET.parse(result["bundle"] / name).getroot()
            self.assertEqual(len(root.findall(".//svg:g[@class]", SVG_NS)), 370)
        self.assertNotIn("generated_at", manifest)

    def test_second_run_keeps_all_outputs_and_pointer_unchanged(self) -> None:
        first = self.render()
        files = [first["current"], *(first["bundle"] / name for name in OUTPUT_NAMES)]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}

        second = self.render()

        self.assertEqual(first["bundle"], second["bundle"])
        for path, (content, modified_ns) in before.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, modified_ns)

    def test_changed_input_publishes_a_consistent_new_bundle(self) -> None:
        first = self.render()
        old_content = self.bundle_bytes(first)
        with self.source.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["contributions"] = "3"
        with self.source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("date", "contributions"), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

        second = self.render()
        new_content = self.bundle_bytes(second)

        self.assertNotEqual(first["bundle"], second["bundle"])
        for name in OUTPUT_NAMES:
            self.assertNotEqual(old_content[name], new_content[name], name)
        manifest = json.loads(second["calendar-manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["commit_activity"]["total_commits"], 371)
        for name in OUTPUT_NAMES[:4]:
            root = ET.parse(second["bundle"] / name).getroot()
            cell = root.find('.//svg:g[@data-date="2025-10-26"]', SVG_NS)
            self.assertIsNotNone(cell, name)
            self.assertEqual(cell.attrib["data-count"], "3", name)

    def test_invalid_input_retains_the_last_complete_bundle_and_pointer(self) -> None:
        result = self.render()
        before = self.bundle_bytes(result)
        pointer = result["current"].read_bytes()
        pointer_mtime = result["current"].stat().st_mtime_ns
        self.source.write_text("date,contributions\n2025-10-26,-1\n", encoding="utf-8")

        with self.assertRaises(CalendarDataError):
            self.render()

        self.assertEqual(self.bundle_bytes(result), before)
        self.assertEqual(result["current"].read_bytes(), pointer)
        self.assertEqual(result["current"].stat().st_mtime_ns, pointer_mtime)

    def test_count_and_date_validation(self) -> None:
        invalid_csv = (
            "day,count\n2025-10-26,0\n",
            "date,contributions\n2025-10-26,not-a-count\n",
            "date,contributions\n2025-10-26,-2\n",
            "date,contributions\n2025-10-26,0\n2025-10-28,1\n",
            "date,contributions\n2025-10-26,0\n2025-10-26,1\n",
            "date,contributions\n2025-10-26,0\n2025-10-27,1\n2025-10-26,2\n",
            "date,contributions\n26/10/2025,1\n",
            "date,contributions\n",
        )
        for index, content in enumerate(invalid_csv):
            with self.subTest(case=index):
                candidate = self.root / f"invalid-{index}.csv"
                candidate.write_text(content, encoding="utf-8")
                with self.assertRaises(CalendarDataError):
                    render_calendar(candidate, self.root / f"output-{index}")
                self.assertFalse((self.root / f"output-{index}").exists())

    def test_selected_repository_commit_scope_is_explicit(self) -> None:
        public_source = self.root / "public-repository-commits.csv"
        public_source.write_text(
            "date,contributions\n2026-10-29,0\n2026-10-30,1\n", encoding="utf-8"
        )
        result = render_calendar(public_source, self.output, source_scope="selected_public_repo_commits")
        manifest = json.loads(result["calendar-manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_scope"], "selected_public_repo_commits")
        self.assertIn("subset of GitHub activity", manifest["source_scope_description"])
        self.assertEqual(manifest["source_file"], "public-repository-commits.csv")
        self.assertEqual(manifest["commit_activity"]["total_commits"], 1)
        svg = result["calendar-accessible"].read_text(encoding="utf-8")
        self.assertIn("Source scope: Distinct commits linked to DoArtMonteiro", svg)

    def test_github_graphql_scope_is_not_labelled_as_commits(self) -> None:
        public_source = self.root / "github-public-contributions.csv"
        public_source.write_text(
            "date,contributions\n2026-10-29,2\n2026-10-30,1\n", encoding="utf-8"
        )
        result = render_calendar(public_source, self.root / "github-calendar",
                                 source_scope="github_public_contributions")
        manifest = json.loads(result["calendar-manifest"].read_text(encoding="utf-8"))

        self.assertEqual(manifest["source_scope"], "github_public_contributions")
        self.assertEqual(manifest["contribution_activity"]["total_contributions"], 3)
        self.assertNotIn("commit_activity", manifest)
        self.assertIn("public", manifest["source_scope_description"].casefold())
        self.assertIn("GitHub public contributions", result["calendar-isometric"].read_text(encoding="utf-8"))
        self.assertIn("GitHub public contributions, daily values",
                      result["calendar-accessible"].read_text(encoding="utf-8"))

    def test_snapshot_date_is_derived_from_the_latest_validated_csv_date(self) -> None:
        with self.source.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["date"] = (date.fromisoformat(row["date"]) + timedelta(days=1)).isoformat()
        with self.source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("date", "contributions"), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

        result = self.render()
        manifest = json.loads(result["calendar-manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_snapshot_date"], "2026-10-31")
        self.assertIn("Data through 31 Oct 2026", result["calendar-light"].read_text(encoding="utf-8"))

    def test_theme_levels_match_and_accessible_svg_has_title_and_description(self) -> None:
        result = self.render()
        roots = {name: ET.parse(result["bundle"] / name).getroot()
                 for name in OUTPUT_NAMES[:4]}

        def cells(root):
            return {node.attrib["data-date"]: node.attrib
                    for node in root.findall(".//svg:g", SVG_NS)
                    if "data-date" in node.attrib}

        expected = cells(roots["calendar-light.svg"])
        self.assertEqual(len(expected), 370)
        for name in ("calendar-dark.svg", "calendar-accessible.svg", "calendar-isometric.svg"):
            compared = cells(roots[name])
            self.assertEqual(set(compared), set(expected))
            for day, attrs in expected.items():
                self.assertEqual(compared[day]["data-count"], attrs["data-count"])
                self.assertEqual(compared[day]["data-level"], attrs["data-level"])

        accessible = roots["calendar-accessible.svg"]
        self.assertIsNotNone(accessible.find("svg:title", SVG_NS))
        self.assertIsNotNone(accessible.find("svg:desc", SVG_NS))
        self.assertIn("including zero-count days", accessible.find("svg:desc", SVG_NS).text)

    def test_isometric_cells_are_prisms_with_linear_height(self) -> None:
        result = self.render()
        root = ET.parse(result["calendar-isometric"]).getroot()
        cells = {node.attrib["data-date"]: node for node in root.findall('.//svg:g[@data-date]', SVG_NS)}
        self.assertEqual(len(cells), 370)
        self.assertEqual(cells["2026-10-27"].attrib["data-count"], "3")
        self.assertEqual(cells["2026-10-27"].attrib["data-height"], "9")
        self.assertEqual(len(cells["2026-10-27"].findall("svg:polygon", SVG_NS)), 3)
        self.assertEqual(cells["2025-10-26"].attrib["data-height"], "3")
        self.assertEqual(len(cells["2025-10-26"].findall("svg:polygon", SVG_NS)), 3)

    def test_high_daily_count_scales_to_fit_the_isometric_canvas(self) -> None:
        original = self.source.read_text(encoding="utf-8")
        self.source.write_text(original.replace("2025-10-26,1\n", "2025-10-26,100\n", 1), encoding="utf-8")

        result = self.render()
        root = ET.parse(result["calendar-isometric"]).getroot()
        cell = next(node for node in root.findall(".//svg:g[@data-date]", SVG_NS)
                    if node.attrib["data-date"] == "2025-10-26")
        manifest = json.loads(result["calendar-manifest"].read_text(encoding="utf-8"))

        self.assertEqual(cell.attrib["data-height"], "75")
        self.assertEqual(manifest["isometric_projection"]["maximum_cell_height"], 75)
        self.assertEqual(manifest["isometric_projection"]["vertical_height_per_contribution"], 0.75)
        for polygon in root.findall(".//svg:polygon", SVG_NS):
            for point in polygon.attrib["points"].split():
                self.assertGreaterEqual(float(point.split(",")[1]), 0)


if __name__ == "__main__":
    unittest.main()
