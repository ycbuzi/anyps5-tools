// Layout audit for the AnyPS5 screen, injected by selftest.cs (and usable in any browser console on index.html).
// uiAudit(name) returns { name, small: [...], cut: [...], overlap: [...] } for what is on screen now:
//   small   - visible text under 20px at 1080p scale (PS5 smallest text is ~20-24px)
//   cut     - visible blocks that run past the window edges
//   overlap - main blocks that collide (title/buttons/side card/shelf/tile name, control center cards vs controls)
window.uiAudit = function (name) {
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const shown = e => { for (let p = e; p && p !== document.body; p = p.parentElement) { const c = getComputedStyle(p); if (c.display === "none" || c.visibility === "hidden" || c.opacity === "0") return false; } return true; };
  const out = { name, rem: Math.round(rem * 100) / 100, small: [], cut: [], overlap: [] };
  const skip = "#legend b, .tile .badge, .me, .av2, .ph, .lt, .padg";
  for (const e of document.querySelectorAll("body *")) {
    const r = e.getBoundingClientRect(); if (!r.width || !r.height || !shown(e)) continue;
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && !e.closest(skip) && parseFloat(getComputedStyle(e).fontSize) < 11) out.small.push((e.id ? "#" + e.id : e.tagName.toLowerCase()) + " " + parseFloat(getComputedStyle(e).fontSize).toFixed(1) + "px on screen '" + e.textContent.trim().slice(0, 20) + "'");
    if (own && !e.closest(skip)) { const px = parseFloat(getComputedStyle(e).fontSize) / rem * 16; if (px < 19.5) out.small.push((e.id ? "#" + e.id : e.tagName.toLowerCase() + (e.className ? "." + String(e.className).split(" ")[0] : "")) + " " + Math.round(px) + "px '" + e.textContent.trim().slice(0, 20) + "'"); }
  }
  const blocks = ["#gTitle", "#acts", "#side", "#shelf", "#tname", ".top", "#ccards", "#cicons", "#cchint", "#ssec", "#slist", ".lhead", "#ltabs", "#lcount", "#lsort", ".plist", "#profiles h1", "#profiles h2", ".hero", "#legend"];
  const vis = blocks.map(q => [q, document.querySelector(q)]).filter(([q, e]) => e && shown(e) && e.getBoundingClientRect().width > 0);
  for (const [q, e] of vis) {
    const r = e.getBoundingClientRect();
    if (/ccards|slist|libgrid|track/.test(q)) continue;
    if (r.bottom > innerHeight + 1 || r.right > innerWidth + 1 || r.left < -1 || r.top < -1) out.cut.push(q + " " + Math.round(r.left) + "," + Math.round(r.top) + "-" + Math.round(r.right) + "," + Math.round(r.bottom));
  }
  const hit = (a, b) => !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top);
  const pairs = [["#gTitle", "#acts"], ["#acts", "#shelf"], ["#side", "#shelf"], ["#side", "#gTitle"], ["#side", "#acts"], [".hero", "#tname"], ["#ccards", "#cicons"], ["#cchint", "#cicons"], ["#ssec", "#slist"], ["#lcount", "#lsort"], ["#profiles h2", ".plist"], ["#shelf", "#legend"], ["#hubhead", "#gTitle"], ["#menu", "#ccards"], ["#menu", "#cicons"], ["#menu", ".ci.sel .lbl"], ["#ccclock", "#ccards"], ["#hubrows", "#acts"], ["#side", "#hubrows"]];
  for (const [a, b] of pairs) {
    const A = document.querySelector(a), B = document.querySelector(b);
    if (!A || !B || !shown(A) || !shown(B)) continue;
    const ra = A.getBoundingClientRect(), rb = B.getBoundingClientRect();
    if (ra.width && rb.width && hit(ra, rb)) out.overlap.push(a + " x " + b);
  }
  const cards = document.querySelectorAll("#ccards .ccard");
  const icons = document.getElementById("cicons");
  if (cards.length && icons && shown(icons)) { const ib = icons.getBoundingClientRect(); for (const c of cards) if (shown(c) && c.getBoundingClientRect().bottom > ib.top - 4) { out.overlap.push("control center card x controls"); break; } }
  const leg = document.getElementById("legend");
  if (leg && shown(leg) && leg.getBoundingClientRect().width) { const lr = leg.getBoundingClientRect(); for (const c of document.querySelectorAll(".hcard, .card")) { if (!shown(c)) continue; const cr = c.getBoundingClientRect(); if (!cr.width || !hit(cr, lr)) continue; const x = (Math.max(cr.left, lr.left) + Math.min(cr.right, lr.right)) / 2, y = (Math.max(cr.top, lr.top) + Math.min(cr.bottom, lr.bottom)) / 2, at = document.elementFromPoint(x, y); if (at && c.contains(at)) { out.overlap.push("card x #legend '" + c.textContent.trim().slice(0, 16) + "'"); break; } } }
  for (const c of document.querySelectorAll(".card, .hcard, .ccard, .srow, .mi, .tile .badge")) {
    if (!shown(c)) continue; const cr = c.getBoundingClientRect(); if (!cr.width || cr.bottom < 0 || cr.top > innerHeight) continue;
    for (const k of c.querySelectorAll("small, b, span, div")) { if (!shown(k)) continue; const kr = k.getBoundingClientRect(); if (!kr.width || !kr.height) continue; if (kr.top < cr.top - 1 || kr.bottom > cr.bottom + 1 || kr.left < cr.left - 1 || kr.right > cr.right + 1) { out.overlap.push("text spills out of its card '" + (k.textContent || "").trim().slice(0, 18) + "'"); break; } if (!k.classList.contains("big") && k.scrollHeight > k.clientHeight + 4 && getComputedStyle(k).overflow === "visible") { out.overlap.push("text overflows '" + (k.textContent || "").trim().slice(0, 18) + "'"); break; } }
    if (out.overlap.length > 6) break;
  }
  // covered: every visible button must be what a click at its centre lands on. With an overlay open, only its buttons count.
  out.covered = [];
  const layer = ["#viewer.on", "#help.on", "#notes.on", "#ginfo.on", "#osk.on", "#pin.on", "#menu.on", "#create.on", "#cc.on"].map(q => document.querySelector(q)).find(Boolean);
  const btns = ".hcard, .tile, .play, .more, .ib, .tabs span, .card, .side, .ccard, .ci, .srow:not(.info), .ssec div, .lt, .lsortbtn, #ltabs span, .prof, .mi, #keys span, .osk span, .vnav";
  for (const b of document.querySelectorAll(btns)) {
    if (!shown(b)) continue;
    if (layer && !layer.contains(b)) continue;
    if (!layer && b.closest(".ov, .menu, .cc")) continue;
    const r = b.getBoundingClientRect(); if (!r.width || r.right < 0 || r.bottom < 0 || r.left > innerWidth || r.top > innerHeight) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
    let clip = b.parentElement, hidden = false;
    for (; clip && clip !== document.body; clip = clip.parentElement) { const o = getComputedStyle(clip).overflowY; if (o === "auto" || o === "hidden" || o === "scroll") { const c = clip.getBoundingClientRect(); if (y < c.top || y > c.bottom || x < c.left || x > c.right) { hidden = true; break; } } }
    if (hidden) continue;
    const at = document.elementFromPoint(x, y);
    if (at && !b.contains(at)) out.covered.push((b.id || String(b.className).split(" ")[0]) + " '" + (b.textContent || "").trim().slice(0, 14) + "' under " + (at.id || String(at.className).split(" ")[0] || at.tagName));
  }
  out.covered = out.covered.slice(0, 6);
  const remPx = parseFloat(getComputedStyle(document.documentElement).fontSize);
  for (const t of document.querySelectorAll(".tg, .ntg")) { if (!shown(t)) continue; const r = t.getBoundingClientRect(); if (r.width && r.width < 2.5 * remPx) { out.cut.push("on/off switch drawn too small (" + Math.round(r.width) + " px wide)"); break; } }
  return out;
};

// Walks every screen and overlay and audits each. Returns only the problems: [] means clean.
window.uiAuditAll = async function () {
  const W = ms => new Promise(r => setTimeout(r, ms)), R = [];
  const step = async (n, f) => { try { f(); } catch (e) { R.push({ name: n, error: String(e) }); return; } await W(120); R.push(uiAudit(n)); };
  if (screen === "boot") await step("boot", () => {});
  await step("profiles", () => { if (screen === "boot") input("ok"); else switchUser(); });
  await step("passcode", () => openPin("login", "Enter the passcode"));
  await step("home", () => { closePin(); prof = 1; enterHome(PROFILES[1]); if (helpOn) closeHelp(); });
  await step("home-buttons", () => input("down"));
  await step("game-hub", () => {});
  await step("game-hub-activities", () => input("down"));
  await step("game-hub-captures", () => input("down"));
  await step("game-hub-about", () => input("down"));
  await step("game-options-menu", () => { input("back"); moreMenu(); });
  await step("game-info", () => { closeMenu(); gameInfo(cur()); for (let i = 0; i < 6; i++) input("down"); });
  await step("media-tab", () => { $("#ginfo").classList.remove("on"); closeMenu(); input("up"); setTab("media"); });
  await step("media-featured", () => input("down"));
  await step("media-viewer", () => { input("right"); input("ok"); });
  await step("control-center", () => { closeViewer(); setTab("games"); openCc(); });
  await step("control-center-icons", () => input("down"));
  await step("downloads-panel", () => { DL = { paused: true, items: [{ kind: "download", title: "A game with a very long name being copied from the PS5 console", gameId: "", status: "Copying the game files, a very long status line that must not spill", pct: 42, started: "20:01" }, { kind: "test", title: "Second game", gameId: "", status: "Testing on the GTX 1650 SUPER (PR #9508)", pct: null, started: "20:05" }, { kind: "build", title: "Building AnyPS5", gameId: "", status: "With PR #1, #2", pct: 77 }, { kind: "machine", title: "Chromebook", status: "PR tests: PR #21221", pct: null }, { kind: "job", title: "Job five", status: "x", pct: 5 }, { kind: "job", title: "Job six", status: "y", pct: 9 }] }; qsel = 3; drawCc(); ccIcons()[3].run(); });
  await step("downloads-panel-end", () => { for (let i = 0; i < 8; i++) input("down"); });
  await step("sound-menu", () => { closeMenu(); qsel = 6; drawCc(); ccIcons()[6].run(); });
  await step("power-menu", () => { closeMenu(); qsel = 9; drawCc(); ccIcons()[9].run(); });
  await step("power-menu-restart", () => { input("down"); input("down"); });
  await step("close-game-menu", () => { closeMenu(); crow = 0; ccard = 0; drawCc(); ccCards()[0].run(); });
  await step("settings", () => { closeMenu(); closeCc(true); openSettings(); });
  await step("settings-page", () => { input("ok"); });
  await step("settings-options", () => input("right"));
  await step("settings-storage", () => { settingsBack(); openSetPage("storage", "Storage"); });
  await step("settings-captures", () => { settingsBack(); openSetPage("capture", "Captures and Broadcasts"); setFocus = "right"; drawSettings(); });
  await step("settings-notifications", () => { settingsBack(); openSetPage("notes", "Notifications"); setSec = 2; setFocus = "right"; drawSettings(); });
  await step("settings-system", () => { settingsBack(); openSetPage("system", "System"); setSec = 2; setFocus = "right"; drawSettings(); });
  const osPage = (cat, title, name) => { settingsBack(); openSetPage(cat, title); const i = catSections(cat).findIndex(x => x.name === name); setSec = i >= 0 ? i : 0; setFocus = "right"; drawSettings(); };
  await step("settings-network", () => osPage("network", "Network", "Settings"));
  await step("settings-network-status", () => osPage("network", "Network", "Connection Status"));
  await step("settings-family", () => { settingsBack(); openSetPage("family", "Family and Parental Controls"); setFocus = "right"; drawSettings(); });
  await step("settings-language", () => osPage("system", "System", "Language"));
  await step("settings-datetime", () => osPage("system", "System", "Date and Time"));
  await step("settings-bluetooth", () => osPage("pad", "Accessories", "Bluetooth Accessories"));
  await step("settings-video-clips", () => osPage("capture", "Captures and Broadcasts", "Video Clips"));
  await step("settings-list-end", () => { settingsBack(); for (let i = 0; i < 12; i++) input("down"); });
  await step("library", () => { settingsBack(); openLibrary(); });
  await step("sort-menu", () => sortMenu());
  await step("create-menu", () => { closeMenu(); goHome(); openCreate(); });
  await step("create-recent", () => input("left"));
  await step("create-options", () => { input("right"); input("right"); input("ok"); });
  await step("gallery", () => { closeMenu(); closeCreate(false); goHome(); openGallery(); });
  await step("gallery-tabs", () => { gFocus = "tabs"; drawGallery(); input("right"); });
  await step("gallery-albums", () => { gTab = "albums"; gFocus = "grid"; gSel = 0; drawGallery(); });
  await step("gallery-album", () => input("ok"));
  await step("gallery-options", () => input("options"));
  await step("gallery-viewer", () => { closeMenu(); gAlbum = null; gTab = "all"; gFocus = "grid"; drawGallery(); input("ok"); });
  await step("trophies", () => { closeViewer(); closeMenu(); goHome(); openTrophies(); });
  await step("trophies-scrolled", () => { for (let i = 0; i < 9; i++) input("down"); });
  await step("trophy-list", () => { trSel = 0; drawTrophies(); input("ok"); });
  await step("trophy-list-scrolled", () => { for (let i = 0; i < 7; i++) input("down"); });
  await step("trophy-sort", () => input("options"));
  await step("search", () => { closeMenu(); goHome(); openSearch(); });
  await step("search-history", () => { if (me) me.searches = ["celeste", "a very long search that must not spill out of its row in a narrow window", "moss", "fatal frame", "octopath", "bendy", "coffee talk", "days", "mandragora", "drift"]; sQuery = ""; sCur = 0; sFocus = "grid"; lsel = 3; drawSearch(); });
  await step("search-history-end", () => { for (let i = 0; i < 12; i++) input("down"); });
  await step("search-media", () => { sFocus = "osk"; });
  await step("search-media-2", () => { oskPage = "abc"; sQuery = "a"; sCur = 1; setSearchTab("media"); });
  await step("search-players", () => { sQuery = (PROFILES[1] && PROFILES[1].name || "b").slice(0, 1); sCur = 1; setSearchTab("players"); sFocus = "tabs"; drawSearch(); });
  await step("search-symbols", () => { sFocus = "osk"; setSearchTab("games"); });
  await step("search-symbols-2", () => { searchKey("Sym"); searchKey("!"); searchKey("Left"); });
  await step("keyboard", () => { oskPage = "abc"; goHome(); openOsk("Name", "", () => {}); });
  await step("keyboard-symbols", () => { oskKey("Sym"); for (let i = 0; i < 6; i++) input("down"); });
  await step("keyboard-accents", () => { oskKey("Acc"); oskKey("Shift"); oskKey("é"); });
  await step("notifications", () => { closeOsk(); for (let i = 0; i < 9; i++) toast("Test notification number " + i + " with a long text that must not spill out of its row"); openCc(); qsel = 2; drawCc(); openNotes(); });
  await step("notifications-scrolled", () => { for (let i = 0; i < 8; i++) input("down"); });
  await step("notifications-options", () => input("options"));
  await step("help", () => { closeMenu(); closeCc(true); openHelp(); });
  closeHelp(); goHome();
  return R.filter(r => r.error || (r.small && r.small.length) || (r.cut && r.cut.length) || (r.overlap && r.overlap.length) || (r.covered && r.covered.length));
};
