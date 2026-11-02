# Contribution map Worker

This Cloudflare Worker serves the profile's interactive public contribution calendar. Its proposed public address is `https://contribution-map.duartemonteiro.workers.dev`; the address is not active until an owner-approved deployment succeeds.

## What it shows

- GitHub's public contribution calendar, with year, month, week and day views.
- A separate metric for distinct commits in the eight selected public repositories.
- Exact daily values, activity totals, intensity labels and a download link.
- Refresh state, source-through date, source digest and a link to the GitHub Actions run history.

The two activity sources remain separate. Aggregate counts do not indicate work quality, time spent or business impact.

## Data path and provenance

The Worker reads only public files from `DoArtMonteiro/DoArtMonteiro` on the `portfolio-assets` branch. It resolves the branch to one commit SHA, then reads the pointer, manifest and CSV from that same immutable revision. It checks that the pointer's bundle digest matches the manifest, then checks the expected activity scope, 365 dated rows, summary total and CSV SHA-256. This prevents independently cached mutable paths from mixing two published snapshots. It also reads the public Actions history to report refresh status. No token, secret, account permission or private contribution field is used by the Worker.

The scheduled GitHub workflow refreshes the source snapshot daily at 05:17 UTC. The Worker resolves the current published revision on each request; Cloudflare caches upstream GitHub responses for up to five minutes. The Worker API responses are not cached by the browser. The page does not call GitHub directly from the browser.

## Freshness and failure behaviour

The page shows the source-through date and checks the latest public refresh run. It labels the data current only when both the successful refresh and source date are within 36 hours. Since the source records a date rather than a collection time, the Worker measures the source date from 00:00 UTC, which errs on the side of marking data stale. A failed or running refresh labels the displayed data as the last valid snapshot and also reports a stale source date when applicable. If the refresh status cannot be checked, the page says currentness is unknown. If the pointer, manifest, hash or calendar data cannot be verified, the Worker returns an error and the page does not show unverified data.

The source publisher preserves its last complete valid snapshot when a refresh fails. To recover, inspect the linked Actions run, fix the source workflow or validator, and dispatch a new successful refresh. No Worker-side fallback changes old values into current ones.

## Local verification

Run the dependency-free checks with:

```powershell
node --test workers/contribution-map/tests/*.test.mjs
```

Deploy only after owner approval with Wrangler from this directory. A successful local test does not prove that the proposed `workers.dev` route exists or that the Cloudflare account can deploy it.
