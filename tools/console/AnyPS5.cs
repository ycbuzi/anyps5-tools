// The console-style AnyPS5 app. ConsoleView holds everything: the WebView2 page (_tools\console\index.html: boot logo,
// profiles, game row, quick menu) and the game it starts, which plays inside the view. It runs in two places:
//   - AnyPS5.exe (ConsoleApp below, build-app.cmd): its own window, F11 fullscreen (remembered).
//   - the "AnyPS5" tab of AnyPS5 Machines.exe (MachinesView.cs, build-machines.cmd), in the same process, so typing and
//     focus work like any other control. "Pop out" moves the same view into its own window.
// 11-15 review: the tab used to paste a separate AnyPS5.exe window in with SetParent; WebView2 never got keyboard
// focus there (Blu could not type his PIN). Game start, pipeline pause and game close no longer block the window.
// Game keys: Shift+Esc asks to close the game, Back+Start held 1 s on a controller closes it, F11 fullscreen. They are
// only taken from Windows while this window is in front.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class ConsoleBoot
{
    public static string Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    static bool installed;

    public static void Install(string root)
    {
        Root = root.TrimEnd('\\');
        if (installed) return;
        installed = true;
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string f = Path.Combine(Root, "_tools", "webview2", new System.Reflection.AssemblyName(e.Name).Name + ".dll");
            return File.Exists(f) ? System.Reflection.Assembly.LoadFrom(f) : null;
        };
    }
}

partial class ConsoleView : UserControl
{
    static string Root { get { return ConsoleBoot.Root; } }
    static string Tools { get { return Path.Combine(Root, "_tools"); } }
    static string Folder { get { return Path.Combine(Tools, "console"); } }
    static string PauseFlag { get { return Path.Combine(Tools, "pause.flag"); } }
    public static string GameMarker { get { return Path.Combine(Tools, "live", TestMode ? "anyps5-app-game-test.txt" : "anyps5-app-game.txt"); } }
    public static bool TestMode;
    static bool appPaused;
    static readonly bool IsAdmin = new System.Security.Principal.WindowsPrincipal(System.Security.Principal.WindowsIdentity.GetCurrent()).IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
    static Task<Microsoft.Web.WebView2.Core.CoreWebView2Environment> envTask;
    static Task<Microsoft.Web.WebView2.Core.CoreWebView2Environment> Env()
    {
        if (envTask == null)
        {
            Microsoft.Web.WebView2.Core.CoreWebView2Environment.SetLoaderDllFolderPath(Path.Combine(Tools, "webview2"));
            envTask = Microsoft.Web.WebView2.Core.CoreWebView2Environment.CreateAsync(null, Path.Combine(Tools, "live", IsAdmin ? "anyps5-app-web-admin" : "anyps5-app-web"));
        }
        return envTask;
    }

    public event Action ExitRequested, WorkAppRequested, FullscreenRequested, MachinesRequested;
    static string SettingsFile { get { return Path.Combine(Tools, "live", "anyps5-settings.json"); } }
    public static string FullscreenFile { get { return Path.Combine(Tools, "live", "anyps5-fullscreen.flag"); } }

    public static void Log(string line)
    {
        try { File.AppendAllText(Path.Combine(Tools, "live", "anyps5-app.log"), DateTime.Now.ToString("MM-dd HH:mm:ss") + " " + line + Environment.NewLine); } catch { }
    }

    readonly Microsoft.Web.WebView2.WinForms.WebView2 view = new Microsoft.Web.WebView2.WinForms.WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(6, 7, 12) };
    readonly Panel gameHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.Black, Visible = false };
    readonly Timer embedTimer = new Timer { Interval = 300 };
    readonly Timer padTimer = new Timer { Interval = 100 };
    Process game;
    string gameName, gameDir;
    bool starting, asking, hotkeys, ready, overlay, guideWas, scanning;
    DateTime gameStart;
    string lastBg = "";
    IntPtr embedded, embeddedStyle;
    int padHeld, backTicks;
    bool backCombo, resting, gameSuspended, dndOn;
    DateTime createDownAt;
    internal string LastPower = "";
    internal bool GameSuspended { get { return gameSuspended; } }
    double aspect = 16.0 / 9.0;
    static Task workChain = Task.FromResult(0);
    string trophyDirIn, trophyGameDir;
    FileSystemWatcher trophyWatch;
    readonly Timer trophyTimer = new Timer { Interval = 450 };
    readonly Timer familyTimer = new Timer { Interval = 60000 };
    string familyUid = "";
    bool familyOver;
    HashSet<string> trophyKnown = new HashSet<string>();
    Dictionary<string, object> trophyManifest;
    internal string LastTrophy = "";

    struct XPad { public uint Packet; public ushort Buttons; public byte LT, RT; public short LX, LY, RX, RY; }
    struct RECT { public int L, T, R, B; }
    [DllImport("xinput1_4.dll", EntryPoint = "XInputGetState")] static extern int XInputGetState(int pad, out XPad state);
    [DllImport("xinput1_4.dll", EntryPoint = "#100")] static extern int XInputGetStateEx(int pad, out XPad state);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] static extern IntPtr GetWindowLongPtr(IntPtr h, int i);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] static extern IntPtr SetWindowLongPtr(IntPtr h, int i, IntPtr v);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hgt, uint flags);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("ntdll.dll")] static extern int NtSuspendProcess(IntPtr h);
    [DllImport("ntdll.dll")] static extern int NtResumeProcess(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr SetFocus(IntPtr h);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr h, int id, uint mods, uint key);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr h, int id);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
    delegate bool EnumProc(IntPtr h, IntPtr l);
    const long WS_CHILD = 0x40000000, WS_POPUP = 0x80000000, WS_CAPTION = 0x00C00000, WS_THICKFRAME = 0x00040000, WS_SYSMENU = 0x00080000, WS_MINIMIZEBOX = 0x00020000, WS_MAXIMIZEBOX = 0x00010000, WS_VISIBLE = 0x10000000;
    const uint SWP_NOZORDER = 0x0004, SWP_FRAMECHANGED = 0x0020, SWP_SHOWWINDOW = 0x0040;
    const int HK_FULL = 0x5F11, HK_CLOSE = 0x5F12;

    public ConsoleView()
    {
        BackColor = Color.FromArgb(6, 7, 12);
        Controls.Add(view);
        Controls.Add(gameHost);
        gameHost.Resize += (s, e) => Fit();
        gameHost.MouseDown += (s, e) => FocusGame();
        embedTimer.Tick += (s, e) => TryEmbed();
        padTimer.Tick += (s, e) => PadTick();
        trophyTimer.Tick += (s, e) => { trophyTimer.Stop(); CheckTrophies(); };
        familyTimer.Tick += (s, e) => FamilyTick();
        if (!TestMode) Task.Run(() => Log("trophies at start: " + RunPython("\"" + Path.Combine(Folder, "trophy_users.py") + "\" fix", 60000)));
        HandleCreated += (s, e) => { if (!ready) { ready = true; Start(); } };
        if (!TestMode) { PadWatch.Start(); PadWatch.PsPressed += OnPs; PadWatch.CreatePressed += OnCreate; PadWatch.CreateReleased += OnCreateUp; }
        padTimer.Start();
    }

    void OnPs() { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)PsButton); } catch { } }
    void OnCreate() { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)CreateDown); } catch { } }
    void OnCreateUp() { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)CreateUp); } catch { } }
    bool EasyShots { get { return CaptureSetting("createMode", "menu") == "shot"; } }
    void CreateDown() { if (EasyShots && !resting) createDownAt = DateTime.Now; else CreateButton(); }
    void CreateUp() { if (!EasyShots || resting || createDownAt == DateTime.MinValue) return; double ms = (DateTime.Now - createDownAt).TotalMilliseconds; createDownAt = DateTime.MinValue; if (ms >= 600) CreateButton(); else ShotInGame(); }
    void ShotInGame() { if (InFront() && embedded != IntPtr.Zero && IsWindow(embedded) && !overlay) Screenshot(); }

    void CreateButton()
    {
        if (resting) { Wake(); Send("{\"woke\":true}"); return; }
        if (!InFront()) return;
        if (embedded != IntPtr.Zero && IsWindow(embedded) && !overlay) ShowOverlay("create");
    }

    internal void OpenCreateMenu() { if (embedded != IntPtr.Zero && !overlay) ShowOverlay("create"); }

    void Rest()
    {
        resting = true;
        if (GameRunning && !gameSuspended)
        {
            try { NtSuspendProcess(game.Handle); gameSuspended = true; Log("rest mode: " + gameName + " paused"); } catch (Exception ex) { Log("rest mode could not pause the game: " + ex.Message); }
        }
        else Log("rest mode");
    }

    void Wake()
    {
        resting = false;
        if (gameSuspended && game != null)
        {
            try { NtResumeProcess(game.Handle); Log("woke: " + gameName + " carries on"); } catch (Exception ex) { Log("could not wake the game: " + ex.Message); }
        }
        gameSuspended = false;
    }

    async void PowerOff(bool restart)
    {
        LastPower = restart ? "restart" : "off";
        Log("power: " + LastPower);
        if (gameSuspended) Wake();
        if (TestMode) return;
        if (GameRunning)
        {
            CloseGame(false);
            for (int i = 0; i < 60 && GameRunning; i++) await Task.Delay(250);
        }
        if (restart)
        {
            try
            {
                string exe = Application.ExecutablePath;
                Process.Start(new ProcessStartInfo("cmd.exe", "/c timeout /t 2 /nobreak >nul & start \"\" \"" + exe + "\"") { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetDirectoryName(exe) });
            }
            catch (Exception ex) { Log("restart failed: " + ex.Message); Toast("AnyPS5 could not restart itself: " + ex.Message); return; }
        }
        if (ExitRequested != null) ExitRequested();
    }

    void PsButton()
    {
        if (resting) { Wake(); Send("{\"woke\":true}"); return; }
        if (!InFront()) return;
        if (embedded != IntPtr.Zero && IsWindow(embedded))
        {
            if (overlay) HideOverlay(); else ShowOverlay();
        }
        else Send("{\"ps\":true}");
    }

    void ShowOverlay(string open = "cc")
    {
        string bg = lastBg;
        try
        {
            if (view.Visible) throw new InvalidOperationException();
            RECT r; GetWindowRect(embedded, out r);
            var area = Rectangle.FromLTRB(r.L, r.T, r.R, r.B);
            if (area.Width > 10 && area.Height > 10)
            {
                string dir = Path.Combine(Folder, "cache");
                Directory.CreateDirectory(dir);
                using (var bmp = new Bitmap(area.Width, area.Height))
                {
                    using (var gr = Graphics.FromImage(bmp)) gr.CopyFromScreen(area.Location, Point.Empty, area.Size);
                    bmp.Save(Path.Combine(dir, "ingame.jpg"), System.Drawing.Imaging.ImageFormat.Jpeg);
                }
                bg = lastBg = "cache/ingame.jpg?" + DateTime.Now.Ticks;
            }
        }
        catch { }
        overlay = true;
        view.Visible = true;
        view.BringToFront();
        view.Focus();
        Send(Json(new Dictionary<string, object> { { "ingame", true }, { "title", gameName ?? "" }, { "bg", bg }, { "open", open } }));
    }

    void HideOverlay()
    {
        overlay = false;
        if (embedded == IntPtr.Zero || !IsWindow(embedded)) return;
        Send("{\"ingame\":false}");
        gameHost.BringToFront();
        view.Visible = false;
        FocusGame();
    }

    void GoHome()
    {
        overlay = false;
        Send("{\"ingame\":false}");
        SendState();
        view.Focus();
    }

    async void Screenshot()
    {
        try
        {
            bool inGame = embedded != IntPtr.Zero && IsWindow(embedded), pageUp = view.Visible;
            string dir = inGame && gameDir != null ? Path.Combine(gameDir, "screenshots") : Path.Combine(Tools, "live", "screenshots");
            Directory.CreateDirectory(dir);
            Rectangle area;
            if (inGame)
            {
                gameHost.BringToFront(); view.Visible = false;
                await Task.Delay(350);
                RECT r; GetWindowRect(embedded, out r);
                area = Rectangle.FromLTRB(r.L, r.T, r.R, r.B);
            }
            else area = RectangleToScreen(ClientRectangle);
            bool jpg = CaptureSetting("fmt", "png") == "jpg";
            string file = Path.Combine(dir, (inGame ? gameName : "AnyPS5") + " " + DateTime.Now.ToString("yyyy-MM-dd HH-mm-ss") + (jpg ? ".jpg" : ".png"));
            using (var bmp = new Bitmap(Math.Max(1, area.Width), Math.Max(1, area.Height)))
            {
                using (var gr = Graphics.FromImage(bmp)) gr.CopyFromScreen(area.Location, Point.Empty, area.Size);
                bmp.Save(file, jpg ? System.Drawing.Imaging.ImageFormat.Jpeg : System.Drawing.Imaging.ImageFormat.Png);
            }
            if (inGame && pageUp) { view.Visible = true; view.BringToFront(); view.Focus(); }
            LastShot = file;
            if (CaptureSetting("notify", "true") != "false") Toast("Screenshot saved: " + file);
            RefreshMedia();
        }
        catch (Exception ex) { Toast("Screenshot failed: " + ex.Message); }
    }

    public bool GameRunning { get { return game != null && !game.HasExited; } }

    public async void Report()
    {
        string who = Process.GetCurrentProcess().ProcessName;
        try
        {
            if (view.CoreWebView2 == null) { Log("screen not started yet in " + who); return; }
            string s = await view.CoreWebView2.ExecuteScriptAsync("typeof screen === 'string' ? screen : 'none'");
            Log((s != "\"none\"" && s != "null" ? "screen ready (page on " + s.Trim('"') + ")" : "screen failed: page did not load") + " in " + who);
        }
        catch (Exception ex) { Log("screen failed: " + ex.Message + " in " + who); }
    }

    public void FocusContent()
    {
        if (embedded != IntPtr.Zero && IsWindow(embedded)) FocusGame();
        else view.Focus();
    }

    void FocusGame() { if (embedded != IntPtr.Zero && IsWindow(embedded)) SetFocus(embedded); }

    async void Start()
    {
        try
        {
            var env = await Env();
            await view.EnsureCoreWebView2Async(env);
            if (IsDisposed) return;
            if (TestMode) await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync("navigator.getGamepads = () => [];");
            view.CoreWebView2.Settings.IsStatusBarEnabled = false;
            view.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            view.CoreWebView2.Settings.AreDevToolsEnabled = false;
            view.CoreWebView2.Settings.IsZoomControlEnabled = false;
            view.CoreWebView2.SetVirtualHostNameToFolderMapping("anyps5.home", Folder, Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind.Allow);
            view.CoreWebView2.WebMessageReceived += (s, e) => { try { OnPageMessage(e.TryGetWebMessageAsString()); } catch { } };
            view.CoreWebView2.NavigationCompleted += (s, e) => { lastNavigation = DateTime.Now; SendState(); Log((e.IsSuccess ? "screen ready" : "screen failed to load: " + e.WebErrorStatus) + " in " + Process.GetCurrentProcess().ProcessName); };
            view.CoreWebView2.Navigate("https://anyps5.home/index.html");
            WatchScreenFiles();
            if (ContainsFocus || (FindForm() != null && FindForm().ContainsFocus)) view.Focus();
            RefreshLibrary();
        }
        catch (Exception ex)
        {
            Log("could not start the screen in " + Process.GetCurrentProcess().ProcessName + ": " + ex.Message + " (thread " + System.Threading.Thread.CurrentThread.GetApartmentState() + ")");
            Controls.Clear();
            Controls.Add(new Label { Text = "AnyPS5 could not start its screen: " + ex.Message, ForeColor = Color.FromArgb(240, 190, 80), AutoSize = true, Location = new Point(24, 24), Font = new Font("Segoe UI", 12f) });
        }
    }

    // Hot reload (11-15, Blu: "so we don't have to keep restarting the app"): when the screen's own files change, the page
    // reloads in place and returns to where it was. While a game runs, it waits until the game closes.
    FileSystemWatcher watcher;
    readonly Timer reloadTimer = new Timer { Interval = 900 };
    bool reloadWanted;

    void WatchScreenFiles()
    {
        if (watcher != null || TestMode) return;
        try
        {
            watcher = new FileSystemWatcher(Folder) { NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size, IncludeSubdirectories = false };
            FileSystemEventHandler h = (s, e) =>
            {
                string n = Path.GetFileName(e.FullPath).ToLowerInvariant();
                if (n == "library.js" || !(n.EndsWith(".html") || n.EndsWith(".js") || n.EndsWith(".css") || n.EndsWith(".png") || n.EndsWith(".svg"))) return;
                try { BeginInvoke((Action)(() => { reloadWanted = true; reloadTimer.Stop(); reloadTimer.Start(); })); } catch { }
            };
            watcher.Changed += h; watcher.Created += h; watcher.Renamed += (s, e) => h(s, e);
            watcher.EnableRaisingEvents = true;
            reloadTimer.Tick += (s, e) => { reloadTimer.Stop(); ReloadIfQuiet(); };
        }
        catch (Exception ex) { Log("screen file watch failed: " + ex.Message); }
    }

    void ReloadIfQuiet()
    {
        if (!reloadWanted || IsDisposed) return;
        if (GameRunning) return;
        reloadWanted = false;
        Log("screen files changed: reloading the page in " + Process.GetCurrentProcess().ProcessName);
        DateTime asked = DateTime.Now;
        reloadAsked = asked;
        Send("{\"reload\":true}");
        var check = new Timer { Interval = 6000 };
        check.Tick += (s2, e2) =>
        {
            check.Stop(); check.Dispose();
            if (IsDisposed || reloadAsked != asked || lastNavigation > asked || view.CoreWebView2 == null) return;
            Log("the page did not answer the reload (a script error?): reloading it from AnyPS5");
            try { view.CoreWebView2.Reload(); } catch { }
        };
        check.Start();
    }
    DateTime reloadAsked, lastNavigation;

    async void RefreshLibrary(bool announce = false)
    {
        if (scanning) return;
        scanning = true;
        bool built = await Task.Run(() =>
        {
            try
            {
                using (var p = Process.Start(new ProcessStartInfo("python", "-I \"" + Path.Combine(Folder, "build_library.py") + "\"") { UseShellExecute = false, CreateNoWindow = true }))
                    return p.WaitForExit(120000) && p.ExitCode == 0;
            }
            catch { return false; }
        });
        scanning = false;
        if (IsDisposed) return;
        if (built) { Send("{\"library\":true}"); if (announce) Toast("Game list updated"); }
        else if (announce) Toast("Could not scan the games folder (build_library.py failed or Python is missing)");
    }

    void Send(string json) { try { if (!IsDisposed && view.CoreWebView2 != null) view.CoreWebView2.PostWebMessageAsString(json); } catch { } }

    static string Json(object o) { return new JavaScriptSerializer().Serialize(o); }

    void Toast(string text) { Send(Json(new Dictionary<string, object> { { "toast", text } })); }

    void SendState()
    {
        object settings = null;
        try { if (File.Exists(SettingsFile)) settings = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(SettingsFile)); } catch { }
        var pads = new List<string>(PadWatch.Describe());
        for (int i = 0; i < 4; i++) { XPad x; try { if (XInputGetState(i, out x) == 0) pads.Add("Xbox-style controller " + (i + 1)); } catch { break; } }
        string build = "";
        try { build = File.GetLastWriteTime(Process.GetCurrentProcess().MainModule.FileName).ToString("yyyy-MM-dd HH:mm"); } catch { }
        Send(Json(new Dictionary<string, object> {
            { "paused", File.Exists(PauseFlag) }, { "host", true }, { "settings", settings }, { "fullscreenStart", File.Exists(FullscreenFile) },
            { "pads", pads }, { "build", build }, { "program", Process.GetCurrentProcess().ProcessName }, { "root", Root },
            { "playing", GameRunning ? gameName : null }, { "drives", Drives() }, { "platform", "windows" } }));
    }

    static List<Dictionary<string, object>> Drives()
    {
        var list = new List<Dictionary<string, object>>();
        try
        {
            foreach (var d in DriveInfo.GetDrives())
            {
                try
                {
                    if (!d.IsReady || d.DriveType != DriveType.Fixed) continue;
                    list.Add(new Dictionary<string, object> { { "name", d.Name.TrimEnd('\\') }, { "label", d.VolumeLabel }, { "free", (double)d.AvailableFreeSpace }, { "total", (double)d.TotalSize } });
                }
                catch { }
            }
        }
        catch { }
        return list;
    }

    void SaveSettings(object o)
    {
        if (TestMode) return;
        try { File.WriteAllText(SettingsFile, new JavaScriptSerializer().Serialize(o)); } catch (Exception ex) { Toast("Settings not saved: " + ex.Message); }
    }

    void OnPageMessage(string raw)
    {
        var msg = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(raw ?? "{}");
        object v;
        if (msg.TryGetValue("play", out v) && v is string) { object u, lg; Play((string)v, msg.TryGetValue("user", out u) && u is string ? (string)u : "", msg.TryGetValue("legacy", out lg) && lg is string ? (string)lg : ""); }
        else if (msg.TryGetValue("trophyOwner", out v) && v is string) SetTrophyOwner((string)v);
        else if (msg.ContainsKey("downloads")) RefreshDownloads();
        else if (msg.TryGetValue("sys", out v) && v is string) SysCall((string)v, msg);
        else if (msg.TryGetValue("dnd", out v) && v is bool) dndOn = (bool)v;
        else if (msg.ContainsKey("rest")) Rest();
        else if (msg.ContainsKey("wake")) Wake();
        else if (msg.TryGetValue("power", out v) && v is string) PowerOff((string)v == "restart");
        else if (msg.TryGetValue("pause", out v) && v is bool) TogglePause((bool)v);
        else if (msg.ContainsKey("state")) SendState();
        else if (msg.TryGetValue("settings", out v)) SaveSettings(v);
        else if (msg.TryGetValue("fullscreenStart", out v) && v is bool) { try { if ((bool)v) File.WriteAllText(FullscreenFile, "fullscreen"); else File.Delete(FullscreenFile); } catch { } SendState(); }
        else if (msg.ContainsKey("resume")) HideOverlay();
        else if (msg.ContainsKey("home")) GoHome();
        else if (msg.ContainsKey("closegame")) CloseGame(false);
        else if (msg.ContainsKey("shot")) Screenshot();
        else if (msg.ContainsKey("rescan")) { Toast("Scanning the games folder"); RefreshLibrary(true); }
        else if (msg.ContainsKey("machines")) { if (MachinesRequested != null) MachinesRequested(); }
        else if (msg.ContainsKey("fullscreen")) { if (FullscreenRequested != null) FullscreenRequested(); }
        else if (msg.ContainsKey("tab")) { if (WorkAppRequested != null) WorkAppRequested(); }
        else if (msg.ContainsKey("exit")) { if (ExitRequested != null) ExitRequested(); }
    }

    void TogglePause(bool on)
    {
        if (TestMode) { SendState(); return; }
        appPaused = false;
        Toast(on ? "Pausing the pipeline..." : "Resuming the pipeline...");
        QueueWorkApp(on ? "--pause" : "--resume", false, ok =>
        {
            try
            {
                if (IsDisposed || !IsHandleCreated) return;
                BeginInvoke((Action)(() => { Toast(ok ? (File.Exists(PauseFlag) ? "Pipeline paused" : "Pipeline running") : "The pipeline did not answer; check the AnyPS5 Games window"); SendState(); }));
            }
            catch { }
        });
    }

    static bool RunWorkApp(string arg)
    {
        if (TestMode) return true;
        if (!IsAdmin)
        {
            try
            {
                if (arg == "--pause") File.WriteAllText(PauseFlag, "paused from the AnyPS5 app (not admin: new tests wait, running tests continue) " + DateTime.Now.ToString("MM-dd HH:mm"));
                else if (File.Exists(PauseFlag) && File.ReadAllText(PauseFlag).Contains("AnyPS5 app")) File.Delete(PauseFlag);
                Log("pipeline " + arg + " by pause.flag only (not admin)");
                return true;
            }
            catch (Exception ex) { Log("pipeline " + arg + " failed: " + ex.Message); return false; }
        }
        try
        {
            using (var p = Process.Start(new ProcessStartInfo(Path.Combine(Root, "AnyPS5 Games.exe"), arg) { WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true }))
            {
                bool done = p.WaitForExit(90000);
                Log("pipeline " + arg + (done ? " done" : " still running after 90 s"));
                return done;
            }
        }
        catch (Exception ex) { Log("pipeline " + arg + " failed: " + ex.Message); return false; }
    }

    static bool OtherGameRunning()
    {
        foreach (var p in Process.GetProcesses())
        {
            try
            {
                string f = p.MainModule.FileName;
                if (f.StartsWith(Root + "\\", StringComparison.OrdinalIgnoreCase) && f.IndexOf("\\_tools\\", StringComparison.OrdinalIgnoreCase) < 0
                    && !Path.GetFileName(f).StartsWith("AnyPS5", StringComparison.OrdinalIgnoreCase) && !p.HasExited) return true;
            }
            catch { }
            finally { p.Dispose(); }
        }
        return false;
    }

    static void QueueWorkApp(string arg, bool onlyIfNoGame = false, Action<bool> after = null)
    {
        lock (typeof(ConsoleView)) workChain = workChain.ContinueWith(t =>
        {
            bool ok = true;
            if (onlyIfNoGame && OtherGameRunning()) Log("pipeline " + arg + " skipped: another game is still running");
            else ok = RunWorkApp(arg);
            if (after != null) after(ok);
        });
    }

    public static void FlushWork(int ms) { try { workChain.Wait(ms); } catch { } }

    static void PauseForGame()
    {
        if (TestMode || File.Exists(PauseFlag)) return;
        appPaused = true;
        QueueWorkApp("--pause");
    }

    static void ResumeAfterGame()
    {
        if (!appPaused) return;
        appPaused = false;
        QueueWorkApp("--resume", true);
    }

    static string GameExe(string dir)
    {
        try
        {
            var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(dir, "game.json")));
            object n;
            string name = j.TryGetValue("exeName", out n) && n != null ? n.ToString() : j.TryGetValue("title", out n) && n != null ? n.ToString() : null;
            if (name != null && File.Exists(Path.Combine(dir, name + ".exe"))) return Path.Combine(dir, name + ".exe");
        }
        catch { }
        foreach (var f in Directory.GetFiles(dir, "*.exe"))
            if (!Path.GetFileName(f).StartsWith("AnyPS5", StringComparison.OrdinalIgnoreCase)) return f;
        return null;
    }

    static int RunGuard(string dir)
    {
        try
        {
            using (var g = Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(Tools, "game-guard.ps1") + "\" -GameDir \"" + dir + "\" -Force") { UseShellExecute = false, CreateNoWindow = true }))
            {
                if (!g.WaitForExit(300000)) return -1;
                return g.ExitCode;
            }
        }
        catch { return -1; }
    }

    async void Play(string dir, string user, string legacy)
    {
        if (starting) { Toast("A game is starting, one moment"); return; }
        if (GameRunning) { Toast((gameName ?? "A game") + " is still running. PS button > Close game first."); return; }
        if (TestMode) { Toast("Test mode: real games are not started"); return; }
        string full;
        try { full = Path.GetFullPath(dir).TrimEnd('\\'); } catch { return; }
        if (!full.StartsWith(Root + "\\", StringComparison.OrdinalIgnoreCase) || !Directory.Exists(full)) { Toast("That game folder is not in the games folder"); return; }
        string exe = GameExe(full);
        if (exe == null) { Toast("This game has no program yet"); return; }
        if (user != "" && !TestMode)
        {
            if (!Sys_Family.GameAllowed(user, Sys_Family.TitleIdFromFolder(full), SettingsFile)) { Toast("This user is not allowed to play this game (Settings > Family and Parental Controls)"); return; }
            if (Sys_Family.MinutesLeft(user, SettingsFile) <= 0) { Toast("Playtime for today is over for this user"); return; }
        }
        starting = true;
        try
        {
            int guard = await Task.Run(() => RunGuard(full));
            if (IsDisposed) return;
            if (guard == 2) { Toast("Something is already running from this game's folder (a test or an earlier copy). Close it, then try again."); return; }
            if (guard == 3) { Toast("The game's own libraries could not be put back (game-guard). Not starting it, so it does not run on the wrong files."); return; }
            if (guard != 0) { Toast("The library check did not finish (game-guard exit " + guard + "). Not starting the game."); return; }
            StartGame(exe, full, user, legacy);
        }
        catch (Exception ex) { Toast("Could not start it: " + ex.Message); }
        finally { starting = false; }
    }

    internal void StartGame(string exe, string full, string user = "", string legacy = "")
    {
        try
        {
            TrophiesIn(full, user, legacy);
            var start = new ProcessStartInfo(exe) { WorkingDirectory = full, UseShellExecute = false };
            string settings = Path.Combine(full, "game-env.txt");
            if (File.Exists(settings))
                foreach (var line in File.ReadAllLines(settings))
                {
                    int eq = line.IndexOf('=');
                    if (eq > 0 && !line.TrimStart().StartsWith("#")) start.EnvironmentVariables[line.Substring(0, eq).Trim()] = line.Substring(eq + 1).Trim();
                }
            string jobError;
            game = GameJob.Start(start, out jobError);
            if (jobError != null) Log("game is not in the app's job (it would outlive the app): " + jobError);
            familyUid = user ?? ""; familyOver = false; if (familyUid != "" && !TestMode) familyTimer.Start();
            gameName = Path.GetFileNameWithoutExtension(exe);
            gameDir = full;
            overlay = false;
            try { File.WriteAllText(GameMarker, game.Id.ToString()); } catch { }
            gameStart = DateTime.Now;
            PauseForGame();
            if (!IsAdmin && !TestMode) Toast("The pipeline waits with new tests while you play. Tests already running continue: AnyPS5.exe is not running as admin.");
            Log("game started: " + gameName + " pid " + game.Id + (GameJob.Holds(game) ? " (child of AnyPS5 pid " + Process.GetCurrentProcess().Id + ", in its job)" : ""));
            SendState();
            var started = game;
            game.EnableRaisingEvents = true;
            game.Exited += (s, e) => { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => GameClosed(started))); } catch { } };
            embedded = IntPtr.Zero;
            embedTimer.Start();
            padHeld = 0;
            StartTrophyWatch(full);
        }
        catch (Exception ex) { TrophiesOut(); Toast("Could not start it: " + ex.Message); }
    }

    internal bool GameEmbedded { get { return embedded != IntPtr.Zero && IsWindow(embedded) && gameHost.Visible; } }
    internal void CloseGameNow() { CloseGame(false); }
    internal void OpenGameMenu() { if (embedded != IntPtr.Zero) ShowOverlay(); }
    internal bool GameMenuUp { get { return overlay && view.Visible; } }
    internal bool PageVisible { get { return view.Visible; } }

    IntPtr GameWindow()
    {
        IntPtr found = IntPtr.Zero;
        uint want = (uint)game.Id;
        EnumWindows((h, l) =>
        {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (pid != want || !IsWindowVisible(h) || GetWindow(h, 4) != IntPtr.Zero) return true;
            RECT c; GetClientRect(h, out c);
            if (c.R - c.L < 50 || c.B - c.T < 50) return true;
            found = h;
            return false;
        }, IntPtr.Zero);
        return found;
    }

    void TryEmbed()
    {
        if (!GameRunning) { embedTimer.Stop(); return; }
        IntPtr h = GameWindow();
        if (h == IntPtr.Zero) return;
        embedTimer.Stop();
        RECT c; GetClientRect(h, out c);
        aspect = (double)(c.R - c.L) / (c.B - c.T);
        embeddedStyle = GetWindowLongPtr(h, -16);
        long style = (embeddedStyle.ToInt64() & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)) | WS_CHILD | WS_VISIBLE;
        SetWindowLongPtr(h, -16, new IntPtr(style));
        SetParent(h, gameHost.Handle);
        embedded = h;
        gameHost.Visible = true;
        gameHost.BringToFront();
        view.Visible = false;
        Fit();
        FocusGame();
    }

    void Fit()
    {
        if (embedded == IntPtr.Zero || !IsWindow(embedded)) return;
        int w = gameHost.ClientSize.Width, hgt = gameHost.ClientSize.Height;
        if (w < 10 || hgt < 10) return;
        int gw = w, gh = (int)(w / aspect);
        if (gh > hgt) { gh = hgt; gw = (int)(hgt * aspect); }
        SetWindowPos(embedded, IntPtr.Zero, (w - gw) / 2, (hgt - gh) / 2, gw, gh, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW | 0x4000);
    }

    bool InFront()
    {
        if (!IsHandleCreated || !Visible) return false;
        IntPtr fg = GetForegroundWindow();
        return fg != IntPtr.Zero && fg == GetAncestor(Handle, 2);
    }

    void SetHotkeys(bool on)
    {
        if (on == hotkeys || !IsHandleCreated) return;
        hotkeys = on;
        if (on) { RegisterHotKey(Handle, HK_FULL, 0, (uint)Keys.F11); RegisterHotKey(Handle, HK_CLOSE, 0x0004, (uint)Keys.Escape); }
        else { UnregisterHotKey(Handle, HK_FULL); UnregisterHotKey(Handle, HK_CLOSE); }
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == 0x0312 && m.WParam.ToInt32() == HK_FULL) { if (FullscreenRequested != null) FullscreenRequested(); return; }
        if (m.Msg == 0x0312 && m.WParam.ToInt32() == HK_CLOSE) { BeginInvoke((Action)(() => CloseGame(true))); return; }
        base.WndProc(ref m);
    }

    void PadTick()
    {
        bool guide = false;
        if (InFront())
            for (int i = 0; i < 4 && !guide; i++)
            {
                XPad g;
                try { if (XInputGetStateEx(i, out g) == 0 && (g.Buttons & 0x0400) != 0) guide = true; } catch { break; }
            }
        if (guide && !guideWas) PsButton();
        guideWas = guide;
        if (!GameRunning) { SetHotkeys(false); padHeld = 0; return; }
        bool front = InFront() && embedded != IntPtr.Zero;
        SetHotkeys(front);
        bool combo = false, backDown = false, startDown = false;
        if (front)
            for (int i = 0; i < 4; i++)
            {
                XPad s;
                try
                {
                    if (XInputGetState(i, out s) != 0) continue;
                    if ((s.Buttons & 0x0030) == 0x0030) combo = true;
                    if ((s.Buttons & 0x0020) != 0) backDown = true;
                    if ((s.Buttons & 0x0010) != 0) startDown = true;
                }
                catch { break; }
            }
        if (backDown) { backTicks++; if (startDown) backCombo = true; }
        else
        {
            if (backTicks > 0 && !backCombo && !overlay)
            {
                if (EasyShots) { if (backTicks >= 6) CreateButton(); else ShotInGame(); }
                else if (backTicks < 8) CreateButton();
            }
            backTicks = 0; backCombo = false;
        }
        padHeld = combo ? padHeld + 1 : 0;
        if (padHeld == 10) CloseGame(false);
    }

    async void CloseGame(bool ask)
    {
        if (gameSuspended) Wake();
        if (asking || !GameRunning) return;
        asking = true;
        try
        {
            if (ask && MessageBox.Show(FindForm(), "Close " + (gameName ?? "the game") + " and go back to the home screen?", "AnyPS5", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) { FocusGame(); return; }
            var g = game;
            if (embedded != IntPtr.Zero && IsWindow(embedded)) PostMessage(embedded, 0x0010, IntPtr.Zero, IntPtr.Zero);
            else { try { g.CloseMainWindow(); } catch { } }
            bool gone = await Task.Run(() => { try { return g.WaitForExit(5000); } catch { return true; } });
            if (!gone) { try { g.Kill(); } catch { } }
        }
        finally { asking = false; }
    }

    void CloseGameForExit()
    {
        var g = game;
        game = null;
        Log("app closing: closing " + gameName + " first (it is in the app's job and would be ended with it)");
        try
        {
            if (embedded != IntPtr.Zero && IsWindow(embedded)) PostMessage(embedded, 0x0010, IntPtr.Zero, IntPtr.Zero);
            else g.CloseMainWindow();
        }
        catch { }
        var until = DateTime.Now.AddSeconds(10);
        while (!g.HasExited && DateTime.Now < until) { Application.DoEvents(); g.WaitForExit(100); }
        if (!g.HasExited) { try { g.Kill(); g.WaitForExit(3000); } catch { } }
        int code = 0; try { code = g.ExitCode; } catch { }
        Log("game ended: " + gameName + " exit 0x" + code.ToString("X8") + " (app closing)");
        try { File.AppendAllText(Path.Combine(Tools, "live", TestMode ? "anyps5-app-plays-test.log" : "anyps5-app-plays.log"), gameStart.ToString("yyyy-MM-dd HH:mm:ss") + "\t" + (int)(DateTime.Now - gameStart).TotalSeconds + " s\t0x" + code.ToString("X8") + "\t" + gameName + "\t" + gameDir + Environment.NewLine); } catch { }
        try { File.Delete(GameMarker); } catch { }
    }

    public void Release()
    {
        familyTimer.Stop(); familyUid = "";
        if (gameSuspended) Wake();
        if (GameRunning && GameJob.Holds(game)) CloseGameForExit();
        StopTrophyWatch();
        if (trophyDirIn != null && GameRunning && !TestMode)
        {
            try
            {
                string cmd = "Wait-Process -Id " + game.Id + " -ErrorAction SilentlyContinue; python -I '" + Path.Combine(Folder, "trophy_users.py").Replace("'", "''") + "' out '" + trophyDirIn.Replace("'", "''") + "'";
                Process.Start(new ProcessStartInfo("conhost.exe", "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"" + cmd + "\"") { UseShellExecute = false, CreateNoWindow = true });
                Log("trophies: " + trophyDirIn + " goes back to its user when the game ends");
            }
            catch (Exception ex) { Log("could not start the trophy watcher: " + ex.Message); }
            trophyDirIn = null;
        }
        else TrophiesOut();
        embedTimer.Stop(); SetHotkeys(false);
        overlay = false;
        if (embedded != IntPtr.Zero && IsWindow(embedded) && GameRunning)
        {
            SetParent(embedded, IntPtr.Zero);
            SetWindowLongPtr(embedded, -16, embeddedStyle);
            SetWindowPos(embedded, IntPtr.Zero, 100, 100, 1600, 900, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW | 0x4000);
        }
        embedded = IntPtr.Zero;
        if (game != null) { try { File.Delete(GameMarker); } catch { } }
        if (appPaused && GameRunning && !TestMode)
        {
            appPaused = false;
            string resume = IsAdmin ? "Start-Process -FilePath '" + Path.Combine(Root, "AnyPS5 Games.exe").Replace("'", "''") + "' -ArgumentList '--resume' -WorkingDirectory '" + Root.Replace("'", "''") + "' -Wait"
                : "if ((Test-Path '" + PauseFlag.Replace("'", "''") + "') -and ((Get-Content '" + PauseFlag.Replace("'", "''") + "' -Raw) -match 'AnyPS5 app')) { Remove-Item '" + PauseFlag.Replace("'", "''") + "' }";
            try
            {
                Process.Start(new ProcessStartInfo("conhost.exe", "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"Wait-Process -Id " + game.Id + " -ErrorAction SilentlyContinue; " + resume.Replace("\"", "\\\"") + "\"") { UseShellExecute = false, CreateNoWindow = true });
                Log("released " + gameName + " still running; the pipeline resumes when it ends");
            }
            catch (Exception ex) { Log("could not start the resume watcher: " + ex.Message); }
        }
        else ResumeAfterGame();
        game = null;
    }

    void GameClosed(Process which)
    {
        if (which != game) return;
        int code = 0; try { code = which.ExitCode; } catch { }
        int secs = (int)(DateTime.Now - gameStart).TotalSeconds;
        Log("game ended: " + gameName + " exit 0x" + code.ToString("X8") + " after " + secs + " s");
        try { File.AppendAllText(Path.Combine(Tools, "live", TestMode ? "anyps5-app-plays-test.log" : "anyps5-app-plays.log"), gameStart.ToString("yyyy-MM-dd HH:mm:ss") + "\t" + secs + " s\t0x" + code.ToString("X8") + "\t" + gameName + "\t" + gameDir + Environment.NewLine); } catch { }
        string closedName = gameName;
        familyTimer.Stop(); familyUid = "";
        StopTrophyWatch();
        CheckTrophiesIn(gameDir);
        TrophiesOut();
        embedTimer.Stop(); SetHotkeys(false);
        embedded = IntPtr.Zero;
        overlay = false;
        game = null;
        Send("{\"ingame\":false}");
        SendState();
        gameHost.Visible = false;
        view.Visible = true;
        try { File.Delete(GameMarker); } catch { }
        ResumeAfterGame();
        var f = FindForm();
        if (f != null && InFront()) f.Activate();
        view.Focus();
        if (code == 0 || code == 1) Toast("Welcome back");
        else if (GameJob.LoaderError(code) != null) Toast(closedName + " could not start: " + GameJob.LoaderError(code) + " (0x" + code.ToString("X8") + "). Rebuild it from the work app.");
        else Toast(closedName + " stopped with error 0x" + code.ToString("X8") + " after " + secs + " s. Its anyps5.log is in the game folder.");
        RefreshLibrary();
        if (reloadWanted) { reloadTimer.Stop(); reloadTimer.Start(); }
    }

    static string SafeUid(string uid) { return Regex.Replace(uid ?? "", "[^A-Za-z0-9_-]", ""); }

    internal static string RunPython(string args, int ms)
    {
        try
        {
            using (var p = Process.Start(new ProcessStartInfo("python.exe", "-I " + args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }))
            {
                var output = p.StandardOutput.ReadToEndAsync();
                if (!p.WaitForExit(ms)) { try { p.Kill(); } catch { } return "python did not finish in " + (ms / 1000) + " s"; }
                return output.Result.Trim();
            }
        }
        catch (Exception ex) { return "python failed: " + ex.Message; }
    }

    void FamilyTick()
    {
        if (!GameRunning || familyUid == "") { familyTimer.Stop(); return; }
        string uid = familyUid;
        Sys_Family.AddMinute(uid);
        int left = Sys_Family.MinutesLeft(uid, SettingsFile);
        if (left == 15 || left == 5 || left == 1) FamilyNotice("Playtime ends in " + left + (left == 1 ? " minute" : " minutes"));
        if (left <= 0 && !familyOver)
        {
            familyOver = true;
            if (Sys_Family.WhenPlaytimeEnds(uid, SettingsFile) == "close") { FamilyNotice("Playtime is over for today. Closing the game."); CloseGame(false); }
            else FamilyNotice("Playtime is over for today");
        }
    }

    void FamilyNotice(string text)
    {
        Toast(text);
        var f = FindForm();
        var area = gameHost.Visible ? gameHost.RectangleToScreen(gameHost.ClientRectangle) : RectangleToScreen(ClientRectangle);
        if (gameHost.Visible && !view.Visible) TrophyPopup.Show(f, area, text, "notice", "", false);
    }

    void SetTrophyOwner(string uid)
    {
        uid = SafeUid(uid);
        if (TestMode || uid == "") return;
        string f = Path.Combine(Tools, "live", "trophy-legacy-owner.txt");
        try { if (!File.Exists(f) || File.ReadAllText(f).Trim() != uid) File.WriteAllText(f, uid); } catch { }
    }

    void TrophiesIn(string full, string user, string legacy)
    {
        trophyDirIn = null;
        user = SafeUid(user); legacy = SafeUid(legacy);
        if (user == "") return;
        string r = RunPython("\"" + Path.Combine(Folder, "trophy_users.py") + "\" in \"" + full + "\" " + user + (legacy != "" ? " " + legacy : ""), 30000);
        Log("trophies: " + r);
        if (r.StartsWith("in place")) trophyDirIn = full;
    }

    void TrophiesOut()
    {
        if (trophyDirIn == null) return;
        string dir = trophyDirIn;
        trophyDirIn = null;
        Task.Run(() =>
        {
            Log("trophies: " + RunPython("\"" + Path.Combine(Folder, "trophy_users.py") + "\" out \"" + dir + "\"", 30000));
            if (!TestMode) RunPython("\"" + Path.Combine(Folder, "trophies.py") + "\" -q", 180000);
        }).ContinueWith(t => { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => Send("{\"trophiesChanged\":true}"))); } catch { } });
    }

    static HashSet<string> ReadTrophyIds(string dir)
    {
        var ids = new HashSet<string>();
        if (!Directory.Exists(dir)) return ids;
        foreach (var f in Directory.GetFiles(dir, "*.txt"))
        {
            var m = Regex.Match(Path.GetFileName(f), @"^\d+_(\d+)\.txt$");
            if (!m.Success) continue;
            string text = null;
            for (int i = 0; i < 6 && text == null; i++)
            {
                try { using (var fs = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) using (var r = new StreamReader(fs)) text = r.ReadToEnd(); }
                catch { System.Threading.Thread.Sleep(60); }
            }
            foreach (var line in (text ?? "").Split('\n'))
            {
                var parts = line.Trim().Split(' ');
                int id;
                if (parts.Length >= 2 && int.TryParse(parts[0], out id)) ids.Add(m.Groups[1].Value + ":" + id);
            }
        }
        return ids;
    }

    void StartTrophyWatch(string full)
    {
        StopTrophyWatch();
        trophyGameDir = full; trophyManifest = null;
        string dir = Path.Combine(full, "_trophy");
        try { Directory.CreateDirectory(dir); } catch { return; }
        trophyKnown = ReadTrophyIds(dir);
        try
        {
            trophyWatch = new FileSystemWatcher(dir, "*.txt") { NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size, IncludeSubdirectories = false };
            FileSystemEventHandler h = (s, e) => { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => { trophyTimer.Stop(); trophyTimer.Start(); })); } catch { } };
            trophyWatch.Changed += h; trophyWatch.Created += h; trophyWatch.Renamed += (s, e) => h(s, e);
            trophyWatch.EnableRaisingEvents = true;
        }
        catch (Exception ex) { Log("trophy watch failed: " + ex.Message); }
    }

    void StopTrophyWatch()
    {
        trophyTimer.Stop();
        if (trophyWatch != null) { try { trophyWatch.EnableRaisingEvents = false; trophyWatch.Dispose(); } catch { } trophyWatch = null; }
    }

    void CheckTrophies() { if (trophyGameDir != null) CheckTrophiesIn(trophyGameDir); }

    void CheckTrophiesIn(string full)
    {
        if (full == null || full != trophyGameDir) return;
        foreach (var key in ReadTrophyIds(Path.Combine(full, "_trophy")))
        {
            if (!trophyKnown.Add(key)) continue;
            var parts = key.Split(':');
            ShowTrophy(full, int.Parse(parts[0]), int.Parse(parts[1]));
        }
    }

    void ShowTrophy(string full, int label, int id)
    {
        string folder = Path.GetFileName(full), title = Regex.Replace(folder, @"\s*\([A-Z]{4}\d{5}\)$", "");
        var tm = Regex.Match(folder, @"\(([A-Z]{4}\d{5})\)$");
        string set = (tm.Success ? tm.Groups[1].Value : "") + "_" + label.ToString("00");
        string name = "", grade = "bronze";
        try
        {
            if (trophyManifest == null)
            {
                string mf = Path.Combine(Folder, "cache", "trophies", "manifest.json");
                trophyManifest = File.Exists(mf) ? new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.Deserialize<Dictionary<string, object>>(File.ReadAllText(mf)) : new Dictionary<string, object>();
            }
            object entry;
            if (trophyManifest.TryGetValue(set, out entry))
            {
                var pkg = (Dictionary<string, object>)((Dictionary<string, object>)entry)["pkg"];
                foreach (var t in (System.Collections.ArrayList)pkg["trophies"])
                {
                    var d = (Dictionary<string, object>)t;
                    if (Convert.ToInt32(d["id"]) != id) continue;
                    name = Convert.ToString(d["name"]); grade = Convert.ToString(d["grade"]);
                    break;
                }
            }
        }
        catch (Exception ex) { Log("trophy names unavailable: " + ex.Message); }
        string png = Path.Combine(Folder, "cache", "trophies", set, "t" + id.ToString("0000") + ".png");
        CaptureTrophy(full, label, id);
        LastTrophy = name != "" ? name : "trophy " + id;
        Log("trophy earned: " + title + " / " + LastTrophy + " (" + grade + ")");
        var f = FindForm();
        var area = gameHost.Visible ? gameHost.RectangleToScreen(gameHost.ClientRectangle) : RectangleToScreen(ClientRectangle);
        if (!dndOn && SettingValue("notes", "trophyPopup", "true") != "false") TrophyPopup.Show(f, area, name != "" ? name : "New trophy", grade, png, SoundsOn());
        Send(Json(new Dictionary<string, object> { { "trophy", new Dictionary<string, object> { { "key", set }, { "id", id }, { "name", name }, { "grade", grade }, { "title", title }, { "icon", "cache/trophies/" + set + "/t" + id.ToString("0000") + ".webp" } } } }));
    }

    void CaptureTrophy(string full, int label, int id)
    {
        if (embedded == IntPtr.Zero || !IsWindow(embedded) || view.Visible || full == null || CaptureSetting("trophy", "true") == "false") return;
        try
        {
            RECT r; GetWindowRect(embedded, out r);
            var area = Rectangle.FromLTRB(r.L, r.T, r.R, r.B);
            if (area.Width < 50 || area.Height < 50) return;
            string dir = Path.Combine(full, "screenshots");
            Directory.CreateDirectory(dir);
            string file = Path.Combine(dir, "Trophy " + label.ToString("00") + "-" + id + " " + DateTime.Now.ToString("yyyy-MM-dd HH-mm-ss") + ".png");
            using (var bmp = new Bitmap(area.Width, area.Height))
            {
                using (var gr = Graphics.FromImage(bmp)) gr.CopyFromScreen(area.Location, Point.Empty, area.Size);
                bmp.Save(file, System.Drawing.Imaging.ImageFormat.Png);
            }
            Log("trophy capture: " + file);
            RefreshMedia();
        }
        catch (Exception ex) { Log("trophy capture failed: " + ex.Message); }
    }

    void SysCall(string area, Dictionary<string, object> msg)
    {
        object a, ar, idv;
        string action = msg.TryGetValue("action", out a) ? Convert.ToString(a) : "";
        var args = msg.TryGetValue("args", out ar) && ar is Dictionary<string, object> ? (Dictionary<string, object>)ar : new Dictionary<string, object>();
        int id = msg.TryGetValue("id", out idv) ? Convert.ToInt32(idv) : 0;
        Task.Run(() =>
        {
            var reply = new Dictionary<string, object> { { "id", id } };
            try
            {
                string name = Regex.Replace(area ?? "", "[^A-Za-z]", "");
                var t = name.Length == 0 ? null : Type.GetType("Sys_" + char.ToUpperInvariant(name[0]) + name.Substring(1).ToLowerInvariant());
                if (t == null) throw new NotSupportedException("Available in AnyPS5 OS");
                var m = t.GetMethod("Handle", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
                reply["data"] = m.Invoke(null, new object[] { action, args });
                reply["ok"] = true;
            }
            catch (Exception ex)
            {
                var e = ex is System.Reflection.TargetInvocationException && ex.InnerException != null ? ex.InnerException : ex;
                reply["ok"] = false; reply["unsupported"] = e is NotSupportedException; reply["error"] = e.Message;
                if (!(e is NotSupportedException)) Log("system " + area + "/" + action + " failed: " + e.Message);
            }
            try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => Send(Json(new Dictionary<string, object> { { "sysReply", reply } })))); } catch { }
        });
    }

    bool dlRunning;
    void RefreshDownloads()
    {
        if (TestMode || dlRunning) return;
        dlRunning = true;
        Task.Run(() => RunPython("\"" + Path.Combine(Folder, "downloads.py") + "\" -q", 30000))
            .ContinueWith(t => { dlRunning = false; try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => Send("{\"downloadsChanged\":true}"))); } catch { } });
    }

    void RefreshMedia()
    {
        if (TestMode) return;
        Task.Run(() => RunPython("\"" + Path.Combine(Folder, "media.py") + "\" -q", 120000))
            .ContinueWith(t => { try { if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => Send("{\"mediaChanged\":true}"))); } catch { } });
    }

    internal string LastShot = "";

    static string CaptureSetting(string key, string def) { return SettingValue("capture", key, def); }

    static string SettingValue(string group, string key, string def)
    {
        try
        {
            var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(SettingsFile));
            object c, v;
            Dictionary<string, object> d = j;
            if (group != null) { if (!j.TryGetValue(group, out c) || !(c is Dictionary<string, object>)) return def; d = (Dictionary<string, object>)c; }
            if (d.TryGetValue(key, out v) && v != null) return v is bool ? ((bool)v ? "true" : "false") : v.ToString().ToLowerInvariant();
        }
        catch { }
        return def;
    }

    static bool SoundsOn()
    {
        if (TestMode) return false;
        try
        {
            var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(SettingsFile));
            object v;
            return !(j.TryGetValue("sounds", out v) && v is bool && !(bool)v) && !(j.TryGetValue("trophySound", out v) && v is bool && !(bool)v);
        }
        catch { return true; }
    }

    internal bool TrophyPopupUp { get { return TrophyPopup.Current != null && !TrophyPopup.Current.IsDisposed && TrophyPopup.Current.Visible; } }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { familyTimer.Dispose(); PadWatch.PsPressed -= OnPs; PadWatch.CreatePressed -= OnCreate; PadWatch.CreateReleased -= OnCreateUp; Release(); embedTimer.Dispose(); padTimer.Dispose(); trophyTimer.Dispose(); }
        base.Dispose(disposing);
    }
}

// The PS5 trophy pop-up over a running game (Blu 11-15): a small owned window at the game's top right that never takes
// focus (WS_EX_NOACTIVATE), fades in, stays 5 s and fades out; more trophies queue behind it.
class TrophyPopup : Form
{
    static readonly Queue<Action> waiting = new Queue<Action>();
    public static TrophyPopup Current;
    static System.Media.SoundPlayer player;
    static byte[] chime;
    readonly Timer tick = new Timer { Interval = 30 };
    readonly string trophyName, grade;
    readonly Image icon;
    readonly float s;
    readonly DateTime t0 = DateTime.Now;

    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams { get { var p = base.CreateParams; p.ExStyle |= 0x08000000 | 0x00000080; return p; } }

    public static void Show(Form owner, Rectangle area, string name, string grade, string iconPath, bool sound)
    {
        Action a = () =>
        {
            var f = new TrophyPopup(name, grade, iconPath, area);
            if (owner != null && !owner.IsDisposed) f.Owner = owner;
            Current = f;
            f.Show();
            if (sound) Chime();
        };
        if (Current != null && !Current.IsDisposed) waiting.Enqueue(a); else a();
    }

    TrophyPopup(string name, string grade, string iconPath, Rectangle area)
    {
        trophyName = name; this.grade = grade;
        s = Math.Max(0.6f, Math.Min(2f, area.Height / 1080f));
        try { if (File.Exists(iconPath)) using (var img = Image.FromFile(iconPath)) icon = new Bitmap(img); } catch { }
        FormBorderStyle = FormBorderStyle.None; ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; TopMost = false;
        BackColor = Color.FromArgb(30, 32, 40); DoubleBuffered = true; Opacity = 0;
        Size = new Size((int)(500 * s), (int)(112 * s));
        Location = new Point(area.Right - Width - (int)(32 * s), area.Top + (int)(32 * s));
        using (var path = new System.Drawing.Drawing2D.GraphicsPath())
        {
            int r = (int)(12 * s);
            path.AddArc(0, 0, r * 2, r * 2, 180, 90); path.AddArc(Width - r * 2, 0, r * 2, r * 2, 270, 90);
            path.AddArc(Width - r * 2, Height - r * 2, r * 2, r * 2, 0, 90); path.AddArc(0, Height - r * 2, r * 2, r * 2, 90, 90);
            path.CloseFigure();
            Region = new Region(path);
        }
        tick.Tick += (o, e) => Step();
        tick.Start();
    }

    void Step()
    {
        double ms = (DateTime.Now - t0).TotalMilliseconds;
        if (ms < 250) Opacity = ms / 250 * 0.97;
        else if (ms < 5250) Opacity = 0.97;
        else if (ms < 5700) Opacity = 0.97 * (1 - (ms - 5250) / 450);
        else { tick.Stop(); Close(); }
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        base.OnFormClosed(e);
        tick.Dispose();
        if (icon != null) icon.Dispose();
        if (Current == this) Current = null;
        if (waiting.Count > 0) waiting.Dequeue()();
    }

    static Color GradeColor(string g)
    {
        return g == "notice" ? Color.FromArgb(255, 179, 92) : g == "platinum" ? Color.FromArgb(185, 211, 245) : g == "gold" ? Color.FromArgb(227, 179, 71) : g == "silver" ? Color.FromArgb(195, 200, 209) : Color.FromArgb(201, 135, 90);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        float pad = 16 * s, ic = Height - 2 * pad, x = pad * 2 + ic;
        if (icon != null) g.DrawImage(icon, pad, pad, ic, ic);
        else using (var b = new SolidBrush(Color.FromArgb(52, 56, 68))) g.FillRectangle(b, pad, pad, ic, ic);
        using (var b = new SolidBrush(GradeColor(grade))) g.FillEllipse(b, x, pad + 5 * s, 13 * s, 13 * s);
        string label = grade == "notice" ? "Family and Parental Controls" : (grade.Length > 0 ? char.ToUpper(grade[0]) + grade.Substring(1) : "Bronze") + " trophy earned";
        using (var f1 = new Font("Segoe UI", 15 * s, FontStyle.Regular, GraphicsUnit.Pixel))
        using (var dim = new SolidBrush(Color.FromArgb(185, 190, 200)))
            g.DrawString(label, f1, dim, x + 20 * s, pad + 1 * s);
        using (var f2 = new Font("Segoe UI Semibold", 23 * s, FontStyle.Regular, GraphicsUnit.Pixel))
        using (var sf = new StringFormat { Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap })
            g.DrawString(trophyName, f2, Brushes.White, new RectangleF(x, pad + 30 * s, Width - x - pad, 36 * s), sf);
    }

    static void Chime()
    {
        try
        {
            if (chime == null)
            {
                int rate = 44100;
                var samples = new List<short>();
                Tone(samples, rate, 988, 0.10);
                Tone(samples, rate, 1480, 0.26);
                var ms = new MemoryStream();
                var w = new BinaryWriter(ms);
                w.Write(new[] { 'R', 'I', 'F', 'F' }); w.Write(36 + samples.Count * 2); w.Write(new[] { 'W', 'A', 'V', 'E', 'f', 'm', 't', ' ' });
                w.Write(16); w.Write((short)1); w.Write((short)1); w.Write(rate); w.Write(rate * 2); w.Write((short)2); w.Write((short)16);
                w.Write(new[] { 'd', 'a', 't', 'a' }); w.Write(samples.Count * 2);
                foreach (var v in samples) w.Write(v);
                chime = ms.ToArray();
            }
            player = new System.Media.SoundPlayer(new MemoryStream(chime));
            player.Play();
        }
        catch { }
    }

    static void Tone(List<short> samples, int rate, double hz, double secs)
    {
        int n = (int)(rate * secs);
        for (int i = 0; i < n; i++)
        {
            double t = (double)i / rate, env = Math.Min(1.0, i / (rate * 0.006)) * Math.Exp(-t * 9);
            samples.Add((short)(Math.Sin(2 * Math.PI * hz * t) * env * 0.28 * short.MaxValue));
        }
    }
}

class ConsoleApp : Form
{
    readonly ConsoleView console = new ConsoleView { Dock = DockStyle.Fill };

    [STAThread]
    static void Main(string[] args)
    {
        ConsoleBoot.Install(AppDomain.CurrentDomain.BaseDirectory);
        Application.EnableVisualStyles();
        Application.Run(new ConsoleApp());
    }

    ConsoleApp()
    {
        Text = "AnyPS5";
        BackColor = Color.FromArgb(6, 7, 12);
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1600, 900);
        if (File.Exists(ConsoleView.FullscreenFile)) { FormBorderStyle = FormBorderStyle.None; WindowState = FormWindowState.Maximized; }
        try { Icon = new Icon(Path.Combine(ConsoleBoot.Root, "_tools", "console", "anyps5.ico")); } catch { }
        Controls.Add(console);
        console.FullscreenRequested += ToggleFullscreen;
        console.ExitRequested += Close;
        console.MachinesRequested += () => { try { Process.Start(new ProcessStartInfo(Path.Combine(ConsoleBoot.Root, "AnyPS5 Games.exe")) { WorkingDirectory = ConsoleBoot.Root }); } catch { } };
        console.WorkAppRequested += () => { try { Process.Start(new ProcessStartInfo(Path.Combine(ConsoleBoot.Root, "AnyPS5 Games.exe")) { WorkingDirectory = ConsoleBoot.Root }); } catch { } };
        Activated += (s, e) => console.FocusContent();
        Shown += (s, e) => { console.FocusContent(); try { File.WriteAllText(Path.Combine(ConsoleBoot.Root, "_tools", "live", "anyps5-window.txt"), Handle.ToInt64().ToString()); } catch { } };
    }

    void ToggleFullscreen()
    {
        bool full = FormBorderStyle == FormBorderStyle.None;
        WindowState = FormWindowState.Normal;
        FormBorderStyle = full ? FormBorderStyle.Sizable : FormBorderStyle.None;
        if (!full) WindowState = FormWindowState.Maximized; else { Size = new Size(1600, 900); CenterToScreen(); }
        try { if (full) File.Delete(ConsoleView.FullscreenFile); else File.WriteAllText(ConsoleView.FullscreenFile, "fullscreen"); } catch { }
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        console.Release();
        ConsoleView.FlushWork(20000);
        base.OnFormClosing(e);
    }
}

// PlayStation controllers (DualSense, DualSense Edge, DualShock 4) read straight from HID, shared with the game, so the
// PS button works on the home screen and in a game. Steam Input or DS4Windows can hold a pad exclusively; it then
// shows as "in use by another program" in Settings > Controllers.
static class PadWatch
{
    public static event Action PsPressed, CreatePressed, CreateReleased;
    static readonly Dictionary<string, string> pads = new Dictionary<string, string>();
    static readonly Dictionary<string, DateTime> busy = new Dictionary<string, DateTime>();
    static System.Threading.Thread scanner;

    public static string[] Describe() { lock (pads) { var l = new List<string>(pads.Values); return l.ToArray(); } }

    public static void Start()
    {
        if (scanner != null) return;
        scanner = new System.Threading.Thread(() => { while (true) { try { Scan(); } catch { } System.Threading.Thread.Sleep(4000); } }) { IsBackground = true, Name = "PadWatch" };
        scanner.Start();
    }

    struct DevIface { public int cbSize; public Guid Class; public int Flags; public IntPtr Reserved; }
    struct Attrs { public int Size; public ushort Vendor, Product, Version; }
    struct Caps { public ushort Usage, UsagePage, InputLength, OutputLength, FeatureLength; [MarshalAs(UnmanagedType.ByValArray, SizeConst = 27)] public ushort[] Rest; }
    [DllImport("hid.dll")] static extern void HidD_GetHidGuid(out Guid g);
    [DllImport("hid.dll")] static extern bool HidD_GetAttributes(Microsoft.Win32.SafeHandles.SafeFileHandle h, ref Attrs a);
    [DllImport("hid.dll")] static extern bool HidD_GetPreparsedData(Microsoft.Win32.SafeHandles.SafeFileHandle h, out IntPtr p);
    [DllImport("hid.dll")] static extern bool HidD_FreePreparsedData(IntPtr p);
    [DllImport("hid.dll")] static extern int HidP_GetCaps(IntPtr p, out Caps c);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode)] static extern IntPtr SetupDiGetClassDevsW(ref Guid g, IntPtr e, IntPtr w, int flags);
    [DllImport("setupapi.dll")] static extern bool SetupDiEnumDeviceInterfaces(IntPtr set, IntPtr info, ref Guid g, int i, ref DevIface d);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode)] static extern bool SetupDiGetDeviceInterfaceDetailW(IntPtr set, ref DevIface d, IntPtr detail, int size, out int need, IntPtr info);
    [DllImport("setupapi.dll")] static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr t);

    static string Model(ushort vendor, ushort product)
    {
        if (vendor != 0x054C) return null;
        if (product == 0x0CE6) return "DualSense";
        if (product == 0x0DF2) return "DualSense Edge";
        if (product == 0x05C4 || product == 0x09CC) return "DualShock 4";
        return null;
    }

    static void Scan()
    {
        Guid g; HidD_GetHidGuid(out g);
        IntPtr set = SetupDiGetClassDevsW(ref g, IntPtr.Zero, IntPtr.Zero, 0x12);
        if (set == new IntPtr(-1)) return;
        try
        {
            for (int i = 0; ; i++)
            {
                var d = new DevIface { cbSize = Marshal.SizeOf(typeof(DevIface)) };
                if (!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref g, i, ref d)) break;
                int need;
                SetupDiGetDeviceInterfaceDetailW(set, ref d, IntPtr.Zero, 0, out need, IntPtr.Zero);
                if (need <= 0) continue;
                IntPtr buf = Marshal.AllocHGlobal(need);
                string path;
                try
                {
                    Marshal.WriteInt32(buf, IntPtr.Size == 8 ? 8 : 6);
                    if (!SetupDiGetDeviceInterfaceDetailW(set, ref d, buf, need, out need, IntPtr.Zero)) continue;
                    path = Marshal.PtrToStringUni(new IntPtr(buf.ToInt64() + 4));
                }
                finally { Marshal.FreeHGlobal(buf); }
                lock (pads) { if (pads.ContainsKey(path) && !busy.ContainsKey(path)) continue; DateTime retry; if (busy.TryGetValue(path, out retry) && DateTime.Now < retry) continue; }
                Probe(path);
            }
        }
        finally { SetupDiDestroyDeviceInfoList(set); }
    }

    static void Probe(string path)
    {
        string model;
        using (var q = CreateFileW(path, 0, 3, IntPtr.Zero, 3, 0, IntPtr.Zero))
        {
            if (q.IsInvalid) return;
            var a = new Attrs { Size = Marshal.SizeOf(typeof(Attrs)) };
            if (!HidD_GetAttributes(q, ref a)) return;
            model = Model(a.Vendor, a.Product);
        }
        if (model == null) return;
        var h = CreateFileW(path, 0x80000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (h.IsInvalid) { lock (pads) { pads[path] = model + " (in use by another program, PS button not available)"; busy[path] = DateTime.Now.AddSeconds(30); } return; }
        lock (pads) busy.Remove(path);
        int len = 64;
        IntPtr pre;
        if (HidD_GetPreparsedData(h, out pre)) { Caps c; if (HidP_GetCaps(pre, out c) >= 0 && c.InputLength > 0) len = c.InputLength; HidD_FreePreparsedData(pre); }
        bool usb = len == 64;
        lock (pads) pads[path] = model + (usb ? " (USB)" : " (Bluetooth)");
        new System.Threading.Thread(() => Read(path, h, model, len, usb)) { IsBackground = true, Name = "Pad " + model }.Start();
    }

    static void Read(string path, Microsoft.Win32.SafeHandles.SafeFileHandle h, string model, int len, bool usb)
    {
        bool ds4 = model == "DualShock 4", was = false, wasCreate = false;
        var buf = new byte[len];
        try
        {
            using (var fs = new FileStream(h, FileAccess.Read, len, false))
                while (true)
                {
                    int n = fs.Read(buf, 0, len);
                    if (n <= 0) break;
                    int at;
                    if (ds4) at = buf[0] == 0x11 ? 9 : 7;
                    else if (buf[0] == 0x31) at = 11;
                    else at = usb ? 10 : 7;
                    if (at >= n) continue;
                    bool ps = (buf[at] & 1) != 0, create = (buf[at - 1] & 0x10) != 0;
                    if (ps && !was && PsPressed != null) PsPressed();
                    if (create && !wasCreate && CreatePressed != null) CreatePressed();
                    if (!create && wasCreate && CreateReleased != null) CreateReleased();
                    was = ps; wasCreate = create;
                }
        }
        catch { }
        finally { lock (pads) pads.Remove(path); }
    }
}
