import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { handleApiRequest, loadCalendar } from "../src/worker.mjs";

const NOW = Date.parse("2026-11-02T10:01:00Z");
const ASSET_COMMIT = "b".repeat(40);
const ASSET_ROOT = `https://raw.githubusercontent.com/DoArtMonteiro/DoArtMonteiro/${ASSET_COMMIT}`;

function csvThrough(dataThrough, activeCount) {
  const last = Date.parse(`${dataThrough}T00:00:00Z`);
  const first = last - (365 - 1) * 86_400_000;
  const rows = Array.from({ length: 365 }, (_, index) => {
    const date = new Date(first + index * 86_400_000).toISOString().slice(0, 10);
    return `${date},${index === 1 ? activeCount : 0}`;
  });
  return `date,contributions\n${rows.join("\n")}\n`;
}

function bundleDigest(assets, manifestBytes) {
  const hash = createHash("sha256");
  for (const [path, digest] of Object.entries(assets).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)) {
    hash.update(path).update("\0").update(digest).update("\0");
  }
  return hash.update(manifestBytes).digest("hex");
}

function createFixture({ dataThrough = "2026-11-02", runState = "success", pointerVersion = null } = {}) {
  const csv = csvThrough(dataThrough, 2);
  const repositoryCsv = csvThrough(dataThrough, 5);
  const assets = {
    "github-calendar/live/calendar-normalized.csv": createHash("sha256").update(csv).digest("hex"),
    "calendar/live/calendar-normalized.csv": createHash("sha256").update(repositoryCsv).digest("hex"),
  };
  const manifest = {
    schema_version: 1,
    asset_sha256: assets,
    github_contribution_summary: {
      scope: "github_public_contributions",
      data_through: dataThrough,
      dated_days: 365,
      active_days: 1,
      total_public_contributions: 2,
    },
    calendar_summary: {
      scope: "selected_public_repo_commits",
      data_through: dataThrough,
      dated_days: 365,
      active_days: 1,
      total_public_commits: 5,
    },
  };
  const manifestBytes = Buffer.from(JSON.stringify(manifest));
  const version = bundleDigest(assets, manifestBytes);
  const pointer = {
    schema_version: 1,
    status: "live",
    version: pointerVersion ?? version,
    manifest: "current/manifest.json",
    data_through: dataThrough,
    source_scope: "github_public_contributions",
    stale_after_hours: 36,
  };
  return { csv, repositoryCsv, manifestBytes, pointer, runState };
}

function stubFetch(fixture, seenUrls = []) {
  return async (input) => {
    const url = String(input);
    seenUrls.push(url);
    if (url.endsWith("/git/ref/heads/portfolio-assets")) {
      return Response.json({ object: { sha: ASSET_COMMIT } });
    }
    if (url === `${ASSET_ROOT}/current.json`) return Response.json(fixture.pointer);
    if (url === `${ASSET_ROOT}/current/manifest.json`) {
      return new Response(fixture.manifestBytes, { headers: { "content-type": "application/json" } });
    }
    if (url === `${ASSET_ROOT}/current/github-calendar/live/calendar-normalized.csv`) {
      return new Response(fixture.csv, { headers: { "content-type": "text/csv" } });
    }
    if (url === `${ASSET_ROOT}/current/calendar/live/calendar-normalized.csv`) {
      return new Response(fixture.repositoryCsv, { headers: { "content-type": "text/csv" } });
    }
    if (url.includes("api.github.com/repos/DoArtMonteiro/DoArtMonteiro/actions/workflows/")) {
      const status = fixture.runState === "running" ? "in_progress" : "completed";
      const conclusion = fixture.runState === "running" ? null : fixture.runState;
      return Response.json({ workflow_runs: [{
        status,
        conclusion,
        updated_at: "2026-11-02T10:00:00Z",
        html_url: "https://github.com/DoArtMonteiro/DoArtMonteiro/actions/runs/123",
      }] });
    }
    throw new Error("Unexpected upstream request");
  };
}

test("returns a verified public calendar pinned to one immutable asset revision", async () => {
  const seenUrls = [];
  const fixture = createFixture();
  const result = await loadCalendar("public", { fetchImpl: stubFetch(fixture, seenUrls), now: NOW });
  assert.equal(result.summary.total, 2);
  assert.equal(result.rows.length, 365);
  assert.equal(result.source.assetSha256, createHash("sha256").update(fixture.csv).digest("hex"));
  assert.equal(result.source.publishedRevision, ASSET_COMMIT);
  assert.equal(result.source.refresh.state, "current");
  assert.ok(seenUrls.filter((url) => url.startsWith("https://raw.githubusercontent.com/")).every((url) => url.startsWith(`${ASSET_ROOT}/`)));
});

test("labels a failed refresh while retaining only the last validated snapshot", async () => {
  const fixture = createFixture({ runState: "failure" });
  const result = await loadCalendar("public", { fetchImpl: stubFetch(fixture), now: NOW });
  assert.equal(result.source.refresh.state, "failed");
  assert.equal(result.source.dataThrough, "2026-11-02");
});

test("labels an old source date stale even after a recent successful workflow run", async () => {
  const fixture = createFixture({ dataThrough: "2026-10-01" });
  const result = await loadCalendar("public", { fetchImpl: stubFetch(fixture), now: NOW });
  assert.equal(result.source.refresh.state, "stale");
});

test("labels a running refresh without presenting it as a completed update", async () => {
  const fixture = createFixture({ runState: "running" });
  const result = await loadCalendar("public", { fetchImpl: stubFetch(fixture), now: NOW });
  assert.equal(result.source.refresh.state, "running");
});

test("keeps selected-repository commits separate from GitHub public contributions", async () => {
  const fixture = createFixture();
  const result = await loadCalendar("repositories", { fetchImpl: stubFetch(fixture), now: NOW });
  assert.equal(result.source.sourceScope, "selected_public_repo_commits");
  assert.equal(result.summary.total, 5);
  assert.equal(result.summary.activeDays, 1);
});

test("rejects a pointer whose bundle version does not match its immutable manifest", async () => {
  const fixture = createFixture({ pointerVersion: "0".repeat(64) });
  await assert.rejects(loadCalendar("public", { fetchImpl: stubFetch(fixture), now: NOW }));
});

test("rejects a CSV whose SHA-256 differs from the published manifest", async () => {
  const fixture = createFixture();
  const corruptedFixture = { ...fixture, csv: fixture.csv.replace("2025-11-04,2", "2025-11-04,3") };
  const response = await handleApiRequest(new Request("https://example.test/api/calendar"), {
    fetchImpl: stubFetch(corruptedFixture), now: NOW,
  });
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "verified_source_unavailable");
});

test("API exposes only the reviewed scopes and blocks other methods", async () => {
  const invalidScope = await handleApiRequest(new Request("https://example.test/api/calendar?scope=private"));
  assert.equal(invalidScope.status, 400);
  const method = await handleApiRequest(new Request("https://example.test/api/calendar", { method: "POST" }));
  assert.equal(method.status, 405);
});

test("CSV export returns only the verified daily public rows", async () => {
  const fixture = createFixture();
  const response = await handleApiRequest(new Request("https://example.test/api/calendar.csv?scope=public"), {
    fetchImpl: stubFetch(fixture), now: NOW,
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /text\/csv/);
  assert.equal(await response.text(), fixture.csv);
});

test("returns no data when the published source cannot be verified", async () => {
  const response = await handleApiRequest(new Request("https://example.test/api/calendar"), {
    fetchImpl: async () => new Response("upstream unavailable", { status: 503 }), now: NOW,
  });
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "verified_source_unavailable");
});
