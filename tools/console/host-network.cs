// Settings > Network on Windows: read-only status, Wi-Fi list and the internet connection test. Windows keeps managing
// this PC's connection, so connecting, disconnecting and radio/networking switches are "Available in AnyPS5 OS".
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

static class Sys_Network
{
    const string DownUrl = "https://speed.cloudflare.com/__down?bytes=";
    const string UpUrl = "https://speed.cloudflare.com/__up";
    const int CapMs = 20000;

    public static object Handle(string action, Dictionary<string, object> args)
    {
        if (args == null) args = new Dictionary<string, object>();
        switch (action)
        {
            case "status": return Status();
            case "wifi_list": return WifiList();
            case "test_ip": return TestIp();
            case "test_internet": return TestInternet();
            case "test_download": return TestDownload(Size(args, "bytes", 10000000, 100000, 25000000));
            case "test_upload": return TestUpload(Size(args, "bytes", 2000000, 100000, 10000000));
            case "connect":
            case "disconnect":
            case "radio":
            case "networking":
                throw new NotSupportedException("Available in AnyPS5 OS");
        }
        throw new NotSupportedException("unknown network action " + action);
    }

    static int Size(Dictionary<string, object> args, string key, int def, int lo, int hi)
    {
        object v;
        if (!args.TryGetValue(key, out v) || v == null) return def;
        if (v is bool || v is string) throw new ArgumentException(key + " must be a number");
        double d = Convert.ToDouble(v, CultureInfo.InvariantCulture);
        return (int)Math.Max(lo, Math.Min(hi, d));
    }

    static string Mac(PhysicalAddress p)
    {
        var b = p == null ? new byte[0] : p.GetAddressBytes();
        return string.Join(":", b.Select(x => x.ToString("X2")).ToArray());
    }

    static string Method(NetworkInterfaceType t)
    {
        if (t == NetworkInterfaceType.Wireless80211) return "Wi-Fi";
        if (t == NetworkInterfaceType.Ethernet || t == NetworkInterfaceType.GigabitEthernet || t == NetworkInterfaceType.FastEthernetT || t == NetworkInterfaceType.FastEthernetFx || t == NetworkInterfaceType.Ethernet3Megabit) return "LAN Cable";
        return t.ToString();
    }

    static Dictionary<string, object> Adapter(NetworkInterface n)
    {
        var d = new Dictionary<string, object>();
        d["name"] = n.Name;
        d["description"] = n.Description;
        d["method"] = Method(n.NetworkInterfaceType);
        d["up"] = n.OperationalStatus == OperationalStatus.Up;
        d["mac"] = Mac(n.GetPhysicalAddress());
        long speed = 0;
        try { speed = n.Speed; } catch { }
        d["speedMbps"] = speed > 0 ? speed / 1000000 : 0;
        string ip = "", mask = "", gw = "";
        var dns = new List<object>();
        try
        {
            var p = n.GetIPProperties();
            var u = p.UnicastAddresses.FirstOrDefault(a => a.Address.AddressFamily == AddressFamily.InterNetwork);
            if (u != null) { ip = u.Address.ToString(); mask = u.IPv4Mask != null ? u.IPv4Mask.ToString() : ""; }
            var g = p.GatewayAddresses.FirstOrDefault(a => a.Address.AddressFamily == AddressFamily.InterNetwork && !a.Address.Equals(IPAddress.Any));
            if (g != null) gw = g.Address.ToString();
            foreach (var a in p.DnsAddresses.Where(a => a.AddressFamily == AddressFamily.InterNetwork)) dns.Add(a.ToString());
            if (dns.Count == 0) foreach (var a in p.DnsAddresses.Where(a => !a.IsIPv6SiteLocal)) dns.Add(a.ToString());
        }
        catch (NetworkInformationException) { }
        d["ip"] = ip; d["mask"] = mask; d["gateway"] = gw; d["dns"] = dns;
        return d;
    }

    static bool Physical(NetworkInterface n)
    {
        var t = n.NetworkInterfaceType;
        return t != NetworkInterfaceType.Loopback && t != NetworkInterfaceType.Tunnel && t != NetworkInterfaceType.Unknown;
    }

    static Dictionary<string, object> Status()
    {
        var all = NetworkInterface.GetAllNetworkInterfaces().Where(Physical).ToList();
        var up = all.Where(n => n.OperationalStatus == OperationalStatus.Up).Select(n => new KeyValuePair<NetworkInterface, Dictionary<string, object>>(n, Adapter(n))).ToList();
        var withIp = up.Where(x => (string)x.Value["ip"] != "").ToList();
        var prim = withIp.Where(x => (string)x.Value["gateway"] != "").Concat(withIp).Select(x => x.Value).FirstOrDefault();
        var res = new Dictionary<string, object>();
        res["platform"] = "windows";
        res["control"] = false;
        res["connected"] = prim != null;
        res["networking"] = null;
        bool wifiHw = all.Any(n => n.NetworkInterfaceType == NetworkInterfaceType.Wireless80211);
        res["wifiHw"] = wifiHw;
        res["wifiRadio"] = null;
        res["method"] = prim != null ? prim["method"] : "";
        res["device"] = prim != null ? prim["name"] : "";
        res["ip"] = prim != null ? prim["ip"] : "";
        res["mask"] = prim != null ? prim["mask"] : "";
        res["gateway"] = prim != null ? prim["gateway"] : "";
        res["dns"] = prim != null ? prim["dns"] : new List<object>();
        res["mac"] = prim != null ? prim["mac"] : "";
        res["ssid"] = "";
        res["signal"] = null;
        res["internet"] = prim != null && Internet((string)prim["gateway"] != "");
        var lan = all.Where(n => Method(n.NetworkInterfaceType) == "LAN Cable").ToList();
        var lanUp = up.Where(x => (string)x.Value["method"] == "LAN Cable" && (string)x.Value["ip"] != "").Select(x => x.Value).FirstOrDefault();
        res["lan"] = new Dictionary<string, object> { { "present", lan.Count > 0 }, { "connected", lanUp != null }, { "device", lanUp != null ? lanUp["name"] : (lan.Count > 0 ? lan[0].Name : "") }, { "speedMbps", lanUp != null ? lanUp["speedMbps"] : 0 } };
        res["adapters"] = up.Select(x => (object)x.Value).ToList();
        if (wifiHw)
        {
            var w = ParseInterfaces(Netsh("wlan show interfaces"));
            if (w != null)
            {
                bool wcon = Get(w, "State").ToLowerInvariant() == "connected";
                string radio = Get(w, "Radio status").ToLowerInvariant();
                res["wifiRadio"] = radio == "" ? (object)null : !radio.Contains("off");
                if (wcon && (prim == null || (string)prim["method"] == "Wi-Fi"))
                {
                    res["ssid"] = Get(w, "SSID");
                    res["signal"] = Percent(Get(w, "Signal"));
                    res["security"] = Get(w, "Authentication");
                    res["band"] = Get(w, "Band");
                    res["channel"] = Get(w, "Channel");
                    res["radioType"] = Get(w, "Radio type");
                }
            }
        }
        return res;
    }

    static bool Internet(bool fallback)
    {
        try
        {
            var t = Type.GetTypeFromCLSID(new Guid("DCB00C01-570F-4A9B-8D69-199FDBA5723B"));
            var nlm = Activator.CreateInstance(t);
            try { return (bool)t.InvokeMember("IsConnectedToInternet", System.Reflection.BindingFlags.GetProperty, null, nlm, null); }
            finally { System.Runtime.InteropServices.Marshal.ReleaseComObject(nlm); }
        }
        catch (Exception) { return fallback && NetworkInterface.GetIsNetworkAvailable(); }
    }

    static string Get(Dictionary<string, string> d, string k)
    {
        string v;
        return d.TryGetValue(k, out v) ? v : "";
    }

    static object Percent(string s)
    {
        var m = Regex.Match(s ?? "", @"(\d+)\s*%");
        if (!m.Success) return null;
        return int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
    }

    static bool KeyValue(string line, out string key, out string val)
    {
        key = val = null;
        int i = line.IndexOf(':');
        if (i <= 0) return false;
        key = line.Substring(0, i).Trim();
        val = line.Substring(i + 1).Trim();
        return key.Length > 0;
    }

    public static Dictionary<string, string> ParseInterfaces(string text)
    {
        Dictionary<string, string> first = null, cur = null;
        string last = null;
        foreach (var raw in (text ?? "").Split('\n'))
        {
            string line = raw.TrimEnd('\r'), k, v;
            if (!KeyValue(line, out k, out v))
            {
                if (cur != null && last != null && line.Trim() != "" && line.StartsWith(" ")) cur[last] = cur[last] + " " + line.Trim();
                continue;
            }
            if (k == "Name") { cur = new Dictionary<string, string>(); if (first == null) first = cur; }
            if (cur == null) continue;
            if (!cur.ContainsKey(k)) { cur[k] = v; last = k; } else last = null;
            if (k == "State" && v.ToLowerInvariant() == "connected") first = cur;
        }
        return first;
    }

    public static List<object> ParseNetworks(string text)
    {
        var bss = new List<Dictionary<string, object>>();
        string ssid = null, auth = "";
        Dictionary<string, object> b = null;
        foreach (var raw in (text ?? "").Split('\n'))
        {
            string k, v;
            if (!KeyValue(raw.TrimEnd('\r'), out k, out v)) continue;
            if (Regex.IsMatch(k, @"^SSID \d+$")) { ssid = v; auth = ""; b = null; continue; }
            if (ssid == null) continue;
            if (k == "Authentication") auth = v;
            else if (Regex.IsMatch(k, @"^BSSID \d+$"))
            {
                b = new Dictionary<string, object> { { "ssid", ssid }, { "bssid", v.ToUpperInvariant() }, { "signal", 0 }, { "security", auth }, { "secured", auth.ToLowerInvariant() != "open" && auth != "" }, { "band", "" }, { "channel", "" }, { "inUse", false } };
                bss.Add(b);
            }
            else if (b != null && k == "Signal") { var p = Percent(v); b["signal"] = p == null ? 0 : (int)p; }
            else if (b != null && k == "Band") b["band"] = v;
            else if (b != null && k == "Channel") b["channel"] = v;
        }
        return bss.Where(x => (string)x["ssid"] != "").GroupBy(x => (string)x["ssid"]).Select(g => g.OrderByDescending(x => (int)x["signal"]).First()).OrderByDescending(x => (int)x["signal"]).Cast<object>().ToList();
    }

    static Dictionary<string, object> WifiList()
    {
        bool hw = NetworkInterface.GetAllNetworkInterfaces().Any(n => n.NetworkInterfaceType == NetworkInterfaceType.Wireless80211);
        var res = new Dictionary<string, object> { { "present", hw }, { "radio", hw }, { "networks", new List<object>() } };
        if (!hw) return res;
        var nets = ParseNetworks(Netsh("wlan show networks mode=bssid"));
        var w = ParseInterfaces(Netsh("wlan show interfaces"));
        string cur = w != null && Get(w, "State").ToLowerInvariant() == "connected" ? Get(w, "SSID") : "";
        foreach (Dictionary<string, object> n in nets) n["inUse"] = cur != "" && (string)n["ssid"] == cur;
        res["networks"] = nets.OrderBy(n => (bool)((Dictionary<string, object>)n)["inUse"] ? 0 : 1).ToList();
        if (w != null) res["device"] = Get(w, "Name");
        return res;
    }

    static string Netsh(string args)
    {
        var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "netsh.exe"), args);
        psi.CreateNoWindow = true;
        psi.UseShellExecute = false;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        try { psi.StandardOutputEncoding = Encoding.GetEncoding(CultureInfo.CurrentCulture.TextInfo.OEMCodePage); } catch { }
        using (var p = Process.Start(psi))
        {
            var outTask = p.StandardOutput.ReadToEndAsync();
            var errTask = p.StandardError.ReadToEndAsync();
            if (!p.WaitForExit(10000))
            {
                try { p.Kill(); } catch { }
                throw new TimeoutException("netsh did not answer");
            }
            outTask.Wait(2000);
            return outTask.IsCompleted ? outTask.Result : "";
        }
    }

    static Dictionary<string, object> TestIp()
    {
        var st = Status();
        return new Dictionary<string, object> { { "ok", (string)st["ip"] != "" }, { "ip", st["ip"] }, { "method", st["method"] } };
    }

    static HttpWebRequest Request(string url)
    {
        ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        var r = (HttpWebRequest)WebRequest.Create(url);
        r.UserAgent = "AnyPS5";
        r.Timeout = CapMs;
        r.ReadWriteTimeout = CapMs;
        r.Proxy = WebRequest.DefaultWebProxy;
        return r;
    }

    static Dictionary<string, object> TestInternet()
    {
        var res = new Dictionary<string, object> { { "host", "speed.cloudflare.com" } };
        try
        {
            using (var ping = new Ping())
            {
                var reply = ping.Send("1.1.1.1", 3000);
                if (reply.Status == IPStatus.Success) res["pingMs"] = reply.RoundtripTime;
            }
        }
        catch (PingException) { }
        var sw = Stopwatch.StartNew();
        try
        {
            using (var resp = (HttpWebResponse)Request(DownUrl + "0").GetResponse())
            {
                int code = (int)resp.StatusCode;
                res["ok"] = code >= 200 && code < 400;
            }
            res["ms"] = sw.ElapsedMilliseconds;
        }
        catch (WebException ex)
        {
            res["ok"] = false;
            res["error"] = ex.Message;
        }
        return res;
    }

    static Dictionary<string, object> Speed(long bytes, double secs)
    {
        secs = Math.Max(secs, 0.001);
        return new Dictionary<string, object> { { "bytes", bytes }, { "seconds", Math.Round(secs, 3) }, { "mbps", Math.Round(bytes * 8 / secs / 1e6, 1) } };
    }

    static Dictionary<string, object> TestDownload(int n)
    {
        var sw = Stopwatch.StartNew();
        long got = 0;
        var buf = new byte[65536];
        using (var resp = Request(DownUrl + n.ToString(CultureInfo.InvariantCulture)).GetResponse())
        using (var s = resp.GetResponseStream())
        {
            int k;
            while ((k = s.Read(buf, 0, buf.Length)) > 0)
            {
                got += k;
                if (sw.ElapsedMilliseconds > CapMs) break;
            }
        }
        return Speed(got, sw.Elapsed.TotalSeconds);
    }

    static Dictionary<string, object> TestUpload(int n)
    {
        var body = new byte[n];
        var r = Request(UpUrl);
        r.Method = "POST";
        r.ContentType = "application/octet-stream";
        r.ContentLength = n;
        r.AllowWriteStreamBuffering = false;
        var sw = Stopwatch.StartNew();
        using (var s = r.GetRequestStream())
        {
            for (int off = 0; off < n; off += 65536)
            {
                s.Write(body, off, Math.Min(65536, n - off));
                if (sw.ElapsedMilliseconds > CapMs) throw new TimeoutException("The upload test took longer than 20 seconds.");
            }
        }
        using (var resp = r.GetResponse()) { }
        return Speed(n, sw.Elapsed.TotalSeconds);
    }
}
