# Living portfolio data and refresh model

## Current state

The live refresh workflow updates public contribution and project activity visuals daily, after relevant source changes on `main`, or by manual dispatch. It generates a complete bundle, validates all listed hashes, then publishes one atomic commit to the dedicated `portfolio-assets` branch. The profile README loads these assets from stable raw-content URLs. The first successful refresh published the bundle with source data through 2 November 2026. The workflow never runs for pull requests and does not deploy an application.

The generator runs with `contents:read`. A separate publisher job receives the validated artifact and uses `contents:write` only to update `portfolio-assets`; it does not make contribution or repository API requests. Neither job grants `read:user`. The latest workflow run is the record of the last successful refresh. The generated image and `current.json` show the source-through date. After 36 hours without a successful refresh, treat the displayed data as stale; the profile's workflow badge and linked Actions history expose failures. A failed generation, validation or publication leaves the last valid asset branch revision unchanged.

## Data sources and inclusion

### Contribution calendar

The calendar counts distinct commits linked to `DoArtMonteiro` in the default branches of the eight repositories in `assets/project-repositories.json`. The preview uses GitHub's public REST list-commits endpoint with its read-only Actions token (`contents:read`), restricted to the approved public-repository list and `api.github.com`; it rejects redirects. The API defaults to each repository's default branch and filters by author; the generator applies its rolling 365-day window locally to the commit author timestamp in UTC. It avoids the API date parameters because they may use a different commit timestamp. The calendar is a subset of public commit activity, not a copy of the native profile graph; it excludes other repositories and non-commit contribution types. It stores only daily counts, not commit messages, email addresses or repository-level contribution details. See the [REST commits API](https://docs.github.com/en/rest/commits/commits).

The collector validates the exact owner and repository allowlist, the linked author, commit identifiers, timestamps, API host, page size and rolling 365-day range. It deduplicates commit identifiers across the selected repositories before rendering. The three-dimensional, two-dimensional and CSV alternatives use the same daily rows. The 3D bar height remains linear within each snapshot and caps the tallest bar at 75 SVG units; compare exact values using cell descriptions, the 2D calendar or CSV rather than bar height across snapshots. No historical profile CSV is copied into this repository because it may include anonymised private activity.

### GitHub contribution calendar

The generator queries GitHub's GraphQL `User.contributionsCollection.contributionCalendar` for the approved account. It requests the daily count, date, weekday and contribution level for an explicit rolling 365-day interval. It does not request restricted-contribution fields. The generation job supplies its GitHub Actions token with `contents:read`; it does not use a personal access token or grant `read:user`. GitHub documents private and internal contributions as available only with the optional `read:user` scope, so this public-calendar path must never be widened to that scope for the portfolio.

This metric is broader than commits in the eight selected repositories: it follows the contribution types and public visibility rules used by GitHub's account calendar. It is stored as its own calendar, manifest and normalized CSV, and the profile displays it as the primary map. The eight-repository REST commit count remains a separate engineering-activity view and is never added to the GraphQL total. See the [GitHub GraphQL users schema](https://docs.github.com/en/graphql/reference/users).

### Repository activity

`assets/project-repositories.json` is the exact inclusion list: eight public repositories under `DoArtMonteiro`. The collector reads public REST data for those repositories; it does not enumerate the account or infer maturity, ownership, operational acceptance or completed product work from activity. Pull requests retain separate draft, open, merged and closed-without-merge states. Releases and closed milestones remain distinct evidence.

The list should be changed only after checking that a repository is public, relevant to the portfolio, and safe to feature. A changed list is reviewed with its generated output. Atlas Arcade's score-integrity limitation remains a separate product constraint, not a status inferred by the collector.

For each entry, the collector verifies that GitHub still reports a public repository before requesting project details. It counts open issues from the public issues endpoint after excluding records that contain a `pull_request` object; GitHub's REST API represents pull requests through that endpoint as well. Pull request states remain separate as draft, open, merged or closed without merge. The dashboard also records the latest workflow run associated with the repository's default branch. It does not treat a successful run, issue count or commit date as proof of product acceptance. See GitHub's [issues API](https://docs.github.com/en/rest/issues/issues) and [workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs).

The generator passes its run-scoped `GH_TOKEN` to the GraphQL public-calendar client and REST collectors. The project collector verifies each exact allowlisted repository is public before collecting project details. The selected-commit collector addresses only the reviewed owner/repository allowlist, sends the token only to GitHub's fixed API host and rejects redirects. The GraphQL query does not request restricted contribution fields, and the generation token has no `read:user` scope. Workflow-run data is queried without authentication. API failures stop generation before publication. See [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

### Campaign results

`assets/campaigns/protected-index.json` starts with no approved runs. The generator emits no result chart when it has no approved campaign records. Each accepted run needs a stable run identifier, source commit, raw-data SHA-256, configuration SHA-256 and public configuration reference, units, observation date and public approval reference. Existing runs are preserved; corrections are appended as a new record linked to the earlier one.

When approved results exist, each metric produces matched light and dark SVG charts plus a Markdown table containing the same non-superseded observations. In a preview bundle, the table links to the provenance file, which records source revisions, configuration and data hashes, and approval references. Each preview derives these assets from the protected registry; unchanged inputs produce identical campaign files. The current workflow does not publish them.

### Product captures

Product images are not refreshed on a timer. A capture is reviewed after a material interface change and pinned to the exact product revision it represents. Generated diagrams are labelled as diagrams and never presented as screenshots.

## Provenance and staleness

Each generated bundle includes a manifest with source descriptions, data-through dates, hashes, selected repositories, campaign records, accessibility variants and the 36-hour staleness threshold. The last successful execution time is available from the Actions run history; it is not added to generated files so identical source data does not create an unnecessary asset commit.

The profile links to the public `current.json` pointer and complete manifest. A failed run leaves the previous assets in place, their data-through date continues to age, and the workflow badge reports the latest run status. The refresh workflow also uploads the validated bundle as a seven-day recovery artifact. A successful run updates the pointer only after the entire bundle has passed hash and public-data checks.

## Publication boundary

The diagnostic workflow for PR #4 remains read-only and checks out that PR's exact head SHA. It writes only a seven-day artifact under `$RUNNER_TEMP` and has no publication permission.

The pull-request validation workflow is also read-only, uses no secrets, and is configured to test on Ubuntu and Windows. The symbolic-link test skips on systems that do not permit creating directory links.

The live refresh workflow runs on the default branch at 05:17 UTC each day, after relevant source changes on `main`, or by manual dispatch. Generation and publication use separate jobs: only the publisher receives `contents:write`, and it receives the validated artifact rather than running the data collectors. Publication targets only `portfolio-assets`, creates a single tree and commit, and updates the branch reference without force. If the generated manifest and pointer are byte-for-byte unchanged, no commit is created. The workflow has no pull-request trigger, schedule changes do not affect application deployment, and no production application is deployed.

Generated public assets live on `portfolio-assets`, never in application repositories or the profile source branch. A failed generation or validation leaves the last published bundle and its source-through date intact. The publishing job must not run on pull-request code and must use only the permission required to update that branch.

## Accessibility

- Dark and light SVGs share the same validated source rows.
- The isometric activity graphic has finite, eight-frame dark and light GIF variants. It does not loop. A static SVG is selected for reduced-motion preferences where GitHub supports the media query; GitHub's own animated-image setting can also disable GIF playback.
- A 2D calendar, textual summary and CSV accompany the 3D view.
- Project activity graphics have desktop and narrow-screen layouts in light and dark themes, plus a linked Markdown report and JSON snapshot.
- Campaign result charts have light/dark SVGs and a Markdown data table generated from the same approved observations.
- Product and architecture visuals have descriptive alternative text and adjacent explanations.
- Automatic updates describe when source data is refreshed, not whether a visual moves. GIF animation only presents the same saved data; it is not interactivity. GitHub README embeds remain static documents, and interactive experiences link to the real application.
