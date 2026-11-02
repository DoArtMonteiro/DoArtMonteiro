import test from "node:test";
import assert from "node:assert/strict";
import { byMonth, byWeek, dateFromOffset, levelForCount, parseDailyCsv, selectedDateFor, summarize, sundayFor } from "../public/calendar-data.mjs";

test("parses a consecutive daily CSV and preserves zero days", () => {
  assert.deepEqual(parseDailyCsv("date,contributions\n2026-01-01,0\n2026-01-02,4\n"), [
    { date: "2026-01-01", count: 0 },
    { date: "2026-01-02", count: 4 },
  ]);
});

test("rejects duplicate, skipped, malformed and negative daily rows", () => {
  for (const csv of [
    "date,contributions\n2026-01-01,0\n2026-01-01,1\n",
    "date,contributions\n2026-01-01,0\n2026-01-03,1\n",
    "date,contributions\n2026-02-30,0\n",
    "date,contributions\n2026-01-01,-1\n",
    "date,count\n2026-01-01,1\n",
  ]) assert.throws(() => parseDailyCsv(csv));
});

test("uses the same five contribution bands as the source renderer", () => {
  assert.deepEqual([0, 1, 5, 6, 12, 13, 24, 25, 100].map(levelForCount), [0, 1, 1, 2, 2, 3, 3, 4, 4]);
});

test("summarises totals and activity without implying a quality score", () => {
  const result = summarize(parseDailyCsv("date,contributions\n2026-01-01,0\n2026-01-02,4\n2026-01-03,2\n"));
  assert.deepEqual(result, { total: 6, activeDays: 2, days: 3, peakDay: { date: "2026-01-02", count: 4 } });
});

test("keeps the selected date inside the visible range", () => {
  const rows = parseDailyCsv("date,contributions\n2026-01-01,0\n2026-01-02,4\n2026-01-03,2\n");
  assert.equal(selectedDateFor(rows, "2026-01-02"), "2026-01-02");
  assert.equal(selectedDateFor(rows, "2025-12-31"), "2026-01-03");
  assert.equal(selectedDateFor([], "2026-01-02"), "");
});

test("groups dates by Sunday-based week and month", () => {
  const rows = parseDailyCsv("date,contributions\n2026-01-31,1\n2026-02-01,2\n2026-02-02,3\n");
  assert.deepEqual(Array.from(byWeek(rows).keys()), ["2026-01-25", "2026-02-01"]);
  assert.deepEqual(Array.from(byMonth(rows).keys()), ["2026-01", "2026-02"]);
  assert.equal(sundayFor("2026-02-04"), "2026-02-01");
  assert.equal(dateFromOffset("2026-02-01", 6), "2026-02-07");
});
