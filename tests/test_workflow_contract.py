from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "living-portfolio-preview.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "living-portfolio-tests.yml"
REFRESH_WORKFLOW = ROOT / ".github" / "workflows" / "living-portfolio-refresh.yml"


class WorkflowContractTests(unittest.TestCase):
    def test_preview_is_limited_to_pr_four_and_default_branch_dispatch(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  pull_request:", source)
        self.assertIn("\n  workflow_dispatch:", source)
        self.assertIn("  pull_request:\n    types:", source)
        self.assertIn("github.event_name == 'workflow_dispatch' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", source)
        self.assertIn("github.event_name == 'pull_request' && github.event.pull_request.number == 4", source)
        self.assertIn("github.event.pull_request.head.sha", source)
        self.assertIn("ref: ${{ github.event_name == 'pull_request'", source)
        self.assertIn("github.event.repository.default_branch", source)
        self.assertNotIn("\n  schedule:", source)
        self.assertNotIn("\n  push:", source)
        self.assertNotIn("pull_request_target", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("permissions:\n      contents: read", source)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", source)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", source)
        self.assertIn("persist-credentials: false", source)
        self.assertNotIn("contents: write", source)
        self.assertIn("GH_TOKEN: ${{ github.token }}", source)
        self.assertNotIn("read:user", source)
        self.assertNotIn("secrets.GH_TOKEN", source)
        self.assertNotIn("git push", source)
        self.assertNotIn("vercel", source.casefold())

    def test_preview_uploads_complete_output_as_short_lived_artifact(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        uses = [line.strip() for line in source.splitlines() if line.strip().startswith("uses:")]
        self.assertEqual(len(uses), 2)
        self.assertIn("name: living-portfolio-preview-${{ github.run_id }}", source)
        self.assertIn("path: ${{ runner.temp }}/living-portfolio-preview/", source)
        self.assertIn("retention-days: 7", source)
        self.assertIn("if-no-files-found: error", source)
        self.assertIn("A diagnostic artifact is available for seven days", source)
        self.assertIn("No diagnostic artifact is available", source)
        self.assertIn("stale after 36 hours", source)
        self.assertIn("last-valid published bundle remain unchanged", source)
        self.assertIn("separate from the profile README assets", source)
        self.assertIn("No README asset was committed and no application was deployed", source)
        self.assertNotIn("Any previously approved bundle remains unchanged", source)

    def test_portfolio_validation_is_read_only_and_runs_without_secrets(self) -> None:
        source = TEST_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  push:\n", source)
        self.assertIn("\n  pull_request:", source)
        self.assertEqual(source.count('"workers/contribution-map/**"'), 2)
        self.assertIn("node --test workers/contribution-map/tests/calendar-data.test.mjs workers/contribution-map/tests/worker.test.mjs", source)
        self.assertIn("github.event.pull_request.number || github.sha", source)
        self.assertNotIn("\n  schedule:", source)
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

    def test_live_refresh_is_default_branch_only_and_uses_split_permissions(self) -> None:
        source = REFRESH_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  push:", source)
        self.assertIn("\n  schedule:\n", source)
        self.assertIn("\n  workflow_dispatch:\n", source)
        self.assertIn('cron: "17 5 * * *"', source)
        self.assertIn("branches:\n      - main", source)
        self.assertIn("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotIn("pull_request_target", source)
        self.assertIn("jobs:\n  generate:", source)
        self.assertIn("contents: read", source)
        self.assertIn("\n  publish:", source)
        self.assertIn("contents: write", source)
        self.assertIn("needs: generate", source)
        self.assertIn("--refresh-mode live", source)
        self.assertIn("portfolio-assets", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", source)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", source)
        self.assertIn("actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333", source)
        self.assertNotIn("read:user", source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("vercel", source.casefold())


if __name__ == "__main__":
    unittest.main()
