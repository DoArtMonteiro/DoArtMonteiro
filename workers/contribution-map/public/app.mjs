import { byMonth, byWeek, dateFromOffset, levelForCount, selectedDateFor, summarize, sundayFor } from "/calendar-data.mjs";

const elements = Object.freeze({
  scope: document.querySelector("#scope-select"),
  month: document.querySelector("#month-select"),
  week: document.querySelector("#week-select"),
  monthControl: document.querySelector("#month-control"),
  weekControl: document.querySelector("#week-control"),
  snapshot: document.querySelector("#snapshot-summary"),
  refreshStatus: document.querySelector("#refresh-status"),
  refreshMessage: document.querySelector("#refresh-message"),
  refreshButton: document.querySelector("#refresh-button"),
  scopeDescription: document.querySelector("#scope-description"),
  viewSummary: document.querySelector("#view-summary"),
  calendarMessage: document.querySelector("#calendar-message"),
  yearScroll: document.querySelector("#year-scroll"),
  yearCalendar: document.querySelector("#year-calendar"),
  monthCalendar: document.querySelector("#month-calendar"),
  weekCalendar: document.querySelector("#week-calendar"),
  dayDate: document.querySelector("#day-date"),
  dayCount: document.querySelector("#day-count"),
  csvLink: document.querySelector("#csv-link"),
  jsonLink: document.querySelector("#json-link"),
});

const weekdayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const monthFormatter = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const dateFormatter = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const shortDateFormatter = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const weekDateFormatter = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const countFormatter = new Intl.NumberFormat("en-GB");

let model = null;
let view = "month";
let monthKey = "";
let weekKey = "";
let selectedDate = "";
let loading = false;

function utcDate(value) {
  return new Date(`${value}T00:00:00Z`);
}

function formatDate(value, formatter = dateFormatter) {
  return formatter.format(utcDate(value));
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function metricName(scope, count, plural = false) {
  if (scope === "repositories") return count === 1 ? "public commit" : "public commits";
  return count === 1 ? "public contribution" : "public contributions";
}

function setRefreshState(state, message, { runUrl = null, severity = "normal" } = {}) {
  elements.refreshStatus.dataset.state = state;
  elements.refreshStatus.dataset.severity = severity;
  elements.refreshMessage.textContent = message;
  for (const child of Array.from(elements.refreshStatus.querySelectorAll("a"))) child.remove();
  if (runUrl) {
    const link = makeElement("a", "status-link", "View failed run");
    link.href = runUrl;
    link.rel = "noreferrer";
    elements.refreshStatus.append(link);
  }
}

function describeRefresh(refresh, source) {
  const through = formatDate(source.dataThrough);
  const staleNote = refresh.snapshotStale
    ? " The source date is more than 36 hours old."
    : refresh.lastSuccessfulStale ? " The last successful refresh is more than 36 hours old." : "";
  const lastSuccess = refresh.lastSuccessfulAt ? new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
  }).format(new Date(refresh.lastSuccessfulAt)) + " UTC" : null;
  if (refresh.state === "current") {
    setRefreshState("current", `Last successful refresh: ${lastSuccess}. Source data through ${through}.`);
  } else if (refresh.state === "stale") {
    setRefreshState("stale", `This snapshot is stale. The last successful refresh was ${lastSuccess}; source data runs through ${through}.`, { severity: "warning" });
  } else if (refresh.state === "failed") {
    setRefreshState("failed", `The latest refresh failed. Showing the last valid snapshot through ${through}, not a current update.${staleNote}`, { runUrl: refresh.latestAttemptUrl, severity: "warning" });
  } else if (refresh.state === "running") {
    setRefreshState("running", `A refresh is in progress. This is the last validated snapshot through ${through}.${staleNote}`, { severity: "warning" });
  } else {
    setRefreshState("unknown", `Refresh time could not be confirmed. This snapshot is through ${through}; its currentness is unknown.${staleNote}`, { severity: "warning" });
  }
}

function setView(nextView) {
  view = nextView;
  for (const button of document.querySelectorAll("[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  elements.monthControl.hidden = view !== "month";
  elements.weekControl.hidden = view !== "week";
  elements.yearScroll.hidden = view !== "year";
  elements.monthCalendar.hidden = view !== "month";
  elements.weekCalendar.hidden = view !== "week";
  if (model) renderCalendar();
}

function setMetricLinks(scope) {
  const value = encodeURIComponent(scope);
  elements.csvLink.href = `/api/calendar.csv?scope=${value}`;
  elements.jsonLink.href = `/api/calendar?scope=${value}`;
}

function updateMonthChoices() {
  const months = byMonth(model.rows);
  elements.month.replaceChildren();
  for (const key of months.keys()) {
    const option = makeElement("option", "", monthFormatter.format(utcDate(`${key}-01`)));
    option.value = key;
    elements.month.append(option);
  }
  if (!months.has(monthKey)) monthKey = Array.from(months.keys()).at(-1) ?? "";
  elements.month.value = monthKey;
}

function updateWeekChoices() {
  const weeks = byWeek(model.rows);
  elements.week.replaceChildren();
  for (const [start, rows] of weeks) {
    const end = rows.at(-1).date;
    const option = makeElement("option", "", `${formatDate(start, weekDateFormatter)}–${formatDate(end, weekDateFormatter)} · ${rows.length} days`);
    option.value = start;
    elements.week.append(option);
  }
  if (!weeks.has(weekKey)) weekKey = Array.from(weeks.keys()).at(-1) ?? "";
  elements.week.value = weekKey;
}

function dayButton(row, scope, compact = true, dayNumber = null) {
  const date = utcDate(row.date);
  const weekday = date.getUTCDay();
  const label = `${formatDate(row.date)}: ${countFormatter.format(row.count)} ${metricName(scope, row.count)}`;
  const button = makeElement("button", `day-cell level-${levelForCount(row.count)}${row.date === selectedDate ? " is-selected" : ""}`);
  button.type = "button";
  button.dataset.date = row.date;
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", String(row.date === selectedDate));
  button.title = label;
  if (dayNumber !== null) button.append(makeElement("span", "month-day-number", String(dayNumber)));
  if (!compact) {
    button.append(makeElement("span", "week-day-name", weekdayNames[weekday].slice(0, 3)));
    button.append(makeElement("span", "week-day-date", formatDate(row.date, shortDateFormatter)));
    button.append(makeElement("span", "week-day-count", countFormatter.format(row.count)));
    button.append(makeElement("span", `week-day-intensity level-${levelForCount(row.count)}`, ""));
  }
  button.addEventListener("click", () => {
    selectedDate = row.date;
    renderCalendar();
    const activeCalendar = view === "year" ? elements.yearCalendar
      : view === "month" ? elements.monthCalendar : elements.weekCalendar;
    activeCalendar.querySelector(`[data-date="${row.date}"]`)?.focus({ preventScroll: true });
  });
  return button;
}

function showSelectedDate(rows) {
  const selected = rows.find((row) => row.date === selectedDate) ?? rows.at(-1);
  if (!selected) {
    elements.dayDate.textContent = "No dated activity is available in this range.";
    elements.dayCount.textContent = "";
    return;
  }
  selectedDate = selected.date;
  elements.dayDate.textContent = formatDate(selected.date);
  elements.dayCount.textContent = `${countFormatter.format(selected.count)} ${metricName(model.scope, selected.count)}`;
}

function addTableCaption(table, text) {
  const caption = makeElement("caption", "visually-hidden", text);
  table.append(caption);
}

function renderYear() {
  const rows = model.rows;
  const byDate = new Map(rows.map((row) => [row.date, row]));
  const firstSunday = sundayFor(rows[0].date);
  const lastSunday = sundayFor(rows.at(-1).date);
  const weekCount = Math.round((utcDate(lastSunday) - utcDate(firstSunday)) / 604_800_000) + 1;
  const table = makeElement("table", "calendar-table year-table");
  addTableCaption(table, `Daily ${metricName(model.scope, 2)} over the latest ${rows.length} days. Each column is a Sunday-to-Saturday week.`);
  const head = document.createElement("thead");
  const monthRow = document.createElement("tr");
  monthRow.append(makeElement("th", "week-axis", ""));
  const firstDateByMonth = new Map();
  for (const row of rows) {
    const key = row.date.slice(0, 7);
    if (!firstDateByMonth.has(key)) firstDateByMonth.set(key, row.date);
  }
  const monthByWeek = new Map(Array.from(firstDateByMonth.values(), (date) => [sundayFor(date), date]));
  for (let week = 0; week < weekCount; week += 1) {
    const start = dateFromOffset(firstSunday, week * 7);
    const monthDay = monthByWeek.get(start);
    const label = monthDay ? monthFormatter.format(utcDate(monthDay)) : "";
    const cell = makeElement("th", `week-column${label ? " month-start" : ""}`, "");
    cell.scope = "col";
    if (label) cell.append(makeElement("span", "month-label", label));
    monthRow.append(cell);
  }
  head.append(monthRow);
  table.append(head);

  const body = document.createElement("tbody");
  for (let weekday = 0; weekday < 7; weekday += 1) {
    const row = document.createElement("tr");
    const label = makeElement("th", "weekday-label", weekdayNames[weekday].slice(0, 3));
    label.scope = "row";
    row.append(label);
    for (let week = 0; week < weekCount; week += 1) {
      const date = dateFromOffset(firstSunday, week * 7 + weekday);
      const cell = document.createElement("td");
      const value = byDate.get(date);
      if (value) cell.append(dayButton(value, model.scope));
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  elements.yearCalendar.replaceChildren(table);
  showSelectedDate(rows);
}

function renderMonth() {
  const monthRows = byMonth(model.rows).get(monthKey) ?? [];
  const table = makeElement("table", "calendar-table month-table");
  addTableCaption(table, `Daily ${metricName(model.scope, 2)} for ${monthFormatter.format(utcDate(`${monthKey}-01`))}.`);
  const head = document.createElement("thead");
  const header = document.createElement("tr");
  for (let weekday = 0; weekday < 7; weekday += 1) {
    const cell = makeElement("th", "weekday-heading", weekdayNames[weekday].slice(0, 3));
    cell.scope = "col";
    header.append(cell);
  }
  head.append(header);
  table.append(head);

  const body = document.createElement("tbody");
  const lookup = new Map(monthRows.map((row) => [row.date, row]));
  const firstDay = `${monthKey}-01`;
  const firstOffset = utcDate(firstDay).getUTCDay();
  const daysInMonth = new Date(Date.UTC(Number(monthKey.slice(0, 4)), Number(monthKey.slice(5, 7)), 0)).getUTCDate();
  const weekCount = Math.ceil((firstOffset + daysInMonth) / 7);
  for (let week = 0; week < weekCount; week += 1) {
    const row = document.createElement("tr");
    for (let weekday = 0; weekday < 7; weekday += 1) {
      const dayNumber = week * 7 + weekday - firstOffset + 1;
      const cell = document.createElement("td");
      if (dayNumber >= 1 && dayNumber <= daysInMonth) {
        const date = `${monthKey}-${String(dayNumber).padStart(2, "0")}`;
        const value = lookup.get(date);
        if (value) cell.append(dayButton(value, model.scope, true, dayNumber));
        else cell.append(makeElement("span", "month-day-number", String(dayNumber)));
      }
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  elements.monthCalendar.replaceChildren(table);
  showSelectedDate(monthRows);
}

function renderWeek() {
  const weekRows = byWeek(model.rows).get(weekKey) ?? [];
  elements.weekCalendar.replaceChildren();
  for (const row of weekRows) {
    const item = document.createElement("li");
    item.append(dayButton(row, model.scope, false));
    elements.weekCalendar.append(item);
  }
  showSelectedDate(weekRows);
}

function renderCalendar() {
  if (!model) return;
  elements.yearScroll.hidden = view !== "year";
  elements.monthCalendar.hidden = view !== "month";
  elements.weekCalendar.hidden = view !== "week";
  elements.monthControl.hidden = view !== "month";
  elements.weekControl.hidden = view !== "week";

  let rows = model.rows;
  if (view === "month") rows = byMonth(model.rows).get(monthKey) ?? [];
  if (view === "week") rows = byWeek(model.rows).get(weekKey) ?? [];
  selectedDate = selectedDateFor(rows, selectedDate);
  const summary = summarize(rows);
  const range = rows.length ? `${formatDate(rows[0].date, shortDateFormatter)}–${formatDate(rows.at(-1).date, shortDateFormatter)}` : "No dates";
  elements.viewSummary.textContent = `${countFormatter.format(summary.total)} ${metricName(model.scope, summary.total)} · ${summary.activeDays} active days · ${range}`;

  if (view === "year") renderYear();
  if (view === "month") renderMonth();
  if (view === "week") renderWeek();
}

function renderLoadedCalendar(data) {
  model = data;
  elements.scopeDescription.textContent = data.source.description;
  elements.yearScroll.setAttribute("aria-label", `${data.source.label}, latest ${data.source.days} days`);
  const total = countFormatter.format(data.summary.total);
  elements.snapshot.textContent = `${total} ${metricName(data.scope, data.summary.total)} · ${data.summary.activeDays} active days · ${data.source.days} dated days`;
  describeRefresh(data.source.refresh, data.source);
  updateMonthChoices();
  updateWeekChoices();
  setMetricLinks(data.scope);
  renderCalendar();
  elements.calendarMessage.textContent = "Select any dated square to read its exact count. Use the view buttons to compare the full year, a month or a week.";
}

async function loadCalendar({ force = false } = {}) {
  if (loading) return;
  loading = true;
  elements.scope.disabled = true;
  elements.refreshButton.disabled = true;
  elements.refreshButton.textContent = "Checking…";
  if (!model) elements.calendarMessage.textContent = "Loading verified public data…";
  try {
    const cache = force ? "no-store" : "default";
    const response = await fetch(`/api/calendar?scope=${encodeURIComponent(elements.scope.value)}`, { cache });
    if (!response.ok) throw new Error("Calendar request failed");
    const data = await response.json();
    renderLoadedCalendar(data);
  } catch {
    if (model) {
      elements.scope.value = model.scope;
      setRefreshState("unknown", `Could not verify a new snapshot. The calendar remains on the last loaded data through ${formatDate(model.source.dataThrough)}; its currentness is unknown.`, { severity: "warning" });
    } else {
      elements.calendarMessage.textContent = "Verified calendar data is temporarily unavailable. Try again shortly.";
      elements.calendarMessage.dataset.state = "error";
    }
  } finally {
    loading = false;
    elements.scope.disabled = false;
    elements.refreshButton.disabled = false;
    elements.refreshButton.textContent = "Refresh data";
  }
}

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}
elements.scope.addEventListener("change", () => loadCalendar({ force: true }));
elements.month.addEventListener("change", () => {
  monthKey = elements.month.value;
  renderCalendar();
});
elements.week.addEventListener("change", () => {
  weekKey = elements.week.value;
  renderCalendar();
});
elements.refreshButton.addEventListener("click", () => loadCalendar({ force: true }));

loadCalendar();
