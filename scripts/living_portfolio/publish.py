#!/usr/bin/env python3
"""Publish a validated public-data preview to the portfolio-assets branch."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable


REPOSITORY = "DoArtMonteiro/DoArtMonteiro"
BRANCH = "portfolio-assets"
API_ROOT = "https://api.github.com"
USER_AGENT = "living-portfolio-publisher/1.0"
MAX_RESPONSE_BYTES = 2_000_000
MAX_ASSET_BYTES = 5_000_000
MAX_BUNDLE_BYTES = 20_000_000
MAX_ASSETS = 128
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STORAGE_ID = re.compile(r"[0-9a-f]{24}\Z")
_GITHUB_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_PRIVATE_PATH = re.compile(r"(?i)(?:[A-Z]:\\Users\\|/home/[^/]+/)")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_TEXT_SUFFIXES = {".csv", ".json", ".md", ".svg"}
_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg"}


class PublishError(RuntimeError):
    """Raised when bundle validation or atomic GitHub publication fails."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _default_transport(request: urllib.request.Request, *, timeout: int) -> Any:
    opener = urllib.request.build_opener(_RejectRedirects())
    return opener.open(request, timeout=timeout)


Transport = Callable[..., Any]


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PublishError("Cannot serialize the live asset pointer") from error


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise PublishError("Generated bundle contains an invalid asset path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PublishError("Generated bundle contains an unsafe asset path")
    if path.parts[0] not in {"calendar", "github-calendar", "projects", "campaigns"} and value not in {
        "provenance.json", "projects-source.json",
    }:
        raise PublishError("Generated bundle contains an asset outside the publication allowlist")
    if path.as_posix() != value or path.suffix.casefold() not in _TEXT_SUFFIXES | _IMAGE_SUFFIXES:
        raise PublishError("Generated bundle contains an unsupported asset path")
    if value.startswith(("current/", "versions/")):
        raise PublishError("Generated bundle contains a reserved asset path")
    return value


def _reject_links(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise PublishError("Generated asset is outside its reviewed bundle") from None
    cursor = root
    path_chain = (root, *(root.joinpath(*relative.parts[:index])
                           for index in range(1, len(relative.parts) + 1)))
    for cursor in path_chain:
        try:
            if cursor.is_symlink():
                raise PublishError("Generated bundle cannot contain symbolic links")
            attributes = getattr(cursor.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise PublishError("Cannot validate a generated asset path") from error
        if attributes & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400):
            raise PublishError("Generated bundle cannot contain junctions or reparse points")


def _read_json(path: Path, *, root: Path) -> tuple[bytes, Any]:
    _reject_links(root, path)
    try:
        payload = path.read_bytes()
        return payload, json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublishError("Generated bundle metadata is missing or invalid") from error


def _scan_public_asset(path: str, payload: bytes) -> None:
    if len(payload) > MAX_ASSET_BYTES:
        raise PublishError("Generated bundle contains an oversized asset")
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix not in _TEXT_SUFFIXES:
        return
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise PublishError("Generated text asset is not valid UTF-8") from None
    if (_SECRET.search(content) or _PRIVATE_PATH.search(content) or _EMAIL.search(content)
            or "restrictedContributionsCount" in content):
        raise PublishError("Generated bundle failed the public-data safety scan")


def load_publication_bundle(output_dir: str | Path) -> tuple[dict[str, bytes], dict[str, Any], bytes]:
    """Read and hash-check the complete generated bundle before making API calls."""
    root = Path(os.path.abspath(output_dir))
    pointer_bytes, pointer = _read_json(root / "current.json", root=root)
    if not isinstance(pointer, dict) or not isinstance(pointer.get("version"), str):
        raise PublishError("Generated bundle has no valid current-version pointer")
    version = pointer["version"]
    manifest_ref = pointer.get("manifest")
    expected_ref = f"versions/{version[:24]}/manifest.json" if _SHA256.fullmatch(version) else ""
    if manifest_ref != expected_ref or not _STORAGE_ID.fullmatch(version[:24]):
        raise PublishError("Generated bundle current-version pointer is invalid")

    manifest_path = root.joinpath(*PurePosixPath(manifest_ref).parts)
    manifest_bytes, manifest = _read_json(manifest_path, root=root)
    bundle_root = manifest_path.parent
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PublishError("Generated bundle manifest schema is unsupported")
    assets = manifest.get("asset_sha256")
    if not isinstance(assets, dict) or not 1 <= len(assets) <= MAX_ASSETS:
        raise PublishError("Generated bundle manifest has an invalid asset list")
    contribution_summary = manifest.get("github_contribution_summary")
    if (not isinstance(contribution_summary, dict)
            or contribution_summary.get("scope") != "github_public_contributions"
            or not isinstance(contribution_summary.get("data_through"), str)):
        raise PublishError("Generated bundle lacks the verified public contribution calendar")

    source_metadata = assets.get("provenance.json")
    if not isinstance(source_metadata, str) or not _SHA256.fullmatch(source_metadata):
        raise PublishError("Generated bundle lacks public-data provenance")
    provenance_path = bundle_root / "provenance.json"
    _reject_links(root, provenance_path)
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        graphql_source = provenance["sources"]["github_public_contributions"]
        scope = graphql_source["scope"]
        authentication = graphql_source["authentication"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise PublishError("Generated bundle public-data provenance is invalid") from None
    if (not isinstance(scope, str) or "no private or restricted-contribution fields" not in scope
            or not isinstance(authentication, str) or "no read:user scope" not in authentication):
        raise PublishError("Generated bundle does not prove a public-only contribution request")

    files: dict[str, bytes] = {}
    total_bytes = 0
    for raw_path, expected_digest in assets.items():
        relative = _safe_relative_path(raw_path)
        if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
            raise PublishError("Generated bundle has an invalid asset digest")
        source = bundle_root.joinpath(*PurePosixPath(relative).parts)
        _reject_links(root, source)
        try:
            content = source.read_bytes()
        except OSError as error:
            raise PublishError("A generated asset listed in the manifest is missing") from error
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_digest:
            raise PublishError("A generated asset does not match its manifest digest")
        _scan_public_asset(relative, content)
        total_bytes += len(content)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise PublishError("Generated bundle exceeds the publication size limit")
        files[f"current/{relative}"] = content

    _scan_public_asset("current/manifest.json", manifest_bytes)
    files["current/manifest.json"] = manifest_bytes
    public_pointer = {
        "schema_version": 1,
        "status": "live",
        "version": version,
        "manifest": "current/manifest.json",
        "data_through": contribution_summary["data_through"],
        "source_scope": "github_public_contributions",
        "stale_after_hours": 36,
        "failure_behaviour": "The last valid assets remain available; the refresh workflow reports failures.",
    }
    pointer_payload = _json_bytes(public_pointer)
    _scan_public_asset("current.json", pointer_payload)
    files["current.json"] = pointer_payload
    return files, manifest, manifest_bytes


class _GitHubApi:
    def __init__(self, token: str, transport: Transport = _default_transport) -> None:
        if not isinstance(token, str) or not token or any(character.isspace() for character in token):
            raise PublishError("A GitHub Actions token is required for asset publication")
        self.token = token
        self.transport = transport

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], Any]:
        url = API_ROOT + path
        parsed = urllib.parse.urlsplit(url)
        expected_path = urllib.parse.urlsplit(path)
        if (parsed.scheme != "https" or parsed.hostname != "api.github.com"
                or parsed.path != expected_path.path or parsed.query != expected_path.query
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise PublishError("Asset publication is restricted to the GitHub API host")
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }, method=method)
        try:
            response = self.transport(request, timeout=20)
            with response:
                final = urllib.parse.urlsplit(response.geturl())
                if (final.scheme != "https" or final.hostname != "api.github.com"
                        or final.path != parsed.path or final.query != parsed.query or final.username is not None
                        or final.password is not None or final.port not in (None, 443)):
                    raise PublishError("GitHub redirected the publication request off-host")
                status = response.status
                headers = response.headers
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            headers = error.headers
            raw = error.read(MAX_RESPONSE_BYTES + 1)
            error.close()
        except PublishError:
            raise
        except Exception:
            raise PublishError("GitHub asset publication request failed") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise PublishError("GitHub asset publication response exceeded the size limit")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return status, payload, headers

    def require(self, method: str, path: str, body: dict[str, Any] | None = None,
                *, accepted: tuple[int, ...] = (200,)) -> dict[str, Any]:
        status, payload, headers = self.request(method, path, body)
        if status not in accepted:
            raise self.error(status, payload, headers)
        return payload

    def error(self, status: int, payload: dict[str, Any], headers: Any) -> PublishError:
        message = payload.get("message")
        if not isinstance(message, str):
            message = "GitHub rejected the publication request"
        message = message.replace(self.token, "[redacted]")
        message = _SECRET.sub("[redacted]", message)[:240]
        request_id = headers.get("X-GitHub-Request-Id", "") if headers else ""
        remaining = headers.get("X-RateLimit-Remaining", "") if headers else ""
        evidence = ", ".join(part for part in (
            f"request {request_id}" if request_id else "",
            f"rate-limit remaining {remaining}" if remaining else "",
        ) if part)
        suffix = f" ({evidence})" if evidence else ""
        return PublishError(f"GitHub API HTTP {status}: {message}{suffix}")


def _content(api: _GitHubApi, path: str) -> bytes | None:
    url_path = f"/repos/{REPOSITORY}/contents/{path}?ref={BRANCH}"
    status, payload, headers = api.request("GET", url_path)
    if status == 404:
        return None
    if status != 200:
        raise api.error(status, payload, headers)
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise PublishError("Cannot read the previous portfolio asset manifest")
    try:
        encoded = "".join(payload["content"].split())
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise PublishError("The previous portfolio asset manifest is invalid") from None


def _old_asset_hashes(previous_manifest_bytes: bytes | None) -> dict[str, str]:
    if previous_manifest_bytes is None:
        return {}
    try:
        previous = json.loads(previous_manifest_bytes.decode("utf-8"))
        assets = previous.get("asset_sha256")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        raise PublishError("The previous portfolio asset manifest is invalid") from None
    if not isinstance(assets, dict):
        raise PublishError("The previous portfolio asset list is invalid")
    if len(assets) > MAX_ASSETS:
        raise PublishError("The previous portfolio asset list is invalid")
    result: dict[str, str] = {}
    for path, digest in assets.items():
        relative = _safe_relative_path(path)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise PublishError("The previous portfolio asset list is invalid")
        result[relative] = digest
    return result


def publish_bundle(output_dir: str | Path, token: str, *, transport: Transport = _default_transport) -> dict[str, Any]:
    """Create one non-forced Git commit with the complete validated asset set."""
    if os.environ.get("GITHUB_REPOSITORY", REPOSITORY) != REPOSITORY:
        raise PublishError("Publication is restricted to the reviewed profile repository")
    files, manifest, manifest_bytes = load_publication_bundle(output_dir)
    api = _GitHubApi(token, transport)
    prefix = f"/repos/{REPOSITORY}/git"

    ref_status, ref_payload, ref_headers = api.request("GET", f"{prefix}/ref/heads/{BRANCH}")
    if ref_status not in (200, 404):
        raise api.error(ref_status, ref_payload, ref_headers)
    exists = ref_status == 200
    parent_sha = ""
    base_tree = ""
    previous_manifest = None
    previous_pointer = None
    if exists:
        parent_sha = ref_payload.get("object", {}).get("sha", "")
        if not _GITHUB_SHA.fullmatch(parent_sha):
            raise PublishError("The existing asset branch has an invalid commit reference")
        previous_commit = api.require("GET", f"{prefix}/commits/{parent_sha}")
        base_tree = previous_commit.get("tree", {}).get("sha", "")
        if not _GITHUB_SHA.fullmatch(base_tree):
            raise PublishError("The existing asset branch has an invalid tree reference")
        previous_manifest = _content(api, "current/manifest.json")
        previous_pointer = _content(api, "current.json")

    new_manifest = files["current/manifest.json"]
    new_pointer = files["current.json"]
    if exists and previous_manifest == new_manifest and previous_pointer == new_pointer:
        return {"status": "unchanged", "commit_sha": parent_sha,
                "version": json.loads(files["current.json"])["version"],
                "asset_count": len(manifest["asset_sha256"])}

    previous_hashes = _old_asset_hashes(previous_manifest)
    old_paths = set(previous_hashes)
    new_paths = {path.removeprefix("current/") for path in files if path.startswith("current/")
                 and path != "current/manifest.json"}
    entries: list[dict[str, Any]] = []
    for path, content in sorted(files.items()):
        if path == "current.json":
            continue
        if exists and path.startswith("current/"):
            relative = path.removeprefix("current/")
            if relative != "manifest.json" and relative in old_paths:
                if hashlib.sha256(content).hexdigest() == previous_hashes[relative]:
                    continue
        blob = api.require("POST", f"{prefix}/blobs", {
            "content": base64.b64encode(content).decode("ascii"), "encoding": "base64",
        }, accepted=(201,))
        blob_sha = blob.get("sha", "")
        if not _GITHUB_SHA.fullmatch(blob_sha):
            raise PublishError("GitHub returned an invalid asset object reference")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

    for stale in sorted(old_paths - new_paths):
        entries.append({"path": f"current/{stale}", "mode": "100644", "type": "blob", "sha": None})
    pointer_blob = api.require("POST", f"{prefix}/blobs", {
        "content": base64.b64encode(new_pointer).decode("ascii"), "encoding": "base64",
    }, accepted=(201,))
    pointer_sha = pointer_blob.get("sha", "")
    if not _GITHUB_SHA.fullmatch(pointer_sha):
        raise PublishError("GitHub returned an invalid current-pointer reference")
    entries.append({"path": "current.json", "mode": "100644", "type": "blob", "sha": pointer_sha})

    tree_body: dict[str, Any] = {"tree": entries}
    if base_tree:
        tree_body["base_tree"] = base_tree
    tree = api.require("POST", f"{prefix}/trees", tree_body, accepted=(201,))
    tree_sha = tree.get("sha", "")
    if not _GITHUB_SHA.fullmatch(tree_sha):
        raise PublishError("GitHub returned an invalid portfolio tree reference")
    commit_body: dict[str, Any] = {
        "message": "Refresh live portfolio assets",
        "tree": tree_sha,
    }
    if parent_sha:
        commit_body["parents"] = [parent_sha]
    commit = api.require("POST", f"{prefix}/commits", commit_body, accepted=(201,))
    commit_sha = commit.get("sha", "")
    if not _GITHUB_SHA.fullmatch(commit_sha):
        raise PublishError("GitHub returned an invalid publication commit reference")

    if exists:
        updated = api.require("PATCH", f"{prefix}/refs/heads/{BRANCH}",
                              {"sha": commit_sha, "force": False})
    else:
        updated = api.require("POST", f"{prefix}/refs",
                              {"ref": f"refs/heads/{BRANCH}", "sha": commit_sha}, accepted=(201,))
    if updated.get("object", {}).get("sha") != commit_sha:
        raise PublishError("GitHub did not confirm the published portfolio revision")
    return {"status": "published", "commit_sha": commit_sha,
            "version": json.loads(files["current.json"])["version"],
            "asset_count": len(manifest["asset_sha256"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--token-env", default="GH_TOKEN")
    args = parser.parse_args(argv)
    try:
        token = os.environ.get(args.token_env, "")
        result = publish_bundle(args.source_dir, token)
        print(json.dumps(result, sort_keys=True))
    except (PublishError, OSError, ValueError) as error:
        print(f"Portfolio asset publication failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
