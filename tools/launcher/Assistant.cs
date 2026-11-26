// AnyPS5 Assistant.exe: opens the offline assistant (_tools\assistant\assistant.py) in this console window.
using System;
using System.Diagnostics;
using System.IO;

class Assistant
{
    static int Main()
    {
        Console.Title = "AnyPS5 Assistant (offline)";
        string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_tools", "assistant", "assistant.py");
        var p = Process.Start(new ProcessStartInfo("python", "-I \"" + script + "\"") { UseShellExecute = false });
        p.WaitForExit();
        if (p.ExitCode != 0) { Console.WriteLine("\nThe assistant stopped (exit " + p.ExitCode + "). Press Enter to close."); Console.ReadLine(); }
        return p.ExitCode;
    }
}
