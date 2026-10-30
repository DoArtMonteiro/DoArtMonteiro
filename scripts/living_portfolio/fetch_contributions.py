#!/usr/bin/env python3
"""Fetch public default-branch commits from an exact repository allowlist."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


API_ROOT = "https://api.github.com"
APPROVED_LOGIN = "DoArtMonteiro"
USER_AGENT = "living-portfolio-calendar/1.0"
MAX_RESPONSE_BYTES = 5_000_000
MAX_REPOSITORIES = 16
MAX_PAGES = 100
PAGE_SIZE = 100
WINDOW_DAYS = 365
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ContributionDataError(RuntimeError):
    """Raised when public commit data is missing, invalid or out of scope."""


def _validated_repositories(repositories: Iterable[str]) -> list[str]:
    if isinstance(repositories, (str, bytes)):
        raise ContributionDataError("Repositories must be an explicit allowlist")
    try:
        entries = list(repositories)
    except TypeError as error:
        raise ContributionDataError("Repositories must be an explicit allowlist") from error
    if not 1 <= len(entries) <= MAX_REPOSITORIES:
        raise ContributionDataError("Repository allowlist must contain 1 to 16 entries")

    seen: set[str] = set()
    validated: list[str] = []
    for repository in entries:
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise ContributionDataError("Allowlist entries must use exact owner/repository names")
        owner, name = repository.split("/", 1)
        if (
            owner.casefold() != APPROVED_LOGIN.casefold()
            or not REPOSITORY_NAME.fullmatch(name)
            or name in {".", ".."}
        ):
            raise ContributionDataError("Allowlist contains a repository outside the reviewed profile")
        key = repository.casefold()
        if key in seen:
            raise ContributionDataError("Repository allowlist contains duplicates")
        seen.add(key)
        validated.append(f"{APPROVED_LOGIN}/{name}")
    return sorted(validated, key=str.casefold)


def _utc_day(value: Any) -> date:
    if not isinstance(value, str):
        raise ContributionDataError("GitHub returned an invalid commit author date")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContributionDataError("GitHub returned an invalid commit author date") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContributionDataError("GitHub returned a timezone-free commit author date")
    return parsed.astimezone(timezone.utc).date()


def _page_url(repository: str, login: str, page: int) -> str:
    owner, name = repository.split("/", 1)
    path = "/".join(
        urllib.parse.quote(part, safe="") for part in (owner, name)
    )
    query = urllib.parse.urlencode({
        "author": login,
        "per_page": PAGE_SIZE,
        "page": page,
    })
    return f"{API_ROOT}/repos/{path}/commits?{query}"


def _read_page(
    url: str,
    *,
    opener: Callable[..., Any],
) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with opener(request, timeout=20) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            parsed = urllib.parse.urlsplit(final_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "api.github.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
            ):
                raise ContributionDataError("GitHub redirected the public commit request off-host")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise ContributionDataError(
            f"Public commit request failed with HTTP {status}"
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ContributionDataError("Public commit request failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ContributionDataError("GitHub commit response exceeded the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContributionDataError("GitHub returned invalid commit JSON") from error
    if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
        raise ContributionDataError("GitHub returned an invalid commit page")
    if any(not isinstance(entry, dict) for entry in payload):
        raise ContributionDataError("GitHub returned an invalid commit record")
    return payload


def fetch_public_calendar(
    login: str,
    repositories: Iterable[str],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    today: date | None = None,
) -> list[tuple[date, int]]:
    """Count distinct linked commits by author date in public default branches.

    Requests are unauthenticated, so a repository that is private or no longer
    publicly readable cannot be inspected through this collector.
    """
    if not isinstance(login, str) or login.casefold() != APPROVED_LOGIN.casefold():
        raise ContributionDataError("Profile login is not in the reviewed allowlist")
    allowed = _validated_repositories(repositories)
    end = today or datetime.now(timezone.utc).date()
    if not isinstance(end, date) or isinstance(end, datetime):
        raise ContributionDataError("Calendar end date must be a UTC date")
    start = end - timedelta(days=WINDOW_DAYS - 1)
    counts: Counter[date] = Counter()
    seen_shas: set[str] = set()

    for repository in allowed:
        for page in range(1, MAX_PAGES + 1):
            payload = _read_page(
                _page_url(repository, APPROVED_LOGIN, page),
                opener=opener,
            )
            for entry in payload:
                author = entry.get("author")
                if author is None:
                    continue
                if not isinstance(author, dict):
                    raise ContributionDataError("GitHub returned an invalid commit author")
                author_login = author.get("login")
                if not isinstance(author_login, str) or author_login.casefold() != APPROVED_LOGIN.casefold():
                    continue

                sha = entry.get("sha")
                if not isinstance(sha, str) or not COMMIT_SHA.fullmatch(sha):
                    raise ContributionDataError("GitHub returned an invalid commit identifier")
                details = entry.get("commit")
                author_details = details.get("author") if isinstance(details, dict) else None
                if not isinstance(author_details, dict):
                    raise ContributionDataError("GitHub returned no commit author date")
                day = _utc_day(author_details.get("date"))
                if start <= day <= end and sha not in seen_shas:
                    counts[day] += 1
                    seen_shas.add(sha)

            if len(payload) < PAGE_SIZE:
                break
            if page == MAX_PAGES:
                raise ContributionDataError("GitHub commit pagination exceeded the safety limit")

    return [
        (start + timedelta(days=offset), counts[start + timedelta(days=offset)])
        for offset in range(WINDOW_DAYS)
    ]


def csv_bytes(rows: list[tuple[date, int]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "contributions"))
    writer.writerows((day.isoformat(), count) for day, count in rows)
    return output.getvalue().encode("utf-8")


def write_csv_atomic(path: str | Path, rows: list[tuple[date, int]]) -> bool:
    """Write only a changed, fully validated CSV; return whether bytes changed."""
    target = Path(path)
    payload = csv_bytes(rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if target.read_bytes() == payload:
            return False
    except FileNotFoundError:
        pass
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", default=APPROVED_LOGIN)
    parser.add_argument("--repository", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        rows = fetch_public_calendar(args.login, args.repository)
        changed = write_csv_atomic(args.output, rows)
    except (ContributionDataError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    total = sum(count for _, count in rows)
    print(
        f"Public default-branch commits: {total} distinct commits, "
        f"through {rows[-1][0].isoformat()}; changed={changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
