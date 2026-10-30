"""Create small, deterministic, finite GIF versions of the contribution calendar.

The renderer uses only the Python standard library and the validated CSV contract
from :mod:`calendar`.  It emits one light and one dark GIF from the same snapshot.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable

from scripts.living_portfolio import calendar as calendar_renderer


FRAME_COUNT = 8
FRAME_DELAY_CENTISECONDS = 18
MAX_GIF_DIMENSION = 65_535
CELL_WEEK_VECTOR = (10, 5)
CELL_DAY_VECTOR = (-10, 5)


def _rgb(colour: str) -> tuple[int, int, int]:
    value = colour.removeprefix("#")
    return tuple(int(value[offset:offset + 2], 16) for offset in (0, 2, 4))  # type: ignore[return-value]


def _palette(theme_name: str) -> tuple[list[int], dict[str, int]]:
    theme = calendar_renderer.THEMES[theme_name]
    colours = [str(theme["background"]), str(theme["grid"]), str(theme["accent"])]
    for key in ("base", "side_a", "side_b"):
        colours.extend(theme[key])  # type: ignore[arg-type]
    # Keep the palette stable and index-based for deterministic GIF bytes.
    rgb_values = [_rgb(colour) for colour in colours]
    indices: dict[str, int] = {}
    for index, colour in enumerate(colours):
        indices.setdefault(colour, index)
    table = [channel for rgb in rgb_values for channel in rgb]
    table.extend([0] * (768 - len(table)))
    return table, indices


def _polygon(pixels: bytearray, width: int, height: int,
             points: tuple[tuple[int, int], ...], colour: int) -> None:
    """Fill a small integer polygon using scanlines, clipped to the canvas."""
    min_y = max(0, min(y for _, y in points))
    max_y = min(height - 1, max(y for _, y in points))
    for y in range(min_y, max_y + 1):
        scan_y = y + 0.5
        intersections: list[float] = []
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
            if (y1 <= scan_y < y2) or (y2 <= scan_y < y1):
                intersections.append(x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1))
        intersections.sort()
        for left, right in zip(intersections[::2], intersections[1::2]):
            start = max(0, int(left + 0.5))
            end = min(width - 1, int(right - 0.5))
            if start <= end:
                offset = y * width + start
                pixels[offset:offset + end - start + 1] = bytes((colour,)) * (end - start + 1)


def _line(pixels: bytearray, width: int, height: int,
          start: tuple[int, int], end: tuple[int, int], colour: int) -> None:
    """Draw a clipped one-pixel Bresenham line for readable calendar cell edges."""
    x, y = start
    end_x, end_y = end
    dx, dy = abs(end_x - x), -abs(end_y - y)
    step_x = 1 if x < end_x else -1
    step_y = 1 if y < end_y else -1
    error = dx + dy
    while True:
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = colour
        if x == end_x and y == end_y:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x += step_x
        if doubled <= dx:
            error += dx
            y += step_y


def _frame(values: list[calendar_renderer.DailyValue], theme_name: str,
           frame_number: int, width: int, height: int,
           colours: dict[str, int]) -> bytes:
    theme = calendar_renderer.THEMES[theme_name]
    positions = calendar_renderer._calendar_positions(values)
    pixels = bytearray([colours[str(theme["background"])]] * (width * height))
    x0, y0 = 80, 42
    week_dx, week_dy = CELL_WEEK_VECTOR
    day_dx, day_dy = CELL_DAY_VECTOR
    max_count = max(item.contributions for item in values)
    height_per_count = min(1.8, 42 / max_count) if max_count else 1.8
    max_week = max(week for week, _, _ in positions.values())
    sweep_week = round(frame_number * max_week / (FRAME_COUNT - 1))

    for item in values:
        week, weekday, count = positions[item.day]
        x, y = x0 + week * week_dx + weekday * day_dx, y0 + week * week_dy + weekday * day_dy
        rise = round(count * height_per_count)
        p0, p1 = (x, y), (x + week_dx, y + week_dy)
        p2, p3 = (x + week_dx + day_dx, y + week_dy + day_dy), (x + day_dx, y + day_dy)
        level = calendar_renderer.level_for_count(count)
        top = tuple((px, py - rise) for px, py in (p0, p1, p2, p3))
        if rise:
            _polygon(pixels, width, height, (top[0], top[3], p3, p0),
                     colours[str(theme["side_a"][level])])
            _polygon(pixels, width, height, (top[1], top[2], p2, p1),
                     colours[str(theme["side_b"][level])])
        face_colour = (colours[str(theme["accent"])]
                       if week == sweep_week and frame_number in (2, 3, 4)
                       else colours[str(theme["base"][level])])
        _polygon(pixels, width, height, top, face_colour)
        for start, end in zip(top, top[1:] + top[:1]):
            _line(pixels, width, height, start, end, colours[str(theme["grid"])])
    return bytes(pixels)


def _lzw(data: bytes) -> bytes:
    """Encode 8-bit palette indices as GIF LZW sub-block data."""
    clear, end = 256, 257
    dictionary = {bytes((index,)): index for index in range(256)}
    next_code, code_size = 258, 9
    codes: list[tuple[int, int]] = [(clear, code_size)]
    phrase = bytes((data[0],))
    for value in data[1:]:
        symbol = bytes((value,))
        combined = phrase + symbol
        if combined in dictionary:
            phrase = combined
            continue
        codes.append((dictionary[phrase], code_size))
        if next_code < 4096:
            dictionary[combined] = next_code
            next_code += 1
            # The encoder is one dictionary entry ahead of the decoder. Keep
            # writing the next code at the old width until code 512 is added.
            if next_code > 1 << code_size and code_size < 12:
                code_size += 1
        else:
            codes.append((clear, code_size))
            dictionary = {bytes((index,)): index for index in range(256)}
            next_code, code_size = 258, 9
        phrase = symbol
    codes.append((dictionary[phrase], code_size))
    codes.append((end, code_size))

    packed = bytearray()
    bit_buffer = bit_count = 0
    for code, size in codes:
        bit_buffer |= code << bit_count
        bit_count += size
        while bit_count >= 8:
            packed.append(bit_buffer & 0xFF)
            bit_buffer >>= 8
            bit_count -= 8
    if bit_count:
        packed.append(bit_buffer & 0xFF)
    blocks = bytearray()
    for offset in range(0, len(packed), 255):
        chunk = packed[offset:offset + 255]
        blocks.append(len(chunk))
        blocks.extend(chunk)
    blocks.append(0)
    return bytes(blocks)


def _gif(width: int, height: int, frames: Iterable[bytes], palette: list[int],
         source_digest: str) -> bytes:
    """Build a GIF89a animation with source provenance and no looping extension."""
    output = bytearray(b"GIF89a")
    output.extend(width.to_bytes(2, "little"))
    output.extend(height.to_bytes(2, "little"))
    output.extend((0xF7, 0, 0))  # Global 256-entry colour table, 8-bit colour resolution.
    output.extend(bytes(palette[:768]))
    comment = f"source-sha256:{source_digest}".encode("ascii")
    output.extend(b"!\xFE")
    for offset in range(0, len(comment), 255):
        chunk = comment[offset:offset + 255]
        output.append(len(chunk))
        output.extend(chunk)
    output.append(0)
    for frame in frames:
        output.extend(b"!\xF9\x04\x00")  # No disposal or transparency.
        output.extend(FRAME_DELAY_CENTISECONDS.to_bytes(2, "little"))
        output.extend(b"\x00\x00")
        output.append(0x2C)
        output.extend(b"\x00\x00\x00\x00")
        output.extend(width.to_bytes(2, "little"))
        output.extend(height.to_bytes(2, "little"))
        output.append(0)  # Use the global colour table.
        output.append(8)
        output.extend(_lzw(frame))
    output.append(0x3B)
    return bytes(output)


def calendar_alt_text(values: Iterable[calendar_renderer.DailyValue], source_scope: str) -> str:
    """Return factual alt text for a rendered calendar, suitable for HTML integration.

    Pass the same validated daily values used to render the GIF. This deliberately
    describes the data rather than the animation, and avoids claims about work quality.
    Pair the GIF with a static SVG for reduced-motion users and include the source
    snapshot SHA-256 returned by :func:`render_animated_calendars` in provenance data.
    """
    if source_scope not in calendar_renderer.SOURCE_SCOPE_LABELS:
        raise calendar_renderer.CalendarDataError("Unsupported calendar source scope")
    rows = list(values)
    total = sum(row.contributions for row in rows)
    active = sum(row.contributions > 0 for row in rows)
    count_label = calendar_renderer._count_label(source_scope)
    calendar_kind = "commit" if source_scope == "selected_public_repo_commits" else "contribution"
    return (f"Isometric daily {calendar_kind} calendar from {rows[0].day.isoformat()} to "
            f"{rows[-1].day.isoformat()}: {total} {count_label} across {active} active days "
            f"and {len(rows)} dated days, including zero-count days.")


def _write_if_changed(path: Path, content: bytes) -> None:
    if path.is_file() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def render_animated_calendars(source_csv: Path, output_dir: Path,
                              *, source_scope: str) -> dict[str, object]:
    """Validate a daily CSV and write deterministic light and dark finite GIFs.

    The CSV must have the existing ``date,contributions`` schema and pass the
    calendar renderer's strict date, ordering, consecutiveness, and count checks.
    Validation and both GIF encodings complete before either target is touched.
    The result includes variant paths, a source-content SHA-256 for provenance, and
    data-derived alt text. Existing files are left intact when source validation fails.
    """
    if source_scope not in calendar_renderer.SOURCE_SCOPE_LABELS:
        raise calendar_renderer.CalendarDataError("Unsupported calendar source scope")
    source_csv, output_dir = Path(source_csv), Path(output_dir)
    raw, values = calendar_renderer._read_source(source_csv)
    positions = calendar_renderer._calendar_positions(values)
    max_week = max(week for week, _, _ in positions.values())
    width, height = 160 + max_week * CELL_WEEK_VECTOR[0], 112 + max_week * CELL_WEEK_VECTOR[1]
    if max(width, height) > MAX_GIF_DIMENSION:
        raise calendar_renderer.CalendarDataError("Calendar is too large for the GIF format")

    artifacts: dict[str, bytes] = {}
    for theme_name in ("light", "dark"):
        palette, colours = _palette(theme_name)
        frames = [_frame(values, theme_name, index, width, height, colours)
                  for index in range(FRAME_COUNT)]
        source_digest = hashlib.sha256(raw).hexdigest()
        artifacts[f"calendar-{theme_name}-animated.gif"] = _gif(
            width, height, frames, palette, source_digest,
        )

    # Both complete outputs are prepared before publishing, so invalid data and
    # encoding errors cannot leave a partial variant set.
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        _write_if_changed(output_dir / name, content)
    return {
        "light": output_dir / "calendar-light-animated.gif",
        "dark": output_dir / "calendar-dark-animated.gif",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "alt_text": calendar_alt_text(values, source_scope),
    }
