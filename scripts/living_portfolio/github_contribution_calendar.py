#!/usr/bin/env python3
"""Fetch and normalize the reviewed user's public GitHub contribution calendar.

Callers must supply a GitHub Actions ``GITHUB_TOKEN`` without ``read:user``.
Never use a personal access token with that scope: this client requests only
public contribution-calendar fields and must not be given access to private data.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


API_URL = "https://api.github.com/graphql"
APPROVED_LOGIN = "DoArtMonteiro"
USER_AGENT = "living-portfolio-calendar/1.0"
MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 20
_LEVELS = {"NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"}


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


_default_transport = urllib.request.build_opener(_RejectRedirects()).open

_QUERY = """query PublicContributionCalendar($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays {
            contributionCount
            date
            weekday
            contributionLevel
          }
        }
      }
    }
  }
}"""


class ContributionCalendarError(RuntimeError):
    """Raised for rejected input, transport failures, or invalid API data."""


Transport = Callable[..., Any]


def _utc_bounds(from_date: date, to_date: date) -> tuple[str, str]:
    if (not isinstance(from_date, date) or isinstance(from_date, datetime)
            or not isinstance(to_date, date) or isinstance(to_date, datetime)):
        raise ContributionCalendarError("Calendar bounds must be explicit UTC dates")
    if from_date > to_date:
        raise ContributionCalendarError("Calendar start date must not follow its end date")
    start = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc) - timedelta(seconds=1)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _valid_login(login: str) -> None:
    if not isinstance(login, str) or login != APPROVED_LOGIN:
        raise ContributionCalendarError("Profile login is not in the reviewed allowlist")


def normalize_calendar(payload: Any, from_date: date, to_date: date) -> list[tuple[date, int]]:
    """Validate GitHub's response and return ordered, inclusive daily counts."""
    _utc_bounds(from_date, to_date)
    try:
        calendar = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        weeks = calendar["weeks"]
    except (TypeError, KeyError):
        raise ContributionCalendarError("GitHub returned an incomplete contribution calendar") from None
    if not isinstance(weeks, list):
        raise ContributionCalendarError("GitHub returned invalid contribution weeks")

    by_day: dict[date, int] = {}
    leading_padding = (from_date.weekday() + 1) % 7
    trailing_padding = 6 - ((to_date.weekday() + 1) % 7)
    for week in weeks:
        days = week.get("contributionDays") if isinstance(week, dict) else None
        if not isinstance(days, list):
            raise ContributionCalendarError("GitHub returned an invalid contribution week")
        for entry in days:
            if not isinstance(entry, dict):
                raise ContributionCalendarError("GitHub returned an invalid contribution day")
            raw_day = entry.get("date")
            if not isinstance(raw_day, str):
                raise ContributionCalendarError("GitHub returned an invalid contribution date")
            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                raise ContributionCalendarError("GitHub returned an invalid contribution date") from None
            if day.isoformat() != raw_day:
                raise ContributionCalendarError("GitHub returned an invalid contribution date")
            if day < from_date and (from_date - day).days > leading_padding:
                raise ContributionCalendarError("GitHub returned a date outside the requested calendar weeks")
            if day > to_date and (day - to_date).days > trailing_padding:
                raise ContributionCalendarError("GitHub returned a date outside the requested calendar weeks")
            if day in by_day:
                raise ContributionCalendarError("GitHub returned a duplicate contribution date")
            count = entry.get("contributionCount")
            weekday = entry.get("weekday")
            level = entry.get("contributionLevel")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ContributionCalendarError("GitHub returned an invalid daily contribution count")
            # GitHub numbers weekdays Sunday=0 through Saturday=6.
            if not isinstance(weekday, int) or isinstance(weekday, bool) or weekday != (day.weekday() + 1) % 7:
                raise ContributionCalendarError("GitHub returned an invalid contribution weekday")
            if not isinstance(level, str) or level not in _LEVELS:
                raise ContributionCalendarError("GitHub returned an invalid contribution level")
            by_day[day] = count

    expected_count = (to_date - from_date).days + 1
    if sum(from_date <= day <= to_date for day in by_day) != expected_count:
        raise ContributionCalendarError("GitHub contribution calendar is missing requested dates")
    rows = [(from_date + timedelta(days=offset), by_day[from_date + timedelta(days=offset)])
            for offset in range(expected_count)]
    return rows


class _ResponseContext:
    """Adapt response objects to a context manager for injected transports."""

    def __init__(self, response: Any) -> None:
        self.response = response

    def __enter__(self) -> Any:
        return self.response.__enter__() if hasattr(self.response, "__enter__") else self.response

    def __exit__(self, *args: Any) -> Any:
        if hasattr(self.response, "__exit__"):
            return self.response.__exit__(*args)
        close = getattr(self.response, "close", None)
        if close:
            close()
        return None


def fetch_public_calendar(
    login: str,
    from_date: date,
    to_date: date,
    *,
    token: str,
    transport: Transport = _default_transport,
) -> list[tuple[date, int]]:
    """Make one bounded GraphQL POST and return validated daily public counts."""
    _valid_login(login)
    start, end = _utc_bounds(from_date, to_date)
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise ContributionCalendarError("A valid injected bearer token is required")
    body = json.dumps({
        "query": _QUERY,
        "variables": {"login": APPROVED_LOGIN, "from": start, "to": end},
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(API_URL, data=body, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }, method="POST")
    try:
        with _ResponseContext(transport(request, timeout=REQUEST_TIMEOUT_SECONDS)) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else API_URL
            parsed = urllib.parse.urlsplit(final_url)
            if (parsed.scheme != "https" or parsed.hostname != "api.github.com"
                    or parsed.path != "/graphql" or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                raise ContributionCalendarError("GitHub redirected the request off-host")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except ContributionCalendarError:
        raise
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise ContributionCalendarError(f"GitHub contribution request failed with HTTP {status}") from None
    except Exception:
        # Transport exception text may contain request headers or response data.
        raise ContributionCalendarError("GitHub contribution request failed") from None
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ContributionCalendarError("GitHub contribution response exceeded the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContributionCalendarError("GitHub returned invalid contribution JSON") from None
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ContributionCalendarError("GitHub could not provide the public contribution calendar")
    return normalize_calendar(payload, from_date, to_date)


def csv_bytes(rows: Iterable[tuple[date, int]]) -> bytes:
    """Serialize validated rows in a stable CSV form."""
    values = list(rows)
    if not values:
        raise ContributionCalendarError("Calendar must contain at least one day")
    previous: date | None = None
    for day, count in values:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ContributionCalendarError("Calendar contains an invalid date")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ContributionCalendarError("Calendar contains an invalid daily count")
        if previous is not None and day != previous + timedelta(days=1):
            raise ContributionCalendarError("Calendar dates must be ordered and consecutive")
        previous = day
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "contributions"))
    writer.writerows((day.isoformat(), count) for day, count in values)
    return output.getvalue().encode("utf-8")


def write_csv_atomic(path: str | Path, rows: Iterable[tuple[date, int]]) -> bool:
    """Atomically publish changed normalized data; return whether bytes changed."""
    target = Path(path)
    payload = csv_bytes(rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if target.read_bytes() == payload:
            return False
    except FileNotFoundError:
        pass
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=f".{target.name}.",
                                        suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return True


def refresh_public_calendar(
    login: str,
    from_date: date,
    to_date: date,
    *,
    token: str,
    output_path: str | Path,
    transport: Transport = _default_transport,
) -> bool:
    """Fetch and validate fully before atomically replacing the prior valid CSV."""
    rows = fetch_public_calendar(login, from_date, to_date, token=token, transport=transport)
    return write_csv_atomic(output_path, rows)
