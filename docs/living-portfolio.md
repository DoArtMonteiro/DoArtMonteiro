# Living portfolio data and refresh model

## Current state

The repository contains a local generator, a manual GitHub Actions preview and a dated public-data snapshot for review. The workflow is read-only, writes output to temporary runner storage and publishes nothing. It has no scheduled trigger, does not create commits and does not deploy an application. The committed snapshot is not a live feed; it becomes stale after 36 hours until a separately approved publisher is activated. GitHub's native contribution graph remains available from the profile link.

## Data sources and inclusion

### Contribution calendar

The calendar counts distinct commits linked to `DoArtMonteiro` in the default branches of the eight repositories in `assets/project-repositories.json`. It uses GitHub's public REST list-commits endpoint without an authorization header. The API defaults to each repository's default branch and filters by author; the generator applies its rolling 365-day window locally to the commit author timestamp in UTC. It avoids the API date parameters because they may use a different commit timestamp. The calendar is a subset of public commit activity, not a copy of the native profile graph; it excludes other repositories and non-commit contribution types. It stores only daily counts, not commit messages, email addresses or repository-level contribution details. See the [REST commits API](https://docs.github.com/en/rest/commits/commits).

The collector validates the exact owner and repository allowlist, the linked author, commit identifiers, timestamps, API host, page size and rolling 365-day range. It deduplicates commit identifiers across the selected repositories before rendering. The three-dimensional, two-dimensional and CSV alternatives use the same daily rows. The 3D bar height remains linear within each snapshot and caps the tallest bar at 75 SVG units; compare exact values using cell descriptions, the 2D calendar or CSV rather than bar height across snapshots. No historical profile CSV is copied into this repository because it may include anonymised private activity.

### Repository activity

`assets/project-repositories.json` is the exact inclusion list: eight public repositories under `DoArtMonteiro`. The collector reads public REST data for those repositories; it does not enumerate the account or infer maturity, ownership, operational acceptance or completed product work from activity. Pull requests retain separate draft, open, merged and closed-without-merge states. Releases and closed milestones remain distinct evidence.

The list should be changed only after checking that a repository is public, relevant to the portfolio, and safe to feature. A changed list is reviewed with its generated output. Atlas Arcade's score-integrity limitation remains a separate product constraint, not a status inferred by the collector.

For each entry, the collector verifies that GitHub still reports a public repository before requesting project details. It counts open issues from the public issues endpoint after excluding records that contain a `pull_request` object; GitHub's REST API represents pull requests through that endpoint as well. Pull request states remain separate as draft, open, merged or closed without merge. The dashboard also records the latest workflow run associated with the repository's default branch. It does not treat a successful run, issue count or commit date as proof of product acceptance. See GitHub's [issues API](https://docs.github.com/en/rest/issues/issues) and [workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs).

The preview passes the run-scoped `GH_TOKEN` only to the project-activity collector, which first verifies each exact allowlisted repository is public. Workflow-run data is queried without authentication. The commit-calendar collector never receives or sends that token. A locally unauthenticated collection can approach GitHub's public REST API limit; the collector fails closed on a limit response and does not retry automatically. See [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

### Campaign results

`assets/campaigns/protected-index.json` starts with no approved runs. The generator emits no result chart when it has no approved campaign records. Each accepted run needs a stable run identifier, source commit, raw-data SHA-256, configuration SHA-256 and public configuration reference, units, observation date and public approval reference. Existing runs are preserved; corrections are appended as a new record linked to the earlier one.

When approved results exist, each metric produces matched light and dark SVG charts plus a Markdown table containing the same non-superseded observations. In a preview bundle, the table links to the provenance file, which records source revisions, configuration and data hashes, and approval references. Each preview derives these assets from the protected registry; unchanged inputs produce identical campaign files. The current workflow does not publish them.

### Product captures

Product images are not refreshed on a timer. A capture is reviewed after a material interface change and pinned to the exact product revision it represents. Generated diagrams are labelled as diagrams and never presented as screenshots.

## Provenance and staleness

Each generated bundle includes a manifest with source descriptions, data-through date, hashes, selected repositories, campaign records, accessibility variants and the 36-hour staleness threshold. The last execution time is available from the Actions run history; it is not added to deterministic files because that would cause changes when source data is unchanged.

The current preview does not publish a public last-success marker. A failed run writes no public assets. Any separately published older image remains unchanged, but its visible data-through date continues to age; it must not be described as current after 36 hours. The GitHub Actions run summary links to the failed run for diagnosis. The current job discards its generated files when the runner exits. Its log provides a concise generation summary, but the workflow does not preserve image files for visual inspection; a successful run alone is not visual acceptance evidence.

## Publication boundary

The current workflow has only `workflow_dispatch`, `contents: read`, a pinned checkout action, and `persist-credentials: false`. It checks out the default branch, runs the unit tests, and creates an unpublished preview under `$RUNNER_TEMP`. It has no `schedule`, upload, Git write or deployment step.

The pull-request validation workflow is also read-only, uses no secrets, and is configured to test on Ubuntu and Windows. The symbolic-link test skips on systems that do not permit creating directory links.

GitHub requires a `workflow_dispatch` workflow to exist on the default branch before it can be manually triggered. Consequently, this local branch cannot demonstrate a real Actions API refresh until the workflow has been reviewed and integrated. That integration and any public asset publication require separate owner approval. The daily schedule remains off until separately approved.

For a future persistent publisher, keep generation and publication separately reviewable. Publish generated assets to a dedicated assets branch, never to the application repositories or profile `main`. Compare generated bytes before committing; make no commit when data is unchanged. A failed generation must leave the published bundle and its source-through date intact. The publishing job must not run on pull-request code and must use only the permissions required to update that branch.

## Accessibility

- Dark and light SVGs share the same validated source rows.
- A 2D calendar, textual summary and CSV accompany the 3D view.
- Project activity graphics have light/dark variants, Markdown text and a JSON snapshot.
- Campaign result charts have light/dark SVGs and a Markdown data table generated from the same approved observations.
- Product and architecture visuals have descriptive alternative text and adjacent explanations.
- Interactive experiences link to the application; GitHub embeds remain static.
