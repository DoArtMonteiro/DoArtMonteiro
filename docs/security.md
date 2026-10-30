# Data and workflow security

## Collection boundaries

- The commit-calendar collector accepts only the eight reviewed `DoArtMonteiro` repositories in the local allowlist. It calls GitHub's public REST commit endpoint without an authorization header, uses the repository's default branch, and retains only linked author dates and distinct commit counts. A private or inaccessible repository returns an error and stops generation.
- The preview workflow requests `contents: read` and uses GitHub's run-scoped token for public repository reads. It does not request `read:user`, create a personal access token or print the token. The public workflow-runs endpoint is called without authentication.
- Repository activity is limited to the exact public slugs in `assets/project-repositories.json`; the collector does not enumerate the account or accept another owner.
- Existing historical calendar CSVs and local campaign outputs are excluded because their public/private scope or approval cannot be established from this branch.

## File and output boundaries

The preview builder in `scripts/living_portfolio/preview.py` writes only under its allowlisted local test directory or `$RUNNER_TEMP/living-portfolio-preview`. It rejects output paths containing symbolic links or junctions and checks generated bundles before advancing their pointer. It does not write repository activity, application files or remote branches.

The manual preview workflow runs only from the default branch. It checks out that branch using a reviewed, SHA-pinned action with persisted credentials disabled, and does not run pull-request code.

The separate pull-request workflow does execute the proposed revision's Python test suite on Ubuntu and Windows. It has no repository secrets, grants only `contents: read`, and checks out code with persisted credentials disabled. Treat the proposed source as untrusted code. Neither workflow has a schedule, uploads generated files, writes to Git or deploys an application.

## Public asset review

The campaign index is empty, so no benchmark or simulation result chart is generated. The diagrams are self-contained SVG with no script, embedded HTML, external image or remote font. The only application screenshot points to the OmniQuiz repository at an immutable commit.

Pattern scans are useful checks, not proof that a file is safe. Review source provenance and the exact staged diff before publication. The pull-request workflow is configured for Ubuntu and Windows; confirm the Linux symlink test result there before accepting that safeguard.
