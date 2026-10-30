from __future__ import annotations

import csv
from datetime import date, timedelta
import tempfile
import unittest
from pathlib import Path

from scripts.living_portfolio.animated_calendar import (
    FRAME_COUNT,
    _palette,
    render_animated_calendars,
)
from scripts.living_portfolio.calendar import THEMES


def write_fixture(path: Path, first_count: int = 1) -> None:
    start = date(2025, 10, 26)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("date", "contributions"))
        for index in range(370):
            count = first_count if index == 0 else (index % 4 if index % 2 == 0 else 0)
            writer.writerow(((start + timedelta(days=index)).isoformat(), count))


def decode_lzw(payload: bytes, minimum_code_size: int) -> bytes:
    """Decode GIF LZW data blocks to verify that frame streams are well formed."""
    clear, end = 1 << minimum_code_size, (1 << minimum_code_size) + 1
    dictionary = [bytes((value,)) for value in range(clear)] + [b"", b""]
    next_code, code_size, bit_offset = end + 1, minimum_code_size + 1, 0
    previous = None
    decoded = bytearray()
    saw_end = False
    while bit_offset + code_size <= len(payload) * 8:
        byte_offset = bit_offset // 8
        shift = bit_offset % 8
        code = int.from_bytes(payload[byte_offset:byte_offset + 3], "little") >> shift
        code &= (1 << code_size) - 1
        bit_offset += code_size
        if code == clear:
            dictionary = [bytes((value,)) for value in range(clear)] + [b"", b""]
            next_code, code_size, previous = end + 1, minimum_code_size + 1, None
            continue
        if code == end:
            saw_end = True
            break
        if code < len(dictionary) and dictionary[code]:
            entry = dictionary[code]
        elif code == next_code and previous is not None:
            entry = previous + previous[:1]
        else:
            raise AssertionError(f"invalid GIF LZW code: {code}")
        decoded.extend(entry)
        if previous is not None and next_code < 4096:
            dictionary.append(previous + entry[:1])
            next_code += 1
            if next_code == 1 << code_size and code_size < 12:
                code_size += 1
        previous = entry
    if not saw_end:
        raise AssertionError("GIF LZW stream has no end code")
    return bytes(decoded)


def gif_frames(data: bytes) -> tuple[tuple[int, int], list[bytes]]:
    """Read dimensions and decode every image descriptor without external libraries."""
    if data[:6] != b"GIF89a":
        raise AssertionError("expected GIF89a")
    dimensions = (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))
    offset, frames = 13 + 768, []
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            break
        if marker == 0x21:
            offset += 1  # Extension label.
            while data[offset]:
                offset += 1 + data[offset]
            offset += 1
            continue
        if marker != 0x2C:
            raise AssertionError(f"unexpected GIF block marker: {marker:#x}")
        packed = data[offset + 8]
        offset += 9
        if packed & 0x80:
            offset += 3 * (2 ** ((packed & 0x07) + 1))
        minimum_code_size = data[offset]
        offset += 1
        compressed = bytearray()
        while data[offset]:
            block_size = data[offset]
            offset += 1
            compressed.extend(data[offset:offset + block_size])
            offset += block_size
        offset += 1
        frames.append(decode_lzw(bytes(compressed), minimum_code_size))
    return dimensions, frames


class AnimatedCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "daily.csv"
        self.output = self.root / "rendered"
        write_fixture(self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_valid_input_creates_light_and_dark_animated_gifs(self) -> None:
        result = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )

        light, dark = Path(result["light"]).read_bytes(), Path(result["dark"]).read_bytes()
        light_size, light_frames = gif_frames(light)
        dark_size, dark_frames = gif_frames(dark)
        self.assertEqual(light_size, dark_size)
        self.assertEqual(len(light_frames), FRAME_COUNT)
        self.assertEqual(len(dark_frames), FRAME_COUNT)
        self.assertTrue(all(len(frame) == light_size[0] * light_size[1] for frame in light_frames))
        self.assertTrue(all(len(frame) == dark_size[0] * dark_size[1] for frame in dark_frames))
        light_grid = _palette("light")[1][str(THEMES["light"]["grid"])]
        dark_grid = _palette("dark")[1][str(THEMES["dark"]["grid"])]
        self.assertIn(light_grid, light_frames[0])
        self.assertIn(dark_grid, dark_frames[0])
        self.assertNotEqual(light, dark)
        self.assertIn("370 dated days", result["alt_text"])
        self.assertIn("commits across", result["alt_text"])
        self.assertEqual(len(result["source_sha256"]), 64)
        self.assertIn(result["source_sha256"].encode("ascii"), light)
        self.assertIn(result["source_sha256"].encode("ascii"), dark)

    def test_renders_are_deterministic_and_idempotent(self) -> None:
        first = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )
        paths = (Path(first["light"]), Path(first["dark"]))
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

        second = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )

        self.assertEqual(first["source_sha256"], second["source_sha256"])
        for path, (content, modified_ns) in before.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, modified_ns)

    def test_changed_source_changes_both_variants(self) -> None:
        first = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )
        previous = (Path(first["light"]).read_bytes(), Path(first["dark"]).read_bytes())
        write_fixture(self.source, first_count=3)

        second = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )

        self.assertNotEqual(previous[0], Path(second["light"]).read_bytes())
        self.assertNotEqual(previous[1], Path(second["dark"]).read_bytes())
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])

    def test_invalid_input_preserves_previous_files(self) -> None:
        result = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )
        paths = (Path(result["light"]), Path(result["dark"]))
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        self.source.write_text("date,contributions\n2025-10-26,-1\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            render_animated_calendars(
                self.source, self.output, source_scope="selected_public_repo_commits",
            )

        for path, (content, modified_ns) in before.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, modified_ns)

    def test_gif_has_finite_encoding_with_no_infinite_loop_extension(self) -> None:
        result = render_animated_calendars(
            self.source, self.output, source_scope="selected_public_repo_commits",
        )
        for variant in ("light", "dark"):
            data = Path(result[variant]).read_bytes()
            self.assertEqual(data[-1:], b";")
            self.assertNotIn(b"NETSCAPE2.0", data)
            self.assertNotIn(b"ANIMEXTS1.0", data)
            self.assertEqual(len(gif_frames(data)[1]), FRAME_COUNT)


if __name__ == "__main__":
    unittest.main()
