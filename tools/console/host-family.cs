using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

static class Sys_Family
{
    static readonly object Gate = new object();
    static readonly Regex UidRx = new Regex("^[A-Za-z0-9_-]{1,64}$");
    static readonly Regex TimeRx = new Regex("^([01][0-9]|2[0-3]):([0-5][0-9])$");
    static readonly Regex TitleRx = new Regex("\\(([A-Z]{4}[0-9]{5})\\)\\s*$");

    static string UsageFile { get { return Path.Combine(ConsoleBoot.Root, "_tools", "live", ConsoleView.TestMode ? "family-usage-test.json" : "family-usage.json"); } }
    static string Today { get { return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture); } }

    public static object Handle(string action, Dictionary<string, object> args)
    {
        if (action == "usage")
        {
            Dictionary<string, Dictionary<string, int>> all;
            lock (Gate) all = Load();
            var today = new Dictionary<string, object>();
            Dictionary<string, int> day;
            if (all.TryGetValue(Today, out day))
                foreach (var kv in day) today[kv.Key] = kv.Value;
            return new Dictionary<string, object> { { "date", Today }, { "today", today } };
        }
        throw new ArgumentException("unknown family action " + action);
    }

    public static void AddMinute(string uid)
    {
        if (uid == null || !UidRx.IsMatch(uid)) return;
        try
        {
            lock (Gate)
            {
                var all = Load();
                Dictionary<string, int> day;
                if (!all.TryGetValue(Today, out day)) { day = new Dictionary<string, int>(); all[Today] = day; }
                int v;
                day.TryGetValue(uid, out v);
                day[uid] = v + 1;
                Save(all);
            }
        }
        catch (Exception ex) { ConsoleView.Log("family: playtime not saved: " + ex.Message); }
    }

    public static int MinutesToday(string uid)
    {
        if (uid == null || !UidRx.IsMatch(uid)) return 0;
        lock (Gate)
        {
            var all = Load();
            Dictionary<string, int> day;
            int v;
            if (all.TryGetValue(Today, out day) && day.TryGetValue(uid, out v)) return v;
            return 0;
        }
    }

    public static int MinutesLeft(string uid, string settingsFile)
    {
        var f = Family(uid, settingsFile);
        if (f == null || !Bool(f, "limited", false)) return int.MaxValue;
        DateTime now = DateTime.Now;
        int n = now.Hour * 60 + now.Minute;
        int toEnd;
        if (!InWindow(Str(f, "from"), Str(f, "to"), n, out toEnd)) return 0;
        long left = long.MaxValue;
        int daily = Num(f, "dailyMin");
        if (daily > 0) left = Math.Max(0, daily - MinutesToday(uid));
        if (toEnd >= 0) left = Math.Min(left, toEnd);
        return left > int.MaxValue ? int.MaxValue : (int)left;
    }

    public static bool GameAllowed(string uid, string titleId, string settingsFile)
    {
        var f = Family(uid, settingsFile);
        if (f == null || Bool(f, "allowAll", true)) return true;
        object g;
        if (string.IsNullOrEmpty(titleId) || !f.TryGetValue("games", out g)) return false;
        var list = g as System.Collections.IEnumerable;
        if (list == null || g is string) return false;
        foreach (var x in list)
            if (x is string && string.Equals((string)x, titleId, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    public static string WhenPlaytimeEnds(string uid, string settingsFile)
    {
        var f = Family(uid, settingsFile);
        return f != null && Str(f, "whenUp") == "close" ? "close" : "notify";
    }

    public static string TitleIdFromFolder(string folder)
    {
        if (string.IsNullOrEmpty(folder)) return "";
        var m = TitleRx.Match(Path.GetFileName(folder.TrimEnd('\\', '/')));
        return m.Success ? m.Groups[1].Value : "";
    }

    internal static bool InWindow(string from, string to, int n, out int toEnd)
    {
        int a = Clock(from), b = Clock(to);
        toEnd = -1;
        if (a < 0 && b < 0) return true;
        if (a >= 0 && b >= 0)
        {
            if (a == b) return true;
            if (a < b) { if (n >= a && n < b) { toEnd = b - n; return true; } toEnd = 0; return false; }
            if (n >= a) { toEnd = 1440 - n + b; return true; }
            if (n < b) { toEnd = b - n; return true; }
            toEnd = 0; return false;
        }
        if (a >= 0) { if (n >= a) { toEnd = 1440 - n; return true; } toEnd = 0; return false; }
        if (n < b) { toEnd = b - n; return true; }
        toEnd = 0; return false;
    }

    static int Clock(string t)
    {
        if (t == null) return -1;
        var m = TimeRx.Match(t);
        return m.Success ? int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) * 60 + int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture) : -1;
    }

    static Dictionary<string, object> Family(string uid, string settingsFile)
    {
        if (uid == null || !UidRx.IsMatch(uid) || string.IsNullOrEmpty(settingsFile) || !File.Exists(settingsFile)) return null;
        Dictionary<string, object> s;
        try { s = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(settingsFile)) as Dictionary<string, object>; }
        catch (Exception ex) { ConsoleView.Log("family: settings not readable: " + ex.Message); return null; }
        if (s == null) return null;
        if (Str(s, "testUid") == uid) return null;
        object ps;
        if (!s.TryGetValue("profiles", out ps) || !(ps is System.Collections.IEnumerable) || ps is string) return null;
        foreach (var p in (System.Collections.IEnumerable)ps)
        {
            var d = p as Dictionary<string, object>;
            if (d == null || Str(d, "uid") != uid) continue;
            if (Str(s, "testUid") == "" && Regex.IsMatch(Str(d, "name"), "^anyps5$", RegexOptions.IgnoreCase)) return null;
            object f;
            return d.TryGetValue("family", out f) ? f as Dictionary<string, object> : null;
        }
        return null;
    }

    static string Str(Dictionary<string, object> d, string k)
    {
        object v;
        return d.TryGetValue(k, out v) && v is string ? (string)v : "";
    }

    static bool Bool(Dictionary<string, object> d, string k, bool def)
    {
        object v;
        return d.TryGetValue(k, out v) && v is bool ? (bool)v : def;
    }

    static int Num(Dictionary<string, object> d, string k)
    {
        object v;
        if (!d.TryGetValue(k, out v) || v == null || v is string || v is bool) return 0;
        try { double x = Convert.ToDouble(v, CultureInfo.InvariantCulture); return x <= 0 || double.IsNaN(x) ? 0 : x >= int.MaxValue ? int.MaxValue : (int)Math.Round(x); }
        catch { return 0; }
    }

    static Dictionary<string, Dictionary<string, int>> Load()
    {
        var all = new Dictionary<string, Dictionary<string, int>>();
        string file = UsageFile;
        if (!File.Exists(file)) return all;
        Dictionary<string, object> raw;
        try { raw = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(file)) as Dictionary<string, object>; }
        catch (Exception ex) { ConsoleView.Log("family: usage file not readable: " + ex.Message); return all; }
        if (raw == null) return all;
        DateTime parsed;
        foreach (var kv in raw)
        {
            var day = kv.Value as Dictionary<string, object>;
            if (day == null || !DateTime.TryParseExact(kv.Key, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed)) continue;
            var outDay = new Dictionary<string, int>();
            foreach (var u in day)
            {
                if (!UidRx.IsMatch(u.Key)) continue;
                int m = Num(day, u.Key);
                if (m > 0) outDay[u.Key] = m;
            }
            all[kv.Key] = outDay;
        }
        return all;
    }

    static void Save(Dictionary<string, Dictionary<string, int>> all)
    {
        DateTime oldest = DateTime.Now.Date.AddDays(-30);
        var keep = new Dictionary<string, object>();
        DateTime parsed;
        foreach (var kv in all)
        {
            if (!DateTime.TryParseExact(kv.Key, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed) || parsed < oldest) continue;
            var day = new Dictionary<string, object>();
            foreach (var u in kv.Value) day[u.Key] = u.Value;
            keep[kv.Key] = day;
        }
        string file = UsageFile, tmp = file + ".tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(file));
        File.WriteAllText(tmp, new JavaScriptSerializer().Serialize(keep));
        if (File.Exists(file)) File.Replace(tmp, file, null);
        else File.Move(tmp, file);
    }
}
