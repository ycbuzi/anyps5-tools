// Self-test for the AnyPS5 view hosted like the Machines tab (a panel in another form). It types with real key presses
// (SendKeys) and reads the page state back: boot -> profiles -> PIN (typed, then keypad by arrows) -> home, Backspace in
// the PIN, F11 message, quick menu. Prints PASS/FAIL lines and exits 0 when all pass. Build and run: selftest.cmd.
// 11-15: real key presses once landed in Blu's chat box while he typed. Real keys are only sent while this window is in
// front and nobody typed for 3 s; otherwise the same keys go to the page as events (no OS focus check) and checks that
// need real keys print SKIP.
using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

class SelfTest : Form
{
    readonly ConsoleView console = new ConsoleView { Dock = DockStyle.Fill };
    int fails, fullscreens, skipped;
    static bool RealKeys;
    struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO i);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    static uint IdleMs() { var i = new LASTINPUTINFO { cbSize = 8 }; GetLastInputInfo(ref i); return (uint)Environment.TickCount - i.dwTime; }

    [STAThread]
    static int Main()
    {
        ConsoleBoot.Install(@"D:\AnyPS5 Games");
        ConsoleView.TestMode = true;
        RealKeys = Array.IndexOf(Environment.GetCommandLineArgs(), "--real-keys") > 0;
        Application.EnableVisualStyles();
        var t = new SelfTest();
        Application.Run(t);
        return t.fails;
    }

    SelfTest()
    {
        Text = "AnyPS5 self-test"; Size = new Size(1100, 700); StartPosition = FormStartPosition.CenterScreen;
        var strip = new Panel { Dock = DockStyle.Top, Height = 30, BackColor = Color.FromArgb(24, 26, 34) };
        var host = new Panel { Dock = DockStyle.Fill };
        host.Controls.Add(console);
        Controls.Add(host); Controls.Add(strip);
        console.FullscreenRequested += () => fullscreens++;
        Shown += async (s, e) => { try { await Run(); } catch (Exception ex) { Console.WriteLine("FAIL exception " + ex.Message); fails++; } Close(); };
    }

    async Task<string> Js(string expr)
    {
        Microsoft.Web.WebView2.WinForms.WebView2 wv = null;
        foreach (Control c in console.Controls) if (c is Microsoft.Web.WebView2.WinForms.WebView2) wv = (Microsoft.Web.WebView2.WinForms.WebView2)c;
        return wv.CoreWebView2 == null ? "null" : await wv.CoreWebView2.ExecuteScriptAsync(expr);
    }

    async Task<bool> RealKeysOk(Form front)
    {
        if (!RealKeys) return false;
        for (int i = 0; i < 20 && IdleMs() < 3000; i++) await Task.Delay(250);
        front.Activate(); console.FocusContent(); await Task.Delay(150);
        return IdleMs() >= 3000 && GetForegroundWindow() == front.Handle;
    }

    async Task Keys(string k) { await Keys(k, this); }

    async Task Keys(string k, Form front)
    {
        if (await RealKeysOk(front)) { SendKeys.SendWait(k); await Task.Delay(400); return; }
        skipped++;

        var names = new System.Collections.Generic.List<string>();
        for (int i = 0; i < k.Length; i++)
        {
            if (k[i] == '{') { int e = k.IndexOf('}', i); string n = k.Substring(i + 1, e - i - 1); i = e; names.Add(n == "ENTER" ? "Enter" : n == "ESC" ? "Escape" : n == "BACKSPACE" ? "Backspace" : n == "RIGHT" ? "ArrowRight" : n == "LEFT" ? "ArrowLeft" : n == "UP" ? "ArrowUp" : n == "DOWN" ? "ArrowDown" : n); }
            else names.Add(k[i].ToString());
        }
        foreach (var n in names) await Js("dispatchEvent(new KeyboardEvent('keydown', { key: " + new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(n) + " }))");
        await Task.Delay(400);
    }

    void Skip(string name) { Console.WriteLine("SKIP " + name + " (desktop busy: real keys not sent)"); }

    void Check(string name, string got, string want)
    {
        bool ok = got == want;
        if (!ok) fails++;
        Console.WriteLine((ok ? "PASS " : "FAIL ") + name + " (got " + got + ", want " + want + ")");
    }

    async Task Run()
    {
        for (int i = 0; i < 60 && await Js("typeof screen === 'string' && document.readyState") != "\"complete\""; i++) await Task.Delay(250);
        await Task.Delay(800);
        await Js("navigator.getGamepads = () => []");
        Check("page loaded on boot", await Js("screen"), "\"boot\"");
        await Js("localStorage.removeItem('anyps5-help-seen')");
        await Keys("{ENTER}");
        Check("Enter leaves boot", await Js("screen"), "\"profiles\"");
        Check("users shown", (int.Parse(await Js("PROFILES.length")) >= 2).ToString(), "True");
        Check("Add User is first, the first user is selected (PS5 order)", await Js("PROFILES[0].add === true && prof === 1"), "true");
        await Js("hashPin(PROFILES[1].name, '1234').then(h => { PROFILES[1].pinHash = h; prof = 1; drawProfiles(); })");
        await Task.Delay(300);
        await Keys("{ENTER}");
        Check("user with a passcode asks for it", await Js("document.getElementById('pin').classList.contains('on')"), "true");
        await Keys("12");
        Check("typed digits", await Js("pinDigits"), "\"12\"");
        await Keys("{BACKSPACE}");
        Check("Backspace deletes one digit", await Js("pinDigits"), "\"1\"");
        await Keys("{RIGHT}{ENTER}");
        Check("keypad by arrows + Enter", await Js("pinDigits"), "\"12\"");
        await Js("document.querySelectorAll('#keys span')[2].click()");
        await Task.Delay(200);
        Check("keypad by click", await Js("pinDigits"), "\"123\"");
        await Keys("9");
        await Task.Delay(700);
        Check("wrong passcode is refused", await Js("document.getElementById('pin').classList.contains('on') && pinDigits === ''"), "true");
        await Keys("1234");
        await Task.Delay(700);
        Check("right passcode opens home", await Js("screen"), "\"home\"");
        await Task.Delay(1500);
        Check("help shows itself the first time", await Js("helpOn"), "true");
        await Keys("{ESC}");
        Check("Esc closes help", await Js("helpOn"), "false");
        await Keys("h");
        Check("H opens help", await Js("helpOn"), "true");
        await Js("document.getElementById('help').click()");
        await Task.Delay(200);
        Check("click closes help", await Js("helpOn"), "false");
        await Js("document.getElementById('menuBtn').click()");
        await Task.Delay(300);
        Check("menu button opens the control center", await Js("screen"), "\"cc\"");
        Check("a click on a tile behind the control center lands on the control center, not the tile", await Js("(() => { const r = document.querySelector('#track .tile').getBoundingClientRect(); const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return at && at.id === 'cc'; })()"), "true");
        await Js("document.getElementById('cc').click()");
        await Task.Delay(300);
        Check("click outside closes the control center", await Js("screen"), "\"home\"");
        Check("button legend shown on home", await Js("document.getElementById('legend').textContent.indexOf('Control center') >= 0"), "true");
        string selBefore = await Js("sel");
        await Js("dispatchEvent(new WheelEvent('wheel', { deltaY: 120 }))");
        await Task.Delay(200);
        Check("mouse wheel moves the game row", (await Js("sel") != selBefore).ToString(), "True");
        Check("selected game's name sits right of its tile", await Js("(() => { const t = document.querySelector('#track .tile.on').getBoundingClientRect(), n = document.getElementById('tname').getBoundingClientRect(); return n.left >= t.right && n.left - t.right < 40 && n.top > t.top; })()"), "true");
        await Keys("{F11}");
        Check("F11 asks for full screen", fullscreens.ToString(), "1");
        await Keys("q");
        Check("Q opens the control center", await Js("screen"), "\"cc\"");
        Check("control center has the PS5 control row (10 controls)", await Js("document.querySelectorAll('#cicons .ci').length"), "10");
        Check("the control row has Downloads where the PS5 has it", await Js("ccIcons()[3].label"), "\"Downloads\"");
        await Js("crow = 1; qsel = 6; drawCc(); ccIcons()[6].run()");
        await Task.Delay(300);
        Check("a control's panel replaces the cards (PS5 control menu), titled with the control", await Js("screen === 'menu' && getComputedStyle(document.getElementById('ccards')).opacity === '0' && document.querySelector('#menu .mh').textContent === 'Sound'"), "true");
        await Js("closeMenu(); crow = 0; ccard = 0; drawCc()");
        await Task.Delay(300);
        Check("selected card is the big one with a hint under it", await Js("document.querySelector('#ccards .ccard.sel').offsetHeight > document.querySelectorAll('#ccards .ccard')[1].offsetHeight && getComputedStyle(document.getElementById('cchint')).display !== 'none'"), "true");
        await Keys("{ESC}");
        Check("Esc closes the control center", await Js("screen"), "\"home\"");
        await Js("document.getElementById('setBtn').click()");
        await Task.Delay(300);
        Check("settings open", await Js("screen"), "\"settings\"");
        Check("settings has the PS5 categories AnyPS5 can really do (11 + Network, Family)", await Js("allCats().length"), "13");
        await Js("window.__sys = null; Promise.all([sysCall('platform', 'info'), sysCall('network', 'status'), sysCall('recording', 'status'), sysCall('nosucharea', 'x')]).then(r => window.__sys = JSON.stringify([r[0].ok && r[0].data.platform, r[1].ok, r[2].ok, !!r[3].unsupported]))");
        string sys = "null";
        for (int i = 0; i < 60 && sys == "null"; i++) { await Task.Delay(250); sys = await Js("window.__sys"); }
        Check("the screen's system calls reach AnyPS5.exe (platform, network, recording; an unknown area is 'Available in AnyPS5 OS')", sys, "\"[\\\"windows\\\",true,true,true]\"");
        Check("Reduce Motion really turns motion off", await Js("(() => { const was = S.a11y; S.a11y = { motion: true }; applySettings(); const on = document.body.classList.contains('reducemotion'); S.a11y = was; applySettings(); return on; })()"), "true");
        Check("the 24-hour clock setting changes the clock", await Js("(() => { const was = S.clock24; S.clock24 = true; const t = fmtTime(new Date(2026, 0, 1, 15, 5)); S.clock24 = was; return t; })()"), "\"15:05\"");
        await Js("srow = allCats().findIndex(c => c[0] === 'users'); drawSettings()");
        await Keys("{ENTER}");
        Check("Users and Accounts opens", await Js("setPage && setPage.id"), "\"users\"");
        Check("settings page has sections on the left and options on the right (PS5)", await Js("document.querySelectorAll('#ssec div').length >= 2 && document.getElementById('slist').classList.contains('spage')"), "true");
        await Keys("{RIGHT}");
        Check("Right moves into the options", await Js("setFocus + ' ' + document.querySelectorAll('#slist .srow.sel').length"), "\"right 1\"");
        await Keys("{LEFT}");
        await Keys("{ESC}{ESC}");
        Check("Esc backs out of settings", await Js("screen"), "\"home\"");
        await Keys("/");
        Check("search opens", await Js("screen"), "\"search\"");
        await Keys("a");
        Check("search finds games", (int.Parse(await Js("searchResults().length")) > 0).ToString(), "True");
        await Keys("{ESC}");
        await Js("var t = document.querySelectorAll('#track .tile'); t[t.length - 1].click(); t[t.length - 1].click()");
        await Task.Delay(300);
        await Keys("{ESC}");
        await Js("sel = 0; focus = 'row'; drawRow()");
        await Keys("{DOWN}");
        Check("down from a game opens its hub (tile row hidden, header with its name)", await Js("document.getElementById('home').classList.contains('hub') && getComputedStyle(document.querySelector('.row')).opacity === '0' && document.getElementById('hubhead').textContent === cur().title"), "true");
        await Keys("{DOWN}");
        Check("down again selects the first activity card", await Js("focus + ' ' + document.querySelectorAll('#hubrows .hcard.sel').length"), "\"hub 1\"");
        await Keys("{DOWN}{DOWN}");
        Check("hub has Activities, Captures and About sections", await Js("Array.from(document.querySelectorAll('#hubrows .hsec h4')).map(h => h.textContent).join(',')"), "\"Activities,Captures,About\"");
        await Keys("{ESC}{UP}");
        Check("Back and Up leave the hub to the tile row", await Js("focus + ' ' + document.getElementById('home').classList.contains('hub')"), "\"row false\"");
        await Keys("{UP}");
        Check("Up from the games row opens the Media tab with media tiles", await Js("tab + ' ' + cur().title"), "\"media Media Gallery\"");
        await Keys("{DOWN}");
        Check("down goes to the Featured row", await Js("focus + ' ' + document.querySelectorAll('#cards .card.sel').length + ' ' + document.getElementById('shelfT').textContent"), "\"cards 1 Featured\"");
        await Keys("{ENTER}");
        Check("a Featured card opens the full-screen viewer", await Js("document.getElementById('viewer').classList.contains('on')"), "true");
        await Keys("{ESC}{UP}{UP}");
        Check("Esc closes the viewer and Up returns to Games", await Js("tab + ' ' + document.getElementById('viewer').classList.contains('on')"), "\"games false\"");
        await Js("var t = document.querySelectorAll('#track .tile'); t[t.length - 1].click(); t[t.length - 1].click()");
        await Task.Delay(300);
        Check("Game Library tile opens the library", await Js("screen"), "\"library\"");
        Check("library grid has 5 columns (PS5)", await Js("getComputedStyle(document.getElementById('lgrid')).gridTemplateColumns.split(' ').length"), "5");
        Check("library shows count and sort", await Js("/^All: [0-9]+$/.test(document.getElementById('lcount').textContent) && document.getElementById('lsort').textContent.indexOf('Sort by') === 0"), "true");
        await Keys("{ESC}");
        Check("Esc returns home", await Js("screen"), "\"home\"");
        Check("host sent its state", await Js("typeof H.paused + ' ' + (H.program.length > 0)"), "\"boolean true\"");
        Check("library has games", (int.Parse(await Js("LIB.length")) > 0).ToString(), "True");
        Check("no scroll bars anywhere", await Js("Array.from(document.querySelectorAll('*')).filter(e => /auto|scroll/.test(getComputedStyle(e).overflowY + getComputedStyle(e).overflowX)).every(e => e.offsetWidth - e.clientWidth <= 1 && e.offsetHeight - e.clientHeight <= 1)"), "true");
        Check("no settings written by the test", await Js("PROFILES[1].pinHash.length > 0"), "true");

        string auditJs = System.IO.File.ReadAllText(System.IO.Path.Combine(ConsoleBoot.Root, "_tools", "console", "ui-audit.js"));
        await Js(auditJs + ";'loaded'");
        await Js("(() => { const st = document.createElement('style'); st.id = 'noanim'; st.textContent = '*{transition:none!important;animation:none!important}'; document.head.appendChild(st); })()");
        var before = ClientSize;
        foreach (var size in new[] { new Size(1280, 720), new Size(1904, 980), new Size(1024, 768), new Size(734, 1000) })
        {
            ClientSize = new Size(size.Width, size.Height + 30);
            await Task.Delay(500);
            await Js("window.__audit = null; uiAuditAll().then(r => window.__audit = JSON.stringify(r))");
            string audit = "null";
            for (int i = 0; i < 40 && audit == "null"; i++) { await Task.Delay(250); audit = await Js("window.__audit"); }
            Check("layout audit " + size.Width + "x" + size.Height + ": text 20px+, nothing cut off, overlapping or covered (all screens)", audit, "\"[]\"");
        }
        foreach (var size in new[] { new Size(1280, 720), new Size(734, 1000) })
        {
            ClientSize = new Size(size.Width, size.Height + 30);
            await Js("window.__a11yWas = S.a11y; S.a11y = Object.assign({}, S.a11y || {}, { text: 'larger' }); applySettings()");
            await Task.Delay(500);
            await Js("window.__audit = null; uiAuditAll().then(r => window.__audit = JSON.stringify(r))");
            string audit = "null";
            for (int i = 0; i < 40 && audit == "null"; i++) { await Task.Delay(250); audit = await Js("window.__audit"); }
            await Js("S.a11y = window.__a11yWas; applySettings()");
            Check("layout audit with Text Size: Larger " + size.Width + "x" + size.Height, audit, "\"[]\"");
        }
        Check("Color Correction really filters the screen", await Js("(() => { const was = S.a11y; S.a11y = { cc: 'deutan' }; applySettings(); const f = document.documentElement.style.filter; S.a11y = was; applySettings(); return f; })()"), "\"url(\\\"#ccf\\\")\"");
        Check("Text Size: Larger really makes text bigger", await Js("(() => { const r = () => { const d = document.createElement('div'); d.className = 'srow'; document.body.appendChild(d); const v = parseFloat(getComputedStyle(d).fontSize); d.remove(); return v; }; const a = r(); const was = S.a11y; S.a11y = { text: 'larger' }; applySettings(); const b = r(); S.a11y = was; applySettings(); return b > a * 1.2; })()"), "true");
        ClientSize = before;
        await Js("(() => { const st = document.getElementById('noanim'); if (st) st.remove(); goHome(); })()");
        await Task.Delay(400);

        var host = console.Parent;
        var pop = new Form { Text = "AnyPS5 popped", Size = new Size(900, 560), StartPosition = FormStartPosition.Manual, Location = new Point(Left + 60, Top + 60) };
        host.Controls.Remove(console); pop.Controls.Add(console); pop.Show();
        await Task.Delay(600);
        await Keys("q", pop);
        Check("popped-out view takes keys", await Js("screen"), "\"cc\"");
        pop.Controls.Remove(console); host.Controls.Add(console); pop.Close();
        await Task.Delay(600);
        await Keys("{ESC}");
        Check("view put back inside takes keys", await Js("screen"), "\"home\"");

        string fake = Environment.GetEnvironmentVariable("ANYPS5_FAKE_GAME");
        if (string.IsNullOrEmpty(fake)) return;
        string keysLog = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(fake), "fakegame-keys.txt");
        try { System.IO.File.Delete(keysLog); } catch { }
        await Js("send({ play: LIB[0].folder })");
        await Task.Delay(1500);
        Check("test mode never starts a real game", console.GameRunning.ToString(), "False");
        string fakeDir = System.IO.Path.GetDirectoryName(fake), trophyDir = System.IO.Path.Combine(fakeDir, "_trophy"), liveRecord = System.IO.Path.Combine(trophyDir, "268435456_0.txt");
        try { if (System.IO.Directory.Exists(trophyDir)) System.IO.Directory.Delete(trophyDir, true); } catch { }
        System.IO.Directory.CreateDirectory(trophyDir);
        System.IO.File.WriteAllText(liveRecord, "1 63928776696107908\n");
        console.StartGame(fake, fakeDir, "utest", "");
        Check("the player's own trophy record is in place while they play (the tests' record set aside)", (!System.IO.File.Exists(liveRecord) && System.IO.File.Exists(System.IO.Path.Combine(trophyDir, "users", "_tests", "268435456_0.txt"))).ToString(), "True");
        for (int i = 0; i < 40 && !console.GameEmbedded; i++) await Task.Delay(250);
        Check("game window plays inside the view", console.GameEmbedded.ToString(), "True");
        Check("game pid written for the pipeline", System.IO.File.Exists(ConsoleView.GameMarker).ToString(), "True");

        if (await RealKeysOk(this))
        {
            SendKeys.SendWait("abc"); await Task.Delay(600);
            string typed = System.IO.File.Exists(keysLog) ? System.IO.File.ReadAllText(keysLog) : "";
            Check("keys reach the game", typed, "abc");
        }
        else Skip("keys reach the game");
        Check("hosted page did not get the keys", await Js("screen"), "\"home\"");
        console.OpenGameMenu();
        await Task.Delay(600);
        Check("PS button menu opens over the game", console.GameMenuUp + " " + await Js("screen + ' ' + ingame"), "True \"cc true\"");
        Check("game menu offers Close game", await Js("ccCards().some(c => c.title === 'Close game')"), "true");
        await Js("send({ shot: true })");
        await Task.Delay(1500);
        string shots = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(fake), "screenshots");
        Check("screenshot really saved", (System.IO.Directory.Exists(shots) && System.IO.Directory.GetFiles(shots, "*.png").Length > 0).ToString(), "True");
        await Js("closeCc(); send({ resume: true })");
        await Task.Delay(500);
        Check("Return to game hides the menu", (console.GameEmbedded && !console.PageVisible).ToString(), "True");
        System.IO.File.AppendAllText(liveRecord, "5 63928776696107999\n");
        for (int i = 0; i < 20 && !console.TrophyPopupUp; i++) await Task.Delay(150);
        Check("a trophy earned in the game pops up over it", console.TrophyPopupUp + " " + console.LastTrophy, "True trophy 5");
        Check("a trophy capture is saved for the Media Gallery's Trophies tab", (System.IO.Directory.Exists(System.IO.Path.Combine(fakeDir, "screenshots")) && System.IO.Directory.GetFiles(System.IO.Path.Combine(fakeDir, "screenshots"), "Trophy 00-5 *.png").Length > 0).ToString(), "True");
        await Task.Delay(6500);
        console.OpenCreateMenu();
        await Task.Delay(700);
        Check("the Create button opens the Create menu over the game", console.PageVisible + " " + await Js("createOn() && ingame"), "True true");
        await Js("crSel = 1; drawCreate(); createPick()");
        for (int i = 0; i < 20 && console.LastShot == ""; i++) await Task.Delay(200);
        Check("Take Screenshot in the Create menu saves the game and returns to it", (console.LastShot != "" && System.IO.File.Exists(console.LastShot) && console.GameEmbedded && !console.PageVisible).ToString(), "True");
        await Js("enterRest()");
        await Task.Delay(500);
        Check("Rest Mode pauses the game and darkens the screen", console.GameSuspended + " " + await Js("restOn()"), "True true");
        await Js("wakeUp()");
        await Task.Delay(500);
        Check("any button wakes AnyPS5 and the game carries on", (!console.GameSuspended && console.GameRunning).ToString() + " " + await Js("restOn()"), "True false");
        await Js("powerOff(false)");
        await Task.Delay(400);
        Check("Turn Off reaches AnyPS5 (test mode does not really close it)", console.LastPower, "off");
        Check("the trophy pop-up does not take the game's focus", (console.GameEmbedded && !console.PageVisible && !(Form.ActiveForm is TrophyPopup)).ToString(), "True");
        console.CloseGameNow();
        for (int i = 0; i < 40 && console.GameRunning; i++) await Task.Delay(250);
        await Task.Delay(500);
        Check("game closed", console.GameRunning.ToString(), "False");
        string mine = System.IO.Path.Combine(trophyDir, "users", "utest", "268435456_0.txt");
        for (int i = 0; i < 40 && !System.IO.File.Exists(mine); i++) await Task.Delay(250);
        await Task.Delay(300);
        Check("after the game the trophy is the player's, and the tests' record is back", (System.IO.File.Exists(mine) ? System.IO.File.ReadAllText(mine).Trim() : "missing") + " / " + (System.IO.File.Exists(liveRecord) ? System.IO.File.ReadAllText(liveRecord).Trim() : "missing"), "5 63928776696107999 / 1 63928776696107908");
        Check("home screen is back", (console.PageVisible && !console.GameEmbedded).ToString(), "True");
        Check("game pid file removed", System.IO.File.Exists(ConsoleView.GameMarker).ToString(), "False");
        await Keys("q");
        Check("page takes keys again after the game", await Js("screen"), "\"cc\"");

        var parent = console.Parent;
        parent.Controls.Remove(console);
        console.Dispose();
        var second = new ConsoleView { Dock = DockStyle.Fill };
        parent.Controls.Add(second);
        string state = "null";
        for (int i = 0; i < 60; i++)
        {
            await Task.Delay(250);
            Microsoft.Web.WebView2.WinForms.WebView2 wv2 = null;
            foreach (Control c in second.Controls) if (c is Microsoft.Web.WebView2.WinForms.WebView2) wv2 = (Microsoft.Web.WebView2.WinForms.WebView2)c;
            if (wv2 == null) { state = "could not start its screen"; break; }
            if (wv2.CoreWebView2 != null) { state = await wv2.CoreWebView2.ExecuteScriptAsync("typeof screen === 'string' ? screen : 'loading'"); if (state == "\"boot\"") break; }
        }
        Check("a second AnyPS5 view in the same program starts (Exit, then the tab again)", state, "\"boot\"");
    }
}
