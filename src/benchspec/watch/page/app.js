// The watch page. It only renders: every number and label arrives computed in the view.

const byId = (id) => document.getElementById(id);
const phone = matchMedia("(max-width: 639px)");
const canHover = matchMedia("(hover: hover)");
const ANNOUNCE_EVERY_MS = 10000;
const SWIPE_CLOSE_PX = 60;

let view = null;
let ended = false;
let connected = true;
let fatal = null;
let frame = 0;
let tab = "matrix";
let pickedTab = false;
let lastState = null;
let shape = "";
let cells = new Map();
let groups = new Map();
let selected = null;
let drawerShown = "";
let hovered = null;
let hoverTimer = 0;
let announced = { text: "", at: 0 };
const groupToggles = new Map();

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);

  for (const [name, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "text") children.unshift(value);
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : value);
  }

  fill(node, ...children);

  return node;
}

function fill(node, ...children) {
  node.replaceChildren(
    ...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false),
  );
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function setClass(node, name) {
  if (node.className !== name) node.className = name;
}

function schedule() {
  if (!frame) frame = requestAnimationFrame(render);
}

const source = new EventSource("events");
source.addEventListener("open", () => {
  connected = true;
  schedule();
});
source.addEventListener("view", (event) => {
  view = JSON.parse(event.data);
  connected = true;
  schedule();
});
source.addEventListener("end", () => {
  ended = true;
  source.close();
  schedule();
});
source.addEventListener("error", (event) => {
  if (event.data) {
    fatal = event.data;
    source.close();
  } else if (!ended) {
    connected = false;
  }

  schedule();
});

function render() {
  frame = 0;
  const state = view ? view.state : "loading";
  const hasRun = view !== null && view.top !== undefined;

  renderBanner();
  renderEmpty(state);
  byId("counter").hidden = !hasRun;
  byId("run").hidden = !hasRun || state === "empty";
  byId("run").setAttribute("aria-busy", String(state === "loading"));

  if (!hasRun) return;

  if (state !== "live" && state !== "stale" && state !== lastState && !pickedTab) tab = "leaderboard";
  lastState = state;

  renderTop();
  renderCounter(state);
  renderTabs();
  renderMatrix();
  renderLeaderboard();
  renderDrawer();
  renderHoverCard();
  announce(state);
}

function renderBanner() {
  const banner = byId("banner");
  const lost = !connected && !ended && !fatal;
  banner.hidden = !fatal && !lost;
  setText(banner, fatal || "Lost connection to benchspec run · retrying in 5s");
}

function renderEmpty(state) {
  const copy = {
    loading: ["Loading run…", ""],
    none: ["No run yet.", "Start one with benchspec run."],
    empty: ["No cells in this run.", ""],
  }[state];
  byId("empty").hidden = !copy;

  if (!copy) return;
  setText(byId("empty-line"), copy[0]);
  setText(byId("empty-hint"), copy[1]);

  if (state !== "empty") setText(byId("status"), copy[0]);
}

function renderTop() {
  const top = view.top;
  setText(byId("iteration"), top.iteration);
  byId("suite").replaceChildren(
    top.suite,
    top.count ? el("span", { class: "bs-wide", text: ` · ${top.count}` }) : "",
  );
  const state = byId("run-state");
  document.querySelector(".bs-top").classList.toggle("bs-live-run", top.live);
  byId("run").classList.toggle("bs-stale-run", view.state === "stale");
  setClass(state, view.state === "stale" ? "bs-stale" : top.live && connected ? "bs-live" : "");
  fill(state, top.status, top.detail ? el("span", { class: "bs-wide", text: ` · ${top.detail}` }) : "");
}

function renderCounter(state) {
  const counter = view.counter;
  const headline = byId("headline");
  const percent = el(
    "span",
    { class: "bs-num" },
    String(counter.percent),
    el("span", { class: "bs-pct", text: "%" }),
  );

  if (state === "finished") {
    setClass(headline, "bs-n bs-fin");
    headline.replaceChildren(
      el(
        "span",
        { class: counter.problem ? "bs-bad" : "bs-ok" },
        counter.problem ? "✗" : "✓",
        el("span", { class: "bs-wide", text: " Finished" }),
      ),
      el("span", { class: "bs-dot bs-wide", text: "·" }),
      percent,
      el("span", { class: "bs-dot", text: "·" }),
      el(
        "span",
        {},
        `${counter.done} of ${counter.total}`,
        el("span", { class: "bs-narrow", text: " samples" }),
      ),
      counter.problem ? el("span", { class: "bs-dot", text: "·" }) : "",
      counter.problem ? el("span", { class: "bs-att", text: counter.problem }) : "",
      el(
        "span",
        { class: "bs-el" },
        el("span", { class: "bs-wide", text: `${counter.clock_word} ` }),
        counter.clock,
      ),
    );
  } else {
    setClass(headline, "bs-n");
    headline.replaceChildren(
      percent,
      el("span", { text: counter.line }),
      el(
        "span",
        { class: "bs-el" },
        `${counter.clock_word} ${counter.clock}`,
        counter.left ? el("span", { class: "bs-wide", text: ` · ${counter.left}` }) : "",
      ),
    );
  }

  byId("progress-frame").hidden = state === "empty";
  const progress = byId("progress");
  const done = counter.problem ? "bs-prog bs-bad" : "bs-prog bs-done";
  setClass(progress, state === "finished" ? done : "bs-prog");
  progress.setAttribute("aria-valuemax", counter.total);
  progress.setAttribute("aria-valuenow", counter.done);
  progress.setAttribute("aria-valuetext", counter.progress);
  progress.firstElementChild.style.width = `${counter.total ? (100 * counter.done) / counter.total : 0}%`;

  const marks = {
    failed: "bs-att bs-failed",
    errored: "bs-att",
    quiet: "bs-warn",
    waiting: "bs-wait",
    unfinished: "bs-warn",
  };
  byId("tally").replaceChildren(
    ...counter.tally.map(({ bucket, count }) =>
      el("li", {
        class: count ? marks[bucket] || "" : `bs-zero ${bucket === "failed" ? "bs-failed" : ""}`,
        text: `${count} ${bucket}`,
      }),
    ),
    counter.workers ? el("li", { class: "bs-wk", text: counter.workers }) : "",
  );

  byId("notes").hidden = !view.report && !ended;
  byId("report-line").hidden = !view.report;
  setText(byId("report"), view.report || "");
  byId("reopen-line").hidden = !ended;
  setText(byId("reopen"), view.reopen);
}

function announce(state) {
  const terminal = state === "finished" || state === "stopped";
  const text = view.counter.announce;
  const now = Date.now();

  if (text === announced.text || (!terminal && now - announced.at < ANNOUNCE_EVERY_MS)) return;
  announced = { text, at: now };
  setText(byId("status"), text);
}

function renderTabs() {
  for (const name of ["matrix", "leaderboard"]) {
    const button = byId(`tab-${name}`);
    button.setAttribute("aria-selected", String(tab === name));
    button.tabIndex = tab === name ? 0 : -1;
    byId(`panel-${name}`).hidden = tab !== name;
  }
}

function pickTab(name, focus) {
  tab = name;
  pickedTab = true;
  render();

  if (focus) byId(`tab-${name}`).focus();
}

function tokenPx(name) {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
}

function armsFit(count) {
  const column = tokenPx("--size-arm-phone") + tokenPx("--space-8");

  return tokenPx("--size-label-phone") + count * column <= byId("panel-matrix").clientWidth;
}

function renderMatrix() {
  const matrix = view.matrix;
  setText(byId("caption"), matrix.caption);
  const scrolls = phone.matches && !armsFit(matrix.arms.length);
  const next = JSON.stringify([
    scrolls,
    matrix.arms,
    matrix.groups.map((group) => [
      group.name,
      group.rows.map((row) => [
        row.id,
        row.cells.map((cell) => [cell.kind === "na", (cell.boxes || []).length]),
      ]),
    ]),
  ]);

  if (next !== shape) {
    shape = next;
    buildMatrix(matrix, scrolls);
  }

  for (const group of matrix.groups) {
    const refs = groups.get(group.name);
    const open = groupToggles.has(group.name) ? groupToggles.get(group.name) : group.open;
    refs.button.setAttribute("aria-expanded", String(open));
    setText(refs.chevron, open ? "▾" : "▸");
    refs.meta.replaceChildren(
      group.evals,
      group.note ? " · " : "",
      group.note ? el("span", { text: group.note }) : "",
    );

    for (const row of refs.rows) row.hidden = !open;
    group.summaries.forEach((summary, index) => {
      const ref = refs.summaries[index];
      ref.text.replaceChildren(
        summary.text,
        summary.count ? el("span", { class: "bs-wide bs-muted", text: ` · ${summary.count}` }) : "",
      );
      setClass(ref.box, summary.muted ? "bs-gs bs-q" : "bs-gs");
      setClass(ref.bar, summary.live ? "bs-gbar bs-live" : "bs-gbar");
      ref.bar.firstElementChild.style.width = `${100 * summary.bar}%`;
    });

    for (const row of group.rows) {
      row.cells.forEach((cell, column) => {
        if (cells.has(keyOf(group, row, column))) updateCell(keyOf(group, row, column), cell);
      });
    }
  }

  keepACellReachable();
  updateArmHint();
}

function buildMatrix(matrix, scrolls) {
  const grid = byId("matrix");
  grid.style.setProperty("--bs-arms", matrix.arms.length);
  cells = new Map();
  groups = new Map();
  const small = matrix.arms.length >= 4 || phone.matches;

  const head = el(
    "div",
    { class: "bs-mrow bs-mhead", role: "row" },
    el(
      "span",
      { role: "columnheader" },
      el("span", { class: "bs-wide", text: "skill/eval_id" }),
      el("span", { class: "bs-narrow", text: "eval" }),
    ),
    matrix.arms.map((arm) =>
      el("span", { role: "columnheader" }, el("b", { text: arm.name }), el("span", { text: arm.detail })),
    ),
  );
  const body = [];
  let rowIndex = 0;

  for (const group of matrix.groups) {
    const chevron = el("span", { class: "bs-chev", "aria-hidden": "true" });
    const meta = el("span", { class: "bs-gmeta" });
    const button = el(
      "button",
      { class: "bs-gl", type: "button", role: "rowheader", onclick: () => toggleGroup(group.name) },
      el("span", { class: "bs-gname" }, chevron, group.name),
      meta,
    );
    const summaries = group.summaries.map(() => {
      const text = el("span");
      const bar = el("div", { class: "bs-gbar" }, el("i"));

      return { text, bar, box: el("div", { class: "bs-gs", role: "gridcell" }, text, bar) };
    });
    body.push(
      el(
        "div",
        { class: "bs-mrow bs-grp", role: "row" },
        button,
        summaries.map((summary) => summary.box),
      ),
    );

    const rows = group.rows.map((row) => {
      const position = rowIndex++;

      return el(
        "div",
        { class: "bs-mrow", role: "row" },
        el("span", { class: "bs-eval", role: "rowheader", text: row.id }),
        row.cells.map((cell, column) => {
          return el(
            "div",
            { role: "gridcell" },
            buildCell(cell, keyOf(group, row, column), position, column, small),
          );
        }),
      );
    });
    body.push(...rows);
    groups.set(group.name, { button, chevron, meta, summaries, rows });
  }

  if (scrolls) {
    const scroller = el("div", { class: "bs-mx-scroll", onscroll: syncArms }, body);
    grid.replaceChildren(el("div", { class: "bs-mx" }, el("div", { class: "bs-mx-head" }, head), scroller));
  } else {
    grid.replaceChildren(head, ...body);
  }
}

// Roving tabindex: one visible cell is always in the tab order, even after its group collapses.
function keepACellReachable() {
  const buttons = [...byId("matrix").querySelectorAll("button.bs-cell")];
  const current = buttons.find((button) => button.tabIndex === 0);

  if (current && current.offsetParent !== null) return;

  const visible = buttons.find((button) => button.offsetParent !== null);

  if (!visible) return;

  if (current) current.tabIndex = -1;

  visible.tabIndex = 0;
}

function keyOf(group, row, column) {
  return `${group.name}${row.id}/${column}`;
}

function buildCell(cell, key, row, column, small) {
  if (cell.kind === "notrun") {
    return el(
      "div",
      { class: "bs-cell bs-k-notrun" },
      el("span", { class: "bs-t bs-rate", text: cell.label }),
    );
  }

  if (cell.kind === "na") {
    return el(
      "div",
      { class: "bs-cell bs-k-na", role: "img", "aria-label": cell.aria },
      el(
        "span",
        { class: small ? "bs-bx bs-sm" : "bs-bx" },
        cell.boxes.map((name) => el("i", { class: `bs-b ${name}` })),
      ),
      el("span", { class: "bs-t bs-rate", text: cell.label }),
    );
  }

  const boxes = cell.boxes.map(() => el("i"));
  const label = el("span");
  const button = el(
    "button",
    {
      class: "bs-cell",
      type: "button",
      tabindex: "-1",
      "aria-haspopup": "dialog",
      "aria-expanded": "false",
      "aria-controls": "drawer",
      "data-row": row,
      "data-column": column,
      onclick: () => openDrawer(key),
      onmouseenter: () => showHover(key),
      onmouseleave: () => hideHover(),
      onfocus: () => showHover(key),
      onblur: () => hideHover(),
    },
    el("span", { class: small ? "bs-bx bs-sm" : "bs-bx", "aria-hidden": "true" }, boxes),
    label,
  );
  cells.set(key, { button, boxes, label, data: cell });

  return button;
}

function updateCell(key, cell) {
  const ref = cells.get(key);
  ref.data = cell;
  const isSelected = selected !== null && selected.key === key;
  setClass(ref.button, `bs-cell bs-k-${cell.kind}${isSelected ? " bs-sel" : ""}`);
  ref.button.setAttribute("aria-expanded", String(isSelected));

  if (cell.aria) ref.button.setAttribute("aria-label", cell.aria);
  cell.boxes.forEach((name, index) => setClass(ref.boxes[index], `bs-b ${name}`));
  const showsOnPhone = cell.short && cell.short === cell.label;
  setClass(ref.label, `bs-t${cell.muted ? " bs-q" : ""}${showsOnPhone ? " bs-rate" : ""}`);

  if (cell.count) fill(ref.label, el("span", { class: "bs-count", text: `${cell.count} · ` }), cell.progress);
  else setText(ref.label, cell.label);
}

function toggleGroup(name) {
  const refs = groups.get(name);
  groupToggles.set(name, refs.button.getAttribute("aria-expanded") !== "true");
  schedule();
}

function syncArms(event) {
  const scroller = event.currentTarget;
  scroller.parentElement.firstElementChild.scrollLeft = scroller.scrollLeft;
  scroller.parentElement.classList.toggle("bs-scrolled", scroller.scrollLeft > 0);
  updateArmHint();
}

function updateArmHint() {
  const hint = byId("arm-hint");
  const scroller = byId("matrix").querySelector(".bs-mx-scroll");
  hint.hidden = !scroller;

  if (!scroller) return;
  const total = view.matrix.arms.length;
  const column = tokenPx("--size-arm-phone") + tokenPx("--space-8");
  const shown = Math.max(1, Math.floor((scroller.clientWidth - tokenPx("--size-label-phone")) / column));
  const first = Math.min(total - shown + 1, Math.round(scroller.scrollLeft / column) + 1);
  const last = first + shown - 1;
  setText(hint, `${first > 1 ? "← " : ""}arms ${first}–${last} of ${total}${last < total ? " →" : ""}`);
}

function moveInGrid(event) {
  const from = event.target.closest("button.bs-cell");
  const step = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[event.key];

  if (!from || !step) return;
  event.preventDefault();
  const buttons = [...byId("matrix").querySelectorAll("button.bs-cell")].filter(
    (button) => button.offsetParent !== null,
  );
  let row = Number(from.dataset.row);
  let column = Number(from.dataset.column);
  const rowLimit = Math.max(...buttons.map((button) => Number(button.dataset.row)));

  for (;;) {
    row += step[0];
    column += step[1];

    if (row < 0 || row > rowLimit || column < 0 || column >= view.matrix.arms.length) return;
    const target = buttons.find(
      (button) => Number(button.dataset.row) === row && Number(button.dataset.column) === column,
    );

    if (target) {
      from.tabIndex = -1;
      target.tabIndex = 0;
      target.focus();

      return;
    }
  }
}

function showHover(key) {
  if (!canHover.matches) return;
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => {
    hovered = key;
    renderHoverCard();
  }, tokenPx("--dur-hover-delay"));
}

function hideHover() {
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => {
    if (byId("hovercard").matches(":hover")) return;
    dismissHover();
  }, 0);
}

function dismissHover() {
  clearTimeout(hoverTimer);

  if (hovered !== null && cells.has(hovered)) cells.get(hovered).button.removeAttribute("aria-describedby");
  hovered = null;
  byId("hovercard").hidden = true;
}

function renderHoverCard() {
  const card = byId("hovercard");
  const ref = hovered === null ? null : cells.get(hovered);

  if (!ref || !ref.data.hover) {
    card.hidden = true;

    return;
  }

  const hover = ref.data.hover;
  const sample = ref.data.samples[0];
  fill(
    card,
    el("div", { class: "bs-hid", text: `${sample.skill}/${sample.eval_id}` }),
    el("div", { class: "bs-muted", text: sample.arm }),
    el(
      "div",
      { class: "bs-hh" },
      el("b", { text: hover.rate }),
      el("span", { class: "bs-muted", text: hover.checks }),
    ),
    hover.problem
      ? el("div", { class: hover.problem.warn ? "bs-hprob bs-warn" : "bs-hprob", text: hover.problem.text })
      : "",
    hover.rows.map((row) =>
      el(
        "div",
        { class: "bs-hr" },
        el("span", { text: row.sample }),
        el("span", { class: row.bad ? "bs-x" : "", text: row.verdict }),
        el("span", { class: "bs-muted", text: row.duration }),
      ),
    ),
    el("p", { text: "Open the cell for assertions and output." }),
  );
  ref.button.setAttribute("aria-describedby", "hovercard");

  if (!card.hidden) return;

  card.hidden = false;
  const row = ref.button.closest(".bs-mrow").getBoundingClientRect();
  const gap = tokenPx("--space-16");
  const beside = row.right + gap + card.offsetWidth <= innerWidth;
  const top = row.top + card.offsetHeight > innerHeight ? row.bottom - card.offsetHeight : row.top;
  card.style.left = `${beside ? row.right + gap : row.left}px`;
  card.style.top = `${Math.max(gap, top)}px`;
}

function firstToShow(samples) {
  const index = samples.findIndex((sample) => !sample.passes);

  return index < 0 ? 0 : index;
}

function openDrawer(key) {
  dismissHover();
  selected = { key, sample: firstToShow(cells.get(key).data.samples) };
  drawerShown = "";
  renderMatrix();
  renderDrawer();
  byId("drawer").querySelector(".bs-x").focus();
}

function closeDrawer() {
  if (selected === null) return;
  const ref = cells.get(selected.key);
  selected = null;
  renderDrawer();

  if (view) renderMatrix();
  if (ref) ref.button.focus();
}

function breakable(path) {
  return path
    .split("/")
    .filter((part) => part !== "")
    .flatMap((part) => [el("span", { text: `${part}/` }), el("wbr")]);
}

function renderDrawer() {
  const drawer = byId("drawer");
  const ref = selected === null ? null : cells.get(selected.key);
  drawer.hidden = !ref;
  byId("scrim").hidden = !ref || !phone.matches;

  if (!ref) return;

  const samples = ref.data.samples;
  const sample = samples[Math.min(selected.sample, samples.length - 1)];
  setClass(drawer, phone.matches ? "bs-drawer bs-sheet" : "bs-drawer");
  const gap = tokenPx("--space-16");
  drawer.style.top = phone.matches
    ? ""
    : `${Math.max(gap, byId("counter").getBoundingClientRect().bottom + gap)}px`;

  const showing = JSON.stringify([sample.cell, sample.graded]);

  if (showing !== drawerShown) {
    drawerShown = showing;
    buildDrawer(drawer, samples, sample);
    loadAssertions(sample);
  }

  fill(
    drawer.querySelector(".bs-st"),
    el("span", { class: sample.bad ? "bs-bad" : "", text: sample.verdict }),
    sample.detail ? ` · ${sample.detail}` : "",
  );
  setText(drawer.querySelector(".bs-facts"), phone.matches ? sample.facts_phone : sample.facts);
  fillPhases(drawer.querySelector(".bs-phases"), sample.phases);
  drawer
    .querySelector(".bs-out")
    .replaceChildren(
      ...sample.output.map((line) =>
        el(
          "li",
          {},
          el("span", { text: line.offset }),
          el("span", { text: line.name }),
          el("span", { text: line.text }),
        ),
      ),
    );
  drawer.querySelector(".bs-output").hidden = sample.output.length === 0;
}

function buildDrawer(drawer, samples, sample) {
  const position = samples.indexOf(sample);
  const step = (by) => () => {
    selected.sample = position + by;
    renderDrawer();
    drawer.querySelector(by < 0 ? ".bs-prev" : ".bs-next").focus();
  };
  drawer.replaceChildren(
    el("div", { class: "bs-grab", "aria-hidden": "true" }),
    el(
      "div",
      { class: "bs-dh" },
      el("div", { class: "bs-st" }),
      el(
        "div",
        { class: "bs-id" },
        el("span", { text: `${sample.skill}/` }),
        el("wbr"),
        el("b", { text: sample.eval_id }),
      ),
      el(
        "div",
        { class: "bs-sub" },
        el("span", { text: `${sample.arm} · sample ${sample.number} of ${sample.of}` }),
        el(
          "span",
          { class: "bs-nav" },
          el("button", {
            class: "bs-iconbtn bs-prev",
            type: "button",
            "aria-label": "Previous sample",
            disabled: position === 0,
            onclick: step(-1),
            text: "‹",
          }),
          el("button", {
            class: "bs-iconbtn bs-next",
            type: "button",
            "aria-label": "Next sample",
            disabled: position === samples.length - 1,
            onclick: step(1),
            text: "›",
          }),
        ),
      ),
      el("div", { class: "bs-sub bs-facts" }),
      sample.message ? el("div", { class: "bs-sub bs-msg", text: sample.message }) : "",
      el("button", {
        class: "bs-iconbtn bs-x",
        type: "button",
        "aria-label": "Close",
        onclick: closeDrawer,
        text: "×",
      }),
    ),
    el(
      "section",
      { class: "bs-checks" },
      el("h3", { class: "bs-section-label", text: "[ assertions ]" }),
      el("p", { class: "bs-muted", text: sample.pending || "" }),
    ),
    el("section", { class: "bs-phases", hidden: true }),
    el(
      "section",
      { class: "bs-output" },
      el("h3", { class: "bs-section-label", text: "[ recent output · last 5 lines ]" }),
      el("ul", { class: "bs-out" }),
    ),
    el("p", { class: "bs-path" }, breakable(sample.path)),
  );
}

function fillPhases(section, phases) {
  section.hidden = !phases;

  if (!phases) return;
  const bar = el("div", { class: "bs-phase", role: "img", "aria-label": phases.label });
  const labels = el("div", { class: "bs-phl" });

  for (const segment of phases.segments) {
    const part = el("i", { class: `bs-p-${segment.kind}` });
    part.style.width = `${100 * segment.share}%`;
    bar.append(part);
    labels.append(
      el(
        "span",
        {},
        el("i", { class: `bs-p-${segment.kind}` }),
        segment.name,
        el("b", { text: segment.time }),
      ),
    );
  }

  section.replaceChildren(el("h3", { class: "bs-section-label", text: phases.caption }), bar, labels);
}

async function loadAssertions(sample) {
  if (!sample.graded) return;
  const response = await fetch(`grading?cell=${encodeURIComponent(sample.cell)}`);
  const section = byId("drawer").querySelector(".bs-checks");

  if (!section || drawerShown !== JSON.stringify([sample.cell, sample.graded])) return;
  if (!response.ok) {
    section.lastElementChild.textContent = "No grading for this sample.";

    return;
  }

  const grading = await response.json();
  let opened = false;
  const items = grading.assertions.map((assertion) => {
    if (assertion.state === "n/a") {
      return el(
        "li",
        {},
        el("span", { class: "bs-na", text: "–" }),
        el(
          "details",
          {},
          el("summary", { class: "bs-na" }, el("span", { text: assertion.text })),
          el("div", { class: "bs-ev", text: sample.scoped[assertion.index] || "n/a" }),
        ),
      );
    }

    const failed = assertion.state === "fail";
    const open = failed && !opened;
    opened = opened || open;

    return el(
      "li",
      {},
      el("span", { class: failed ? "bs-no" : "bs-ok", text: failed ? "✗" : "✓" }),
      el(
        "details",
        { open },
        el("summary", {}, el("span", { text: assertion.text })),
        el("div", { class: "bs-ev", text: `${assertion.checker} · ${assertion.evidence}` }),
      ),
    );
  });
  section.replaceChildren(
    el("h3", { class: "bs-section-label", text: grading.caption }),
    el("ul", { class: "bs-alst" }, items),
  );
}

function tip(label, text, id) {
  return el(
    "span",
    { class: "bs-lbwrap" },
    el(
      "button",
      { class: "bs-lgbtn bs-lbh", type: "button", "aria-describedby": id, text: label },
      el("span", { class: "bs-info", "aria-hidden": "true", text: "ⓘ" }),
    ),
    el("span", { class: "bs-tip", id, role: "tooltip", text }),
  );
}

function valueOf(cell, bestInline) {
  return el(
    "span",
    { class: cell.best ? "bs-v bs-best" : cell.na ? "bs-v bs-muted bs-na" : "bs-v" },
    cell.best ? "◆ " : "",
    cell.value,
    cell.best && bestInline ? el("span", { class: "bs-best-word", text: " best" }) : "",
  );
}

// Under a desktop value: its sub-line with the word "best", then its delta.
function subOf(cell) {
  const own = cell.delta ? "" : cell.sub || "";
  const first = cell.best ? (own ? `${own} · best` : "best") : own;
  const gain = cell.delta && cell.delta.startsWith("+");

  return [
    first ? el("span", { class: cell.err ? "bs-sub bs-err" : "bs-sub", text: first }) : "",
    cell.delta
      ? el(
          "span",
          { class: "bs-sub bs-delta" },
          el(gain ? "b" : "span", { text: `${cell.delta} ` }),
          cell.sub,
        )
      : "",
  ];
}

let boardShown = "";

function renderLeaderboard() {
  const board = view.leaderboard;
  const next = JSON.stringify(board);

  if (next === boardShown) return;
  boardShown = next;

  const table = el(
    "table",
    { class: "bs-lb bs-wide" },
    el("caption", { text: "Arms compared on graded samples" }),
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        el("th", { scope: "col" }, el("span", { class: "bs-sr", text: "Metric" })),
        board.arms.map((arm) => el("th", { scope: "col" }, el("b", { text: arm.name }), arm.detail)),
      ),
    ),
    el(
      "tbody",
      {},
      board.rows.map((row) =>
        el(
          "tr",
          {},
          el("th", { scope: "row" }, row.tip ? tip(row.label, row.tip, "tip-rate") : row.label),
          row.cells.map((cell) => el("td", {}, valueOf(cell), subOf(cell))),
        ),
      ),
    ),
  );

  const card = el("div", { class: "bs-lbc bs-narrow" });
  const list = el("ul", { class: "bs-lbl" });

  for (const row of board.rows) {
    if (row.bars) {
      const best = row.cells.findIndex((cell) => cell.best);
      const winner = row.cells[best];
      const under =
        best < 0
          ? ""
          : el(
              "p",
              { class: "bs-dl" },
              `${board.arms[best].name} is best`,
              winner.delta ? [" · ", el("b", { text: winner.delta }), ` ${winner.sub}`] : "",
              !winner.delta && winner.sub ? ` · ${winner.sub}` : "",
            );
      card.append(
        el(
          "div",
          { class: row.tip ? "bs-lbm" : "bs-lbm bs-sm" },
          row.tip
            ? tip(row.label, row.tip, "tip-rate-phone")
            : el("span", { class: "bs-lbh", text: row.label }),
          row.cells.map((cell, index) => {
            const name = el("span", { class: "bs-an", text: board.arms[index].name });

            if (cell.na) return [name, el("span", { class: "bs-nr", text: cell.value })];

            const fill = el("i");
            fill.style.width = `${100 * (cell.fraction || 0)}%`;

            return [
              name,
              el("div", { class: "bs-tr" }, fill),
              el("span", {
                class: cell.best ? "bs-v bs-best" : "bs-v",
                text: `${cell.best ? "◆ " : ""}${cell.value}`,
              }),
            ];
          }),
          under,
        ),
      );
    } else {
      list.append(
        el(
          "li",
          {},
          el("span", { class: "bs-ml", text: row.label }),
          el(
            "div",
            { class: board.arms.length > 2 ? "bs-vals bs-four" : "bs-vals" },
            row.cells.map((cell, index) => [
              el("span", { class: "bs-an", text: board.arms[index].name }),
              el(
                "span",
                {},
                valueOf(cell, true),
                cell.err ? el("span", { class: "bs-err", text: ` ${cell.sub}` }) : "",
              ),
            ]),
          ),
        ),
      );
    }
  }

  card.append(list);
  byId("leaderboard").replaceChildren(table, card);
}

function applyTheme(light) {
  document.documentElement.dataset.theme = light ? "light" : "dark";
  const toggle = byId("theme");
  toggle.setAttribute("aria-pressed", String(light));
  setText(toggle, light ? "Dark theme" : "Light theme");
}

function storedTheme() {
  try {
    return localStorage.getItem("benchspec-watch-theme");
  } catch {
    return null;
  }
}

byId("theme").addEventListener("click", () => {
  const light = document.documentElement.dataset.theme !== "light";
  applyTheme(light);

  try {
    localStorage.setItem("benchspec-watch-theme", light ? "light" : "dark");
  } catch {
    // Storage is unavailable: the choice lasts for this page only.
  }
});
applyTheme(storedTheme() === "light");

for (const name of ["matrix", "leaderboard"]) {
  byId(`tab-${name}`).addEventListener("click", () => pickTab(name, false));
}

document.querySelector(".bs-tabs").addEventListener("keydown", (event) => {
  const target = { ArrowLeft: "matrix", Home: "matrix", ArrowRight: "leaderboard", End: "leaderboard" }[
    event.key
  ];

  if (!target) return;
  event.preventDefault();
  pickTab(target, true);
});

byId("matrix").addEventListener("keydown", moveInGrid);
byId("hovercard").addEventListener("mouseleave", dismissHover);
byId("scrim").addEventListener("click", closeDrawer);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    for (const button of document.querySelectorAll(".bs-lgbtn")) button.classList.add("bs-dismissed");
    if (hovered !== null) dismissHover();
    else closeDrawer();
  }

  if (event.key === "Tab" && selected !== null && phone.matches) {
    const stops = [...byId("drawer").querySelectorAll("button:not(:disabled), summary")];
    const edge = event.shiftKey ? stops[0] : stops[stops.length - 1];

    if (document.activeElement === edge || !byId("drawer").contains(document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? stops[stops.length - 1] : stops[0]).focus();
    }
  }
});
document.addEventListener("focusout", (event) => {
  if (event.target.classList && event.target.classList.contains("bs-lgbtn"))
    event.target.classList.remove("bs-dismissed");
});
document.addEventListener("mouseout", (event) => {
  if (event.target.classList && event.target.classList.contains("bs-lgbtn"))
    event.target.classList.remove("bs-dismissed");
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");

  if (!button) return;
  await navigator.clipboard.writeText(byId(button.dataset.copy).textContent);
});

let touchStart = null;
byId("drawer").addEventListener("touchstart", (event) => {
  touchStart = byId("drawer").scrollTop === 0 ? event.touches[0].clientY : null;
});
byId("drawer").addEventListener("touchend", (event) => {
  if (touchStart !== null && phone.matches && event.changedTouches[0].clientY - touchStart > SWIPE_CLOSE_PX)
    closeDrawer();
  touchStart = null;
});

phone.addEventListener("change", schedule);
addEventListener("resize", schedule);
addEventListener("scroll", () => selected !== null && schedule(), { passive: true });
schedule();
