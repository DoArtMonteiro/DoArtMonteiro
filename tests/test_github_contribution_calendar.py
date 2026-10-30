from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.living_portfolio.github_contribution_calendar import (
    API_URL,
    APPROVED_LOGIN,
    ContributionCalendarError,
    fetch_public_calendar,
    refresh_public_calendar,
    write_csv_atomic,
)


START = date(2026, 10, 25)  # Sunday
END = START + timedelta(days=2)
TOKEN = "test-bearer-token"


def graphql_payload(counts: list[int]) -> dict[str, Any]:
    days = []
    for offset, count in enumerate(counts):
        day = START + timedelta(days=offset)
        days.append({
            "contributionCount": count,
            "date": day.isoformat(),
            "weekday": (day.weekday() + 1) % 7,
            "contributionLevel": "NONE" if count == 0 else "FIRST_QUARTILE",
        })
    return {
        "data": {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {
                        "totalContributions": sum(counts),
                        "weeks": [{"contributionDays": days}],
                    }
                }
            }
        }
    }


class FakeResponse:
    def __init__(self, body: bytes, url: str = API_URL) -> None:
        self.body = body
        self.url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, _limit: int) -> bytes:
        return self.body


class GitHubContributionCalendarTests(unittest.TestCase):
    def transport_for(self, payload: Any, captured: list[Any] | None = None):
        body = json.dumps(payload).encode("utf-8")

        def transport(request: Any, timeout: int) -> FakeResponse:
            if captured is not None:
                captured.extend((request, timeout))
            return FakeResponse(body)

        return transport

    def test_normalizes_real_shaped_graphql_calendar_and_limits_query_fields(self) -> None:
        captured: list[Any] = []
        rows = fetch_public_calendar(
            APPROVED_LOGIN, START, END, token=TOKEN,
            transport=self.transport_for(graphql_payload([0, 3, 7]), captured),
        )
        self.assertEqual(rows, [(START, 0), (START + timedelta(days=1), 3), (END, 7)])
        request, timeout = captured
        self.assertEqual(request.full_url, API_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 20)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        request_body = json.loads(request.data)
        self.assertEqual(request_body["variables"], {
            "login": APPROVED_LOGIN,
            "from": "2026-10-25T00:00:00Z",
            "to": "2026-10-27T23:59:59Z",
        })
        query = request_body["query"]
        for field in ("contributionCount", "date", "weekday", "contributionLevel"):
            self.assertIn(field, query)
        for field in ("restrictedContributionsCount", "hasAnyRestrictedContributions", "privateContributions"):
            self.assertNotIn(field, query)

    def test_changed_source_updates_output_and_unchanged_data_is_byte_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "contributions.csv"
            self.assertTrue(refresh_public_calendar(
                APPROVED_LOGIN, START, END, token=TOKEN, output_path=output,
                transport=self.transport_for(graphql_payload([0, 3, 7])),
            ))
            initial = output.read_bytes()
            self.assertFalse(refresh_public_calendar(
                APPROVED_LOGIN, START, END, token=TOKEN, output_path=output,
                transport=self.transport_for(graphql_payload([0, 3, 7])),
            ))
            self.assertEqual(output.read_bytes(), initial)
            self.assertTrue(refresh_public_calendar(
                APPROVED_LOGIN, START, END, token=TOKEN, output_path=output,
                transport=self.transport_for(graphql_payload([0, 4, 7])),
            ))
            self.assertNotEqual(output.read_bytes(), initial)
            self.assertIn(b"2026-10-26,4\n", output.read_bytes())

    def test_failed_fetch_or_validation_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "contributions.csv"
            write_csv_atomic(output, [(START + timedelta(days=i), i) for i in range(3)])
            previous = output.read_bytes()

            def failing_transport(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("transport leaked test-bearer-token and private response")

            with self.assertRaises(ContributionCalendarError):
                refresh_public_calendar(
                    APPROVED_LOGIN, START, END, token=TOKEN, output_path=output,
                    transport=failing_transport,
                )
            with self.assertRaises(ContributionCalendarError):
                refresh_public_calendar(
                    APPROVED_LOGIN, START, END, token=TOKEN, output_path=output,
                    transport=self.transport_for({"data": {"errors": ["invalid"]}}),
                )
            self.assertEqual(output.read_bytes(), previous)

    def test_rejects_foreign_login_before_request(self) -> None:
        def must_not_run(*_args: Any, **_kwargs: Any) -> None:
            self.fail("unapproved login reached the network")

        with self.assertRaises(ContributionCalendarError):
            fetch_public_calendar("another-user", START, END, token=TOKEN, transport=must_not_run)

    def test_drops_only_week_padding_outside_the_explicit_inclusive_range(self) -> None:
        payload = graphql_payload([0, 3, 7])
        days = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"]
        for offset in range(1, 5):
            day = END + timedelta(days=offset)
            days.append({
                "contributionCount": 9,
                "date": day.isoformat(),
                "weekday": (day.weekday() + 1) % 7,
                "contributionLevel": "FIRST_QUARTILE",
            })

        rows = fetch_public_calendar(
            APPROVED_LOGIN, START, END, token=TOKEN,
            transport=self.transport_for(payload),
        )

        self.assertEqual(rows, [(START, 0), (START + timedelta(days=1), 3), (END, 7)])

        out_of_week = END + timedelta(days=5)
        days.append({
            "contributionCount": 1,
            "date": out_of_week.isoformat(),
            "weekday": (out_of_week.weekday() + 1) % 7,
            "contributionLevel": "FIRST_QUARTILE",
        })
        with self.assertRaises(ContributionCalendarError):
            fetch_public_calendar(
                APPROVED_LOGIN, START, END, token=TOKEN,
                transport=self.transport_for(payload),
            )

    def test_error_messages_do_not_include_token_or_response_body(self) -> None:
        sensitive_body = "private-body-marker-42"

        def broken_transport(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"{TOKEN} {sensitive_body}")

        with self.assertRaises(ContributionCalendarError) as caught:
            fetch_public_calendar(APPROVED_LOGIN, START, END, token=TOKEN, transport=broken_transport)
        message = str(caught.exception)
        self.assertNotIn(TOKEN, message)
        self.assertNotIn(sensitive_body, message)

        invalid = self.transport_for({"secret": sensitive_body})
        with self.assertRaises(ContributionCalendarError) as bad_shape:
            fetch_public_calendar(APPROVED_LOGIN, START, END, token=TOKEN, transport=invalid)
        self.assertNotIn(TOKEN, str(bad_shape.exception))
        self.assertNotIn(sensitive_body, str(bad_shape.exception))


if __name__ == "__main__":
    unittest.main()
