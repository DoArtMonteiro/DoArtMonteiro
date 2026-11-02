export const LEVELS = Object.freeze([
  Object.freeze({ level: 0, label: "0", minimum: 0, maximum: 0 }),
  Object.freeze({ level: 1, label: "1–5", minimum: 1, maximum: 5 }),
  Object.freeze({ level: 2, label: "6–12", minimum: 6, maximum: 12 }),
  Object.freeze({ level: 3, label: "13–24", minimum: 13, maximum: 24 }),
  Object.freeze({ level: 4, label: "25+", minimum: 25, maximum: null }),
]);

function parseDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new TypeError("Calendar dates must use YYYY-MM-DD format");
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new TypeError("Calendar contains an invalid date");
  }
  return parsed;
}

export function parseDailyCsv(csv) {
  if (typeof csv !== "string" || csv.length > 100_000) {
    throw new TypeError("Calendar CSV is missing or too large");
  }
  const normalised = csv.replace(/\r\n/g, "\n");
  const lines = normalised.endsWith("\n") ? normalised.slice(0, -1).split("\n") : normalised.split("\n");
  if (lines[0] !== "date,contributions" || lines.length < 2 || lines.length > 367) {
    throw new TypeError("Calendar CSV has an unsupported shape");
  }

  const rows = [];
  let previousDay = null;
  for (const line of lines.slice(1)) {
    const fields = line.split(",");
    if (fields.length !== 2) throw new TypeError("Calendar CSV contains an invalid row");
    const day = parseDate(fields[0]);
    const count = Number(fields[1]);
    if (!/^\d+$/.test(fields[1]) || !Number.isSafeInteger(count) || count < 0) {
      throw new TypeError("Calendar CSV contains an invalid daily count");
    }
    if (previousDay && day.getTime() - previousDay.getTime() !== 86_400_000) {
      throw new TypeError("Calendar dates must be unique and consecutive");
    }
    rows.push(Object.freeze({ date: fields[0], count }));
    previousDay = day;
  }
  return Object.freeze(rows);
}

export function levelForCount(count) {
  if (!Number.isSafeInteger(count) || count < 0) throw new TypeError("Count must be a non-negative integer");
  return LEVELS.find(({ level, minimum, maximum }) => level === 0
    ? count === 0
    : count >= minimum && (maximum === null || count <= maximum)).level;
}

export function summarize(rows) {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  return Object.freeze({
    total,
    activeDays: rows.reduce((sum, row) => sum + Number(row.count > 0), 0),
    days: rows.length,
    peakDay: rows.reduce((peak, row) => row.count > peak.count ? row : peak, { date: "", count: -1 }),
  });
}

export function selectedDateFor(rows, selectedDate) {
  return rows.some((row) => row.date === selectedDate) ? selectedDate : rows.at(-1)?.date ?? "";
}

export function sundayFor(dateString) {
  const day = parseDate(dateString);
  day.setUTCDate(day.getUTCDate() - day.getUTCDay());
  return day.toISOString().slice(0, 10);
}

export function dateFromOffset(dateString, offset) {
  if (!Number.isInteger(offset)) throw new TypeError("Date offset must be an integer");
  const day = parseDate(dateString);
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

export function byMonth(rows) {
  const months = new Map();
  for (const row of rows) {
    const key = row.date.slice(0, 7);
    months.set(key, [...(months.get(key) ?? []), row]);
  }
  return months;
}

export function byWeek(rows) {
  const weeks = new Map();
  for (const row of rows) {
    const key = sundayFor(row.date);
    weeks.set(key, [...(weeks.get(key) ?? []), row]);
  }
  return weeks;
}
