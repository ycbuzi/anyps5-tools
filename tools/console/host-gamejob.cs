using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

// Games started from AnyPS5.exe belong to it (Blu 11-16): they are its child processes and sit in one Job Object the
// app owns. The job kills whatever is left in it when the app's process ends (closed, crashed or killed), so a game
// never outlives the app that runs it. The app closes the game cleanly first (ConsoleView.Release).
// Start() also starts the game with loader pop-ups off (like _tools\start-game.ps1): a missing DLL or entry point
// makes the game exit with 0xC0000135 / 0xC0000139 instead of a box that waits for a click.
static class GameJob
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attrs, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, int size);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);

    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits { public long PerProcessUserTimeLimit, PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass; }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits { public BasicLimits Basic; public IoCounters Io; public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed; }

    const uint KillOnJobClose = 0x2000;
    const uint NoLoaderPopups = 0x8003;
    static IntPtr job;
    static readonly object gate = new object();

    static IntPtr Job()
    {
        lock (gate)
        {
            if (job != IntPtr.Zero) return job;
            IntPtr j = CreateJobObject(IntPtr.Zero, null);
            if (j == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            var info = new ExtendedLimits();
            info.Basic.LimitFlags = KillOnJobClose;
            if (!SetInformationJobObject(j, 9, ref info, Marshal.SizeOf(typeof(ExtendedLimits)))) throw new System.ComponentModel.Win32Exception();
            job = j;
            return job;
        }
    }

    public static Process Start(ProcessStartInfo start, out string jobError)
    {
        jobError = null;
        IntPtr j = IntPtr.Zero;
        try { j = Job(); } catch (Exception ex) { jobError = ex.Message; }
        uint before = SetErrorMode(NoLoaderPopups);
        Process p;
        try { p = Process.Start(start); }
        finally { SetErrorMode(before); }
        if (j != IntPtr.Zero && !AssignProcessToJobObject(j, p.Handle)) jobError = new System.ComponentModel.Win32Exception().Message;
        return p;
    }

    public static bool Holds(Process p)
    {
        bool inJob;
        lock (gate) { if (job == IntPtr.Zero) return false; }
        try { return IsProcessInJob(p.Handle, job, out inJob) && inJob; } catch { return false; }
    }

    public static string LoaderError(int code)
    {
        if ((uint)code == 0xC0000135) return "a library (DLL) it needs is missing";
        if ((uint)code == 0xC0000139) return "a library it needs is missing a function (entry point not found)";
        return null;
    }
}
