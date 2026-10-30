"""Render deterministic, theme-aware project activity views from REST data."""

from __future__ import annotations

import json
import math
import re
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

from scripts.living_portfolio.projects import escape_markdown


THEMES = {
    "light": {"background": "#f6f3ed", "panel": "#fffdf8", "text": "#1c2630", "muted": "#52616b", "line": "#c9c4b8", "accent": "#245f63", "highlight": "#9b4a2f"},
    "dark": {"background": "#101922", "panel": "#17232c", "text": "#f1eee6", "muted": "#b3c0c5", "line": "#3a4a53", "accent": "#78c6bc", "highlight": "#f1a675"},
}
EXPECTED_STATUSES = {"draft", "open", "merged", "closed"}
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")


class ProjectVisualError(ValueError):
    """Raised when project activity input is incomplete or unsafe."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ProjectVisualError(f"Invalid {field}")
    return value.strip()


def _repo_url(repository: str, value: Any) -> str:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ProjectVisualError("Invalid repository identifier")
    url = _text(value, "repository URL")
    parsed = urlsplit(url)
    expected = f"/{repository}"
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.path.rstrip("/").casefold() != expected.casefold() or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        raise ProjectVisualError("Unexpected repository URL")
    return url


def _event_url(repository: str, value: Any) -> str:
    url = _text(value, "event URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ProjectVisualError("Invalid event URL") from error
    prefix = f"/{repository}".casefold()
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or parsed.username or parsed.password or port not in (None, 443)
            or not parsed.path.casefold().startswith(prefix + "/")
            or parsed.query or parsed.fragment):
        raise ProjectVisualError("Unexpected event URL")
    if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
        raise ProjectVisualError("Invalid percent escape in event URL")
    safe_path = quote(parsed.path, safe="/-._~!$&'*,;=:@%")
    return urlunsplit(("https", "github.com", safe_path, "", ""))


def _validate_snapshot(snapshot: Any) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ProjectVisualError("Unsupported project snapshot schema")
    projects = snapshot.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ProjectVisualError("Project snapshot must contain at least one repository")
    seen: set[str] = set()
    validated = []
    for project in projects:
        if not isinstance(project, dict):
            raise ProjectVisualError("Invalid project entry")
        repository = _text(project.get("repository"), "repository")
        key = repository.casefold()
        if key in seen:
            raise ProjectVisualError("Duplicate repository in project snapshot")
        seen.add(key)
        url = _repo_url(repository, project.get("url"))
        default_branch = _text(project.get("default_branch"), "default branch")
        if "/" in default_branch or "\\" in default_branch:
            raise ProjectVisualError("Invalid default branch")
        commit = project.get("latest_default_branch_commit")
        if commit is not None:
            if not isinstance(commit, dict) or not re.fullmatch(r"[0-9a-f]{40}", _text(commit.get("sha"), "commit SHA")):
                raise ProjectVisualError("Invalid latest commit")
            _text(commit.get("committed_at"), "commit date")
            _event_url(repository, commit.get("url"))
        release = project.get("latest_release")
        if release is not None:
            if not isinstance(release, dict):
                raise ProjectVisualError("Invalid latest release")
            _text(release.get("tag"), "release tag")
            _text(release.get("title"), "release title")
            _text(release.get("published_at"), "release date")
            _event_url(repository, release.get("url"))
        milestones = project.get("closed_milestones")
        pulls = project.get("pull_requests")
        if not isinstance(milestones, list) or not isinstance(pulls, list):
            raise ProjectVisualError("Invalid milestone or pull request data")
        issue_count = project.get("open_issue_count")
        recent_issues = project.get("recent_open_issues")
        if isinstance(issue_count, bool) or not isinstance(issue_count, int) or issue_count < 0:
            raise ProjectVisualError("Invalid open issue count")
        if not isinstance(recent_issues, list) or len(recent_issues) > issue_count:
            raise ProjectVisualError("Invalid recent open issue data")
        for issue in recent_issues:
            if not isinstance(issue, dict):
                raise ProjectVisualError("Invalid recent open issue")
            _text(issue.get("title"), "issue title")
            _event_url(repository, issue.get("url"))
            _date_label(_text(issue.get("updated_at"), "issue update date"))
        for pull in pulls:
            if not isinstance(pull, dict) or pull.get("status") not in EXPECTED_STATUSES:
                raise ProjectVisualError("Invalid pull request status")
            _text(pull.get("title"), "pull request title")
        workflow = project.get("latest_default_branch_workflow_run")
        if workflow is not None:
            if not isinstance(workflow, dict):
                raise ProjectVisualError("Invalid latest workflow run")
            _text(workflow.get("name"), "workflow name")
            _text(workflow.get("status"), "workflow status")
            conclusion = workflow.get("conclusion")
            if conclusion is not None:
                _text(conclusion, "workflow conclusion")
            if not re.fullmatch(r"[0-9a-f]{40}", _text(workflow.get("head_sha"), "workflow SHA")):
                raise ProjectVisualError("Invalid workflow SHA")
            _event_url(repository, workflow.get("url"))
            _date_label(_text(workflow.get("created_at"), "workflow run date"))
        validated.append({**project, "repository": repository, "url": url, "default_branch": default_branch})
    return sorted(validated, key=lambda entry: entry["repository"].casefold())


def _date_label(value: str | None) -> str:
    if value is None:
        return "none"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProjectVisualError("Invalid event timestamp") from error
    if parsed.tzinfo is None:
        raise ProjectVisualError("Timestamp must include a timezone")
    return parsed.strftime("%d %b %Y")


def _escape(value: Any) -> str:
    return escape(str(value), {"\"": "&quot;", "'": "&apos;"})


def render_dashboard(snapshot: Any, theme: str) -> str:
    """Return an accessible SVG with objective repository and PR facts only."""
    if theme not in THEMES:
        raise ProjectVisualError("Theme must be light or dark")
    projects = _validate_snapshot(snapshot)
    colors = THEMES[theme]
    columns = 2
    rows = math.ceil(len(projects) / columns)
    card_height = 190
    card_gap = 16
    cards_top = 130
    timeline_top = cards_top + rows * (card_height + card_gap) + 28
    width = 1400
    height = timeline_top + 280
    output = [
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc" viewBox="0 0 {width} {height}">',
        '<title id="title">Public project activity dashboard</title>',
        '<desc id="desc">Latest default-branch commits and workflow runs, public releases, open issue counts, closed milestones, and public pull request states for an explicit repository list. This dashboard reports repository facts and does not infer project maturity or operational acceptance.</desc>',
        f'<rect width="{width}" height="{height}" fill="{colors["background"]}"/>',
        f'<text x="48" y="58" fill="{colors["text"]}" font-family="Arial,sans-serif" font-size="30" font-weight="700">Public project activity</text>',
        f'<text x="48" y="91" fill="{colors["muted"]}" font-family="Arial,sans-serif" font-size="16">GitHub public API · default-branch commits and workflow runs · releases · issues · milestones · pull request states</text>',
    ]
    card_width = 640
    for index, project in enumerate(projects):
        column = index // rows
        row = index % rows
        x = 48 + column * (card_width + 24)
        y = cards_top + row * (card_height + card_gap)
        output.append(f'<rect x="{x}" y="{y}" width="{card_width}" height="{card_height}" rx="14" fill="{colors["panel"]}" stroke="{colors["line"]}"/>')
        output.append(f'<text x="{x + 22}" y="{y + 32}" fill="{colors["text"]}" font-family="Arial,sans-serif" font-size="18" font-weight="700">{_escape(project["repository"])}</text>')
        commit = project.get("latest_default_branch_commit")
        if commit:
            activity = f"Default branch · {project['default_branch']} · {commit['sha'][:8]} · {_date_label(commit['committed_at'])}"
        else:
            activity = f"Default branch · {project['default_branch']} · no commit data"
        output.append(f'<text x="{x + 22}" y="{y + 61}" fill="{colors["muted"]}" font-family="Arial,sans-serif" font-size="14">{_escape(activity)}</text>')
        release = project.get("latest_release")
        release_text = f"Latest release · {release['tag']} · {_date_label(release['published_at'])}" if release else "Latest release · none recorded"
        output.append(f'<text x="{x + 22}" y="{y + 86}" fill="{colors["muted"]}" font-family="Arial,sans-serif" font-size="14">{_escape(release_text)}</text>')
        output.append(f'<text x="{x + 22}" y="{y + 111}" fill="{colors["text"]}" font-family="Arial,sans-serif" font-size="14">Open issues · {project["open_issue_count"]}</text>')
        status_counts = {status: sum(pull["status"] == status for pull in project["pull_requests"]) for status in ("draft", "open", "merged", "closed")}
        pull_text = "Pull requests · " + " · ".join(
            f"{label} {status_counts[status]}"
            for status, label in (("draft", "Draft"), ("open", "Open"), ("merged", "Merged"), ("closed", "Closed unmerged"))
        )
        output.append(f'<text x="{x + 22}" y="{y + 136}" fill="{colors["text"]}" font-family="Arial,sans-serif" font-size="14">{_escape(pull_text)}</text>')
        milestone_text = f"Closed milestones · {len(project['closed_milestones'])}"
        output.append(f'<text x="{x + 22}" y="{y + 161}" fill="{colors["accent"]}" font-family="Arial,sans-serif" font-size="14">{_escape(milestone_text)}</text>')
        workflow = project["latest_default_branch_workflow_run"]
        if workflow:
            workflow_result = workflow["conclusion"] or workflow["status"]
            workflow_text = f"Latest workflow · {_escape(workflow['name'])} · {_escape(workflow_result)} · {_date_label(workflow['created_at'])}"
        else:
            workflow_text = "Latest workflow · no default-branch run recorded"
        output.append(f'<text x="{x + 22}" y="{y + 183}" fill="{colors["muted"]}" font-family="Arial,sans-serif" font-size="13">{workflow_text}</text>')

    output.append(f'<text x="48" y="{timeline_top + 10}" fill="{colors["text"]}" font-family="Arial,sans-serif" font-size="22" font-weight="700">Recent repository events</text>')
    timeline = snapshot.get("timeline")
    if not isinstance(timeline, list):
        raise ProjectVisualError("Invalid project timeline")
    for index, event in enumerate(timeline[:8]):
        if not isinstance(event, dict):
            raise ProjectVisualError("Invalid timeline event")
        repository = _text(event.get("repository"), "event repository")
        kind = _text(event.get("kind"), "event kind")
        occurred = _date_label(_text(event.get("occurred_at"), "event date"))
        if kind == "pull_request":
            detail = f"pull request #{event.get('number')} · {_text(event.get('status'), 'pull request status')} · {_text(event.get('title'), 'event title')}"
        elif kind == "release":
            detail = f"release · {_text(event.get('title'), 'event title')}"
        elif kind == "milestone_closed":
            detail = f"milestone closed · {_text(event.get('title'), 'event title')}"
        elif kind == "default_branch_commit":
            detail = f"default-branch commit · {_text(event.get('sha'), 'event SHA')[:8]}"
        elif kind == "workflow_run":
            workflow_result = event.get("conclusion") or _text(event.get("status"), "workflow status")
            detail = f"workflow · {_text(event.get('name'), 'workflow name')} · {workflow_result}"
        else:
            raise ProjectVisualError("Unsupported timeline event kind")
        y = timeline_top + 48 + index * 25
        output.append(f'<text x="48" y="{y}" fill="{colors["muted"]}" font-family="Arial,sans-serif" font-size="14">{_escape(occurred)} · {_escape(repository)} · {_escape(detail)}</text>')
    output.append(f'<text x="48" y="{height - 24}" fill="{colors["muted"]}" font-family="Arial,sans-serif" font-size="13">Facts come from public GitHub REST endpoints. The profile repository records portfolio refresh runs separately from these project workflow results.</text>')
    output.append("</svg>")
    return "\n".join(output) + "\n"


def accessible_markdown(snapshot: Any) -> str:
    """Return a linked text alternative for screen readers and narrow screens."""
    projects = _validate_snapshot(snapshot)
    lines = [
        "# Public project activity",
        "",
        "Source: GitHub's public REST API. This summary reports repository facts; it does not infer project maturity, operational acceptance or personal ownership.",
        "",
    ]
    for project in projects:
        commit = project.get("latest_default_branch_commit")
        commit_url = _event_url(project["repository"], commit["url"]) if commit else None
        commit_label = f"[{commit['sha'][:8]}]({commit_url}) on {_date_label(commit['committed_at'])}" if commit else "no commit data"
        release = project.get("latest_release")
        release_label = (
            f"[{escape_markdown(release['tag'])}]({_event_url(project['repository'], release['url'])}) "
            f"on {_date_label(release['published_at'])}"
            if release else "none recorded"
        )
        counts = {status: sum(pull["status"] == status for pull in project["pull_requests"]) for status in ("draft", "open", "merged", "closed")}
        lines.extend([
            f"## [{project['repository']}]({project['url']})",
            "",
            f"- Latest default-branch commit: {commit_label}.",
            f"- Latest public release: {release_label}.",
            f"- Open issues: {project['open_issue_count']}.",
            f"- Pull requests: {counts['draft']} draft, {counts['open']} open, {counts['merged']} merged, {counts['closed']} closed without merge.",
            f"- Closed milestones: {len(project['closed_milestones'])}.",
        ])
        workflow = project.get("latest_default_branch_workflow_run")
        if workflow:
            result = workflow["conclusion"] or workflow["status"]
            workflow_url = _event_url(project["repository"], workflow["url"])
            lines.append(
                f"- Latest default-branch workflow: [{escape_markdown(workflow['name'])}: "
                f"{escape_markdown(result)}]({workflow_url}) on {_date_label(workflow['created_at'])}."
            )
        else:
            lines.append("- Latest default-branch workflow: no run returned by the public API.")
        lines.append("")
    timeline = snapshot.get("timeline")
    if not isinstance(timeline, list):
        raise ProjectVisualError("Invalid project timeline")
    lines.extend(["## Recent repository events", ""])
    for event in timeline[:8]:
        if not isinstance(event, dict):
            raise ProjectVisualError("Invalid timeline event")
        repository = _text(event.get("repository"), "event repository")
        kind = _text(event.get("kind"), "event kind")
        occurred = _date_label(_text(event.get("occurred_at"), "event date"))
        url = _event_url(repository, event.get("url"))
        if kind == "pull_request":
            status = _text(event.get("status"), "pull request status")
            if status not in EXPECTED_STATUSES:
                raise ProjectVisualError("Invalid pull request status")
            title = escape_markdown(_text(event.get("title"), "event title"))
            label = f"{repository} · pull request #{event.get('number')} · {status} · {title}"
        elif kind == "release":
            title = escape_markdown(_text(event.get("title"), "event title"))
            label = f"{repository} · release · {title}"
        elif kind == "milestone_closed":
            title = escape_markdown(_text(event.get("title"), "event title"))
            label = f"{repository} · closed milestone · {title}"
        elif kind == "default_branch_commit":
            label = f"{repository} · default-branch commit · {_text(event.get('sha'), 'event SHA')[:8]}"
        elif kind == "workflow_run":
            status = _text(event.get("status"), "workflow status")
            conclusion = event.get("conclusion") or status
            if event.get("conclusion") is not None:
                _text(event.get("conclusion"), "workflow conclusion")
            name = escape_markdown(_text(event.get("name"), "workflow name"))
            label = f"{repository} · workflow · {name} · {escape_markdown(conclusion)}"
        else:
            raise ProjectVisualError("Unsupported timeline event kind")
        lines.append(f"- [{occurred} · {label}]({url})")
    lines.append("")
    return "\n".join(lines)


def write_dashboard(snapshot_path: str | Path, output_dir: str | Path) -> dict[str, Path]:
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "light": output / "projects-dashboard-light.svg",
        "dark": output / "projects-dashboard-dark.svg",
        "accessible": output / "projects-activity.md",
    }
    values = {
        "light": render_dashboard(snapshot, "light"),
        "dark": render_dashboard(snapshot, "dark"),
        "accessible": accessible_markdown(snapshot),
    }
    values["json"] = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    paths["json"] = output / "projects-activity.json"
    for key, path in paths.items():
        payload = values[key].encode("utf-8")
        try:
            if path.read_bytes() == payload:
                continue
        except FileNotFoundError:
            pass
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=output, prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
    return paths
