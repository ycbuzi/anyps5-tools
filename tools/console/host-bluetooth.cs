using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

static class Sys_Bluetooth
{
    const string Unsupported = "Available in AnyPS5 OS";
    const string KeyConnected = "{83DA6326-97A6-4088-9453-A1923F573B29} 15";
    const string KeyClass = "{2BD67D8B-8BEB-48D5-87E0-6CDA3428040A} 10";

    public static object Handle(string action, Dictionary<string, object> args)
    {
        switch (action)
        {
            case "status":
                return Status();
            case "scan":
            case "pair":
            case "trust":
            case "connect":
            case "disconnect":
            case "remove":
            case "power":
                throw new NotSupportedException(Unsupported);
        }
        throw new NotSupportedException("unknown bluetooth action " + action);
    }

    static string Script()
    {
        var sb = new StringBuilder();
        sb.AppendLine("$ErrorActionPreference='SilentlyContinue'");
        sb.AppendLine("[Console]::OutputEncoding=[Text.Encoding]::UTF8");
        sb.AppendLine("$k1='" + KeyConnected + "'");
        sb.AppendLine("$k2='" + KeyClass + "'");
        sb.AppendLine("foreach($d in @(Get-PnpDevice -Class Bluetooth)){");
        sb.AppendLine(" $c='';$cod='';$id=[string]$d.InstanceId");
        sb.AppendLine(" if($d.Present -and ($id -like 'BTHENUM\\DEV_*' -or $id -like 'BTHLE\\DEV_*')){");
        sb.AppendLine("  foreach($x in @(Get-PnpDeviceProperty -InstanceId $id -KeyName $k1,$k2)){");
        sb.AppendLine("   if($x.KeyName -eq $k1 -and $x.Data -ne $null){$c=[string]$x.Data}");
        sb.AppendLine("   if($x.KeyName -eq $k2 -and $x.Data -ne $null){$cod=[string]$x.Data}}}");
        sb.AppendLine(" $n=([string]$d.FriendlyName) -replace \"[`t`r`n]\",' '");
        sb.AppendLine(" 'D'+\"`t\"+[string]$d.Status+\"`t\"+[string]$d.Present+\"`t\"+$id+\"`t\"+$n+\"`t\"+$c+\"`t\"+$cod}");
        sb.AppendLine("'END'");
        return sb.ToString();
    }

    static Dictionary<string, object> Status()
    {
        string output = RunPowerShell(Script(), 15000);
        return Parse(output);
    }

    public static Dictionary<string, object> Parse(string output)
    {
        var lines = (output ?? "").Replace("\r", "").Split('\n');
        bool ended = false;
        string radio = null;
        bool radioOk = false;
        var devices = new List<object>();
        foreach (var raw in lines)
        {
            var line = raw.TrimStart((char)0xFEFF);
            if (line.Trim() == "END") { ended = true; continue; }
            if (!line.StartsWith("D\t")) continue;
            var f = line.Split('\t');
            if (f.Length < 7) continue;
            string status = f[1].Trim(), id = f[3].Trim(), name = f[4].Trim(), connected = f[5].Trim(), cod = f[6].Trim();
            bool present = string.Equals(f[2].Trim(), "True", StringComparison.OrdinalIgnoreCase);
            if (!present) continue;
            string up = id.ToUpperInvariant();
            if (up.StartsWith("BTHENUM\\DEV_") || up.StartsWith("BTHLE\\DEV_"))
            {
                var m = Regex.Match(up, "DEV_([0-9A-F]{12})");
                string address = m.Success ? FormatMac(m.Groups[1].Value) : "";
                var d = new Dictionary<string, object>();
                d["address"] = address;
                d["name"] = name;
                d["type"] = TypeOf(cod, name);
                d["paired"] = true;
                d["le"] = up.StartsWith("BTHLE\\");
                if (string.Equals(connected, "True", StringComparison.OrdinalIgnoreCase)) d["connected"] = true;
                else if (string.Equals(connected, "False", StringComparison.OrdinalIgnoreCase)) d["connected"] = false;
                else d["connected"] = null;
                d["state"] = status == "OK" ? "Available" : (status.Length > 0 ? status : "Unknown");
                devices.Add(d);
                continue;
            }
            if (up.StartsWith("BTH\\") || up.StartsWith("BTHENUM\\") || up.StartsWith("BTHLE\\") || up.StartsWith("BTHLEDEVICE\\") || up.StartsWith("SWD\\") || up.StartsWith("ROOT\\")) continue;
            bool ok = status == "OK";
            if (radio == null || (ok && !radioOk)) { radio = name; radioOk = ok; }
        }
        if (!ended) throw new Exception("Windows did not list Bluetooth devices");
        var r = new Dictionary<string, object>();
        r["platform"] = "windows";
        r["available"] = radio != null;
        r["adapter"] = radio ?? "";
        r["adapterOk"] = radioOk;
        r["powered"] = null;
        r["canPower"] = false;
        r["canPair"] = false;
        r["devices"] = devices;
        return r;
    }

    static string FormatMac(string hex12)
    {
        var sb = new StringBuilder();
        for (int i = 0; i < 12; i += 2) { if (i > 0) sb.Append(':'); sb.Append(hex12.Substring(i, 2)); }
        return sb.ToString();
    }

    public static string TypeOf(string cod, string name)
    {
        long c;
        if (long.TryParse(cod, out c) && c > 0)
        {
            int major = (int)((c >> 8) & 0x1F), minor = (int)((c >> 2) & 0x3F);
            if (major == 1) return "computer";
            if (major == 2) return "phone";
            if (major == 4) return "audio";
            if (major == 5)
            {
                int sub = minor & 0x0F, kind = (minor >> 4) & 3;
                if (sub == 1 || sub == 2) return "gamepad";
                if (kind == 2) return "mouse";
                if (kind == 1 || kind == 3) return "keyboard";
            }
        }
        string n = (name ?? "").ToLowerInvariant();
        if (Regex.IsMatch(n, "controller|gamepad|dualsense|dualshock|joy-con|joystick")) return "gamepad";
        if (Regex.IsMatch(n, "headphone|headset|earbud|buds|speaker|earphone")) return "audio";
        if (n.Contains("keyboard")) return "keyboard";
        if (n.Contains("mouse") || n.Contains("trackpad")) return "mouse";
        return "other";
    }

    static string RunPowerShell(string script, int timeoutMs)
    {
        var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -NonInteractive -EncodedCommand " + Convert.ToBase64String(Encoding.Unicode.GetBytes(script)));
        psi.CreateNoWindow = true;
        psi.UseShellExecute = false;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.RedirectStandardInput = true;
        psi.StandardOutputEncoding = Encoding.UTF8;
        using (var p = Process.Start(psi))
        {
            p.StandardInput.Close();
            Task<string> o = p.StandardOutput.ReadToEndAsync();
            Task<string> e = p.StandardError.ReadToEndAsync();
            if (!p.WaitForExit(timeoutMs))
            {
                try { p.Kill(); } catch { }
                throw new Exception("Windows did not answer about Bluetooth in time");
            }
            p.WaitForExit();
            return o.Result;
        }
    }
}
