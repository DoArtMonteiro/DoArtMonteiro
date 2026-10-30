from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from scripts.living_portfolio.preview import (
    DEFAULT_REGISTRY,
    PreviewError,
    _asset_hashes,
    _is_link_or_junction,
    build_preview,
    load_allowlist,
)
from scripts.living_portfolio.campaigns import CampaignDataError, append_run, initialize_registry, verify_registry


ROOT = Path(__file__).resolve().parents[1]
TEST_OUTPUT_ROOT = ROOT / "previews" / "living-portfolio-tests"


def project_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "projects": [{
            "repository": "DoArtMonteiro/demo",
            "url": "https://github.com/DoArtMonteiro/demo",
            "default_branch": "main",
            "latest_default_branch_commit": None,
            "latest_release": None,
            "closed_milestones": [],
            "pull_requests": [],
            "open_issue_count": 0,
            "recent_open_issues": [],
            "latest_default_branch_workflow_run": None,
        }],
        "timeline": [],
    }


def write_contributions(path: Path, changed: bool = False) -> None:
    start = date(2025, 10, 31)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("date", "contributions"))
        for index in range(365):
            writer.writerow(((start + timedelta(days=index)).isoformat(), int(changed and index == 1)))


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_allowlist_is_exact_bounded_and_owner_scoped(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            path = Path(temp_dir) / "repositories.json"
            path.write_text(json.dumps({"schema_version": 1, "repositories": [
                "DoArtMonteiro/beta", "DoArtMonteiro/alpha",
            ]}), encoding="utf-8")
            self.assertEqual(load_allowlist(path), ["DoArtMonteiro/alpha", "DoArtMonteiro/beta"])
            path.write_text(json.dumps({"schema_version": 1, "repositories": ["someone-else/repo"]}), encoding="utf-8")
            with self.assertRaises(PreviewError):
                load_allowlist(path)

    def test_default_campaign_registry_is_valid_and_empty(self) -> None:
        self.assertEqual([], verify_registry(DEFAULT_REGISTRY))

    def test_preview_is_content_addressed_and_source_change_creates_new_version(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            source = root / "contributions.csv"
            output = root / "published-assets"
            write_contributions(source)
            first = build_preview(source, project_snapshot(), output, source_scope="selected_public_repo_commits")
            first_pointer = (output / "current.json").read_bytes()
            self.assertEqual(first["manifest"]["campaign_state"], "no approved campaign data")
            self.assertEqual(first["manifest"]["campaign_chart_count"], 0)
            provenance = json.loads((Path(first["bundle"]) / "provenance.json").read_text(encoding="utf-8"))
            commit_authentication = provenance["sources"]["public_commits"]["authentication"]
            self.assertIn("read-only github actions github_token", commit_authentication.casefold())
            self.assertIn("contents:read", commit_authentication)
            self.assertIn("api.github.com", commit_authentication)
            self.assertIn("reviewed owner/repository allowlist", commit_authentication)
            self.assertIn("no read:user scope", commit_authentication)
            self.assertIn(
                "uploads a seven-day GitHub Actions diagnostic artifact",
                provenance["refresh"]["failure_behaviour"],
            )
            self.assertIn("does not update the README assets", provenance["refresh"]["failure_behaviour"])
            graphql_status = provenance["sources"]["github_public_contributions"]
            self.assertEqual(graphql_status["state"], "not included in this snapshot")
            self.assertNotIn("fixture", json.dumps(graphql_status).casefold())
            self.assertIn("PR #4 diagnostic preview", provenance["refresh"]["workflow_mode"])
            self.assertIn("workflow_dispatch", provenance["refresh"]["workflow_mode"])
            self.assertEqual(Path(first["bundle"]).name, first["version"][:24])
            self.assertEqual(
                json.loads(first_pointer)["manifest"],
                f"versions/{first['version'][:24]}/manifest.json",
            )
            self.assertTrue((Path(first["bundle"]) / "calendar" / "current.json").is_file())
            self.assertTrue((Path(first["bundle"]) / "projects" / "projects-dashboard-light.svg").is_file())
            second = build_preview(source, project_snapshot(), output, source_scope="selected_public_repo_commits")
            self.assertEqual(first["version"], second["version"])
            self.assertEqual(first_pointer, (output / "current.json").read_bytes())
            write_contributions(source, changed=True)
            third = build_preview(source, project_snapshot(), output, source_scope="selected_public_repo_commits")
            self.assertNotEqual(first["version"], third["version"])
            self.assertTrue((output / "versions" / first["version"][:24]).is_dir())
            self.assertEqual(json.loads((output / "current.json").read_text(encoding="utf-8"))["version"], third["version"])
            self.assertEqual(
                sorted(path.name for path in (output / "versions").iterdir() if path.is_dir() and not path.name.startswith(".")),
                sorted([first["version"][:24], third["version"][:24]]),
            )

    def test_graphql_calendar_is_separate_animated_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            commits = root / "selected-repository-commits.csv"
            github = root / "github-public-contributions.csv"
            output = root / "preview"
            write_contributions(commits)
            write_contributions(github)

            first = build_preview(
                commits, project_snapshot(), output,
                source_scope="selected_public_repo_commits",
                github_contribution_csv=github,
            )
            first_pointer = (output / "current.json").read_bytes()
            bundle = Path(first["bundle"])
            profile_calendar = bundle / "github-calendar"
            provenance = json.loads((bundle / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(first["manifest"]["calendar_summary"]["total_public_commits"], 0)
            self.assertEqual(first["manifest"]["github_contribution_summary"]["total_public_contributions"], 0)
            self.assertTrue((profile_calendar / "calendar-light-animated.gif").is_file())
            self.assertTrue((profile_calendar / "calendar-dark-animated.gif").is_file())
            self.assertIn("public contribution calendar", provenance["sources"]["github_public_contributions"]["scope"].casefold())
            self.assertIn("no read:user scope", provenance["sources"]["github_public_contributions"]["authentication"])
            self.assertIn("commits across", provenance["sources"]["public_commits"]["animation"]["alt_text"])
            self.assertIn("contributions across", provenance["sources"]["github_public_contributions"]["animation"]["alt_text"])

            same = build_preview(
                commits, project_snapshot(), output,
                source_scope="selected_public_repo_commits",
                github_contribution_csv=github,
            )
            self.assertEqual(first["version"], same["version"])
            self.assertEqual(first_pointer, (output / "current.json").read_bytes())

            write_contributions(github, changed=True)
            changed = build_preview(
                commits, project_snapshot(), output,
                source_scope="selected_public_repo_commits",
                github_contribution_csv=github,
            )
            self.assertNotEqual(first["version"], changed["version"])
            self.assertEqual(changed["manifest"]["github_contribution_summary"]["total_public_contributions"], 1)
            self.assertEqual(changed["manifest"]["calendar_summary"]["total_public_commits"], 0)

            pointer = (output / "current.json").read_bytes()
            github.write_text("date,contributions\n2025-10-31,-1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_preview(
                    commits, project_snapshot(), output,
                    source_scope="selected_public_repo_commits",
                    github_contribution_csv=github,
                )
            self.assertEqual((output / "current.json").read_bytes(), pointer)

    def test_failed_generation_keeps_last_good_pointer_and_bundle(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            source = root / "contributions.csv"
            output = root / "published-assets"
            write_contributions(source)
            first = build_preview(source, project_snapshot(), output, source_scope="selected_public_repo_commits")
            pointer = (output / "current.json").read_bytes()
            bad_project_snapshot = project_snapshot()
            bad_project_snapshot["projects"][0]["url"] = "https://invalid.example/repo"
            with self.assertRaises(ValueError):
                build_preview(source, bad_project_snapshot, output, source_scope="selected_public_repo_commits")
            self.assertEqual((output / "current.json").read_bytes(), pointer)
            self.assertTrue((Path(first["bundle"]) / "manifest.json").is_file())
            self.assertEqual(
                [path.name for path in (output / "versions").iterdir() if path.is_dir() and not path.name.startswith(".")],
                [first["version"][:24]],
            )

    def test_output_path_must_stay_in_the_allowlisted_root(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            source = Path(temp_dir) / "contributions.csv"
            write_contributions(source)
            outside = Path(tempfile.gettempdir()) / f"untrusted-living-portfolio-{uuid.uuid4().hex}"
            with self.assertRaises(PreviewError):
                build_preview(source, project_snapshot(), outside, source_scope="selected_public_repo_commits")
            self.assertFalse(outside.exists())

    def test_windows_reparse_point_is_detected_without_is_junction_support(self) -> None:
        path = Path("junction-candidate")
        path_type = type(path)
        reparse_point = SimpleNamespace(st_file_attributes=0x0400)
        with patch.object(path_type, "is_symlink", return_value=False):
            with patch.object(path_type, "is_junction", None, create=True):
                with patch.object(path_type, "lstat", return_value=reparse_point):
                    self.assertTrue(_is_link_or_junction(path))

    def test_path_inspection_errors_fail_closed(self) -> None:
        path = Path("inaccessible-candidate")
        path_type = type(path)
        with patch.object(path_type, "is_symlink", return_value=False):
            with patch.object(path_type, "is_junction", None, create=True):
                with patch.object(path_type, "lstat", side_effect=PermissionError("access denied")):
                    with self.assertRaises(PreviewError):
                        _is_link_or_junction(path)

    def test_asset_hashes_rejects_windows_reparse_points(self) -> None:
        directory = Path("bundle-candidate")
        junction = directory / "nested-junction"
        path_type = type(directory)
        reparse_point = SimpleNamespace(st_file_attributes=0x0400)
        with patch.object(path_type, "rglob", return_value=[junction]):
            with patch.object(path_type, "is_symlink", return_value=False):
                with patch.object(path_type, "is_junction", None, create=True):
                    with patch.object(path_type, "lstat", return_value=reparse_point):
                        with self.assertRaises(PreviewError):
                            _asset_hashes(directory)

    def _create_directory_symlink_or_skip(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except NotImplementedError:
            if os.name == "nt":
                self.skipTest("Directory symlinks are unavailable on this Windows host")
            raise
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) in {1, 5, 1314}:
                self.skipTest("This Windows host does not grant directory symlink creation")
            raise

    def test_symlinked_output_and_versions_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            source = root / "contributions.csv"
            write_contributions(source)
            external_target = Path(tempfile.gettempdir()) / f"untrusted-living-portfolio-{uuid.uuid4().hex}"
            linked_output = root / "linked-output"
            self._create_directory_symlink_or_skip(linked_output, external_target)
            try:
                with self.assertRaises(PreviewError):
                    build_preview(source, project_snapshot(), linked_output / "preview", source_scope="selected_public_repo_commits")
            finally:
                linked_output.unlink()

            output = root / "output"
            output.mkdir()
            versions = output / "versions"
            self._create_directory_symlink_or_skip(versions, external_target)
            with self.assertRaises(PreviewError):
                build_preview(source, project_snapshot(), output, source_scope="selected_public_repo_commits")
            self.assertTrue(versions.is_symlink())
            versions.unlink()

    def test_broken_campaign_registry_fails_without_moving_current_pointer(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            source = root / "contributions.csv"
            output = root / "published-assets"
            write_contributions(source)
            first = build_preview(source, project_snapshot(), output, source_scope="selected_public_repo_commits")
            pointer = (output / "current.json").read_bytes()
            registry = root / "campaigns"
            (registry / "runs").mkdir(parents=True)
            (registry / "runs" / "orphan.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(CampaignDataError):
                build_preview(
                    source, project_snapshot(), output, source_scope="selected_public_repo_commits",
                    campaign_registry=registry,
                )
            self.assertEqual((output / "current.json").read_bytes(), pointer)
            self.assertTrue((Path(first["bundle"]) / "manifest.json").is_file())

    def test_initialized_empty_campaign_registry_generates_no_chart(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            source = root / "contributions.csv"
            write_contributions(source)
            registry = root / "campaigns"
            initialize_registry(registry)
            result = build_preview(
                source, project_snapshot(), root / "published-assets", source_scope="selected_public_repo_commits",
                campaign_registry=registry,
            )
            self.assertEqual(result["manifest"]["campaign_state"], "no approved campaign data")
            self.assertEqual(result["manifest"]["campaign_chart_count"], 0)
            self.assertEqual(list((Path(result["bundle"]) / "campaigns").glob("*.svg")), [])

    def test_hyphenated_campaign_and_metric_pairs_keep_both_preview_charts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp_dir:
            root = Path(temp_dir)
            source = root / "contributions.csv"
            write_contributions(source)
            registry = root / "campaigns"
            initialize_registry(registry)
            for campaign_id, run_id, metric_name in (("a-b", "a-b-v1", "c"), ("a", "a-v1", "b-c")):
                append_run(registry, {
                    "campaign_id": campaign_id,
                    "run_id": run_id,
                    "version": 1,
                    "source_commit": "a" * 40,
                    "raw_data_sha256": hashlib.sha256(run_id.encode()).hexdigest(),
                    "configuration_sha256": hashlib.sha256(b"synthetic configuration").hexdigest(),
                    "configuration_reference": "https://github.com/DoArtMonteiro/demo/blob/" + "a" * 40 + "/config.json",
                    "public_approval_reference": "https://github.com/DoArtMonteiro/demo/pull/1",
                    "approval_status": "approved",
                    "observed_on": "2026-10-29",
                    "data_kind": "simulation",
                    "metrics": [{"name": metric_name, "value": 0.8, "unit": "ratio", "sample_count": 30}],
                })

            result = build_preview(
                source, project_snapshot(), root / "published-assets", source_scope="selected_public_repo_commits",
                campaign_registry=registry,
            )
            campaigns_dir = Path(result["bundle"]) / "campaigns"
            provenance = json.loads(
                (Path(result["bundle"]) / "provenance.json").read_text(encoding="utf-8")
            )

            self.assertEqual(result["manifest"]["campaign_chart_count"], 4)
            self.assertEqual(
                set(provenance["sources"]["campaigns"]["charts"]),
                {"a-b/c-light.svg", "a-b/c-dark.svg", "a/b-c-light.svg", "a/b-c-dark.svg"},
            )
            self.assertEqual(
                set(provenance["sources"]["campaigns"]["text_alternatives"]),
                {"a-b/c.md", "a/b-c.md"},
            )
            for evidence in provenance["sources"]["campaigns"]["evidence"]:
                self.assertTrue(evidence["configuration_sha256"])
                self.assertTrue(evidence["configuration_reference"].startswith("https://"))
            self.assertTrue((campaigns_dir / "a-b" / "c-light.svg").is_file())
            self.assertTrue((campaigns_dir / "a-b" / "c-dark.svg").is_file())
            self.assertTrue((campaigns_dir / "a-b" / "c.md").is_file())
            self.assertTrue((campaigns_dir / "a" / "b-c-light.svg").is_file())
            self.assertTrue((campaigns_dir / "a" / "b-c-dark.svg").is_file())
            self.assertTrue((campaigns_dir / "a" / "b-c.md").is_file())
            summary_path = campaigns_dir / "a-b" / "c.md"
            self.assertIn("../../provenance.json", summary_path.read_text(encoding="utf-8"))
            resolved_provenance = (summary_path.parent / "../../provenance.json").resolve()
            self.assertEqual(resolved_provenance, (Path(result["bundle"]) / "provenance.json").resolve())
            self.assertTrue(resolved_provenance.is_file())


if __name__ == "__main__":
    unittest.main()
