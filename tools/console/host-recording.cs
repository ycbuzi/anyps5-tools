// Video clips and recent gameplay (sysCall "recording"). This PC has no screen recorder and nothing may be installed,
// so status reports supported=false and every other action is left to AnyPS5 OS (os-host/areas/recording.py).
using System;
using System.Collections.Generic;

static class Sys_Recording
{
    public static object Handle(string action, Dictionary<string, object> args)
    {
        if (action == "status")
            return new Dictionary<string, object> {
                { "supported", false }, { "recording", false }, { "replay", false },
                { "replaySupported", false }, { "micSupported", false }, { "backend", "" }, { "platform", "windows" },
                { "reason", "AnyPS5.exe has no screen recorder on Windows. Video clips are recorded in AnyPS5 OS." } };
        throw new NotSupportedException("Available in AnyPS5 OS");
    }
}
