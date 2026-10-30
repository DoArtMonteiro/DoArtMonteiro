from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.living_portfolio.fetch_contributions import (
    APPROVED_LOGIN,
    ContributionDataError,
    fetch_public_calendar,
    write_csv_atomic,
)


TODAY = date(2026, 10, 30)
REPOSITORY = "DoArtMonteiro/omniquiz"


def commit_record(
    day: date,
    *,
    sha_number: int,
    author_login: str | None = APPROVED_LOGIN,
    committer_day: date | None = None,
) -> dict[str, Any]:
    return {
        "sha": f"{sha_number:040x}",
        "author": {"login": author_login} if author_login else None,
        "commit": {
            "author": {"date": f"{day.isoformat()}T12:00:00Z"},
            "committer": {
                "date": f"{(committer_day or day).isoformat()}T12:00:00Z",
            },
        },
    }


class FakeResponse:
    def __init__(self, body: bytes, final_url: str | None = None) -> None:
        self.body = body
        self.final_url = final_url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str | None:
        return self.final_url

    def read(self, _limit: int) -> bytes:
        return self.body


class FetchContributionsTests(unittest.TestCase):
    def opener_for(self, payload: list[dict[str, Any]], captured: list[Any] | None = None):
        def opener(request: Any, timeout: int):
            if captured is not None:
                captured.extend((request, timeout))
            return FakeResponse(json.dumps(payload).encode("utf-8"), request.full_url)
        return opener

    def test_fetches_only_linked_commits_from_public_default_branch_api(self) -> None:
        start = TODAY - timedelta(days=364)
        payload = [
            commit_record(start, sha_number=1, committer_day=start - timedelta(days=20)),
            commit_record(TODAY, sha_number=2, committer_day=TODAY + timedelta(days=20)),
            commit_record(start - timedelta(days=1), sha_number=3),
            commit_record(TODAY, sha_number=4, author_login="another-user"),
            commit_record(TODAY, sha_number=5, author_login=None),
        ]
        captured: list[Any] = []
        rows = fetch_public_calendar(
            "doartmonteiro", [REPOSITORY],
            opener=self.opener_for(payload, captured),
            today=TODAY,
        )

        self.assertEqual(len(rows), 365)
        self.assertEqual(rows[0], (start, 1))
        self.assertEqual(rows[-1], (TODAY, 1))
        self.assertEqual(sum(count for _, count in rows), 2)
        request, timeout = captured
        self.assertEqual(timeout, 20)
        self.assertFalse(request.has_header("Authorization"))
        parsed = urllib.parse.urlsplit(request.full_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "api.github.com")
        self.assertEqual(parsed.path, "/repos/DoArtMonteiro/omniquiz/commits")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["author"], [APPROVED_LOGIN])
        self.assertNotIn("since", query)
        self.assertNotIn("until", query)
        self.assertNotIn("sha", query)

    def test_rejects_unknown_profile_and_repository_before_network_call(self) -> None:
        def must_not_run(*_args: Any, **_kwargs: Any) -> None:
            self.fail("unapproved input reached the network")

        with self.assertRaises(ContributionDataError):
            fetch_public_calendar("other-user", [REPOSITORY], opener=must_not_run, today=TODAY)
        with self.assertRaises(ContributionDataError):
            fetch_public_calendar(APPROVED_LOGIN, ["OtherOwner/repo"], opener=must_not_run, today=TODAY)

    def test_rejects_duplicate_repository_entries(self) -> None:
        def must_not_run(*_args: Any, **_kwargs: Any) -> None:
            self.fail("duplicate allowlist reached the network")

        with self.assertRaisesRegex(ContributionDataError, "duplicates"):
            fetch_public_calendar(
                APPROVED_LOGIN,
                [REPOSITORY, "doartmonteiro/OMNIQUIZ"],
                opener=must_not_run,
                today=TODAY,
            )

    def test_paginates_and_counts_duplicate_commit_objects_once(self) -> None:
        first_page = [commit_record(TODAY, sha_number=index) for index in range(1, 101)]
        duplicate = commit_record(TODAY, sha_number=1)
        calls: list[int] = []

        def opener(request: Any, timeout: int):
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["page"][0])
            calls.append(page)
            payload = first_page if page == 1 else [duplicate]
            return FakeResponse(json.dumps(payload).encode("utf-8"), request.full_url)

        rows = fetch_public_calendar(APPROVED_LOGIN, [REPOSITORY], opener=opener, today=TODAY)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(sum(count for _, count in rows), 100)

    def test_deduplicates_same_commit_seen_in_two_allowlisted_repositories(self) -> None:
        same_commit = commit_record(TODAY, sha_number=88)

        def opener(request: Any, timeout: int):
            return FakeResponse(json.dumps([same_commit]).encode("utf-8"), request.full_url)

        rows = fetch_public_calendar(
            APPROVED_LOGIN,
            [REPOSITORY, "DoArtMonteiro/murdoku"],
            opener=opener,
            today=TODAY,
        )
        self.assertEqual(sum(count for _, count in rows), 1)

    def test_unavailable_public_repository_fails_closed(self) -> None:
        def missing_public_repo(request: Any, timeout: int):
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

        with self.assertRaisesRegex(ContributionDataError, "HTTP 404"):
            fetch_public_calendar(
                APPROVED_LOGIN, [REPOSITORY], opener=missing_public_repo, today=TODAY
            )

    def test_rejects_cross_host_redirect(self) -> None:
        def redirected(request: Any, timeout: int):
            return FakeResponse(b"[]", "https://example.invalid/commits")

        with self.assertRaisesRegex(ContributionDataError, "off-host"):
            fetch_public_calendar(APPROVED_LOGIN, [REPOSITORY], opener=redirected, today=TODAY)

    def test_changed_public_source_updates_csv_and_unchanged_source_is_idempotent(self) -> None:
        first = fetch_public_calendar(
            APPROVED_LOGIN,
            [REPOSITORY],
            opener=self.opener_for([commit_record(TODAY, sha_number=1)]),
            today=TODAY,
        )
        changed_day = TODAY - timedelta(days=1)
        changed = fetch_public_calendar(
            APPROVED_LOGIN,
            [REPOSITORY],
            opener=self.opener_for([commit_record(changed_day, sha_number=2)]),
            today=TODAY,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "contributions.csv"
            self.assertTrue(write_csv_atomic(output, first))
            first_bytes = output.read_bytes()
            self.assertFalse(write_csv_atomic(output, first))
            self.assertEqual(output.read_bytes(), first_bytes)
            self.assertTrue(write_csv_atomic(output, changed))
            self.assertNotEqual(output.read_bytes(), first_bytes)

    def test_failed_fetch_preserves_last_valid_csv(self) -> None:
        valid = fetch_public_calendar(
            APPROVED_LOGIN,
            [REPOSITORY],
            opener=self.opener_for([commit_record(TODAY, sha_number=1)]),
            today=TODAY,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "contributions.csv"
            write_csv_atomic(output, valid)
            previous = output.read_bytes()

            def failed_fetch(*_args: Any, **_kwargs: Any) -> None:
                raise urllib.error.URLError("offline")

            with self.assertRaises(ContributionDataError):
                fetch_public_calendar(
                    APPROVED_LOGIN, [REPOSITORY], opener=failed_fetch, today=TODAY
                )
            self.assertEqual(output.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
