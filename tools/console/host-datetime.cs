// Date and time for the AnyPS5 screen (sysCall "datetime"). Windows is read-only here: the PC runs the test pipeline,
// so changing the time zone or time sync throws NotSupportedException ("Available in AnyPS5 OS").
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Text.RegularExpressions;
using Microsoft.Win32;

static class Sys_Datetime
{
    public static object Handle(string action, Dictionary<string, object> args)
    {
        if (action == "status") return Status();
        if (action == "zones") return Zones();
        if (action == "setTimezone" || action == "setNtp" || action == "setAutoTimezone")
            throw new NotSupportedException("Available in AnyPS5 OS");
        throw new NotSupportedException("unknown datetime action " + action);
    }

    static string Offset(TimeSpan t)
    {
        string sign = t < TimeSpan.Zero ? "-" : "+";
        TimeSpan a = t.Duration();
        return sign + ((int)a.TotalHours).ToString("00") + ":" + a.Minutes.ToString("00");
    }

    static string Label(TimeZoneInfo z)
    {
        string s = Regex.Replace(z.DisplayName ?? "", @"^\(UTC[^)]*\)\s*", "");
        return s.Length > 0 ? s : z.Id;
    }

    static object RegValue(string key, string name)
    {
        try
        {
            using (RegistryKey k = Registry.LocalMachine.OpenSubKey(key))
                return k == null ? null : k.GetValue(name);
        }
        catch (Exception) { return null; }
    }

    static Dictionary<string, object> Status()
    {
        DateTime now = DateTime.Now;
        TimeZoneInfo z = TimeZoneInfo.Local;
        bool dst = z.SupportsDaylightSavingTime && z.IsDaylightSavingTime(now);
        var d = new Dictionary<string, object>();
        d["platform"] = "windows";
        d["localTime"] = now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
        d["epochMs"] = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
        d["timezone"] = z.Id;
        d["timezoneLabel"] = Label(z);
        d["displayName"] = z.DisplayName;
        d["abbrev"] = dst ? z.DaylightName : z.StandardName;
        d["utcOffset"] = Offset(z.GetUtcOffset(now));
        d["baseOffset"] = Offset(z.BaseUtcOffset);
        d["supportsDst"] = z.SupportsDaylightSavingTime;
        d["dst"] = dst;
        d["canSetTimezone"] = false;
        d["canSetNtp"] = false;

        object type = RegValue(@"SYSTEM\CurrentControlSet\Services\W32Time\Parameters", "Type");
        string ts = type == null ? null : Convert.ToString(type);
        d["ntp"] = ts == null ? null : (object)!string.Equals(ts, "NoSync", StringComparison.OrdinalIgnoreCase);
        object server = RegValue(@"SYSTEM\CurrentControlSet\Services\W32Time\Parameters", "NtpServer");
        d["ntpServer"] = server == null ? null : Regex.Replace(Convert.ToString(server), @",0x[0-9a-fA-F]+", "").Trim();

        object start = RegValue(@"SYSTEM\CurrentControlSet\Services\tzautoupdate", "Start");
        int sv;
        d["autoTimezone"] = start != null && int.TryParse(Convert.ToString(start), out sv) ? (object)(sv != 4) : null;
        d["canSetAutoTimezone"] = false;

        Dictionary<string, object> w = W32tm();
        foreach (var kv in w) d[kv.Key] = kv.Value;
        return d;
    }

    static Dictionary<string, object> W32tm()
    {
        var r = new Dictionary<string, object>();
        r["ntpSynchronized"] = null;
        r["timeSource"] = null;
        r["lastSync"] = null;
        r["syncNote"] = null;
        string output;
        int code;
        try
        {
            var psi = new ProcessStartInfo("w32tm", "/query /status");
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            using (Process p = Process.Start(psi))
            {
                var errTask = p.StandardError.ReadToEndAsync();
                var outTask = p.StandardOutput.ReadToEndAsync();
                if (!p.WaitForExit(5000))
                {
                    try { p.Kill(); } catch (Exception) { }
                    r["syncNote"] = "Windows Time did not answer.";
                    return r;
                }
                output = outTask.Result + "\n" + errTask.Result;
                code = p.ExitCode;
            }
        }
        catch (Exception ex)
        {
            r["syncNote"] = "Could not ask Windows Time: " + ex.Message;
            return r;
        }
        return ParseW32tm(output, code, r);
    }

    public static Dictionary<string, object> ParseW32tm(string output, int code, Dictionary<string, object> r)
    {
        if (r == null) r = new Dictionary<string, object>();
        if (Regex.IsMatch(output, @"0x80070005|Access is denied", RegexOptions.IgnoreCase))
        {
            r["syncNote"] = "Sync status needs administrator rights on Windows.";
            return r;
        }
        if (Regex.IsMatch(output, @"0x80070426", RegexOptions.IgnoreCase))
        {
            r["syncNote"] = "The Windows Time service is not running right now; it starts by itself when Windows syncs.";
            return r;
        }
        Match leap = Regex.Match(output, @"^\s*Leap Indicator:\s*(\d+)", RegexOptions.Multiline);
        Match src = Regex.Match(output, @"^\s*Source:\s*(.+?)\s*$", RegexOptions.Multiline);
        Match last = Regex.Match(output, @"^\s*Last Successful Sync Time:\s*(.+?)\s*$", RegexOptions.Multiline);
        string source = src.Success ? Regex.Replace(src.Groups[1].Value, @",0x[0-9a-fA-F]+", "").Trim() : null;
        if (source != null) r["timeSource"] = source;
        if (last.Success && !Regex.IsMatch(last.Groups[1].Value, "unspecified", RegexOptions.IgnoreCase)) r["lastSync"] = last.Groups[1].Value;
        bool local = source != null && Regex.IsMatch(source, "Local CMOS Clock|Free-running System Clock", RegexOptions.IgnoreCase);
        if (leap.Success) r["ntpSynchronized"] = leap.Groups[1].Value != "3" && !local;
        else if (code != 0) r["syncNote"] = "Windows Time gave no status.";
        return r;
    }

    static Dictionary<string, object> Zones()
    {
        var list = new List<object>();
        foreach (TimeZoneInfo z in TimeZoneInfo.GetSystemTimeZones())
        {
            string off = Offset(z.BaseUtcOffset);
            var e = new Dictionary<string, object>();
            e["id"] = z.Id;
            e["label"] = Label(z);
            e["group"] = off == "+00:00" ? "UTC" : "UTC" + off;
            e["offsetMinutes"] = (int)z.BaseUtcOffset.TotalMinutes;
            list.Add(e);
        }
        var d = new Dictionary<string, object>();
        d["grouping"] = "offset";
        d["current"] = TimeZoneInfo.Local.Id;
        d["zones"] = list;
        return d;
    }
}
