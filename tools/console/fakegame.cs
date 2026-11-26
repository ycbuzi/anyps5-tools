// Stand-in game for selftest.cmd: a 1280x720 window that writes every typed character to fakegame-keys.txt next to it.
using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

class FakeGame : Form
{
    [STAThread]
    static void Main()
    {
        Application.Run(new FakeGame());
    }

    FakeGame()
    {
        Text = "FakeGame"; ClientSize = new Size(1280, 720); BackColor = Color.DarkSlateBlue; KeyPreview = true;
        string log = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fakegame-keys.txt");
        KeyPress += (s, e) => { try { File.AppendAllText(log, e.KeyChar.ToString()); } catch { } };
    }
}
