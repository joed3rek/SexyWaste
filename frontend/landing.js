// CityLoom opening page: a scroll-driven portal over a drawing of HSR's real street network, a statement
// fold, a throwable deck of the modules, the pilot's sectors and the SWM Rules 2026 key dates.
// The portal is bound to scroll position (it plays backwards on the way up); entry reveals fire once.
// With prefers-reduced-motion the page renders finished: portal open, nothing moving.

(function () {
  const PILOT_KEY = "hsr";
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Main roads in the amber, the rest in tints of the teal: the page's two accents come from this image.
  const ROAD_COLOUR = { trunk: "#e8913c", primary: "#e8913c", secondary: "#e8913c", tertiary: "#6fb3bb", residential: "#3f8a93", living_street: "#3f8a93" };

  // ---------- The image: HSR's streets, drawn from the street inventory ----------
  let STREETS = [];
  function project(w, h) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    STREETS.forEach((s) => s.coords.forEach(([x, y]) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }));
    const k = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
    const sx = (x1 - x0) * k, sy = y1 - y0;
    const scale = Math.max(w / sx, h / sy) * 1.02;  // cover the frame
    const ox = (w - sx * scale) / 2, oy = (h - sy * scale) / 2;
    return ([x, y]) => [ox + (x - x0) * k * scale, oy + (y1 - y) * scale];
  }
  // ground: the fill behind the streets (the page ground of the current theme; the orb passes null to stay see-through).
  const pageGround = () => getComputedStyle(document.body).getPropertyValue("--canvas").trim() || "#fafafa";
  // mono: draw every road in the theme's ink (greyscale), main roads darker and wider.
  function drawStreets(canvas, zoom = 1, centre = [0.5, 0.5], ground = pageGround(), mono = false) {
    const ink = getComputedStyle(document.body).getPropertyValue("--ink").trim() || "#09090b";
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    if (ground) { c.fillStyle = ground; c.fillRect(0, 0, w, h); }
    if (!STREETS.length) return;
    const p = project(w * zoom, h * zoom);
    const dx = -w * zoom * centre[0] + w / 2, dy = -h * zoom * centre[1] + h / 2;
    c.lineCap = "round";
    ["residential", "living_street", "tertiary", "secondary", "primary", "trunk"].forEach((cls) => {
      const minor = cls === "residential" || cls === "living_street";
      c.strokeStyle = mono ? ink : ROAD_COLOUR[cls];
      c.globalAlpha = mono ? (minor ? 0.35 : cls === "tertiary" ? 0.55 : 0.9) : minor ? 0.8 : 1;
      c.lineWidth = (cls === "trunk" || cls === "primary" || cls === "secondary" ? 2.4 : 1.2) * Math.sqrt(zoom);
      c.beginPath();
      STREETS.filter((s) => s.road_class === cls).forEach((s) => {
        s.coords.forEach((pt, i) => { const [x, y] = p(pt); i ? c.lineTo(x + dx, y + dy) : c.moveTo(x + dx, y + dy); });
      });
      c.stroke();
    });
    c.globalAlpha = 1;
  }

  // ---------- The portal: panels part, the image settles, the title grows while it tightens ----------
  function portal() {
    const hero = document.getElementById("hero");
    const stage = hero.querySelector(".lp-stage");
    const set = (p) => {
      const ease = p * p * (3 - 2 * p);  // smoothstep
      stage.style.setProperty("--open", ease.toFixed(4));
      stage.style.setProperty("--img-s", (1.14 - 0.14 * ease).toFixed(4));
      stage.style.setProperty("--duo", (0.16 * ease).toFixed(4));
      stage.style.setProperty("--title-s", (1 + 0.22 * ease).toFixed(4));
      stage.style.setProperty("--track", `${(0.04 - 0.07 * ease).toFixed(4)}em`);
    };
    if (reduced) { hero.style.height = "100vh"; set(1); return; }
    let ticking = false;
    const update = () => {
      ticking = false;
      const r = hero.getBoundingClientRect();
      const range = r.height - window.innerHeight;
      set(clamp(-r.top / (range || 1), 0, 1));
    };
    window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    window.addEventListener("resize", update);
    update();
  }

  // ---------- Statement: the circular crop drifts and turns with scroll ----------
  function orb() {
    const el = document.getElementById("orb");
    if (reduced) return;
    const sec = el.closest("section");
    const update = () => {
      const r = sec.getBoundingClientRect();
      const t = clamp((window.innerHeight - r.top) / (window.innerHeight + r.height), 0, 1);
      el.style.setProperty("--orb-y", `${(t - 0.5) * -120}px`);
      el.style.setProperty("--orb-r", `${(t - 0.5) * 40}deg`);
    };
    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  // ---------- The deck: the modules as a stack of cards you throw aside ----------
  const MODULES = [
    { code: "CL-01", title: "Survey & baseline", who: "Surveyors", text: "Every building visited: its use, units, floors and how its waste leaves. Survey values replace map guesses; weighed values replace estimates." },
    { code: "CL-02", title: "Garbage mapping", who: "Public · Surveyors", text: "Dumped waste reported on the street it sits on, with photos and severity, and followed from reported to cleared to monitored." },
    { code: "CL-03", title: "Resources", who: "Fleet · HR", text: "The vehicles, people and machinery that actually exist, by sector, with status and shift. No plan uses more than this." },
    { code: "CL-04", title: "Collection cycle", who: "Planners", text: "Which stream is collected from whom, on which days and in which window. The routes for a day carry only what is due." },
    { code: "CL-05", title: "Clean City", who: "Planners", text: "Every street classed and given a cleaning frequency, public bins placed by spacing standards, and the worker-hours it takes." },
    { code: "CL-06", title: "Route builder", who: "Planners", text: "Door-to-door routes, bin and GVP pickups and truck trips, optimised with OR-Tools within the vehicles and crews on record." },
  ];
  function deck() {
    const el = document.getElementById("deck");
    const dots = document.getElementById("deckDots");
    let order = MODULES.map((_, i) => i);
    el.innerHTML = MODULES.map((m, i) => `
      <article class="lp-card" data-i="${i}" role="option" aria-label="${m.title}">
        <div class="code"><span>${m.code} / 06</span><span class="who">${m.who}</span></div>
        <div><span class="rule"></span><h3 style="margin-top:18px">${m.title}</h3><p>${m.text}</p></div>
      </article>`).join("");
    dots.innerHTML = MODULES.map(() => "<i></i>").join("");
    const cards = [...el.querySelectorAll(".lp-card")];
    const layout = () => {
      order.forEach((idx, depth) => {
        const c = cards[idx];
        c.style.zIndex = String(MODULES.length - depth);
        c.style.opacity = depth > 3 ? "0" : "1";
        c.style.transform = `translate(${depth * -8}px, ${depth * -10}px) scale(${1 - depth * 0.04}) rotate(${depth * -1.6}deg)`;
        c.setAttribute("aria-selected", depth === 0 ? "true" : "false");
      });
      [...dots.children].forEach((d, i) => d.classList.toggle("on", i === order[0]));
    };
    // Throw the top card out across the deck's width, then re-stack with the next card on top.
    const throwTop = (dir) => {
      const top = cards[order[0]];
      top.style.transform = `translate(${dir * el.clientWidth * 1.1}px, -40px) rotate(${dir * 18}deg)`;
      top.style.opacity = "0";
      setTimeout(() => { order = [...order.slice(1), order[0]]; layout(); }, reduced ? 0 : 280);
    };
    const back = () => { order = [order[order.length - 1], ...order.slice(0, -1)]; layout(); };
    // Drag: capture the pointer, follow it, throw past a tenth of the deck's width.
    let start = null;
    el.addEventListener("pointerdown", (e) => {
      const top = cards[order[0]];
      if (!top.contains(e.target)) return;
      start = { x: e.clientX, y: e.clientY, id: e.pointerId };
      top.setPointerCapture(e.pointerId);
      top.classList.add("dragging");
    });
    el.addEventListener("pointermove", (e) => {
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x, dy = e.clientY - start.y;
      cards[order[0]].style.transform = `translate(${dx}px, ${dy * 0.3}px) rotate(${dx * 0.06}deg) scale(1.02)`;
    });
    const end = (e) => {
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x;
      cards[order[0]].classList.remove("dragging");
      start = null;
      if (Math.abs(dx) > el.clientWidth * 0.1) throwTop(Math.sign(dx));
      else layout();
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") { e.preventDefault(); throwTop(1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); back(); }
    });
    layout();
  }

  // ---------- Real data: the pilot's sectors and the rules' key dates ----------
  async function data() {
    const fmt = (n) => Number(n).toLocaleString("en-IN");
    try {
      const sum = await apiFetch(`/api/pilots/${PILOT_KEY}/summary`);
      document.getElementById("roster").innerHTML = sum.survey.coverage_by_sector.map((r) => `
        <div class="row lp-reveal"><span class="tag">Pilot sector</span><span class="name">${r.sector}</span><span class="count">${fmt(r.buildings)} buildings</span></div>`).join("");
    } catch { /* without the API the section stays empty */ }
    try {
      const swm = await apiFetch("/api/regulations/swm_rules_2026");
      const today = new Date().toISOString().slice(0, 10);
      const dates = swm.key_deadlines;
      const nextIdx = dates.findIndex((d) => d.date >= today);
      const long = (d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
      document.querySelector("#dateTable tbody").innerHTML = dates.map((d, i) => `
        <tr class="${i === nextIdx ? "next" : ""}"><td>${long(d.date)}</td><td>${d.what}</td><td class="rule">${/^\d/.test(d.rule) ? "r. " : ""}${d.rule}</td></tr>`).join("");
    } catch { /* without the API the table stays empty */ }
    watchReveals();
  }

  // ---------- Entry reveals: once, and only when motion is allowed ----------
  let io = null;
  function watchReveals() {
    if (reduced) return;
    io = io || new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0.15 });
    document.querySelectorAll(".lp-reveal:not(.in)").forEach((el) => io.observe(el));
  }

  // ---------- The accents come out of the photo ----------
  // Sample the photo small; the most saturated warm hue becomes the amber, the most saturated cool hue
  // the teal. Lightness is set so both read on the dark ground. Falls back to the defaults.
  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, sat = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    const h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, sat, l];
  }
  const hsl = (h, sat, l) => `hsl(${Math.round(h)} ${Math.round(sat * 100)}% ${Math.round(l * 100)}%)`;
  function accentsFrom(img) {
    const c = document.createElement("canvas");
    c.width = 64; c.height = Math.max(1, Math.round((64 * img.naturalHeight) / img.naturalWidth));
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, c.width, c.height);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    // Bucket hues in 10-degree bins, weighted by saturation, ignoring greys, near-black and near-white.
    const bins = new Array(36).fill(0), sum = new Array(36).fill(0).map(() => [0, 0, 0]);
    for (let i = 0; i < px.length; i += 4) {
      const [h, sat, l] = rgbToHsl(px[i], px[i + 1], px[i + 2]);
      if (sat < 0.25 || l < 0.12 || l > 0.9) continue;
      const b = Math.floor(h / 10) % 36, w = sat * sat;
      bins[b] += w; sum[b][0] += h * w; sum[b][1] += sat * w; sum[b][2] += l * w;
    }
    const best = (from, to) => {
      let k = -1;
      for (let b = 0; b < 36; b++) { const h = b * 10 + 5; if ((from < to ? h >= from && h < to : h >= from || h < to) && (k < 0 || bins[b] > bins[k])) k = b; }
      return k >= 0 && bins[k] > 0.5 ? [sum[k][0] / bins[k], sum[k][1] / bins[k], sum[k][2] / bins[k]] : null;
    };
    const warm = best(345, 60), cool = best(160, 240);  // reds-oranges-ambers; teals-blues
    const root = document.body.style;
    if (warm) root.setProperty("--lp-amber", hsl(warm[0], clamp(warm[1], 0.6, 0.9), 0.6));
    if (cool) {
      root.setProperty("--lp-teal", hsl(cool[0], clamp(cool[1], 0.35, 0.7), 0.32));
      root.setProperty("--lp-teal-ink", hsl(cool[0], clamp(cool[1], 0.35, 0.7), 0.6));
    }
  }

  // Cover photos: any images in frontend/img/hero/, one picked at random on each load.
  async function photo() {
    try {
      const { photos } = await apiFetch("/api/landing/photos");
      if (!photos.length) return;
      const img = document.getElementById("heroPhoto");
      img.onload = () => {
        img.hidden = false;
        document.getElementById("heroImage").style.visibility = "hidden";
        try { accentsFrom(img); } catch { /* keep the default accents */ }
      };
      img.src = photos[Math.floor(Math.random() * photos.length)];
    } catch { /* keep the street drawing */ }
  }

  async function streets() {
    try {
      STREETS = (await apiFetch(`/api/pilots/${PILOT_KEY}/cleancity/streets`)).streets;
    } catch { STREETS = []; }
    const hero = document.getElementById("heroImage"), circle = document.getElementById("orb");
    const paint = () => { drawStreets(hero); drawStreets(circle, 2.4, [0.55, 0.45], null, true); };
    document.addEventListener("swm:theme", paint);
    paint();
    window.addEventListener("resize", paint);
  }

  function build() {
    if (!reduced) document.documentElement.classList.add("lp-motion");
    portal();
    orb();
    deck();
    watchReveals();
    drawStreets(document.getElementById("heroImage"));
    streets();
    photo();
    data();
  }

  if (typeof UI_READY !== "undefined") UI_READY.then(build);
  else document.addEventListener("DOMContentLoaded", build);
})();
