#!/usr/bin/env python3
"""Build a complete, unpublished preview bundle from verified GitHub data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.living_portfolio.calendar import render_calendar
from scripts.living_portfolio.campaigns import (
    CAMPAIGN_CHART_THEMES,
    render_metric_chart,
    render_metric_summary,
    verify_registry,
)
from scripts.living_portfolio.fetch_contributions import (
    APPROVED_LOGIN,
    ContributionDataError,
    csv_bytes,
    fetch_public_calendar,
)
from scripts.living_portfolio.project_visuals import write_dashboard
from scripts.living_portfolio.projects import GitHubApiError, collect_projects


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWLIST = ROOT / "assets" / "project-repositories.json"
DEFAULT_REGISTRY = ROOT / "assets" / "campaigns"
LOCAL_TEST_OUTPUT_ROOT = ROOT / "previews" / "living-portfolio-tests"


class PreviewError(RuntimeError):
    """Raised when a complete, validated preview cannot be prepared."""


def _validate_output_root(path: str | Path) -> Path:
    candidate = Path(os.path.abspath(path))
    allowed_roots = [(ROOT, LOCAL_TEST_OUTPUT_ROOT)]
    runner_temp = os.environ.get("RUNNER_TEMP")
    if runner_temp:
        runner_root = Path(os.path.abspath(runner_temp))
        allowed_roots.append((runner_root, runner_root / "living-portfolio-preview"))
    for anchor, allowed in allowed_roots:
        anchor = Path(os.path.abspath(anchor))
        allowed = Path(os.path.abspath(allowed))
        if candidate != allowed and allowed not in candidate.parents:
            continue
        try:
            relative = candidate.relative_to(anchor)
        except ValueError:
            continue
        cursor = anchor
        if _is_link_or_junction(cursor):
            raise PreviewError("Preview output anchor cannot be a link or junction")
        for component in relative.parts:
            cursor = cursor / component
            if _is_link_or_junction(cursor):
                raise PreviewError("Preview output path cannot contain links or junctions")
        return candidate
    raise PreviewError("Preview output must stay inside the allowlisted test or GitHub runner directory")


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        if checker and checker():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise PreviewError("Cannot inspect path for symbolic links or junctions") from error
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(attributes & reparse_point)


def load_allowlist(path: str | Path) -> list[str]:
    """Load an exact, small repository allowlist for the known public owner."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreviewError("Cannot read the reviewed repository allowlist") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PreviewError("Unsupported repository allowlist schema")
    repositories = document.get("repositories")
    if not isinstance(repositories, list) or not 1 <= len(repositories) <= 16:
        raise PreviewError("Repository allowlist must contain 1 to 16 exact repositories")
    seen: set[str] = set()
    for repository in repositories:
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise PreviewError("Allowlist entries must use exact owner/repository names")
        owner, name = repository.split("/", 1)
        if owner.casefold() != APPROVED_LOGIN.casefold() or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
            raise PreviewError("Allowlist contains a repository outside the reviewed profile owner")
        key = repository.casefold()
        if key in seen:
            raise PreviewError("Repository allowlist contains duplicates")
        seen.add(key)
    return sorted(repositories, key=str.casefold)


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PreviewError("Preview manifest contains an unsupported value") from error


def _write_if_changed(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_bytes() == payload:
            return False
    except FileNotFoundError:
        pass
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return True


def _asset_hashes(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if _is_link_or_junction(path):
            raise PreviewError("Generated asset bundle cannot contain symbolic links or junctions")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _latest_event_date(project_snapshot: dict[str, Any]) -> str | None:
    dates = []
    for event in project_snapshot.get("timeline", []):
        raw = event.get("occurred_at") if isinstance(event, dict) else None
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as error:
                raise PreviewError("Project timeline has an invalid event date") from error
            if parsed.tzinfo is None:
                raise PreviewError("Project timeline timestamps must include a timezone")
            dates.append(parsed.date().isoformat())
    return max(dates) if dates else None


def _collect_campaign_charts(registry: Path | None, output: Path) -> dict[str, Any]:
    if registry is None:
        return {
            "state": "no approved campaign data", "cadence": "per approved run",
            "runs": 0, "charts": [], "text_alternatives": [],
        }
    if not registry.exists():
        raise PreviewError("Configured campaign registry is missing; preserve the previous portfolio bundle")
    if _is_link_or_junction(registry):
        raise PreviewError("Campaign registry cannot be a link or junction")
    runs = verify_registry(registry)
    if not runs:
        return {
            "state": "no approved campaign data", "cadence": "per approved run",
            "runs": 0, "charts": [], "text_alternatives": [],
        }
    combinations = sorted({
        (run["campaign_id"], metric["name"])
        for run in runs
        for metric in run["metrics"]
    })
    charts: list[str] = []
    text_alternatives: list[str] = []
    for campaign_id, metric_name in combinations:
        for theme in CAMPAIGN_CHART_THEMES:
            relative_target = Path(campaign_id) / f"{metric_name}-{theme}.svg"
            target = output / relative_target
            result = render_metric_chart(
                registry, campaign_id, metric_name, target, theme=theme,
            )
            if result is not None:
                charts.append(relative_target.as_posix())
        relative_summary = Path(campaign_id) / f"{metric_name}.md"
        summary = render_metric_summary(
            registry,
            campaign_id,
            metric_name,
            output / relative_summary,
            include_bundle_provenance=True,
        )
        if summary is not None:
            text_alternatives.append(relative_summary.as_posix())
    return {
        "state": "approved campaign data",
        "cadence": "per approved run",
        "runs": len(runs),
        "charts": charts,
        "text_alternatives": text_alternatives,
        "evidence": [
            {
                "campaign_id": run["campaign_id"],
                "run_id": run["run_id"],
                "source_commit": run["source_commit"],
                "raw_data_sha256": run["raw_data_sha256"],
                "configuration_sha256": run["configuration_sha256"],
                "configuration_reference": run["configuration_reference"],
                "public_approval_reference": run["public_approval_reference"],
                "observed_on": run["observed_on"],
            }
            for run in runs
        ],
    }


def build_preview(
    contribution_csv: str | Path,
    project_snapshot: dict[str, Any],
    output_root: str | Path,
    *,
    source_scope: str,
    campaign_registry: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a content-addressed bundle; only its pointer changes on success.

    Any invalid source or failed renderer leaves the last ``current.json`` and
    its referenced files untouched. The caller controls whether publication
    is possible; this preview function never writes to a Git branch.
    """
    destination = _validate_output_root(output_root)
    versions = destination / "versions"
    destination.mkdir(parents=True, exist_ok=True)
    if _is_link_or_junction(destination):
        raise PreviewError("Preview output directory cannot be a link or junction")
    if _is_link_or_junction(versions):
        raise PreviewError("Preview versions directory cannot be a link or junction")
    versions.mkdir(parents=True, exist_ok=True)
    _validate_output_root(versions)
    with tempfile.TemporaryDirectory(prefix=".preview-stage-", dir=versions) as staging_name:
        stage = Path(staging_name)
        calendar_outputs = render_calendar(Path(contribution_csv), stage / "calendar", source_scope)
        selected_repositories = [
            project["repository"] for project in project_snapshot["projects"]
        ]
        project_source = stage / "projects-source.json"
        _write_if_changed(project_source, _canonical_json(project_snapshot))
        project_outputs = write_dashboard(project_source, stage / "projects")
        campaign_root = Path(campaign_registry) if campaign_registry is not None else None
        campaign_summary = _collect_campaign_charts(campaign_root, stage / "campaigns")

        calendar_manifest = json.loads(calendar_outputs["calendar-manifest"].read_text(encoding="utf-8"))
        provenance = {
            "schema_version": 1,
            "profile": APPROVED_LOGIN,
            "sources": {
                "public_commits": {
                    "source": "GitHub REST List commits endpoint",
                    "scope": source_scope,
                    "metric": "Distinct commit objects linked to the profile in selected public default branches, bucketed by author timestamp in UTC.",
                    "authentication": "No authorization token is sent to the public commit endpoint.",
                    "repositories": selected_repositories,
                    "cadence": "manual preview now; proposed daily after owner approval",
                    "data_through": calendar_manifest["observed_interval"]["end"],
                    "source_sha256": calendar_manifest["source_sha256"],
                },
                "projects": {
                    "source": "GitHub public REST API",
                    "cadence": "manual preview now; proposed daily after owner approval",
                    "repositories": [project["repository"] for project in project_snapshot["projects"]],
                    "latest_event_date": _latest_event_date(project_snapshot),
                },
                "campaigns": campaign_summary,
                "product_screenshots": {
                    "state": "No product screenshot is included in this generated bundle",
                    "cadence": "Refresh only after a material product change and review",
                    "source_policy": "Any added capture must reference a human-reviewed immutable product revision",
                },
            },
            "refresh": {
                "workflow_mode": "manual workflow_dispatch until schedule and publication are separately approved",
                "proposed_cadence": "daily after owner approval",
                "stale_after_hours": 36,
                "last_successful_refresh": "For an Actions run, use that run's timestamp. Execution time is kept outside the deterministic bundle.",
                "failure_behaviour": "The workflow writes only to ephemeral runner storage and publishes nothing. A failed run creates no current asset. Any separately published older asset remains unchanged, and its data-through date continues to age. Local reruns in the same output directory preserve the last valid bundle and pointer.",
                "recovery": "Inspect the failed run, correct the source or validator, then dispatch a new preview. Publish only after a complete successful review and separate owner approval.",
            },
            "accessibility": {
                "calendar": "Theme-specific 3D SVGs, a 2D SVG alternative, normalized CSV and textual summary",
                "projects": "Theme-specific desktop and mobile SVGs, linked Markdown text alternative and JSON source snapshot",
                "interactive_visuals": "GitHub README embeds are static; genuinely interactive views use external links",
            },
        }
        _write_if_changed(stage / "provenance.json", _canonical_json(provenance))
        hashes = _asset_hashes(stage)
        manifest = {
            "schema_version": 1,
            "asset_sha256": hashes,
            "calendar_summary": {
                "data_through": calendar_manifest["observed_interval"]["end"],
                "dated_days": calendar_manifest["observed_interval"]["dated_days"],
                "total_public_commits": calendar_manifest["commit_activity"]["total_commits"],
                "active_days": calendar_manifest["commit_activity"]["active_days"],
                "scope": source_scope,
            },
            "project_count": len(project_snapshot["projects"]),
            "campaign_state": campaign_summary["state"],
            "campaign_chart_count": len(campaign_summary["charts"]),
        }
        manifest_bytes = _canonical_json(manifest)
        bundle_hash = hashlib.sha256()
        for name, digest in sorted(hashes.items()):
            bundle_hash.update(name.encode("utf-8"))
            bundle_hash.update(b"\0")
            bundle_hash.update(digest.encode("ascii"))
            bundle_hash.update(b"\0")
        bundle_hash.update(manifest_bytes)
        version_id = bundle_hash.hexdigest()
        (stage / "manifest.json").write_bytes(manifest_bytes)
        # The full digest remains the bundle identity; keep the on-disk folder
        # shorter so nested generated assets remain usable on Windows hosts.
        storage_id = version_id[:24]
        final_directory = versions / storage_id
        if _is_link_or_junction(final_directory):
            raise PreviewError("Content-addressed bundle cannot be a link or junction")
        if final_directory.exists():
            existing_hashes = _asset_hashes(final_directory)
            if existing_hashes != _asset_hashes(stage) or (final_directory / "manifest.json").read_bytes() != manifest_bytes:
                raise PreviewError("Existing content-addressed version is inconsistent")
        else:
            os.replace(stage, final_directory)
        pointer = {
            "version": version_id,
            "manifest": f"versions/{storage_id}/manifest.json",
            "data_through": calendar_manifest["observed_interval"]["end"],
            "stale_after_hours": 36,
        }
        pointer_bytes = _canonical_json(pointer)
        pointer_path = destination / "current.json"
        if _is_link_or_junction(pointer_path):
            raise PreviewError("Current-version pointer cannot be a link or junction")
        _write_if_changed(pointer_path, pointer_bytes)
        return {
            "version": version_id,
            "bundle": str(final_directory),
            "current": str(destination / "current.json"),
            "manifest": manifest,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--token-env", default="GH_TOKEN")
    args = parser.parse_args(argv)
    try:
        repositories = load_allowlist(DEFAULT_ALLOWLIST)
        token = os.environ.get(args.token_env, "")
        rows = fetch_public_calendar(APPROVED_LOGIN, repositories)
        snapshot = collect_projects(repositories)
        with tempfile.TemporaryDirectory(prefix="living-portfolio-sources-") as source_dir:
            contribution_path = Path(source_dir) / "contributions.csv"
            contribution_path.write_bytes(csv_bytes(rows))
            result = build_preview(
                contribution_path, snapshot, args.output_dir,
                source_scope="selected_public_repo_commits", campaign_registry=DEFAULT_REGISTRY,
            )
        print(json.dumps({
            "status": "preview generated; assets were not published",
            "version": result["version"],
            "calendar": result["manifest"]["calendar_summary"],
            "projects": result["manifest"]["project_count"],
            "campaign_state": result["manifest"]["campaign_state"],
            "campaign_charts": result["manifest"]["campaign_chart_count"],
        }, ensure_ascii=False, sort_keys=True))
    except (PreviewError, ContributionDataError, GitHubApiError, OSError, ValueError) as error:
        print(f"Living portfolio preview failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
