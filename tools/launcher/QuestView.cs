// The Quest desktop, live and controllable from the PC (Blu 11-07: "make it able so I can control it from the Windows
// PC ... click and launch games ... I wouldn't have to look through the Meta Quest"; "I don't want it through VNC, I
// want it live"; "remove the headset view"). scrcpy (C:\AnyPS5-tools\scrcpy) asks Android for a virtual display of
// its own, starts Termux:X11 (the X server showing the XFCE desktop, display :0) on it, and streams it as hardware
// H.264 video while the mouse and keyboard go back to the Quest. Its window is embedded in this panel. When the view
// stops, --no-vd-destroy-content moves Termux:X11 back into the headset instead of closing it. The status line reads
// live\quest-view.json, written by remote-view.ps1.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Windows.Forms;

class QuestView : Panel
{
    const string Scrcpy = @"C:\AnyPS5-tools\scrcpy\v5.0.1\scrcpy-win64-v5.0.1\scrcpy.exe";
    const string Adb = @"C:\AnyPS5-tools\platform-tools\adb.exe";
    const string Device = "192.0.2.1:5555";
    const string WindowTitle = "AnyPS5 Quest desktop";
    const string DesktopApp = "com.termux.x11";

    [DllImport("user32.dll")] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] static extern IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value);
    [DllImport("user32.dll")] static extern bool MoveWindow(IntPtr hwnd, int x, int y, int w, int h, bool repaint);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr SetFocus(IntPtr hwnd);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, IntPtr processId);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint attach, uint attachTo, bool on);
    const int GWL_STYLE = -16;
    const long WS_CHILD = 0x40000000, WS_POPUP = 0x80000000, WS_CAPTION = 0x00C00000, WS_THICKFRAME = 0x00040000, WS_VISIBLE = 0x10000000;

    readonly string liveDir;
    readonly Panel host = new Panel { Dock = DockStyle.Fill, BackColor = Color.Black };
    readonly Label note = new Label { Dock = DockStyle.Fill, ForeColor = Color.FromArgb(140, 146, 156), TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Segoe UI", 11f) };
    // Blu 11-07: "that UI text ... needs to be bigger ... clean up the application UI": one bar, the status large on
    // the left (a coloured dot, then what the Quest is doing in plain words), the few buttons on the right.
    static readonly Color BarColor = Color.FromArgb(24, 26, 30), Fg = Color.FromArgb(232, 234, 238), Dim = Color.FromArgb(150, 156, 166);
    readonly Label dot = new Label { Dock = DockStyle.Left, Width = 34, Text = "●", Font = new Font("Segoe UI", 15f), TextAlign = ContentAlignment.MiddleCenter, ForeColor = Color.FromArgb(110, 116, 126), BackColor = BarColor };
    readonly Label status = new Label { Dock = DockStyle.Fill, ForeColor = Fg, BackColor = BarColor, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true, Font = new Font("Segoe UI Semibold", 13f) };
    readonly ComboBox size = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 120, Margin = new Padding(6, 14, 10, 4), Font = new Font("Segoe UI", 10f), FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(44, 47, 53), ForeColor = Fg };
    readonly Timer timer = new Timer { Interval = 1000 };
    readonly Queue<string> lastLines = new Queue<string>();
    Process proc;
    IntPtr window = IntPtr.Zero;
    DateTime nextStart = DateTime.MinValue;
    int failures;
    bool showing;
    DateTime statusStarted = DateTime.MinValue;
    DateTime refit = DateTime.MaxValue;
    string runningSize = "";

    bool FitWindow() { return size.SelectedIndex == 0; }

    // The size the Quest desktop is created at: the panel's own size (even numbers, at least 640x360) when fitting.
    string DesktopSize()
    {
        if (!FitWindow()) return (string)size.SelectedItem;
        int w = Math.Max(640, host.ClientSize.Width) & ~1, h = Math.Max(360, host.ClientSize.Height) & ~1;
        return w + "x" + h;
    }

    public QuestView(string liveDir)
    {
        this.liveDir = liveDir;
        Dock = DockStyle.Fill; BackColor = Color.Black;
        var bar = new Panel { Dock = DockStyle.Top, Height = 52, BackColor = BarColor, Padding = new Padding(8, 0, 4, 0) };
        var actions = new FlowLayoutPanel { Dock = DockStyle.Right, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = false, BackColor = BarColor, Padding = new Padding(0, 0, 0, 0) };
        actions.Controls.Add(MakeButton("Reconnect", (s, e) => Restart()));
        actions.Controls.Add(MakeButton("Save image", (s, e) => SaveImage()));
        // Blu 11-07: snapped beside AnyPS5 Games the window is tall and narrow, and a fixed 1920x1080 desktop shrank to a
        // strip. "Fit window" (the default) gives the Quest desktop the panel's own size and follows every resize.
        size.Items.AddRange(new object[] { "Fit window", "1920x1080", "1600x900", "1280x720" });
        size.SelectedIndex = 0;
        host.Resize += (s, e) => { if (FitWindow()) refit = DateTime.Now.AddSeconds(1.5); };
        size.SelectedIndexChanged += (s, e) => Restart();
        size.DropDownClosed += (s, e) => FocusDesktop();
        size.TabStop = false;
        actions.Controls.Add(new Label { Text = "Desktop size", AutoSize = true, ForeColor = Dim, Font = new Font("Segoe UI", 10f), Margin = new Padding(14, 17, 0, 0) });
        actions.Controls.Add(size);
        bar.Controls.Add(status); bar.Controls.Add(dot); bar.Controls.Add(actions);
        note.Text = "Connecting to the Quest desktop...";
        host.Controls.Add(note);
        host.Resize += (s, e) => Fit();
        host.Click += (s, e) => FocusDesktop();
        // Blu 11-07: clicks reached the Quest but typing did not. Activating this window gave the keyboard back to its
        // own controls (the size list), so the embedded scrcpy window never got the keys; it now takes them on every
        // activation and after each button.
        ParentChanged += (s, e) => { var form = FindForm(); if (form != null) form.Activated += (s2, e2) => FocusDesktop(); };
        // Blu 11-07: at half the screen the buttons left the status a few words; below 1100 px it gets its own row.
        var statusRow = new Panel { Dock = DockStyle.Top, Height = 62, BackColor = BarColor, Padding = new Padding(8, 4, 8, 4), Visible = false };
        Controls.Add(host); Controls.Add(bar); Controls.Add(statusRow);
        Resize += (s, e) =>
        {
            bool narrow = Width > 0 && Width < 1100;
            if (narrow == statusRow.Visible) return;
            Control to = narrow ? (Control)statusRow : bar;
            to.Controls.Add(status); to.Controls.Add(dot);
            to.Controls.SetChildIndex(status, 0); to.Controls.SetChildIndex(dot, 1);
            status.AutoEllipsis = !narrow;
            statusRow.Visible = narrow;
        };
        timer.Tick += (s, e) => Tick();
        timer.Start();
        Application.ApplicationExit += (s, e) => Stop();
    }

    Button MakeButton(string text, EventHandler click)
    {
        var b = new Button { Text = text, AutoSize = true, Height = 34, FlatStyle = FlatStyle.Flat, ForeColor = Fg, BackColor = Color.FromArgb(44, 47, 53), Font = new Font("Segoe UI", 10f), Margin = new Padding(4, 9, 4, 4), Padding = new Padding(8, 0, 8, 0), TabStop = false };
        b.FlatAppearance.BorderColor = Color.FromArgb(70, 74, 82);
        b.Click += click;
        b.Click += (s, e) => FocusDesktop();
        return b;
    }

    // scrcpy's window belongs to another process, so focus can only move into it while the two input queues are joined.
    void FocusDesktop()
    {
        if (window == IntPtr.Zero || !IsWindow(window)) return;
        uint mine = GetCurrentThreadId(), theirs = GetWindowThreadProcessId(window, IntPtr.Zero);
        bool joined = theirs != 0 && theirs != mine && AttachThreadInput(mine, theirs, true);
        SetFocus(window);
        if (joined) AttachThreadInput(mine, theirs, false);
    }

    public void Show(bool on)
    {
        showing = on;
        if (!on) { Stop(); return; }
        nextStart = DateTime.MinValue;
    }

    void Restart()
    {
        Stop();
        failures = 0;
        nextStart = DateTime.MinValue;
    }

    void Tick()
    {
        ShowStatus();
        if (!showing) return;
        if (proc != null && proc.HasExited)
        {
            Log("scrcpy exited " + proc.ExitCode + ": " + string.Join(" | ", lastLines));
            failures++;
            int wait = Math.Min(30, 5 * failures);
            note.Text = "The Quest desktop stopped (" + LastLine() + ").\nTrying again in " + wait + " s. Is the Quest on and awake, on Wi-Fi adb (" + Device + ")?";
            note.Visible = true; note.BringToFront();
            proc = null; window = IntPtr.Zero;
            nextStart = DateTime.Now.AddSeconds(wait);
        }
        if (DateTime.Now >= refit)
        {
            refit = DateTime.MaxValue;
            if (proc != null && vnc) ApplyDesktopResolution();
            else if (proc != null && sharedDisplay == 0 && DesktopSize() != runningSize) { Log("refit the Quest desktop to " + DesktopSize()); Restart(); }
        }
        if (proc == null && DateTime.Now >= nextStart) Start();
        if (proc != null && window == IntPtr.Zero)
        {
            proc.Refresh();
            var h = vnc ? VncWindow() : proc.MainWindowHandle;
            if (h != IntPtr.Zero) Embed(h);
        }
        if (window != IntPtr.Zero && !IsWindow(window)) window = IntPtr.Zero;
    }

    // 11-07: a force-closed window left its scrcpy children running; three of them kept the headset encoding video
    // and held about 900 MB of the Quest's RAM. Any scrcpy started earlier is stopped before a new one.
    static void StopStrayScrcpy()
    {
        foreach (var p in Process.GetProcessesByName("scrcpy"))
        {
            try { p.Kill(); } catch { }
        }
    }

    int sharedDisplay;
    bool vnc;
    const string Vnc = @"C:\AnyPS5-tools\tigervnc\1.16.2\vncviewer64.exe";
    const string VncPassword = @"C:\AnyPS5-tools\tigervnc\quest\pc-passwd";

    void StartVnc()
    {
        if (!File.Exists(Vnc) || !File.Exists(VncPassword)) { note.Text = "TigerVNC or its password file is missing (" + Vnc + ")"; nextStart = DateTime.Now.AddSeconds(30); return; }
        foreach (var p in Process.GetProcessesByName("vncviewer64")) { try { p.Kill(); } catch { } }
        appliedResolution = "";
        ApplyDesktopResolution();
        string args = "-PasswordFile=\"" + VncPassword + "\" -Shared=1 -AlertOnFatalError=0 -ReconnectOnError=0 -DotWhenNoCursor=1 " +
            "-PreferredEncoding=Tight -QualityLevel=8 -CompressLevel=1 " + DeviceHost() + "::5903";
        try
        {
            proc = Process.Start(new ProcessStartInfo(Vnc, args) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetDirectoryName(Vnc) });
            vnc = true;
            runningSize = "vnc";
            note.Text = "Connecting to the AnyPS5 desktop that is open in the headset..."; note.Visible = true; note.BringToFront();
            Log("started TigerVNC " + args);
        }
        catch (Exception ex) { note.Text = "TigerVNC could not start: " + ex.Message; proc = null; nextStart = DateTime.Now.AddSeconds(30); }
    }

    // Blu 11-07: the desktop must fill this window, as the scrcpy view did. TigerVNC cannot scale, so the Quest's X
    // desktop itself takes the window's size ("Fit window") or the chosen size; the headset scales it into its panel.
    string appliedResolution = "";
    void ApplyDesktopResolution()
    {
        string wanted = DesktopSize();
        if (wanted == appliedResolution) return;
        appliedResolution = wanted;
        try
        {
            var info = new ProcessStartInfo("ssh", "-n -o BatchMode=yes -o ConnectTimeout=8 quest termux-x11-preference \"displayResolutionMode:custom\" \"displayResolutionCustom:" + wanted + "\"")
                { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
            using (var p = Process.Start(info)) { p.WaitForExit(15000); }
            Log("Quest desktop set to " + wanted);
        }
        catch (Exception ex) { Log("could not set the Quest desktop size: " + ex.Message); appliedResolution = ""; }
    }

    static string DeviceHost() { int colon = Device.IndexOf(':'); return colon > 0 ? Device.Substring(0, colon) : Device; }

    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);

    // TigerVNC's first window is a hidden helper; the desktop is the visible one titled "... - TigerVNC".
    IntPtr VncWindow()
    {
        IntPtr found = IntPtr.Zero;
        uint want = (uint)proc.Id;
        EnumWindows((h, l) =>
        {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (pid != want || !IsWindowVisible(h)) return true;
            var title = new System.Text.StringBuilder(256);
            GetWindowText(h, title, 256);
            if (title.ToString().Contains("TigerVNC")) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    // The headset panel (a display other than 0, the lens view) that holds the desktop app, or 0 when there is none.
    int FindDesktopDisplay()
    {
        string dump;
        try { dump = RunAdb("-s " + Device + " shell dumpsys activity activities", 15000); } catch { return 0; }
        int current = -1;
        foreach (var line in dump.Split('\n'))
        {
            var m = System.Text.RegularExpressions.Regex.Match(line, @"^\s*Display #(\d+)");
            if (m.Success) { current = int.Parse(m.Groups[1].Value); continue; }
            if (current >= 0 && line.Contains("Hist") && line.Contains(DesktopApp + "/")) return current == 0 ? -1 : current;
        }
        return 0;
    }

    static string RunAdb(string args, int timeoutMs)
    {
        var info = new ProcessStartInfo(Adb, args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        using (var p = Process.Start(info))
        {
            string output = p.StandardOutput.ReadToEnd();
            if (!p.WaitForExit(timeoutMs)) { try { p.Kill(); } catch { } }
            return output.Trim();
        }
    }

    void Start()
    {
        if (!File.Exists(Scrcpy)) { note.Text = "scrcpy is missing: " + Scrcpy; nextStart = DateTime.Now.AddSeconds(30); return; }
        StopStrayScrcpy();
        // Wi-Fi adb drops when the headset sleeps (seen 11-07); reconnecting is harmless when it is already there.
        try { RunAdb("connect " + Device, 10000); } catch { }
        // Blu 11-07 (streaming): the PC must show the same desktop he sees in the headset. When the desktop is open as
        // a panel in the headset, that panel's display is mirrored (both see and control the one desktop); only when it
        // is not open anywhere does Quest View make its own display for it.
        sharedDisplay = FindDesktopDisplay();
        // -1: the desktop is a panel in the headset's own view (display 0). scrcpy cannot cut that panel out of the
        // lens image, and making a display here would pull the desktop out of Blu's headset, so wait until he closes it.
        if (sharedDisplay < 0)
        {
            // Blu 11-07: "put the headset on and continue working": the same X desktop is shown here through the Quest's
            // password-protected x11vnc (:5903, start-x11.sh) while the AnyPS5 app shows it in the headset.
            StartVnc();
            return;
        }
        vnc = false;
        runningSize = sharedDisplay > 0 ? "panel" : DesktopSize();
        string target = sharedDisplay > 0 ? " --display-id=" + sharedDisplay
            : " --new-display=" + runningSize + " --start-app=" + DesktopApp + " --no-vd-destroy-content --no-vd-system-decorations";
        string args = "-s " + Device + target + " --no-audio --window-borderless --window-title \"" + WindowTitle + "\"" +
            " --max-fps 60 --video-bit-rate 12M --mouse-bind=++++:bhsn";
        var info = new ProcessStartInfo(Scrcpy, args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true, WorkingDirectory = Path.GetDirectoryName(Scrcpy) };
        info.EnvironmentVariables["ADB"] = Adb;
        try
        {
            proc = Process.Start(info);
            proc.ErrorDataReceived += (s, e) => Remember(e.Data);
            proc.OutputDataReceived += (s, e) => Remember(e.Data);
            proc.BeginErrorReadLine(); proc.BeginOutputReadLine();
            note.Text = "Connecting to the Quest desktop..."; note.Visible = true; note.BringToFront();
            Log("started scrcpy " + args);
        }
        catch (Exception ex) { note.Text = "scrcpy could not start: " + ex.Message; proc = null; nextStart = DateTime.Now.AddSeconds(30); }
    }

    void Remember(string line)
    {
        if (string.IsNullOrEmpty(line)) return;
        lock (lastLines) { lastLines.Enqueue(line.Trim()); while (lastLines.Count > 6) lastLines.Dequeue(); }
    }

    string LastLine() { lock (lastLines) { string last = ""; foreach (var l in lastLines) last = l; return last.Length > 120 ? last.Substring(0, 120) : last; } }

    void Embed(IntPtr h)
    {
        long style = (long)GetWindowLongPtr(h, GWL_STYLE);
        style = (style & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME)) | WS_CHILD | WS_VISIBLE;
        SetWindowLongPtr(h, GWL_STYLE, new IntPtr(style));
        SetParent(h, host.Handle);
        window = h;
        failures = 0;
        note.Visible = false;
        Fit();
        FocusDesktop();
        Log("embedded the Quest desktop");
    }

    void Fit()
    {
        if (window == IntPtr.Zero) return;
        MoveWindow(window, 0, 0, Math.Max(1, host.ClientSize.Width), Math.Max(1, host.ClientSize.Height), true);
    }

    void Stop()
    {
        if (proc != null) { try { if (!proc.HasExited) proc.Kill(); } catch { } proc = null; }
        window = IntPtr.Zero;
        note.Visible = true; note.Text = "Quest desktop stopped."; note.BringToFront();
    }

    // remote-view.ps1 writes the status file; the AnyPS5 Games window used to be the only thing starting it, so the
    // line went stale whenever this window ran alone (11-07). It keeps one copy running itself, so starting it again
    // while another is up does nothing.
    void KeepStatusWriterRunning(string path)
    {
        bool fresh = File.Exists(path) && File.GetLastWriteTime(path) > DateTime.Now.AddSeconds(-60);
        if (fresh || statusStarted > DateTime.Now.AddMinutes(-2)) return;
        statusStarted = DateTime.Now;
        string script = Path.Combine(Path.GetDirectoryName(liveDir), "remote-view.ps1");
        if (!File.Exists(script)) return;
        try
        {
            Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"") { UseShellExecute = false, CreateNoWindow = true });
            Log("started remote-view.ps1 (the status line was stale)");
        }
        catch { }
    }

    void ShowStatus()
    {
        try
        {
            string path = Path.Combine(liveDir, "quest-view.json");
            KeepStatusWriterRunning(path);
            if (!File.Exists(path)) { status.Text = "Quest 2  ·  waiting for the first status"; return; }
            string json = File.ReadAllText(path);
            bool running = Regex.IsMatch(json, "\"running\"\\s*:\\s*true");
            bool online = Regex.IsMatch(json, "\"online\"\\s*:\\s*true");
            string game = Field(json, "game"), last = Field(json, "status");
            bool stale = File.GetLastWriteTime(path) < DateTime.Now.AddMinutes(-2);
            string now = stale ? "status not updated for 2 minutes" : !online ? "offline" : running ? "Playing " + GameName(game) : "No game running";
            string text = "Shown on Oculus Quest 2 (Snapdragon XR2, Adreno 650, 6 GB)  ·  " + now;
            string previous = running ? "" : LastRun(last);
            if (previous.Length > 0) text += "  ·  Last: " + previous;
            if (status.Text != text) status.Text = text;
            dot.ForeColor = stale || !online ? Color.FromArgb(214, 140, 60) : running ? Color.FromArgb(80, 200, 120) : Color.FromArgb(110, 116, 126);
        }
        catch { }
    }

    // "Celeste Classic (9387+8bit)" -> "Celeste Classic": the build tag in brackets is for the logs.
    static string GameName(string name) { return Regex.Replace(name ?? "", @"\s*\([^)]*\)\s*$", "").Trim(); }

    // Status lines from the Quest's run scripts: "EXIT | game | rc=134 | 2026-11-07T10:09:49Z",
    // "FRAME | game | /path/frame.png | mean=0.04 | time", "START | game | time | script".
    static string LastRun(string line)
    {
        var parts = (line ?? "").Split('|');
        if (parts.Length < 3) return "";
        string kind = parts[0].Trim(), game = GameName(parts[1].Trim());
        DateTime when;
        string at = DateTime.TryParse(parts[parts.Length - 1].Trim(), null, System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out when) ? " at " + when.ToLocalTime().ToString("h:mm tt") : "";
        if (kind == "EXIT")
        {
            var code = Regex.Match(parts[2], @"rc=(\d+)");
            return code.Success && code.Groups[1].Value != "0" ? game + " stopped (code " + code.Groups[1].Value + ")" + at : game + " closed" + at;
        }
        if (kind == "FRAME") return game + " showed a frame" + at;
        if (kind == "START") return game + " started" + at;
        return "";
    }

    static string Field(string json, string name)
    {
        var m = Regex.Match(json, "\"" + name + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
        return m.Success ? Regex.Unescape(m.Groups[1].Value) : "";
    }

    void SaveImage()
    {
        if (window == IntPtr.Zero) { note.Visible = true; note.Text = "No Quest desktop to save yet."; note.BringToFront(); return; }
        try
        {
            var area = host.RectangleToScreen(host.ClientRectangle);
            using (var bmp = new Bitmap(area.Width, area.Height))
            {
                using (var g = Graphics.FromImage(bmp)) g.CopyFromScreen(area.Location, Point.Empty, area.Size);
                string dir = Path.Combine(liveDir, "quest-images");
                Directory.CreateDirectory(dir);
                string file = Path.Combine(dir, "quest-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".png");
                bmp.Save(file, System.Drawing.Imaging.ImageFormat.Png);
                Log("saved " + file);
                status.Text = "Saved " + file;
            }
        }
        catch (Exception ex) { status.Text = "Could not save the image: " + ex.Message; }
    }

    void Log(string text)
    {
        try { File.AppendAllText(Path.Combine(liveDir, "quest-view.log"), DateTime.Now.ToString("MM-dd HH:mm:ss ") + text + Environment.NewLine); } catch { }
    }
}
