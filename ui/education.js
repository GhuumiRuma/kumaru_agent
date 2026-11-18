/* Kumaru Education page: what the model knows, per area.
 *
 * Reads GET /v1/education (kumaru.server.education): for each benchmark and
 * setup (run label + model), the newest `kumaru bench` measurement, overall
 * and per area. A measurement still running refreshes every 30 seconds.
 *
 * Area names, labels and paths come from run files on disk: everything is
 * built with DOM nodes and textContent, never parsed as HTML.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const SETUP_KEY = "kumaru.education.setup";
const COMPARE_KEY = "kumaru.education.compare";
// An unfinished measurement updated this recently is still running.
const LIVE_MS = 10 * 60 * 1000;
const FAST_POLL_MS = 30 * 1000;
const SLOW_POLL_MS = 5 * 60 * 1000;
// Areas with fewer answers are shown dimmed: their interval is too wide to read much into.
const FEW = 10;

// How each section is titled. `chance` is the score of random guessing.
const FAMILIES = {
  supergpqa_cs: {
    title: "Computer science · graduate level",
    note: "SuperGPQA, field “Computer Science and Technology”, by subfield. Mostly ten options per question.",
    chance: 0.1,
  },
  supergpqa_stem: {
    title: "STEM · graduate level",
    note: "SuperGPQA, Science and Engineering, by field. A sample: see the question count per area.",
    chance: 0.1,
  },
  mmlu_pro: { title: "Knowledge and reasoning · 14 subjects", note: "MMLU-Pro, by subject.", chance: 0.1 },
  multipl_e: {
    title: "Programming languages",
    note: "HumanEval's problems in eight languages, graded by running their unit tests (MultiPL-E).",
    unit: "languages",
  },
  humaneval: { title: "Python coding", note: "HumanEval, graded by running unit tests." },
  gsm8k: { title: "Grade-school maths", note: "GSM8K word problems." },
  ifeval: { title: "Following instructions", note: "IFEval: answers checked against verifiable instructions." },
};
const ORDER = ["supergpqa_cs", "supergpqa_stem", "mmlu_pro", "multipl_e", "humaneval", "gsm8k", "ifeval"];
const LANGUAGES = {
  multipl_e_py: "Python", multipl_e_js: "JavaScript", multipl_e_ts: "TypeScript", multipl_e_cs: "C#",
  multipl_e_java: "Java", multipl_e_cpp: "C++", multipl_e_go: "Go", multipl_e_rs: "Rust",
};

const state = { data: null, timer: null, failures: 0 };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const pct = (x, digits = 0) => `${(x * 100).toFixed(digits)}%`;
const clamp = (x) => Math.max(0, Math.min(1, x));

function shortModel(id) {
  const name = (id || "unknown model").split("/").pop().split(":")[0];
  return name.replace(/-GGUF$/i, "");
}

function when(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function phase(m) {
  if (m.complete) return "complete";
  return Date.now() - Date.parse(m.updated_at) < LIVE_MS ? "live" : "stopped";
}

function familyInfo(family) {
  if (FAMILIES[family]) return FAMILIES[family];
  if (family.startsWith("golden")) {
    return { title: `Your questions: ${family.replace(/^golden-?/, "") || "golden"}`, note: "A golden set, by tag." };
  }
  return { title: family, note: "" };
}

// --- setups ------------------------------------------------------------------

function setupsOf(measurements) {
  const setups = new Map();
  for (const m of measurements) {
    const key = `${m.label}\u0000${m.model}`;
    if (!setups.has(key)) setups.set(key, { key, label: m.label, model: m.model, list: [], updated: "" });
    const setup = setups.get(key);
    setup.list.push(m);
    setup.references = Math.max(setup.references || 0, m.references);
    setup.thinking = setup.thinking ?? m.thinking;
    if (m.updated_at > setup.updated) setup.updated = m.updated_at;
  }
  return [...setups.values()].sort((a, b) => (b.updated > a.updated ? 1 : -1));
}

function setupName(setup) {
  const parts = [setup.label, shortModel(setup.model)];
  if (setup.thinking === true) parts.push("thinking");
  if (setup.references) parts.push(`${setup.references} reference${setup.references === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function fillSelect(select, setups, chosen, blank) {
  select.replaceChildren();
  if (blank) select.append(new Option(blank, ""));
  for (const setup of setups) select.append(new Option(setupName(setup), setup.key));
  select.value = setups.some((s) => s.key === chosen) ? chosen : blank ? "" : setups[0]?.key ?? "";
}

// --- rows --------------------------------------------------------------------

// The bars of one section: a benchmark's areas, or one bar per suite member.
function rowsOf(list) {
  if (list.length === 1 && (list[0].areas.length || !(list[0].benchmark in LANGUAGES))) {
    const m = list[0];
    if (m.areas.length) return m.areas.map((a) => ({ ...a, key: a.name }));
    return [{ name: m.benchmark, answered: m.answered, correct: m.correct, accuracy: m.accuracy, ci95: m.ci95, key: m.benchmark }];
  }
  return list
    .map((m) => ({
      name: LANGUAGES[m.benchmark] || m.benchmark,
      answered: m.answered, correct: m.correct, accuracy: m.accuracy, ci95: m.ci95, key: m.benchmark,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function buildRow(row, chance, other, otherName) {
  const node = el("div", row.answered < FEW ? "row few" : "row");
  const [lo, hi] = row.ci95;
  const sentence = `${row.name}: ${pct(row.accuracy)} correct, 95% interval ${pct(lo)} to ${pct(hi)}, ${row.correct} of ${row.answered}`;
  node.title = other === undefined ? sentence : `${sentence}; ${otherName}: ${pct(other.accuracy)}`;

  const track = el("div", "track");
  track.setAttribute("aria-hidden", "true");
  const band = el("div", "band");
  band.style.left = pct(clamp(lo), 2);
  band.style.width = pct(clamp(hi - lo), 2);
  const fill = el("div", "fill");
  fill.style.width = pct(clamp(row.accuracy), 2);
  track.append(band, fill);
  if (chance) {
    const tick = el("div", "chance");
    tick.style.left = pct(chance, 2);
    track.append(tick);
  }
  if (other !== undefined) {
    const mark = el("div", "other");
    mark.style.left = pct(clamp(other.accuracy), 2);
    track.append(mark);
  }

  const detail = el("div", "detail", `${pct(lo)}–${pct(hi)} · ${row.correct}/${row.answered}`);
  if (other !== undefined) {
    const delta = (row.accuracy - other.accuracy) * 100;
    const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
    detail.append(" · ", el("span", `delta ${delta > 0 ? "up" : delta < 0 ? "down" : ""}`, `${sign}${Math.abs(delta).toFixed(1)}`));
  }
  node.append(el("div", "name", row.name), track, el("div", "value", row.answered ? pct(row.accuracy) : "–"), detail);
  return node;
}

// --- sections ----------------------------------------------------------------

function summaryOf(list, unit) {
  const line = el("div", "summary");
  if (list.length === 1) {
    const m = list[0];
    line.append(el("span", "score", m.answered ? pct(m.accuracy, 1) : "–"));
    if (m.answered) line.append(el("span", "", `95% interval ${pct(m.ci95[0], 1)}–${pct(m.ci95[1], 1)}`));
    line.append(el("span", "", `${m.answered} of ${m.planned} questions`));
  } else {
    line.append(el("span", "", `${list.length} ${unit || "benchmarks"}`));
  }
  const newest = list.reduce((a, b) => (b.updated_at > a.updated_at ? b : a));
  line.append(el("span", "", when(newest.updated_at)));
  const running = list.filter((m) => phase(m) === "live");
  const stopped = list.filter((m) => phase(m) === "stopped");
  if (running.length) line.append(el("span", "chip live", "measuring now"));
  else if (stopped.length) line.append(el("span", "chip stopped", "stopped before the end"));
  return line;
}

function progressOf(list) {
  const planned = list.reduce((sum, m) => sum + m.planned, 0);
  const answered = list.reduce((sum, m) => sum + m.answered, 0);
  if (!planned || answered >= planned) return null;
  const bar = el("div", "progress");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", "Questions answered");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(planned));
  bar.setAttribute("aria-valuenow", String(answered));
  const inner = el("div");
  inner.style.width = pct(answered / planned, 2);
  bar.append(inner);
  return bar;
}

function buildSection(family, list, compare) {
  const info = familyInfo(family);
  const card = el("section", "card");
  card.setAttribute("aria-label", info.title);
  card.append(el("h2", "", info.title));
  if (info.note) card.append(el("p", "note", info.note));
  card.append(summaryOf(list, info.unit));
  const progress = progressOf(list);
  if (progress) card.append(progress);

  const others = new Map();
  const otherList = compare?.list.filter((m) => m.family === family) ?? [];
  if (otherList.length) for (const row of rowsOf(otherList)) others.set(row.key, row);

  const rows = el("div", "rows");
  const built = rowsOf(list);
  for (const row of built) rows.append(buildRow(row, info.chance, others.get(row.key), compare && setupName(compare)));
  if (!built.length) rows.append(el("p", "note", "No answers yet."));
  card.append(rows);
  return card;
}

function legend(compare, anyChance) {
  const node = el("div", "legend");
  const key = (cls, text) => {
    const item = el("span");
    item.append(el("i", cls), text);
    return item;
  };
  node.append(key("k-fill", "answered correctly"), key("k-band", "95% interval"));
  if (anyChance) node.append(key("k-chance", "random guessing"));
  if (compare) node.append(key("k-other", setupName(compare)));
  return node;
}

// --- page --------------------------------------------------------------------

function render() {
  const data = state.data;
  const setups = setupsOf(data.measurements);
  const setupSelect = $("setup");
  const compareSelect = $("compare");
  fillSelect(setupSelect, setups, localStorage.getItem(SETUP_KEY), null);
  const current = setups.find((s) => s.key === setupSelect.value);
  fillSelect(compareSelect, setups.filter((s) => s !== current), localStorage.getItem(COMPARE_KEY), "nothing");
  const compare = setups.find((s) => s.key === compareSelect.value) || null;
  setupSelect.disabled = setups.length < 2;
  compareSelect.disabled = setups.length < 2;

  $("empty").hidden = Boolean(current);
  const sections = $("sections");
  sections.replaceChildren();
  if (current) {
    const families = new Map();
    for (const m of current.list) {
      if (!families.has(m.family)) families.set(m.family, []);
      families.get(m.family).push(m);
    }
    const rank = (f) => (ORDER.includes(f) ? ORDER.indexOf(f) : ORDER.length);
    const names = [...families.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    sections.append(legend(compare, names.some((f) => familyInfo(f).chance)));
    for (const family of names) sections.append(buildSection(family, families.get(family), compare));
  }

  const live = data.measurements.filter((m) => phase(m) === "live");
  const status = $("status");
  status.replaceChildren();
  if (live.length) {
    const parts = live.map((m) => `${m.benchmark} ${m.answered}/${m.planned}`);
    status.textContent = `Measuring now: ${parts.join(", ")}. This page updates every 30 seconds.`;
  }

  const footer = $("sources");
  footer.replaceChildren(el("span", "", `Read from: ${data.runs_dirs.join(", ")}`));
  if (data.missing.length) footer.append(el("span", "", `Not found: ${data.missing.join(", ")}`));
  for (const skipped of data.skipped) footer.append(el("span", "", `Skipped: ${skipped}`));
  return live.length > 0;
}

async function refresh() {
  clearTimeout(state.timer);
  let live = false;
  try {
    const response = await fetch("/v1/education", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    state.failures = 0;
    live = render();
  } catch (error) {
    state.failures += 1;
    const status = $("status");
    status.replaceChildren(el("span", "err", `Can't read measurements from the Kumaru server (${error.message}). Retrying.`));
  }
  if (document.visibilityState === "visible") {
    state.timer = setTimeout(refresh, live || state.failures ? FAST_POLL_MS : SLOW_POLL_MS);
  }
}

$("setup").addEventListener("change", (event) => {
  localStorage.setItem(SETUP_KEY, event.target.value);
  if (state.data) render();
});
$("compare").addEventListener("change", (event) => {
  localStorage.setItem(COMPARE_KEY, event.target.value);
  if (state.data) render();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refresh();
  else clearTimeout(state.timer);
});

refresh();
