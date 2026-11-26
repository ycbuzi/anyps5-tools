using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

static class Sys_Language
{
    [DllImport("shlwapi.dll", CharSet = CharSet.Unicode)]
    static extern int SHLoadIndirectString(string source, StringBuilder outBuf, int outBufSize, IntPtr reserved);

    public static object Handle(string action, Dictionary<string, object> args)
    {
        if (action == "status") return Status();
        if (action == "locales" || action == "setLocale" || action == "layouts" || action == "setLayout")
            throw new NotSupportedException("Available in AnyPS5 OS");
        throw new NotSupportedException("unknown language action " + action);
    }

    static Dictionary<string, object> Culture(CultureInfo c)
    {
        return new Dictionary<string, object> { { "tag", c.Name }, { "name", c.DisplayName }, { "native", c.NativeName } };
    }

    static Dictionary<string, object> Status()
    {
        var keyboards = Keyboards();
        return new Dictionary<string, object>
        {
            { "platform", "windows" },
            { "canChange", false },
            { "language", Culture(CultureInfo.CurrentUICulture) },
            { "region", Culture(CultureInfo.CurrentCulture) },
            { "keyboards", keyboards }
        };
    }

    static List<object> Keyboards()
    {
        var list = new List<object>();
        var seen = new HashSet<string>();
        using (var prof = Registry.CurrentUser.OpenSubKey(@"Control Panel\International\User Profile"))
        {
            var langs = prof == null ? null : prof.GetValue("Languages") as string[];
            if (langs != null)
                foreach (var lang in langs)
                {
                    using (var lk = prof.OpenSubKey(lang))
                    {
                        if (lk == null) continue;
                        foreach (var vn in lk.GetValueNames())
                        {
                            int colon = vn.IndexOf(':');
                            if (colon != 4) continue;
                            string tip = vn.Substring(colon + 1);
                            if (!seen.Add(lang + "|" + tip)) continue;
                            list.Add(Entry(lang, tip));
                        }
                    }
                }
        }
        if (list.Count > 0) return list;
        using (var pre = Registry.CurrentUser.OpenSubKey(@"Keyboard Layout\Preload"))
        using (var sub = Registry.CurrentUser.OpenSubKey(@"Keyboard Layout\Substitutes"))
        {
            if (pre == null) return list;
            var names = new List<string>(pre.GetValueNames());
            names.Sort(delegate(string a, string b) { int x, y; int.TryParse(a, out x); int.TryParse(b, out y); return x.CompareTo(y); });
            foreach (var n in names)
            {
                string klid = Convert.ToString(pre.GetValue(n));
                if (string.IsNullOrEmpty(klid) || !seen.Add(klid)) continue;
                string real = sub == null ? null : sub.GetValue(klid) as string;
                string lang = "";
                int langId;
                if (klid.Length == 8 && int.TryParse(klid.Substring(4), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out langId))
                {
                    try { lang = new CultureInfo(langId).Name; } catch (ArgumentException) { }
                }
                list.Add(Entry(lang, string.IsNullOrEmpty(real) ? klid : real));
            }
        }
        return list;
    }

    static Dictionary<string, object> Entry(string lang, string tip)
    {
        string langName = lang;
        try { if (lang != "") langName = new CultureInfo(lang).DisplayName; } catch (ArgumentException) { }
        string layout;
        if (tip.StartsWith("{")) layout = "Input method editor";
        else layout = LayoutName(tip) ?? tip;
        return new Dictionary<string, object> { { "id", tip }, { "lang", lang }, { "langName", langName }, { "name", layout } };
    }

    static string LayoutName(string klid)
    {
        if (klid.Length != 8) return null;
        foreach (char ch in klid) if (!Uri.IsHexDigit(ch)) return null;
        using (var k = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Keyboard Layouts\" + klid))
        {
            if (k == null) return null;
            string disp = k.GetValue("Layout Display Name") as string;
            if (!string.IsNullOrEmpty(disp) && disp.StartsWith("@"))
            {
                var sb = new StringBuilder(512);
                try { if (SHLoadIndirectString(disp, sb, sb.Capacity, IntPtr.Zero) == 0 && sb.Length > 0) return sb.ToString(); } catch (Exception) { }
            }
            return k.GetValue("Layout Text") as string;
        }
    }
}
