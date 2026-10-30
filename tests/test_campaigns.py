from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from scripts.living_portfolio.campaigns import (
    CampaignDataError,
    append_run,
    initialize_registry,
    render_metric_chart,
    render_metric_summary,
    verify_registry,
)


def sample_run(**overrides):
    record = {
        "campaign_id": "rocket-landing",
        "run_id": "rocket-landing-v1",
        "version": 1,
        "source_commit": "a" * 40,
        "raw_data_sha256": hashlib.sha256(b"temporary synthetic fixture").hexdigest(),
        "configuration_sha256": hashlib.sha256(b"temporary synthetic configuration").hexdigest(),
        "configuration_reference": "https://github.com/example/project/blob/" + "a" * 40 + "/config.json",
        "public_approval_reference": "https://github.com/example/project/pull/17",
        "approval_status": "approved",
        "observed_on": "2026-10-29",
        "data_kind": "simulation",
        "metrics": [
            {"name": "landing_rate", "value": 0.8, "unit": "ratio", "sample_count": 30},
        ],
    }
    record.update(overrides)
    if "configuration_reference" not in overrides:
        record["configuration_reference"] = (
            f"https://github.com/example/project/blob/{record['source_commit']}/config.json"
        )
    return record


class CampaignRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "campaigns"
        initialize_registry(self.root)

    def test_tampered_run_is_rejected_against_index(self):
        run = append_run(self.root, sample_run())
        path = self.root / "runs" / f"{run['run_id']}.json"
        altered = json.loads(path.read_text(encoding="utf-8"))
        altered["metrics"][0]["value"] = 0.99
        path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(CampaignDataError, "Indexed run changed"):
            verify_registry(self.root)

    def test_deleted_indexed_run_is_rejected(self):
        run = append_run(self.root, sample_run())
        (self.root / "runs" / f"{run['run_id']}.json").unlink()
        with self.assertRaisesRegex(CampaignDataError, "Campaign index/files differ"):
            verify_registry(self.root)

    def test_identical_append_is_idempotent_and_correction_updates_chart(self):
        first = sample_run()
        append_run(self.root, first)
        first_chart = render_metric_chart(self.root, "rocket-landing", "landing_rate")
        self.assertIsNotNone(first_chart)
        self.assertIn(Path(self.temp.name).resolve(), first_chart.resolve().parents)
        first_svg = first_chart.read_bytes()

        self.assertEqual(append_run(self.root, copy.deepcopy(first)), first)
        corrected = sample_run(
            run_id="rocket-landing-v2",
            version=2,
            source_commit="b" * 40,
            raw_data_sha256=hashlib.sha256(b"corrected temporary synthetic fixture").hexdigest(),
            public_approval_reference="https://github.com/example/project/pull/18",
            observed_on="2026-10-30",
            supersedes_run_id="rocket-landing-v1",
            metrics=[{"name": "landing_rate", "value": 0.9, "unit": "ratio", "sample_count": 32}],
        )
        append_run(self.root, corrected)
        chart = render_metric_chart(self.root, "rocket-landing", "landing_rate")
        self.assertIn(Path(self.temp.name).resolve(), chart.resolve().parents)
        second_svg = chart.read_bytes()
        self.assertNotEqual(first_svg, second_svg)
        self.assertEqual(second_svg, render_metric_chart(
            self.root, "rocket-landing", "landing_rate").read_bytes())
        self.assertEqual(["rocket-landing-v1", "rocket-landing-v2"],
                         [run["run_id"] for run in verify_registry(self.root)])

    def test_hyphenated_campaign_and_metric_names_have_distinct_output_paths(self):
        append_run(self.root, sample_run(
            campaign_id="a-b", run_id="a-b-v1",
            metrics=[{"name": "c", "value": 0.8, "unit": "ratio", "sample_count": 30}],
        ))
        append_run(self.root, sample_run(
            campaign_id="a", run_id="a-v1",
            metrics=[{"name": "b-c", "value": 0.9, "unit": "ratio", "sample_count": 32}],
        ))

        first = render_metric_chart(self.root, "a-b", "c")
        second = render_metric_chart(self.root, "a", "b-c")

        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    def test_long_campaign_and_metric_titles_wrap_inside_the_svg(self):
        campaign_id = "campaign-" + "a" * 71
        metric_name = "m" + "x" * 79
        append_run(self.root, sample_run(
            campaign_id=campaign_id,
            run_id="long-title-v1",
            metrics=[{"name": metric_name, "value": 0.8, "unit": "ratio", "sample_count": 30}],
        ))

        chart = render_metric_chart(self.root, campaign_id, metric_name)
        root = ElementTree.parse(chart).getroot()
        namespace = "{http://www.w3.org/2000/svg}"
        title = root.find(f"{namespace}title[@id='title']")
        title_lines = root.findall(f".//{namespace}tspan")

        self.assertEqual(title.text, f"{campaign_id}: {metric_name}")
        self.assertGreater(len(title_lines), 1)
        self.assertTrue(all(len(line.text or "") <= 32 for line in title_lines))
        self.assertGreater(int(root.attrib["height"]), 360)

    def test_metric_chart_has_light_dark_and_text_alternatives(self):
        append_run(self.root, sample_run(metrics=[{
            "name": "landing_rate",
            "value": 0.8,
            "unit": "![evidence](https://example.com/pixel.png)|`code`",
            "sample_count": 30,
        }]))

        light = render_metric_chart(self.root, "rocket-landing", "landing_rate", theme="light")
        dark = render_metric_chart(self.root, "rocket-landing", "landing_rate", theme="dark")
        summary = render_metric_summary(self.root, "rocket-landing", "landing_rate")
        light_root = ElementTree.parse(light).getroot()
        dark_root = ElementTree.parse(dark).getroot()
        summary_text = summary.read_text(encoding="utf-8")

        self.assertNotEqual(light, dark)
        self.assertNotEqual(light_root.find("{http://www.w3.org/2000/svg}rect").attrib["fill"],
                            dark_root.find("{http://www.w3.org/2000/svg}rect").attrib["fill"])
        self.assertEqual(light_root.attrib["role"], "img")
        self.assertEqual(dark_root.attrib["role"], "img")
        namespace = "{http://www.w3.org/2000/svg}"
        light_points = [point.text for point in light_root.findall(f".//{namespace}circle/{namespace}title")]
        dark_points = [point.text for point in dark_root.findall(f".//{namespace}circle/{namespace}title")]
        self.assertEqual(light_points, dark_points)
        self.assertIn("2026-10-29", summary_text)
        self.assertIn("0.8", summary_text)
        self.assertIn("30", summary_text)
        self.assertIn("Sample count", summary_text)
        self.assertIn("rocket-landing-v1", summary_text)
        self.assertIn("` ![evidence](https://example.com/pixel.png) `&#124;`` `code` ``", summary_text)
        self.assertNotIn("[Bundle provenance]", summary_text)
        self.assertEqual(light.read_bytes(), render_metric_chart(
            self.root, "rocket-landing", "landing_rate", theme="light").read_bytes())
        self.assertEqual(dark.read_bytes(), render_metric_chart(
            self.root, "rocket-landing", "landing_rate", theme="dark").read_bytes())
        self.assertEqual(summary.read_bytes(), render_metric_summary(
            self.root, "rocket-landing", "landing_rate").read_bytes())

    def test_extreme_finite_values_keep_chart_coordinates_finite(self):
        append_run(self.root, sample_run(
            run_id="extreme-negative-v1",
            metrics=[{"name": "landing_rate", "value": -1e308, "unit": "ratio",
                      "sample_count": 30, "allow_negative": True}],
        ))
        append_run(self.root, sample_run(
            run_id="extreme-positive-v1",
            source_commit="b" * 40,
            raw_data_sha256=hashlib.sha256(b"extreme positive fixture").hexdigest(),
            configuration_sha256=hashlib.sha256(b"extreme positive configuration").hexdigest(),
            public_approval_reference="https://github.com/example/project/pull/18",
            observed_on="2026-10-30",
            metrics=[{"name": "landing_rate", "value": 1e308, "unit": "ratio",
                      "sample_count": 30, "allow_negative": True}],
        ))
        chart = render_metric_chart(self.root, "rocket-landing", "landing_rate")
        svg_text = chart.read_text(encoding="utf-8").casefold()

        self.assertNotIn("nan", svg_text)
        self.assertNotIn("inf", svg_text)

    def test_empty_registry_emits_no_result_chart_and_unapproved_run_is_refused(self):
        chart = Path(self.temp.name) / "empty.svg"
        self.assertIsNone(render_metric_chart(self.root, "rocket-landing", "landing_rate", chart))
        self.assertFalse(chart.exists())
        pending = sample_run(approval_status="pending")
        with self.assertRaisesRegex(CampaignDataError, "explicitly approved"):
            append_run(self.root, pending)
        self.assertEqual([], verify_registry(self.root))

    def test_invalid_approval_and_negative_metric_are_refused(self):
        with self.assertRaisesRegex(CampaignDataError, "public HTTPS URL"):
            append_run(self.root, sample_run(public_approval_reference="http://localhost/approval"))
        negative = sample_run(metrics=[{"name": "landing_rate", "value": -0.1,
                                        "unit": "ratio", "sample_count": 30}])
        with self.assertRaisesRegex(CampaignDataError, "cannot be negative"):
            append_run(self.root, negative)
        self.assertEqual([], verify_registry(self.root))

    def test_campaign_configuration_digest_and_public_reference_are_required(self):
        with self.assertRaisesRegex(CampaignDataError, "configuration_sha256"):
            append_run(self.root, sample_run(configuration_sha256="not-a-digest"))
        with self.assertRaisesRegex(CampaignDataError, "public HTTPS URL"):
            append_run(self.root, sample_run(configuration_reference="http://localhost/config.json"))
        self.assertEqual([], verify_registry(self.root))

    def test_non_finite_metric_and_invalid_correction_reference_are_refused(self):
        invalid_metric = sample_run(metrics=[{"name": "landing_rate", "value": float("nan"),
                                              "unit": "ratio", "sample_count": 30}])
        oversized_integer = sample_run(metrics=[{"name": "landing_rate", "value": 10 ** 400,
                                                  "unit": "ratio", "sample_count": 30}])
        with self.assertRaisesRegex(CampaignDataError, "finite numeric"):
            append_run(self.root, invalid_metric)
        with self.assertRaisesRegex(CampaignDataError, "finite numeric"):
            append_run(self.root, oversized_integer)
        correction = sample_run(run_id="rocket-landing-v2", version=2,
                                supersedes_run_id="missing-v1")
        with self.assertRaisesRegex(CampaignDataError, "superseded run"):
            append_run(self.root, correction)
        self.assertEqual([], verify_registry(self.root))


if __name__ == "__main__":
    unittest.main()
