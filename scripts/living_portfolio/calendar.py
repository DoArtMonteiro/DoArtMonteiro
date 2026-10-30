#!/usr/bin/env python3
"""Render deterministic contribution-calendar assets using only the standard library."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "assets" / "contribution-values.csv"
DEFAULT_OUTPUT = ROOT / "previews" / "living_portfolio"
SOURCE_SCOPE_LABELS = {
    "historical_profile": "Historical profile CSV; may include anonymised private contributions.",
    "selected_public_repo_commits": (
        "Distinct commits linked to DoArtMonteiro on selected public repositories' default branches; "
        "dated by author timestamp in UTC. This is a subset of GitHub activity, not the full profile graph."
    ),
}
OUTPUT_NAMES = (
    "calendar-light.svg",
    "calendar-dark.svg",
    "calendar-accessible.svg",
    "calendar-isometric.svg",
    "calendar-normalized.csv",
    "calendar-manifest.json",
)
LEVEL_INTERVALS = ((0, 0), (1, 5), (6, 12), (13, 24), (25, None))


class CalendarDataError(ValueError):
    """Raised when the daily CSV is malformed or does not describe a daily series."""


@dataclass(frozen=True)
class DailyValue:
    day: date
    contributions: int


THEMES = {
    "light": {
        "background": "#ffffff", "ink": "#1f2328", "muted": "#59636e",
        "grid": "#d1d9e0", "base": ("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"),
        "side_a": ("#dfe3e8", "#78cf89", "#32a953", "#288f47", "#1c6032"),
        "side_b": ("#e5e9ed", "#8bdc9a", "#38b65d", "#2b9b4d", "#1e6938"),
        "accent": "#1f883d",
    },
    "dark": {
        "background": "#15191b", "ink": "#f0f4f1", "muted": "#bcc7c3",
        "grid": "#394547", "base": ("#283033", "#49644b", "#6e945e", "#93b96c", "#c0d987"),
        "side_a": ("#1d2527", "#3d5740", "#5c7d4d", "#779653", "#9fb96a"),
        "side_b": ("#222a2c", "#435e46", "#648854", "#83a85e", "#b0ca79"),
        "accent": "#a1cf74",
    },
    "night": {
        "background": "#08111f", "ink": "#edf4f7", "muted": "#a9bdc9",
        "grid": "#2c4053", "base": ("#1b2836", "#226b63", "#36a99b", "#71d5bd", "#ffc46b"),
        "side_a": ("#142131", "#195a55", "#288d84", "#4bb69f", "#d1904e"),
        "side_b": ("#172534", "#1d635b", "#2f9b8f", "#5bc5aa", "#e5a75e"),
        "accent": "#55d6d0",
    },
}


def level_for_count(count: int) -> int:
    if count == 0:
        return 0
    if count <= 5:
        return 1
    if count <= 12:
        return 2
    if count <= 24:
        return 3
    return 4


def _read_source(path: Path) -> tuple[bytes, list[DailyValue]]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise CalendarDataError(f"Cannot read UTF-8 calendar CSV: {path.name}: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != ["date", "contributions"]:
        raise CalendarDataError("CSV header must be exactly: date,contributions")

    values: list[DailyValue] = []
    seen: set[date] = set()
    for line_number, row in enumerate(reader, start=2):
        if None in row or row.get("date") is None or row.get("contributions") is None:
            raise CalendarDataError(f"CSV row {line_number} must contain exactly two fields")
        date_text = row["date"]
        count_text = row["contributions"]
        try:
            day = date.fromisoformat(date_text)
        except (TypeError, ValueError) as exc:
            raise CalendarDataError(f"Invalid ISO date on CSV row {line_number}: {date_text!r}") from exc
        if date_text != day.isoformat():
            raise CalendarDataError(f"Date on CSV row {line_number} must use YYYY-MM-DD")
        if day in seen:
            raise CalendarDataError(f"Duplicate date on CSV row {line_number}: {date_text}")
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)", count_text or ""):
            raise CalendarDataError(f"Contribution count on CSV row {line_number} must be a non-negative integer")
        count = int(count_text)
        if values and day != values[-1].day + timedelta(days=1):
            raise CalendarDataError(
                f"Dates must be consecutive and ordered; row {line_number} follows {values[-1].day.isoformat()}"
            )
        values.append(DailyValue(day, count))
        seen.add(day)

    if not values:
        raise CalendarDataError("CSV must contain at least one dated contribution row")
    return raw, values


def _normalised_csv(values: Iterable[DailyValue]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("date", "contributions"))
    writer.writerows((value.day.isoformat(), value.contributions) for value in values)
    return stream.getvalue()


def _count_label(source_scope: str) -> str:
    return "commits" if source_scope == "selected_public_repo_commits" else "contributions"


def _header_summary(values: list[DailyValue], source_scope: str) -> str:
    active = sum(value.contributions > 0 for value in values)
    total = sum(value.contributions for value in values)
    return f"{total} {_count_label(source_scope)} across {active} active days; {len(values)} dated days."


def _svg_header(title: str, description: str, width: int, height: int,
                background: str, ink: str, muted: str, grid: str, accent: str,
                summary: str, source_date: date) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">\n'
        f'  <title id="title">{html.escape(title)}</title>\n'
        f'  <desc id="desc">{html.escape(description)}</desc>\n'
        f'  <style>text{{font-family:Arial,sans-serif}}.muted{{font-size:13px}}.grid-line{{stroke:{grid}}}.accent{{fill:{accent}}}</style>\n'
        f'  <rect width="{width}" height="{height}" fill="{background}"/>\n'
        f'  <text x="34" y="42" fill="{ink}" font-size="27" font-weight="700">{html.escape(title)}</text>\n'
        f'  <text x="34" y="69" fill="{muted}" font-size="12">Data through {source_date:%d %b %Y} · {html.escape(summary)}</text>\n'
    )


def _sunday_index(day: date) -> int:
    return (day.weekday() + 1) % 7


def _calendar_positions(values: list[DailyValue]) -> dict[date, tuple[int, int, int]]:
    first_sunday = values[0].day - timedelta(days=_sunday_index(values[0].day))
    positions: dict[date, tuple[int, int, int]] = {}
    for value in values:
        offset = (value.day - first_sunday).days
        positions[value.day] = (offset // 7, offset % 7, value.contributions)
    return positions


def _legend_svg(theme: dict[str, object], y: int, start_x: int = 34) -> list[str]:
    palette = theme["base"]
    assert isinstance(palette, tuple)
    labels = ("0", "1 to 5", "6 to 12", "13 to 24", "25+")
    output = [f'<text x="{start_x}" y="{y}" fill="{theme["ink"]}" font-size="13" font-weight="700">Daily count:</text>']
    x = start_x + 88
    for level, label in enumerate(labels):
        output.append(f'<rect x="{x}" y="{y - 13}" width="13" height="13" rx="2" fill="{palette[level]}"/>')
        output.append(f'<text x="{x + 18}" y="{y}" fill="{theme["muted"]}" font-size="13">{label}</text>')
        x += 112 if level < 2 else 126
    return output


def _svg_number(value: int | float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.12g}"


def _render_isometric(values: list[DailyValue], theme_name: str, source_scope: str) -> str:
    theme = THEMES[theme_name]
    count_label = _count_label(source_scope)
    positions = _calendar_positions(values)
    week_dx, week_dy = 18, 8
    day_dx, day_dy = -18, 8
    x0, y0 = 140, 220
    maximum_cell_height = 75
    maximum_count = max(item.contributions for item in values)
    height_per_count = min(3.0, maximum_cell_height / maximum_count) if maximum_count else 3.0
    active = sum(item.contributions > 0 for item in values)
    total = sum(item.contributions for item in values)
    description = (
        f"Isometric public commit calendar from {values[0].day:%d %b %Y} to "
        f"{values[-1].day:%d %b %Y}. {len(values)} dated cells include zero-count days; "
        f"{total} {count_label} across {active} active days. Cell height is linear within this snapshot, "
        f"at {_svg_number(height_per_count)} SVG units per {count_label[:-1]}, with a maximum bar height of "
        f"{maximum_cell_height} units. "
        f"Source scope: {SOURCE_SCOPE_LABELS[source_scope]} "
        "Aggregate counts do not measure work quality or intensity."
    )
    parts = [_svg_header("Selected public-repository commits", description, 1200, 830,
                         str(theme["background"]), str(theme["ink"]), str(theme["muted"]),
                         str(theme["grid"]), str(theme["accent"]), _header_summary(values, source_scope), values[-1].day)]
    parts.append(f'<text x="34" y="101" fill="{theme["muted"]}" font-size="13">Source scope: {html.escape(SOURCE_SCOPE_LABELS[source_scope])} All {len(values)} dated cells are shown, including zeros; totals do not measure work quality or intensity.</text>')
    months: set[tuple[int, int]] = set()
    for item in values:
        week, weekday, count = positions[item.day]
        x = x0 + week * week_dx + weekday * day_dx
        y = y0 + week * week_dy + weekday * day_dy
        height = count * height_per_count
        p0 = (x, y)
        p1 = (x + week_dx, y + week_dy)
        p2 = (x + week_dx + day_dx, y + week_dy + day_dy)
        p3 = (x + day_dx, y + day_dy)
        top_points = ((p0[0], p0[1] - height), (p1[0], p1[1] - height),
                      (p2[0], p2[1] - height), (p3[0], p3[1] - height))
        level = level_for_count(count)
        title = html.escape(f"{item.day.isoformat()}: {count} {count_label}")
        parts.append(f'<g class="day level-{level}" data-date="{item.day.isoformat()}" data-count="{count}" data-level="{level}" data-height="{_svg_number(height)}"><title>{title}</title>')
        if height:
            sa = theme["side_a"][level]
            sb = theme["side_b"][level]
            parts.append(f'<polygon points="{_svg_number(top_points[0][0])},{_svg_number(top_points[0][1])} {_svg_number(top_points[3][0])},{_svg_number(top_points[3][1])} {_svg_number(p3[0])},{_svg_number(p3[1])} {_svg_number(p0[0])},{_svg_number(p0[1])}" fill="{sa}"/>')
            parts.append(f'<polygon points="{_svg_number(top_points[1][0])},{_svg_number(top_points[1][1])} {_svg_number(top_points[2][0])},{_svg_number(top_points[2][1])} {_svg_number(p2[0])},{_svg_number(p2[1])} {_svg_number(p1[0])},{_svg_number(p1[1])}" fill="{sb}"/>')
        points_text = " ".join(f"{_svg_number(px)},{_svg_number(py)}" for px, py in top_points)
        parts.append(f'<polygon class="top level-{level}" points="{points_text}" fill="{theme["base"][level]}" stroke="{theme["background"]}" stroke-width="0.7"/>')
        parts.append("</g>")
        key = (item.day.year, item.day.month)
        if key not in months:
            months.add(key)
            label_y = y - height - 13
            label = item.day.strftime("%b %Y")
            parts.append(f'<text x="{x - 5}" y="{label_y}" fill="{theme["muted"]}" font-size="11">{html.escape(label)}</text>')

    parts.append(f'<text x="34" y="730" fill="{theme["ink"]}" font-size="14" font-weight="700">Start: {values[0].day:%d %b %Y}</text>')
    parts.append(f'<text x="870" y="730" fill="{theme["ink"]}" font-size="14" font-weight="700">End: {values[-1].day:%d %b %Y}</text>')
    parts.extend(_legend_svg(theme, 770))
    parts.append(f'<text x="34" y="808" fill="{theme["muted"]}" font-size="12">Linear within this snapshot: {_svg_number(height_per_count)} SVG units per {count_label[:-1]}; maximum bar {maximum_cell_height} units. Exact counts appear in each cell description.</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _render_accessible_2d(values: list[DailyValue], source_scope: str) -> str:
    theme = THEMES["light"]
    count_label = _count_label(source_scope)
    positions = _calendar_positions(values)
    max_week = max(week for week, _, _ in positions.values())
    cell, gap, grid_x, grid_y = 13, 4, 93, 135
    step = cell + gap
    width = grid_x + (max_week + 1) * step + 34
    height = 390
    description = (
        f"Accessible two-dimensional calendar. All {len(values)} dated cells from "
        f"{values[0].day:%d %b %Y} to {values[-1].day:%d %b %Y}, including zero-count days, are shown. "
        f"Total {sum(item.contributions for item in values)} {count_label} across "
        f"{sum(item.contributions > 0 for item in values)} active days. "
        f"Source scope: {SOURCE_SCOPE_LABELS[source_scope]} "
        "This aggregate does not measure work quality or intensity."
    )
    parts = [_svg_header("Selected public-repository commits, daily values", description, width, height,
                         str(theme["background"]), str(theme["ink"]), str(theme["muted"]),
                         str(theme["grid"]), str(theme["accent"]), _header_summary(values, source_scope), values[-1].day)]
    parts.append(f'<text x="34" y="99" fill="#1f2328" font-size="13">Source scope: {html.escape(SOURCE_SCOPE_LABELS[source_scope])} All dated days are shown, including zero-count days.</text>')
    months: set[tuple[int, int]] = set()
    for item in values:
        week, weekday, count = positions[item.day]
        x, y = grid_x + week * step, grid_y + weekday * step
        level = level_for_count(count)
        title = html.escape(f"{item.day.isoformat()}: {count} {count_label}")
        parts.append(
            f'<g class="day level-{level}" data-date="{item.day.isoformat()}" data-count="{count}" data-level="{level}" role="listitem" aria-label="{title}">'
            f'<title>{title}</title><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" '
            f'fill="{theme["base"][level]}" stroke="{theme["grid"]}" stroke-width="0.5"/></g>'
        )
        key = (item.day.year, item.day.month)
        if key not in months:
            months.add(key)
            parts.append(f'<text x="{x}" y="122" fill="{theme["muted"]}" font-size="11">{html.escape(item.day.strftime("%b %Y"))}</text>')

    for weekday, label in enumerate(("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")):
        parts.append(f'<text x="34" y="{grid_y + weekday * step + 11}" fill="{theme["muted"]}" font-size="11">{label}</text>')
    parts.append(f'<text x="34" y="274" fill="{theme["ink"]}" font-size="13" font-weight="700">Start: {values[0].day:%d %b %Y}</text>')
    parts.append(f'<text x="220" y="274" fill="{theme["ink"]}" font-size="13" font-weight="700">End: {values[-1].day:%d %b %Y}</text>')
    parts.extend(_legend_svg(theme, 309))
    parts.append(f'<text x="34" y="350" fill="{theme["muted"]}" font-size="12">Daily-count ranges are shown above. Aggregate counts do not measure work quality or intensity.</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _build_artifacts(source_path: Path, source_bytes: bytes,
                     values: list[DailyValue], source_scope: str) -> dict[str, bytes]:
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    active_values = [item for item in values if item.contributions > 0]
    week_count = max(week for week, _, _ in _calendar_positions(values).values()) + 1
    manifest = {
        "renderer": "living-portfolio-calendar",
        "renderer_version": 2,
        "source_file": source_path.name,
        "source_sha256": source_digest,
        "source_snapshot_date": values[-1].day.isoformat(),
        "source_snapshot_date_basis": "Latest dated row in the validated CSV.",
        "source_scope": source_scope,
        "source_scope_description": SOURCE_SCOPE_LABELS[source_scope],
        "observed_interval": {
            "start": values[0].day.isoformat(),
            "end": values[-1].day.isoformat(),
            "dated_days": len(values),
        },
        "commit_activity": {
            "total_commits": sum(item.contributions for item in values),
            "active_days": len(active_values),
            "zero_days": len(values) - len(active_values),
            "first_active_day": active_values[0].day.isoformat() if active_values else None,
            "last_active_day": active_values[-1].day.isoformat() if active_values else None,
            "maximum_daily_commits": max(item.contributions for item in values),
        },
        "level_intervals": [
            {"level": index, "minimum": low, "maximum": high}
            for index, (low, high) in enumerate(LEVEL_INTERVALS)
        ],
        "isometric_projection": {
            "week_vector": [18, 8],
            "weekday_vector": [-18, 8],
            "vertical_height_per_contribution": min(
                3.0, 75 / max(item.contributions for item in values),
            ) if max(item.contributions for item in values) else 3.0,
            "maximum_cell_height": 75,
            "calendar_weeks": week_count,
            "dated_cells_only": True,
        },
        "quality_caveat": "Aggregate counts do not measure work quality or intensity.",
        "files": list(OUTPUT_NAMES),
    }
    artifacts = {
        "calendar-light.svg": _render_isometric(values, "light", source_scope).encode("utf-8"),
        "calendar-dark.svg": _render_isometric(values, "dark", source_scope).encode("utf-8"),
        "calendar-accessible.svg": _render_accessible_2d(values, source_scope).encode("utf-8"),
        "calendar-isometric.svg": _render_isometric(values, "night", source_scope).encode("utf-8"),
        "calendar-normalized.csv": _normalised_csv(values).encode("utf-8"),
    }
    manifest["artifact_sha256"] = {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(artifacts.items())
    }
    artifacts["calendar-manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return artifacts


def _atomic_write_bundle(output_dir: Path, source_digest: str,
                         artifacts: dict[str, bytes]) -> dict[str, Path]:
    versions = output_dir / "versions"
    bundle_hasher = hashlib.sha256()
    for name in sorted(artifacts):
        bundle_hasher.update(name.encode("utf-8"))
        bundle_hasher.update(b"\0")
        bundle_hasher.update(artifacts[name])
        bundle_hasher.update(b"\0")
    bundle_name = f"sha256-{bundle_hasher.hexdigest()}"
    bundle = versions / bundle_name
    pointer_path = output_dir / "current.json"
    pointer = {
        "bundle": f"versions/{bundle_name}",
        "manifest": f"versions/{bundle_name}/calendar-manifest.json",
        "source_sha256": source_digest,
        "files": list(OUTPUT_NAMES),
    }
    pointer_bytes = (json.dumps(pointer, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    versions.mkdir(parents=True, exist_ok=True)
    if bundle.exists():
        for name, content in artifacts.items():
            target = bundle / name
            if not target.is_file() or target.read_bytes() != content:
                raise RuntimeError(f"Existing content-addressed bundle is incomplete or inconsistent: {bundle}")
    else:
        stage = Path(tempfile.mkdtemp(prefix=".calendar-stage-", dir=versions))
        try:
            for name, content in artifacts.items():
                (stage / name).write_bytes(content)
            os.replace(stage, bundle)
        finally:
            if stage.exists():
                for child in stage.iterdir():
                    child.unlink()
                stage.rmdir()

    if not pointer_path.is_file() or pointer_path.read_bytes() != pointer_bytes:
        fd, temp_name = tempfile.mkstemp(prefix=".current-", dir=output_dir)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(pointer_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, pointer_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    result = {name.removesuffix(".svg").removesuffix(".csv").removesuffix(".json"): bundle / name
              for name in OUTPUT_NAMES}
    result["bundle"] = bundle
    result["current"] = pointer_path
    return result


def render_calendar(source_csv: Path, output_dir: Path,
                    source_scope: str = "historical_profile") -> dict[str, Path]:
    """Validate the CSV, then publish a complete immutable asset bundle atomically.

    The returned mapping includes each output path plus ``bundle`` and ``current``.
    Invalid source data raises CalendarDataError before any output path is changed.
    Each call consumes one CSV with one scope; sources are never merged.
    """
    source_csv = Path(source_csv)
    output_dir = Path(output_dir)
    if source_scope not in SOURCE_SCOPE_LABELS:
        raise CalendarDataError(f"Unknown source scope: {source_scope!r}")
    raw, values = _read_source(source_csv)
    artifacts = _build_artifacts(source_csv, raw, values, source_scope)
    digest = hashlib.sha256(raw).hexdigest()
    return _atomic_write_bundle(output_dir, digest, artifacts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Daily CSV with date,contributions columns")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help="Output root for immutable versions and current.json")
    parser.add_argument("--source-scope", choices=tuple(SOURCE_SCOPE_LABELS),
                        default="historical_profile",
                        help="Label this input CSV as historical profile data or public contributions only")
    args = parser.parse_args(argv)
    try:
        outputs = render_calendar(args.input, args.output_dir, args.source_scope)
    except (CalendarDataError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: str(path) for key, path in outputs.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
