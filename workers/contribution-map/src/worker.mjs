import { LEVELS, parseDailyCsv, summarize } from "../public/calendar-data.mjs";

const API_ROOT = "https://api.github.com/repos/DoArtMonteiro/DoArtMonteiro";
const RAW_ROOT = "https://raw.githubusercontent.com/DoArtMonteiro/DoArtMonteiro/";
const ASSET_REF_API = `${API_ROOT}/git/ref/heads/portfolio-assets`;
const REFRESH_API = `${API_ROOT}/actions/workflows/living-portfolio-refresh.yml/runs?branch=main&per_page=10`;
const MAX_JSON_BYTES = 500_000;
const MAX_CSV_BYTES = 100_000;
const FRESH_FOR_HOURS = 36;
const SOURCES = Object.freeze({
  public: Object.freeze({
    assetPath: "github-calendar/live/calendar-normalized.csv",
    manifestKey: "github-calendar/live/calendar-normalized.csv",
    summaryKey: "github_contribution_summary",
    scope: "github_public_contributions",
    label: "GitHub public contributions",
    plural: "public contributions",
    description: "Daily counts from GitHub's public contribution calendar. The refresh query does not request restricted-contribution fields.",
  }),
  repositories: Object.freeze({
    assetPath: "calendar/live/calendar-normalized.csv",
    manifestKey: "calendar/live/calendar-normalized.csv",
    summaryKey: "calendar_summary",
    scope: "selected_public_repo_commits",
    label: "Selected-repository commits",
    plural: "public commits",
    description: "Distinct commits linked to the approved account across eight selected public repositories' default branches. This is not GitHub's complete contribution calendar.",
  }),
});

class SourceError extends Error {}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "strict-origin-when-cross-origin",
    },
  });
}

async function readBounded(response, limit) {
  if (!response.ok || response.redirected) throw new SourceError("Upstream request was not successful");
  const length = Number(response.headers.get("content-length"));
  if (Number.isFinite(length) && length > limit) throw new SourceError("Upstream response exceeded its limit");
  if (!response.body) throw new SourceError("Upstream response had no body");
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      throw new SourceError("Upstream response exceeded its limit");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

async function fetchBytes(url, limit, fetchImpl, headers = {}) {
  const requestedHost = new URL(url).hostname;
  if (requestedHost !== "raw.githubusercontent.com" && requestedHost !== "api.github.com") {
    throw new SourceError("Upstream host was not approved");
  }
  const response = await fetchImpl(url, {
    headers,
    redirect: "error",
    cf: { cacheTtl: 300, cacheEverything: true },
    signal: AbortSignal.timeout(8_000),
  });
  const finalUrl = response.url;
  if (finalUrl && new URL(finalUrl).hostname !== requestedHost) {
    throw new SourceError("Upstream redirected outside its approved host");
  }
  return readBounded(response, limit);
}

async function fetchJson(url, fetchImpl, headers = {}) {
  const bytes = await fetchBytes(url, MAX_JSON_BYTES, fetchImpl, headers);
  return parseJson(bytes);
}

function parseJson(bytes) {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new SourceError("Upstream JSON was invalid");
  }
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

async function bundleVersion(manifest, manifestBytes) {
  const assets = manifest?.asset_sha256;
  if (!assets || typeof assets !== "object" || Array.isArray(assets)) {
    throw new SourceError("Published asset manifest had no valid asset list");
  }
  const entries = Object.entries(assets);
  if (entries.length < 1 || entries.length > 128) {
    throw new SourceError("Published asset manifest had an invalid asset count");
  }
  for (const [path, digest] of entries) {
    if (typeof path !== "string" || !/^[A-Za-z0-9._/-]+$/.test(path)
        || path.startsWith("/") || path.split("/").some((part) => part === "" || part === "." || part === "..")
        || typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest)) {
      throw new SourceError("Published asset manifest contained an invalid entry");
    }
  }
  const encoder = new TextEncoder();
  const parts = entries.sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    .map(([path, digest]) => encoder.encode(`${path}\0${digest}\0`));
  const size = parts.reduce((sum, part) => sum + part.byteLength, manifestBytes.byteLength);
  const bundleBytes = new Uint8Array(size);
  let offset = 0;
  for (const part of [...parts, manifestBytes]) {
    bundleBytes.set(part, offset);
    offset += part.byteLength;
  }
  return sha256Hex(bundleBytes);
}

async function portfolioAssetsRevision(fetchImpl) {
  const response = await fetchJson(ASSET_REF_API, fetchImpl, {
    accept: "application/vnd.github+json",
    "user-agent": "contribution-map/1.0",
  });
  const revision = response?.object?.sha;
  if (typeof revision !== "string" || !/^[0-9a-f]{40}$/.test(revision)) {
    throw new SourceError("Published asset branch did not return a valid commit");
  }
  return revision;
}

function validDate(value) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function safeRunUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com"
      && url.pathname.startsWith("/DoArtMonteiro/DoArtMonteiro/actions/runs/") ? url.href : null;
  } catch {
    return null;
  }
}

async function refreshStatus(fetchImpl, now, dataThrough) {
  try {
    const runsUrl = new URL(REFRESH_API);
    const response = await fetchImpl(runsUrl, {
      headers: { accept: "application/vnd.github+json", "user-agent": "contribution-map/1.0" },
      redirect: "error",
      cf: { cacheTtl: 300, cacheEverything: true },
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok || response.redirected || (response.url && new URL(response.url).hostname !== "api.github.com")) {
      return { state: "unknown" };
    }
    const bytes = await readBounded(response, MAX_JSON_BYTES);
    const payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!Array.isArray(payload.workflow_runs)) return { state: "unknown" };
    const runs = payload.workflow_runs
      .filter((run) => run && typeof run.status === "string" && typeof run.updated_at === "string"
        && !Number.isNaN(Date.parse(run.updated_at)))
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
    if (!runs.length) return { state: "unknown" };
    const latest = runs[0];
    const latestSuccess = runs.find((run) => run.status === "completed" && run.conclusion === "success");
    const lastSuccessfulAgeHours = latestSuccess
      ? (now - Date.parse(latestSuccess.updated_at)) / 3_600_000 : null;
    const snapshotAgeHours = (now - Date.parse(`${dataThrough}T00:00:00Z`)) / 3_600_000;
    const shared = {
      latestAttemptAt: latest.updated_at,
      latestAttemptUrl: safeRunUrl(latest.html_url),
      lastSuccessfulAt: latestSuccess?.updated_at ?? null,
      snapshotAgeHours,
      snapshotStale: snapshotAgeHours > FRESH_FOR_HOURS,
      lastSuccessfulStale: lastSuccessfulAgeHours !== null && lastSuccessfulAgeHours > FRESH_FOR_HOURS,
    };
    if (latest.status !== "completed") return { ...shared, state: "running" };
    if (latest.conclusion !== "success") return { ...shared, state: "failed" };
    if (!latestSuccess) return { ...shared, state: "unknown" };
    return {
      ...shared,
      state: lastSuccessfulAgeHours > FRESH_FOR_HOURS || snapshotAgeHours > FRESH_FOR_HOURS
        ? "stale" : "current",
    };
  } catch {
    return { state: "unknown" };
  }
}

function assertPointer(pointer) {
  if (!pointer || pointer.schema_version !== 1 || pointer.status !== "live"
      || pointer.source_scope !== "github_public_contributions"
      || pointer.manifest !== "current/manifest.json"
      || typeof pointer.version !== "string" || !/^[0-9a-f]{64}$/.test(pointer.version)
      || !validDate(pointer.data_through) || pointer.stale_after_hours !== FRESH_FOR_HOURS) {
    throw new SourceError("Published source pointer did not match the reviewed format");
  }
}

function assertSummary(manifest, source) {
  const summary = manifest?.[source.summaryKey];
  const digest = manifest?.asset_sha256?.[source.manifestKey];
  if (manifest?.schema_version !== 1 || !summary || summary.scope !== source.scope
      || !validDate(summary.data_through) || !Number.isSafeInteger(summary.dated_days)
      || summary.dated_days !== 365 || !Number.isSafeInteger(summary.total_public_contributions ?? summary.total_public_commits)
      || !Number.isSafeInteger(summary.active_days) || summary.active_days < 0 || summary.active_days > summary.dated_days
      || typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest)) {
    throw new SourceError("Published calendar manifest did not match the reviewed format");
  }
  return { summary, digest };
}

export async function loadCalendar(scope, { fetchImpl = fetch, now = Date.now() } = {}) {
  const source = SOURCES[scope];
  if (!source) throw new SourceError("Unsupported calendar scope");

  const revision = await portfolioAssetsRevision(fetchImpl);
  const immutableRoot = `${RAW_ROOT}${revision}`;
  const pointerBytes = await fetchBytes(`${immutableRoot}/current.json`, MAX_JSON_BYTES, fetchImpl);
  const pointer = parseJson(pointerBytes);
  assertPointer(pointer);
  const dataThroughTimestamp = Date.parse(`${pointer.data_through}T00:00:00Z`);
  if (dataThroughTimestamp > now) throw new SourceError("Published data date is in the future");
  const [manifestBytes, csvBytes, refresh] = await Promise.all([
    fetchBytes(`${immutableRoot}/${pointer.manifest}`, MAX_JSON_BYTES, fetchImpl),
    fetchBytes(`${immutableRoot}/current/${source.assetPath}`, MAX_CSV_BYTES, fetchImpl),
    refreshStatus(fetchImpl, now, pointer.data_through),
  ]);
  const manifest = parseJson(manifestBytes);
  const { summary, digest } = assertSummary(manifest, source);
  if (await bundleVersion(manifest, manifestBytes) !== pointer.version) {
    throw new SourceError("Published pointer did not match its immutable asset manifest");
  }
  const actualDigest = await sha256Hex(csvBytes);
  if (actualDigest !== digest) throw new SourceError("Published calendar failed its source hash check");

  let csv;
  try {
    csv = new TextDecoder("utf-8", { fatal: true }).decode(csvBytes);
  } catch {
    throw new SourceError("Published calendar was not valid UTF-8");
  }
  const rows = parseDailyCsv(csv);
  if (rows.length !== summary.dated_days || rows.at(-1).date !== summary.data_through
      || summary.data_through !== pointer.data_through) {
    throw new SourceError("Calendar rows did not match their verified source summary");
  }
  const totals = summarize(rows);
  const expectedTotal = source.summaryKey === "github_contribution_summary"
    ? summary.total_public_contributions : summary.total_public_commits;
  if (totals.total !== expectedTotal || totals.activeDays !== summary.active_days) {
    throw new SourceError("Calendar totals did not match their verified source summary");
  }

  return Object.freeze({
    scope,
    rows,
    csv,
    summary: totals,
    levels: LEVELS,
    source: Object.freeze({
      label: source.label,
      description: source.description,
      sourceScope: source.scope,
      dataThrough: summary.data_through,
      firstDay: rows[0].date,
      days: rows.length,
      staleAfterHours: FRESH_FOR_HOURS,
      assetVersion: pointer.version,
      publishedRevision: revision,
      assetSha256: actualDigest,
      refresh,
    }),
  });
}

function csvResponse(csv, scope) {
  return new Response(csv, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": `attachment; filename=${scope === "public" ? "github-public-contributions.csv" : "selected-public-repository-commits.csv"}`,
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

export async function handleApiRequest(request, { fetchImpl = fetch, now = Date.now() } = {}) {
  const url = new URL(request.url);
  if (request.method !== "GET") {
    return jsonResponse({ error: "method_not_allowed", message: "Use GET for calendar data." }, 405);
  }
  const scope = url.searchParams.get("scope") ?? "public";
  if (!Object.hasOwn(SOURCES, scope)) {
    return jsonResponse({ error: "invalid_scope", message: "Choose public contributions or selected-repository commits." }, 400);
  }
  if (url.pathname !== "/api/calendar" && url.pathname !== "/api/calendar.csv") {
    return jsonResponse({ error: "not_found", message: "This calendar endpoint does not exist." }, 404);
  }
  try {
    const calendar = await loadCalendar(scope, { fetchImpl, now });
    return url.pathname.endsWith(".csv") ? csvResponse(calendar.csv, calendar.scope) : jsonResponse({
      scope: calendar.scope,
      source: calendar.source,
      summary: calendar.summary,
      levels: calendar.levels,
      rows: calendar.rows,
    });
  } catch {
    return jsonResponse({
      error: "verified_source_unavailable",
      message: "A verified calendar snapshot could not be loaded. Try again shortly; unverified values are not shown.",
    }, 503);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) return handleApiRequest(request);
    return env.ASSETS.fetch(request);
  },
};
