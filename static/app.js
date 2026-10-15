// Live match screen: consumes /api/match/stream and renders a broadcast-style view.
const $ = (id) => document.getElementById(id);
const W = 105, H = 68;
const STAT_ROWS = [
  ["Possession", (t) => t.possession_pct, (v) => `${v}%`],
  ["xG", (t) => t.xg, (v) => v.toFixed(2)],
  ["Shots", (t) => t.shots, String],
  ["On target", (t) => t.on_target, String],
  ["Pass accuracy", (t) => t.pass_accuracy_pct, (v) => `${v}%`],
  ["Final-third passes", (t) => t.final_third_passes, String],
  ["Pressures", (t) => t.pressures, String],
  ["Tackles won", (t) => t.tackles, String],
  ["Fastest shot", (t) => t.top_shot_kmh, (v) => (v ? `${v} km/h` : "–")],
];
const LABELS = { goal: "Goal", big_chance: "Big chance", momentum_shift: "Momentum", flow_shift: "Control vs chaos", half_time: "Half-time", full_time: "Full-time", player_focus: "Player cam" };
const STAT_NAMES = {
  possession_pct: "Possession %", xg: "Expected goals (xG)", shots: "Shots", on_target: "Shots on target",
  pass_accuracy_pct: "Pass accuracy %", final_third_passes: "Final-third passes", pressures: "Pressures", tackles: "Tackles won",
};

let source = null, lastId = 0, ended = false, lowerThirdTimer = null, graphicTimer = null, teamsInfo = null;

// Chaos index runs roughly 12–50 in practice; map that span onto the meter.
const flowPos = (chaos) => Math.min(Math.max(((chaos - 12) / 38) * 100, 0), 100);

// Agent text is model output: always insert it as text, never as HTML.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// Event coordinates are normalised to the acting team attacking towards x=105; home attacks right on screen.
const abs = (side, x, y) => (side === "home" ? [x, y] : [W - x, H - y]);

function line(cls, [x1, y1], [x2, y2]) {
  const l = document.createElementNS("http://www.w3.org/2000/svg", "line");
  Object.entries({ x1, y1, x2, y2 }).forEach(([k, v]) => l.setAttribute(k, v));
  l.setAttribute("class", cls);
  const trail = $("trail");
  trail.appendChild(l);
  while (trail.childNodes.length > 6) trail.firstChild.remove();
  [...trail.childNodes].forEach((n, i, all) => n.setAttribute("opacity", (i + 1) / all.length));
}

function moveBall([x, y]) {
  $("ball").setAttribute("cx", x);
  $("ball").setAttribute("cy", y);
}

function drawEvent(e) {
  if (e.type === "pass" || e.type === "carry") {
    const from = abs(e.team, e.x, e.y), to = abs(e.team, e.end_x, e.end_y);
    line(`${e.team}${e.outcome === "incomplete" ? " fail" : ""}`, from, to);
    moveBall(to);
  } else if (e.type === "shot") {
    const from = abs(e.team, e.x, e.y);
    line("shot", from, abs(e.team, W, H / 2 + (e.outcome === "off_target" ? 6 : 0)));
    moveBall(from);
  } else if ("x" in e) {
    moveBall(abs(e.team, e.x, e.y));
  }
}

function renderStats(s) {
  const [h, a] = [s.teams.home, s.teams.away];
  $("home-goals").textContent = h.goals;
  $("away-goals").textContent = a.goals;
  $("stats").innerHTML = STAT_ROWS.map(([label, get, fmt]) => {
    const hv = get(h), av = get(a), total = hv + av || 1;
    return `<div class="stat"><div class="stat-row"><b>${fmt(hv)}</b><span>${label}</span><b>${fmt(av)}</b></div>
      <div class="stat-bar"><div class="home" style="width:${(100 * hv) / total}%"></div><div class="away" style="width:${(100 * av) / total}%"></div></div></div>`;
  }).join("");
  const f = s.flow;
  $("stats").insertAdjacentHTML("beforeend", `<div class="stat"><div class="stat-row"><b>${f.press_per_min.home}</b>
    <span>Pressing / min (last 5')</span><b>${f.press_per_min.away}</b></div></div>
    <div class="stat"><div class="stat-row"><span>Tempo</span><b>${f.tempo_ppm} passes / min</b></div></div>`);
  $("flow-state").textContent = f.state;
  $("flow-marker").style.left = `${flowPos(f.chaos)}%`;
  const m = s.momentum, bar = $("momentum-bar");
  bar.style.width = `${Math.abs(m) / 2}%`;
  bar.style.left = m >= 0 ? "50%" : `${50 - Math.abs(m) / 2}%`;
  bar.style.background = m >= 0 ? "var(--home)" : "var(--away)";
}

function showMoment(m) {
  const li = document.createElement("li");
  li.className = m.team;
  li.id = `m-${m.id}`;
  li.innerHTML = `<time></time><div><b></b><p class="commentary pending">Agents are on it…</p><p class="analysis" hidden></p></div>`;
  li.querySelector("time").textContent = m.clock;
  li.querySelector("b").textContent = m.headline;
  $("moments").prepend(li);
  lowerThird(LABELS[m.kind] || m.kind, m.headline, m.id);
}

// Spoken commentary (Azure AI Speech): lines queue up so they never talk over each other.
const voiceQueue = [];
let speaking = false, currentAudio = null;

function say(text, language, token) {
  if (!$("voice").checked || !token) return;
  voiceQueue.push({ text, language, token });
  while (voiceQueue.length > 2) voiceQueue.shift(); // drop stale lines rather than fall behind the match
  if (!speaking) playNext();
}

async function playNext() {
  const next = voiceQueue.shift();
  if (!next) { speaking = false; return; }
  speaking = true;
  try {
    const r = await fetch("/api/voice", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
    });
    if (!r.ok) throw new Error(r.statusText);
    const url = URL.createObjectURL(await r.blob());
    const audio = currentAudio = new Audio(url);
    audio.onended = audio.onerror = () => { URL.revokeObjectURL(url); playNext(); };
    await audio.play();
  } catch {
    playNext();
  }
}

// A badge that opens the Fact-Checker's evidence: where every number and player in the line comes from.
function evidenceBadge(label, cls, evidence, title = "") {
  const badge = document.createElement("button");
  badge.type = "button";
  badge.className = `badge ${cls}`;
  badge.textContent = label;
  badge.title = title;
  const list = document.createElement("ul");
  list.className = "evidence";
  list.hidden = true;
  if (!evidence?.length) {
    badge.disabled = true;
    return [badge, list];
  }
  badge.textContent += " ▾";
  badge.setAttribute("aria-expanded", "false");
  for (const e of evidence) {
    const li = document.createElement("li");
    li.innerHTML = "<b></b> <span></span>";
    li.querySelector("b").textContent = e.claim;
    li.querySelector("span").textContent = `← ${e.source}`;
    list.append(li);
  }
  badge.addEventListener("click", () => {
    list.hidden = !list.hidden;
    badge.setAttribute("aria-expanded", String(!list.hidden));
  });
  return [badge, list];
}

// The agents answer a few seconds after the moment; the newest moment's lower-third gets the line.
function showCommentary(c) {
  const li = document.getElementById(`m-${c.moment_id}`);
  if (!li) return;
  const p = li.querySelector(".commentary");
  p.textContent = c.text;
  p.classList.remove("pending");
  const title = c.rejected ? `Fact-Checker rejected: ${c.rejected.join(", ")}` : c.error || "";
  p.append(" ", ...evidenceBadge(c.source === "ai" ? "✓ AI · fact-checked" : "template", c.source, c.evidence, title));
  if (c.analysis) {
    const a = li.querySelector(".analysis");
    a.textContent = `Why it matters: ${c.analysis}`;
    a.hidden = false;
  }
  if ($("producer").checked) holdForProducer(li, c);
  else putOnAir(c, false);
}

// Lower-third text, the Director's graphic and the voice go out only once a line is on air.
function putOnAir(c, approved) {
  if (c.moment_id.endsWith(":full_time")) {
    $("recap").textContent = c.text;
    $("recap-box").hidden = false;
  } else if (approved) {
    lowerThird(LABELS[c.moment_id.split(":")[1]] || "On air", c.text, c.moment_id);
  } else if ($("lower-third").dataset.moment === c.moment_id && !$("lower-third").hidden) {
    $("lt-text").textContent = c.text;
  }
  if (c.overlay) renderGraphic(c.overlay);
  say(c.text, c.voice_language, c.voice_token);
}

// Producer mode: the AI's line waits as a draft until a human sends it to air or drops it.
function holdForProducer(li, c) {
  li.classList.add("draft");
  const bar = document.createElement("div");
  bar.className = "review";
  bar.innerHTML = `<button type="button" class="air">✓ On air</button><button type="button" class="drop">✕ Drop</button>`;
  const decide = (aired) => {
    li.classList.remove("draft");
    li.classList.toggle("dropped", !aired);
    bar.textContent = aired ? "🎬 Approved by producer" : "🎬 Dropped by producer";
    bar.className = "review done";
    if (aired) putOnAir(c, true);
  };
  bar.querySelector(".air").addEventListener("click", () => decide(true));
  bar.querySelector(".drop").addEventListener("click", () => decide(false));
  li.querySelector(".commentary").after(bar);
}

// The Director agent's overlay: graphic type, title and on-screen duration, data from the evidence.
function renderGraphic(o) {
  const d = o.data, short = (side) => esc(teamsInfo ? teamsInfo[side].short : side);
  let body;
  if (o.graphic === "score_update") {
    body = `<div class="g-big">${short("home")} ${d.score.home}–${d.score.away} ${short("away")}</div>
      <div>${esc(d.player || "")}${d.xg != null ? ` · xG ${d.xg.toFixed(2)}` : ""}${d.speed_kmh ? ` · ${d.speed_kmh} km/h` : ""}</div>`;
  } else if (o.graphic === "stat_compare") {
    const { home, away } = d.values, total = home + away || 1;
    body = `<div class="g-label">${esc(STAT_NAMES[o.stat] || o.stat)}</div>
      <div class="g-compare"><b class="home">${home}</b><div class="stat-bar"><div class="home" style="width:${(100 * home) / total}%"></div><div class="away" style="width:${(100 * away) / total}%"></div></div><b class="away">${away}</b></div>`;
  } else if (o.graphic === "player_card") {
    const p = d.player;
    body = `<div class="g-big ${esc(p.team)}">${esc(p.name)}</div>
      <div class="g-grid"><span>Passes<b>${p.passes_completed}/${p.passes}</b></span><span>Shots<b>${p.shots}</b></span>
      <span>xG<b>${Number(p.xg).toFixed(2)}</b></span><span>Goals<b>${p.goals}</b></span></div>`;
  } else if (o.graphic === "flow") {
    const f = d.flow;
    body = `<div class="g-big">${esc(f.state)}</div>
      <div class="flow-track"><div id="g-flow-marker" style="position:absolute;top:-3px;width:4px;height:14px;background:#fff;border-radius:2px;left:${flowPos(f.chaos)}%"></div></div>
      <div class="g-grid"><span>Chaos index<b>${f.chaos}</b></span><span>Tempo<b>${f.tempo_ppm}/min</b></span>
      <span>${short("home")} press<b>${f.press_per_min.home}/min</b></span><span>${short("away")} press<b>${f.press_per_min.away}/min</b></span></div>`;
  } else {
    const m = d.momentum;
    body = `<div class="g-label">Momentum · last 5 min</div>
      <div class="momentum-track g-momentum"><div style="position:absolute;top:0;bottom:0;left:${m >= 0 ? 50 : 50 - Math.abs(m) / 2}%;width:${Math.abs(m) / 2}%;background:var(--${m >= 0 ? "home" : "away"})"></div></div>`;
  }
  const g = $("graphic");
  g.innerHTML = `<div class="g-title">${esc(o.title)}</div>${body}`;
  g.hidden = false;
  clearTimeout(graphicTimer);
  graphicTimer = setTimeout(() => (g.hidden = true), o.duration_s * 1000);
}

function renderFocus(f) {
  const cam = $("player-cam");
  cam.hidden = false;
  cam.innerHTML = `<div class="g-label">Player cam</div><b>${esc(f.name)}</b>
    <div class="g-grid"><span>Passes<b>${f.passes_completed ?? 0}/${f.passes ?? 0}</b></span><span>Shots<b>${f.shots ?? 0}</b></span>
    <span>xG<b>${Number(f.xg ?? 0).toFixed(2)}</b></span><span>Goals<b>${f.goals ?? 0}</b></span></div>`;
}

function lowerThird(kind, text, momentId) {
  $("lt-kind").textContent = kind;
  $("lt-text").textContent = text;
  const lt = $("lower-third");
  lt.dataset.moment = momentId;
  lt.hidden = false;
  lt.style.animation = "none";
  void lt.offsetWidth; // restart the slide-in animation
  lt.style.animation = "";
  clearTimeout(lowerThirdTimer);
  lowerThirdTimer = setTimeout(() => (lt.hidden = true), 8000);
}

function stop() {
  if (source) source.close();
  source = null;
  document.body.classList.remove("live");
  $("play").textContent = ended ? "↻ New match" : "▶ Resume";
}

function start() {
  if (ended) reset();
  const params = new URLSearchParams({
    seed: $("seed").value || 0, speed: $("speed").value, start: lastId,
    mode: $("mode").value, favorite: $("favorite").value, language: $("language").value, player: $("player").value,
  });
  source = new EventSource(`/api/match/stream?${params}`);
  document.body.classList.add("live");
  $("play").textContent = "⏸ Pause";
  source.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.commentary) return showCommentary(data.commentary);
    const { event, stats, moments } = data;
    lastId = event.id;
    $("clock").textContent = event.clock;
    drawEvent(event);
    renderStats(stats);
    moments.forEach(showMoment);
    if (data.focus) renderFocus(data.focus);
  };
  source.addEventListener("end", () => { ended = true; stop(); });
  source.onerror = () => { if (!ended) stop(); };
}

function reset() {
  ended = false;
  lastId = 0;
  $("moments").innerHTML = "";
  $("trail").innerHTML = "";
  ["lower-third", "graphic", "recap-box", "player-cam"].forEach((id) => ($(id).hidden = true));
  $("clock").textContent = "0:00";  // a new match starts from a clean scorebug
  $("home-goals").textContent = $("away-goals").textContent = "0";
  moveBall([W / 2, H / 2]);
}

// Ask the Match: the agent answers from the match as it stands at the last event on screen.
$("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const box = $("ask-answer"), button = $("ask-form").querySelector("button");
  box.hidden = false;
  box.className = "ask-answer pending";
  box.textContent = "Asking the data desk…";
  button.disabled = true;
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: $("ask-input").value, seed: Number($("seed").value) || 0, until_event: lastId,
        mode: $("mode").value, favorite: $("favorite").value, language: $("language").value, player: $("player").value,
      }),
    });
    const a = await r.json();
    if (!r.ok) throw new Error(typeof a.detail === "string" ? a.detail : r.statusText);
    box.className = "ask-answer";
    box.textContent = a.answer;
    const [badge, evidence] = evidenceBadge(a.source === "ai" ? "✓ AI · fact-checked" : a.source,
      a.source === "ai" ? "ai" : "template", a.evidence);
    box.append(" ", badge);
    if (a.tools?.length) {
      const tools = document.createElement("span");
      tools.className = "tools";
      const by = a.engine === "foundry" ? "Microsoft Foundry agent" : "Agent Framework agent";
      tools.textContent = `${by} · data via MCP: ${a.tools.join(", ")}`;
      box.append(tools);
    }
    box.append(evidence);
    say(a.answer, a.voice_language, a.voice_token);
  } catch (err) {
    box.className = "ask-answer";
    box.textContent = `Could not ask: ${err.message}`;
  } finally {
    button.disabled = false;
  }
});

$("voice").addEventListener("change", () => {
  if ($("voice").checked) return;
  voiceQueue.length = 0;
  currentAudio?.pause();
  speaking = false;
});

$("play").addEventListener("click", () => (source ? stop() : start()));
$("seed").addEventListener("change", () => { stop(); reset(); $("play").textContent = "▶ Kick off"; });
["speed", "mode", "favorite", "language", "player"].forEach((id) =>
  $(id).addEventListener("change", () => {
    if (id === "player" && !$("player").value) $("player-cam").hidden = true;
    if (source) { stop(); start(); }
  }));

fetch("/api/teams").then((r) => r.json()).then((t) => {
  teamsInfo = t;
  for (const side of ["home", "away"]) {
    const group = document.createElement("optgroup");
    group.label = t[side].name;
    t[side].players.forEach((p) => group.append(new Option(`${p.number} · ${p.name}`, p.name)));
    $("player").append(group);
  }
  $("home-short").textContent = t.home.short;
  $("away-short").textContent = t.away.short;
  $("fav-home").textContent = t.home.name;
  $("fav-away").textContent = t.away.name;
  $("home-name").textContent = t.home.name;
  $("away-name").textContent = t.away.name;
});
