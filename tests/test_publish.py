from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.living_portfolio.publish import PublishError, publish_bundle


def _git_sha(kind: str, content: bytes) -> str:
    header = f"{kind} {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


class _Response:
    def __init__(self, status: int, payload: dict[str, object], path: str) -> None:
        self.status = status
        self.path = path
        self.headers = {"X-GitHub-Request-Id": "test-request", "X-RateLimit-Remaining": "4999"}
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://api.github.com" + self.path

    def read(self, size: int = -1) -> bytes:
        return self._payload[:size]


class _MemoryGitHub:
    def __init__(self) -> None:
        self.ref: str | None = None
        self.commits: dict[str, str] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.blobs: dict[str, bytes] = {}
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []
        self.fail_next_blob = False

    def __call__(self, request: object, *, timeout: int) -> _Response:
        method = request.get_method()  # type: ignore[attr-defined]
        parsed = urlsplit(request.full_url)  # type: ignore[attr-defined]
        self.assert_auth(request)
        body = json.loads(request.data) if request.data else None  # type: ignore[attr-defined]
        self.requests.append((method, parsed.path, body))
        path = parsed.path

        def respond(status: int, payload: dict[str, object]) -> _Response:
            full_path = path + (f"?{parsed.query}" if parsed.query else "")
            return _Response(status, payload, full_path)

        if method == "GET" and path.endswith("/git/ref/heads/portfolio-assets"):
            if self.ref is None:
                return respond(404, {"message": "Not Found"})
            return respond(200, {"object": {"sha": self.ref}})
        if method == "GET" and "/git/commits/" in path:
            sha = path.rsplit("/", 1)[-1]
            return respond(200, {"tree": {"sha": self.commits[sha]}})
        if method == "GET" and "/contents/" in path:
            path_name = path.split("/contents/", 1)[1]
            ref_commit = self.ref
            if ref_commit is None:
                return respond(404, {"message": "Not Found"})
            tree = self.trees[self.commits[ref_commit]]
            blob_sha = tree.get(path_name)
            if blob_sha is None:
                return respond(404, {"message": "Not Found"})
            return respond(200, {
                "encoding": "base64",
                "content": base64.b64encode(self.blobs[blob_sha]).decode("ascii"),
            })
        if method == "POST" and path.endswith("/git/blobs"):
            if self.fail_next_blob:
                self.fail_next_blob = False
                return respond(403, {"message": "Resource not accessible by integration"})
            content = base64.b64decode(body["content"])
            sha = _git_sha("blob", content)
            self.blobs[sha] = content
            return respond(201, {"sha": sha})
        if method == "POST" and path.endswith("/git/trees"):
            entry_paths = [entry["path"] for entry in body["tree"]]
            if len(entry_paths) != len(set(entry_paths)):
                return respond(422, {"message": "Tree entries must use unique paths"})
            tree = dict(self.trees.get(body.get("base_tree"), {}))
            for entry in body["tree"]:
                if entry["sha"] is None:
                    tree.pop(entry["path"], None)
                else:
                    tree[entry["path"]] = entry["sha"]
            canonical = json.dumps(tree, sort_keys=True).encode("utf-8")
            sha = hashlib.sha1(canonical).hexdigest()
            self.trees[sha] = tree
            return respond(201, {"sha": sha})
        if method == "POST" and path.endswith("/git/commits"):
            tree_sha = body["tree"]
            raw = json.dumps(body, sort_keys=True).encode("utf-8")
            sha = hashlib.sha1(raw).hexdigest()
            self.commits[sha] = tree_sha
            return respond(201, {"sha": sha})
        if method == "POST" and path.endswith("/git/refs"):
            self.ref = body["sha"]
            return respond(201, {"object": {"sha": self.ref}})
        if method == "PATCH" and path.endswith("/git/refs/heads/portfolio-assets"):
            if body["force"] is not False:
                raise AssertionError("Asset updates must not force-push")
            self.ref = body["sha"]
            return respond(200, {"object": {"sha": self.ref}})
        raise AssertionError(f"Unexpected GitHub API request: {method} {path}")

    @staticmethod
    def assert_auth(request: object) -> None:
        headers = request.header_items()  # type: ignore[attr-defined]
        authorization = next((value for key, value in headers if key.casefold() == "authorization"), "")
        if authorization != "Bearer test-token-for-tests":
            raise AssertionError("Publisher must use the injected token")


def _make_bundle(root: Path, *, date: str, extra_asset: bool = False) -> None:
    version = hashlib.sha256(date.encode("ascii")).hexdigest()
    storage_id = version[:24]
    bundle = root / "versions" / storage_id
    bundle.mkdir(parents=True)
    assets = {
        "github-calendar/calendar-light.svg": (
            f'<svg xmlns="http://www.w3.org/2000/svg"><title>Data through {date}</title></svg>\n'
        ).encode("utf-8"),
        "provenance.json": json.dumps({
            "sources": {
                "github_public_contributions": {
                    "scope": "Public contribution calendar; no private or restricted-contribution fields.",
                    "authentication": "Job token with contents:read; no read:user scope.",
                },
            },
        }, sort_keys=True).encode("utf-8"),
    }
    if extra_asset:
        assets["github-calendar/calendar-dark.svg"] = b"<svg></svg>\n"
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in assets.items()}
    manifest = {
        "schema_version": 1,
        "asset_sha256": hashes,
        "github_contribution_summary": {
            "scope": "github_public_contributions",
            "data_through": date,
        },
    }
    for name, content in assets.items():
        destination = bundle.joinpath(*Path(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    (root / "current.json").write_text(json.dumps({
        "version": version,
        "manifest": f"versions/{storage_id}/manifest.json",
    }), encoding="utf-8")


class PublishTests(unittest.TestCase):
    def test_first_publish_and_identical_rerun_are_idempotent(self) -> None:
        api = _MemoryGitHub()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_bundle(root, date="2026-11-02")
            first = publish_bundle(root, "test-token-for-tests", transport=api)
            request_count = len(api.requests)
            second = publish_bundle(root, "test-token-for-tests", transport=api)

        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(first["commit_sha"], second["commit_sha"])
        self.assertEqual(len(api.requests), request_count + 4)
        self.assertTrue(any(method == "POST" and path.endswith("/git/refs") for method, path, _ in api.requests))

    def test_changed_bundle_updates_pointer_and_removes_stale_asset(self) -> None:
        api = _MemoryGitHub()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_bundle(root, date="2026-11-02", extra_asset=True)
            initial = publish_bundle(root, "test-token-for-tests", transport=api)
            _make_bundle(root, date="2026-11-03")
            updated = publish_bundle(root, "test-token-for-tests", transport=api)

        self.assertEqual(updated["status"], "published")
        self.assertNotEqual(initial["commit_sha"], updated["commit_sha"])
        tree_sha = api.commits[api.ref]
        self.assertIn("current/github-calendar/calendar-light.svg", api.trees[tree_sha])
        self.assertNotIn("current/github-calendar/calendar-dark.svg", api.trees[tree_sha])
        patch = next(body for method, path, body in api.requests
                     if method == "PATCH" and path.endswith("/git/refs/heads/portfolio-assets"))
        self.assertIs(patch["force"], False)

    def test_failed_asset_upload_leaves_last_valid_ref_unchanged(self) -> None:
        api = _MemoryGitHub()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_bundle(root, date="2026-11-02")
            first = publish_bundle(root, "test-token-for-tests", transport=api)
            _make_bundle(root, date="2026-11-03")
            previous_ref = api.ref
            api.fail_next_blob = True
            with self.assertRaisesRegex(PublishError, "HTTP 403"):
                publish_bundle(root, "test-token-for-tests", transport=api)

        self.assertEqual(api.ref, previous_ref)
        self.assertEqual(api.ref, first["commit_sha"])

    def test_corrupt_asset_hash_is_rejected_before_network_access(self) -> None:
        api = _MemoryGitHub()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_bundle(root, date="2026-11-02")
            image = root / "versions" / (hashlib.sha256(b"2026-11-02").hexdigest()[:24]) / "github-calendar" / "calendar-light.svg"
            image.write_text("<svg>tampered</svg>", encoding="utf-8")
            with self.assertRaisesRegex(PublishError, "digest"):
                publish_bundle(root, "test-token-for-tests", transport=api)

        self.assertEqual(api.requests, [])

    def test_unapproved_asset_path_is_rejected_before_network_access(self) -> None:
        api = _MemoryGitHub()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_bundle(root, date="2026-11-02")
            storage_id = hashlib.sha256(b"2026-11-02").hexdigest()[:24]
            manifest_path = root / "versions" / storage_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            digest = next(iter(manifest["asset_sha256"].values()))
            manifest["asset_sha256"]["private/notes.svg"] = digest
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(PublishError, "outside the publication allowlist"):
                publish_bundle(root, "test-token-for-tests", transport=api)

        self.assertEqual(api.requests, [])


if __name__ == "__main__":
    unittest.main()
