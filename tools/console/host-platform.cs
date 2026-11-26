// System areas the AnyPS5 screen asks the host about (sysCall in index.html). Each area is a static class Sys_<Area>
// with Handle(action, args) in its own host-<area>.cs; AnyPS5.cs finds it by name. Throw NotSupportedException for what
// Windows cannot or must not do ("Available in AnyPS5 OS"); the Chromebook host (os-host/host.py) answers the same calls.
using System;
using System.Collections.Generic;

static class Sys_Platform
{
    public static object Handle(string action, Dictionary<string, object> args)
    {
        if (action == "info")
            return new Dictionary<string, object> { { "platform", "windows" }, { "os", Environment.OSVersion.VersionString }, { "machine", Environment.MachineName }, { "testMode", ConsoleView.TestMode } };
        throw new NotSupportedException("unknown platform action " + action);
    }
}
