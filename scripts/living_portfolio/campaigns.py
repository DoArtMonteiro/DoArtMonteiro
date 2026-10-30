#!/usr/bin/env python3
"""Maintain an append-only registry of approved benchmark and simulation runs.

The protected index is the trust anchor for immutable run files. Commit and
review index changes with the approval evidence. This module never fetches
approval URLs, source repositories, or raw data.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import sys
import tempfile
import textwrap
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "assets" / "campaigns"
RUNS_DIR = "runs"
INDEX_NAME = "protected-index.json"
SLUG_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
CAMPAIGN_CHART_THEMES = {
    "light": {
        "background": "#ffffff", "ink": "#1f2328", "muted": "#59636e",
        "axis": "#aeb8c2", "grid": "#e5e9ee", "line": "#2878c8",
    },
    "dark": {
        "background": "#0d1117", "ink": "#e6edf3", "muted": "#8b949e",
        "axis": "#6e7681", "grid": "#30363d", "line": "#58a6ff",
    },
}


class CampaignDataError(ValueError):
    """Raised when campaign evidence is missing, invalid, or has changed."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CampaignDataError("Campaign data must be JSON-safe and finite") from error


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CampaignDataError(f"Invalid {field}")
    if any(ord(char) < 32 for char in value):
        raise CampaignDataError(f"Invalid {field}")
    return value


def _valid_slug(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if not SLUG_PATTERN.fullmatch(text):
        raise CampaignDataError(f"Invalid {field}: use a lowercase stable slug")
    return text


def _valid_metric_name(value: Any) -> str:
    text = _required_text(value, "metric name")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", text):
        raise CampaignDataError("Invalid metric name: use a lowercase stable name")
    return text


def _public_approval_url(value: Any) -> str:
    url = _required_text(value, "public approval reference")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise CampaignDataError("Invalid public approval reference") from error
    try:
        ip = ipaddress.ip_address(hostname) if hostname else None
    except ValueError:
        ip = None
    if (parsed.scheme != "https" or hostname is None or "." not in hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or len(parsed.path) < 2 or parsed.query or parsed.fragment
            or (ip is not None and not ip.is_global)
            or hostname.casefold() in {"localhost", "localhost.localdomain"}
            or hostname.casefold().endswith((".local", ".internal", ".test", ".invalid"))):
        raise CampaignDataError("Approval evidence must be a public HTTPS URL")
    return url


def _validate_run(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise CampaignDataError("Run must be a JSON object")
    required = {
        "campaign_id", "run_id", "version", "source_commit", "raw_data_sha256",
        "configuration_sha256", "configuration_reference", "public_approval_reference",
        "approval_status", "observed_on", "data_kind", "metrics",
    }
    allowed = required | {"supersedes_run_id"}
    missing = required - record.keys()
    extra = record.keys() - allowed
    if missing:
        raise CampaignDataError(f"Run is missing required fields: {', '.join(sorted(missing))}")
    if extra:
        raise CampaignDataError(f"Run has unsupported fields: {', '.join(sorted(extra))}")

    campaign_id = _valid_slug(record["campaign_id"], "campaign_id")
    run_id = _valid_slug(record["run_id"], "run_id")
    version = record["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CampaignDataError("version must be a positive integer")
    has_version_suffix = re.search(r"-v[1-9][0-9]*\Z", run_id) is not None
    if (version > 1 and not run_id.endswith(f"-v{version}")
            or has_version_suffix and not run_id.endswith(f"-v{version}")):
        raise CampaignDataError("Versioned run_id suffix must match version")
    if record["approval_status"] != "approved":
        raise CampaignDataError("Only explicitly approved runs may be indexed")
    approval_reference = _public_approval_url(record["public_approval_reference"])
    commit = _required_text(record["source_commit"], "source_commit")
    if not COMMIT_PATTERN.fullmatch(commit):
        raise CampaignDataError("source_commit must be a full hexadecimal commit SHA")
    raw_sha = _required_text(record["raw_data_sha256"], "raw_data_sha256")
    if not SHA256_PATTERN.fullmatch(raw_sha):
        raise CampaignDataError("raw_data_sha256 must be a lowercase SHA-256 digest")
    configuration_sha = _required_text(record["configuration_sha256"], "configuration_sha256")
    if not SHA256_PATTERN.fullmatch(configuration_sha):
        raise CampaignDataError("configuration_sha256 must be a lowercase SHA-256 digest")
    configuration_reference = _public_approval_url(record["configuration_reference"])
    observed_on = _required_text(record["observed_on"], "observed_on")
    try:
        parsed_date = date.fromisoformat(observed_on)
    except ValueError as error:
        raise CampaignDataError("observed_on must use YYYY-MM-DD") from error
    if parsed_date.isoformat() != observed_on:
        raise CampaignDataError("observed_on must use YYYY-MM-DD")
    if record["data_kind"] not in {"benchmark", "simulation"}:
        raise CampaignDataError("data_kind must be benchmark or simulation")

    metrics = record["metrics"]
    if not isinstance(metrics, list) or not metrics:
        raise CampaignDataError("metrics must be a non-empty list")
    clean_metrics = []
    seen_names: set[str] = set()
    for metric in metrics:
        if not isinstance(metric, dict) or set(metric) - {"name", "value", "unit", "sample_count", "allow_negative"}:
            raise CampaignDataError("Each metric must have name, value, unit and sample_count")
        name = _valid_metric_name(metric.get("name"))
        if name in seen_names:
            raise CampaignDataError(f"Duplicate metric name: {name}")
        seen_names.add(name)
        unit = _required_text(metric.get("unit"), "metric unit")
        value = metric.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CampaignDataError(f"Metric {name} must have a finite numeric value")
        try:
            finite_value = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite_value = False
        if not finite_value:
            raise CampaignDataError(f"Metric {name} must have a finite numeric value")
        allow_negative = metric.get("allow_negative", False)
        if not isinstance(allow_negative, bool):
            raise CampaignDataError(f"Metric {name} allow_negative must be boolean")
        if value < 0 and not allow_negative:
            raise CampaignDataError(f"Metric {name} cannot be negative")
        sample_count = metric.get("sample_count")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
            raise CampaignDataError(f"Metric {name} sample_count must be a positive integer")
        clean = {"name": name, "value": value, "unit": unit, "sample_count": sample_count}
        if allow_negative:
            clean["allow_negative"] = True
        clean_metrics.append(clean)

    supersedes = record.get("supersedes_run_id")
    if supersedes is not None:
        supersedes = _valid_slug(supersedes, "supersedes_run_id")
    if version == 1 and supersedes is not None:
        raise CampaignDataError("A version 1 run cannot supersede another run")
    if version > 1 and supersedes is None:
        raise CampaignDataError("Corrections must reference supersedes_run_id")

    cleaned = {
        "campaign_id": campaign_id,
        "run_id": run_id,
        "version": version,
        "source_commit": commit,
        "raw_data_sha256": raw_sha,
        "configuration_sha256": configuration_sha,
        "configuration_reference": configuration_reference,
        "public_approval_reference": approval_reference,
        "approval_status": "approved",
        "observed_on": observed_on,
        "data_kind": record["data_kind"],
        "metrics": clean_metrics,
    }
    if supersedes is not None:
        cleaned["supersedes_run_id"] = supersedes
    _canonical_bytes(cleaned)
    return cleaned


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CampaignDataError(f"Missing {label}: {path}") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignDataError(f"Cannot read {label}: {path}") from error


def _index_path(registry_root: Path) -> Path:
    return registry_root / INDEX_NAME


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def verify_registry(registry_root: str | Path) -> list[dict[str, Any]]:
    """Verify that every immutable run matches the approved protected index."""
    root = Path(registry_root)
    index_path = _index_path(root)
    runs_dir = root / RUNS_DIR
    if _is_link_or_junction(root) or _is_link_or_junction(index_path) or _is_link_or_junction(runs_dir):
        raise CampaignDataError("Campaign registry cannot contain link or junction paths")
    index = _load_json(index_path, "protected campaign index")
    if not isinstance(index, dict) or set(index) != {"schema_version", "runs"}:
        raise CampaignDataError("Protected index has an invalid shape")
    if index["schema_version"] != 1 or isinstance(index["schema_version"], bool):
        raise CampaignDataError("Unsupported protected index schema version")
    entries = index["runs"]
    if not isinstance(entries, list):
        raise CampaignDataError("Protected index runs must be a list")
    indexed: dict[str, str] = {}
    previous_id = ""
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"run_id", "sha256"}:
            raise CampaignDataError("Protected index entry has an invalid shape")
        run_id = _valid_slug(entry["run_id"], "indexed run_id")
        digest = _required_text(entry["sha256"], "indexed run digest")
        if not SHA256_PATTERN.fullmatch(digest) or run_id in indexed or run_id <= previous_id:
            raise CampaignDataError("Protected index entries must be unique, sorted and valid")
        indexed[run_id] = digest
        previous_id = run_id

    actual_files: dict[str, Path] = {}
    if runs_dir.exists():
        if not runs_dir.is_dir():
            raise CampaignDataError("Campaign runs path is not a directory")
        for path in runs_dir.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise CampaignDataError(f"Unexpected entry in campaign runs: {path.name}")
            run_id = path.stem
            if not SLUG_PATTERN.fullmatch(run_id) or run_id in actual_files:
                raise CampaignDataError(f"Invalid or duplicate run file: {path.name}")
            actual_files[run_id] = path
    if set(actual_files) != set(indexed):
        missing = sorted(set(indexed) - set(actual_files))
        unindexed = sorted(set(actual_files) - set(indexed))
        raise CampaignDataError(f"Campaign index/files differ (missing={missing}, unindexed={unindexed})")

    runs = []
    for run_id in sorted(indexed):
        path = actual_files[run_id]
        raw = path.read_bytes()
        if _sha256(raw) != indexed[run_id]:
            raise CampaignDataError(f"Indexed run changed: {run_id}")
        parsed = _load_json(path, f"run {run_id}")
        run = _validate_run(parsed)
        if run["run_id"] != run_id or _canonical_bytes(run) != raw:
            raise CampaignDataError(f"Run file is not canonical or its ID does not match: {run_id}")
        runs.append(run)
    _validate_supersession(runs)
    return runs


def _validate_supersession(runs: list[dict[str, Any]]) -> None:
    by_id = {run["run_id"]: run for run in runs}
    superseded_by: set[str] = set()
    for run in runs:
        supersedes = run.get("supersedes_run_id")
        if supersedes is None:
            continue
        if supersedes in superseded_by:
            raise CampaignDataError(f"More than one correction supersedes run: {supersedes}")
        superseded_by.add(supersedes)
        prior = by_id.get(supersedes)
        if (prior is None or prior["campaign_id"] != run["campaign_id"]
                or prior["version"] != run["version"] - 1
                or prior["observed_on"] > run["observed_on"]):
            raise CampaignDataError(f"Invalid superseded run reference: {run['run_id']}")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def append_run(registry_root: str | Path, run_record: dict[str, Any]) -> dict[str, Any]:
    """Append one approved run; repeating an identical run is idempotent."""
    root = Path(registry_root)
    run = _validate_run(run_record)
    runs = verify_registry(root)
    run_id = run["run_id"]
    existing = next((item for item in runs if item["run_id"] == run_id), None)
    run_bytes = _canonical_bytes(run)
    if existing is not None:
        if _canonical_bytes(existing) == run_bytes:
            return existing
        raise CampaignDataError(f"Duplicate run_id with different content: {run_id}")
    _validate_supersession([*runs, run])

    run_path = root / RUNS_DIR / f"{run_id}.json"
    if run_path.exists():
        raise CampaignDataError(f"Unindexed run file already exists: {run_id}")
    index_path = _index_path(root)
    index = _load_json(index_path, "protected campaign index")
    entries = [*index["runs"], {"run_id": run_id, "sha256": _sha256(run_bytes)}]
    entries.sort(key=lambda item: item["run_id"])
    index_bytes = _canonical_bytes({"schema_version": 1, "runs": entries})

    _atomic_write(run_path, run_bytes)
    try:
        _atomic_write(index_path, index_bytes)
    except OSError:
        try:
            run_path.unlink()
        except OSError:
            pass
        raise
    return run


def initialize_registry(registry_root: str | Path) -> Path:
    """Create an empty protected index; refuse to overwrite any existing data."""
    root = Path(registry_root)
    index = _index_path(root)
    if _is_link_or_junction(root) or _is_link_or_junction(index) or _is_link_or_junction(root / RUNS_DIR):
        raise CampaignDataError("Campaign registry cannot contain link or junction paths")
    if index.exists() or (root / RUNS_DIR).exists():
        raise CampaignDataError("Campaign registry already exists")
    _atomic_write(index, _canonical_bytes({"schema_version": 1, "runs": []}))
    return index


def _approved_metric_series(
    registry_root: str | Path, campaign_id: str, metric_name: str,
) -> tuple[str, str, list[tuple[str, int | float, int, str]], str] | None:
    campaign = _valid_slug(campaign_id, "campaign_id")
    metric = _valid_metric_name(metric_name)
    runs = verify_registry(registry_root)
    superseded = {run["supersedes_run_id"] for run in runs
                  if run.get("supersedes_run_id") is not None}
    points: list[tuple[str, int | float, int, str]] = []
    units: set[str] = set()
    for run in runs:
        if (run["campaign_id"] != campaign or run["approval_status"] != "approved"
                or run["run_id"] in superseded):
            continue
        found = next((item for item in run["metrics"] if item["name"] == metric), None)
        if found is None:
            continue
        points.append((run["observed_on"], found["value"], found["sample_count"], run["run_id"]))
        units.add(found["unit"])
    if not points:
        return None
    if len(units) != 1:
        raise CampaignDataError("A metric chart requires a consistent unit across indexed runs")
    points.sort(key=lambda point: (point[0], point[3]))
    return campaign, metric, points, next(iter(units))


def render_metric_chart(
    registry_root: str | Path,
    campaign_id: str,
    metric_name: str,
    output_path: str | Path | None = None,
    *,
    theme: str = "light",
) -> Path | None:
    """Write a deterministic, accessible SVG from approved indexed runs.

    Returns None without writing a result chart when there are no matching
    approved observations.
    """
    if theme not in CAMPAIGN_CHART_THEMES:
        raise CampaignDataError("Chart theme must be light or dark")
    series = _approved_metric_series(registry_root, campaign_id, metric_name)
    if series is None:
        return None
    campaign, metric, points, unit = series
    palette = CAMPAIGN_CHART_THEMES[theme]
    target = Path(output_path) if output_path is not None else (
        Path(registry_root).parent / "campaign-results" / campaign / f"{metric}-{theme}.svg")
    target.parent.mkdir(parents=True, exist_ok=True)

    title = f"{campaign}: {metric}"
    width = 760
    left, right, bottom = 72, 30, 72
    title_lines = textwrap.wrap(title, width=32, break_long_words=True, break_on_hyphens=False) or [title]
    title_line_height = 22
    extra_title_height = (len(title_lines) - 1) * title_line_height
    height = 360 + extra_title_height
    top = 56 + extra_title_height
    plot_width, plot_height = width - left - right, height - top - bottom
    values = [float(point[1]) for point in points]
    scale = max((abs(value) for value in values), default=0.0) or 1.0
    scaled_values = [value / scale for value in values]
    low, high = min(0.0, min(scaled_values)), max(scaled_values)
    if high == low:
        high = low + 1.0
    x_positions = [left + (plot_width / 2 if len(points) == 1
                           else i * plot_width / (len(points) - 1))
                   for i in range(len(points))]
    y_positions = [top + (high - value) * plot_height / (high - low)
                   for value in scaled_values]
    desc = (f"Approved {len(points)}-run {metric} series in {unit}. "
            f"Each point is indexed by the protected campaign registry. {theme.title()} theme.")
    title_markup = "".join(
        f'<tspan x="{left}" dy="{0 if index == 0 else title_line_height}">{html.escape(line)}</tspan>'
        for index, line in enumerate(title_lines)
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'  <title id="title">{html.escape(title)}</title>',
        f'  <desc id="desc">{html.escape(desc)}</desc>',
        f'  <style>text{{font-family:Arial,sans-serif;fill:{palette["ink"]}}}.muted{{fill:{palette["muted"]};font-size:12px}}</style>',
        f'  <rect width="100%" height="100%" fill="{palette["background"]}"/>',
        f'  <text x="{left}" y="32" font-size="21" font-weight="700">{title_markup}</text>',
        f'  <text x="{left}" y="{50 + extra_title_height}" class="muted">{html.escape(unit)} · approved, indexed source data</text>',
        f'  <path d="M {left} {top} V {height-bottom} H {width-right}" fill="none" stroke="{palette["axis"]}"/>',
    ]
    for i in range(5):
        value = (low + (high - low) * (4 - i) / 4) * scale
        y = top + plot_height * i / 4
        lines.extend((
            f'  <path d="M {left} {y:.2f} H {width-right}" stroke="{palette["grid"]}"/>',
            f'  <text x="{left-10}" y="{y+4:.2f}" text-anchor="end" class="muted">{value:.4g}</text>',
        ))
    if len(points) > 1:
        coordinates = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(x_positions, y_positions))
        lines.append(f'  <polyline points="{coordinates}" fill="none" stroke="{palette["line"]}" stroke-width="3"/>')
    for point, x, y in zip(points, x_positions, y_positions):
        observed, value, samples, run_id = point
        label = f"{observed} · {value:.6g} {unit} · n={samples} · {run_id}"
        lines.append(f'  <circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{palette["line"]}"><title>{html.escape(label)}</title></circle>')
        lines.append(f'  <text x="{x:.2f}" y="{height-bottom+22}" text-anchor="middle" class="muted">{html.escape(observed)}</text>')
    lines.append('</svg>')
    _atomic_write(target, ("\n".join(lines) + "\n").encode("utf-8"))
    return target


def render_metric_summary(
    registry_root: str | Path,
    campaign_id: str,
    metric_name: str,
    output_path: str | Path | None = None,
    *,
    include_bundle_provenance: bool = False,
) -> Path | None:
    """Write a text alternative table from the same approved metric series."""
    if not isinstance(include_bundle_provenance, bool):
        raise CampaignDataError("Bundle provenance option must be boolean")
    series = _approved_metric_series(registry_root, campaign_id, metric_name)
    if series is None:
        return None
    campaign, metric, points, unit = series
    target = Path(output_path) if output_path is not None else (
        Path(registry_root).parent / "campaign-results" / campaign / f"{metric}.md")
    target.parent.mkdir(parents=True, exist_ok=True)

    def code_span(value: str) -> str:
        longest_backtick_run = max(
            (len(match.group(0)) for match in re.finditer(r"`+", value)),
            default=0,
        )
        delimiter = "`" * (longest_backtick_run + 1)
        return f"{delimiter} {value} {delimiter}"

    def cell(value: str) -> str:
        return "&#124;".join(code_span(part) for part in value.split("|"))

    lines = [
        f"# {campaign}: {metric}",
        "",
        "Approved, indexed observations, ordered by observation date. Superseded runs are excluded.",
        "",
        "| Observed on | Value | Unit | Sample count | Run |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    lines.extend(
        f"| {observed} | {cell(str(value))} | {cell(unit)} | {samples} | {run_id} |"
        for observed, value, samples, run_id in points
    )
    if include_bundle_provenance:
        lines.extend(("", "[Bundle provenance](../../provenance.json) contains source revisions, hashes and approval references."))
    _atomic_write(target, ("\n".join(lines) + "\n").encode("utf-8"))
    return target


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="create a new empty protected index")
    subparsers.add_parser("verify", help="verify every run against the protected index")
    append_parser = subparsers.add_parser("append", help="append one approved run from a JSON file")
    append_parser.add_argument("--input", required=True, type=Path)
    chart_parser = subparsers.add_parser("chart", help="render a deterministic indexed metric chart")
    chart_parser.add_argument("--campaign-id", required=True)
    chart_parser.add_argument("--metric-name", required=True)
    chart_parser.add_argument("--output", type=Path)
    chart_parser.add_argument("--theme", choices=tuple(CAMPAIGN_CHART_THEMES), default="light")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(initialize_registry(args.registry))
        elif args.command == "verify":
            print(f"Verified {len(verify_registry(args.registry))} indexed runs")
        elif args.command == "append":
            record = _load_json(args.input, "run input")
            run = append_run(args.registry, record)
            print(f"Indexed {run['run_id']}")
        elif args.command == "chart":
            output = render_metric_chart(args.registry, args.campaign_id,
                                         args.metric_name, args.output, theme=args.theme)
            print(output if output is not None else "No approved indexed runs; no chart written")
        return 0
    except (CampaignDataError, OSError) as error:
        print(f"campaigns: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
