// The AI Team tab (Blu 11-04): the whole Claude team and the local AI drawn live for Discord viewers, and the place to
// drive the pipeline from: Claude fix or experiment for a game, pause and resume, and a team activity on any task.
// It reads _tools\repair-runs\team-live.json (claude_bridge.py writes what each member does), the pipeline's log and
// state; it writes only requests.txt lines and activity task files, so the pipeline and the bridge stay in charge.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class TeamView : UserControl
{
    class Member { public string Kind, Name, Model, Icon; public Color Color; public RectangleF Box; }

    static readonly Member[] Roster =
    {
        new Member { Kind = "boss", Name = "Boss", Model = "Fable 5.1", Icon = "\U0001F451", Color = Color.FromArgb(245, 197, 66) },
        new Member { Kind = "build", Name = "Builder", Model = "Sonnet 5.5", Icon = "\U0001F528", Color = Color.FromArgb(255, 140, 66) },
        new Member { Kind = "fix", Name = "Fixer", Model = "Sonnet 5.5", Icon = "\U0001F527", Color = Color.FromArgb(74, 163, 255) },
        new Member { Kind = "exp", Name = "Experimenter", Model = "Sonnet 5.5", Icon = "\U0001F9EA", Color = Color.FromArgb(176, 107, 255) },
        new Member { Kind = "repair", Name = "Repairer", Model = "Sonnet 5.5", Icon = "⚙", Color = Color.FromArgb(46, 211, 183) },
        new Member { Kind = "coach", Name = "Teacher", Model = "Sonnet 5.5", Icon = "\U0001F4D8", Color = Color.FromArgb(91, 214, 91) },
        new Member { Kind = "supervise", Name = "Watcher", Model = "Haiku 5.5", Icon = "\U0001F441", Color = Color.FromArgb(255, 216, 74) },
        new Member { Kind = "ask", Name = "Helper", Model = "Haiku 5.5", Icon = "\U0001F4AC", Color = Color.FromArgb(255, 111, 174) },
        new Member { Kind = "pr", Name = "Ambassador", Model = "Opus 5.5", Icon = "\U0001F4DC", Color = Color.FromArgb(38, 166, 154) },
        new Member { Kind = "local", Name = "Local AI", Model = "Qwen3-Coder 30B", Icon = "\U0001F916", Color = Color.FromArgb(143, 163, 191) },
    };

    static readonly Color Bg = Color.FromArgb(14, 16, 22), Panel = Color.FromArgb(24, 27, 34), Fg = Color.FromArgb(225, 229, 236), Dim = Color.FromArgb(130, 138, 150);
    readonly string tools, runs, livePath, logPath, statePath, requestsPath;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    readonly Func<bool> paused; readonly Action pause, resume;
    readonly Timer anim = new Timer { Interval = 50 }, data = new Timer { Interval = 2000 };
    readonly Canvas canvas = new Canvas();
    readonly ListView gameList = new ListView { View = View.Details, FullRowSelect = true, HideSelection = false, BackColor = Color.FromArgb(24, 27, 34), ForeColor = Color.FromArgb(225, 229, 236), BorderStyle = BorderStyle.None, Dock = DockStyle.Fill, HeaderStyle = ColumnHeaderStyle.Nonclickable };
    readonly ListBox feed = new ListBox { Dock = DockStyle.Fill, BackColor = Color.FromArgb(18, 20, 27), ForeColor = Color.FromArgb(200, 206, 216), BorderStyle = BorderStyle.None, Font = new Font("Consolas", 9f), IntegralHeight = false };
    readonly TextBox task = new TextBox { Multiline = true, Dock = DockStyle.Top, Height = 70, BackColor = Color.FromArgb(30, 34, 42), ForeColor = Color.FromArgb(225, 229, 236), BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 10f) };
    readonly TextBox answer = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill, BackColor = Color.FromArgb(20, 23, 29), ForeColor = Color.FromArgb(225, 229, 236), BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 9.5f) };
    readonly Button pauseButton = new Button();
    Dictionary<string, object> liveState = new Dictionary<string, object>();
    double phase;
    readonly Font headFont = new Font("Segoe UI Semibold", 10f);
    int dataTicks;
    string selected = "";

    readonly bool popout;
    readonly Panel side = new Panel(), bottomPanel = new Panel(), topPanel = new Panel();
    Kingdom2D town;
    string viewMode = "2d";
    bool townMode { get { return viewMode == "2d"; } }
    Dictionary<string, object> simState = new Dictionary<string, object>();
    readonly Dictionary<string, Button> viewButtons = new Dictionary<string, Button>();
    Point dragFrom; bool dragging, dragged;
    DateTime lastFrame = DateTime.Now;
    readonly ListBox townChat = new ListBox { Dock = DockStyle.Fill, BackColor = Color.FromArgb(18, 20, 27), ForeColor = Color.FromArgb(210, 216, 226), BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 9f), IntegralHeight = false };
    readonly List<Tuple<string, string, string>> gamesInfo = new List<Tuple<string, string, string>>();
    List<string> facts = new List<string>();
    int factsAt = -100;
    readonly Random rnd = new Random();
    readonly TabControl sideTabs = new TabControl();

    // popout: its own big window for a Discord screen share (F11 full screen); otherwise the AI Team tab in the app.
    // Both scale with the space: the side panel and the feed shrink, and a short board lays the team out in a row.
    public TeamView(string toolsDir, Func<bool> isPaused, Action doPause, Action doResume, bool asPopout = false)
    {
        tools = toolsDir; runs = Path.Combine(tools, "repair-runs"); livePath = Path.Combine(runs, "team-live.json");
        logPath = Path.Combine(tools, "auto-games.log"); statePath = Path.Combine(tools, "auto-state.json"); requestsPath = Path.Combine(tools, "requests.txt");
        paused = isPaused; pause = doPause; resume = doResume; popout = asPopout;
        BackColor = Bg; Dock = DockStyle.Fill; Font = new Font("Segoe UI", popout ? 10.5f : 9f);

        sideTabs.Dock = DockStyle.Fill; sideTabs.Appearance = TabAppearance.FlatButtons; sideTabs.ItemSize = new Size(110, 24); sideTabs.SizeMode = TabSizeMode.Fixed;
        var gamesPage = new TabPage("Games") { BackColor = Panel, Padding = new Padding(4) };
        gameList.Columns.Add("Game", 160); gameList.Columns.Add("Status", 100);
        var gameButtons = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 38, BackColor = Panel, WrapContents = false };
        gameButtons.Controls.Add(MakeButton("Claude fix", (s, e) => GameRequest("claudefix", "Claude Code tries a fix for")));
        gameButtons.Controls.Add(MakeButton("Experiment", (s, e) => GameRequest("experiment", "the Experimenter writes code for")));
        gameList.DoubleClick += (s, e) => { var g = SelectedGame(); if (g != null && Directory.Exists(g.Item2)) Process.Start("explorer.exe", "\"" + g.Item2 + "\""); };
        gamesPage.Controls.Add(gameList); gamesPage.Controls.Add(gameButtons);
        var activityPage = new TabPage("Team activity") { BackColor = Panel, Padding = new Padding(4) };
        var start = MakeButton("Start team activity", (s, e) => StartActivity()); start.Dock = DockStyle.Top; start.Height = 30; start.AutoSize = false;
        task.Height = 54;
        activityPage.Controls.Add(answer); activityPage.Controls.Add(start); activityPage.Controls.Add(task); activityPage.Controls.Add(Title("Any task or question for the whole team:"));
        sideTabs.TabPages.Add(gamesPage); sideTabs.TabPages.Add(activityPage);
        side.Dock = DockStyle.Right; side.BackColor = Panel; side.Padding = new Padding(6); side.Controls.Add(sideTabs);

        bottomPanel.Dock = DockStyle.Bottom; bottomPanel.BackColor = Panel; bottomPanel.Padding = new Padding(8, 2, 8, 6);
        var bottomTabs = new TabControl { Dock = DockStyle.Fill, Appearance = TabAppearance.FlatButtons, ItemSize = new Size(110, 22), SizeMode = TabSizeMode.Fixed };
        var feedPage = new TabPage("Live feed") { BackColor = Panel }; feedPage.Controls.Add(feed);
        var chatPage = new TabPage("Town chat") { BackColor = Panel }; chatPage.Controls.Add(townChat);
        bottomTabs.TabPages.Add(chatPage); bottomTabs.TabPages.Add(feedPage);
        bottomPanel.Controls.Add(bottomTabs);

        topPanel.Dock = DockStyle.Top; topPanel.Height = 38; topPanel.BackColor = Panel;
        pauseButton.FlatStyle = FlatStyle.Flat; pauseButton.ForeColor = Fg; pauseButton.BackColor = Color.FromArgb(44, 48, 56); pauseButton.Width = 92; pauseButton.Height = 30;
        pauseButton.Dock = DockStyle.Right; pauseButton.Click += (s, e) => { if (paused()) resume(); else pause(); UpdatePauseButton(); };
        topPanel.Controls.Add(pauseButton);
        // Blu 11-04: the kingdom lives inside the window (and the Discord pop-out), 2D and 3D, not in a window of its own.
        foreach (var v in new[] { Tuple.Create("board", "Board", 60), Tuple.Create("3d", "3D kingdom", 92), Tuple.Create("2d", "2D kingdom", 92) })
        {
            string mode = v.Item1;
            var b = MakeButton(v.Item2, (s, e) => SetView(mode)); b.Dock = DockStyle.Right; b.AutoSize = false; b.Width = v.Item3;
            viewButtons[mode] = b; topPanel.Controls.Add(b);
        }
        if (!popout)
        {
            var pop = MakeButton("↗ Discord", (s, e) => OpenPopout()); pop.Dock = DockStyle.Right; pop.AutoSize = false; pop.Width = 84;
            topPanel.Controls.Add(pop);
        }

        canvas.Dock = DockStyle.Fill; canvas.Paint += PaintTeam; canvas.MouseClick += ClickTeam; canvas.Cursor = Cursors.Hand;
        canvas.MouseDown += (s, e) => { if (e.Button == MouseButtons.Left) { dragging = true; dragged = false; dragFrom = e.Location; canvas.Focus(); } };
        canvas.MouseMove += (s, e) =>
        {
            if (!dragging || !townMode) return;
            int dx = e.X - dragFrom.X, dy = e.Y - dragFrom.Y;
            if (!dragged && Math.Abs(dx) + Math.Abs(dy) < 5) return;
            dragged = true; town.Drag(dx, dy); dragFrom = e.Location;
        };
        canvas.MouseUp += (s, e) => dragging = false;
        canvas.MouseEnter += (s, e) => canvas.Focus();
        canvas.MouseWheel += (s, e) => { if (townMode) town.Wheel(e.Location, e.Delta); };
        canvas.DoubleClick += (s, e) => { if (townMode) town.Director(); };
        canvas.Resize += (s, e) => Fit3D();
        HandleDestroyed += (s, e) => { if (host3d == canvas) Stop3D(); };
        Controls.Add(canvas); Controls.Add(topPanel); Controls.Add(side); Controls.Add(bottomPanel);
        topPanel.Paint += PaintHeader;
        Resize += (s, e) => FitPanels();
        FitPanels();

        town = new Kingdom2D(tools, key => key == "king" ? Color.FromArgb(214, 64, 64) : (Roster.FirstOrDefault(m => m.Kind == key) ?? Roster[0]).Color);
        SetView("2d");
        anim.Tick += (s, e) =>
        {
            phase += 0.05;
            var now = DateTime.Now; double dt = Math.Min(0.2, (now - lastFrame).TotalSeconds); lastFrame = now;
            if (canvas.Width > 100) town.Update(dt, new RectangleF(0, 0, canvas.Width, canvas.Height), Get(simState, "members"), liveState, NextFact);
            if (viewMode != "3d" || host3d != canvas) canvas.Invalidate();
        };
        data.Tick += (s, e) => RefreshData();
        anim.Start(); data.Start(); RefreshData();
    }

    class Canvas : Panel { public Canvas() { DoubleBuffered = true; ResizeRedraw = true; } }

    void FitPanels()
    {
        side.Width = Math.Max(240, Math.Min(popout ? 420 : 340, Width / 4));
        bottomPanel.Height = Math.Max(70, Math.Min(popout ? 170 : 110, Height / 5));
        feed.Font = new Font("Consolas", popout ? 10f : 8.5f);
        sideTabs.ItemSize = new Size(Math.Max(80, (side.Width - 36) / 2), 24);
        if (gameList.Columns.Count == 2) { gameList.Columns[1].Width = 90; gameList.Columns[0].Width = Math.Max(100, side.Width - 130); }
    }

    void OpenPopout()
    {
        var f = new Form { Text = "AnyPS5 - AI Team Live", BackColor = Bg, Width = 1600, Height = 900, StartPosition = FormStartPosition.CenterScreen, KeyPreview = true };
        f.Controls.Add(new TeamView(tools, paused, pause, resume, true));
        var normal = FormBorderStyle.Sizable;
        f.KeyDown += (s, e) =>
        {
            if (e.KeyCode != Keys.F11) return;
            if (f.FormBorderStyle == FormBorderStyle.None) { f.FormBorderStyle = normal; f.WindowState = FormWindowState.Normal; }
            else { f.FormBorderStyle = FormBorderStyle.None; f.WindowState = FormWindowState.Maximized; }
        };
        f.Show();
    }

    void SetView(string mode)
    {
        if (mode == "3d" && !Start3D()) mode = viewMode == "3d" ? "2d" : viewMode;
        if (mode != "3d" && host3d == canvas) Stop3D();
        viewMode = mode;
        foreach (var kv in viewButtons) { kv.Value.BackColor = kv.Key == mode ? Color.FromArgb(70, 90, 130) : Color.FromArgb(44, 48, 56); }
        canvas.Cursor = mode == "board" ? Cursors.Hand : Cursors.SizeAll;
        canvas.Invalidate();
    }

    // Blu 11-04: the 3D kingdom (Godot, live: the court walks and talks as the team really does, with the director
    // camera) runs inside this view, so it is in the window and in the Discord pop-out. One copy runs at a time; the view
    // that asks last shows it. While it runs the pipeline waits (it holds _tools\live\gpu-busy.flag), so it never skews a
    // game test; leaving the 3D view closes it.
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int index);
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern int SetWindowLong(IntPtr h, int index, int value);
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hgt, bool repaint);
    static Process kingdom3d;
    static IntPtr window3d = IntPtr.Zero;
    static Control host3d;
    static string flag3d = "", mine3d = "";
    static Timer keep3d;

    bool Start3D()
    {
        if (kingdom3d != null && !kingdom3d.HasExited)
        {
            host3d = canvas;
            if (window3d != IntPtr.Zero) { SetParent(window3d, canvas.Handle); Fit3D(); }
            return true;
        }
        string godot = @"C:\AnyPS5-work\engine\godot-4.7.2\Godot_v4.7.2-stable_win64.exe", project = @"C:\AnyPS5-work\kingdom\godot";
        if (!File.Exists(godot) || !Directory.Exists(project)) { MessageBox.Show(this, "The 3D kingdom is not installed (" + project + ").", "AnyPS5 Games"); return false; }
        string games = Path.GetDirectoryName(tools);
        bool gameRunning = Process.GetProcesses().Any(p => { try { var f = p.MainModule.FileName; return f.StartsWith(games + "\\", StringComparison.OrdinalIgnoreCase) && f.IndexOf(@"\_tools\", StringComparison.OrdinalIgnoreCase) < 0 && !p.ProcessName.StartsWith("AnyPS5"); } catch { return false; } });
        if (gameRunning && MessageBox.Show(this, "A game is running (you or a pipeline test). The 3D kingdom uses the graphics card too and would slow it.\n\nShow it anyway?", "AnyPS5 Games", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return false;
        flag3d = Path.Combine(tools, "live", "gpu-busy.flag"); mine3d = Process.GetCurrentProcess().Id + " " + DateTime.Now.ToString("HH:mm:ss") + " Blu is watching the 3D kingdom";
        bool held = false;
        try { using (var fs = new FileStream(flag3d, FileMode.CreateNew, FileAccess.Write)) { var b = Encoding.UTF8.GetBytes(mine3d); fs.Write(b, 0, b.Length); } held = true; } catch (IOException) { }
        if (!held) mine3d = "";
        int w = Math.Max(320, canvas.Width), h = Math.Max(200, canvas.Height);
        kingdom3d = Process.Start(new ProcessStartInfo(godot, "--path \"" + project + "\" --resolution " + w + "x" + h + " -- --live --tour") { UseShellExecute = false, WorkingDirectory = project });
        window3d = IntPtr.Zero; host3d = canvas;
        int tries = 0;
        keep3d = new Timer { Interval = 250 };
        keep3d.Tick += (s, e) =>
        {
            if (kingdom3d == null || kingdom3d.HasExited) { Release3D(); return; }
            if (window3d == IntPtr.Zero)
            {
                kingdom3d.Refresh();
                if (kingdom3d.MainWindowHandle != IntPtr.Zero)
                {
                    window3d = kingdom3d.MainWindowHandle;
                    const int GWL_STYLE = -16, WS_CHILD = 0x40000000, WS_VISIBLE = 0x10000000, CHROME = unchecked((int)0x80000000) | 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000;
                    SetWindowLong(window3d, GWL_STYLE, (GetWindowLong(window3d, GWL_STYLE) & ~CHROME) | WS_CHILD | WS_VISIBLE);
                    if (host3d != null && !host3d.IsDisposed) SetParent(window3d, host3d.Handle);
                    Fit3D();
                    keep3d.Interval = 60000;
                }
                else if (++tries > 240) { Stop3D(); }
                return;
            }
            try { if (mine3d != "") File.SetLastWriteTime(flag3d, DateTime.Now); } catch { }
        };
        keep3d.Start();
        return true;
    }

    void Fit3D() { if (window3d != IntPtr.Zero && host3d == canvas) MoveWindow(window3d, 0, 0, canvas.ClientSize.Width, canvas.ClientSize.Height, true); }

    static void Stop3D()
    {
        try { if (kingdom3d != null && !kingdom3d.HasExited) kingdom3d.Kill(); } catch { }
        Release3D();
    }

    static void Release3D()
    {
        if (keep3d != null) { keep3d.Stop(); keep3d.Dispose(); keep3d = null; }
        try { if (mine3d != "" && File.ReadAllText(flag3d) == mine3d) File.Delete(flag3d); } catch { }
        mine3d = ""; kingdom3d = null; window3d = IntPtr.Zero; host3d = null;
    }

    static Label Title(string text) { return new Label { Text = text, Dock = DockStyle.Top, Height = 24, ForeColor = Color.FromArgb(130, 138, 150), Font = new Font("Segoe UI Semibold", 8.5f), TextAlign = ContentAlignment.MiddleLeft }; }

    static Button MakeButton(string text, EventHandler click)
    {
        var b = new Button { Text = text, AutoSize = true, Height = 30, FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(44, 48, 56), ForeColor = Color.FromArgb(225, 229, 236), Margin = new Padding(3) };
        b.FlatAppearance.BorderColor = Color.FromArgb(70, 76, 88); b.Click += click; return b;
    }

    // ---------------------------------------------------------------- data
    void RefreshData()
    {
        dataTicks++;
        try { if (File.Exists(livePath)) liveState = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(livePath)) ?? liveState; } catch { }
        // The team's conversations (talk.py) for the town: what they say is their real messages.
        try { var sp = Path.Combine(runs, "realm-sim.json"); if (File.Exists(sp)) simState = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(sp)) ?? simState; } catch { }
        try { var tp = Path.Combine(runs, "threads.json"); if (File.Exists(tp)) town.Hear(json.Deserialize<List<Dictionary<string, object>>>(File.ReadAllText(tp)) ?? new List<Dictionary<string, object>>()); } catch { }
        RefreshFeed();
        if (dataTicks % 5 == 1) RefreshGames();
        RefreshActivity();
        UpdatePauseButton();
        Invalidate(true);
    }

    void RefreshFeed()
    {
        try
        {
            if (!File.Exists(logPath)) return;
            string tail;
            using (var f = new FileStream(logPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                long from = Math.Max(0, f.Length - 60000); f.Seek(from, SeekOrigin.Begin);
                tail = new StreamReader(f, Encoding.UTF8).ReadToEnd();
            }
            var events = tail.Split('\n').Select(l => l.TrimEnd('\r')).Where(l => l.Contains("  EVENT ")).Reverse().Take(40)
                             .Select(l => { int i = l.IndexOf("  EVENT "); return (l.Length >= 14 ? l.Substring(6, 8) : "") + "  " + l.Substring(i + 8); }).ToArray();
            if (feed.Items.Count == events.Length && (events.Length == 0 || (string)feed.Items[0] == events[0])) return;
            feed.BeginUpdate(); feed.Items.Clear(); feed.Items.AddRange(events); feed.EndUpdate();
        }
        catch { }
    }

    void RefreshGames()
    {
        try
        {
            if (!File.Exists(statePath)) return;
            var state = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(statePath));
            string keep = SelectedGame() != null ? SelectedGame().Item1 : null;
            gameList.BeginUpdate(); gameList.Items.Clear(); gamesInfo.Clear();
            foreach (var pair in state.OrderBy(p => p.Key))
            {
                var e = pair.Value as Dictionary<string, object>; if (e == null) continue;
                string dir = Str(e, "gameDir"); if (dir == "") continue;
                string note = Str(e, "note"), status;
                if (note.StartsWith("runs:")) status = "runs";
                else if (note.StartsWith("experimental:")) status = "runs (AI code)";
                else if (note.StartsWith("stops later")) status = "stops later";
                else if (Str(e, "status") == "built") status = "built, to test";
                else if (e.ContainsKey("failure") && e["failure"] is Dictionary<string, object>) status = "being fixed";
                else status = Str(e, "status");
                gamesInfo.Add(Tuple.Create(Path.GetFileName(dir), status, note));
                var item = new ListViewItem(new[] { Path.GetFileName(dir), status }) { Tag = Tuple.Create(pair.Key, dir) };
                item.ForeColor = status.StartsWith("runs") ? Color.FromArgb(87, 199, 133) : status == "being fixed" || status == "stops later" ? Color.FromArgb(240, 190, 80) : Fg;
                item.ToolTipText = note;
                gameList.Items.Add(item);
                if (pair.Key == keep) item.Selected = true;
            }
            gameList.EndUpdate(); gameList.ShowItemToolTips = true;
        }
        catch { }
    }

    void RefreshActivity()
    {
        var act = Get(liveState, "activity");
        if (act == null || !act.ContainsKey("task")) return;
        var sb = new StringBuilder();
        sb.Append("Task: ").Append(Str(act, "task")).Append("\r\n").Append("Status: ").Append(Str(act, "status")).Append("\r\n");
        if (Str(act, "why") != "") sb.Append("Plan: ").Append(Str(act, "why")).Append("\r\n");
        var parts = act.ContainsKey("parts") ? act["parts"] as ArrayList : null;
        if (parts != null) foreach (var p in parts.OfType<Dictionary<string, object>>())
                sb.Append("  ").Append(Str(p, "status") == "done" ? "[done] " : Str(p, "status") == "working" ? "[working] " : "[waiting] ").Append(Str(p, "member")).Append(": ").Append(Str(p, "task")).Append("\r\n");
        if (Str(act, "answer") != "") sb.Append("\r\nANSWER\r\n").Append(Str(act, "answer").Replace("\n", "\r\n"));
        string text = sb.ToString();
        if (answer.Text != text) answer.Text = text;
    }

    void UpdatePauseButton() { bool p = paused(); pauseButton.Text = p ? "▶ Resume" : "⏸ Pause"; pauseButton.ForeColor = p ? Color.FromArgb(240, 190, 80) : Fg; }

    static Dictionary<string, object> Get(Dictionary<string, object> d, string key) { object v; return d != null && d.TryGetValue(key, out v) ? v as Dictionary<string, object> : null; }
    static string Str(Dictionary<string, object> d, string key) { object v; return d != null && d.TryGetValue(key, out v) && v != null ? v.ToString() : ""; }

    Tuple<string, string> SelectedGame() { return gameList.SelectedItems.Count == 0 ? null : gameList.SelectedItems[0].Tag as Tuple<string, string>; }

    void GameRequest(string what, string label)
    {
        var g = SelectedGame();
        if (g == null) { MessageBox.Show(this, "Pick a game in the list first.", "AI Team"); return; }
        try { File.AppendAllText(requestsPath, what + " " + g.Item1 + "\r\n"); MessageBox.Show(this, "Queued: " + label + " " + Path.GetFileName(g.Item2) + ". The pipeline takes it at its next pass; watch the team and the feed.", "AI Team"); }
        catch (Exception ex) { MessageBox.Show(this, "Could not queue it: " + ex.Message, "AI Team"); }
    }

    void StartActivity()
    {
        string text = task.Text.Trim();
        if (text.Length < 5) { MessageBox.Show(this, "Write the task or question for the team first.", "AI Team"); return; }
        var act = Get(liveState, "activity");
        string st = Str(act, "status");
        if (st == "planning" || st == "working" || st == "answering") { MessageBox.Show(this, "The team is still on the last activity. Wait for its answer.", "AI Team"); return; }
        try
        {
            string dir = Path.Combine(runs, "activities"); Directory.CreateDirectory(dir);
            string file = Path.Combine(dir, "task-" + DateTime.Now.ToString("yyyy-MM-dd-HHmmss") + ".txt");
            File.WriteAllText(file, text);
            var psi = new ProcessStartInfo("python", "-I \"" + Path.Combine(tools, "assistant", "claude_bridge.py") + "\" activity \"" + file + "\"") { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.Combine(tools, "assistant") };
            Process.Start(psi);
            answer.Text = "Task: " + text + "\r\nStatus: starting (the boss plans it)...";
            task.Text = "";
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not start it: " + ex.Message, "AI Team"); }
    }

    string NextFact()
    {
        if (dataTicks - factsAt >= 15 || facts.Count == 0) { factsAt = dataTicks; facts = BuildFacts(); }
        return facts.Count == 0 ? "" : facts[rnd.Next(facts.Count)];
    }

    // Town talk is made from what the pipeline really knows: game results, the stops the team found, the functions
    // games still need, the MVP, the spend, the team's own notes and the latest events.
    List<string> BuildFacts()
    {
        var list = new List<string>();
        foreach (var gi in gamesInfo)
        {
            string name = System.Text.RegularExpressions.Regex.Replace(gi.Item1, @"\s*\(PPSA\d+\)", "");
            var fps = System.Text.RegularExpressions.Regex.Match(gi.Item3, @"FPS: ([\d.]+)");
            if (gi.Item2.StartsWith("runs") && fps.Success) list.Add(name + " runs at " + fps.Groups[1].Value + " FPS on AnyPS5!");
            else if (gi.Item2.StartsWith("runs")) list.Add(name + " boots and runs on AnyPS5.");
            else if (gi.Item2 == "stops later") list.Add(name + " runs a while, then " + Clip(gi.Item3.Replace("stops later: ", "stops on "), 80));
            else if (gi.Item2 == "being fixed") list.Add("we're still working on " + name + ".");
        }
        try
        {
            string notes = Path.Combine(runs, "team-notes.md");
            if (File.Exists(notes)) foreach (var l in File.ReadAllLines(notes).Reverse().Take(8)) { int c = l.IndexOf(": ", 14); if (c > 0) list.Add("a teammate noted: " + Clip(l.Substring(c + 2), 90)); }
            string pred = Path.Combine(Path.GetDirectoryName(tools), "_reports", "predicted-missing.md");
            if (File.Exists(pred))
                foreach (var l in File.ReadAllLines(pred).Where(x => x.StartsWith("| ") && char.IsDigit(x.Length > 2 ? x[2] : ' ')).Take(8))
                {
                    var cells = l.Trim('|').Split('|').Select(x => x.Trim()).ToArray();
                    if (cells.Length > 4) list.Add(cells[1] + " (" + cells[2] + ") is wanted by " + cells[4] + " games.");
                }
        }
        catch { }
        foreach (var ev in feed.Items.Cast<string>().Take(12))
        {
            string t = ev.Length > 10 ? ev.Substring(10) : ev;
            if (t.StartsWith("FIX PROVEN") || t.StartsWith("TESTED") || t.StartsWith("STOPS LATER") || t.StartsWith("CLAUDE") || t.StartsWith("AI TEAM") || t.StartsWith("BUILDER"))
                list.Add("news: " + Clip(t, 90));
        }
        var mvp = Get(liveState, "mvp");
        if (Str(mvp, "name") != "") list.Add(Str(mvp, "name") + " is MVP this week with " + Str(mvp, "points") + " points.");
        else list.Add("nobody has scored points this week yet. First game fixed wins MVP!");
        list.Add("there are " + gamesInfo.Count(x => x.Item2.StartsWith("runs")) + " games running on AnyPS5 right now.");
        return list;
    }

    static string Clip(string s, int n) { s = (s ?? "").Trim(); return s.Length <= n ? s : s.Substring(0, n - 1) + "\u2026"; }

    // ---------------------------------------------------------------- drawing
    void PaintHeader(object sender, PaintEventArgs e)
    {
        var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        var header = (Control)sender;
        int x = 12, room = header.ClientSize.Width - header.Controls.OfType<Button>().Sum(b => b.Width) - 12;
        using (var title = new Font("Segoe UI Semibold", 13f))
        {
            TextRenderer.DrawText(g, "AnyPS5  ·  AI TEAM LIVE", title, new Point(x, 8), Fg);
            x += TextRenderer.MeasureText("AnyPS5  ·  AI TEAM LIVE", title).Width + 24;
        }
        var mvp = Get(liveState, "mvp"); string mvpName = Str(mvp, "name");
        string mvpText = mvpName != "" ? "\U0001F3C6 MVP: " + mvpName + " (" + Str(mvp, "points") + " pts)" : "\U0001F3C6 MVP: nobody yet";
        if (x + TextRenderer.MeasureText(mvpText, Font).Width > room) return;
        TextRenderer.DrawText(g, mvpText, Font, new Point(x, 12), Color.FromArgb(245, 197, 66));
        x += TextRenderer.MeasureText(mvpText, Font).Width + 24;
        var spend = Get(liveState, "spend");
        double today = 0, cap = 3; double.TryParse(Str(spend, "today"), out today); double.TryParse(Str(spend, "cap"), out cap); if (cap <= 0) cap = 3;
        string money = string.Format("Claude today ${0:0.00} of ${1:0.00}", today, cap);
        int moneyWidth = TextRenderer.MeasureText(money, Font).Width, barWidth = Math.Max(40, Math.Min(140, room - x - moneyWidth - 12));
        if (x + moneyWidth + barWidth + 12 <= room)
        {
            TextRenderer.DrawText(g, money, Font, new Point(x, 12), Fg);
            var bar = new Rectangle(x + moneyWidth + 8, 15, barWidth, 10);
            using (var b = new SolidBrush(Color.FromArgb(40, 44, 54))) g.FillRectangle(b, bar);
            double f = Math.Min(1, today / cap);
            using (var b = new SolidBrush(f >= 1 ? Color.FromArgb(235, 100, 100) : f > 0.7 ? Color.FromArgb(240, 190, 80) : Color.FromArgb(87, 199, 133))) g.FillRectangle(b, bar.X, bar.Y, (int)(bar.Width * f), bar.Height);
        }
    }

    float scale = 1f;

    void PaintTeam(object sender, PaintEventArgs e)
    {
        if (townChat.Items.Count != town.Chat.Count) { townChat.BeginUpdate(); townChat.Items.Clear(); for (int i = town.Chat.Count - 1; i >= 0; i--) townChat.Items.Add(town.Chat[i]); townChat.EndUpdate(); }
        if (townMode) { town.Draw(e.Graphics, new RectangleF(0, 0, canvas.Width, canvas.Height), Font); return; }
        if (viewMode == "3d")
        {
            e.Graphics.Clear(Color.FromArgb(10, 12, 18));
            string say = host3d == canvas ? "Opening the 3D kingdom… (it takes about 20 seconds to build the realm)" : "The 3D kingdom is showing in the other window. Click 3D kingdom here to bring it back.";
            TextRenderer.DrawText(e.Graphics, say, headFont, new Rectangle(0, 0, canvas.Width, canvas.Height), Fg, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.WordBreak);
            return;
        }
        var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        var area = canvas.ClientRectangle; if (area.Width < 120 || area.Height < 80) return;
        using (var grid = new Pen(Color.FromArgb(22, 26, 34))) { for (int gx = 0; gx < area.Width; gx += 32) g.DrawLine(grid, gx, 0, gx, area.Height); for (int gy = 0; gy < area.Height; gy += 32) g.DrawLine(grid, 0, gy, area.Width, gy); }
        var members = Get(liveState, "members"); var board = Get(liveState, "board");
        string mvpName = Str(Get(liveState, "mvp"), "name");
        var boss = Roster[0]; var ring = Roster.Skip(1).ToArray();
        // A ring needs about 520 x 420 to keep 8 members and their labels apart; a shorter board puts the team in a row
        // (the boss first), with the labels under each member.
        bool row = area.Height < 420 || area.Width < 520;
        if (row)
        {
            float cell = area.Width / 9f;
            float r = Math.Max(14, Math.Min(cell / 2 - 10, (area.Height - 70) / 2f));
            scale = Math.Max(0.55f, Math.Min(1.1f, r / 38f));
            float cy = Math.Max(r + 20, area.Height / 2f - 20);
            boss.Box = new RectangleF(cell / 2 - r * 1.15f, cy - r * 1.15f, r * 2.3f, r * 2.3f);
            for (int i = 0; i < ring.Length; i++) ring[i].Box = new RectangleF(cell * (i + 1) + cell / 2 - r, cy - r, r * 2, r * 2);
            using (var p = new Pen(Color.FromArgb(50, 245, 197, 66), 2f)) g.DrawLine(p, Center(boss.Box).X, cy, Center(ring[ring.Length - 1].Box).X, cy);
            foreach (var m in ring)
                if (Str(Get(members, m.Kind), "status") == "working")
                    for (int k = 0; k < 3; k++)
                    {
                        double t = (phase * 0.6 + k / 3.0) % 1.0;
                        float x0 = Center(m.Box).X, x1 = Center(boss.Box).X;
                        using (var b = new SolidBrush(Color.FromArgb(220, m.Color))) g.FillEllipse(b, (float)(x0 + (x1 - x0) * t) - 3, cy - 3, 6, 6);
                    }
        }
        else
        {
            float cx = area.Width / 2f, cy = area.Height / 2f + 4;
            float r = Math.Max(26, Math.Min(46, Math.Min(area.Width, area.Height) / 13f));
            scale = r / 38f;
            float rx = area.Width / 2f - r - 95 * scale, ry = area.Height / 2f - r - 60 * scale;
            boss.Box = new RectangleF(cx - r * 1.35f, cy - r * 1.35f, r * 2.7f, r * 2.7f);
            for (int i = 0; i < ring.Length; i++)
            {
                double a = -Math.PI / 2 + (i + 0.5) * 2 * Math.PI / ring.Length;
                float mx = cx + (float)(rx * Math.Cos(a)), my = cy + (float)(ry * Math.Sin(a));
                ring[i].Box = new RectangleF(mx - r, my - r, r * 2, r * 2);
            }
            foreach (var m in ring)
            {
                string st = Str(Get(members, m.Kind), "status");
                var from = Center(m.Box); var to = Center(boss.Box);
                using (var p = new Pen(Color.FromArgb(st == "working" ? 120 : 45, m.Color), st == "working" ? 2.5f : 1.5f)) g.DrawLine(p, from, to);
                if (st == "working")
                    for (int k = 0; k < 4; k++)
                    {
                        double t = (phase * 0.6 + k / 4.0) % 1.0;
                        var pt = new PointF((float)(from.X + (to.X - from.X) * t), (float)(from.Y + (to.Y - from.Y) * t));
                        using (var b = new SolidBrush(Color.FromArgb(220, m.Color))) g.FillEllipse(b, pt.X - 4, pt.Y - 4, 8, 8);
                    }
            }
        }
        DrawMember(g, boss, Get(members, "boss"), Get(board, "boss"), mvpName == boss.Name, true, row);
        foreach (var m in ring) DrawMember(g, m, Get(members, m.Kind), Get(board, m.Kind), mvpName == m.Name, false, row);
        if (selected != "") DrawDetails(g, area, Roster.First(x => x.Kind == selected), Get(members, selected), Get(board, selected));
        else if (!row) TextRenderer.DrawText(g, "Click a member for its record", Font, new Point(12, area.Height - 22), Dim);
    }

    static PointF Center(RectangleF r) { return new PointF(r.X + r.Width / 2, r.Y + r.Height / 2); }

    static Font Sized(string family, float size, FontStyle style = FontStyle.Regular) { return new Font(family, Math.Max(6.5f, size), style); }

    void DrawMember(Graphics g, Member m, Dictionary<string, object> live, Dictionary<string, object> rec, bool isMvp, bool isBoss, bool row)
    {
        string st = Str(live, "status"); bool busy = st == "working" || st == "reviewing" || st == "planning";
        var r = m.Box; float s = scale;
        if (busy)
        {
            float pulse = (float)(0.5 + 0.5 * Math.Sin(phase * 4));
            for (int k = 3; k >= 1; k--)
                using (var b = new SolidBrush(Color.FromArgb((int)(28 * pulse * k), m.Color))) g.FillEllipse(b, r.X - k * 6 * s, r.Y - k * 6 * s, r.Width + k * 12 * s, r.Height + k * 12 * s);
        }
        using (var b = new SolidBrush(Color.FromArgb(30, 34, 44))) g.FillEllipse(b, r);
        using (var p = new Pen(busy ? m.Color : Color.FromArgb(150, m.Color), busy ? 3.5f * s : 2f)) g.DrawEllipse(p, r);
        using (var icon = Sized("Segoe UI Emoji", r.Width * 0.30f)) TextRenderer.DrawText(g, m.Icon, icon, Rectangle.Round(r), Color.White, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        if (isMvp) using (var crown = Sized("Segoe UI Emoji", 14f * s)) TextRenderer.DrawText(g, "\U0001F3C6", crown, new Point((int)(r.Right - 16 * s), (int)(r.Y - 12 * s)), Color.Gold);
        string lvl = m.Kind == "local" ? "learning" : "Lv" + Str(rec, "level") + (row ? "" : " " + Str(rec, "title"));
        using (var small = Sized("Segoe UI", 7.5f * s, FontStyle.Bold))
        {
            var sz = TextRenderer.MeasureText(lvl, small);
            var badge = new Rectangle((int)(r.X + r.Width / 2 - sz.Width / 2 - 3), (int)(r.Y - sz.Height + 2), sz.Width + 6, sz.Height);
            using (var b = new SolidBrush(Color.FromArgb(220, 20, 23, 30))) g.FillRectangle(b, badge);
            using (var p = new Pen(m.Color)) g.DrawRectangle(p, badge);
            TextRenderer.DrawText(g, lvl, small, badge, m.Color, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }
        int w = row ? (int)(canvas.ClientSize.Width / 9f) - 4 : (int)(190 * s), x = (int)(r.X + r.Width / 2 - w / 2), y = (int)r.Bottom + 3;
        using (var bold = Sized("Segoe UI Semibold", (isBoss ? 11f : 9.5f) * s))
        {
            TextRenderer.DrawText(g, m.Name, bold, new Rectangle(x, y, w, bold.Height + 2), m.Color, TextFormatFlags.HorizontalCenter | TextFormatFlags.EndEllipsis);
            y += bold.Height + 1;
        }
        using (var small = Sized("Segoe UI", 8f * s))
        {
            string pts = Str(rec, "points"); if (pts == "") pts = "0";
            int sCount = 0; int.TryParse(Str(rec, "strikes"), out sCount);
            string line = (row ? "" : m.Model + "  ·  ") + pts + " pts" + (sCount > 0 ? "  " + new string('✖', sCount) : "") + (busy || row ? "" : "  ·  idle");
            TextRenderer.DrawText(g, line, small, new Rectangle(x, y, w, small.Height + 2), sCount > 0 ? Color.FromArgb(235, 100, 100) : Dim, TextFormatFlags.HorizontalCenter | TextFormatFlags.EndEllipsis);
            y += small.Height + 1;
            if (!busy && !row) return;
            string doing = busy ? (st == "reviewing" ? "reviewing: " : st == "planning" ? "planning: " : "") + Str(live, "task") : "idle";
            TextRenderer.DrawText(g, doing, small, new Rectangle(x - (row ? 0 : 20), y, w + (row ? 0 : 40), small.Height * (row ? 2 : 1) + 2), busy ? Fg : Color.FromArgb(90, 96, 108), TextFormatFlags.HorizontalCenter | TextFormatFlags.WordBreak | TextFormatFlags.EndEllipsis);
        }
    }

    void DrawDetails(Graphics g, Rectangle area, Member m, Dictionary<string, object> live, Dictionary<string, object> rec)
    {
        var box = new Rectangle(12, area.Height - 132, 360, 120);
        using (var b = new SolidBrush(Color.FromArgb(235, 22, 25, 32))) g.FillRectangle(b, box);
        using (var p = new Pen(m.Color, 1.5f)) g.DrawRectangle(p, box);
        var lines = new List<string>
        {
            m.Icon + "  " + m.Name + "  (" + m.Model + ")",
            m.Kind == "local" ? "Never punished: it learns from the boss and the teacher" : "Level " + Str(rec, "level") + " " + Str(rec, "title") + "   standing: " + Str(rec, "standing"),
            "Points this week: " + (Str(rec, "points") == "" ? "0" : Str(rec, "points")) + "   passed: " + (Str(rec, "passes") == "" ? "0" : Str(rec, "passes")) + "   strikes: " + (Str(rec, "strikes") == "" ? "0" : Str(rec, "strikes")),
            "Now: " + (Str(live, "status") == "" || Str(live, "status") == "idle" ? "idle" : Str(live, "status") + " - " + Str(live, "task")),
            "Last verdict: " + (Str(rec, "last") == "" ? "none yet" : Str(rec, "last")),
        };
        for (int i = 0; i < lines.Count; i++)
            TextRenderer.DrawText(g, lines[i], i == 0 ? headFont : Font, new Rectangle(box.X + 10, box.Y + 8 + i * 21, box.Width - 20, 20), i == 0 ? m.Color : Fg, TextFormatFlags.EndEllipsis);
    }

    void ClickTeam(object sender, MouseEventArgs e)
    {
        if (townMode) { if (!dragged) town.Click(e.Location); return; }
        if (viewMode != "board") return;
        var hit = Roster.FirstOrDefault(m => { var c = Center(m.Box); float dx = e.X - c.X, dy = e.Y - c.Y; return dx * dx + dy * dy <= (m.Box.Width / 2 + 6) * (m.Box.Width / 2 + 6); });
        selected = hit == null || hit.Kind == selected ? "" : hit.Kind;
        canvas.Invalidate();
    }
}
