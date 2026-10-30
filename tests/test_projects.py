from __future__ import annotations

import copy
from email.message import Message
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts.living_portfolio.projects import (
    GitHubApiError,
    _fetch_json,
    _get,
    collect_projects,
    refresh_projects,
)


OWNER = "DoArtMonteiro"
HOSTILE_TITLE = '# [release](javascript:alert(1)) <script>alert("x")</script> &'


def repository_fixtures() -> dict[str, object]:
    fixtures: dict[str, object] = {}
    for repo_name, branch, sha, committed_at in [
        ("zeta", "main", "b" * 40, "2026-10-20T10:00:00Z"),
        ("alpha", "trunk", "a" * 40, "2026-10-22T10:00:00+02:00"),
    ]:
        base = f"https://api.github.com/repos/{OWNER}/{repo_name}"
        html = f"https://github.com/{OWNER}/{repo_name}"
        fixtures[base] = {
            "full_name": f"{OWNER}/{repo_name}",
            "html_url": html,
            "default_branch": branch,
            "private": False,
            "visibility": "public",
        }
        fixtures[f"{base}/commits?sha={branch}&per_page=1"] = [{
            "sha": sha,
            "html_url": f"{html}/commit/{sha}",
            "commit": {"committer": {"date": committed_at}},
        }]
        if repo_name == "alpha":
            fixtures[f"{base}/releases/latest"] = {
                "tag_name": "v1.0.0",
                "name": HOSTILE_TITLE,
                "html_url": f"{html}/releases/tag/v1.0.0",
                "published_at": "2026-10-21T00:00:00Z",
            }
        else:
            fixtures[f"{base}/releases/latest"] = None

        milestone = {
            "state": "closed",
            "number": 4,
            "title": HOSTILE_TITLE,
            "html_url": f"{html}/milestone/4",
            "closed_at": "2026-10-19T12:00:00Z",
        }
        fixtures[f"{base}/milestones?state=closed&sort=updated&direction=desc&per_page=100&page=1"] = [
            copy.deepcopy(milestone), copy.deepcopy(milestone),
        ]
        fixtures[f"{base}/pulls?state=all&sort=updated&direction=desc&per_page=100&page=1"] = [
            {
                "number": 7, "title": "Work in progress", "state": "open", "draft": True,
                "html_url": f"{html}/pull/7", "created_at": "2026-10-18T10:00:00Z",
                "updated_at": "2026-10-19T10:00:00Z", "closed_at": None, "merged_at": None,
            },
            {
                "number": 8, "title": "Awaiting review", "state": "open", "draft": False,
                "html_url": f"{html}/pull/8", "created_at": "2026-10-20T10:00:00Z",
                "updated_at": "2026-10-20T10:00:00Z", "closed_at": None, "merged_at": None,
            },
            {
                "number": 9, "title": "Merged change", "state": "closed", "draft": False,
                "html_url": f"{html}/pull/9", "created_at": "2026-10-15T10:00:00Z",
                "updated_at": "2026-10-17T10:00:00Z", "closed_at": "2026-10-17T10:00:00Z",
                "merged_at": "2026-10-17T10:00:00Z",
            },
            {
                "number": 10, "title": "Closed without merge", "state": "closed", "draft": False,
                "html_url": f"{html}/pull/10", "created_at": "2026-10-14T10:00:00Z",
                "updated_at": "2026-10-16T10:00:00Z", "closed_at": "2026-10-16T10:00:00Z",
                "merged_at": None,
            },
            {
                "number": 11, "title": "Closed while still a draft", "state": "closed", "draft": True,
                "html_url": f"{html}/pull/11", "created_at": "2026-10-13T10:00:00Z",
                "updated_at": "2026-10-15T10:00:00Z", "closed_at": "2026-10-15T10:00:00Z",
                "merged_at": None,
            },
        ]
        fixtures[f"{base}/issues?state=open&sort=updated&direction=desc&per_page=100&page=1"] = (
            [
                {
                    "number": 3, "title": "Open issue", "state": "open",
                    "html_url": f"{html}/issues/3", "created_at": "2026-10-21T10:00:00Z",
                    "updated_at": "2026-10-22T10:00:00Z", "closed_at": None,
                },
                {
                    "number": 7, "title": "An open pull request", "state": "open",
                    "pull_request": {"url": f"https://api.github.com/repos/{OWNER}/{repo_name}/pulls/7"},
                },
            ] if repo_name == "alpha" else []
        )
        fixtures[f"{base}/actions/runs?branch={branch}&per_page=1"] = (
            {
                "total_count": 1,
                "workflow_runs": [{
                    "name": "CI", "head_branch": branch, "head_sha": "d" * 40,
                    "status": "completed", "conclusion": "success",
                    "html_url": f"{html}/actions/runs/42", "created_at": "2026-10-23T10:00:00Z",
                    "event": "push",
                }],
            } if repo_name == "alpha" else {"total_count": 0, "workflow_runs": []}
        )
    return fixtures


class FixtureFetcher:
    def __init__(self, fixtures: dict[str, object], fail_path: str | None = None):
        self.fixtures = fixtures
        self.fail_path = fail_path
        self.calls: list[str] = []

    def __call__(self, url: str) -> object:
        self.calls.append(url)
        parsed = urlsplit(url)
        if self.fail_path and parsed.path.endswith(self.fail_path):
            raise GitHubApiError("fixture API failure")
        if parsed.path.endswith("/milestones") or parsed.path.endswith("/pulls") or parsed.path.endswith("/issues"):
            query = parse_qs(parsed.query)
            key = (
                f"https://api.github.com{parsed.path}?"
                + "&".join(f"{name}={values[0]}" for name, values in query.items())
            )
        else:
            key = url
        if key not in self.fixtures:
            raise AssertionError(f"Unexpected API request in fixture test: {url}")
        value = self.fixtures[key]
        if value is None and parsed.path.endswith("/releases/latest"):
            import urllib.error

            raise urllib.error.HTTPError(url, 404, "no release", None, None)
        return copy.deepcopy(value)


class ProjectCollectorTests(unittest.TestCase):
    def test_rest_reads_use_run_token_except_public_actions_endpoint(self) -> None:
        with patch.dict(os.environ, {"GH_TOKEN": "fixture-run-token"}), \
                patch("scripts.living_portfolio.projects.urllib.request.urlopen",
                      side_effect=[io.BytesIO(b"{}"), io.BytesIO(b"{}")]) as urlopen:
            _fetch_json("https://api.github.com/repos/DoArtMonteiro/demo")
            repository_request = urlopen.call_args_list[0].args[0]
            self.assertEqual(repository_request.get_header("Authorization"), "Bearer fixture-run-token")
            self.assertEqual(
                repository_request.unredirected_hdrs.get("Authorization"),
                "Bearer fixture-run-token",
            )

            _fetch_json("https://api.github.com/repos/DoArtMonteiro/demo/actions/runs?branch=main")
            workflow_request = urlopen.call_args_list[1].args[0]
            self.assertIsNone(workflow_request.get_header("Authorization"))

    def test_rest_fetcher_never_sends_run_token_to_another_host(self) -> None:
        with patch.dict(os.environ, {"GH_TOKEN": "fixture-run-token"}), \
                patch("scripts.living_portfolio.projects.urllib.request.urlopen") as urlopen:
            with self.assertRaises(GitHubApiError):
                _fetch_json("https://example.com/repos/DoArtMonteiro/demo")
            urlopen.assert_not_called()

    def test_rate_limit_failure_reports_wait_guidance_without_response_body(self) -> None:
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "1793500000"
        error = urllib.error.HTTPError(
            "https://api.github.com/repos/DoArtMonteiro/demo", 403,
            "rate limited", headers, None,
        )

        with self.assertRaises(GitHubApiError) as raised:
            _get(lambda _url: (_ for _ in ()).throw(error),
                 "https://api.github.com/repos/DoArtMonteiro/demo")

        self.assertIn("rate-limit-reset-utc=1793500000", str(raised.exception))
        self.assertNotIn("rate limited", str(raised.exception))

    def test_repository_and_timeline_order_are_deterministic(self) -> None:
        first = collect_projects(["DoArtMonteiro/zeta", "DoArtMonteiro/alpha"], FixtureFetcher(repository_fixtures()))
        second = collect_projects(["DoArtMonteiro/alpha", "DoArtMonteiro/zeta"], FixtureFetcher(repository_fixtures()))

        self.assertEqual(first, second)
        self.assertEqual([item["repository"] for item in first["projects"]], [
            "DoArtMonteiro/alpha", "DoArtMonteiro/zeta",
        ])
        dates = [item["occurred_at"] for item in first["timeline"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(first["projects"][0]["latest_default_branch_commit"]["committed_at"], "2026-10-22T08:00:00Z")

    def test_hostile_titles_are_escaped_for_markdown_and_xml(self) -> None:
        result = collect_projects(["DoArtMonteiro/alpha"], FixtureFetcher(repository_fixtures()))
        release = result["projects"][0]["latest_release"]

        self.assertEqual(release["title"], HOSTILE_TITLE)
        self.assertIn(r"\# \[release\]", release["title_markdown"])
        self.assertIn(r"\<script\>", release["title_markdown"])
        self.assertIn("&lt;script&gt;", release["title_xml"])
        self.assertIn("&amp;", release["title_xml"])

    def test_pull_request_statuses_keep_open_draft_distinct_from_merged(self) -> None:
        result = collect_projects(["DoArtMonteiro/alpha"], FixtureFetcher(repository_fixtures()))
        pulls = {item["number"]: item["status"] for item in result["projects"][0]["pull_requests"]}

        self.assertEqual(pulls, {7: "draft", 8: "open", 9: "merged", 10: "closed", 11: "closed"})
        timeline_pulls = [item for item in result["timeline"] if item["kind"] == "pull_request"]
        self.assertEqual({item["number"]: item["status"] for item in timeline_pulls}, pulls)

    def test_open_issues_exclude_pull_requests_and_workflow_state_is_collected(self) -> None:
        result = collect_projects(["DoArtMonteiro/alpha"], FixtureFetcher(repository_fixtures()))
        project = result["projects"][0]

        self.assertEqual(project["open_issue_count"], 1)
        self.assertEqual([item["number"] for item in project["recent_open_issues"]], [3])
        self.assertEqual(project["latest_default_branch_workflow_run"]["conclusion"], "success")
        self.assertEqual(project["latest_default_branch_workflow_run"]["head_sha"], "d" * 40)

    def test_workflow_count_cannot_be_lower_than_returned_runs(self) -> None:
        fixtures = repository_fixtures()
        workflow_url = f"https://api.github.com/repos/{OWNER}/alpha/actions/runs?branch=trunk&per_page=1"
        fixtures[workflow_url]["total_count"] = 0

        with self.assertRaises(GitHubApiError):
            collect_projects(["DoArtMonteiro/alpha"], FixtureFetcher(fixtures))

    def test_private_repository_is_rejected_before_collecting_details(self) -> None:
        fixtures = repository_fixtures()
        fixtures[f"https://api.github.com/repos/{OWNER}/alpha"]["private"] = True
        fetcher = FixtureFetcher(fixtures)

        with self.assertRaises(GitHubApiError):
            collect_projects(["DoArtMonteiro/alpha"], fetcher)

        self.assertEqual(len(fetcher.calls), 1)

    def test_duplicate_closed_milestones_are_emitted_once(self) -> None:
        result = collect_projects(["DoArtMonteiro/alpha"], FixtureFetcher(repository_fixtures()))

        self.assertEqual(len(result["projects"][0]["closed_milestones"]), 1)
        self.assertEqual(
            sum(item["kind"] == "milestone_closed" for item in result["timeline"]),
            1,
        )

    def test_api_failure_preserves_previous_snapshot_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "projects.json"
            original = b'{"previous":"snapshot"}\n'
            target.write_bytes(original)
            fetcher = FixtureFetcher(repository_fixtures(), fail_path="/milestones")

            with self.assertRaises(GitHubApiError):
                refresh_projects(target, ["DoArtMonteiro/alpha"], fetcher)

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
