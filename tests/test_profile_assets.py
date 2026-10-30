from __future__ import annotations

import json
import hashlib
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class ProfileAssetTests(unittest.TestCase):
    def test_all_relative_readme_assets_exist_and_stay_in_repository(self) -> None:
        source = README.read_text(encoding="utf-8")
        destinations = [
            *re.findall(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", source),
            *re.findall(r"<(?:source|img)\b[^>]*\b(?:src|srcset)=\"([^\"]+)\"", source),
        ]
        self.assertTrue(destinations)
        for destination in destinations:
            parsed = urlsplit(destination)
            if parsed.scheme or destination.startswith("#"):
                continue
            relative = parsed.path
            self.assertFalse(Path(relative).is_absolute(), destination)
            resolved = (ROOT / relative).resolve()
            self.assertTrue(resolved == ROOT or ROOT in resolved.parents, destination)
            self.assertTrue(resolved.is_file(), destination)

    def test_readme_image_alt_text_is_present(self) -> None:
        source = README.read_text(encoding="utf-8")
        image_alts = re.findall(r"!\[([^\]]*)\]\([^)]+\)", source)
        self.assertTrue(image_alts)
        self.assertTrue(all(alt.strip() for alt in image_alts))
        html_image = re.search(r"<img\b[^>]*\balt=\"([^\"]*)\"", source)
        self.assertIsNotNone(html_image)
        self.assertTrue(html_image.group(1).strip())

    def test_mobile_dashboard_sources_precede_desktop_theme_fallbacks(self) -> None:
        source = README.read_text(encoding="utf-8")
        activity = source.split("## Activity", 1)[1]
        pictures = re.findall(r"<picture>[\s\S]*?</picture>", activity)
        self.assertEqual(len(pictures), 2)
        project_dashboard = pictures[1]
        self.assertLess(
            project_dashboard.index('media="(max-width: 480px) and (prefers-color-scheme: dark)"'),
            project_dashboard.index('media="(prefers-color-scheme: dark)"'),
        )
        self.assertIn("projects-dashboard-mobile-dark.svg", project_dashboard)
        self.assertIn("projects-dashboard-mobile-light.svg", project_dashboard)
        dashboard_alt = re.search(r"<img\b[^>]*\balt=\"([^\"]*)\"", project_dashboard)
        self.assertIsNotNone(dashboard_alt)
        self.assertIn("dashboard", dashboard_alt.group(1).casefold())
        self.assertNotIn("mobile", dashboard_alt.group(1).casefold())
        self.assertIn("full timeline", dashboard_alt.group(1).casefold())

    def test_primary_navigation_anchors_have_headings(self) -> None:
        source = README.read_text(encoding="utf-8")
        headings = {
            re.sub(r"[^a-z0-9 -]", "", heading.casefold()).strip().replace(" ", "-")
            for heading in re.findall(r"^#{1,6}\s+(.+)$", source, flags=re.MULTILINE)
        }
        links = set(re.findall(r"\]\(#([^)]+)\)", source))
        self.assertEqual({"engineering", "creative-work", "project-index", "activity"}, links)
        self.assertTrue(links <= headings)

    def test_readme_links_are_secure_and_local_targets_exist(self) -> None:
        source = README.read_text(encoding="utf-8")
        destinations = [
            *re.findall(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)", source),
            *re.findall(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", source),
        ]
        headings = {
            re.sub(r"[^a-z0-9 -]", "", heading.casefold()).strip().replace(" ", "-")
            for heading in re.findall(r"^#{1,6}\s+(.+)$", source, flags=re.MULTILINE)
        }
        for destination in destinations:
            parsed = urlsplit(destination)
            if parsed.scheme:
                self.assertEqual("https", parsed.scheme, destination)
                self.assertIsNotNone(parsed.hostname, destination)
                self.assertIsNone(parsed.username, destination)
                self.assertIsNone(parsed.password, destination)
                continue
            if parsed.path:
                resolved = (ROOT / parsed.path).resolve()
                self.assertTrue(resolved == ROOT or ROOT in resolved.parents, destination)
                self.assertTrue(resolved.exists(), destination)
            elif parsed.fragment:
                self.assertIn(parsed.fragment, headings, destination)

    def test_project_index_covers_the_reviewed_repository_allowlist(self) -> None:
        source = README.read_text(encoding="utf-8")
        allowlist = json.loads((ROOT / "assets/project-repositories.json").read_text(encoding="utf-8"))
        for repository in allowlist["repositories"]:
            _, name = repository.split("/", 1)
            self.assertIn(f"github.com/{repository}", source, name)

    def test_local_svg_assets_are_valid_and_have_no_active_content(self) -> None:
        source = README.read_text(encoding="utf-8")
        relative_svgs = {
            urlsplit(path).path
            for path in re.findall(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", source)
            if urlsplit(path).path.casefold().endswith(".svg") and not urlsplit(path).scheme
        }
        for relative in relative_svgs:
            root = ElementTree.parse(ROOT / relative).getroot()
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1].casefold()
                self.assertNotIn(tag, {"script", "foreignobject", "iframe"}, relative)
                for name, value in element.attrib.items():
                    if name.rsplit("}", 1)[-1].casefold() in {"href", "src"}:
                        self.assertFalse(urlsplit(value).scheme, f"{relative}: external {name}")

    def test_light_and_dark_banner_share_the_same_composition(self) -> None:
        dark = ElementTree.parse(ROOT / "assets/identity/night-atlas-dark.svg").getroot()
        light = ElementTree.parse(ROOT / "assets/identity/night-atlas-light.svg").getroot()
        self.assertEqual(dark.attrib.get("viewBox"), light.attrib.get("viewBox"))
        dark_text = [item.text for item in dark.iter() if item.tag.endswith("text") and item.text]
        light_text = [item.text for item in light.iter() if item.tag.endswith("text") and item.text]
        self.assertEqual(dark_text, light_text)

    def test_generated_snapshot_is_dated_public_only_and_hash_verified(self) -> None:
        generated = ROOT / "assets" / "generated"
        current = generated / "current.json"
        status = json.loads(current.read_text(encoding="utf-8"))
        version = status["version"]
        storage_id = version[:24]
        version_root = generated / "versions" / storage_id
        manifest_path = version_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(status["status"], "draft_review_snapshot")
        self.assertEqual(status["manifest"], f"versions/{storage_id}/manifest.json")
        self.assertEqual(len(version), 64)
        self.assertEqual(status["data_through"], manifest["calendar_summary"]["data_through"])
        refreshed = datetime.fromisoformat(status["last_successful_refresh_utc"].replace("Z", "+00:00"))
        stale_after = datetime.fromisoformat(status["stale_after_utc"].replace("Z", "+00:00"))
        self.assertEqual(stale_after, refreshed + timedelta(hours=status["stale_after_hours"]))
        self.assertEqual(manifest["calendar_summary"]["scope"], "selected_public_repo_commits")
        self.assertEqual(manifest["calendar_summary"]["dated_days"], 365)
        self.assertEqual(manifest["project_count"], 8)

        provenance = json.loads((version_root / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["sources"]["public_commits"]["authentication"],
                         "No authorization token is sent to the public commit endpoint.")
        allowlist = json.loads((ROOT / "assets/project-repositories.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["sources"]["public_commits"]["repositories"],
                         sorted(allowlist["repositories"], key=str.casefold))
        self.assertEqual(status["refresh_mode"],
                         "manual preview; recurring refresh and automatic asset publication are disabled")

        for name, digest in status["asset_sha256"].items():
            asset = generated / "current" / name
            self.assertTrue(asset.is_file(), name)
            self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), digest, name)

        for name, digest in manifest["asset_sha256"].items():
            asset = (version_root / name).resolve()
            self.assertTrue(asset == version_root.resolve() or version_root.resolve() in asset.parents, name)
            self.assertTrue(asset.is_file(), name)
            self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), digest, name)

        bundle_hash = hashlib.sha256()
        for name, digest in sorted(manifest["asset_sha256"].items()):
            bundle_hash.update(name.encode("utf-8"))
            bundle_hash.update(b"\0")
            bundle_hash.update(digest.encode("ascii"))
            bundle_hash.update(b"\0")
        bundle_hash.update(manifest_path.read_bytes())
        self.assertEqual(bundle_hash.hexdigest(), version)

        longest_relative_path = max(
            len(path.relative_to(ROOT).as_posix())
            for path in version_root.rglob("*")
            if path.is_file()
        )
        self.assertLess(longest_relative_path, 180)

        for asset in (generated / "current").glob("*.svg"):
            root = ElementTree.parse(asset).getroot()
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1].casefold()
                self.assertNotIn(tag, {"script", "foreignobject", "iframe"}, asset.name)
                for name, value in element.attrib.items():
                    if name.rsplit("}", 1)[-1].casefold() in {"href", "src"}:
                        self.assertFalse(urlsplit(value).scheme, f"{asset.name}: external {name}")


if __name__ == "__main__":
    unittest.main()
