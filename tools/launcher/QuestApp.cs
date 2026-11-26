// AnyPS5 Quest View.exe (Blu 11-07: "its own separate window ... its main job is just to display what's being seen on
// the Oculus Quest", run beside the AnyPS5 Games window on stream). It is the QuestView control (QuestView.cs) in a
// window of its own: the Quest's XFCE desktop, live, with the PC's mouse and keyboard. It sits next to AnyPS5
// Games.exe and shares its _tools folder (live status).
using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

static class QuestApp
{
    [STAThread]
    static void Main()
    {
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string f = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "webview2", new System.Reflection.AssemblyName(e.Name).Name + ".dll");
            return File.Exists(f) ? System.Reflection.Assembly.LoadFrom(f) : null;
        };
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Run();
    }

    static void Run()
    {
        string live = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "live");
        var form = new Form
        {
            Text = "AnyPS5 Quest View: the Meta Quest 2 desktop",
            ClientSize = new Size(1600, 900),
            BackColor = Color.Black,
            StartPosition = FormStartPosition.CenterScreen
        };
        try { form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        // Blu 11-07: test windows that pop up and place themselves must not cover the Quest view, so it stays on top
        // of other windows; F9 turns that off and on (the title says which).
        form.TopMost = true;
        form.KeyPreview = true;
        Action title = () => form.Text = "AnyPS5 Quest View: the Meta Quest 2 desktop" + (form.TopMost ? "   (always on top, F9 to change)" : "   (F9: keep on top)");
        title();
        form.KeyDown += (s, e) => { if (e.KeyCode == Keys.F9) { form.TopMost = !form.TopMost; title(); e.Handled = true; } };
        var view = new QuestView(live);
        form.Controls.Add(view);
        form.Shown += (s, e) => view.Show(true);
        form.FormClosing += (s, e) => view.Show(false);
        AppDomain.CurrentDomain.ProcessExit += (s, e) => { foreach (var p in System.Diagnostics.Process.GetProcessesByName("scrcpy")) { try { p.Kill(); } catch { } } };
        Application.Run(form);
    }
}
