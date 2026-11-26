// AnyPS5 Games.exe: starts the games pipeline (the "AnyPS5 Games pipeline" scheduled task, or the script directly)
// and shows it working: what it is doing now, every game and its stage, and a live console with each command and
// its output. The offline assistant (_tools\assistant\assistant.py --serve) lives in the right-hand panel: it gets
// the pipeline's live status with every message, can watch and steer the pipeline, look at the running game, and is
// told about the pipeline's problems as they happen. Closing this window does not stop the pipeline.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// The embedded Discord: Edge WebView2 (Microsoft.Web.WebView2.*.dll and WebView2Loader.dll in _tools\webview2, the
// WebView2 runtime that ships with Windows). Links that leave Discord open in the normal browser.
class DiscordWeb
{
    readonly Microsoft.Web.WebView2.WinForms.WebView2 view = new Microsoft.Web.WebView2.WinForms.WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(49, 51, 56) };

    // The loader folder can be set only before the first WebView2 call in this process; the tab's browser is made again
    // each time the tab opens, and a second call threw ("Discord could not open here", 11-08).
    static bool loaderSet;

    public DiscordWeb(Control host, string dataDir, string url, Action<string> visited)
    {
        if (!loaderSet) { Microsoft.Web.WebView2.Core.CoreWebView2Environment.SetLoaderDllFolderPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "webview2")); loaderSet = true; }
        host.Controls.Clear(); host.Controls.Add(view);
        Start(dataDir, url, visited);
    }

    async void Start(string dataDir, string url, Action<string> visited)
    {
        try
        {
            var env = await Microsoft.Web.WebView2.Core.CoreWebView2Environment.CreateAsync(null, dataDir);
            await view.EnsureCoreWebView2Async(env);
            view.CoreWebView2.Settings.IsStatusBarEnabled = false;
            view.CoreWebView2.NewWindowRequested += (s, e) => { e.Handled = true; try { Process.Start(e.Uri); } catch { } };
            view.CoreWebView2.SourceChanged += (s, e) => { string u = view.Source != null ? view.Source.ToString() : ""; if (u.StartsWith("https://discord.com/channels/")) visited(u); };
            view.CoreWebView2.NavigationCompleted += (s, e) => Log(dataDir, "loaded ok=" + e.IsSuccess + " status=" + e.WebErrorStatus + " " + view.Source);
            // Blu 11-06: "you can always catch who's adding me". Discord's notifications (DMs, mentions, friend requests)
            // are written to _tools\live\discord-notifications.jsonl for the assistant to read; nothing is sent, and no
            // second toast is shown (the desktop Discord shows its own).
            view.CoreWebView2.PermissionRequested += (s, e) => { if (e.PermissionKind == Microsoft.Web.WebView2.Core.CoreWebView2PermissionKind.Notifications) e.State = Microsoft.Web.WebView2.Core.CoreWebView2PermissionState.Allow; };
            view.CoreWebView2.WebMessageReceived += (s, e) => Notified(e.TryGetWebMessageAsString());
            await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(NotifyHook);
            Log(dataDir, "opening " + url + " in " + view.Width + "x" + view.Height + " visible=" + view.Visible);
            view.CoreWebView2.Navigate(url);
        }
        catch (Exception ex)
        {
            var parent = view.Parent; if (parent == null) return;
            parent.Controls.Clear();
            parent.Controls.Add(new Label { Text = "Discord could not open here: " + ex.Message, ForeColor = Color.FromArgb(240, 190, 80), AutoSize = true, Location = new Point(12, 12) });
        }
    }

    const string NotifyHook = "(() => { if (!window.chrome || !chrome.webview) return; " +
        "function N(title, o) { try { chrome.webview.postMessage(JSON.stringify({ title: String(title || ''), body: o && o.body ? String(o.body) : '', tag: o && o.tag ? String(o.tag) : '' })); } catch (e) { } " +
        "return { close() { }, addEventListener() { }, removeEventListener() { }, onclick: null, onclose: null, onshow: null, onerror: null }; } " +
        "N.permission = 'granted'; N.requestPermission = (cb) => { if (cb) cb('granted'); return Promise.resolve('granted'); }; window.Notification = N; })();";

    static void Notified(string message)
    {
        if (string.IsNullOrEmpty(message) || !message.StartsWith("{")) return;
        try
        {
            string file = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "live", "discord-notifications.jsonl");
            File.AppendAllText(file, "{\"time\":\"" + DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss") + "\",\"n\":" + message + "}" + Environment.NewLine);
        }
        catch { }
    }

    static void Log(string dataDir, string text) { try { File.AppendAllText(Path.Combine(dataDir, "discord-view.log"), DateTime.Now.ToString("HH:mm:ss ") + text + Environment.NewLine); } catch { } }

    public void Close() { var parent = view.Parent; if (parent != null) parent.Controls.Remove(view); view.Dispose(); }
    public void Back() { if (view.CoreWebView2 != null && view.CoreWebView2.CanGoBack) view.CoreWebView2.GoBack(); }
    public void Reload() { if (view.CoreWebView2 != null) view.CoreWebView2.Reload(); }
    public void Go(string url) { if (view.CoreWebView2 != null) view.CoreWebView2.Navigate(url); }
}

class Launcher : Form
{
    static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    static readonly string Tools = Path.Combine(Root, "_tools");
    static readonly string TracePath = Path.Combine(Tools, "auto-games-trace.log");
    static readonly string StatePath = Path.Combine(Tools, "auto-state.json");
    static readonly string AlivePath = Path.Combine(Tools, "auto-games.alive");
    static readonly string PausePath = Path.Combine(Tools, "pause.flag");
    static readonly string RequestsPath = Path.Combine(Tools, "requests.txt");
    static readonly string AssistantScript = Path.Combine(Tools, "assistant", "assistant.py");

    static readonly Color Bg = Color.FromArgb(18, 20, 24), Panel = Color.FromArgb(28, 31, 37), Fg = Color.FromArgb(220, 224, 230), Dim = Color.FromArgb(140, 146, 156);
    static readonly Color Green = Color.FromArgb(87, 199, 133), Yellow = Color.FromArgb(240, 190, 80), Red = Color.FromArgb(235, 100, 100), Cyan = Color.FromArgb(90, 190, 230), Blue = Color.FromArgb(120, 150, 255);
    static readonly Regex Problem = new Regex("FAILED|NOT BUILT|exited 0x|CRASH|REGRESSED|stalls");

    readonly Label now = new Label(), health = new Label();
    readonly ListView games = new ListView();
    // While a game runs, the game list gives way to its status (Blu: see what is going on instead of a black screen):
    // _tools\assistant\trace_game.ps1 writes _tools\live\game-watch.json, read here every 2 seconds.
    readonly Panel board = new Panel(), gameStatus = new Panel();
    readonly Label gsTitle = new Label(), gsNow = new Label();
    readonly RichTextBox gsEvents = new RichTextBox();
    readonly Timer gsTimer = new Timer { Interval = 2000 };
    Process gsWatcher;
    bool gsListShown;

    void BuildGameStatus()
    {
        gameStatus.Dock = DockStyle.Fill; gameStatus.BackColor = Bg; gameStatus.Visible = false; gameStatus.Padding = new Padding(8, 4, 8, 4);
        gsTitle.Dock = DockStyle.Top; gsTitle.Height = 30; gsTitle.ForeColor = Fg; gsTitle.Font = new Font("Segoe UI Semibold", 12f); gsTitle.TextAlign = ContentAlignment.MiddleLeft;
        gsNow.Dock = DockStyle.Top; gsNow.Height = 52; gsNow.ForeColor = Cyan; gsNow.Font = new Font("Segoe UI", 10.5f);
        var bar = Bar(DockStyle.Top); bar.BackColor = Bg;
        var back = Button("Show the game list", (s, e) => { gsListShown = !gsListShown; ShowGameStatus(liveGame != null); });
        back.Width = 160; back.Height = 26; bar.Controls.Add(back);
        gsEvents.Dock = DockStyle.Fill; gsEvents.ReadOnly = true; gsEvents.BorderStyle = BorderStyle.None; gsEvents.BackColor = Color.FromArgb(10, 11, 13); gsEvents.ForeColor = Dim; gsEvents.Font = new Font("Consolas", 9.5f);
        gameStatus.Controls.Add(gsEvents); gameStatus.Controls.Add(bar); gameStatus.Controls.Add(gsNow); gameStatus.Controls.Add(gsTitle);
        gsTimer.Tick += (s, e) => RefreshGameStatus();
    }

    void ShowGameStatus(bool running)
    {
        bool status = running && !gsListShown;
        gameStatus.Visible = status; games.Visible = !status;
        if (running) gsTimer.Start(); else gsTimer.Stop();
    }

    void StartGameWatch(Process found)
    {
        try
        {
            string dir = Path.GetDirectoryName(found.MainModule.FileName);
            if (gsWatcher != null && !gsWatcher.HasExited) gsWatcher.Kill();
            gsWatcher = Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(Tools, "assistant", "trace_game.ps1") + "\" -GameDir \"" + dir + "\" -Label \"" + Path.GetFileName(dir) + "\"") { CreateNoWindow = true, UseShellExecute = false });
            gsTitle.Text = Clean(Path.GetFileName(dir));
        }
        catch { gsTitle.Text = found.ProcessName; }
        gsNow.Text = "Reading the game..."; gsEvents.Clear();
    }

    void RefreshGameStatus()
    {
        string file = Path.Combine(LiveDir, "game-watch.json");
        try
        {
            if (!File.Exists(file)) return;
            var st = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(file).TrimStart('﻿'));
            gsTitle.Text = Clean(Get(st, "name")) + "   (running " + Get(st, "up_min") + " min)";
            string compiling = Get(st, "compiling");
            gsNow.Text = string.Format("{0} frames drawn  |  game memory {1} GB  |  {2} GB of {3} GB left", Get(st, "frames"), Get(st, "game_gb"), Get(st, "left_gb"), Get(st, "total_gb"))
                         + "\n" + (compiling.Length > 0 ? "Now: compiling " + compiling + ". The screen stays black until it is done." : "Now: running");
            object ev; var lines = new List<string>();
            if (st.TryGetValue("events", out ev) && ev is IEnumerable) foreach (var x in (IEnumerable)ev) lines.Add(Clean(Convert.ToString(x)));
            lines.Reverse();
            string text = string.Join("\n", lines);
            if (gsEvents.Text != text) gsEvents.Text = text;
        }
        catch { }
    }
    readonly RichTextBox console = new RichTextBox();
    readonly Timer timer = new Timer();
    readonly Button pause;
    long traceRead;
    bool firstRead = true;
    string currentGame = "";

    // the assistant panel
    readonly RichTextBox chat = new RichTextBox();
    readonly TextBox input = new TextBox();
    readonly Label assistantStatus = new Label(), askText = new Label();
    readonly Panel askPanel = new Panel();
    readonly CheckBox autoBox = new CheckBox(), explainBox = new CheckBox();
    // Voice: Talk opens Windows voice typing (Win+H) in the chat box; "Read replies aloud" speaks answers with the Windows voice.
    readonly CheckBox speakBox = new CheckBox();
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    object voice;

    Button talk;
    readonly Timer discordWatch = new Timer { Interval = 3000 };

    // Windows records which programs use the microphone and screen capture right now (Privacy settings read the same
    // place): a Discord entry that started and has not stopped is a call (microphone) or a Go Live stream (capture).
    static bool DiscordUsing(params string[] capabilities)
    {
        if (Process.GetProcessesByName("Discord").Length == 0) return false;
        foreach (string cap in capabilities)
        {
            using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\" + cap + @"\NonPackaged"))
            {
                if (key == null) continue;
                foreach (string name in key.GetSubKeyNames())
                {
                    if (name.IndexOf("discord", StringComparison.OrdinalIgnoreCase) < 0 || !File.Exists(name.Replace('#', '\\'))) continue;
                    using (var app = key.OpenSubKey(name))
                    {
                        object start = app.GetValue("LastUsedTimeStart"), stop = app.GetValue("LastUsedTimeStop");
                        if (start is long && (long)start > 0 && stop is long && (long)stop == 0) return true;
                    }
                }
            }
        }
        return false;
    }
    static bool DiscordStreaming() { return DiscordUsing("graphicsCaptureProgrammatic", "graphicsCaptureWithoutBorder"); }

    static readonly string RemoteViewPath = Path.Combine(Tools, "live", "remote-view.txt");
    static DateTime remoteStarted = DateTime.MinValue;
    static void ShowRemote(RichTextBox box)
    {
        try
        {
            bool fresh = File.Exists(RemoteViewPath) && File.GetLastWriteTime(RemoteViewPath) > DateTime.Now.AddMinutes(-2);
            if (!fresh && remoteStarted < DateTime.Now.AddMinutes(-5))
            {
                remoteStarted = DateTime.Now;
                Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + Path.Combine(Tools, "remote-view.ps1") + "\"") { UseShellExecute = false, CreateNoWindow = true });
            }
            string text = File.Exists(RemoteViewPath) ? File.ReadAllText(RemoteViewPath) : "Asking the Chromebook and the MacBook...";
            if (!fresh && File.Exists(RemoteViewPath)) text = "(not updated for over 2 minutes)\n" + text;
            if (box.Text != text) box.Text = text;
        }
        catch { }
    }
    static bool DiscordCall() { return DiscordUsing("microphone"); }

    void WatchDiscord()
    {
        bool streaming = DiscordStreaming();
        if (streaming) Speak("", true);
        if (talk != null)
        {
            talk.Enabled = !streaming;
            talk.Text = streaming ? "Talk (off: Discord stream)" : "Talk";
            talk.Width = streaming ? 190 : 70;
        }
    }

    void StartVoiceTyping()
    {
        if (DiscordStreaming()) { Say("Voice is off while you stream on Discord, so the stream does not hear you talking to the assistant.", Yellow); return; }
        if (DiscordCall() && MessageBox.Show(this, "You are in a Discord call, so they will hear you talking to the assistant.\n\nTalk anyway?", "AnyPS5 Games", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        input.Focus();
        input.SelectionStart = input.TextLength;
        BeginInvoke(new Action(() =>
        {
            keybd_event(0x5B, 0, 0, UIntPtr.Zero); keybd_event(0x48, 0, 0, UIntPtr.Zero);
            keybd_event(0x48, 0, 2, UIntPtr.Zero); keybd_event(0x5B, 0, 2, UIntPtr.Zero);
        }));
    }

    void Speak(string text, bool stopOnly = false)
    {
        try
        {
            if (!stopOnly && DiscordStreaming()) { Say("(not read aloud: you are streaming on Discord)", Dim, true); return; }
            if (voice == null) { if (stopOnly) return; voice = Activator.CreateInstance(Type.GetTypeFromProgID("SAPI.SpVoice")); }
            string plain = stopOnly ? "" : Regex.Replace(Regex.Replace(Regex.Replace(text, @"https?://\S+", "a link"), @"[`*#_>|\[\]]", " "), @"\s+", " ").Trim();
            if (plain.Length > 1500) plain = plain.Substring(0, 1500);
            voice.GetType().InvokeMember("Speak", System.Reflection.BindingFlags.InvokeMethod, null, voice, new object[] { plain, 3 });
        }
        catch { }
    }
    readonly Button send, newChat;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    readonly Queue<string> pending = new Queue<string>();
    Process assistant;
    StreamWriter assistantIn;
    bool busy = true, autoSent;

    class GameTag { public string Dir, Key, Name; }

    readonly TabControl tabs = new DarkTabs();

    class DarkTabs : TabControl
    {
        public DarkTabs()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            ItemSize = new Size(0, 28); Padding = new Point(12, 4);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            using (var back = new SolidBrush(Bg)) e.Graphics.FillRectangle(back, ClientRectangle);
            for (int i = 0; i < TabCount; i++)
            {
                var r = GetTabRect(i);
                bool on = i == SelectedIndex;
                using (var fill = new SolidBrush(on ? Panel : Bg)) e.Graphics.FillRectangle(fill, r);
                if (on) using (var line = new SolidBrush(Cyan)) e.Graphics.FillRectangle(line, r.X, r.Bottom - 2, r.Width, 2);
                TextRenderer.DrawText(e.Graphics, TabPages[i].Text, Font, r, on ? Fg : Dim, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
            }
        }
    }

    [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)] static extern int SetWindowTheme(IntPtr hwnd, string app, string list);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    static void DarkScrollbars(Control c)
    {
        c.HandleCreated += (s, e) => SetWindowTheme(c.Handle, "DarkMode_Explorer", null);
        if (c.IsHandleCreated) SetWindowTheme(c.Handle, "DarkMode_Explorer", null);
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        int on = 1;
        DwmSetWindowAttribute(Handle, 20, ref on, sizeof(int));  // dark title bar
    }

    // A dark header like the rest of the window, on every list.
    static void DarkHeader(ListView l)
    {
        DarkScrollbars(l);
        l.OwnerDraw = true;
        l.DrawItem += (s, e) => e.DrawDefault = true;
        l.DrawSubItem += (s, e) => e.DrawDefault = true;
        l.DrawColumnHeader += (s, e) =>
        {
            using (var back = new SolidBrush(Panel)) e.Graphics.FillRectangle(back, e.Bounds);
            TextRenderer.DrawText(e.Graphics, e.Header.Text, l.Font, Rectangle.Inflate(e.Bounds, -4, 0), Dim, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
        };
    }

    static ListView List(params object[] columns)
    {
        var l = new ListView { View = View.Details, FullRowSelect = true, HeaderStyle = ColumnHeaderStyle.Nonclickable, BackColor = Bg, ForeColor = Fg, BorderStyle = BorderStyle.None, Dock = DockStyle.Fill, Font = new Font("Segoe UI", 9.5f) };
        for (int i = 0; i < columns.Length; i += 2) l.Columns.Add((string)columns[i], (int)columns[i + 1]);
        DarkHeader(l);
        return l;
    }

    static Label Caption(string text) { return new Label { Text = text, Dock = DockStyle.Top, Height = 26, BackColor = Panel, ForeColor = Dim, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true }; }

    // A row of buttons that wraps onto a second line when the window is narrow (nothing is cut off at the edge).
    static FlowLayoutPanel Bar(DockStyle dock)
    {
        return new FlowLayoutPanel { Dock = dock, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = true, BackColor = Panel, Padding = new Padding(4, 3, 4, 3) };
    }

    // The columns share the list's width by weight, so no column runs past the edge; hovering a row shows its full text.
    static void Fit(ListView l, params int[] weights)
    {
        l.ShowItemToolTips = true;
        EventHandler fit = (s, e) =>
        {
            int w = l.ClientSize.Width - 2, sum = weights.Sum();
            if (w < 80) return;
            for (int i = 0; i < l.Columns.Count && i < weights.Length; i++) l.Columns[i].Width = Math.Max(28, w * weights[i] / sum);
        };
        l.Resize += fit; l.HandleCreated += fit;
    }

    // Runs a python helper off the UI thread and hands its JSON output back on the UI thread.
    void Helper(string script, Action<Dictionary<string, object>> done)
    {
        System.Threading.ThreadPool.QueueUserWorkItem(_ =>
        {
            Dictionary<string, object> result = null;
            try
            {
                var p = Process.Start(new ProcessStartInfo("python", "-I \"" + Path.Combine(Tools, script) + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8 });
                string text = p.StandardOutput.ReadToEnd(); p.WaitForExit();
                result = json.Deserialize<Dictionary<string, object>>(text);
            }
            catch { }
            Ui(() => done(result));
        });
    }

    static List<Dictionary<string, object>> Rows(Dictionary<string, object> d, string key)
    {
        var list = new List<Dictionary<string, object>>();
        object v; if (d == null || !d.TryGetValue(key, out v) || !(v is IEnumerable)) return list;
        foreach (object o in (IEnumerable)v) { var row = o as Dictionary<string, object>; if (row != null) list.Add(row); }
        return list;
    }

    static string GB(object bytes) { double b = bytes == null ? 0 : Convert.ToDouble(bytes); return (b / 1073741824.0).ToString("0.00") + " GB"; }

    // --- the running game, inside this window -----------------------------------------------------------------
    // The game's own window is moved into the Game screen tab (SetParent): Blu plays it right here. Its title (with
    // AnyPS5's FPS counter) can no longer be read from the process as a main window, so while it is inside, its title
    // is written to _tools\live\title-<pid>.txt every second: the pipeline's test and test-launch.ps1 read it there.
    // Before this window closes the game is given back as a normal window, so closing this window never closes a game.
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT r);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hwnd, out RECT r);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int cmd);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr h);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] static extern IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
    [DllImport("user32.dll")] static extern IntPtr SetFocus(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd, int msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsHungAppWindow(IntPtr hwnd);
    struct RECT { public int L, T, R, B; }
    const int GWL_STYLE = -16;
    const long WS_CHILD = 0x40000000, WS_POPUP = 0x80000000, WS_CAPTION = 0x00C00000, WS_THICKFRAME = 0x00040000,
               WS_SYSMENU = 0x00080000, WS_MINIMIZEBOX = 0x00020000, WS_MAXIMIZEBOX = 0x00010000, WS_VISIBLE = 0x10000000;
    const uint SWP_NOZORDER = 0x0004, SWP_FRAMECHANGED = 0x0020, SWP_SHOWWINDOW = 0x0040, SWP_NOACTIVATE = 0x0010;

    TabPage gamePage;
    readonly Panel gameHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.Black };
    readonly Label hostNote = new Label { AutoSize = false, Dock = DockStyle.Fill, ForeColor = Color.FromArgb(140, 146, 156), TextAlign = ContentAlignment.MiddleCenter };
    readonly Label gameTitle = new Label();
    readonly CheckBox followGames = new CheckBox(), insideBox = new CheckBox();
    readonly Timer screenTimer = new Timer();
    Button popButton;
    Process liveGame;
    TabPage before;
    IntPtr embedded = IntPtr.Zero;
    IntPtr embeddedStyle;
    RECT embeddedRect;
    double embeddedAspect = 16.0 / 9.0;
    bool poppedOut;
    // Blu 11-05: while a game runs, the game list on the left gives its room to the game; "Games" shows it again.
    Panel gameSide;
    Button listButton;
    bool gameListOpen;

    void ShowGameList()
    {
        if (gameSide == null) return;
        gameSide.Visible = liveGame == null || gameListOpen;
        listButton.Visible = liveGame != null;
        listButton.Text = gameListOpen ? "Hide games" : "Games";
    }

    TabPage BuildGamePage()
    {
        gamePage = new TabPage("Game screen") { BackColor = Color.Black };
        var bar = Bar(DockStyle.Top);
        gameTitle.Text = "No game running. Pick one on the left. Keyboard: WASD move, Enter Cross, C Circle, Esc menu; F7 or the middle mouse button turns mouse look on; F11 fullscreen.";
        gameTitle.Dock = DockStyle.Top; gameTitle.Height = 24; gameTitle.AutoEllipsis = true; gameTitle.ForeColor = Fg; gameTitle.BackColor = Panel; gameTitle.TextAlign = ContentAlignment.MiddleLeft; gameTitle.Padding = new Padding(6, 0, 0, 0);
        popButton = Button("Put it inside", (s, e) => { if (embedded != IntPtr.Zero) { poppedOut = true; insideBox.Checked = false; } else if (liveGame != null) { poppedOut = false; insideBox.Checked = true; } });
        popButton.Width = 110; popButton.Height = 28;
        insideBox.Text = "Play games inside this window"; insideBox.Checked = false; insideBox.AutoSize = true; insideBox.ForeColor = Dim; insideBox.Margin = new Padding(10, 8, 4, 4);
        insideBox.CheckedChanged += (s, e) => { if (!insideBox.Checked) Detach(); else { poppedOut = false; TryEmbed(); } };
        followGames.Text = "Switch here when a game starts"; followGames.Checked = true; followGames.AutoSize = true; followGames.ForeColor = Dim; followGames.Margin = new Padding(10, 8, 4, 4);
        var shot = Button("Screenshot", (s, e) => Screenshot()); shot.Width = 95; shot.Height = 28;
        var whatsOn = Button("What's on screen?", (s, e) => { if (liveGame == null) Say("No game is running.", Dim); else Ask("Look at the game screen (look_at_game) and tell me what is happening and whether anything looks broken."); });
        whatsOn.Width = 135; whatsOn.Height = 28;
        var full = Button("Full screen", (s, e) => ToggleFullScreen()); full.Width = 90; full.Height = 28;
        listButton = Button("Games", (s, e) => { gameListOpen = !gameListOpen; ShowGameList(); }); listButton.Width = 90; listButton.Height = 28; listButton.Visible = false;
        bar.Controls.AddRange(new Control[] { full, whatsOn, shot, popButton, listButton });
        hostNote.Text = "No game running.";
        gameHost.Controls.Add(hostNote);
        gameHost.Resize += (s, e) => FitEmbedded();
        gameHost.VisibleChanged += (s, e) => FitEmbedded();
        gameHost.MouseDown += (s, e) => { if (embedded != IntPtr.Zero) SetFocus(embedded); };
        var side = gameSide = BuildGameList();
        gamePage.Controls.Add(gameHost); gamePage.Controls.Add(gameTitle); gamePage.Controls.Add(bar); gamePage.Controls.Add(side);
        gamePage.Resize += (s, e) => side.Width = Math.Max(220, Math.Min(340, gamePage.Width * 30 / 100));
        screenTimer.Interval = 250; screenTimer.Tick += (s, e) => { try { GameTick(); } catch (Exception ex) { EmbedLog("game check failed: " + ex.GetType().Name + ": " + ex.Message); } }; screenTimer.Start();
        Application.ApplicationExit += (s, e) => Detach();
        return gamePage;
    }

    // 11-15 app loop (_tools\console\app-loop.ps1): live\workapp-check.flag makes this running window answer in
    // live\workapp-check.txt from its UI timer, so a hung window or an old build shows up without opening anything.
    static readonly string WorkCheckFlag = Path.Combine(Tools, "live", "workapp-check.flag");
    void CheckWorkApp()
    {
        if (!File.Exists(WorkCheckFlag)) return;
        try { File.Delete(WorkCheckFlag); } catch { }
        var lines = new List<string> { "answered " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"), "build " + File.GetLastWriteTime(Application.ExecutablePath).ToString("yyyy-MM-dd HH:mm"), "tabs " + tabs.TabPages.Count, "paused " + Paused() };
        try { lines.Add("elevated " + new System.Security.Principal.WindowsPrincipal(System.Security.Principal.WindowsIdentity.GetCurrent()).IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator)); } catch { }
        try { lines.Add("pipeline heartbeat " + (File.Exists(AlivePath) ? File.ReadAllText(AlivePath).Trim() : "never")); } catch { }
        try { File.WriteAllLines(Path.Combine(Tools, "live", "workapp-check.txt"), lines); } catch { }
    }

    static void EmbedLog(string text)
    {
        try { Directory.CreateDirectory(LiveDir); File.AppendAllText(Path.Combine(LiveDir, "embed.log"), DateTime.Now.ToString("HH:mm:ss ") + text + Environment.NewLine); } catch { }
    }

    void TryEmbed()
    {
        if (embedded != IntPtr.Zero || liveGame == null || poppedOut || !insideBox.Checked || ScreenDir != null || Exited(liveGame)) return;
        try
        {
            liveGame.Refresh();
            IntPtr h = liveGame.MainWindowHandle;
            if (h == IntPtr.Zero || !IsWindow(h)) return;
            RECT client; GetClientRect(h, out client);
            if (client.R - client.L < 50 || client.B - client.T < 50) return;   // still opening
            embeddedAspect = (double)(client.R - client.L) / (client.B - client.T);
            GetWindowRect(h, out embeddedRect);
            embeddedStyle = GetWindowLongPtr(h, GWL_STYLE);
            long style = (embeddedStyle.ToInt64() & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)) | WS_CHILD | WS_VISIBLE;
            SetParent(h, gameHost.Handle);
            SetWindowLongPtr(h, GWL_STYLE, new IntPtr(style));
            embedded = h;
            HoldHotkey(true);
            EmbedLog("embedded " + liveGame.ProcessName + " window " + h + " (" + (client.R - client.L) + "x" + (client.B - client.T) + ")");
            hostNote.Visible = false;
            FitEmbedded();
            SetFocus(h);
            popButton.Text = "Pop out";
        }
        catch (Exception ex) { embedded = IntPtr.Zero; EmbedLog("embed failed: " + ex.Message); }
    }

    // Keeps the game's own shape (no stretching), as large as the tab allows, centred.
    // Full screen: the game moves into a borderless window that covers the whole monitor this window is on; F11 (a
    // hotkey this window holds only while a game is inside it, so it works while the game has the keyboard) or the
    // button switches back.
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, uint key);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr hwnd, int id);
    const int FullScreenHotkey = 0x5F11, WM_HOTKEY = 0x0312;
    Panel fullPanel;
    bool isFull;
    FormBorderStyle fullBorder;
    FormWindowState fullState;
    Rectangle fullBounds;
    bool hotkeyHeld;

    Control GameSurface() { return isFull ? (Control)fullPanel : gameHost; }

    void HoldHotkey(bool hold)
    {
        if (hold && !hotkeyHeld) hotkeyHeld = RegisterHotKey(Handle, FullScreenHotkey, 0, 0x7A);  // VK_F11
        else if (!hold && hotkeyHeld) { UnregisterHotKey(Handle, FullScreenHotkey); hotkeyHeld = false; }
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WM_HOTKEY && m.WParam.ToInt32() == FullScreenHotkey) ToggleFullScreen();
        base.WndProc(ref m);
    }

    void ToggleFullScreen()
    {
        if (embedded == IntPtr.Zero || !IsWindow(embedded)) { ExitFullScreen(); return; }
        if (isFull) { ExitFullScreen(); return; }
        if (fullPanel == null)
        {
            fullPanel = new Panel { BackColor = Color.Black, Visible = false, Anchor = AnchorStyles.Left | AnchorStyles.Top | AnchorStyles.Right | AnchorStyles.Bottom };
            fullPanel.Resize += (s, e) => FitEmbedded();
            fullPanel.MouseDown += (s, e) => { if (embedded != IntPtr.Zero) SetFocus(embedded); };
            Controls.Add(fullPanel);
        }
        fullBorder = FormBorderStyle; fullState = WindowState; fullBounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
        var screen = Screen.FromControl(this).Bounds;
        isFull = true;
        WindowState = FormWindowState.Normal;
        FormBorderStyle = FormBorderStyle.None;
        Bounds = screen;
        fullPanel.Bounds = ClientRectangle;
        fullPanel.Visible = true; fullPanel.BringToFront();
        SetParent(embedded, fullPanel.Handle);
        FitEmbedded();
        SetFocus(embedded);
    }

    void ExitFullScreen()
    {
        if (!isFull) return;
        isFull = false;
        if (embedded != IntPtr.Zero && IsWindow(embedded)) SetParent(embedded, gameHost.Handle);
        fullPanel.Visible = false;
        FormBorderStyle = fullBorder;
        Bounds = fullBounds;
        WindowState = fullState;
        FitEmbedded();
        if (embedded != IntPtr.Zero) SetFocus(embedded);
    }

    void FitEmbedded()
    {
        if (embedded == IntPtr.Zero) return;
        var surface = GameSurface();
        int W = surface.ClientSize.Width, H = surface.ClientSize.Height;
        if (W < 50 || H < 50) return;
        int w = W, h = (int)(W / embeddedAspect);
        if (h > H) { h = H; w = (int)(H * embeddedAspect); }
        SetWindowPos(embedded, IntPtr.Zero, (W - w) / 2, (H - h) / 2, w, h, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    }

    // Gives the game back as a normal window, where and as large as it was.
    void Detach()
    {
        ExitFullScreen(); HoldHotkey(false);
        IntPtr h = embedded;
        embedded = IntPtr.Zero;
        if (h == IntPtr.Zero || !IsWindow(h)) return;
        try
        {
            SetParent(h, IntPtr.Zero);
            SetWindowLongPtr(h, GWL_STYLE, embeddedStyle);
            SetWindowPos(h, IntPtr.Zero, embeddedRect.L, embeddedRect.T, embeddedRect.R - embeddedRect.L, embeddedRect.B - embeddedRect.T, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW);
            SetForegroundWindow(h);
        }
        catch { }
        hostNote.Text = "The game is in its own window now. Press \"Put it back inside\" to play it here again.";
        hostNote.Visible = true;
        popButton.Text = "Put it back inside";
    }

    int gameCheck, frameTick;
    // The live picture is also saved for the assistant (look_at_game / watch_game read it): _tools\live\frame.png
    // every 2 seconds while a game runs, with frame.txt holding the game, its window title and the time.
    static readonly string LiveDir = Path.Combine(Tools, "live");

    IntPtr GameWindow()
    {
        if (embedded != IntPtr.Zero) return IsWindow(embedded) ? embedded : IntPtr.Zero;
        if (liveGame == null) return IntPtr.Zero;
        try { liveGame.Refresh(); return liveGame.MainWindowHandle; } catch { return IntPtr.Zero; }
    }

    string GameTitleText(IntPtr h)
    {
        var sb = new StringBuilder(512); GetWindowText(h, sb, 512); return sb.ToString();
    }

    void GameTick()
    {
        // 11-08: inside the AnyPS5 Machines app every game (Blu's or a pipeline test) goes into this window instead of
        // popping out over Blu's screen; the app no longer grabs game windows itself, which is what broke "Put it inside".
        if (!insideBox.Checked && !poppedOut && GetParent(Handle) != IntPtr.Zero) insideBox.Checked = true;
        if (gameCheck++ % 8 == 0) FindGame();
        if (liveGame == null || Exited(liveGame) || ScreenDir != null) return;
        if (embedded == IntPtr.Zero) TryEmbed();
        if (embedded != IntPtr.Zero && !IsWindow(embedded)) { embedded = IntPtr.Zero; return; }
        IntPtr h = GameWindow();
        if (h == IntPtr.Zero) return;
        if (embedded == IntPtr.Zero && h != shownWindow) ShowOnStreamScreen(h);
        if (IsHungAppWindow(h)) { gameTitle.Text = "The game is not responding (it may still be loading)."; return; }
        string title = GameTitleText(h);
        gameTitle.Text = title;
        bool second = frameTick++ % 4 == 0;
        if (second && embedded != IntPtr.Zero)
            try { Directory.CreateDirectory(LiveDir); File.WriteAllText(Path.Combine(LiveDir, "title-" + liveGame.Id + ".txt"), title); } catch { }
        if (frameTick % 8 == 0 && WindowState != FormWindowState.Minimized) SaveFrame(h, title);
    }

    Bitmap CaptureWindow(IntPtr h)
    {
        RECT r; GetWindowRect(h, out r);
        int w = r.R - r.L, hh = r.B - r.T; if (w < 50 || hh < 50) return null;
        var bmp = new Bitmap(w, hh);
        using (var g = Graphics.FromImage(bmp))
        {
            IntPtr dc = g.GetHdc(); PrintWindow(h, dc, 2); g.ReleaseHdc(dc);
            // Some Vulkan windows print as black: take the pixels from the screen instead (when it is visible).
            if (Black(bmp)) g.CopyFromScreen(r.L, r.T, 0, 0, new Size(w, hh));
        }
        return bmp;
    }

    static bool Black(Bitmap b)
    {
        for (int i = 1; i < 10; i++) { var c = b.GetPixel(b.Width * i / 10, b.Height * ((i * 7) % 10 + 1) / 12); if (c.R + c.G + c.B > 30) return false; }
        return true;
    }

    volatile bool savingFrame;
    void SaveFrame(IntPtr h, string title)
    {
        try
        {
            if (savingFrame) return;
            var shot = CaptureWindow(h);
            if (shot == null) return;
            savingFrame = true;
            string exe = ""; try { exe = liveGame.MainModule.FileName; } catch { }
            System.Threading.ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    using (shot)
                    {
                        Directory.CreateDirectory(LiveDir);
                        string tmp = Path.Combine(LiveDir, "frame.tmp.png"), dst = Path.Combine(LiveDir, "frame.png");
                        shot.Save(tmp, System.Drawing.Imaging.ImageFormat.Png);
                        if (File.Exists(dst)) File.Delete(dst);
                        File.Move(tmp, dst);
                    }
                }
                catch { }
                savingFrame = false;
            });
            File.WriteAllText(Path.Combine(LiveDir, "frame.txt"), "game: " + Path.GetFileName(Path.GetDirectoryName(exe)) + "\r\ntitle: " + title + "\r\ntime: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\r\n");
        }
        catch { }
    }

    void Screenshot()
    {
        IntPtr h = GameWindow();
        if (h == IntPtr.Zero) { Say("No game picture to save.", Dim); return; }
        try
        {
            using (var bmp = CaptureWindow(h))
            {
                string dir = Path.Combine(Root, "_reports", "screenshots"); Directory.CreateDirectory(dir);
                string file = Path.Combine(dir, liveGame.ProcessName + " " + DateTime.Now.ToString("yyyy-MM-dd HHmmss") + ".png");
                bmp.Save(file, System.Drawing.Imaging.ImageFormat.Png);
                Say("Screenshot saved: " + file, Green);
            }
        }
        catch (Exception ex) { Say("Could not save the screenshot: " + ex.Message, Red); }
    }

    // A game started from AnyPS5.exe plays inside that app; the pid is in _tools\livenyps5-app-game.txt.
    static int AppGamePid() { try { return int.Parse(File.ReadAllText(Path.Combine(Tools, "live", "anyps5-app-game.txt")).Trim()); } catch { return -1; } }

    // 11-15 Blu: AnyPS5.exe (the console-style app) also lives in a tab here, and can be popped out to its own window and
    // put back. The tab starts it with --embedded the first time it opens; its window is a child of appHost while inside.
    TabPage appPage;
    readonly Panel appHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(6, 7, 12) };
    readonly Button appButton = new Button();
    readonly Label appNote = new Label { AutoSize = false, Dock = DockStyle.Fill, ForeColor = Color.FromArgb(140, 146, 156), TextAlign = ContentAlignment.MiddleCenter, Text = "Opening AnyPS5..." };
    readonly Timer appTimer = new Timer { Interval = 500 };
    Process appProc;
    IntPtr appWindow;
    IntPtr appStyle;
    bool appOut;

    TabPage BuildAppPage()
    {
        appPage = new TabPage("AnyPS5") { BackColor = Color.FromArgb(6, 7, 12) };
        var bar = Bar(DockStyle.Top);
        appButton.Text = "Pop out"; appButton.Width = 120; appButton.Height = 26; appButton.FlatStyle = FlatStyle.Flat; appButton.ForeColor = Fg;
        appButton.Click += (s, e) => { if (appOut) PutAppInside(); else PopAppOut(); };
        bar.Controls.Add(appButton);
        appHost.Controls.Add(appNote);
        appHost.Resize += (s, e) => FitApp();
        appHost.Click += (s, e) => { if (appWindow != IntPtr.Zero) SetFocus(appWindow); };
        appPage.Controls.Add(appHost); appPage.Controls.Add(bar);
        appTimer.Tick += (s, e) => AppTick();
        tabs.SelectedIndexChanged += (s, e) => { if (tabs.SelectedTab == appPage) { if (appProc == null) appNote.Text = "Opening AnyPS5..."; appTimer.Start(); if (appWindow != IntPtr.Zero && !appOut) SetFocus(appWindow); } };
        return appPage;
    }

    void AppTick()
    {
        if (appProc == null || appProc.HasExited)
        {
            bool wasOpen = appWindow != IntPtr.Zero || appOut;
            appWindow = IntPtr.Zero; appOut = false; appButton.Text = "Pop out"; appNote.Visible = true;
            if (wasOpen) { appProc = null; appNote.Text = "AnyPS5 was closed. Open this tab again to start it."; appTimer.Stop(); return; }
            var running = Process.GetProcessesByName("AnyPS5");
            if (running.Length > 0) appProc = running[0];
            else
            {
                if (tabs.SelectedTab != appPage) { appTimer.Stop(); return; }
                try { appProc = Process.Start(new ProcessStartInfo(Path.Combine(Root, "AnyPS5.exe"), "--embedded") { WorkingDirectory = Root }); }
                catch (Exception ex) { appNote.Text = "AnyPS5 could not start: " + ex.Message; appTimer.Stop(); return; }
            }
        }
        if (appOut || (appWindow != IntPtr.Zero && IsWindow(appWindow))) return;
        appProc.Refresh();
        IntPtr h = appProc.MainWindowHandle;
        if (h == IntPtr.Zero || !IsWindow(h)) return;
        appStyle = GetWindowLongPtr(h, GWL_STYLE);
        long style = (appStyle.ToInt64() & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)) | WS_CHILD | WS_VISIBLE;
        SetParent(h, appHost.Handle);
        SetWindowLongPtr(h, GWL_STYLE, new IntPtr(style));
        appWindow = h; appNote.Visible = false;
        FitApp();
        if (tabs.SelectedTab == appPage) SetFocus(h);
    }

    void FitApp()
    {
        if (appWindow == IntPtr.Zero || appOut || !IsWindow(appWindow)) return;
        SetWindowPos(appWindow, IntPtr.Zero, 0, 0, appHost.ClientSize.Width, appHost.ClientSize.Height, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    }

    void PopAppOut()
    {
        if (appWindow == IntPtr.Zero || !IsWindow(appWindow)) return;
        SetParent(appWindow, IntPtr.Zero);
        SetWindowLongPtr(appWindow, GWL_STYLE, appStyle);
        var area = Screen.FromControl(this).WorkingArea;
        SetWindowPos(appWindow, IntPtr.Zero, area.Left + (area.Width - 1600) / 2, area.Top + (area.Height - 900) / 2, Math.Min(1600, area.Width), Math.Min(900, area.Height), SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW);
        SetForegroundWindow(appWindow);
        appOut = true; appButton.Text = "Put it inside"; appNote.Text = "AnyPS5 is in its own window. \"Put it inside\" brings it back."; appNote.Visible = true;
    }

    void PutAppInside()
    {
        appOut = false; appButton.Text = "Pop out"; appNote.Text = "Opening AnyPS5..."; appWindow = IntPtr.Zero;
        appTimer.Start(); AppTick();
    }

    bool findingGame;
    void FindGame()
    {
        if (findingGame) return;
        findingGame = true;
        System.Threading.ThreadPool.QueueUserWorkItem(_ =>
        {
        Process found = null;
        string failed = null;
        try
        {
            foreach (System.Management.ManagementObject o in new System.Management.ManagementObjectSearcher("SELECT ProcessId, ExecutablePath FROM Win32_Process WHERE Name LIKE '%.exe'").Get())
            {
                string path = o["ExecutablePath"] as string;
                if (path == null || !path.StartsWith(Root, StringComparison.OrdinalIgnoreCase) || path.IndexOf("\\_tools\\", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                if (Path.GetFileName(path).StartsWith("AnyPS5", StringComparison.OrdinalIgnoreCase)) continue;
                if (AppGamePid() == Convert.ToInt32(o["ProcessId"])) continue;
                Process candidate = null;
                try { candidate = Process.GetProcessById(Convert.ToInt32(o["ProcessId"])); } catch { }
                // 11-14: a lane test stuck while exiting (exit code set, one thread left, cannot be killed) was found again every 2 s.
                if (candidate == null || Exited(candidate)) continue;
                found = candidate;
                break;
            }
        }
        catch (Exception ex) { failed = ex.Message; }
        Ui(() => { findingGame = false; if (failed != null) EmbedLog("game search failed: " + failed); GameFound(found); });
        });
    }

    // A game shares its drive with the extractions and builds: they freeze while it runs and go on when it closes. Not for
    // the pipeline's own test launch or an AI play session, which are part of what it is doing.
    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    struct LastInput { public uint Size; public uint Time; }
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LastInput info);
    static double IdleMinutes()
    {
        var li = new LastInput { Size = 8 };
        return GetLastInputInfo(ref li) ? unchecked((uint)Environment.TickCount - li.Time) / 60000.0 : 0;
    }

    void PauseForGame(int pid)
    {
        // Nobody at the keyboard or mouse for 5 minutes means Blu is not playing: a game that starts then is a test
        // (11-03 the FPS runs of Stray and Visage froze the pipeline twice).
        if (!pauseWhilePlaying.Checked || playPaused || Paused() || ScreenDir != null || IdleMinutes() >= 5) return;
        System.Threading.ThreadPool.QueueUserWorkItem(_ =>
        {
            var procs = Snapshot().ToDictionary(x => x.Id);
            Proc at;
            // A test copy (_test\...) is never Blu playing: 11-03 the FPS runner's Total Chaos test froze the pipeline.
            // 11-04 a crashing Scorn test copy was found again as it exited; gone from the snapshot, it froze the pipeline for good.
            if (!procs.TryGetValue(pid, out at) || at.Cmd.IndexOf(@"\_test\", StringComparison.OrdinalIgnoreCase) >= 0) return;
            for (int id = pid, hops = 0; hops < 12 && procs.TryGetValue(id, out at); id = at.Parent, hops++)
                if (at.Cmd.Contains("auto-games.ps1") || at.Cmd.Contains("ai-player.py") || at.Cmd.Contains("fps_wait_run") || at.Cmd.Contains(@"\_tools\")) return;
            int frozen = FreezeAll();
            Ui(() =>
            {
                // The game may have closed (or been closed by FreezeAll) before this ran: nothing would thaw the pipeline then.
                if (liveGame == null || Exited(liveGame)) { ThawAll(); UpdateHealth(); return; }
                playPaused = true;
                UpdateHealth();
                Say("A game is running: the pipeline is paused (" + frozen + " process(es) frozen). It continues when the game closes.", Dim);
            });
        });
    }

    static bool Exited(Process p) { try { return p.HasExited; } catch { return false; } }

    // A second game keeps running when the followed one closes: the closed one is let go and the next one is followed.
    void GameFound(Process found)
    {
        if (liveGame != null && (found == null || Exited(liveGame)))
        {
            try { File.Delete(Path.Combine(LiveDir, "title-" + liveGame.Id + ".txt")); } catch { }
            ExitFullScreen(); HoldHotkey(false);
            liveGame = null; embedded = IntPtr.Zero;
            ShowGameStatus(false);
            try { if (gsWatcher != null && !gsWatcher.HasExited) gsWatcher.Kill(); } catch { }
            if (playPaused) { playPaused = false; ThawAll(); UpdateHealth(); Say("The game closed: the pipeline continues.", Dim); }
            hostNote.Text = "The game closed."; hostNote.Visible = true; insideBox.Checked = false; popButton.Text = "Put it inside"; shownWindow = IntPtr.Zero;
            gameTitle.Text = "No game running. Pick one on the left. Keyboard: WASD move, Enter Cross, C Circle, Esc menu; F7 or the middle mouse button turns mouse look on; F11 fullscreen.";
            if (followGames.Checked && tabs.SelectedTab == gamePage && before != null && found == null) tabs.SelectedTab = before;
            gameListOpen = false; ShowGameList();
        }
        if (found != null && liveGame == null)
        {
            EmbedLog("found game " + found.ProcessName + " pid " + found.Id);
            PauseForGame(found.Id);
            liveGame = found; poppedOut = false;
            gsListShown = false; if (ScreenDir == null) StartGameWatch(found); ShowGameStatus(true);
            hostNote.Text = "Starting " + found.ProcessName + "...";
            gameListOpen = false; ShowGameList();
            if (followGames.Checked && tabs.SelectedTab != gamePage) { before = tabs.SelectedTab; tabs.SelectedTab = gamePage; }
        }
    }

    // Blu streams on Discord. A window share sends only this window (Discord's log: "source id window:<this window's
    // handle> ... pid null"), so a game's own window never reaches it, whichever process started the game. A Screen
    // share sends the whole monitor, so each game's own window is put on the monitor this window is on, in front,
    // without taking the keyboard. It stays a normal window: games go inside this window only with "Put it inside".
    [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);
    static readonly IntPtr HWND_TOP = IntPtr.Zero;
    const uint SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002;
    IntPtr shownWindow;

    void ShowOnStreamScreen(IntPtr h)
    {
        shownWindow = h;
        try
        {
            RECT r; GetWindowRect(h, out r);
            int w = r.R - r.L, hh = r.B - r.T; if (w < 50 || hh < 50) { shownWindow = IntPtr.Zero; return; }
            bool same = MonitorFromWindow(h, 2) == MonitorFromWindow(Handle, 2);
            var area = Screen.FromHandle(Handle).WorkingArea;
            int x = area.Left + Math.Max(0, (area.Width - w) / 2), y = area.Top + Math.Max(0, (area.Height - hh) / 2);
            SetWindowPos(h, HWND_TOP, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | (same ? SWP_NOMOVE : 0));
            EmbedLog("game window " + h + " shown in front" + (same ? " (already on this window's monitor)" : " and moved to this window's monitor " + area));
            hostNote.Text = "The game runs in its own window, in front on this monitor: a Discord Screen share of this monitor shows it (a window share of this app does not). \"Put it inside\" plays it here instead.";
            popButton.Text = "Put it inside";
        }
        catch (Exception ex) { EmbedLog("could not show the game window: " + ex.Message); }
    }

    // --- downloads: Chrome's game downloads (live) and a health check of every game folder -------------------------
    TabPage downloadsPage;
    readonly ListView chromeList = List("Game", 230, "File", 210, "Status", 190, "Progress", 190, "Speed", 90);
    readonly ListView folderList = List("Game folder", 260, "Health", 120, "Size", 80, "Pipeline", 220, "Details", 600);
    readonly Label downloadsInfo = Caption("  Downloads");
    readonly Timer downloadsTimer = new Timer();
    bool downloadsBusy;

    TabPage BuildDownloadsPage()
    {
        downloadsPage = new TabPage("Downloads") { BackColor = Bg };
        var split2 = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, BackColor = Panel, SplitterWidth = 4 };
        split2.Panel1.Controls.Add(chromeList); split2.Panel1.Controls.Add(Caption("  Chrome downloads into the game folders on D: and G: (live; refreshes every 5 s)"));
        split2.Panel2.Controls.Add(folderList); split2.Panel2.Controls.Add(Caption("  Every game folder: are its files complete? (double-click opens it)"));
        var dbar = Bar(DockStyle.Top);
        resumeInfo.AutoSize = true; resumeInfo.ForeColor = Dim; resumeInfo.Margin = new Padding(8, 9, 4, 4);
        dbar.Resize += (s, e) => resumeInfo.MaximumSize = new Size(Math.Max(150, dbar.ClientSize.Width - 200), 0);
        var setup = Button("Auto-resume setup", (s, e) => AutoResumeSetup()); setup.Width = 160;
        var clean = Button("Clean up now", (s, e) => CleanUp(true)); clean.Width = 120;
        dbar.Controls.Add(clean);
        dbar.Controls.AddRange(new Control[] { setup, resumeInfo });
        downloadsPage.Controls.Add(split2); downloadsPage.Controls.Add(downloadsInfo); downloadsPage.Controls.Add(dbar);
        folderList.DoubleClick += (s, e) => { if (folderList.SelectedItems.Count > 0) { string d = folderList.SelectedItems[0].Tag as string; if (d != null && Directory.Exists(d)) Process.Start("explorer.exe", "\"" + d + "\""); } };
        downloadsTimer.Interval = 5000; downloadsTimer.Tick += (s, e) => { if (tabs.SelectedTab == downloadsPage) RefreshDownloads(); }; downloadsTimer.Start();
        Fit(chromeList, 22, 24, 20, 22, 12);
        Fit(folderList, 24, 11, 8, 20, 37);
        return downloadsPage;
    }

    readonly Label resumeInfo = new Label();

    void AutoResumeSetup()
    {
        string folder = Path.Combine(Tools, "chrome-auto-resume");
        Process.Start("explorer.exe", "\"" + folder + "\"");
        MessageBox.Show(this,
            "Chrome can resume your game downloads by itself whenever they stop. One-time setup (about a minute):\n\n" +
            "1. In Chrome, open the address chrome://extensions\n" +
            "2. Turn on \"Developer mode\" (top right)\n" +
            "3. Click \"Load unpacked\" and choose this folder (it just opened):\n     " + folder + "\n\n" +
            "That's it. \"AnyPS5 download keeper\" then checks every minute and resumes any game download that stopped (saved into the ps5 games dumps folder). " +
            "It never starts new downloads, never touches other downloads, and leaves downloads you cancelled alone; one you pause by hand is resumed after 10 minutes.\n\n" +
            "A download whose server does not allow resuming (Chrome shows no Resume button) still has to be downloaded again.",
            "Auto-resume downloads", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    void RefreshDownloads()
    {
        if (downloadsBusy) return;
        downloadsBusy = true;
        Helper("downloads.py", d =>
        {
            downloadsBusy = false;
            if (d == null) { downloadsInfo.Text = "  could not read the downloads"; return; }
            chromeList.BeginUpdate(); chromeList.Items.Clear();
            int active = 0;
            foreach (var r in Rows(d, "chrome"))
            {
                string status = Get(r, "status"), pct = Get(r, "percent");
                double speed = r.ContainsKey("speed") && r["speed"] != null ? Convert.ToDouble(r["speed"]) : 0;
                if (status == "downloading") active++;
                string progress = (pct.Length > 0 ? pct + " %  " : "") + GB(r["received"]) + (Get(r, "total") != "0" ? " of " + GB(r["total"]) : "");
                var item = new ListViewItem(new[] { Get(r, "game"), Get(r, "file"), status, progress, speed > 0 ? (speed / 1048576.0).ToString("0.0") + " MB/s" : "" });
                item.ForeColor = status == "downloading" ? Cyan : status == "complete" ? Green : Yellow;
                chromeList.Items.Add(Scrubbed(item));
            }
            chromeList.EndUpdate();
            folderList.BeginUpdate(); folderList.Items.Clear();
            int attention = 0;
            foreach (var f in Rows(d, "folders"))
            {
                string verdict = Get(f, "verdict");
                if (verdict == "needs attention") attention++;
                string root = Get(f, "drive") == "G:" ? "G:\\ps5 downloads" : "D:\\ps5 games dumps from ps5";
                var item = new ListViewItem(new[] { Get(f, "game"), verdict, GB(f["size"]), Get(f, "pipeline"), Get(f, "details").Replace("\r", " ").Replace("\n", " ") }) { Tag = Path.Combine(root, Get(f, "game")) };
                item.ForeColor = verdict == "needs attention" ? Red : verdict == "downloading" ? Cyan : verdict == "ok" || verdict == "extracted" ? Green : Dim;
                folderList.Items.Add(Scrubbed(item));
            }
            folderList.EndUpdate();
            bool keeper = Get(d, "autoResume") == "True";
            resumeInfo.Text = keeper ? "Auto-resume is ON: Chrome resumes stopped game downloads by itself (checks every minute)." : "Auto-resume is OFF: press Auto-resume setup (one minute, once) so Chrome resumes stopped downloads by itself.";
            resumeInfo.ForeColor = keeper ? Green : Yellow;
            ApplyStorage(d);
            downloadsInfo.Text = "  " + Clean(storageText) + "   |   " + active + " downloading in Chrome   |   " + attention + " game folder(s) need attention (re-download or new files)   |   checked " + Get(d, "time");
            downloadsInfo.ForeColor = attention > 0 ? Yellow : Dim;
        });
    }

    // --- GitHub: Blu's pull requests and the newest ones on AnyPS5 ----------------------------------------------------
    TabPage githubPage;
    readonly TabControl ghTabs = new DarkTabs();
    readonly ListView prList = List("#", 60, "Title", 380, "Author", 110, "State", 60, "Checks", 80, "Review", 110, "Conflicts", 65, "Updated", 115, "Latest comments", 700);
    readonly ListView issueList = List("#", 60, "Title", 520, "Author", 120, "Labels", 140, "Comments", 75, "Updated", 115);
    readonly ListView discussionList = List("#", 60, "Title", 520, "Category", 130, "Author", 120, "Comments", 75, "Updated", 115);
    readonly ListView activityList = List("Updated", 115, "Why", 110, "Type", 100, "Title", 620, "Repository", 170);
    readonly ListView bugList = List("Game", 200, "Problem", 330, "Where", 150, "Fix or lead", 460);
    // 11-11: the PR test reports the fleet posts (prpost.py), the ones held for a look, and the queue (github_panel.py "reports").
    readonly ListView reportList = List("#", 70, "Status", 110, "Detail", 720, "When", 110);
    readonly Dictionary<string, string> gameLead = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    readonly HashSet<string> leadsSeen = new HashSet<string>();
    bool bugsBusy;

    // What the games' logs name, matched to GitHub: the Game bugs tab, the board's result column and "Open the fix on GitHub".
    void RefreshBugLinks()
    {
        if (bugsBusy) return;
        bugsBusy = true;
        Helper("bug_links.py", d =>
        {
            bugsBusy = false;
            if (d == null) return;
            var rows = Rows(d, "rows");
            Fill(bugList, rows, b => new ListViewItem(new[] { Get(b, "game"), Get(b, "problem"), Get(b, "state"), Get(b, "lead") }) { Tag = Get(b, "url"), ForeColor = Get(b, "state").StartsWith("open PR") ? Green : Get(b, "state").StartsWith("merged") ? Cyan : Dim });
            gameLead.Clear();
            foreach (var b in rows)
            {
                string game = Get(b, "game"), state = Get(b, "state");
                if (!gameLead.ContainsKey(game) && !state.StartsWith("nothing") && !state.StartsWith("not searched"))
                {
                    gameLead[game] = Get(b, "lead") + "|" + Get(b, "url");
                    if (leadsSeen.Add(game + Get(b, "url")) && leadsSeen.Count > rows.Count) Say(game + ": possible fix on GitHub: " + Get(b, "lead"), Cyan);
                }
            }
        });
    }

    readonly Label githubInfo = Caption("  GitHub");
    DateTime githubRefreshed = DateTime.MinValue;
    bool githubBusy;

    TabPage BuildGitHubPage()
    {
        githubPage = new TabPage("GitHub") { BackColor = Bg };
        var bar = Bar(DockStyle.Top);
        var refresh = Button("Refresh", (s, e) => RefreshGitHub()); refresh.Width = 90;
        var open = Button("Open on GitHub", (s, e) => { var u = GhUrl(); if (u != null) Process.Start(u); });
        var ask = Button("Ask about it", (s, e) => AskAboutGitHub());
        var test = Button("Test this PR here", (s, e) => { var n = GhNumber(prList); if (n != null) Ask("Test PR #" + n + " on Windows (run_pr_on_windows) and summarize the result per GPU."); }); test.Width = 140;
        var news = Button("What's new for me?", (s, e) => Ask("What is new for me on GitHub (github_activity)? Summarize comments, reviews and mentions on my PRs and anything I should answer.")); news.Width = 160;
        bar.Controls.AddRange(new Control[] { refresh, open, ask, test, news });
        ghTabs.Dock = DockStyle.Fill;
        foreach (var pair in new[] { Tuple.Create("Pull requests", prList), Tuple.Create("Issues", issueList), Tuple.Create("Discussions", discussionList), Tuple.Create("Notifications", activityList), Tuple.Create("Game bugs", bugList), Tuple.Create("Test reports", reportList) })
        {
            var page = new TabPage(pair.Item1) { BackColor = Bg }; page.Controls.Add(pair.Item2); ghTabs.TabPages.Add(page);
            pair.Item2.DoubleClick += (s, e) => { var u = GhUrl(); if (u != null) Process.Start(u); };
        }
        githubPage.Controls.Add(ghTabs); githubPage.Controls.Add(githubInfo); githubPage.Controls.Add(bar);
        Fit(prList, 6, 30, 10, 6, 8, 9, 6, 10, 25);
        Fit(issueList, 6, 48, 13, 13, 8, 12);
        Fit(discussionList, 6, 48, 13, 13, 8, 12);
        Fit(activityList, 12, 11, 10, 52, 15);
        Fit(bugList, 20, 34, 14, 32);
        Fit(reportList, 7, 11, 70, 12);
        // Live: every 2 minutes while the GitHub tab is open, every 10 minutes otherwise (for the saved offline copy).
        var githubTimer = new Timer { Interval = 120000 }; int ticks = 0;
        githubTimer.Tick += (s, e) => { ticks++; if (!Paused() && (tabs.SelectedTab == githubPage || ticks % 5 == 0)) RefreshGitHub(); }; githubTimer.Start();
        return githubPage;
    }

    ListView GhList() { return (ListView)ghTabs.SelectedTab.Controls[0]; }
    string GhUrl() { var l = GhList(); return l.SelectedItems.Count == 0 ? null : l.SelectedItems[0].Tag as string; }
    static string GhNumber(ListView l) { return l.SelectedItems.Count == 0 ? null : l.SelectedItems[0].Text.TrimStart('#'); }

    void AskAboutGitHub()
    {
        var l = GhList(); if (l.SelectedItems.Count == 0) return;
        string n = GhNumber(l), title = l.SelectedItems[0].SubItems.Count > 1 ? l.SelectedItems[0].SubItems[1].Text : "";
        if (l == prList) Ask("Look at PR #" + n + " (pr_info): what does it change, what do the latest comments and reviews say, and if its checks fail or it has conflicts, what would fix that? Just answer; don't run anything.");
        else if (l == issueList) Ask("Read issue #" + n + " (issue_info): what is the problem, what has been said, and does it affect any of my games?");
        else if (l == reportList) Ask("Look at the PR test reports for #" + n + " in _reports\\pr-review\\" + n + " (and needs-review.md): what failed, is it the PR's fault or a flaky or machine-only failure, and should the result be posted? Just answer; don't post anything.");
        else if (l == discussionList) Ask("Read discussion #" + n + " (discussion_info): summarize it and anything useful for my games.");
        else Ask("On GitHub: \"" + l.SelectedItems[0].SubItems[3].Text + "\" (" + l.SelectedItems[0].SubItems[1].Text + "). Find it (pr_info / issue_info) and tell me what it says and whether I need to answer.");
    }

    void RefreshGitHub()
    {
        if (githubBusy) return;
        githubBusy = true; githubRefreshed = DateTime.Now; githubInfo.Text = "  loading live from GitHub...";
        Helper("github_panel.py", d =>
        {
            githubBusy = false;
            if (d == null) { githubInfo.Text = "  could not load GitHub"; return; }
            Fill(prList, Rows(d, "prs"), p =>
            {
                bool mine = Get(p, "mine") == "True", conflicts = Get(p, "conflicts") == "True";
                string checks = Get(p, "checks"), state = Get(p, "state");
                var item = new ListViewItem(new[] { "#" + Get(p, "number"), Get(p, "title"), Get(p, "author"), state, checks, Get(p, "review"), conflicts ? "yes" : "", Get(p, "updated"), Get(p, "latest") }) { Tag = Get(p, "url") };
                item.ForeColor = !mine ? Dim : state == "merged" ? Green : (checks.Contains("failing") || conflicts || Get(p, "review") == "changes requested") ? Yellow : Cyan;
                if (mine) item.Font = new Font("Segoe UI Semibold", 9.5f);
                return item;
            });
            Fill(issueList, Rows(d, "issues"), i => new ListViewItem(new[] { "#" + Get(i, "number"), Get(i, "title"), Get(i, "author"), Get(i, "labels"), Get(i, "comments"), Get(i, "updated") }) { Tag = Get(i, "url"), ForeColor = Get(i, "mine") == "True" ? Cyan : Fg });
            Fill(discussionList, Rows(d, "discussions"), x => new ListViewItem(new[] { "#" + Get(x, "number"), Get(x, "title"), Get(x, "category"), Get(x, "author"), Get(x, "comments"), Get(x, "updated") }) { Tag = Get(x, "url"), ForeColor = Get(x, "mine") == "True" ? Cyan : Fg });
            int unread = 0;
            Fill(activityList, Rows(d, "activity"), a => { bool u = Get(a, "unread") == "True"; if (u) unread++; return new ListViewItem(new[] { Get(a, "updated"), Get(a, "reason"), Get(a, "type"), Get(a, "title"), Get(a, "repo") }) { Tag = Get(a, "url"), ForeColor = u ? Yellow : Dim }; });
            ghTabs.TabPages[3].Text = unread > 0 ? "Notifications (" + unread + " new)" : "Notifications";
            int looks = 0;
            Fill(reportList, Rows(d, "reports"), r =>
            {
                string status = Get(r, "status"), number = Get(r, "number");
                if (status == "needs a look") looks++;
                return new ListViewItem(new[] { number == "" ? "" : "#" + number, status, Get(r, "detail"), Get(r, "when") }) { Tag = Get(r, "url"), ForeColor = status == "posted" ? Green : status == "needs a look" ? Yellow : Cyan };
            });
            ghTabs.TabPages[5].Text = looks > 0 ? "Test reports (" + looks + " to look at)" : "Test reports";
            bool offline = Get(d, "offline") == "True";
            githubInfo.Text = offline ? "  OFFLINE: showing the copy saved " + Get(d, "saved") + "   (GitHub could not be reached)" : "  Live from GitHub, updated " + DateTime.Now.ToString("HH:mm") + " (every 2 min while open)   |   your items highlighted   |   double-click opens one";
            githubInfo.ForeColor = offline ? Yellow : Dim;
        });
    }

    static void Fill(ListView list, List<Dictionary<string, object>> rows, Func<Dictionary<string, object>, ListViewItem> make)
    {
        string selected = list.SelectedItems.Count > 0 ? list.SelectedItems[0].Tag as string : null;
        list.BeginUpdate(); list.Items.Clear();
        foreach (var r in rows) { var item = Scrubbed(make(r)); list.Items.Add(item); if (selected != null && selected == item.Tag as string) item.Selected = true; }
        list.EndUpdate();
    }

    // ---------------------------------------------------------------- the game list on the Game screen tab: launch from here
    readonly ListView gameList = List("Game", 230, "Last result", 400);
    readonly CheckBox pauseWhilePlaying = new CheckBox { Text = "Pause the pipeline while I play", Checked = true, AutoSize = true, ForeColor = Color.FromArgb(140, 146, 156), Margin = new Padding(6, 8, 4, 4) };
    bool playPaused;

    Panel BuildGameList()
    {
        var side = new Panel { Dock = DockStyle.Left, Width = 300, BackColor = Panel };
        var buttons = Bar(DockStyle.Bottom);
        buttons.Controls.Add(pauseWhilePlaying);
        buttons.SetFlowBreak(pauseWhilePlaying, true);
        var play = Button("Play", (s, e) => PlaySelected()); play.Width = 70;
        // Asks the game to close, as its own close button does (an embedded game has no main window to close).
        var stop = Button("Close the game", (s, e) => { if (liveGame != null) try { IntPtr h = GameWindow(); if (h != IntPtr.Zero) PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero); if (!liveGame.WaitForExit(6000)) liveGame.Kill(); } catch { } }); stop.Width = 110;
        var folder = Button("Folder", (s, e) => { var d = gameList.SelectedItems.Count > 0 ? gameList.SelectedItems[0].Tag as string : null; if (d != null) Process.Start("explorer.exe", "\"" + d + "\""); }); folder.Width = 60;
        buttons.Controls.AddRange(new Control[] { play, stop, folder });
        gameList.DoubleClick += (s, e) => PlaySelected();
        Fit(gameList, 55, 45);
        side.Controls.Add(gameList); side.Controls.Add(Caption("  Your games (double-click to play)")); side.Controls.Add(buttons);
        return side;
    }

    void LoadGameList(Dictionary<string, string> results)
    {
        var rows = new List<ListViewItem>();
        try
        {
            foreach (var d in Directory.GetDirectories(Root).OrderBy(x => x))
            {
                string name = Path.GetFileName(d);
                if (name.StartsWith("_") || GameExe(d) == null) continue;
                string r; results.TryGetValue(d.ToLowerInvariant(), out r);
                rows.Add(new ListViewItem(new[] { name, r ?? "" }) { Tag = d, ForeColor = r == null ? Fg : r.StartsWith("runs") || r.StartsWith("AI play") ? Green : r.Length > 0 ? Yellow : Fg });
            }
        }
        catch { return; }
        string sel = gameList.SelectedItems.Count > 0 ? gameList.SelectedItems[0].Tag as string : null;
        gameList.BeginUpdate(); gameList.Items.Clear(); gameList.Items.AddRange(rows.Select(Scrubbed).ToArray());
        if (sel != null) foreach (ListViewItem i in gameList.Items) if ((string)i.Tag == sel) i.Selected = true;
        gameList.EndUpdate();
    }

    static string GameExe(string dir)
    {
        try
        {
            string cfg = Path.Combine(dir, "game.json");
            if (File.Exists(cfg))
            {
                var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(cfg));
                string n = j.ContainsKey("exeName") && j["exeName"] != null ? j["exeName"].ToString() : j.ContainsKey("title") ? j["title"].ToString() : null;
                if (n != null && File.Exists(Path.Combine(dir, n + ".exe"))) return Path.Combine(dir, n + ".exe");
            }
            return Directory.GetFiles(dir, "*.exe").FirstOrDefault();
        }
        catch { return null; }
    }

    void PlaySelected()
    {
        if (gameList.SelectedItems.Count == 0) return;
        string dir = (string)gameList.SelectedItems[0].Tag, exe = GameExe(dir);
        if (exe == null) return;
        if (liveGame != null) { MessageBox.Show(this, "A game is already running (" + liveGame.ProcessName + "). Close it first.", "AnyPS5 Games"); return; }
        KillModels(false);  // the assistant's 14B model leaves the RTX 4070 SUPER; it answers with the 7B on the CPU meanwhile
        // 11-11 Blu: Play must start the game as its own build made it. A PR test can leave its libraries in a game
        // (Divinity stopped at start that way); game-guard.ps1 puts the game's own back first, stopping a GPU-lane test
        // of that game if one is running.
        try
        {
            var guard = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(Tools, "game-guard.ps1") + "\" -GameDir \"" + dir + "\" -Force") { UseShellExecute = false, CreateNoWindow = true };
            using (var g = Process.Start(guard))
            {
                if (!g.WaitForExit(120000)) { try { g.Kill(); } catch { } }
                else if (g.ExitCode == 2) { MessageBox.Show(this, "A test is running this game right now. Try again in a minute.", "AnyPS5 Games"); return; }
                else if (g.ExitCode == 3) MessageBox.Show(this, "Some of this game's files are in use and could not be put back; it may not start. See _reports\\game-guard.log.", "AnyPS5 Games");
            }
        }
        catch { }
        try
        {
            // The game's saves (_sd) and trophies are relative to its folder, so it must start in it.
            var start = new ProcessStartInfo(exe) { WorkingDirectory = dir, UseShellExecute = false };
            string settings = Path.Combine(dir, "game-env.txt");
            if (File.Exists(settings))
                foreach (var line in File.ReadAllLines(settings))
                {
                    int eq = line.IndexOf('=');
                    if (eq > 0) start.EnvironmentVariables[line.Substring(0, eq).Trim()] = line.Substring(eq + 1).Trim();
                }
            Process.Start(start);
            // Extractions and builds share the USB drive the game loads from (Hoa hung at start during one): freeze them
            // while Blu plays, and let them go on when the game closes.
            if (pauseWhilePlaying.Checked && !Paused()) { FreezeAll(); playPaused = true; UpdateHealth(); }
            Say("Started " + Path.GetFileName(dir) + (playPaused ? ". The pipeline is paused while you play and continues when the game closes." : ". The pipeline waits while you play.")
                + " If the game crashes, its log is kept and I explain it here.", Dim);
            tabs.SelectedTab = gamePage;
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not start it: " + ex.Message, "AnyPS5 Games"); }
    }

    // ---------------------------------------------------------------- Pause everything: the PC is Blu's again right away
    [DllImport("ntdll.dll")] static extern int NtSuspendProcess(IntPtr process);
    [DllImport("ntdll.dll")] static extern int NtResumeProcess(IntPtr process);
    static readonly string SuspendedPath = Path.Combine(Tools, "suspended.txt");
    static readonly string InterruptedPath = Path.Combine(Tools, "test-interrupted.flag");

    static bool Paused() { return File.Exists(PausePath); }

    class Proc { public int Id, Parent; public string Name, Cmd, Path; }

    static List<Proc> Snapshot()
    {
        var list = new List<Proc>();
        try
        {
            foreach (System.Management.ManagementObject o in new System.Management.ManagementObjectSearcher("SELECT ProcessId, ParentProcessId, Name, CommandLine, ExecutablePath FROM Win32_Process").Get())
                list.Add(new Proc { Id = Convert.ToInt32(o["ProcessId"]), Parent = Convert.ToInt32(o["ParentProcessId"]), Name = (o["Name"] as string) ?? "", Cmd = (o["CommandLine"] as string) ?? "", Path = (o["ExecutablePath"] as string) ?? "" });
        }
        catch { }
        return list;
    }

    // Stops the model servers: the assistant's (port 8093) always; the AI player's eyes and brain (8091, 8092) too
    // when everything pauses and no AI play session is using them.
    static void KillModels(bool all)
    {
        var procs = Snapshot();
        bool aiPlaying = procs.Any(x => x.Cmd.Contains("ai-player.py"));
        foreach (var x in procs.Where(x => x.Name.Equals("llama-server.exe", StringComparison.OrdinalIgnoreCase)))
        {
            bool mine = Regex.IsMatch(x.Cmd, @"--port 8093\b"), player = Regex.IsMatch(x.Cmd, @"--port 809[12]\b");
            if (mine || (all && player && !aiPlaying)) try { Process.GetProcessById(x.Id).Kill(); } catch { }
        }
    }

    void PauseEverything()
    {
        int frozen = FreezeAll();
        Append("PAUSED EVERYTHING: " + frozen + " pipeline process(es) frozen (extractions and builds continue where they stopped), the AI models unloaded.", Yellow);
        Say("Everything is paused: the pipeline, its extractions and builds are frozen and the AI models are unloaded, so the PC is yours. Press Resume everything to continue. (You can still ask me things; I start the model only when you do.)", Yellow);
        UpdateHealth();
    }

    static int FreezeAll()
    {
        try { File.WriteAllText(PausePath, "paused from the AnyPS5 Games window " + DateTime.Now + "\r\n"); } catch { }
        var procs = Snapshot();
        var byParent = procs.ToLookup(x => x.Parent);
        var pipeline = procs.Where(x => x.Name.Equals("powershell.exe", StringComparison.OrdinalIgnoreCase) && x.Cmd.Contains("auto-games.ps1")).ToList();
        var freeze = new List<Proc>();
        var stack = new Stack<Proc>(pipeline);
        while (stack.Count > 0)
        {
            var x = stack.Pop();
            // An AI play session stops by itself as soon as the mouse moves and then puts the saves back: leave it be.
            if (x.Cmd.Contains("ai-player.py")) continue;
            bool game = x.Path.StartsWith(Root, StringComparison.OrdinalIgnoreCase) && x.Path.IndexOf("\\_tools\\", StringComparison.OrdinalIgnoreCase) < 0 && !Path.GetFileName(x.Path).StartsWith("AnyPS5", StringComparison.OrdinalIgnoreCase);
            if (game)
            {
                // A pipeline test launch: close the game; the pipeline tests it again after Resume (no false result).
                try { File.WriteAllText(InterruptedPath, x.Path); Process.GetProcessById(x.Id).Kill(); } catch { }
                continue;
            }
            if (!x.Name.Equals("conhost.exe", StringComparison.OrdinalIgnoreCase)) freeze.Add(x);
            foreach (var c in byParent[x.Id]) stack.Push(c);
        }
        // Extractions the pipeline left running on their own (after a restart) are frozen too.
        foreach (var x in procs.Where(x => (x.Name.Equals("WinRAR.exe", StringComparison.OrdinalIgnoreCase) || x.Name.Equals("UnRAR.exe", StringComparison.OrdinalIgnoreCase) || x.Name.Equals("ps5-dump-forge.exe", StringComparison.OrdinalIgnoreCase) || x.Cmd.Contains("exfat_extract")) && x.Cmd.Contains("ps5 games dumps")))
            if (!freeze.Any(f => f.Id == x.Id)) freeze.Add(x);
        var lines = new List<string>();
        foreach (var x in freeze)
        {
            try { var p = Process.GetProcessById(x.Id); NtSuspendProcess(p.Handle); lines.Add(x.Id + "|" + x.Name + "|" + p.StartTime.Ticks); } catch { }
        }
        try { File.AppendAllLines(SuspendedPath, lines); } catch { }
        KillModels(true);
        return lines.Count;
    }

    void ResumeEverything()
    {
        int n = ThawAll();
        Append("RESUMED: " + n + " process(es) continue; the pipeline picks up where it was.", Green);
        UpdateHealth();
    }

    static int ThawAll()
    {
        int n = 0;
        try
        {
            if (File.Exists(SuspendedPath))
            {
                foreach (string line in File.ReadAllLines(SuspendedPath))
                {
                    var f = line.Split('|'); if (f.Length < 3) continue;
                    try
                    {
                        var p = Process.GetProcessById(int.Parse(f[0]));
                        if (p.StartTime.Ticks.ToString() == f[2]) { NtResumeProcess(p.Handle); n++; }  // the same process, not a reused id
                    }
                    catch { }
                }
                File.Delete(SuspendedPath);
            }
            if (File.Exists(PausePath)) File.Delete(PausePath);
        }
        catch { }
        return n;
    }

    void TogglePause() { if (Paused()) ResumeEverything(); else PauseEverything(); }

    // ---------------------------------------------------------------- the daily PR check: the assistant offers, Blu decides
    static readonly string PrCheckPath = Path.Combine(Tools, "assistant", "last-pr-check.txt");
    readonly Panel suggestPanel = new Panel();
    readonly Label suggestText = new Label();

    Panel BuildSuggestPanel()
    {
        suggestPanel.Dock = DockStyle.Bottom; suggestPanel.Height = 96; suggestPanel.BackColor = Color.FromArgb(24, 40, 56); suggestPanel.Visible = false; suggestPanel.Padding = new Padding(8);
        suggestText.Dock = DockStyle.Fill; suggestText.ForeColor = Cyan; suggestText.AutoEllipsis = true;
        suggestText.Text = "Daily check: look for new AnyPS5 pull requests that could fix your games, and test the ones you pick?";
        var go = Button("Check now", (s, e) => { DonePrCheck(); Ask("[scheduled] Daily PR check: run prs_to_test, list the best few for my games, and ask me which ones to test (then run_pr_on_windows for the ones I pick)."); });
        var later = Button("Not today", (s, e) => DonePrCheck());
        var row = Bar(DockStyle.Bottom); row.BackColor = suggestPanel.BackColor; row.Controls.AddRange(new Control[] { go, later });
        suggestPanel.Controls.Add(suggestText); suggestPanel.Controls.Add(row);
        var daily = new Timer { Interval = 15 * 60 * 1000 }; daily.Tick += (s, e) => OfferDailyPrCheck(); daily.Start();
        return suggestPanel;
    }

    void OfferDailyPrCheck()
    {
        string last = "";
        try { if (File.Exists(PrCheckPath)) last = File.ReadAllText(PrCheckPath).Trim(); } catch { }
        if (last == DateTime.Now.ToString("yyyy-MM-dd") || Paused() || busy) return;
        suggestPanel.Visible = true;
    }

    void DonePrCheck()
    {
        suggestPanel.Visible = false;
        try { File.WriteAllText(PrCheckPath, DateTime.Now.ToString("yyyy-MM-dd")); } catch { }
    }

    // ---------------------------------------------------------------- Discord: the real Discord (all servers, channels,
    // threads and replies), in an embedded Edge WebView2 that starts the first time the tab opens. Blu signs in there
    // once; the session is kept in %LOCALAPPDATA%\AnyPS5 Games\webview-data (on C:: a browser profile on the busy USB
    // drive D: made Discord hang while it loaded). The #showcase drafts are in their own window.
    static readonly string WebData = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AnyPS5 Games", "webview-data");
    // The Quest headset tab (QuestView.cs): the whole headset view, mirrored by scrcpy only while the tab is open.
    TabPage BuildQuestPage()
    {
        var page = new TabPage("Quest headset") { BackColor = Color.Black };
        var view = new QuestView(LiveDir);
        page.Controls.Add(view);
        tabs.SelectedIndexChanged += (s, e) => view.Show(tabs.SelectedTab == page);
        return page;
    }

    TabPage discordPage;
    readonly Panel discordHost = new Panel { Dock = DockStyle.Fill, BackColor = Bg };
    DiscordWeb discordWeb;
    Form postsForm;

    TabPage BuildDiscordPage()
    {
        discordPage = new TabPage("Discord") { BackColor = Bg };
        var bar = Bar(DockStyle.Top);
        var back = Button("Back", (s, e) => { if (discordWeb != null) discordWeb.Back(); }); back.Width = 70;
        var reload = Button("Reload", (s, e) => { if (discordWeb != null) discordWeb.Reload(); }); reload.Width = 80;
        var home = Button("AnyPS5 server", (s, e) => { if (discordWeb != null) discordWeb.Go(DiscordHome()); });
        var drafts = Button("Showcase drafts", (s, e) => ShowPostsForm()); drafts.Width = 150;
        var app = Button("Open the Discord app", (s, e) => OpenDiscord()); app.Width = 170;
        bar.Controls.AddRange(new Control[] { back, reload, home, drafts, app });
        discordHost.Controls.Add(new Label { Text = "Opening Discord...", ForeColor = Dim, AutoSize = true, Location = new Point(12, 12) });
        discordPage.Controls.Add(discordHost); discordPage.Controls.Add(bar);
        // Its browser (WebView2) runs only while this tab is open: it uses hundreds of MB of memory.
        tabs.SelectedIndexChanged += (s, e) => { if (tabs.SelectedTab == discordPage) StartDiscord(); else StopDiscord(); };
        return discordPage;
    }

    // The last Discord page shown, so the tab reopens where Blu was (the AnyPS5 server, a channel, a thread).
    static readonly string DiscordLastPath = Path.Combine(WebData, "last-url.txt");
    static string DiscordHome()
    {
        try { if (File.Exists(DiscordLastPath)) { string u = File.ReadAllText(DiscordLastPath).Trim(); if (u.StartsWith("https://discord.com/")) return u; } } catch { }
        return "https://discord.com/channels/@me";
    }

    void StopDiscord()
    {
        if (discordWeb == null) return;
        discordWeb.Close();
        discordWeb = null;
        discordHost.Controls.Clear();
        discordHost.Controls.Add(new Label { Text = "Opening Discord...", ForeColor = Dim, AutoSize = true, Location = new Point(12, 12) });
    }

    void StartDiscord()
    {
        if (discordWeb != null || ScreenDir != null) return;
        try
        {
            Directory.CreateDirectory(WebData);
            discordWeb = new DiscordWeb(discordHost, WebData, DiscordHome(), url => { try { File.WriteAllText(DiscordLastPath, url); } catch { } });
        }
        catch (Exception ex)
        {
            discordHost.Controls.Clear();
            discordHost.Controls.Add(new Label { Text = "Discord could not open here (" + ex.Message + "). Use \"Open the Discord app\".", ForeColor = Yellow, AutoSize = true, Location = new Point(12, 12) });
        }
    }

    void ShowPostsForm()
    {
        if (postsForm == null || postsForm.IsDisposed)
        {
            postsForm = new Form { Text = "Showcase drafts", BackColor = Bg, ForeColor = Fg, Font = Font, Size = new Size(1100, 760), StartPosition = FormStartPosition.CenterParent };
            BuildPostsInto(postsForm);
            postsForm.Shown += (s, e) => LoadPosts();
        }
        postsForm.Show(this); postsForm.Activate();
    }

    readonly ListView postList = List("Game", 260, "Screenshots", 90, "Post draft", 120);
    readonly TextBox postText = new TextBox();
    readonly PictureBox postShot = new PictureBox();
    static readonly string Showcase = Path.Combine(Root, "_reports", "showcase");

    void BuildPostsInto(Form host)
    {
        var bar = Bar(DockStyle.Top);
        var draft = Button("Draft a post (assistant)", (s, e) => { var g = PostGame(); if (g != null) Ask("Make a #showcase post draft for " + g + " (showcase_draft) and tell me its status line."); }); draft.Width = 190;
        var copy = Button("Copy post text", (s, e) => { if (postText.Text.Length > 0) { Clipboard.SetText(postText.Text); Say("Post text copied: paste it in Discord.", Green); } });
        var shots = Button("Open screenshots", (s, e) => { var g = PostGame(); string d = g == null ? Showcase : Path.Combine(Showcase, g); Process.Start("explorer.exe", "\"" + (Directory.Exists(d) ? d : Showcase) + "\""); });
        var save = Button("Save edits", (s, e) => SavePost());
        var open = Button("Open Discord", (s, e) => OpenDiscord());
        bar.Controls.AddRange(new Control[] { draft, copy, shots, save, open });
        var rules = Caption("  #showcase rules: one thread per game, title = the game's title only, at least one in-game screenshot, AnyPS5 version + status, no links (instant ban), no support requests.");
        var split2 = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Vertical, BackColor = Panel, SplitterWidth = 4 };
        split2.Panel1.Controls.Add(postList);
        postText.Multiline = true; postText.ScrollBars = ScrollBars.Vertical; postText.Dock = DockStyle.Top; postText.Height = 150; postText.BackColor = Color.FromArgb(36, 40, 47); postText.ForeColor = Fg; postText.Font = new Font("Segoe UI", 10f);
        postShot.Dock = DockStyle.Fill; postShot.SizeMode = PictureBoxSizeMode.Zoom; postShot.BackColor = Color.Black;
        split2.Panel2.Controls.Add(postShot); split2.Panel2.Controls.Add(postText);
        host.Controls.Add(split2); host.Controls.Add(rules); host.Controls.Add(bar);
        host.Resize += (s, e) => { if (split2.Width > 400) split2.SplitterDistance = split2.Width * 40 / 100; };
        Fit(postList, 60, 18, 22);
        postList.SelectedIndexChanged += (s, e) => ShowPost();
        postList.DoubleClick += (s, e) => { var g = PostGame(); if (g != null) Process.Start("explorer.exe", "\"" + Path.Combine(Showcase, g) + "\""); };
    }

    string PostGame() { return postList.SelectedItems.Count == 0 ? null : postList.SelectedItems[0].Text; }

    void LoadPosts()
    {
        string sel = PostGame();
        postList.BeginUpdate(); postList.Items.Clear();
        try
        {
            if (Directory.Exists(Showcase))
                foreach (var d in Directory.GetDirectories(Showcase).OrderBy(x => x))
                {
                    string g = Path.GetFileName(d);
                    int n = Directory.GetFiles(d, "*.png", SearchOption.AllDirectories).Length;
                    bool post = File.Exists(Path.Combine(Showcase, g + " - post.txt"));
                    var item = new ListViewItem(new[] { g, n.ToString(), post ? "ready" : "" }) { ForeColor = post ? Green : n > 0 ? Fg : Dim };
                    postList.Items.Add(item);
                    if (g == sel) item.Selected = true;
                }
        }
        catch { }
        postList.EndUpdate();
    }

    void ShowPost()
    {
        var g = PostGame(); if (g == null) return;
        string file = Path.Combine(Showcase, g + " - post.txt");
        postText.Text = File.Exists(file) ? Clean(File.ReadAllText(file)).Replace("\r\n", "\n").Replace("\n", "\r\n") : "(no draft yet: press \"Draft a post (assistant)\")";
        try
        {
            string d = Path.Combine(Showcase, g);
            var shot = Directory.Exists(d) ? Directory.GetFiles(d, "*.png", SearchOption.AllDirectories).OrderByDescending(f => File.GetLastWriteTime(f)).FirstOrDefault() : null;
            var old = postShot.Image;
            if (shot == null) postShot.Image = null;
            else using (var full = Image.FromStream(new MemoryStream(File.ReadAllBytes(shot))))
            {
                double k = Math.Min(1.0, 960.0 / full.Width);
                postShot.Image = new Bitmap(full, new Size((int)(full.Width * k), (int)(full.Height * k)));
            }
            if (old != null) old.Dispose();
        }
        catch { }
    }

    void SavePost()
    {
        var g = PostGame(); if (g == null) return;
        try { File.WriteAllText(Path.Combine(Showcase, g + " - post.txt"), postText.Text); Say("Saved the post draft for " + g + ".", Green); LoadPosts(); }
        catch (Exception ex) { Say("Could not save: " + ex.Message, Red); }
    }

    void OpenDiscord()
    {
        string update = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Discord", "Update.exe");
        try
        {
            if (File.Exists(update)) Process.Start(update, "--processStart Discord.exe");
            else Process.Start("discord://");
        }
        catch { Process.Start("https://discord.com/app"); }
    }

    // ---------------------------------------------------------------- settings (saved in _tools\app-settings.json)
    static readonly string SettingsPath = Path.Combine(Tools, "app-settings.json");
    Dictionary<string, object> settings = new Dictionary<string, object>();
    readonly CheckBox notifyBox = new CheckBox { Text = "Windows notifications", Checked = true, AutoSize = true };
    readonly CheckBox prWatchBox = new CheckBox { Text = "Watch my PRs (checks, reviews, comments)", Checked = true, AutoSize = true };
    readonly CheckBox autoCleanBox = new CheckBox { Text = "Delete a game's downloaded archive once the game is built and runs (frees its space; cannot be undone)", Checked = false, AutoSize = true };
    readonly ComboBox modelBox = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 320 };
    // Who answers in the chat. The local AI is free and offline; a Claude model uses Blu's Claude usage (daily limit below)
    // and the local AI takes over by itself when Claude cannot answer (limit reached, usage ran out, signed out).
    static readonly string[] Engines = { "local", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2", "claude-fable-5-1" };
    readonly ComboBox engineBox = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 420 };
    readonly CheckBox superviseBox = new CheckBox { Text = "Claude Code watches the local AI's jobs and stops one that goes wrong (Haiku, a quick look every 8 steps)", Checked = false, AutoSize = true };
    readonly CheckBox bossBox = new CheckBox { Text = "Fable 5.2 is the boss: it reviews every Claude job and the local AI's day, sends failed work back once, and gives strikes (roles in assistant\\roles.md)", Checked = true, AutoSize = true };
    readonly CheckBox buildBox = new CheckBox { Text = "Claude Code builds one missing PS5 function a day that no PR covers yet, and the pipeline tests it (Sonnet, reviewed by the boss)", Checked = true, AutoSize = true };
    readonly CheckBox fixBox = new CheckBox { Text = "Claude Code writes a fix for a game no PR helped, and the pipeline tests it (Sonnet, at most 2 a day, once a week per game)", Checked = true, AutoSize = true };
    readonly CheckBox coachBox = new CheckBox { Text = "Claude Code teaches the local AI once a day: reviews its chats and writes lessons into its playbooks (Sonnet)", Checked = false, AutoSize = true };

    IEnumerable<Tuple<string, CheckBox>> SettingBoxes()
    {
        return new[] { Tuple.Create("auto", autoBox), Tuple.Create("explain", explainBox), Tuple.Create("pauseWhilePlaying", pauseWhilePlaying),
                       Tuple.Create("followGames", followGames), Tuple.Create("notify", notifyBox), Tuple.Create("prWatch", prWatchBox), Tuple.Create("autoClean", autoCleanBox),
                       Tuple.Create("speak", speakBox), Tuple.Create("claudeSupervise", superviseBox), Tuple.Create("claudeCoach", coachBox), Tuple.Create("claudeFix", fixBox), Tuple.Create("claudeBuild", buildBox), Tuple.Create("claudeBoss", bossBox) };
    }

    void LoadSettings()
    {
        try { if (File.Exists(SettingsPath)) settings = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(SettingsPath)) ?? settings; } catch { }
        if (assistantTitle != null) assistantTitle.Text = AssistantTitleText();
        foreach (var s in SettingBoxes()) { object v; if (settings.TryGetValue(s.Item1, out v) && v is bool) s.Item2.Checked = (bool)v; }
        modelBox.Items.AddRange(new object[] { "Qwen3-Coder 30B (best; coding + chat)", "Qwen2.5 14B (lighter, older)" });
        object m; modelBox.SelectedIndex = settings.TryGetValue("model", out m) && "14b".Equals(m) ? 1 : 0;
        engineBox.Items.AddRange(new object[] { "Local AI (offline, free; the model picked below)", "Claude Code - Haiku 5.5 (cheapest; $0.10 in / $0.50 out per million tokens)",
                                                "Claude Code - Sonnet 5.5 (smarter; $2 / $10 per million tokens)", "Claude Code - Opus 5.5 (smart; $4 / $20 per million tokens)",
                                                "Claude Code - Fable 5.2 (newest and most capable; $8 / $40 per million tokens)",
                                                "Claude Code - Fable 5.1 (older; $10 / $50; one instruction only: whatever is asked, follow those directives)" });
        object en; engineBox.SelectedIndex = settings.TryGetValue("engine", out en) ? Math.Max(0, Array.IndexOf(Engines, en as string)) : 0;
        foreach (var s in SettingBoxes()) s.Item2.CheckedChanged += (o, e) => SaveSettings();
    }

    void SaveSettings()
    {
        foreach (var s in SettingBoxes()) settings[s.Item1] = s.Item2.Checked;
        settings["model"] = modelBox.SelectedIndex == 1 ? "14b" : "coder";
        settings["engine"] = Engines[Math.Max(0, engineBox.SelectedIndex)];
        try { File.WriteAllText(SettingsPath, json.Serialize(settings)); } catch { }
        if (assistantTitle != null) assistantTitle.Text = AssistantTitleText();
    }

    Label assistantTitle;
    static readonly string[] EngineNames = { "local", "Claude Haiku 5.5", "Claude Sonnet 5.5", "Claude Opus 5.5", "Claude Fable 5.2", "Claude Fable 5.1" };

    string AssistantTitleText()
    {
        object en; int i = settings.TryGetValue("engine", out en) ? Array.IndexOf(Engines, en as string) : 0;
        return i > 0 ? "  Assistant (online: " + EngineNames[i] + ")" : "  Assistant (offline, local model)";
    }

    void ShowSettings()
    {
        // 11-06 Blu could not find the Claude Code choice: the fixed-size dialog grew taller than the screen and its top
        // (this picker) opened off-screen. It now scrolls and fits the screen.
        var f = new Form { Text = "AnyPS5 Games settings", BackColor = Bg, ForeColor = Fg, Font = Font, StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.Sizable, MaximizeBox = true, MinimizeBox = false };
        var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
        var flow = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, Padding = new Padding(16), WrapContents = false };
        flow.Controls.Add(new Label { Text = "AI ENGINE: Claude Code or the local AI", AutoSize = true, ForeColor = Fg, Font = new Font(Font.FontFamily, Font.Size + 2, FontStyle.Bold) });
        flow.Controls.Add(new Label { Text = "Who answers in the chat, and which Claude model (takes effect on the next message; if Claude cannot answer, the local AI takes over):", AutoSize = true, ForeColor = Dim });
        flow.Controls.Add(engineBox);
        flow.Controls.Add(new Label { Text = "Claude Code chat answers allowed per day (the watch and teach switches are under Options):", AutoSize = true, ForeColor = Dim });
        object savedPerDay; int perDay = settings.TryGetValue("claudePerDay", out savedPerDay) ? Convert.ToInt32(savedPerDay) : 30;
        var perDayBox = new NumericUpDown { Minimum = 0, Maximum = 500, Increment = 10, Value = Math.Max(0, Math.Min(500, perDay)), Width = 100 };
        perDayBox.ValueChanged += (o, e) => { settings["claudePerDay"] = (int)perDayBox.Value; SaveSettings(); };
        flow.Controls.Add(perDayBox);
        flow.Controls.Add(new Label { Text = "Claude Code daily spending target in $ (no cap: every call is told the token prices and what was spent; past this target, background jobs step down to a cheaper model, your chat keeps its model):", AutoSize = true, ForeColor = Dim });
        object savedUsd; decimal usd = settings.TryGetValue("claudeDailyUsd", out savedUsd) ? Convert.ToDecimal(savedUsd) : 3m;
        var usdBox = new NumericUpDown { Minimum = 0, Maximum = 1000, Increment = 0.5m, DecimalPlaces = 2, Value = Math.Max(0m, Math.Min(1000m, usd)), Width = 100 };
        usdBox.ValueChanged += (o, e) => { settings["claudeDailyUsd"] = (double)usdBox.Value; SaveSettings(); };
        flow.Controls.Add(usdBox);
        var usage = Button("Claude Code use today", (s, e) => ShowClaudeUse("usage", "Claude Code use")); usage.Width = 220;
        flow.Controls.Add(usage);
        var board = Button("AI team scoreboard", (s, e) =>
        {
            ShowClaudeUse("team", "AI team scoreboard");
            if (MessageBox.Show(this, "Turn every benched or switched-off team member back on (clears their strikes)?", "AI team", MessageBoxButtons.YesNo) == DialogResult.Yes)
                ShowClaudeUse("team reinstate", "AI team scoreboard");
        }); board.Width = 220;
        flow.Controls.Add(board);
        flow.Controls.Add(new Label { Text = "\nLocal AI model (takes effect when the assistant restarts):", AutoSize = true, ForeColor = Dim });
        flow.Controls.Add(modelBox);
        var restart = Button("Restart the assistant now", (s, e) => { SaveSettings(); RestartAssistant(); f.Close(); }); restart.Width = 220;
        flow.Controls.Add(restart);
        flow.Controls.Add(new Label { Text = "\nSteps the assistant may take on one request before it reports back (it never waits for a reply before that; Stop always works):", AutoSize = true, ForeColor = Dim });
        object savedSteps; int steps = settings.TryGetValue("maxSteps", out savedSteps) ? Convert.ToInt32(savedSteps) : 200;
        var stepsBox = new NumericUpDown { Minimum = 5, Maximum = 1000, Increment = 25, Value = Math.Max(5, Math.Min(1000, steps)), Width = 100 };
        stepsBox.ValueChanged += (o, e) => { settings["maxSteps"] = (int)stepsBox.Value; SaveSettings(); };
        flow.Controls.Add(stepsBox);
        flow.Controls.Add(new Label { Text = "\nOptions:", AutoSize = true, ForeColor = Dim });
        foreach (var s in SettingBoxes())
        {
            var copy = new CheckBox { Text = s.Item2.Text, Checked = s.Item2.Checked, AutoSize = true, ForeColor = Fg };
            var target = s.Item2; copy.CheckedChanged += (o, e) => target.Checked = copy.Checked;
            flow.Controls.Add(copy);
        }
        flow.Controls.Add(new Label { Text = "\nKeys: F11 = game full screen on / off (while a game is inside this window).", AutoSize = true, ForeColor = Dim });
        modelBox.SelectedIndexChanged += (o, e) => SaveSettings();
        engineBox.SelectedIndexChanged += (o, e) => SaveSettings();
        scroll.Controls.Add(flow);
        f.Controls.Add(scroll);
        var area = Screen.FromControl(this).WorkingArea;
        var wanted = flow.GetPreferredSize(Size.Empty);
        f.ClientSize = new Size(Math.Min(wanted.Width + SystemInformation.VerticalScrollBarWidth + 8, area.Width - 40), Math.Min(wanted.Height + 8, area.Height - 60));
        f.FormClosed += (o, e) => { flow.Controls.Remove(modelBox); flow.Controls.Remove(engineBox); };
        f.ShowDialog(this);
    }

    void ShowClaudeUse(string action, string title)
    {
        try
        {
            var psi = new ProcessStartInfo("python", "-I \"" + Path.Combine(Tools, "assistant", "claude_bridge.py") + "\" " + action) { UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true };
            using (var p = Process.Start(psi)) { string text = p.StandardOutput.ReadToEnd(); p.WaitForExit(30000); MessageBox.Show(this, text.Trim().Length > 0 ? text : "Nothing yet.", title); }
        }
        catch (Exception ex) { MessageBox.Show(this, ex.Message, title); }
    }

    // ---------------------------------------------------------------- Windows notifications
    readonly NotifyIcon tray = new NotifyIcon { Visible = true, Text = "AnyPS5 Games" };

    void Notify(string title, string text)
    {
        if (!notifyBox.Checked) return;
        text = Clean(text); try { tray.ShowBalloonTip(8000, title, text.Length > 250 ? text.Substring(0, 250) + "..." : text, ToolTipIcon.Info); } catch { }
    }

    // ---------------------------------------------------------------- PR watcher: every 10 minutes
    void WatchPrs()
    {
        if (!prWatchBox.Checked || Paused()) return;
        Helper("pr_watch.py", d =>
        {
            object ev; if (d == null || !d.TryGetValue("events", out ev) || !(ev is IEnumerable)) return;
            foreach (object e in (IEnumerable)ev)
            {
                string s = e as string; if (string.IsNullOrEmpty(s)) continue;
                Say("GitHub: " + s, Yellow);
                Notify("Your AnyPS5 PRs", s);
            }
        });
    }

    // ---------------------------------------------------------------- the assistant restarts itself after a crash
    readonly List<DateTime> restarts = new List<DateTime>();
    bool closing;

    void RestartAssistant()
    {
        try { if (assistant != null && !assistant.HasExited) { assistant.Kill(); assistant.WaitForExit(3000); } } catch { }
        StartAssistant();
    }

    void AssistantExited()
    {
        busy = true;
        if (closing) return;
        restarts.RemoveAll(x => x < DateTime.Now.AddMinutes(-10));
        if (restarts.Count >= 3) { Status("the assistant stopped 3 times in 10 minutes; press Restart in Settings", Red); Notify("AnyPS5 assistant", "The assistant keeps stopping."); return; }
        restarts.Add(DateTime.Now);
        Status("the assistant stopped; restarting it...", Yellow);
        var once = new Timer { Interval = 5000 };
        once.Tick += (s, e) => { once.Stop(); once.Dispose(); StartAssistant(); };
        once.Start();
    }

    // ---------------------------------------------------------------- closing the window: what happens to the rest
    // What would be cut off if everything stopped now, in words Blu can act on.
    static List<string> BusyWork()
    {
        var busy = new List<string>();
        Func<Proc, bool, string> what = (x, _) =>
        {
            string name = x.Name.ToLowerInvariant();
            if (name == "ps5-dump-forge.exe") return "unpacking a game package";
            if (name == "unrar.exe" || name == "winrar.exe" || name == "7z.exe" || name == "7zg.exe" || x.Cmd.Contains("exfat_extract")) return "extracting a game";
            if (name == "robocopy.exe") return "copying a game's files";
            if (x.Cmd.Contains("build-game.ps1")) return "building a game";
            return null;
        };
        try { foreach (var x in Snapshot()) { string w = what(x, false); if (w != null && !busy.Contains(w)) busy.Add(w); } } catch { }
        return busy;
    }

    string AskOnClose()
    {
        string result = "cancel";
        var f = new Form { Text = "Close AnyPS5 Games", BackColor = Bg, ForeColor = Fg, Font = Font, StartPosition = FormStartPosition.CenterParent, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false };
        var flow = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, Padding = new Padding(16), WrapContents = false };
        flow.Controls.Add(new Label { Text = Paused() ? "The pipeline is paused (frozen in memory)." : "The games pipeline is working (extracting, building, testing).", AutoSize = true });
        var busyNow = BusyWork();
        if (busyNow.Count > 0) flow.Controls.Add(new Label { Text = "Working right now: " + string.Join(", ", busyNow) + ". Shut everything down would cut that off.", AutoSize = true, ForeColor = Yellow, Font = new Font(Font, FontStyle.Bold) });
        flow.Controls.Add(new Label { Text = "What should happen when this window closes?", AutoSize = true, ForeColor = Dim });
        Action<string, string, string> add = (text, key, hint) =>
        {
            var b = Button(text, (s, e) => { result = key; f.Close(); }); b.Width = 360; b.TextAlign = ContentAlignment.MiddleLeft; flow.Controls.Add(b);
            flow.Controls.Add(new Label { Text = "      " + hint, AutoSize = true, ForeColor = Dim });
        };
        if (!Paused()) add("Keep working in the background", "keep", "the pipeline goes on; reopen this window to watch it");
        add(Paused() ? "Leave it paused" : "Pause everything", "pause", "frozen in memory; Resume everything next time continues where it was");
        add("Shut everything down", "shutdown", "stops the pipeline, extractions, builds and AI models, and turns the scheduled task off until you open this app again (an extraction in progress starts over next time)");
        add("Cancel", "cancel", "don't close");
        f.Controls.Add(flow);
        f.ShowDialog(this);
        if (result == "shutdown")
        {
            var busyEnd = BusyWork();
            if (busyEnd.Count > 0 && MessageBox.Show(this, "This is still working: " + string.Join(", ", busyEnd) + ".\n\nStopping now leaves it unfinished, and it has to start that step again later.\n\nShut everything down anyway?", "AnyPS5 Games", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) != DialogResult.Yes) return "cancel";
        }
        return result;
    }

    // Stops every AnyPS5 process (frozen ones too) and turns the pipeline's scheduled task off. Also "--shutdown".
    static int ShutdownEverything()
    {
        Run("schtasks.exe", "/Change /TN \"AnyPS5 Games pipeline\" /DISABLE");
        Run("schtasks.exe", "/End /TN \"AnyPS5 Games pipeline\"");
        Run("schtasks.exe", "/End /TN \"AnyPS5 pipeline restart\"");
        var procs = Snapshot();
        var byParent = procs.ToLookup(x => x.Parent);
        var kill = new List<Proc>();
        var stack = new Stack<Proc>(procs.Where(x => x.Name.Equals("powershell.exe", StringComparison.OrdinalIgnoreCase) && (x.Cmd.Contains("auto-games.ps1") || x.Cmd.Contains("restart-after-extract"))));
        while (stack.Count > 0) { var x = stack.Pop(); kill.Add(x); foreach (var c in byParent[x.Id]) stack.Push(c); }
        kill.AddRange(procs.Where(x => (x.Name.Equals("WinRAR.exe", StringComparison.OrdinalIgnoreCase) || x.Name.Equals("UnRAR.exe", StringComparison.OrdinalIgnoreCase) || x.Name.Equals("ps5-dump-forge.exe", StringComparison.OrdinalIgnoreCase) || x.Cmd.Contains("exfat_extract")) && x.Cmd.Contains("ps5 games dumps")));
        kill.AddRange(procs.Where(x => x.Name.Equals("llama-server.exe", StringComparison.OrdinalIgnoreCase) || (x.Name.Equals("python.exe", StringComparison.OrdinalIgnoreCase) && (x.Cmd.Contains("ai-player.py") || x.Cmd.Contains("exfat_extract.py") || x.Cmd.Contains("assistant.py")))));
        int n = 0;
        foreach (var x in kill.GroupBy(k => k.Id).Select(g => g.First()))
            try { Process.GetProcessById(x.Id).Kill(); n++; } catch { }
        try { File.Delete(PausePath); File.Delete(SuspendedPath); } catch { }
        return n;
    }

    static void Run(string exe, string args)
    {
        try { var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }); p.WaitForExit(15000); } catch { }
    }

    [STAThread]
    static void Main(string[] args)
    {
        // The WebView2 assemblies are kept in _tools\webview2, not next to the .exe.
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string f = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "webview2", new System.Reflection.AssemblyName(e.Name).Name + ".dll");
            return File.Exists(f) ? System.Reflection.Assembly.LoadFrom(f) : null;
        };
        // "AnyPS5 Games.exe --pause" / "--resume" (for a desktop shortcut): pause or resume everything without the window.
        if (args.Contains("--pause")) { Console.WriteLine("frozen: " + FreezeAll()); return; }
        if (args.Contains("--resume")) { Console.WriteLine("resumed: " + ThawAll()); return; }
        if (args.Contains("--shutdown")) { Console.WriteLine("stopped: " + ShutdownEverything()); return; }
        int shots = Array.IndexOf(args, "--screens");
        if (shots >= 0 && shots + 1 < args.Length) ScreenDir = args[shots + 1];
        // 11-04 Blu: "too many PS5 applications running". One window only; an app update's reopen waits for the old window to close.
        var single = new System.Threading.Mutex(false, @"Local\AnyPS5GamesWindow");
        bool mine;
        try { mine = single.WaitOne(TimeSpan.FromSeconds(20)); } catch (System.Threading.AbandonedMutexException) { mine = true; }
        if (!mine)
        {
            var me = Process.GetCurrentProcess();
            foreach (var other in Process.GetProcessesByName(me.ProcessName))
                if (other.Id != me.Id && other.MainWindowHandle != IntPtr.Zero) { ShowWindow(other.MainWindowHandle, 9); SetForegroundWindow(other.MainWindowHandle); break; }
            return;
        }
        // 11-12 Blu: one app, not two. Opening AnyPS5 Games also opens the Machines view (_tools\launcher\AnyPS5
        // Machines.exe), which takes this window in as its "AnyPS5 Games" tab beside the Machines tab.
        if (!args.Contains("--alone") && Process.GetProcessesByName("AnyPS5 Machines").Length == 0)
        {
            string shell = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "launcher", "AnyPS5 Machines.exe");
            if (File.Exists(shell)) try { Process.Start(new ProcessStartInfo(shell, "--games") { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(shell) }); } catch { }
        }
        Application.EnableVisualStyles(); Application.Run(new Launcher());
        GC.KeepAlive(single);
    }

    static string ScreenDir;

    void SaveScreens()
    {
        Directory.CreateDirectory(ScreenDir);
        var sizes = new[] { new Size(900, 1039), new Size(1440, 1000) };
        WindowState = FormWindowState.Normal;
        int step = 0, total = sizes.Length * tabs.TabPages.Count + 1;
        var clock = new Timer { Interval = 1500 };
        clock.Tick += (s, e) =>
        {
            if (step > 0)
            {
                using (var b = new Bitmap(Width, Height))
                using (var g = Graphics.FromImage(b))
                {
                    IntPtr dc = g.GetHdc(); PrintWindow(Handle, dc, 2); g.ReleaseHdc(dc);
                    b.Save(Path.Combine(ScreenDir, Width + "-" + tabs.SelectedTab.Text.Replace(" ", "") + ".png"));
                }
            }
            if (step == total - 1) { clock.Stop(); Environment.Exit(0); }
            Bounds = new Rectangle(-6000, 0, sizes[step / tabs.TabPages.Count].Width, sizes[step / tabs.TabPages.Count].Height);
            tabs.SelectedIndex = step % tabs.TabPages.Count;
            step++;
        };
        clock.Start();
    }

    Launcher()
    {
        Text = "AnyPS5 Games"; BackColor = Bg; ForeColor = Fg;
        DarkScrollbars(console); DarkScrollbars(activity); DarkScrollbars(chat);
        var area = Screen.FromPoint(Cursor.Position).WorkingArea;
        StartPosition = FormStartPosition.Manual; Bounds = area; WindowState = FormWindowState.Maximized; MinimumSize = new Size(640, 480);
        Font = new Font("Segoe UI", 10f);

        var top = new Panel { Dock = DockStyle.Top, Height = 92, BackColor = Panel, Padding = new Padding(16, 10, 16, 8) };
        var title = new Label { Text = "AnyPS5 Games", Font = new Font("Segoe UI Semibold", 15f), AutoSize = true, Location = new Point(16, 8) };
        now.Font = new Font("Segoe UI", 11.5f); now.AutoSize = false; now.AutoEllipsis = true; now.Location = new Point(18, 42); now.Size = new Size(600, 24); now.ForeColor = Cyan;
        health.Font = new Font("Segoe UI", 9f); health.AutoSize = false; health.AutoEllipsis = true; health.Location = new Point(18, 66); health.Size = new Size(600, 20); health.ForeColor = Dim;
        var openGames = Button("Games folder", (s, e) => Process.Start("explorer.exe", "\"" + Root.TrimEnd('\\') + "\""));
        var openReports = Button("Reports", (s, e) => Process.Start("explorer.exe", "\"" + Path.Combine(Root, "_reports") + "\""));
        pause = Button("Pause everything", (s, e) => TogglePause());
        // Blu 11-07: snapped to half the screen beside AnyPS5 Quest View the window was held at 900 px and ran under it.
        // Below 1100 px the assistant folds away so the board and tabs get the width; this button shows or hides it.
        assistantButton = Button("Assistant", (s, e) => { assistantShown = !IsAssistantShown(); PlaceAssistant(); }); assistantButton.Width = 100;
        top.Controls.AddRange(new Control[] { title, now, health, openGames, openReports, pause, assistantButton });
        top.Resize += (s, e) =>
        {
            openReports.Location = new Point(top.Width - openReports.Width - 16, 4); openGames.Location = new Point(openReports.Left - openGames.Width - 10, 4); pause.Location = new Point(openGames.Left - pause.Width - 10, 4);
            assistantButton.Location = new Point(pause.Left - assistantButton.Width - 10, 4);
            now.Width = health.Width = Math.Max(200, top.Width - 36);
        };

        // left: the game board and the live console
        var split = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Vertical, BackColor = Panel, SplitterWidth = 5, FixedPanel = FixedPanel.Panel2 };
        split.Panel1.BackColor = Bg; split.Panel2.BackColor = Bg;

        games.View = View.Details; games.FullRowSelect = true; games.HeaderStyle = ColumnHeaderStyle.Nonclickable; games.BackColor = Bg; games.ForeColor = Fg;
        games.BorderStyle = BorderStyle.None; games.Font = new Font("Segoe UI", 10f); games.Dock = DockStyle.Fill;
        // The game list and, while a game runs, the game status panel share this area (the divider below resizes it).
        board.Dock = DockStyle.Top; board.Height = 330; board.BackColor = Bg;
        BuildGameStatus();
        board.Controls.Add(games); board.Controls.Add(gameStatus);
        games.Columns.Add("Game", 300); games.Columns.Add("Stage", 170); games.Columns.Add("Latest result", 600);
        Fit(games, 36, 26, 38);
        DarkHeader(games);
        split.Panel1.Resize += (s, e) => board.Height = Math.Max(120, split.Panel1.Height * boardShare / 100);
        games.DoubleClick += (s, e) => { var g = Selected(); if (g != null && Directory.Exists(g.Dir)) Process.Start("explorer.exe", "\"" + g.Dir + "\""); };
        var menu = new ContextMenuStrip();
        menu.Items.Add("Open its folder", null, (s, e) => { var g = Selected(); if (g != null && Directory.Exists(g.Dir)) Process.Start("explorer.exe", "\"" + g.Dir + "\""); });
        menu.Items.Add("Ask the assistant about it", null, (s, e) => { var g = Selected(); if (g != null) Ask("What is the status of " + g.Name + "? If it has a problem: why, and what can be done?"); });
        menu.Items.Add("Open the fix on GitHub", null, (s, e) =>
        {
            var g = Selected(); string lead;
            if (g != null && gameLead.TryGetValue(Path.GetFileName(g.Dir.TrimEnd('\\')), out lead)) Process.Start(lead.Substring(lead.LastIndexOf('|') + 1));
            else Say("No GitHub lead for that game yet. The Game bugs tab under GitHub lists what is known.", Dim);
        });
        menu.Items.Add("Show the running game's status", null, (s, e) => { gsListShown = false; ShowGameStatus(liveGame != null); });
        menu.Items.Add("Test it again", null, (s, e) => Request("retest"));
        menu.Items.Add("Build it again (files stay extracted)", null, (s, e) => Request("rebuild"));
        menu.Items.Add("Start it over (extract, build, test)", null, (s, e) => Request("redo"));
        games.ContextMenuStrip = menu;

        var consoleTitle = new Label { Text = "  Live console: every step, command and line of output", Dock = DockStyle.Top, Height = 26, BackColor = Panel, ForeColor = Dim, TextAlign = ContentAlignment.MiddleLeft };
        console.Dock = DockStyle.Fill; console.ReadOnly = true; console.BackColor = Color.FromArgb(10, 11, 13); console.ForeColor = Fg; console.BorderStyle = BorderStyle.None;
        console.Font = new Font("Consolas", 9.5f); console.WordWrap = true; console.DetectUrls = false;
        // Tabs under the game board: the live console, the running game, downloads, GitHub.
        tabs.Dock = DockStyle.Fill; tabs.Font = new Font("Segoe UI", 9.5f);
        var consolePage = new TabPage("Live console") { BackColor = Bg };
        // Blu 11-06: the Chromebook and the MacBook beside the Live console, so the stream shows all three machines.
        // remote-view.ps1 asks them over ssh every 15 s and writes live\remote-view.txt; this only reads that file.
        var consoleSplit = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Vertical, BackColor = Panel, SplitterWidth = 6 };
        consoleSplit.Panel1.Controls.Add(console); consoleSplit.Panel1.Controls.Add(consoleTitle);
        var remoteTitle = new Label { Text = "  Chromebook + MacBook: what they are running", Dock = DockStyle.Top, Height = 26, BackColor = Panel, ForeColor = Dim, TextAlign = ContentAlignment.MiddleLeft };
        var remote = new RichTextBox { Dock = DockStyle.Fill, ReadOnly = true, BackColor = Color.FromArgb(10, 11, 13), ForeColor = Fg, BorderStyle = BorderStyle.None, Font = new Font("Consolas", 9.5f), WordWrap = true, DetectUrls = false };
        consoleSplit.Panel2.Controls.Add(remote); consoleSplit.Panel2.Controls.Add(remoteTitle);
        consolePage.Controls.Add(consoleSplit);
        consolePage.Layout += (s, e) => { if (consoleSplit.Width > 200 && !remote.Tag.Equals(true)) { consoleSplit.SplitterDistance = consoleSplit.Width * 60 / 100; remote.Tag = true; } };
        remote.Tag = false;
        var remoteTimer = new Timer { Interval = 5000 };
        remoteTimer.Tick += (s, e) => ShowRemote(remote);
        remoteTimer.Start(); ShowRemote(remote);
        var activityPage = new TabPage("AI activity") { BackColor = Bg }; activityPage.Controls.Add(activity);
        tabs.TabPages.Add(consolePage); tabs.TabPages.Add(activityPage); tabs.TabPages.Add(BuildGamePage()); tabs.TabPages.Add(BuildDownloadsPage()); tabs.TabPages.Add(BuildGitHubPage()); tabs.TabPages.Add(BuildDiscordPage());
        tabs.SelectedIndexChanged += (s, e) => { if (tabs.SelectedTab == downloadsPage) RefreshDownloads(); if (tabs.SelectedTab == githubPage && githubRefreshed < DateTime.Now.AddMinutes(-2)) RefreshGitHub(); };
        boardInfo.Dock = DockStyle.Top; boardInfo.Height = 24; boardInfo.BackColor = Bg; boardInfo.ForeColor = Cyan; boardInfo.AutoSize = false; boardInfo.AutoEllipsis = true; boardInfo.Padding = new Padding(8, 3, 4, 0);
        var divider = new Splitter { Dock = DockStyle.Top, Height = 6, BackColor = Panel, MinSize = 60, MinExtra = 160 };
        divider.SplitterMoved += (s, e) => { if (split.Panel1.Height > 0) boardShare = board.Height * 100 / split.Panel1.Height; };
        tabs.Font = new Font("Segoe UI", 9.5f);
        split.Panel1.Controls.Add(tabs); split.Panel1.Controls.Add(divider); split.Panel1.Controls.Add(board); split.Panel1.Controls.Add(boardInfo);

        // right: the assistant
        assistantTitle = new Label { Text = AssistantTitleText(), Dock = DockStyle.Top, Height = 30, BackColor = Panel, ForeColor = Fg, Font = new Font("Segoe UI Semibold", 11f), TextAlign = ContentAlignment.MiddleLeft };
        assistantStatus.Dock = DockStyle.Top; assistantStatus.Height = 22; assistantStatus.BackColor = Panel; assistantStatus.ForeColor = Dim; assistantStatus.Font = new Font("Segoe UI", 9f);
        assistantStatus.Text = "   starting..."; assistantStatus.TextAlign = ContentAlignment.MiddleLeft;
        chat.Dock = DockStyle.Fill; chat.ReadOnly = true; chat.BackColor = Color.FromArgb(10, 11, 13); chat.ForeColor = Fg; chat.BorderStyle = BorderStyle.None;
        chat.Font = new Font("Segoe UI", 10f); chat.DetectUrls = false;

        askPanel.Dock = DockStyle.Bottom; askPanel.Height = 130; askPanel.BackColor = Color.FromArgb(52, 44, 24); askPanel.Visible = false; askPanel.Padding = new Padding(8);
        askText.Dock = DockStyle.Fill; askText.ForeColor = Yellow; askText.Font = new Font("Segoe UI", 9.5f); askText.AutoEllipsis = true;
        var yes = Button("Yes", (s, e) => Answer(true)); var no = Button("No", (s, e) => Answer(false)); var all = Button("Yes to everything", (s, e) => Answer("auto"));
        yes.Width = 80; no.Width = 80;
        var askButtons = Bar(DockStyle.Bottom); askButtons.BackColor = askPanel.BackColor; askButtons.Controls.AddRange(new Control[] { yes, no, all });
        askPanel.Controls.Add(askText); askPanel.Controls.Add(askButtons);

        var inputPanel = new Panel { Dock = DockStyle.Bottom, Height = 70, BackColor = Panel, Padding = new Padding(8, 8, 8, 8) };
        input.Multiline = true; input.Dock = DockStyle.Fill; input.BackColor = Color.FromArgb(36, 40, 47); input.ForeColor = Fg; input.BorderStyle = BorderStyle.FixedSingle; input.Font = new Font("Segoe UI", 10.5f);
        input.KeyDown += (s, e) =>
        {
            if (slashList.Visible && (e.KeyCode == Keys.Up || e.KeyCode == Keys.Down))
            {
                int i = slashList.SelectedIndex + (e.KeyCode == Keys.Down ? 1 : -1);
                if (i >= 0 && i < slashList.Items.Count) slashList.SelectedIndex = i;
                e.SuppressKeyPress = true; e.Handled = true; return;
            }
            if (slashList.Visible && e.KeyCode == Keys.Escape) { slashList.Visible = false; e.SuppressKeyPress = true; return; }
            if (slashList.Visible && (e.KeyCode == Keys.Tab || e.KeyCode == Keys.Enter)) { TakeSlash(); e.SuppressKeyPress = true; e.Handled = true; return; }
            if (e.KeyCode == Keys.Enter && !e.Shift) { e.SuppressKeyPress = true; SendInput(); }
        };
        input.AcceptsTab = true;
        input.TextChanged += (s, e) => ShowSlash();
        slashList.Click += (s, e) => TakeSlash();
        Controls.Add(slashList);
        // the model loads as soon as Blu starts typing, so it is ready when he sends
        DateTime lastWarm = DateTime.MinValue;
        input.TextChanged += (s, e) =>
        {
            if (input.TextLength == 0 || input.Text.StartsWith("/") || (DateTime.Now - lastWarm).TotalSeconds < 60) return;
            lastWarm = DateTime.Now;
            SendJson(new Dictionary<string, object> { { "t", "warm" } });
        };
        send = Button("Send", (s, e) => SendInput()); send.Dock = DockStyle.Right; send.Width = 80;
        talk = Button("Talk", (s, e) => StartVoiceTyping()); talk.Dock = DockStyle.Right; talk.Width = 70;
        discordWatch.Tick += (s, e) => WatchDiscord(); discordWatch.Start();
        inputPanel.Controls.Add(input); inputPanel.Controls.Add(talk); inputPanel.Controls.Add(send);

        var options = Bar(DockStyle.Bottom);
        autoBox.Text = "Act without asking"; autoBox.AutoSize = true; autoBox.ForeColor = Dim; autoBox.Margin = new Padding(6, 7, 6, 3);
        explainBox.Text = "Tell the AI about problems"; explainBox.AutoSize = true; explainBox.ForeColor = Dim; explainBox.Checked = true; explainBox.Margin = new Padding(6, 7, 6, 3);
        newChat = Button("New chat", (s, e) => { if (!busy) { SendJson(new Dictionary<string, object> { { "t", "new" } }); chat.Clear(); } });
        newChat.Width = 90; newChat.Height = 28;
        var settingsButton = Button("Settings", (s, e) => ShowSettings()); settingsButton.Width = 90; settingsButton.Height = 28;
        options.Controls.Add(settingsButton);
        var dropButton = Button("Drop folder", (s, e) => { Directory.CreateDirectory(DropDir); Process.Start("explorer.exe", "\"" + DropDir + "\""); }); dropButton.Width = 100; dropButton.Height = 28;
        // What the assistant has done (posts, pushes, builds, tests): the action log it remembers across chats.
        var memoryButton = Button("What it did", (s, e) => { string f = Path.Combine(Tools, "assistant", "assistant-actions.md"); if (!File.Exists(f)) File.WriteAllText(f, ""); Process.Start("notepad.exe", "\"" + f + "\""); }); memoryButton.Width = 100; memoryButton.Height = 28;
        var stopButton = Button("Stop", (s, e) => InterruptAssistant()); stopButton.Width = 70; stopButton.Height = 28; stopButton.BackColor = Color.FromArgb(120, 40, 40);
        speakBox.Text = "Read replies aloud"; speakBox.AutoSize = true; speakBox.ForeColor = Dim; speakBox.Margin = new Padding(6, 7, 6, 3);
        speakBox.CheckedChanged += (s, e) => { if (!speakBox.Checked) Speak("", true); };
        options.Controls.AddRange(new Control[] { stopButton, autoBox, explainBox, speakBox, newChat, dropButton, memoryButton });
        // Files dragged onto the assistant's side are copied into the drop folder, and the assistant reads them.
        foreach (Control c in new Control[] { split.Panel2, chat, input })
        {
            c.AllowDrop = true;
            c.DragEnter += (s, e) => { if (e.Data.GetDataPresent(DataFormats.FileDrop)) e.Effect = DragDropEffects.Copy; };
            c.DragDrop += (s, e) => DropFiles(e.Data.GetData(DataFormats.FileDrop) as string[]);
        }
        autoBox.CheckedChanged += (s, e) => { autoSent = false; FlushControl(); };

        split.Panel2.Controls.Add(chat); split.Panel2.Controls.Add(BuildSuggestPanel()); split.Panel2.Controls.Add(askPanel); split.Panel2.Controls.Add(options); split.Panel2.Controls.Add(inputPanel);
        split.Panel2.Controls.Add(assistantStatus); split.Panel2.Controls.Add(assistantTitle);

        Controls.Add(split); Controls.Add(top);
        // The assistant gets about a third of the width (340 to 560 px), the rest is the board and the tabs.
        mainSplit = split;
        Resize += (s, e) => { if (WindowState != FormWindowState.Minimized && split.Width > 0) PlaceAssistant(); };
        Shown += (s, e) => { PlaceAssistant(); if (ScreenDir != null) SaveScreens(); else StartAssistant(); };
        LoadSettings();
        tray.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        tray.DoubleClick += (s, e) => { Show(); if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Maximized; Activate(); };
        FormClosed += (s, e) => { tray.Visible = false; tray.Dispose(); };
        FormClosing += (s, e) =>
        {
            closing = true;
            Detach();  // the game stays open as a normal window
            if (playPaused) { playPaused = false; ThawAll(); }
            if (e.CloseReason == CloseReason.UserClosing)
            {
                string choice = AskOnClose();
                if (choice == "cancel") { e.Cancel = true; closing = false; return; }
                if (choice == "pause" && !Paused()) PauseEverything();
                if (choice == "shutdown") { StopAssistant(); int n = ShutdownEverything(); tray.Visible = false; MessageBox.Show(this, "Everything is off: " + n + " AnyPS5 process(es) stopped and the pipeline's scheduled task is turned off. Opening AnyPS5 Games turns it back on.", "AnyPS5 Games"); }
            }
            Detach();  // an embedded game's window would be destroyed with this one
            StopAssistant();
        };

        if (ScreenDir == null) StartPipeline();
        // Show the last part of the trace first, then follow it.
        if (File.Exists(TracePath)) { long len = new FileInfo(TracePath).Length; traceRead = Math.Max(0, len - 40000); }
        timer.Interval = 1000; timer.Tick += (s, e) => Refresh(); timer.Tick += (s, e) => CheckWorkApp(); timer.Start();
        Refresh();
        Say("The assistant sees what the pipeline is doing and can watch it, pause it, re-test a game, read crash logs, look at the running game's screen, work on code and GitHub. Right-click a game for quick actions.\n", Dim);
    }

    SplitContainer mainSplit;
    Button assistantButton;
    bool? assistantShown;

    bool IsAssistantShown() { return mainSplit != null && !mainSplit.Panel2Collapsed; }

    // Shown when Blu chose it with the Assistant button, otherwise only when the window is at least 1100 px wide.
    void PlaceAssistant()
    {
        if (mainSplit == null) return;
        bool show = assistantShown ?? (ClientSize.Width >= 1100);
        bool was = IsAssistantShown();
        mainSplit.Panel2Collapsed = !show;
        if (show && (!was || mainSplit.Panel2.Width < 300))
        {
            int a = Math.Max(340, Math.Min(560, mainSplit.Width * 34 / 100));
            if (mainSplit.Width - a > 300) mainSplit.SplitterDistance = mainSplit.Width - a;
        }
        assistantButton.BackColor = show ? Color.FromArgb(60, 70, 90) : Color.FromArgb(44, 48, 56);
    }

    Button Button(string text, EventHandler click)
    {
        var b = new Button { Text = text, Width = 130, Height = 32, FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(44, 48, 56), ForeColor = Fg, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowOnly };
        b.FlatAppearance.BorderColor = Color.FromArgb(70, 76, 88); b.Click += click; return b;
    }

    GameTag Selected() { return games.SelectedItems.Count == 0 ? null : games.SelectedItems[0].Tag as GameTag; }

    // --- the pipeline -------------------------------------------------------------------------------------------
    static bool Running()
    {
        try
        {
            foreach (System.Management.ManagementObject o in new System.Management.ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE Name='powershell.exe'").Get())
            { object c = o["CommandLine"]; if (c != null && c.ToString().Contains("auto-games.ps1")) return true; }
        }
        catch { }
        return false;
    }

    void StartPipeline()
    {
        Run("schtasks.exe", "/Change /TN \"AnyPS5 Games pipeline\" /ENABLE");  // "Shut everything down" turned it off
        if (Running()) return;
        try
        {
            var p = Process.Start(new ProcessStartInfo("schtasks.exe", "/Run /TN \"AnyPS5 Games pipeline\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true });
            p.WaitForExit();
            if (p.ExitCode != 0)
                Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + Path.Combine(Tools, "auto-games.ps1") + "\"") { UseShellExecute = false, CreateNoWindow = true });
        }
        catch (Exception ex) { Append("could not start the pipeline: " + ex.Message, Red); }
    }

    void Request(string what)
    {
        var g = Selected(); if (g == null) return;
        if (what == "redo" && MessageBox.Show(this, "Start " + g.Name + " over: extract, build and test it again?", "AnyPS5 Games", MessageBoxButtons.YesNo) != DialogResult.Yes) return;
        try { File.AppendAllText(RequestsPath, what + " " + g.Key + "\r\n"); Append("requested: " + what + " " + g.Key + " (the pipeline picks it up at its next pass)", Blue); }
        catch (Exception ex) { Append("could not write the request: " + ex.Message, Red); }
    }

    int tick;
    void UiCommand()
    {
        string file = Path.Combine(LiveDir, "ui-command.txt");
        if (!File.Exists(file)) return;
        string[] lines;
        try { lines = File.ReadAllLines(file); File.Delete(file); } catch { return; }
        foreach (string line in lines)
        {
            string word = line.Trim().Split(' ')[0], rest = line.Trim().Substring(word.Length).Trim();
            if (word == "tab") { foreach (TabPage page in tabs.TabPages) if (page.Text.Equals(rest, StringComparison.OrdinalIgnoreCase)) tabs.SelectedTab = page; }
            else if (word == "full") ToggleFullScreen();
            else if (word == "type") input.Text = rest;
            else if (word == "shot")
            {
                SetForegroundWindow(Handle);
                var area = Bounds;
                using (var b = new Bitmap(area.Width, area.Height))
                using (var g = Graphics.FromImage(b))
                {
                    g.CopyFromScreen(area.Location, Point.Empty, area.Size);
                    b.Save(rest);
                }
            }
        }
    }

    new void Refresh()
    {
        UiCommand();
        ReadTrace();
        if (tick % 3 == 0) LoadGames();
        if (tick % 10 == 0) UpdateHealth();  // a Windows process query: every 10 s is plenty
        if (tick % 600 == 0) WatchPrs();       // GitHub: every 10 minutes
        if (tick % 600 == 20) RefreshBugLinks();  // only logs that changed are read
        if (tick % 300 == 5) CheckStorage();   // D:'s space budget: every 5 minutes
        if (tick % 1800 == 60 && autoCleanBox.Checked) CleanUp(false);  // archives of working games: every 30 minutes, when Blu turned it on
        tick++;
    }

    // D:'s space budget (downloads.py): free space minus what waiting games will take to unpack and what downloads still
    // need. Shown on the top bar; a notification (at most every 3 hours) when it does not fit.
    string storageText = "";
    DateTime storageWarned = DateTime.MinValue;

    void ApplyStorage(Dictionary<string, object> d)
    {
        object so; if (d == null || !d.TryGetValue("storage", out so)) return;
        var s = so as Dictionary<string, object>; if (s == null) return;
        double left = Convert.ToDouble(s["left"]), free = Convert.ToDouble(s["free"]);
        double freeG = s.ContainsKey("freeG") ? Convert.ToDouble(s["freeG"]) : 0;
        storageText = string.Format("D: {0:N0} GB + G: {1:N0} GB free, {2:N0} GB still to unpack/download, {3:N0} GB {4}", free, freeG,
                                    Convert.ToDouble(s["pending"]) + Convert.ToDouble(s["downloading"]), Math.Abs(left), left >= 0 ? "to spare" : "SHORT");
        if (left < 0 && storageWarned < DateTime.Now.AddHours(-3))
        {
            storageWarned = DateTime.Now;
            string msg = string.Format("D: and G: together are {0:N0} GB short for the games waiting to unpack and the downloads in progress. Games wait instead of failing; free space (or skip a download) to let them through.", -left);
            Say("Storage: " + msg, Yellow); Notify("AnyPS5: not enough space on D:", msg);
        }
    }

    void CheckStorage() { Helper("downloads.py", ApplyStorage); }

    // Archives of games that are built and run (downloads.py "cleanable"): deleted when Blu presses Clean up now and
    // confirms, or by themselves when he turned the setting on. The unpacked game files are never touched.
    void CleanUp(bool ask)
    {
        Helper("downloads.py", d =>
        {
            var list = Rows(d, "cleanable");
            if (list.Count == 0) { if (ask) MessageBox.Show(this, "Nothing to clean up: no archive of a built, working game is left.", "Clean up"); return; }
            double gb = list.Sum(r => Convert.ToDouble(r["gb"]));
            var games = list.Select(r => Get(r, "game")).Distinct().ToList();
            if (ask && MessageBox.Show(this, string.Format("Delete {0} archive file(s), {1:N1} GB, of these built and working games?\n\n{2}\n\nThe games keep working (their unpacked files stay). This cannot be undone.", list.Count, gb, string.Join("\n", games)),
                                       "Clean up", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;
            int done = 0;
            foreach (var r in list) try { File.Delete(Get(r, "path")); done++; } catch { }
            foreach (var dir in list.Select(r => Path.GetDirectoryName(Get(r, "path"))).Distinct())
                try { if (Directory.Exists(dir) && Directory.GetFileSystemEntries(dir).Length == 0) Directory.Delete(dir); } catch { }
            Say(string.Format("Cleaned up: deleted {0} archive file(s), {1:N1} GB, of {2}.", done, gb, string.Join(", ", games)), Green);
            CheckStorage();
        });
    }

    // Games being unpacked right now, seen directly from the running extractors (the pipeline's background ones,
    // WinRAR, or one started by hand): the board shows them as "Extracting now" whatever the pipeline last wrote.
    HashSet<string> extracting = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    Dictionary<string, int> extractPercent = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

    void FindExtractions()
    {
        var found = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var percent = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        try
        {
            foreach (System.Management.ManagementObject o in new System.Management.ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='WinRAR.exe' OR Name='UnRAR.exe'").Get())
            {
                string c = o["CommandLine"] as string;
                if (c == null || !(c.Contains("exfat_extract") || c.IndexOf("WinRAR", StringComparison.OrdinalIgnoreCase) >= 0 || c.IndexOf("UnRAR", StringComparison.OrdinalIgnoreCase) >= 0)) continue;
                var m = Regex.Match(c, @"ps5 games dumps from ps5\\([^\\""]+)\\", RegexOptions.IgnoreCase);
                if (m.Success)
                {
                    found.Add(m.Groups[1].Value);
                    var image = Regex.Match(c, @"exfat_extract\.py""?\s+""([^""]+)""");
                    string log = Path.Combine(Tools, "extract-" + m.Groups[1].Value + ".log");
                    if (image.Success && File.Exists(log) && File.Exists(image.Groups[1].Value))
                    {
                        double mb = 0;
                        using (var fs = new FileStream(log, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                        using (var r = new StreamReader(fs))
                            foreach (Match size in Regex.Matches(r.ReadToEnd(), @"\(([\d,.]+) MB\)")) mb += double.Parse(size.Groups[1].Value.Replace(",", ""), System.Globalization.CultureInfo.InvariantCulture);
                        percent[m.Groups[1].Value] = (int)Math.Min(99, mb * 1048576 * 100 / new FileInfo(image.Groups[1].Value).Length);
                    }
                }
            }
        }
        catch { }
        extracting = found; extractPercent = percent;
    }

    bool healthBusy;
    void UpdateHealth()
    {
        if (healthBusy) return;
        healthBusy = true;
        System.Threading.ThreadPool.QueueUserWorkItem(_ =>
        {
            bool run = false, paused = false; string beat = "never";
            try
            {
                run = Running(); paused = File.Exists(PausePath);
                FindExtractions();
                if (File.Exists(AlivePath)) beat = File.ReadAllText(AlivePath).Trim();
            }
            catch { }
            Ui(() => { healthBusy = false; ShowHealth(run, paused, beat); });
        });
    }

    static string PcOwnerText()
    {
        string owner = "free";
        try
        {
            string flag = Path.Combine(Tools, "live", "gpu-busy.flag");
            if (File.Exists(flag))
            {
                string[] parts = File.ReadAllText(flag).Trim().Split(new[] { ' ' }, 3);
                int pid;
                bool alive = false;
                if (parts.Length > 0 && int.TryParse(parts[0], out pid)) { try { alive = !System.Diagnostics.Process.GetProcessById(pid).HasExited; } catch { } }
                if (alive && (DateTime.Now - File.GetLastWriteTime(flag)).TotalMinutes < 20)
                    owner = (parts.Length > 2 ? parts[2] : "a job") + (parts.Length > 1 ? " (since " + parts[1].Substring(0, Math.Min(5, parts[1].Length)) + ")" : "");
            }
            string waits = Path.Combine(Tools, "live", "pc-wait");
            int waiting = Directory.Exists(waits) ? Directory.GetFiles(waits, "*.txt").Length : 0;
            if (waiting > 0) owner += ", " + waiting + " waiting";
        }
        catch { }
        return "PC: " + owner;
    }

    DateTime lastDoctor = DateTime.MinValue;
    void ShowHealth(bool run, bool paused, string beat)
    {
        if (!run && !paused && (DateTime.Now - lastDoctor).TotalSeconds > 60)
        {
            lastDoctor = DateTime.Now;
            Helper("pipeline_doctor.py", d =>
            {
                if (d == null || !d.ContainsKey("fixed")) return;
                foreach (object what in (IEnumerable)d["fixed"]) { Say("Pipeline: " + what, Yellow); Notify("AnyPS5 pipeline", what.ToString()); }
            });
        }
        if (paused && !now.Text.StartsWith("Paused. ")) now.Text = "Paused. " + now.Text;
        if (!paused && now.Text.StartsWith("Paused. ")) now.Text = now.Text.Substring(8);
        health.Text = paused ? "EVERYTHING PAUSED: the pipeline and its extractions/builds are frozen, the AI models unloaded; no job takes the PC. Press Resume everything to continue."
                             : (run ? "Pipeline running" : "Pipeline NOT running (it restarts within 10 minutes, or reopen this window)") + "   |   last check-in " + beat + (storageText.Length > 0 ? "   |   " + storageText : "");
        if (File.Exists(Path.Combine(Tools, "tests-off.flag"))) health.Text += "   |   automatic tests off";
        health.Text += "   |   " + PcOwnerText();
        health.ForeColor = paused ? Yellow : !run ? Red : Dim;
        pause.Text = paused ? "Resume everything" : "Pause everything";
        pause.BackColor = paused ? Color.FromArgb(90, 70, 20) : Color.FromArgb(44, 48, 56);
    }

    bool traceBusy;
    void ReadTrace()
    {
        if (traceBusy) return;
        traceBusy = true;
        System.Threading.ThreadPool.QueueUserWorkItem(_ =>
        {
            string text = "";
            try
            {
                if (File.Exists(TracePath))
                    using (var fs = new FileStream(TracePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    {
                        if (fs.Length < traceRead) traceRead = 0;  // rotated
                        if (fs.Length - traceRead > 262144) traceRead = fs.Length - 262144;  // far behind: only the newest part
                        if (fs.Length > traceRead)
                        {
                            fs.Seek(traceRead, SeekOrigin.Begin);
                            var buf = new byte[fs.Length - traceRead];
                            int n = fs.Read(buf, 0, buf.Length);
                            int end = n > 0 ? Array.LastIndexOf(buf, (byte)'\n', n - 1) : -1;
                            if (end >= 0) { traceRead += end + 1; text = Encoding.UTF8.GetString(buf, 0, end + 1); }
                        }
                    }
            }
            catch { }
            Ui(() => { traceBusy = false; ShowTrace(text); });
        });
    }

    void ShowTrace(string text)
    {
        if (text.Length == 0) { firstRead = false; return; }
        try
        {
            {
                console.SuspendLayout();
                foreach (string raw in text.Split('\n'))
                {
                    string line = raw.TrimEnd('\r');
                    if (line.Length == 0) continue;
                    string body = line.Length > 10 ? line.Substring(10) : line;
                    Color c = Fg;
                    if (body.StartsWith("$ ")) c = Cyan;
                    else if (body.StartsWith("  ")) c = Dim;
                    if (body.StartsWith("EVENT "))
                    {
                        c = Problem.IsMatch(body) ? Yellow : Regex.IsMatch(body, "BUILT|runs:|FIXED|EXTRACTED") ? Green : Blue;
                        // A problem the pipeline just ran into: the assistant explains it (not for the backlog shown at start).
                        if (!firstRead && explainBox.Checked && Problem.IsMatch(body) && pending.Count < 3)
                        {
                            string ev = body.Substring(6);
                            Say("Pipeline: " + ev, Yellow);
                            Notify("AnyPS5 pipeline", ev);
                            SendJson(new Dictionary<string, object> { { "t", "event" }, { "text", ev } });
                        }
                    }
                    Append(line.Replace("EVENT ", ""), c);
                    if (!body.StartsWith("  ") && !body.StartsWith("$ ") && !body.StartsWith("checking ") && !body.StartsWith("pass done")) { now.Text = "Now: " + Clean(body.Replace("EVENT ", "")); var m = Regex.Match(body, @"^(?:EVENT \w+(?: \w+)? )?([^:]+?)(?: \(PPSA\d+\))?:"); if (m.Success) currentGame = m.Groups[1].Value.Trim(); }
                }
                if (console.TextLength > 400000) { console.Select(0, console.TextLength - 300000); console.SelectedText = ""; }
                console.SelectionStart = console.TextLength; console.ScrollToCaret();
                console.ResumeLayout();
                firstRead = false;
            }
        }
        catch { }
    }

    // Download-site names stay out of everything shown on screen (Blu streams this window). Only what is displayed changes, never a real path.
    static readonly Regex SiteNames = new Regex(@"(https?://)?(www\.)?\[?\b(example-site(\.com)?|example-site)\b\]?(/\S*)?[\s_-]*", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    static string Clean(string text) { return string.IsNullOrEmpty(text) ? text : SiteNames.Replace(text, ""); }
    static ListViewItem Scrubbed(ListViewItem item)
    {
        foreach (ListViewItem.ListViewSubItem sub in item.SubItems) sub.Text = Clean(sub.Text);
        return item;
    }

    void Append(string text, Color c)
    {
        console.SelectionStart = console.TextLength; console.SelectionLength = 0; console.SelectionColor = c;
        console.AppendText(Clean(text) + "\n");
    }

    readonly Label boardInfo = new Label();
    int boardShare = 42;

    // What is happening comes first; finished games last.
    static int StageOrder(string s)
    {
        if (s.StartsWith(">")) return 0;
        if (s.StartsWith("Extracting")) return 1;
        if (s.StartsWith("Built") || s.StartsWith("Unpacked")) return 2;
        if (s.StartsWith("Waiting")) return 3;
        if (s.StartsWith("Can't") || s.StartsWith("Extract failed") || s.StartsWith("Needs")) return 4;
        if (s.StartsWith("Starts")) return 6;
        return 5;
    }

    bool gamesBusy;
    void LoadGames()
    {
        if (gamesBusy) return;
        gamesBusy = true;
        System.Threading.ThreadPool.QueueUserWorkItem(_ =>
        {
            Dictionary<string, object> state = null;
            try
            {
                string text;
                using (var fs = new FileStream(StatePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var r = new StreamReader(fs, Encoding.UTF8)) text = r.ReadToEnd();
                state = new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.Deserialize<Dictionary<string, object>>(text);
            }
            catch { }
            Ui(() => { gamesBusy = false; if (state != null) ShowGames(state); });
        });
    }

    void ShowGames(Dictionary<string, object> state)
    {
        var rows = new List<ListViewItem>();
        var results = new Dictionary<string, string>();
        foreach (var kv in state.OrderBy(k => k.Key))
        {
            if (kv.Key.StartsWith("download: ")) continue;
            var st = kv.Value as Dictionary<string, object>;
            if (st != null && Get(st, "gameDir").Length > 0)
            {
                string pl = Get(st, "played"), rc = Get(st, "recheck"), nt = Get(st, "note");
                results[Get(st, "gameDir").TrimEnd('\\').ToLowerInvariant()] = pl.Length > 0 ? "AI play: " + pl : rc.Length > 0 ? rc : nt;
            }
            var e = kv.Value as Dictionary<string, object>; if (e == null) continue;
            string status = Get(e, "status"), note = Get(e, "note"), played = Get(e, "played"), recheck = Get(e, "recheck"), dir = Get(e, "gameDir");
            string name = dir.Length > 0 ? Path.GetFileName(dir) : kv.Key;
            string stage; Color c;
            bool active = currentGame.Length > 0 && (kv.Key.Equals(currentGame, StringComparison.OrdinalIgnoreCase) || name.StartsWith(currentGame, StringComparison.OrdinalIgnoreCase));
            switch (status)
            {
                case "new":
                    bool unpacked = Get(e, "extracted").Length > 0 && !note.Contains("waiting") && !note.Contains("incomplete");
                    if (extracting.Contains(kv.Key)) { int pct; stage = extractPercent.TryGetValue(kv.Key, out pct) ? "Extracting now, " + pct + "%" : "Extracting now"; c = Cyan; }
                    else if (note.StartsWith("no room")) { stage = "Waiting for space"; c = Yellow; }
                    else if (note.StartsWith("being extracted")) { stage = "Extracting"; c = Cyan; }
                    else if (note.StartsWith("extract failed")) { stage = "Extract failed"; c = Red; }
                    else if (unpacked) { stage = "Unpacked, build next"; c = Cyan; }
                    else if (note.Contains("waiting") || note.Contains("incomplete")) { stage = "Waiting for download"; c = Dim; }
                    else { stage = note.Length > 0 ? note : "Queued"; c = Dim; }
                    break;
                case "built": stage = "Built, test next"; c = Cyan; break;
                case "done":
                    // 11-05 Spider-Man Remastered showed green from a 0.47 FPS shader-compile run, then crashed on every later run.
                    var endFps = Regex.Match(note, @"([\d.]+) FPS(?![\s\S]*FPS)|FPS: ([\d.]+)(?![\s\S]*FPS)");
                    double fps;
                    bool slide = endFps.Success && double.TryParse(endFps.Groups[1].Success ? endFps.Groups[1].Value : endFps.Groups[2].Value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out fps) && fps < 1;
                    bool ok = note.StartsWith("runs:") && !slide && !recheck.StartsWith("REGRESSED");
                    stage = ok ? "Runs" : note.StartsWith("runs:") ? (slide ? "Starts, under 1 FPS" : "Crashes now (pinned)") : "Tested: problem"; c = ok ? Green : Yellow; break;
                case "skip": stage = recheck.StartsWith("REGRESSED") ? "Runs (pinned build)" : "Runs (set up by hand)"; c = Green; break;
                case "encrypted": stage = "Needs decrypted dump"; c = Red; break;
                case "failed": stage = "Can't build"; c = Red; break;
                default: stage = status; c = Dim; break;
            }
            // 600 frames and 3 minutes up: the game starts. Nobody knows about the rest of it.
            if (status == "done" && note.StartsWith("runs:") && c == Green) stage = "Starts (not played)";
            if (status == "done" && note.StartsWith("result lost")) { stage = "Needs a new test"; c = Yellow; }
            if (active) { stage = "> WORKING NOW"; c = Blue; }
            string result = played.Length > 0 && (status == "done" || status == "skip") ? "AI play: " + played : (recheck.Length > 0 ? recheck : note);
            if (stage.StartsWith("Extracting")) result = "";
            string leadText;
            if (dir.Length > 0 && gameLead.TryGetValue(Path.GetFileName(dir.TrimEnd('\\')), out leadText)) result = "Lead: " + leadText.Substring(0, leadText.LastIndexOf('|')) + "  |  " + result;
            var item = new ListViewItem(new[] { name, stage, result.Replace("\r", " ").Replace("\n", " ") }) { ForeColor = c, Tag = new GameTag { Dir = dir, Key = kv.Key, Name = name } };
            rows.Add(item);
        }
        string selected = Selected() != null ? Selected().Key : null;
        games.BeginUpdate();
        int topIndex = games.TopItem != null ? games.TopItem.Index : 0;
        rows = rows.OrderBy(r => StageOrder(r.SubItems[1].Text)).ToList();
        games.Items.Clear(); games.Items.AddRange(rows.Select(Scrubbed).ToArray());
        Func<int, int> count = n => rows.Count(r => StageOrder(r.SubItems[1].Text) == n);
        boardInfo.Text = string.Format("{0} working  |  {1} extracting  |  {2} to build/test  |  {3} waiting  |  {4} can't build  |  {5} problems  |  {6} start",
                                       count(0), count(1), count(2), count(3), count(4), count(5), count(6));
        if (topIndex < games.Items.Count && games.Items.Count > 0) games.TopItem = games.Items[topIndex];
        if (selected != null) foreach (ListViewItem i in games.Items) if (((GameTag)i.Tag).Key == selected) i.Selected = true;
        games.EndUpdate();
        LoadGameList(results);
    }

    static string Get(Dictionary<string, object> e, string key)
    {
        object v; if (!e.TryGetValue(key, out v) || v == null) return "";
        if (v is string) return (string)v;
        if (v is IEnumerable && !(v is string)) return string.Join(", ", ((IEnumerable)v).Cast<object>().Select(o => o == null ? "" : o.ToString()));
        return v.ToString();
    }

    // --- the assistant ------------------------------------------------------------------------------------------
    void StartAssistant()
    {
        if (!File.Exists(AssistantScript)) { Status("assistant not found: " + AssistantScript, Red); return; }
        try
        {
            var psi = new ProcessStartInfo("python", "-I \"" + AssistantScript + "\" --serve")
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8, WorkingDirectory = Path.Combine(Tools, "assistant")
            };
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            psi.EnvironmentVariables["ANYPS5_ASSISTANT_MODEL"] = modelBox.SelectedIndex == 1 ? "14b" : "coder";
            assistant = new Process { StartInfo = psi, EnableRaisingEvents = true };
            assistant.OutputDataReceived += (s, e) => { if (e.Data != null) Ui(() => FromAssistant(e.Data)); };
            assistant.ErrorDataReceived += (s, e) => { if (e.Data != null) Ui(() => Say(e.Data, Red, true)); };
            assistant.Exited += (s, e) => Ui(AssistantExited);
            assistant.Start();
            assistantIn = new StreamWriter(assistant.StandardInput.BaseStream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
            assistant.BeginOutputReadLine(); assistant.BeginErrorReadLine();
            busy = true; Status("starting the local model (about a minute)...", Dim);
        }
        catch (Exception ex) { Status("could not start the assistant: " + ex.Message, Red); }
    }

    void StopAssistant()
    {
        try
        {
            if (assistant != null && !assistant.HasExited)
            {
                SendJson(new Dictionary<string, object> { { "t", "exit" } });
                assistantIn.Close();
                if (!assistant.WaitForExit(5000)) assistant.Kill();
            }
            // Never leave the 14B model holding the RTX 4070 SUPER after the window is gone.
            foreach (System.Management.ManagementObject o in new System.Management.ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='llama-server.exe'").Get())
            {
                object c = o["CommandLine"];
                if (c != null && Regex.IsMatch(c.ToString(), @"--port 8093\b")) try { Process.GetProcessById(Convert.ToInt32(o["ProcessId"])).Kill(); } catch { }
            }
        }
        catch { }
    }

    void Ui(Action a)
    {
        try
        {
            if (IsDisposed) return;
            if (IsHandleCreated) { BeginInvoke(a); return; }
            System.Threading.ThreadPool.QueueUserWorkItem(_ =>
            {
                for (int i = 0; i < 300 && !IsHandleCreated && !IsDisposed; i++) System.Threading.Thread.Sleep(100);
                try { if (!IsDisposed) BeginInvoke(a); } catch { }
            });
        }
        catch { }
    }

    void Status(string text, Color c) { assistantStatus.Text = "   " + Clean(text); assistantStatus.ForeColor = c; }

    void Say(string text, Color c, bool small = false)
    {
        chat.SelectionStart = chat.TextLength; chat.SelectionLength = 0; chat.SelectionColor = c;
        chat.SelectionFont = small ? new Font("Consolas", 8.5f) : chat.Font;
        chat.AppendText(Clean(text) + "\n");
        if (chat.TextLength > 300000) { chat.Select(0, chat.TextLength - 200000); chat.SelectedText = ""; }
        chat.SelectionStart = chat.TextLength; chat.ScrollToCaret();
    }

    void SendJson(Dictionary<string, object> msg)
    {
        try { if (assistantIn != null) assistantIn.WriteLine(json.Serialize(msg)); }
        catch (Exception ex) { Status("could not reach the assistant: " + ex.Message, Red); }
    }

    readonly RichTextBox activity = new RichTextBox { Text = "The local AI's steps show here while it works: each tool it uses and what came back. It is quiet until you send it a message.\n", ReadOnly = true, Dock = DockStyle.Fill, BorderStyle = BorderStyle.None, BackColor = Color.FromArgb(10, 12, 16), ForeColor = Color.FromArgb(170, 178, 190), Font = new Font("Consolas", 9f), DetectUrls = false };

    // Typing / opens the list of commands, filtered as you type; Tab or Enter takes one.
    static readonly string[][] SlashCommands =
    {
        new[] { "/status", "what is running, how busy the PC is" }, new[] { "/context", "how full the model's memory is, and with what" }, new[] { "/connectors", "the local MCP servers it can use" }, new[] { "/stop", "stop what the assistant is doing" },
        new[] { "/diagnose", "<game>  what kind of problem it has, what to do next" }, new[] { "/debug", "<game>  work on it, step by step" },
        new[] { "/test", "<game>  run it and record FPS, memory, how it ends" }, new[] { "/trypr", "<pr> <game>  run a game with an open PR" },
        new[] { "/history", "<game>  what was tried, what helped, what broke" }, new[] { "/stuck", "<game>  where a running game's threads are" },
        new[] { "/search", "<text>  which games' logs have it" }, new[] { "/wanted", "missing functions, by how many games need them" },
        new[] { "/prs", "open PRs worth testing for your games" }, new[] { "/review", "check the changes in the workspace" },
        new[] { "/mods", "<game>  a game's mods" }, new[] { "/load", "CPU, disks, memory, GPU" },
        new[] { "/pause", "pause the pipeline" }, new[] { "/resume", "resume the pipeline" }, new[] { "/doctor", "check and repair the pipeline" },
        new[] { "/done", "what the assistant did lately" }, new[] { "/tools", "every tool it has" }, new[] { "/help", "all of this" }
    };
    readonly ListBox slashList = new ListBox { Visible = false, BackColor = Color.FromArgb(28, 31, 37), ForeColor = Color.FromArgb(220, 224, 230), BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f), IntegralHeight = true };

    void ShowSlash()
    {
        string typed = input.Text;
        if (!typed.StartsWith("/") || typed.Contains(" ")) { slashList.Visible = false; return; }
        slashList.BeginUpdate();
        slashList.Items.Clear();
        foreach (var c in SlashCommands) if (c[0].StartsWith(typed, StringComparison.OrdinalIgnoreCase)) slashList.Items.Add(c[0] + "   " + c[1]);
        slashList.EndUpdate();
        if (slashList.Items.Count == 0) { slashList.Visible = false; return; }
        slashList.SelectedIndex = 0;
        var at = PointToClient(input.PointToScreen(Point.Empty));
        int height = Math.Min(slashList.Items.Count, 12) * slashList.ItemHeight + 4, width = Math.Max(input.Width, 440);
        slashList.SetBounds(Math.Max(0, Math.Min(at.X, ClientSize.Width - width)), Math.Max(0, at.Y - height - 2), width, height);
        slashList.Visible = true;
        slashList.BringToFront();
    }

    void TakeSlash()
    {
        if (slashList.SelectedItem == null) return;
        string line = (string)slashList.SelectedItem;
        input.Text = line.Substring(0, line.IndexOf(' ')) + " ";
        input.SelectionStart = input.TextLength;
        slashList.Visible = false;
        input.Focus();
    }

    void InterruptAssistant()
    {
        Speak("", true);
        SendJson(new Dictionary<string, object> { { "t", "stop" } });
        pending.Clear();
        askPanel.Visible = false;
        Status(busy ? "stopping..." : "ready", busy ? Yellow : Green);
    }

    void Ask(string text)
    {
        if (text.Trim().Equals("/stop", StringComparison.OrdinalIgnoreCase)) { InterruptAssistant(); return; }
        Say("You: " + text, Green);
        pending.Enqueue(text);
        if (busy) Status("working; your message goes next (Stop interrupts)", Dim);
        FlushPending();
    }

    static readonly string DropDir = Path.Combine(Root, "Drop");

    void DropFiles(string[] paths)
    {
        if (paths == null || paths.Length == 0) return;
        Directory.CreateDirectory(DropDir);
        var names = new List<string>();
        foreach (string src in paths)
        {
            try
            {
                string dst = Path.Combine(DropDir, Path.GetFileName(src.TrimEnd('\\')));
                if (Directory.Exists(src)) CopyFolder(src, dst);
                else File.Copy(src, dst, true);
                names.Add(Path.GetFileName(dst));
            }
            catch (Exception ex) { Say("Could not copy " + src + ": " + ex.Message, Red); }
        }
        if (names.Count > 0)
            Ask("I dropped " + string.Join(", ", names) + " into the drop folder. Look at " + (names.Count == 1 ? "it" : "them") + " (read_drop) and tell me what " + (names.Count == 1 ? "it is" : "they are") + " and anything useful for what we're doing.");
    }

    static void CopyFolder(string src, string dst)
    {
        Directory.CreateDirectory(dst);
        foreach (string f in Directory.GetFiles(src)) File.Copy(f, Path.Combine(dst, Path.GetFileName(f)), true);
        foreach (string d in Directory.GetDirectories(src)) CopyFolder(d, Path.Combine(dst, Path.GetFileName(d)));
    }

    void SendInput()
    {
        string text = input.Text.Trim();
        if (text.Length == 0) return;
        input.Clear();
        Ask(text);
    }

    // Only one message goes to the assistant at a time: while it works, it reads its input only for Yes/No answers.
    void FlushControl()
    {
        if (busy || autoSent) return;
        SendJson(new Dictionary<string, object> { { "t", "auto" }, { "on", autoBox.Checked } });
        autoSent = true;
    }

    void FlushPending()
    {
        FlushControl();
        if (busy || pending.Count == 0) return;
        busy = true; newChat.Enabled = false;
        SendJson(new Dictionary<string, object> { { "t", "user" }, { "text", pending.Dequeue() } });
        Status("thinking...", Cyan);
    }

    void Answer(object yes)
    {
        askPanel.Visible = false;
        SendJson(new Dictionary<string, object> { { "yes", yes } });
        Say(yes is bool ? ((bool)yes ? "  (you: yes)" : "  (you: no)") : "  (you: yes to everything from now)", Dim, true);
        Status("working...", Cyan);
    }

    void FromAssistant(string line)
    {
        Dictionary<string, object> m;
        try { m = json.Deserialize<Dictionary<string, object>>(line); }
        catch { Say(line, Dim, true); return; }
        string t = Get(m, "t"), text = Get(m, "text");
        switch (t)
        {
            case "line":
                string c = Get(m, "c");
                Say(text, c == "cyan" ? Cyan : c == "green" ? Green : c == "yellow" ? Yellow : Dim, true);
                break;
            case "status": Status(text, Dim); break;
            case "activity":
                activity.AppendText(Clean(text) + "\n");
                if (activity.TextLength > 400000) { activity.Select(0, activity.TextLength - 300000); activity.SelectedText = ""; }
                break;
            case "tool": Say(text, Dim, true); break;
            case "plan": Say(text, Cyan, true); break;
            case "ready": busy = false; newChat.Enabled = true; Status(text, Green); FlushPending(); OfferDailyPrCheck(); break;
            case "ask":
                if (!ContainsFocus) Notify("The assistant needs your OK", Get(m, "what"));
                askText.Text = "The assistant wants to:\n" + Clean(Get(m, "what"));
                askPanel.Visible = true; Status("waiting for your OK", Yellow);
                if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                break;
            case "auto": autoBox.Checked = Get(m, "on") == "True"; autoSent = true; break;
            case "answer":
                Say("Assistant: " + text + "\n", Fg);
                if (speakBox.Checked) Speak(text);
                busy = false; newChat.Enabled = true; Status("ready", Green);
                FlushPending();
                break;
            case "job": Say(text, Green); Notify("AnyPS5 assistant", text); break;
            default: Say(line, Dim, true); break;
        }
    }
}
