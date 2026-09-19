/* Renders dashboard.html headlessly against the real league_history.json, once as a
   desktop and once as a 390px phone, and checks the things that actually break on a
   small screen: chart viewBox width, x-label crowding, table scrollers, the team
   picker, and tap-to-pin tooltips. Run: node test_mobile.js */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const HTML = fs.readFileSync("dashboard.html", "utf8");
const DATA = JSON.parse(fs.readFileSync("league_history.json", "utf8"));

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`  ok   ${name}${extra ? "   " + extra : ""}`); }
  else { fail++; console.log(`  FAIL ${name}${extra ? "   " + extra : ""}`); }
};

function boot(width) {
  const html = HTML.replace("/*__EMBEDDED_DATA__*/null", JSON.stringify(DATA));
  // the page script runs at parse time, so the stubs have to be in place first
  return new JSDOM(html, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(win) {
      // jsdom ships no matchMedia and no layout engine; both are faked at a fixed width
      win.matchMedia = q => {
        const m = /max-width:\s*(\d+)px/.exec(q);
        return { matches: m ? width <= +m[1] : false, media: q,
                 addEventListener() {}, removeEventListener() {}, addListener() {} };
      };
      Object.defineProperty(win.HTMLElement.prototype, "offsetWidth",
        { configurable: true, get() { return 170; } });
      win.Element.prototype.getBoundingClientRect = function () {
        return { left: 0, top: 0, width, height: width, right: width, bottom: width };
      };
    }
  });
}

// jsdom has no PointerEvent, so a MouseEvent carries the pointerType instead
function fire(win, el, type, x, y, pointerType) {
  const e = new win.MouseEvent(type, { clientX: x, clientY: y, bubbles: true });
  Object.defineProperty(e, "pointerType", { value: pointerType });
  el.dispatchEvent(e);
}
function tap(win, el, x, y, pointerType = "touch") {
  fire(win, el, "pointerdown", x, y, pointerType);
  fire(win, el, "pointerup", x, y, pointerType);
}

function run(label, width) {
  console.log(`\n--- ${label} (${width}px) ---`);
  const dom = boot(width);
  const d = dom.window.document;

  // the script runs at parse time; boot() is called from the EMBEDDED branch
  ok("boots without throwing", d.querySelectorAll("#body .panel").length > 0,
     `${d.querySelectorAll("#body .panel").length} panels`);

  const svgs = [...d.querySelectorAll("#body svg.chart")];
  ok("charts rendered", svgs.length >= 2, `${svgs.length} charts`);

  const boxes = svgs.map(s => +s.getAttribute("viewBox").split(" ")[2]);
  if (width <= 700) {
    ok("charts use the narrow viewBox", boxes.every(w => w <= 420),
       `widths ${[...new Set(boxes)].join(", ")}`);
    ok("mobile chart class applied", svgs.every(s => s.classList.contains("mob")));
  } else {
    ok("charts use the wide viewBox", boxes.some(w => w === 1000),
       `widths ${[...new Set(boxes)].join(", ")}`);
    ok("no mobile chart class", svgs.every(s => !s.classList.contains("mob")));
  }

  // x labels must not be packed tighter than they can render
  let tightest = Infinity;
  svgs.forEach(s => {
    const xs = [...s.querySelectorAll("text.axis")]
      .filter(t => t.getAttribute("text-anchor") === "middle" && t.getAttribute("x"))
      .map(t => +t.getAttribute("x")).sort((a, b) => a - b);
    const box = +s.getAttribute("viewBox").split(" ")[2];
    for (let i = 1; i < xs.length; i++) {
      const gapCss = (xs[i] - xs[i - 1]) * (width / box);   // gap in real pixels
      if (gapCss > 0.5) tightest = Math.min(tightest, gapCss);
    }
  });
  ok("x labels have room to breathe", tightest === Infinity || tightest >= 20,
     `tightest gap ${tightest === Infinity ? "n/a" : tightest.toFixed(1) + "px"}`);

  // effective on-screen size of axis text, which is the whole point of the exercise
  const axisPx = svgs.map(s => {
    const box = +s.getAttribute("viewBox").split(" ")[2];
    return (s.classList.contains("mob") ? 13 : 11) * (width / box);
  });
  ok("axis text is legible", axisPx.every(p => p >= 9),
     `${Math.min(...axisPx).toFixed(1)}px smallest`);

  return { dom, d };
}

console.log("=== dashboard.html: layout across breakpoints ===");
const desktop = run("desktop", 1400);
const phone = run("phone", 390);

// ---- phone-only structure ----
console.log("\n--- phone: picker and tables ---");
{
  const d = phone.d, w = phone.dom.window;
  const rail = d.getElementById("rail");
  const tog = d.getElementById("railToggle");
  ok("team list starts collapsed", !rail.classList.contains("open"));
  ok("picker names the empty state", /pick one/i.test(d.getElementById("railWho").textContent));
  tog.onclick();
  ok("picker opens on tap", rail.classList.contains("open"));

  const rows = [...rail.querySelectorAll(".trow")];
  ok("every team is in the list", rows.length === DATA.data[DATA.seasons.at(-1)].teams.length,
     `${rows.length} teams`);
  rows[0].onclick();
  ok("picking a team collapses the list", !d.getElementById("rail").classList.contains("open"));
  ok("picker now names the pick",
     !/pick one/i.test(d.getElementById("railWho").textContent),
     d.getElementById("railWho").textContent);

  // manager names only, never ESPN team names
  const names = [...d.querySelectorAll("#rail .trow .nm")].map(n => n.firstChild.textContent);
  ok("teams show as manager 'First L'", names.every(n => /^[^ ]+ [A-Z]$/.test(n)),
     names.slice(0, 3).join(" / "));
}

console.log("\n--- phone: wide tables scroll ---");
{
  const d = phone.d;
  d.getElementById("vLeague").onclick();
  const tables = [...d.querySelectorAll("#body table")];
  const unwrapped = tables.filter(t => !t.closest(".scrollx"));
  ok("every league table sits in a scroller", unwrapped.length === 0,
     `${tables.length} tables, ${unwrapped.length} bare`);

  const head = [...d.querySelectorAll("#body table thead th")].map(t => t.textContent);
  ok("standings leads with the manager column", head[0] === "Team", `first col "${head[0]}"`);

  d.getElementById("vAdv").onclick();
  // narrow tables are allowed to sit bare; anything wide has to scroll
  const adv = [...d.querySelectorAll("#body table")];
  const wideBare = adv.filter(t => t.querySelectorAll("thead th").length > 5
                                   && !t.closest(".scrollx"));
  ok("every wide advanced table scrolls", wideBare.length === 0,
     `${adv.length} tables, ${wideBare.length} wide and bare`);
}

console.log("\n--- phone: team view and scatter ---");
{
  const d = phone.d, w = phone.dom.window;
  d.getElementById("railToggle").onclick();
  // a row already selected would toggle back off, so pick one that is not
  [...d.querySelectorAll("#rail .trow")]
    .find(r => r.getAttribute("aria-pressed") === "false").onclick();
  d.getElementById("vTeam").onclick();
  ok("team view renders for a pick", d.querySelectorAll("#body .panel").length >= 3,
     `${d.querySelectorAll("#body .panel").length} panels`);
  ok("headline stats present", d.querySelectorAll("#body .stat").length >= 6,
     `${d.querySelectorAll("#body .stat").length} stats`);
  const tw = d.querySelector("#body svg.chart");
  ok("team chart is mobile-sized", tw && tw.classList.contains("mob") &&
     +tw.getAttribute("viewBox").split(" ")[2] === 400);
  const wide = [...d.querySelectorAll("#body table")]
    .filter(t => t.querySelectorAll("thead th").length > 5 && !t.closest(".scrollx"));
  ok("ledger and career tables scroll", wide.length === 0, `${wide.length} wide and bare`);

  d.getElementById("vAdv").onclick();
  const sc = [...d.querySelectorAll("#body svg.chart")]
    .find(s => /Draft slot/.test(s.getAttribute("aria-label") || ""));
  if (sc) {
    const [, , vw, vh] = sc.getAttribute("viewBox").split(" ").map(Number);
    ok("scatter uses the narrow viewBox", vw === 360, `${vw}x${vh}`);
    const plotW = vw - 56 - 10, plotH = vh - 10 - 56;
    ok("scatter plot area stays square", Math.abs(plotW - plotH) < 0.5,
       `${plotW} x ${plotH}`);
    ok("scatter has finger-sized targets", sc.querySelectorAll(".hitdot").length > 0,
       `${sc.querySelectorAll(".hitdot").length} targets`);
    ok("scatter y label shortened for the margin", /Best/.test(sc.textContent));
  } else {
    ok("scatter present", false, "no draft scatter rendered");
  }
}

console.log("\n--- desktop: standings order unchanged ---");
{
  const d = desktop.d;
  const head = [...d.querySelectorAll("#body table thead th")].map(t => t.textContent);
  ok("finish still leads on desktop", head[0] === "#" && head[1] === "Team",
     head.slice(0, 3).join(" | "));
}

console.log("\n--- tap to pin ---");
{
  const d = phone.d, w = phone.dom.window;
  d.getElementById("vLeague").onclick();
  const wrap = d.querySelector("#body .chartwrap");
  const ov = wrap.querySelector('rect[id^="ov_"]');
  const tip = wrap.querySelector(".tip");
  ok("tooltip hidden to begin with", tip.style.opacity !== "1");
  tap(w, ov, 200, 120);
  ok("a tap pins the tooltip", tip.style.opacity === "1" && tip.classList.contains("pinned"));
  ok("pinned tooltip is tappable", /pinned/.test(tip.className));
  ok("tooltip lists the week", /W\d/.test(tip.textContent), tip.querySelector(".th").textContent.trim());
  ok("rows carry a team key", tip.querySelectorAll(".tr[data-key]").length > 0);

  const before = tip.textContent;
  tap(w, ov, 200, 120);
  ok("tapping the same spot unpins", tip.style.opacity === "0");

  // a drag is a page scroll, not a tap
  tap(w, ov, 200, 120);
  tip.classList.remove("pinned"); tip.style.opacity = "0";
  fire(w, ov, "pointerdown", 100, 100, "touch");
  fire(w, ov, "pointerup", 100, 240, "touch");
  ok("a vertical drag does not pin", tip.style.opacity !== "1");
}

console.log("\n--- desktop: click a line to follow it ---");
{
  const d = desktop.d, w = desktop.dom.window;
  d.getElementById("vLeague").onclick();
  const wrap = d.querySelector("#body .chartwrap");
  const ov = wrap.querySelector('rect[id^="ov_"]');
  const before = d.querySelectorAll("#rail .trow[aria-pressed='true']").length;
  ok("nothing selected to start", before === 0);
  // aim at a point that is actually on a line: read one back off the svg
  const dot = wrap.querySelector("path.line");
  const first = dot.getAttribute("d").slice(1).split(/[ ,L]/);
  const x = +first[0], y = +first[1];
  const box = +wrap.querySelector("svg").getAttribute("viewBox").split(" ")[2];
  const k = 1400 / box;
  tap(w, ov, x * k, y * k, "mouse");
  ok("clicking near a line selects that team",
     d.querySelectorAll("#rail .trow[aria-pressed='true']").length === 1);
}

console.log(`\n${fail === 0 ? "ALL CHECKS PASSED" : fail + " CHECK(S) FAILED"}  (${pass} passed)`);
process.exit(fail === 0 ? 0 : 1);
