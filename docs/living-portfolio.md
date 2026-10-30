# Living portfolio data and refresh model

## Current state

The repository contains a local generator, a read-only GitHub Actions diagnostic preview and a dated public-data snapshot for review. PR #4's preview stores output in temporary runner storage and uploads it as a seven-day diagnostic artifact. It does not create commits, publish assets or deploy an application. There is no schedule. The committed snapshot is not a live feed; it becomes stale after 36 hours. GitHub's native contribution graph remains available from the profile link.

## Data sources and inclusion

### Contribution calendar

The calendar counts distinct commits linked to `DoArtMonteiro` in the default branches of the eight repositories in `assets/project-repositories.json`. It uses GitHub's public REST list-commits endpoint without an authorization header. The API defaults to each repository's default branch and filters by author; the generator applies its rolling 365-day window locally to the commit author timestamp in UTC. It avoids the API date parameters because they may use a different commit timestamp. The calendar is a subset of public commit activity, not a copy of the native profile graph; it excludes other repositories and non-commit contribution types. It stores only daily counts, not commit messages, email addresses or repository-level contribution details. See the [REST commits API](https://docs.github.com/en/rest/commits/commits).

The collector validates the exact owner and repository allowlist, the linked author, commit identifiers, timestamps, API host, page size and rolling 365-day range. It deduplicates commit identifiers across the selected repositories before rendering. The three-dimensional, two-dimensional and CSV alternatives use the same daily rows. The 3D bar height remains linear within each snapshot and caps the tallest bar at 75 SVG units; compare exact values using cell descriptions, the 2D calendar or CSV rather than bar height across snapshots. No historical profile CSV is copied into this repository because it may include anonymised private activity.

### GitHub contribution calendar

The preview generator also queries GitHub's GraphQL `User.contributionsCollection.contributionCalendar` for the approved account. It requests the daily count, date, weekday and contribution level for an explicit rolling 365-day interval. It does not request restricted-contribution fields. The preview workflow supplies its read-only `GITHUB_TOKEN` with `contents:read`; it does not use a personal access token or grant `read:user`. GitHub documents private and internal contributions as available only with the optional `read:user` scope, so this public-calendar path must never be widened to that scope for the portfolio.

This metric is broader than commits in the eight selected repositories: it follows the contribution types and public visibility rules used by GitHub's account calendar. It is stored as a separate calendar, manifest and normalized CSV in the diagnostic bundle. It does not overwrite or get added to the eight-repository commit count. A successful API response and renderer test do not, by themselves, establish that the GitHub Actions installation token can query this user field; the exact PR workflow run must confirm that compatibility before this view can replace or appear beside a committed profile image. The user's native [GitHub contribution calendar](https://github.com/DoArtMonteiro?tab=overview#js-contribution-activity) remains available meanwhile. See the [GitHub GraphQL users schema](https://docs.github.com/en/graphql/reference/users).

### Repository activity

`assets/project-repositories.json` is the exact inclusion list: eight public repositories under `DoArtMonteiro`. The collector reads public REST data for those repositories; it does not enumerate the account or infer maturity, ownership, operational acceptance or completed product work from activity. Pull requests retain separate draft, open, merged and closed-without-merge states. Releases and closed milestones remain distinct evidence.

The list should be changed only after checking that a repository is public, relevant to the portfolio, and safe to feature. A changed list is reviewed with its generated output. Atlas Arcade's score-integrity limitation remains a separate product constraint, not a status inferred by the collector.

For each entry, the collector verifies that GitHub still reports a public repository before requesting project details. It counts open issues from the public issues endpoint after excluding records that contain a `pull_request` object; GitHub's REST API represents pull requests through that endpoint as well. Pull request states remain separate as draft, open, merged or closed without merge. The dashboard also records the latest workflow run associated with the repository's default branch. It does not treat a successful run, issue count or commit date as proof of product acceptance. See GitHub's [issues API](https://docs.github.com/en/rest/issues/issues) and [workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs).

The preview passes the run-scoped read-only `GH_TOKEN` to the GraphQL public-calendar client and project-activity collector. The project collector first verifies each exact allowlisted repository is public. The selected-repository commit collector never receives or sends a token. Workflow-run data is queried without authentication. API failures stop the preview before the current pointer changes. See [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

### Campaign results

`assets/campaigns/protected-index.json` starts with no approved runs. The generator emits no result chart when it has no approved campaign records. Each accepted run needs a stable run identifier, source commit, raw-data SHA-256, configuration SHA-256 and public configuration reference, units, observation date and public approval reference. Existing runs are preserved; corrections are appended as a new record linked to the earlier one.

When approved results exist, each metric produces matched light and dark SVG charts plus a Markdown table containing the same non-superseded observations. In a preview bundle, the table links to the provenance file, which records source revisions, configuration and data hashes, and approval references. Each preview derives these assets from the protected registry; unchanged inputs produce identical campaign files. The current workflow does not publish them.

### Product captures

Product images are not refreshed on a timer. A capture is reviewed after a material interface change and pinned to the exact product revision it represents. Generated diagrams are labelled as diagrams and never presented as screenshots.

## Provenance and staleness

Each generated bundle includes a manifest with source descriptions, data-through dates, hashes, selected repositories, campaign records, accessibility variants and the 36-hour staleness threshold. The last execution time is available from the Actions run history; it is not added to deterministic files because that would cause changes when source data is unchanged.

The current preview does not publish a public last-success marker. A failed run writes no profile assets. Any separately published older image remains unchanged, but its visible data-through date continues to age; it must not be described as current after 36 hours. The run summary links to the failed run for diagnosis. The PR #4 diagnostic workflow uploads a complete preview artifact for seven days so the generated images and manifests can be retrieved from the workflow run. The artifact is not committed to the README or a persistent asset store. A successful run alone is not visual acceptance evidence.

## Publication boundary

The diagnostic workflow runs on PR #4 opened, reopened or synchronized revisions and checks out that PR's exact head SHA. Manual dispatch remains limited to the default branch. It has `contents: read`, pinned checkout and artifact-upload actions, `persist-credentials: false`, and writes only a seven-day diagnostic artifact under `$RUNNER_TEMP`. It has no `schedule`, Git write, persistent publication or deployment step.

The pull-request validation workflow is also read-only, uses no secrets, and is configured to test on Ubuntu and Windows. The symbolic-link test skips on systems that do not permit creating directory links.

GitHub requires a `workflow_dispatch` workflow to exist on the default branch before it can be manually triggered. The PR-triggered path is intended to validate the GraphQL API call and generated assets against the exact proposed revision. It cannot run until the owner approves pushing that revision. Any persistent public asset publication requires separate owner approval. The daily schedule remains off.

For a future persistent publisher, keep generation and publication separately reviewable. Publish generated assets to a dedicated assets branch, never to the application repositories or profile `main`. Compare generated bytes before committing; make no commit when data is unchanged. A failed generation must leave the published bundle and its source-through date intact. The publishing job must not run on pull-request code and must use only the permissions required to update that branch.

## Accessibility

- Dark and light SVGs share the same validated source rows.
- The isometric activity graphic has finite, eight-frame dark and light GIF variants. It does not loop. A static SVG is selected for reduced-motion preferences where GitHub supports the media query; GitHub's own animated-image setting can also disable GIF playback.
- A 2D calendar, textual summary and CSV accompany the 3D view.
- Project activity graphics have desktop and narrow-screen layouts in light and dark themes, plus a linked Markdown report and JSON snapshot.
- Campaign result charts have light/dark SVGs and a Markdown data table generated from the same approved observations.
- Product and architecture visuals have descriptive alternative text and adjacent explanations.
- Automatic updates describe when source data is refreshed, not whether a visual moves. GIF animation only presents the same saved data; it is not interactivity. GitHub README embeds remain static documents, and interactive experiences link to the real application.
