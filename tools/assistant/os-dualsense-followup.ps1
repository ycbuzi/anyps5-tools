$ErrorActionPreference = "Continue"
$log = "C:\AnyPS5 Games\_reports\os-dualsense-followup.log"
$out = "C:\AnyPS5 Games\_reports\os-dualsense-shots"
New-Item -ItemType Directory -Force $out | Out-Null
function Note($t) { Add-Content -Encoding utf8 $log ("{0} {1}" -f (Get-Date -Format "MM-dd HH:mm"), $t) }
. "C:\AnyPS5 Games\_tools\assistant\skills\gpu_flag.ps1"
Note "waiting for the PC lock"
Use-Pc "os-dualsense console build + shots" 50 {
    $env:MSYS_NO_PATHCONV = "1"
    $script = @'
set -e
cd "/mnt/c/AnyPS5 Games/_tools/console-native"
rm -f /root/acn/osdualsense/build/build.ninja
sh tools/build.sh osdualsense > /root/acn/osdualsense-build.log 2>&1 || { echo "BUILD FAILED"; grep -m5 -E "error" /root/acn/osdualsense-build.log; exit 1; }
echo "build ok"
O="/mnt/c/AnyPS5 Games/_reports/os-dualsense-shots"
D=down,down,down,down,down,down,down,down,ok
sh tools/shot.sh osdualsense "$O/empty.png" "$D,down" tests/fixtures/controller-empty.json --start settings >/dev/null 2>&1 && echo "shot empty"
sh tools/shot.sh osdualsense "$O/pads.png" "$D,down,right" tests/fixtures/controller.json --start settings >/dev/null 2>&1 && echo "shot pads"
sh tools/shot.sh osdualsense "$O/vibration.png" "$D,down,down,right" tests/fixtures/controller.json --start settings >/dev/null 2>&1 && echo "shot vibration"
sh tools/shot.sh osdualsense "$O/buttons.png" "$D,down,down,down,down,right" tests/fixtures/controller.json --start settings >/dev/null 2>&1 && echo "shot buttons"
'@
    $tmp = "C:\AnyPS5 Games\_reports\os-dualsense-shots\run.sh"
    [IO.File]::WriteAllText($tmp, $script.Replace("`r", ""))
    $res = wsl.exe -d archlinux -u root -- bash "/mnt/c/AnyPS5 Games/_reports/os-dualsense-shots/run.sh" 2>&1
    foreach ($l in $res) { Note $l }
    Remove-Item $tmp -ErrorAction SilentlyContinue
}
Note "done; deploy is a separate step: bash _tools/anyps5-os-stick/deploy-native.sh 192.0.2.1 /root/acn/osdualsense/build"
schtasks /Delete /TN "AnyPS5 os-dualsense follow-up" /F | Out-Null
