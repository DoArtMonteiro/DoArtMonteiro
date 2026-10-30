from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "living-portfolio-preview.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "living-portfolio-tests.yml"


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_manual_read_only_and_does_not_publish(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  workflow_dispatch:", source)
        self.assertIn("if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", source)
        self.assertNotIn("\n  schedule:", source)
        self.assertNotIn("\n  push:", source)
        self.assertNotIn("\n  pull_request:", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("permissions:\n      contents: read", source)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", source)
        self.assertNotIn("contents: write", source)
        self.assertNotIn("upload-artifact", source)
        self.assertNotIn("git push", source)
        self.assertNotIn("vercel", source.casefold())

    def test_workflow_has_one_pinned_external_action_and_unpublished_output(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        uses = [line.strip() for line in source.splitlines() if line.strip().startswith("uses:")]
        self.assertEqual(len(uses), 1)
        self.assertIn("No assets were uploaded, committed, published or deployed", source)
        self.assertIn("stale after 36 hours", source)
        self.assertIn("writes only to temporary runner storage and publishes nothing", source)
        self.assertNotIn("Any previously approved bundle remains unchanged", source)

    def test_pull_request_validation_is_read_only_and_runs_without_secrets(self) -> None:
        source = TEST_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  pull_request:", source)
        self.assertNotIn("\n  schedule:", source)
        self.assertNotIn("\n  push:", source)
        self.assertNotIn("pull_request_target", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("permissions:\n      contents: read", source)
        self.assertIn("runs-on: ${{ matrix.os }}", source)
        self.assertIn("ubuntu-latest", source)
        self.assertIn("windows-latest", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", source)
        self.assertIn("python -m unittest discover -s tests -v", source)
        self.assertNotIn("contents: write", source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("vercel", source.casefold())


if __name__ == "__main__":
    unittest.main()
