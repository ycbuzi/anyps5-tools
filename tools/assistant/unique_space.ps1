# Space a folder would free if deleted: the bytes of files with no other hard link on the volume. Test copies under
# _test are mostly hard links to a game's app0, so their size on disk is not what deleting them frees (11-05: deleting
# Astro's old D: copy freed 3 GB of 29 because _test\_fps\Astro's Playroom still linked the files).
# usage: unique_space.ps1 -Root "D:\AnyPS5 Games\_test" [-Depth 2] [-Out file.json]
param([Parameter(Mandatory = $true)][string]$Root, [int]$Depth = 1, [string]$Out = "")
$ErrorActionPreference = "Stop"
if (-not ("LinkCount" -as [type])) {
    Add-Type @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class LinkCount {
    [StructLayout(LayoutKind.Sequential)]
    struct Info { public uint Attributes; public long Created, Accessed, Written; public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetFileInformationByHandle(SafeFileHandle h, out Info i);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr template);
    public static int Of(string path) {
        using (var h = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            Info i; if (h.IsInvalid || !GetFileInformationByHandle(h, out i)) return -1; return (int)i.Links;
        }
    }
}
'@
}
function Folders([string]$dir, [int]$left) {
    foreach ($d in Get-ChildItem -LiteralPath $dir -Directory -Force) {
        if ($d.LinkType) { continue }
        if ($left -gt 1 -and $d.Name.StartsWith("_")) { Folders $d.FullName ($left - 1) } else { $d }
    }
}
$rows = foreach ($d in Folders $Root $Depth) {
    $all = 0L; $own = 0L
    foreach ($f in Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue) {
        $all += $f.Length
        if ([LinkCount]::Of($f.FullName) -eq 1) { $own += $f.Length }
    }
    [pscustomobject]@{ folder = $d.FullName; sizeGB = [math]::Round($all / 1GB, 1); freesGB = [math]::Round($own / 1GB, 1); written = $d.LastWriteTime.ToString("MM-dd HH:mm") }
}
$rows = @($rows | Sort-Object freesGB -Descending)
if ($Out) { $rows | ConvertTo-Json | Set-Content -LiteralPath $Out -Encoding utf8 }
$rows | Format-Table -AutoSize | Out-String -Width 250
"total frees: {0:N1} GB" -f (($rows | Measure-Object freesGB -Sum).Sum)
