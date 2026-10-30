"""Collect objective, public GitHub project facts for the living portfolio.

The collector is deliberately read-only. It queries only the exact repositories
passed by the caller, writes no Git state, and never derives project maturity,
acceptance, personal ownership, or completion from repository activity.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.sax.saxutils import escape as xml_escape


API_ROOT = "https://api.github.com"
GITHUB_HOST = "github.com"
USER_AGENT = "living-portfolio-projects/1.0"
PAGE_SIZE = 100
MAX_PAGES = 100
MAX_RESPONSE_BYTES = 5_000_000

JsonFetcher = Callable[[str], Any]


class GitHubApiError(RuntimeError):
    """Raised when GitHub data is unavailable or fails validation."""


def _valid_repository_slug(slug: str) -> tuple[str, str]:
    if not isinstance(slug, str) or slug.count("/") != 1:
        raise ValueError("Repository must be specified as owner/name")
    owner, name = slug.split("/", 1)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        raise ValueError(f"Invalid GitHub owner in repository: {slug!r}")
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)
            or name in {".", ".."}):
        raise ValueError(f"Invalid GitHub repository name: {slug!r}")
    return owner, name


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GitHubApiError(f"GitHub response has an invalid {field}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GitHubApiError(f"GitHub response has control characters in {field}")
    return value.strip()


def _required_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GitHubApiError(f"GitHub response has an invalid {field}")
    return value


def _timestamp(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    raw = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise GitHubApiError(f"GitHub response has an invalid {field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GitHubApiError(f"GitHub response has a timezone-free {field}")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def escape_markdown(value: str) -> str:
    """Escape untrusted text for a Markdown text node."""
    return re.sub(r"([\\`*_{}\[\]<>()#+.!|~-])", r"\\\1", value)


def escape_xml(value: str) -> str:
    """Escape untrusted text for XML text or attribute content."""
    return xml_escape(value, {"\"": "&quot;", "'": "&apos;"})


def _validated_github_url(value: Any, expected_prefix: str, field: str) -> str:
    url = _required_text(value, field)
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise GitHubApiError(f"GitHub response has an invalid {field}") from error
    expected_path = urllib.parse.urlsplit(expected_prefix).path
    valid_path = (parsed.path.casefold() == expected_path.casefold()
                  or parsed.path.casefold().startswith(expected_path.casefold().rstrip("/") + "/"))
    if (parsed.scheme != "https" or parsed.hostname != GITHUB_HOST
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.query or parsed.fragment
            or not valid_path):
        raise GitHubApiError(f"GitHub response has an unexpected {field}")
    return url


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubApiError(f"GitHub response has an invalid {field}")
    return value


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise GitHubApiError(f"GitHub response has an invalid {field}")
    return value


def _fetch_json(url: str) -> Any:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise GitHubApiError("GitHub API request URL is invalid") from error
    if (parsed.scheme != "https" or parsed.hostname != "api.github.com"
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443)):
        raise GitHubApiError("GitHub API requests must use HTTPS on api.github.com")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN", "")
    if (token.strip() != token
            or any(ord(character) < 32 or ord(character) == 127 for character in token)):
        raise GitHubApiError("GH_TOKEN contains invalid characters")
    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )
    if token and not parsed.path.endswith("/actions/runs"):
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError) as error:
        raise GitHubApiError(f"GitHub API request failed: {url}") from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise GitHubApiError("GitHub API response exceeded the size limit")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GitHubApiError("GitHub API returned invalid JSON") from error


def _endpoint(slug: str, suffix: str) -> str:
    owner, name = _valid_repository_slug(slug)
    path = "/".join(urllib.parse.quote(part, safe="") for part in (owner, name))
    endpoint = f"{API_ROOT}/repos/{path}"
    return f"{endpoint}/{suffix.lstrip('/')}" if suffix else endpoint


def _get(fetch_json: JsonFetcher, url: str, *, not_found_ok: bool = False) -> Any:
    try:
        result = fetch_json(url)
    except urllib.error.HTTPError as error:
        if not_found_ok and error.code == 404:
            error.close()
            return None
        retry_after = error.headers.get("Retry-After") if error.headers else None
        remaining = error.headers.get("X-RateLimit-Remaining") if error.headers else None
        reset = error.headers.get("X-RateLimit-Reset") if error.headers else None
        rate_limit_detail = ""
        if error.code in {403, 429}:
            details = []
            if retry_after and retry_after.isdigit():
                details.append(f"retry-after={retry_after}s")
            if remaining == "0" and reset and reset.isdigit():
                details.append(f"rate-limit-reset-utc={reset}")
            if details:
                rate_limit_detail = "; rate limit guidance: " + ", ".join(details)
        error.close()
        raise GitHubApiError(
            f"GitHub API request failed with HTTP {error.code}{rate_limit_detail}"
        ) from error
    except GitHubApiError:
        raise
    except Exception as error:
        raise GitHubApiError(f"GitHub API request failed: {url}") from error
    return result


def _paged(fetch_json: JsonFetcher, base_url: str, field: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        separator = "&" if "?" in base_url else "?"
        url = f"{base_url}{separator}per_page={PAGE_SIZE}&page={page}"
        payload = _array(_get(fetch_json, url), field)
        page_items = [_object(item, field) for item in payload]
        results.extend(page_items)
        if len(page_items) < PAGE_SIZE:
            return results
    raise GitHubApiError(f"GitHub pagination exceeded the safety limit for {field}")


def _release(fetch_json: JsonFetcher, slug: str, repo_url: str) -> dict[str, Any] | None:
    payload = _get(fetch_json, _endpoint(slug, "releases/latest"), not_found_ok=True)
    if payload is None:
        return None
    release = _object(payload, "latest release")
    title = _required_text(release.get("name") or release.get("tag_name"), "release title")
    return {
        "tag": _required_text(release.get("tag_name"), "release tag"),
        "title": title,
        "title_markdown": escape_markdown(title),
        "title_xml": escape_xml(title),
        "url": _validated_github_url(release.get("html_url"), repo_url, "release URL"),
        "published_at": _timestamp(release.get("published_at"), "release published_at"),
    }


def _closed_milestones(fetch_json: JsonFetcher, slug: str, repo_url: str) -> list[dict[str, Any]]:
    base = _endpoint(slug, "milestones") + "?state=closed&sort=updated&direction=desc"
    milestones = _paged(fetch_json, base, "closed milestones")
    events_by_number: dict[int, dict[str, Any]] = {}
    for milestone in milestones:
        if milestone.get("state") != "closed":
            raise GitHubApiError("GitHub returned a non-closed milestone")
        title = _required_text(milestone.get("title"), "milestone title")
        event = {
            "number": _required_int(milestone.get("number"), "milestone number"),
            "title": title,
            "title_markdown": escape_markdown(title),
            "title_xml": escape_xml(title),
            "url": _validated_github_url(milestone.get("html_url"), repo_url, "milestone URL"),
            "closed_at": _timestamp(milestone.get("closed_at"), "milestone closed_at"),
        }
        existing = events_by_number.get(event["number"])
        if existing is not None and existing != event:
            raise GitHubApiError("GitHub returned conflicting copies of a milestone")
        events_by_number[event["number"]] = event
    events = list(events_by_number.values())
    return sorted(events, key=lambda item: (item["closed_at"], item["number"]))


def _pull_requests(fetch_json: JsonFetcher, slug: str, repo_url: str) -> list[dict[str, Any]]:
    base = _endpoint(slug, "pulls") + "?state=all&sort=updated&direction=desc"
    requests = _paged(fetch_json, base, "pull requests")
    projects = []
    seen_numbers: set[int] = set()
    for request in requests:
        number = _required_int(request.get("number"), "pull request number")
        if number in seen_numbers:
            continue
        seen_numbers.add(number)
        state = request.get("state")
        merged_at = _timestamp(request.get("merged_at"), "pull request merged_at", optional=True)
        draft = request.get("draft")
        if not isinstance(draft, bool) or not isinstance(state, str) or state not in {"open", "closed"}:
            raise GitHubApiError("GitHub response has an invalid pull request state")
        if state == "open":
            if merged_at is not None:
                raise GitHubApiError("GitHub returned an open, merged pull request")
            status = "draft" if draft else "open"
            closed_at = None
        else:
            closed_at = _timestamp(request.get("closed_at"), "pull request closed_at")
            status = "merged" if merged_at is not None else "closed"
        title = _required_text(request.get("title"), "pull request title")
        projects.append({
            "number": number,
            "title": title,
            "title_markdown": escape_markdown(title),
            "title_xml": escape_xml(title),
            "url": _validated_github_url(request.get("html_url"), repo_url, "pull request URL"),
            "status": status,
            "draft": draft,
            "created_at": _timestamp(request.get("created_at"), "pull request created_at"),
            "updated_at": _timestamp(request.get("updated_at"), "pull request updated_at"),
            "closed_at": closed_at,
            "merged_at": merged_at,
        })
    return sorted(projects, key=lambda item: item["number"])


def _open_issues(fetch_json: JsonFetcher, slug: str, repo_url: str) -> dict[str, Any]:
    base = _endpoint(slug, "issues") + "?state=open&sort=updated&direction=desc"
    records = _paged(fetch_json, base, "open issues")
    issues: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for record in records:
        # GitHub's issues endpoint also returns pull requests. Keep the two
        # categories separate by excluding every record with this key.
        if "pull_request" in record:
            continue
        number = _required_int(record.get("number"), "issue number")
        if number in seen_numbers:
            raise GitHubApiError("GitHub returned a duplicate issue")
        seen_numbers.add(number)
        if record.get("state") != "open":
            raise GitHubApiError("GitHub returned a non-open issue for the open issue query")
        title = _required_text(record.get("title"), "issue title")
        issues.append({
            "number": number,
            "title": title,
            "title_markdown": escape_markdown(title),
            "title_xml": escape_xml(title),
            "url": _validated_github_url(record.get("html_url"), repo_url, "issue URL"),
            "created_at": _timestamp(record.get("created_at"), "issue created_at"),
            "updated_at": _timestamp(record.get("updated_at"), "issue updated_at"),
        })
    issues.sort(key=lambda item: (item["updated_at"], item["number"]), reverse=True)
    return {"count": len(issues), "recent": issues[:5]}


def _latest_default_branch_workflow_run(
    fetch_json: JsonFetcher, slug: str, repo_url: str, default_branch: str,
) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"branch": default_branch, "per_page": 1})
    payload = _get(fetch_json, _endpoint(slug, "actions/runs") + "?" + query, not_found_ok=True)
    if payload is None:
        return None
    result = _object(payload, "workflow runs response")
    total_count = _required_int(result.get("total_count"), "workflow run count")
    runs = _array(result.get("workflow_runs"), "workflow runs")
    if total_count < len(runs):
        raise GitHubApiError("GitHub returned more workflow runs than its count")
    if not runs:
        if total_count != 0:
            raise GitHubApiError("GitHub returned a workflow count without a workflow run")
        return None
    run = _object(runs[0], "workflow run")
    head_branch = _required_text(run.get("head_branch"), "workflow run branch")
    if head_branch != default_branch:
        raise GitHubApiError("GitHub returned a workflow run from a different branch")
    head_sha = _required_text(run.get("head_sha"), "workflow run commit SHA")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise GitHubApiError("GitHub returned an invalid workflow run commit SHA")
    conclusion = run.get("conclusion")
    if conclusion is not None:
        conclusion = _required_text(conclusion, "workflow run conclusion")
    return {
        "name": _required_text(run.get("name") or run.get("display_title"), "workflow run name"),
        "status": _required_text(run.get("status"), "workflow run status"),
        "conclusion": conclusion,
        "head_sha": head_sha.lower(),
        "url": _validated_github_url(run.get("html_url"), repo_url, "workflow run URL"),
        "created_at": _timestamp(run.get("created_at"), "workflow run created_at"),
    }


def _latest_commit(fetch_json: JsonFetcher, slug: str, repo_url: str,
                   default_branch: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"sha": default_branch, "per_page": 1})
    commits = _array(_get(fetch_json, _endpoint(slug, "commits") + "?" + query), "default branch commits")
    if not commits:
        return None
    commit = _object(commits[0], "default branch commit")
    sha = _required_text(commit.get("sha"), "commit SHA")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise GitHubApiError("GitHub response has an invalid commit SHA")
    details = _object(commit.get("commit"), "commit details")
    committer = _object(details.get("committer"), "commit committer")
    return {
        "sha": sha.lower(),
        "url": _validated_github_url(commit.get("html_url"), repo_url, "commit URL"),
        "committed_at": _timestamp(committer.get("date"), "commit committer date"),
    }


def _collect_repository(slug: str, fetch_json: JsonFetcher) -> dict[str, Any]:
    owner, name = _valid_repository_slug(slug)
    canonical_slug = f"{owner}/{name}"
    repo_url = f"https://{GITHUB_HOST}/{owner}/{name}"
    metadata = _object(_get(fetch_json, _endpoint(canonical_slug, "")), "repository")
    returned_slug = _required_text(metadata.get("full_name"), "repository full_name")
    if returned_slug.casefold() != canonical_slug.casefold():
        raise GitHubApiError("GitHub returned a different repository than requested")
    if metadata.get("private") is not False or metadata.get("visibility", "public") != "public":
        raise GitHubApiError("The reviewed repository allowlist resolved to a non-public repository")
    _validated_github_url(metadata.get("html_url"), repo_url, "repository URL")
    default_branch = _required_text(metadata.get("default_branch"), "default branch")
    if "/" in default_branch or "\\" in default_branch:
        raise GitHubApiError("GitHub returned an invalid default branch")
    open_issues = _open_issues(fetch_json, canonical_slug, repo_url)
    return {
        "repository": canonical_slug,
        "url": repo_url,
        "default_branch": default_branch,
        "latest_default_branch_commit": _latest_commit(fetch_json, canonical_slug, repo_url, default_branch),
        "latest_release": _release(fetch_json, canonical_slug, repo_url),
        "closed_milestones": _closed_milestones(fetch_json, canonical_slug, repo_url),
        "pull_requests": _pull_requests(fetch_json, canonical_slug, repo_url),
        "open_issue_count": open_issues["count"],
        "recent_open_issues": open_issues["recent"],
        "latest_default_branch_workflow_run": _latest_default_branch_workflow_run(
            fetch_json, canonical_slug, repo_url, default_branch,
        ),
    }


def collect_projects(repositories: Iterable[str], fetch_json: JsonFetcher = _fetch_json) -> dict[str, Any]:
    """Fetch and normalize public facts for an explicit repository allowlist.

    A failure from any endpoint aborts the whole result so callers cannot
    mistake a partial acquisition for a complete dashboard generation.
    """
    slugs = list(repositories)
    if not slugs:
        raise ValueError("At least one exact public repository is required")
    normalized: dict[str, str] = {}
    for slug in slugs:
        owner, name = _valid_repository_slug(slug)
        canonical = f"{owner}/{name}"
        key = canonical.casefold()
        if key in normalized:
            raise ValueError(f"Duplicate repository in allowlist: {canonical}")
        normalized[key] = canonical
    projects = [_collect_repository(normalized[key], fetch_json) for key in sorted(normalized)]
    timeline: list[dict[str, Any]] = []
    for project in projects:
        repo = project["repository"]
        commit = project["latest_default_branch_commit"]
        if commit:
            timeline.append({
                "repository": repo, "kind": "default_branch_commit",
                "occurred_at": commit["committed_at"], "url": commit["url"],
                "sha": commit["sha"],
            })
        release = project["latest_release"]
        if release:
            timeline.append({
                "repository": repo, "kind": "release",
                "occurred_at": release["published_at"], "url": release["url"],
                "title": release["title"], "title_markdown": release["title_markdown"],
                "title_xml": release["title_xml"],
            })
        for milestone in project["closed_milestones"]:
            timeline.append({
                "repository": repo, "kind": "milestone_closed",
                "occurred_at": milestone["closed_at"], "url": milestone["url"],
                "number": milestone["number"], "title": milestone["title"],
                "title_markdown": milestone["title_markdown"],
                "title_xml": milestone["title_xml"],
            })
        for pull in project["pull_requests"]:
            time_field = "merged_at" if pull["status"] == "merged" else "closed_at" if pull["status"] == "closed" else "created_at"
            timeline.append({
                "repository": repo, "kind": "pull_request", "status": pull["status"],
                "occurred_at": pull[time_field] or pull["created_at"], "url": pull["url"],
                "number": pull["number"], "title": pull["title"],
                "title_markdown": pull["title_markdown"], "title_xml": pull["title_xml"],
            })
        workflow = project["latest_default_branch_workflow_run"]
        if workflow:
            timeline.append({
                "repository": repo, "kind": "workflow_run",
                "occurred_at": workflow["created_at"], "url": workflow["url"],
                "name": workflow["name"], "status": workflow["status"],
                "conclusion": workflow["conclusion"], "sha": workflow["head_sha"],
            })
    timeline.sort(key=lambda event: (
        event["occurred_at"], event["repository"].casefold(), event["kind"],
        event.get("number", 0), event.get("sha", ""),
    ), reverse=True)
    return {"schema_version": 1, "projects": projects, "timeline": timeline}


def refresh_projects(path: str | Path, repositories: Iterable[str],
                     fetch_json: JsonFetcher = _fetch_json) -> dict[str, Any]:
    """Atomically replace a previous snapshot only after complete acquisition.

    Network and validation failures occur before the target is opened. A write
    or replacement failure also leaves the prior snapshot intact.
    """
    snapshot = collect_projects(repositories, fetch_json)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = temporary.name
            json.dump(snapshot, temporary, ensure_ascii=False, sort_keys=True, indent=2)
            temporary.write("\n")
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
    return snapshot
