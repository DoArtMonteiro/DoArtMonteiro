from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from scripts.living_portfolio.project_visuals import (
    ProjectVisualError,
    accessible_markdown,
    render_dashboard,
    render_mobile_dashboard,
    write_dashboard,
)


def sample_snapshot(*, hostile: bool = False, changed: bool = False) -> dict[str, Any]:
    alpha = "DoArtMonteiro/alpha"
    beta = "DoArtMonteiro/beta"
    title = '# [release](javascript:alert(1)) <script>alert("x")</script> &' if hostile else "A public release"
    alpha_commit = {
        "sha": ("c" if changed else "a") * 40,
        "url": f"https://github.com/{alpha}/commit/{('c' if changed else 'a') * 40}",
        "committed_at": "2026-10-28T10:00:00Z",
    }
    projects = [
        {
            "repository": alpha, "url": f"https://github.com/{alpha}", "default_branch": "main",
            "latest_default_branch_commit": alpha_commit,
            "latest_release": {"tag": "v1.0", "title": title, "url": f"https://github.com/{alpha}/releases/tag/v1.0", "published_at": "2026-10-27T10:00:00Z"},
            "closed_milestones": [{"number": 1}],
            "open_issue_count": 2,
            "recent_open_issues": [{
                "number": 8, "title": "Open issue", "url": f"https://github.com/{alpha}/issues/8",
                "updated_at": "2026-10-28T11:00:00Z",
            }],
            "latest_default_branch_workflow_run": {
                "name": "CI", "status": "completed", "conclusion": "success",
                "head_sha": "d" * 40, "url": f"https://github.com/{alpha}/actions/runs/42",
                "created_at": "2026-10-28T12:00:00Z",
            },
            "pull_requests": [
                {"number": 1, "status": "draft", "title": "Draft proposal"},
                {"number": 2, "status": "open", "title": "Open proposal"},
                {"number": 3, "status": "merged", "title": "Merged change"},
                {"number": 4, "status": "closed", "title": "Closed without merge"},
            ],
        },
        {
            "repository": beta, "url": f"https://github.com/{beta}", "default_branch": "trunk",
            "latest_default_branch_commit": None, "latest_release": None,
            "closed_milestones": [], "open_issue_count": 0, "recent_open_issues": [],
            "latest_default_branch_workflow_run": None, "pull_requests": [],
        },
    ]
    timeline = [
        {"repository": alpha, "kind": "release", "occurred_at": "2026-10-27T10:00:00Z", "title": title,
         "url": f"https://github.com/{alpha}/releases/tag/v1.0"},
        {"repository": alpha, "kind": "default_branch_commit", "occurred_at": "2026-10-28T10:00:00Z",
         "sha": alpha_commit["sha"], "url": alpha_commit["url"]},
    ]
    return {"schema_version": 1, "projects": projects, "timeline": timeline}


def text_content(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [element.text or "" for element in root.iter() if element.tag.endswith("text")]


class ProjectVisualTests(unittest.TestCase):
    def test_light_dark_renderings_share_content_and_have_accessible_descriptions(self) -> None:
        snapshot = sample_snapshot()
        light = render_dashboard(snapshot, "light")
        dark = render_dashboard(snapshot, "dark")
        self.assertEqual(text_content(light), text_content(dark))
        for svg in (light, dark):
            root = ET.fromstring(svg)
            self.assertEqual(root.attrib.get("role"), "img")
            self.assertEqual(root.attrib.get("aria-labelledby"), "title desc")
            self.assertIn("does not infer project maturity", "".join(root.itertext()))
            self.assertIn("profile repository records portfolio refresh runs separately", svg)
            self.assertIn("Draft 1", svg)
            self.assertIn("Open 1", svg)
            self.assertIn("Merged 1", svg)
            self.assertIn("Closed unmerged 1", svg)
            self.assertIn("Open issues · 2", svg)
            self.assertIn("Latest workflow · CI · success", svg)

    def test_mobile_renderings_are_narrow_readable_and_theme_consistent(self) -> None:
        snapshot = sample_snapshot()
        light = render_mobile_dashboard(snapshot, "light")
        dark = render_mobile_dashboard(snapshot, "dark")
        self.assertEqual(text_content(light), text_content(dark))
        root = ET.fromstring(light)
        self.assertEqual(root.attrib.get("role"), "img")
        self.assertEqual(root.attrib.get("viewBox", "").split()[2], "360")
        self.assertEqual(sum(item.text == "alpha" for item in root.iter() if item.tag.endswith("text")), 1)
        self.assertIn("Open issues · 2 · Closed milestones · 1", light)
        self.assertIn("PRs · 1 draft / 1 open / 1 merged / 1 closed", light)
        self.assertIn("Latest workflow · success", light)
        self.assertIn("linked Markdown report provides the full timeline", "".join(root.itertext()))
        self.assertIn('font-size="12"', light)

    def test_untrusted_release_text_is_escaped(self) -> None:
        svg = render_dashboard(sample_snapshot(hostile=True), "light")
        ET.fromstring(svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertNotIn("<script>", svg)

    def test_mobile_dashboard_escapes_release_tags(self) -> None:
        snapshot = sample_snapshot()
        snapshot["projects"][0]["latest_release"]["tag"] = "<script>"
        svg = render_mobile_dashboard(snapshot, "dark")
        ET.fromstring(svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertNotIn("<script>", svg)

    def test_accessible_summary_contains_clickable_repository_links(self) -> None:
        summary = accessible_markdown(sample_snapshot())
        self.assertIn("[DoArtMonteiro/alpha](https://github.com/DoArtMonteiro/alpha)", summary)
        self.assertIn("1 draft, 1 open, 1 merged, 1 closed without merge", summary)
        self.assertIn("- Open issues: 2.", summary)
        self.assertIn("[CI: success](https://github.com/DoArtMonteiro/alpha/actions/runs/42)", summary)
        self.assertIn("does not infer project maturity", summary)
        self.assertIn("default-branch commit", summary)
        self.assertIn("release", summary)

    def test_timeline_markdown_rejects_external_or_script_urls(self) -> None:
        snapshot = sample_snapshot()
        snapshot["timeline"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(ProjectVisualError):
            accessible_markdown(snapshot)

    def test_timeline_text_escapes_untrusted_titles(self) -> None:
        summary = accessible_markdown(sample_snapshot(hostile=True))
        self.assertIn(r"\<script\>", summary)

    def test_release_tag_cannot_change_accessible_markdown_link(self) -> None:
        snapshot = sample_snapshot()
        snapshot["projects"][0]["latest_release"]["tag"] = "v1](https://evil.example)"
        summary = accessible_markdown(snapshot)
        self.assertIn(r"v1\]\(https://evil\.example\)", summary)
        self.assertNotIn("](https://evil.example)", summary)

    def test_event_url_parentheses_are_encoded_for_markdown_destinations(self) -> None:
        snapshot = sample_snapshot()
        original_url = "https://github.com/DoArtMonteiro/alpha/releases/tag/v1)tail"
        snapshot["projects"][0]["latest_release"]["url"] = original_url
        snapshot["timeline"][0]["url"] = original_url
        commit_sha = snapshot["projects"][0]["latest_default_branch_commit"]["sha"]
        original_commit_url = f"https://github.com/DoArtMonteiro/alpha/commit/{commit_sha})tail"
        snapshot["projects"][0]["latest_default_branch_commit"]["url"] = original_commit_url
        snapshot["timeline"][1]["url"] = original_commit_url

        summary = accessible_markdown(snapshot)
        encoded_url = "https://github.com/DoArtMonteiro/alpha/releases/tag/v1%29tail"
        encoded_commit_url = f"https://github.com/DoArtMonteiro/alpha/commit/{commit_sha}%29tail"

        self.assertIn(f"]({encoded_url})", summary)
        self.assertNotIn(f"]({original_url})", summary)
        self.assertIn(f"]({encoded_commit_url})", summary)
        self.assertNotIn(f"]({original_commit_url})", summary)
        self.assertEqual(unquote(encoded_url), original_url)
        self.assertEqual(unquote(encoded_commit_url), original_commit_url)

    def test_write_is_idempotent_and_changed_data_updates_only_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = root / "projects.json"
            snapshot.write_text(json.dumps(sample_snapshot()), encoding="utf-8")
            outputs = write_dashboard(snapshot, root / "out")
            first = {path.name: path.read_bytes() for path in outputs.values()}
            write_dashboard(snapshot, root / "out")
            second = {path.name: path.read_bytes() for path in outputs.values()}
            self.assertEqual(first, second)
            snapshot.write_text(json.dumps(sample_snapshot(changed=True)), encoding="utf-8")
            write_dashboard(snapshot, root / "out")
            changed = (root / "out" / "projects-dashboard-light.svg").read_bytes()
            self.assertNotEqual(first["projects-dashboard-light.svg"], changed)
            changed_mobile = (root / "out" / "projects-dashboard-mobile-light.svg").read_bytes()
            self.assertNotEqual(first["projects-dashboard-mobile-light.svg"], changed_mobile)

    def test_invalid_update_preserves_last_valid_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "out"
            source = root / "projects.json"
            source.write_text(json.dumps(sample_snapshot()), encoding="utf-8")
            outputs = write_dashboard(source, output_dir)
            previous = {path.name: path.read_bytes() for path in outputs.values()}
            invalid = sample_snapshot()
            invalid["projects"][0]["url"] = "https://evil.example/DoArtMonteiro/alpha"
            source.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ProjectVisualError):
                write_dashboard(source, output_dir)
            current = {path.name: path.read_bytes() for path in outputs.values()}
            self.assertEqual(previous, current)


if __name__ == "__main__":
    unittest.main()
