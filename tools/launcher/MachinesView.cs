// AnyPS5 Games window (Blu 11-17: the Machines tab was removed; its code is kept in archive\MachinesView-with-machines-tab-20261117.cs
// with machines.json, to bring back if VM desktops are wanted again). Tabs: AnyPS5 Games (embedded launcher + games) and Open AnyPS5.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Windows.Forms;


interface IFocusable { bool Owns(IntPtr h); void FocusDesktop(); }

// The "AnyPS5 Games" tab: Blu's games launcher (AnyPS5 Games.exe) embedded like the viewers. A game Blu starts (from the
// launcher or by double-click) is put inside this tab over the launcher, kept in its own shape; F11 or Full screen makes the
// whole window full screen, "Show launcher" switches back while it keeps running. A game the launcher put inside itself
// ("Put it inside") also works, its F11 then fills the whole window too. Games the pipeline starts (same rule as the
// launcher's pause-for-game) are taken in quietly, without switching tabs or taking focus: they wait in an off-screen
// window that stays visible (a hidden window keeps rendering, but PrintWindow then returns the screen, so window-shot.ps1
// could not capture it), and "Pipeline tests (N)" brings them into the tab while Blu watches. Every embedded game's title
// goes to _tools\live\title-<pid>.txt like the launcher writes it.
class ParkForm : Form
{
    public ParkForm()
    {
        FormBorderStyle = FormBorderStyle.None; ShowInTaskbar = false; StartPosition = FormStartPosition.Manual;
        Location = new Point(-12000, -12000); Size = new Size(64, 64); BackColor = Color.Black; Text = "AnyPS5 Machines pipeline tests";
    }
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams { get { var cp = base.CreateParams; cp.ExStyle |= 0x80 | 0x08000000; return cp; } }
}

class GamesView : Panel, IFocusable
{
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    class TestGame { public IntPtr Hwnd, Style; public uint Pid; public int W, H; public RECT Rect; public string Title; }
    readonly List<TestGame> tests = new List<TestGame>();
    readonly Dictionary<uint, DateTime> pendingTests = new Dictionary<uint, DateTime>();
    readonly Panel testsHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.Black, Visible = false };
    readonly Button testsButton = new Button { Text = "Pipeline tests", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White, Visible = false };
    ParkForm park;
    bool showingTests, testsInView;
    IntPtr prevForeground;
    int testCount = -1;
    [DllImport("user32.dll")] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr h);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] static extern IntPtr GetWindowLongPtr(IntPtr h, int i);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] static extern IntPtr SetWindowLongPtr(IntPtr h, int i, IntPtr v);
    [DllImport("user32.dll")] static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool r);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsChild(IntPtr parent, IntPtr child);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern IntPtr SetFocus(IntPtr h);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a, uint b, bool on);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    const int WM_SHOWWINDOW = 0x0018;
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr h, int id, uint mods, uint key);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr h, int id);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out RECT r);
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr l);
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool QueryFullProcessImageNameW(IntPtr p, int flags, System.Text.StringBuilder s, ref int n);
    const int GWL_STYLE = -16, Hotkey = 0x5F12, WM_HOTKEY = 0x0312;
    const long WS_CHILD = 0x40000000, WS_VISIBLE = 0x10000000, WS_POPUP = 0x80000000, WS_CAPTION = 0xC00000, WS_THICKFRAME = 0x40000,
               WS_SYSMENU = 0x80000, WS_MINIMIZEBOX = 0x20000, WS_MAXIMIZEBOX = 0x10000, WS_MINIMIZE = 0x20000000, WS_MAXIMIZE = 0x1000000;
    const long Frame = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX;
    const uint SWP = 0x0001 | 0x0002 | 0x0004 | 0x0020;
    public const string Root = @"D:\AnyPS5 Games\";
    const string LauncherExe = Root + "AnyPS5 Games.exe";
    static readonly string LiveDir = Path.Combine(Root, "_tools", "live");

    readonly Panel host = new Panel { Dock = DockStyle.Fill, BackColor = Color.Black };
    readonly Panel gameHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.Black, Visible = false };
    readonly FlowLayoutPanel gameBar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 32, Padding = new Padding(4, 3, 0, 0), WrapContents = false, BackColor = Color.FromArgb(32, 34, 44), Visible = false };
    readonly Button switchButton = new Button { Text = "Show launcher", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White };
    readonly Button fullButton = new Button { Text = "Full screen (F11)", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White };
    readonly Button popButton = new Button { Text = "Pop out", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White };
    readonly Label gameTitle = new Label { AutoSize = true, ForeColor = Color.Gainsboro, Padding = new Padding(8, 6, 0, 0) };
    readonly Label note = new Label { Dock = DockStyle.Fill, ForeColor = Color.Silver, TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Segoe UI", 11) };
    readonly Button startButton = new Button { Text = "Start AnyPS5 Games", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White, Visible = false };
    readonly Button adminButton = new Button { Text = "Restart AnyPS5 Machines as administrator", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White, Visible = false };
    IntPtr launcher, game, own;
    long launcherStyle;
    IntPtr ownStyle;
    RECT ownRect;
    double ownAspect = 16.0 / 9.0;
    uint launcherPid, launcherThread, gameThread, gamePid, ownPid, ownThread, pendingPid, poppedPid;
    bool opened, launcherFull, ownFull, sentFull, embedFailed, hotkey, showingGame;
    DateTime startedAt = DateTime.MinValue, pendingUntil, windowlessSince = DateTime.MinValue;
    int ticks;
    string lastTitle;
    readonly HashSet<uint> seenGames = new HashSet<uint>();
    readonly Dictionary<uint, string> paths = new Dictionary<uint, string>();
    public event Action<bool> FullChanged;
    public event Action GameStarted;

    public GamesView()
    {
        Dock = DockStyle.Fill; BackColor = Color.FromArgb(12, 12, 16); Visible = false;
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 60, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(20, 10, 0, 0) };
        buttons.Controls.Add(startButton); buttons.Controls.Add(adminButton);
        host.Controls.Add(note); host.Controls.Add(buttons);
        gameBar.Controls.Add(switchButton); gameBar.Controls.Add(fullButton); gameBar.Controls.Add(popButton); gameBar.Controls.Add(testsButton); gameBar.Controls.Add(gameTitle);
        Controls.Add(host); Controls.Add(gameHost); Controls.Add(testsHost); Controls.Add(gameBar);
        testsButton.Click += (s, e) => ShowTests(!showingTests);
        testsHost.Resize += (s, e) => { if (testsInView) LayoutTests(); };
        startButton.Click += (s, e) => { startButton.Visible = false; startedAt = DateTime.MinValue; embedFailed = false; };
        adminButton.Click += (s, e) =>
        {
            try { Process.Start(new ProcessStartInfo(Application.ExecutablePath, "--games") { Verb = "runas", UseShellExecute = true }); FindForm().Close(); } catch { }
        };
        switchButton.Click += (s, e) => ShowGame(!showingGame);
        fullButton.Click += (s, e) => ToggleOwnFull();
        popButton.Click += (s, e) => { if (own != IntPtr.Zero) PopOut(); else PutBack(); };
        host.Resize += (s, e) => Fit();
        gameHost.Resize += (s, e) => FitOwn();
        gameHost.MouseDown += (s, e) => FocusDesktop();
        Say("AnyPS5 Games opens here.");
    }

    void Say(string text) { note.Text = text; note.Visible = true; }

    static bool Elevated()
    {
        return new System.Security.Principal.WindowsPrincipal(System.Security.Principal.WindowsIdentity.GetCurrent()).IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
    }

    public void Open() { opened = true; Tick(); }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WM_HOTKEY && m.WParam.ToInt32() == Hotkey) ToggleOwnFull();
        base.WndProc(ref m);
    }

    public void Tick()
    {
        ticks++;
        IntPtr fg = GetForegroundWindow(); uint fgPid; GetWindowThreadProcessId(fg, out fgPid);
        if (fg != IntPtr.Zero && !IsGamePath(PathOf(fgPid))) prevForeground = fg;
        if (pendingTests.Count > 0) TryTests();
        KeepTests();
        if (!opened) return;
        if (own != IntPtr.Zero) KeepOwn();
        if (pendingPid != 0 && ticks % 2 == 0) TryOwn();
        if (launcher != IntPtr.Zero && !IsWindow(launcher)) { ReleaseGame(); Detach(ref launcherThread); launcher = IntPtr.Zero; Say("AnyPS5 Games was closed."); startButton.Visible = true; startedAt = DateTime.MaxValue; }
        if (launcher == IntPtr.Zero) { if (ticks % 4 == 0 || startedAt == DateTime.MinValue) FindLauncher(); }
        else
        {
            long st = (long)GetWindowLongPtr(launcher, GWL_STYLE);
            if ((st & (WS_MAXIMIZE | WS_MINIMIZE)) != 0) { ShowWindow(launcher, 9); st = (long)GetWindowLongPtr(launcher, GWL_STYLE); }
            if ((st & (Frame | WS_MAXIMIZE)) != 0 || (st & WS_CHILD) == 0) { SetWindowLongPtr(launcher, GWL_STYLE, new IntPtr((st & ~(Frame | WS_MAXIMIZE | WS_MINIMIZE)) | WS_CHILD | WS_VISIBLE)); SetWindowPos(launcher, IntPtr.Zero, 0, 0, 0, 0, SWP); }
            if (GetParent(launcher) != host.Handle) SetParent(launcher, host.Handle);
            if (IsIconic(launcher)) ShowWindow(launcher, 9);
            // WinForms in the launcher can mark its embedded form hidden; then tab pages opened later are never created and
            // its WebView2 (Discord) stays invisible (11-08: blank GitHub and Discord tabs). A "shown" message puts it right.
            if (ticks % 8 == 1) PostMessage(launcher, WM_SHOWWINDOW, new IntPtr(1), IntPtr.Zero);
            Fit();
            TrackGame();
        }
    }

    void FindLauncher()
    {
        foreach (var p in Process.GetProcessesByName("AnyPS5 Games"))
        {
            IntPtr h;
            try { h = p.MainWindowHandle; } catch { continue; }
            if (h == IntPtr.Zero || !IsWindowVisible(h))
            {
                // 11-08: a launcher whose window died with a force-closed AnyPS5 Machines keeps running with no window,
                // and a new start only hands off to it; after 30 s it is replaced.
                try { if (DateTime.Now - p.StartTime > TimeSpan.FromSeconds(30) && windowlessSince != DateTime.MinValue && DateTime.Now - windowlessSince > TimeSpan.FromSeconds(30)) { p.Kill(); windowlessSince = DateTime.MinValue; startedAt = DateTime.MinValue; return; } } catch { }
                if (windowlessSince == DateTime.MinValue) windowlessSince = DateTime.Now;
                continue;
            }
            windowlessSince = DateTime.MinValue;
            Embed(h);
            return;
        }
        if (embedFailed) return;
        if (startedAt == DateTime.MinValue)
        {
            if (!File.Exists(LauncherExe)) { Say("AnyPS5 Games is missing: " + LauncherExe); startedAt = DateTime.MaxValue; return; }
            var psi = new ProcessStartInfo(LauncherExe) { UseShellExecute = false, WorkingDirectory = Root };
            psi.EnvironmentVariables["__COMPAT_LAYER"] = "RunAsInvoker";
            try { Process.Start(psi); startedAt = DateTime.Now; Say("Starting AnyPS5 Games..."); }
            catch (Exception ex) { Say("AnyPS5 Games did not start: " + ex.Message); startButton.Visible = true; startedAt = DateTime.MaxValue; }
        }
        else if (startedAt != DateTime.MaxValue && DateTime.Now - startedAt > TimeSpan.FromSeconds(60))
        {
            Say("AnyPS5 Games did not open a window."); startButton.Visible = true; startedAt = DateTime.MaxValue;
        }
    }

    void Embed(IntPtr h)
    {
        launcherStyle = (long)GetWindowLongPtr(h, GWL_STYLE);
        if ((launcherStyle & (WS_MAXIMIZE | WS_MINIMIZE)) != 0) { ShowWindow(h, 9); launcherStyle = (long)GetWindowLongPtr(h, GWL_STYLE); }
        SetWindowLongPtr(h, GWL_STYLE, new IntPtr((launcherStyle & ~(Frame | WS_MAXIMIZE | WS_MINIMIZE)) | WS_CHILD | WS_VISIBLE));
        if (SetParent(h, host.Handle) == IntPtr.Zero)
        {
            SetWindowLongPtr(h, GWL_STYLE, new IntPtr(launcherStyle));
            embedFailed = true; startedAt = DateTime.MaxValue;
            if (!Elevated()) { Say("AnyPS5 Games runs as administrator, so it can only be shown here when AnyPS5 Machines runs as administrator too."); adminButton.Visible = true; }
            else Say("AnyPS5 Games could not be shown here.");
            startButton.Visible = true;
            return;
        }
        ShowWindow(h, 1);
        SetWindowPos(h, IntPtr.Zero, 0, 0, 0, 0, SWP);
        SendMessage(h, WM_SHOWWINDOW, new IntPtr(1), IntPtr.Zero);
        launcher = h; launcherThread = GetWindowThreadProcessId(h, out launcherPid);
        AttachThreadInput(GetCurrentThreadId(), launcherThread, true);
        note.Visible = false; startButton.Visible = false; adminButton.Visible = false;
        Fit();
    }

    void Fit()
    {
        if (launcher == IntPtr.Zero) return;
        int w = Math.Max(1, host.ClientSize.Width), h = Math.Max(1, host.ClientSize.Height);
        RECT r; GetWindowRect(launcher, out r);
        var origin = host.PointToScreen(Point.Empty);
        if (r.L != origin.X || r.T != origin.Y || r.R - r.L != w || r.B - r.T != h) MoveWindow(launcher, 0, 0, w, h, true);
    }

    void RaiseFull()
    {
        bool f = launcherFull || ownFull;
        if (f != sentFull) { sentFull = f; if (FullChanged != null) FullChanged(f); }
    }

    void TrackGame()
    {
        if (game != IntPtr.Zero && (!IsWindow(game) || !IsChild(launcher, game))) ReleaseGame();
        if (game == IntPtr.Zero && ticks % 2 == 0)
        {
            IntPtr found = IntPtr.Zero; uint foundPid = 0;
            EnumChildWindows(launcher, (h, l) => { uint pid; GetWindowThreadProcessId(h, out pid); if (pid != launcherPid) { found = h; foundPid = pid; return false; } return true; }, IntPtr.Zero);
            if (found != IntPtr.Zero)
            {
                game = found; gamePid = foundPid; gameThread = GetWindowThreadProcessId(found, out foundPid);
                AttachThreadInput(GetCurrentThreadId(), gameThread, true);
                if (pendingPid == gamePid) pendingPid = 0;
            }
        }
        launcherFull = game != IntPtr.Zero && GetParent(GetParent(game)) == launcher;
        RaiseFull();
    }

    void ReleaseGame()
    {
        Detach(ref gameThread); game = IntPtr.Zero; gamePid = 0;
        launcherFull = false; RaiseFull();
    }

    void Detach(ref uint thread) { if (thread != 0) { AttachThreadInput(GetCurrentThreadId(), thread, false); thread = 0; } }

    string PathOf(uint pid)
    {
        string path;
        if (paths.TryGetValue(pid, out path)) return path;
        path = "";
        IntPtr p = OpenProcess(0x1000, false, pid);
        if (p != IntPtr.Zero)
        {
            var sb = new System.Text.StringBuilder(1024); int n = sb.Capacity;
            if (QueryFullProcessImageNameW(p, 0, sb, ref n)) path = sb.ToString();
            CloseHandle(p);
        }
        if (paths.Count > 2000) paths.Clear();
        paths[pid] = path;
        return path;
    }

    // A game started from the AnyPS5 app plays inside that app; the app writes its pid here (11-15).
    public static uint AppGamePid()
    {
        try { uint pid; return uint.TryParse(File.ReadAllText(Path.Combine(Root, "_tools", "live", "anyps5-app-game.txt")).Trim(), out pid) ? pid : 0; } catch { return 0; }
    }

    static bool IsGamePath(string path)
    {
        return path.StartsWith(Root, StringComparison.OrdinalIgnoreCase) && path.IndexOf(@"\_tools\", StringComparison.OrdinalIgnoreCase) < 0
            && !Path.GetFileName(path).StartsWith("AnyPS5", StringComparison.OrdinalIgnoreCase);
    }

    void WatchNewGames()
    {
        var fresh = new List<uint>();
        EnumWindows((h, l) =>
        {
            if (!IsWindowVisible(h)) return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (pid == launcherPid || seenGames.Contains(pid) || fresh.Contains(pid)) return true;
            if (IsGamePath(PathOf(pid))) fresh.Add(pid);
            return true;
        }, IntPtr.Zero);
        uint appGame = AppGamePid();
        foreach (var pid in fresh)
        {
            seenGames.Add(pid);
            if (pid == appGame) continue;
            if (!StartedByBlu(pid)) { pendingTests[pid] = DateTime.Now.AddSeconds(60); continue; }
            if (!opened || own != IntPtr.Zero) continue;
            pendingPid = pid; pendingUntil = DateTime.Now.AddSeconds(40);
            if (GameStarted != null) GameStarted();
        }
    }

    void TryTests()
    {
        foreach (var pid in new List<uint>(pendingTests.Keys))
        {
            if (DateTime.Now > pendingTests[pid]) { pendingTests.Remove(pid); continue; }
            IntPtr h = TopWindowOf(pid);
            if (h != IntPtr.Zero) { pendingTests.Remove(pid); EmbedTest(h, pid); }
        }
    }

    void EmbedTest(IntPtr h, uint pid)
    {
        if (park == null || park.IsDisposed) { park = new ParkForm(); park.Show(); }
        var t = new TestGame { Hwnd = h, Pid = pid };
        RECT c; GetClientRect(h, out c); t.W = Math.Max(64, c.R - c.L); t.H = Math.Max(64, c.B - c.T);
        GetWindowRect(h, out t.Rect); t.Style = GetWindowLongPtr(h, GWL_STYLE);
        bool tookFocus = GetForegroundWindow() == h;
        SetWindowLongPtr(h, GWL_STYLE, new IntPtr((t.Style.ToInt64() & ~Frame) | WS_CHILD | WS_VISIBLE));
        if (SetParent(h, testsInView ? testsHost.Handle : park.Handle) == IntPtr.Zero) { SetWindowLongPtr(h, GWL_STYLE, t.Style); return; }
        tests.Add(t);
        LayoutTests();
        if (tookFocus && prevForeground != IntPtr.Zero && IsWindow(prevForeground)) SetForegroundWindow(prevForeground);
    }

    // Off screen each test keeps its own size (side by side: an overlapped part would not capture); in the tab they share it.
    void LayoutTests()
    {
        const uint flags = 0x0004 | 0x0010 | 0x0020 | 0x0040;
        if (testsInView)
        {
            int n = tests.Count; if (n == 0) return;
            int cols = (int)Math.Ceiling(Math.Sqrt(n)), rows = (n + cols - 1) / cols;
            int cw = Math.Max(1, testsHost.ClientSize.Width / cols), ch = Math.Max(1, testsHost.ClientSize.Height / rows);
            for (int i = 0; i < n; i++)
            {
                var t = tests[i]; double a = (double)t.W / t.H;
                int w = cw - 4, hh = (int)(w / a); if (hh > ch - 4) { hh = ch - 4; w = (int)(hh * a); }
                SetWindowPos(t.Hwnd, IntPtr.Zero, (i % cols) * cw + (cw - w) / 2, (i / cols) * ch + (ch - hh) / 2, Math.Max(1, w), Math.Max(1, hh), flags);
            }
            return;
        }
        if (park == null || park.IsDisposed) return;
        int x = 0, maxH = 64;
        foreach (var t in tests) { SetWindowPos(t.Hwnd, IntPtr.Zero, x, 0, t.W, t.H, flags); x += t.W; maxH = Math.Max(maxH, t.H); }
        park.Size = new Size(Math.Max(64, x), maxH);
    }

    void KeepTests()
    {
        bool changed = false;
        for (int i = tests.Count - 1; i >= 0; i--)
            if (!IsWindow(tests[i].Hwnd)) { try { File.Delete(Path.Combine(LiveDir, "title-" + tests[i].Pid + ".txt")); } catch { } tests.RemoveAt(i); changed = true; }
        var form = FindForm();
        bool want = showingTests && tests.Count > 0 && Visible && form != null && form.WindowState != FormWindowState.Minimized;
        if (want != testsInView && (want || (park != null && !park.IsDisposed)))
        {
            testsInView = want; changed = true;
            foreach (var t in tests) SetParent(t.Hwnd, want ? testsHost.Handle : park.Handle);
        }
        foreach (var t in tests)
        {
            long st = GetWindowLongPtr(t.Hwnd, GWL_STYLE).ToInt64();
            if ((st & Frame) != 0 || (st & WS_CHILD) == 0) { SetWindowLongPtr(t.Hwnd, GWL_STYLE, new IntPtr((st & ~Frame) | WS_CHILD | WS_VISIBLE)); changed = true; }
            if (ticks % 4 == 0)
            {
                var sb = new System.Text.StringBuilder(512); GetWindowText(t.Hwnd, sb, 512);
                string title = sb.ToString();
                if (title != t.Title) { t.Title = title; try { Directory.CreateDirectory(LiveDir); File.WriteAllText(Path.Combine(LiveDir, "title-" + t.Pid + ".txt"), title); } catch { } }
            }
        }
        if (changed) LayoutTests();
        if (tests.Count != testCount) { testCount = tests.Count; if (tests.Count == 0) showingTests = false; UpdateView(); }
    }

    void ShowTests(bool on)
    {
        showingTests = on && tests.Count > 0;
        if (showingTests) showingGame = false;
        UpdateView();
        KeepTests();
    }

    void UpdateView()
    {
        if (own == IntPtr.Zero) showingGame = false;
        gameHost.Visible = showingGame; testsHost.Visible = showingTests; host.Visible = !showingGame && !showingTests;
        gameBar.Visible = !ownFull && (own != IntPtr.Zero || poppedPid != 0 || tests.Count > 0);
        switchButton.Visible = own != IntPtr.Zero; switchButton.Text = showingGame ? "Show launcher" : "Show game";
        fullButton.Visible = own != IntPtr.Zero; popButton.Visible = own != IntPtr.Zero || poppedPid != 0;
        testsButton.Visible = tests.Count > 0; testsButton.Text = showingTests ? "Hide pipeline tests" : "Pipeline tests (" + tests.Count + ")";
        gameTitle.Visible = own != IntPtr.Zero;
        gameBar.SendToBack();
        if (showingGame) FitOwn(); else if (!showingTests) Fit();
    }

    static bool StartedByBlu(uint pid)
    {
        try
        {
            var procs = new Dictionary<uint, Tuple<uint, string>>();
            foreach (System.Management.ManagementObject o in new System.Management.ManagementObjectSearcher("SELECT ProcessId, ParentProcessId, CommandLine FROM Win32_Process").Get())
                procs[Convert.ToUInt32(o["ProcessId"])] = Tuple.Create(Convert.ToUInt32(o["ParentProcessId"]), (o["CommandLine"] as string) ?? "");
            Tuple<uint, string> at;
            if (!procs.TryGetValue(pid, out at) || at.Item2.IndexOf(@"\_test\", StringComparison.OrdinalIgnoreCase) >= 0) return false;
            for (uint id = at.Item1, hops = 0; hops < 12 && procs.TryGetValue(id, out at); id = at.Item1, hops++)
                if (at.Item2.Contains("auto-games.ps1") || at.Item2.Contains("ai-player.py") || at.Item2.Contains("fps_wait_run") || at.Item2.IndexOf(@"\_tools\", StringComparison.OrdinalIgnoreCase) >= 0) return false;
            return true;
        }
        catch { return false; }
    }

    IntPtr TopWindowOf(uint pid)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) =>
        {
            uint p; GetWindowThreadProcessId(h, out p);
            if (p != pid || !IsWindowVisible(h)) return true;
            RECT c; GetClientRect(h, out c);
            if (c.R - c.L < 50 || c.B - c.T < 50) return true;
            found = h; return false;
        }, IntPtr.Zero);
        return found;
    }

    void TryOwn()
    {
        if (DateTime.Now > pendingUntil || own != IntPtr.Zero || (game != IntPtr.Zero && gamePid == pendingPid)) { pendingPid = 0; return; }
        IntPtr h = TopWindowOf(pendingPid);
        if (h != IntPtr.Zero) { EmbedOwn(h, pendingPid); pendingPid = 0; }
    }

    void EmbedOwn(IntPtr h, uint pid)
    {
        RECT c; GetClientRect(h, out c);
        ownAspect = (double)(c.R - c.L) / Math.Max(1, c.B - c.T);
        GetWindowRect(h, out ownRect);
        ownStyle = GetWindowLongPtr(h, GWL_STYLE);
        SetWindowLongPtr(h, GWL_STYLE, new IntPtr((ownStyle.ToInt64() & ~Frame) | WS_CHILD | WS_VISIBLE));
        if (SetParent(h, gameHost.Handle) == IntPtr.Zero) { SetWindowLongPtr(h, GWL_STYLE, ownStyle); return; }
        own = h; ownPid = pid; poppedPid = 0;
        ownThread = GetWindowThreadProcessId(h, out pid);
        AttachThreadInput(GetCurrentThreadId(), ownThread, true);
        if (!hotkey) hotkey = RegisterHotKey(Handle, Hotkey, 0, 0x7A);
        popButton.Text = "Pop out"; fullButton.Enabled = true;
        ShowGame(true);
        SetFocus(own);
    }

    void ShowGame(bool on)
    {
        showingGame = on && own != IntPtr.Zero;
        if (showingGame) showingTests = false;
        UpdateView();
        KeepTests();
        if (showingGame) FocusDesktop();
    }

    void KeepOwn()
    {
        if (!IsWindow(own)) { OwnGone(); return; }
        long st = GetWindowLongPtr(own, GWL_STYLE).ToInt64();
        if ((st & Frame) != 0 || (st & WS_CHILD) == 0) { SetWindowLongPtr(own, GWL_STYLE, new IntPtr((st & ~Frame) | WS_CHILD | WS_VISIBLE)); SetWindowPos(own, IntPtr.Zero, 0, 0, 0, 0, SWP); FitOwn(); }
        if (GetParent(own) != gameHost.Handle) SetParent(own, gameHost.Handle);
        if (ticks % 8 == 0)
        {
            var t = new System.Text.StringBuilder(512); GetWindowText(own, t, 512);
            string title = t.ToString();
            gameTitle.Text = title;
            if (title != lastTitle) { lastTitle = title; try { Directory.CreateDirectory(LiveDir); File.WriteAllText(Path.Combine(LiveDir, "title-" + ownPid + ".txt"), title); } catch { } }
        }
    }

    void FitOwn()
    {
        if (own == IntPtr.Zero) return;
        int W = gameHost.ClientSize.Width, H = gameHost.ClientSize.Height;
        if (W < 50 || H < 50) return;
        int w = W, h = (int)(W / ownAspect);
        if (h > H) { h = H; w = (int)(H * ownAspect); }
        SetWindowPos(own, IntPtr.Zero, (W - w) / 2, (H - h) / 2, w, h, 0x0004 | 0x0020 | 0x0040);
    }

    void ToggleOwnFull()
    {
        if (own == IntPtr.Zero) { ownFull = false; RaiseFull(); return; }
        ownFull = !ownFull;
        if (ownFull) ShowGame(true);
        UpdateView();
        RaiseFull();
        FitOwn(); FocusDesktop();
    }

    void ClearOwn()
    {
        Detach(ref ownThread);
        if (hotkey) { UnregisterHotKey(Handle, Hotkey); hotkey = false; }
        try { File.Delete(Path.Combine(LiveDir, "title-" + ownPid + ".txt")); } catch { }
        own = IntPtr.Zero; lastTitle = null;
        ownFull = false; RaiseFull();
    }

    void OwnGone()
    {
        ClearOwn(); ownPid = 0;
        ShowGame(false);
    }

    void GiveBack()
    {
        IntPtr h = own;
        if (h == IntPtr.Zero || !IsWindow(h)) return;
        SetParent(h, IntPtr.Zero);
        SetWindowLongPtr(h, GWL_STYLE, ownStyle);
        SetWindowPos(h, IntPtr.Zero, ownRect.L, ownRect.T, ownRect.R - ownRect.L, ownRect.B - ownRect.T, 0x0004 | 0x0020 | 0x0040);
    }

    void PopOut()
    {
        uint pid = ownPid; IntPtr h = own;
        GiveBack(); ClearOwn();
        poppedPid = pid; ownPid = 0;
        popButton.Text = "Put it back inside"; fullButton.Enabled = false;
        ShowGame(false);
        SetForegroundWindow(h);
    }

    void PutBack()
    {
        if (poppedPid == 0) return;
        IntPtr h = TopWindowOf(poppedPid);
        if (h == IntPtr.Zero) { poppedPid = 0; UpdateView(); return; }
        EmbedOwn(h, poppedPid);
    }

    public bool Owns(IntPtr h)
    {
        if (own != IntPtr.Zero && showingGame && (h == own || IsChild(own, h))) return true;
        return game != IntPtr.Zero && (h == game || IsChild(game, h));
    }

    public void FocusDesktop()
    {
        if (own != IntPtr.Zero && showingGame) SetFocus(own);
        else if (game != IntPtr.Zero) SetFocus(game);
    }

    // When this window closes, the game and the launcher go back to being normal windows and keep running.
    public IntPtr LauncherWindow { get { return launcher; } }

    public void Release()
    {
        foreach (var t in tests)
        {
            if (!IsWindow(t.Hwnd)) continue;
            SetParent(t.Hwnd, IntPtr.Zero);
            SetWindowLongPtr(t.Hwnd, GWL_STYLE, t.Style);
            SetWindowPos(t.Hwnd, IntPtr.Zero, t.Rect.L, t.Rect.T, t.Rect.R - t.Rect.L, t.Rect.B - t.Rect.T, 0x0004 | 0x0010 | 0x0020 | 0x0040);
        }
        tests.Clear();
        if (park != null && !park.IsDisposed) park.Close();
        GiveBack(); ClearOwn();
        Detach(ref gameThread);
        Detach(ref launcherThread);
        if (launcher == IntPtr.Zero || !IsWindow(launcher)) return;
        SetParent(launcher, IntPtr.Zero);
        SetWindowLongPtr(launcher, GWL_STYLE, new IntPtr(launcherStyle & ~(WS_MINIMIZE | WS_MAXIMIZE | WS_CHILD) | WS_VISIBLE));
        SetWindowPos(launcher, IntPtr.Zero, 0, 0, 0, 0, SWP);
        ShowWindow(launcher, 3);
        launcher = IntPtr.Zero;
    }
}
class MachinesView : Form
{
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Point p);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    IFocusable focusTarget;
    DateTime focusUntil;
    readonly GamesView games = new GamesView();
    readonly FlowLayoutPanel strip = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 34, Padding = new Padding(6, 4, 0, 0), WrapContents = false, BackColor = Color.FromArgb(24, 26, 34) };
    readonly Button gamesTab = new Button { Text = "AnyPS5 Games", Width = 130, Height = 26, FlatStyle = FlatStyle.Flat, ForeColor = Color.White, BackColor = Color.FromArgb(60, 64, 84) };
    // 11-15 Blu: AnyPS5.exe (the screen for AnyPS5 OS) is its own app, not inside this window. This button only opens it
    // or brings it to the front; restarting either app never restarts the other. This pipeline window starts minimized
    // while _tools\live\pipeline-background.flag exists (Blu: "the pipeline doesn't need us to be seeing it work").
    readonly Button appTab = new Button { Text = "Open AnyPS5", Width = 150, Height = 26, FlatStyle = FlatStyle.Flat, ForeColor = Color.White };
    static readonly string AppWindowFile = Path.Combine(GamesView.Root, "_tools", "live", "anyps5-window.txt");
    static readonly string BackgroundFlag = Path.Combine(GamesView.Root, "_tools", "live", "pipeline-background.flag");
    static readonly string TabFile = Path.Combine(GamesView.Root, "_tools", "live", "machines-tab.txt");
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);

    static Process AppProcess() { foreach (var p in Process.GetProcessesByName("AnyPS5")) { try { if (!p.HasExited) return p; } catch { } } return null; }

    void ShowApp()
    {
        if (AppProcess() == null)
        {
            try { Process.Start(new ProcessStartInfo(Path.Combine(GamesView.Root, "AnyPS5.exe")) { WorkingDirectory = GamesView.Root }); }
            catch (Exception ex) { MessageBox.Show(this, "AnyPS5 could not start: " + ex.Message, "AnyPS5"); }
            return;
        }
        try
        {
            long v;
            if (long.TryParse(File.ReadAllText(AppWindowFile).Trim(), out v) && IsWindow(new IntPtr(v)))
            {
                var h = new IntPtr(v);
                if (IsIconic(h)) ShowWindow(h, 9);
                SetForegroundWindow(h);
            }
        }
        catch { }
    }

    FormWindowState gamesBefore;
    bool gamesFull;

    void GamesFull(bool on)
    {
        if (on == gamesFull) return;
        gamesFull = on;
        if (on) { gamesBefore = WindowState; strip.Visible = false; FormBorderStyle = FormBorderStyle.None; WindowState = FormWindowState.Normal; WindowState = FormWindowState.Maximized; }
        else { strip.Visible = true; FormBorderStyle = FormBorderStyle.Sizable; WindowState = gamesBefore; }
    }

    readonly Timer focusTimer = new Timer { Interval = 60 };

    void FocusTick()
    {
        if (focusTarget == null || DateTime.Now > focusUntil) { focusTimer.Stop(); focusTarget = null; return; }
        focusTarget.FocusDesktop();
    }

    MachinesView()
    {
        Text = "AnyPS5 Games"; Width = 1600; Height = 1000; BackColor = Color.FromArgb(12, 12, 16); KeyPreview = true;
        strip.Controls.Add(gamesTab); strip.Controls.Add(appTab);
        appTab.Click += (s, e) => ShowApp();
        gamesTab.Click += (s, e) => games.Open();
        Controls.Add(games); Controls.Add(strip);
        games.Visible = true;
        games.FullChanged += on => GamesFull(on);
        games.Open();
        try { File.WriteAllText(TabFile, "games"); } catch { }
        if (File.Exists(BackgroundFlag)) Shown += (s, e) => WindowState = FormWindowState.Minimized;
        var gamesTimer = new Timer { Interval = 250 };
        gamesTimer.Tick += (s, e) => games.Tick();
        gamesTimer.Start();
        var timer = new Timer { Interval = 1000 };
        timer.Tick += (s, e) => { uint gp = GamesView.AppGamePid(); bool playing = false; if (gp != 0) { try { playing = !Process.GetProcessById((int)gp).HasExited; } catch { } } string t = playing ? "AnyPS5 · playing" : "Open AnyPS5"; if (appTab.Text != t) appTab.Text = t; };
        timer.Start();
        focusTimer.Tick += (s, e) => FocusTick();
        Activated += (s, e) => { if (focusTarget != null) focusTarget.FocusDesktop(); };
        var clickWatch = new Timer { Interval = 40 };
        bool wasDown = false;
        clickWatch.Tick += (s, e) =>
        {
            bool down = (GetAsyncKeyState(1) & 0x8000) != 0 || (GetAsyncKeyState(2) & 0x8000) != 0;
            if (down && !wasDown)
            {
                IntPtr under = WindowFromPoint(Cursor.Position);
                if (games.Visible && games.Owns(under)) { focusTarget = games; focusUntil = DateTime.Now.AddMilliseconds(600); games.FocusDesktop(); focusTimer.Start(); }
            }
            wasDown = down;
        };
        clickWatch.Start();
        // 11-12 Blu: one app. Closing this window closes the AnyPS5 Games window it holds too (with its own keep working /
        // pause / shut down question) instead of leaving it behind as a second window.
        FormClosing += (s, e) =>
        {
            IntPtr gamesWindow = games.LauncherWindow;
            games.Release();
            if (gamesWindow != IntPtr.Zero) PostMessage(gamesWindow, 0x0010, IntPtr.Zero, IntPtr.Zero);
            try { File.Delete(TabFile); } catch { }
        };
    }

    [DllImport("user32.dll")] static extern int SetThreadDpiHostingBehavior(int value);
    [STAThread]
    static void Main(string[] args)
    {
        try { SetThreadDpiHostingBehavior(1); } catch { }
        Application.EnableVisualStyles(); Application.Run(new MachinesView());
    }
}
