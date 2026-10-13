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
const LABELS = { goal: "Goal", big_chance: "Big chance", momentum_shift: "Momentum", half_time: "Half-time", full_time: "Full-time" };

let source = null, lastId = 0, ended = false, lowerThirdTimer = null;

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
  const m = s.momentum, bar = $("momentum-bar");
  bar.style.width = `${Math.abs(m) / 2}%`;
  bar.style.left = m >= 0 ? "50%" : `${50 - Math.abs(m) / 2}%`;
  bar.style.background = m >= 0 ? "var(--home)" : "var(--away)";
}

function showMoment(m) {
  const li = document.createElement("li");
  li.className = m.team;
  li.innerHTML = `<time></time><span></span>`;
  li.querySelector("time").textContent = m.clock;
  li.querySelector("span").textContent = m.headline;
  $("moments").prepend(li);

  $("lt-kind").textContent = LABELS[m.kind] || m.kind;
  $("lt-text").textContent = m.headline;
  const lt = $("lower-third");
  lt.hidden = false;
  lt.style.animation = "none";
  void lt.offsetWidth; // restart the slide-in animation
  lt.style.animation = "";
  clearTimeout(lowerThirdTimer);
  lowerThirdTimer = setTimeout(() => (lt.hidden = true), 6000);
}

function stop() {
  if (source) source.close();
  source = null;
  document.body.classList.remove("live");
  $("play").textContent = ended ? "↻ New match" : "▶ Resume";
}

function start() {
  if (ended) reset();
  const params = new URLSearchParams({ seed: $("seed").value || 0, speed: $("speed").value, start: lastId });
  source = new EventSource(`/api/match/stream?${params}`);
  document.body.classList.add("live");
  $("play").textContent = "⏸ Pause";
  source.onmessage = (msg) => {
    const { event, stats, moments } = JSON.parse(msg.data);
    lastId = event.id;
    $("clock").textContent = event.clock;
    drawEvent(event);
    renderStats(stats);
    moments.forEach(showMoment);
  };
  source.addEventListener("end", () => { ended = true; stop(); });
  source.onerror = () => { if (!ended) stop(); };
}

function reset() {
  ended = false;
  lastId = 0;
  $("moments").innerHTML = "";
  $("trail").innerHTML = "";
  $("lower-third").hidden = true;
  moveBall([W / 2, H / 2]);
}

$("play").addEventListener("click", () => (source ? stop() : start()));
$("seed").addEventListener("change", () => { stop(); reset(); $("play").textContent = "▶ Kick off"; });
$("speed").addEventListener("change", () => { if (source) { stop(); start(); } });

fetch("/api/teams").then((r) => r.json()).then((t) => {
  $("home-short").textContent = t.home.short;
  $("away-short").textContent = t.away.short;
  $("home-name").textContent = t.home.name;
  $("away-name").textContent = t.away.name;
});
