# Runs the GPU tests of the queued GPU pull requests on the PC, one after another (gpu_prtest.py has the details).
# Holds _tools\live\gpu-busy.flag through gpu_flag.ps1 so the pipeline and the GPU lanes wait, refreshing it while a run
# lasts. Scheduled task "AnyPS5 GPU PR tests" starts it every 30 minutes; it exits at once when nothing is queued.
param([int]$Max = 3)
$ErrorActionPreference = "Continue"
$py = "%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe"
$tool = Join-Path $PSScriptRoot "gpu_prtest.py"
. (Join-Path $PSScriptRoot "gpu_flag.ps1")
# Blu plays on this PC: no GPU run while a game of his is open (a game from the games folder that no GPU lane
# started, or Rocket League).
$lanePids = @(Get-ChildItem -LiteralPath "C:\AnyPS5 Games\_tools\live\lanes" -Filter *.json -ErrorAction SilentlyContinue | ForEach-Object { try { (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).pid } catch { } })
$playing = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { ($_.Path -like "D:\AnyPS5 Games\*" -and $_.ProcessName -ne "AnyPS5 Games" -and $lanePids -notcontains $_.Id) -or $_.ProcessName -like "RocketLeague*" })
if ($playing.Count) { exit 0 }
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\AnyPS5GpuPrTests", [ref]$created)
if (-not $created) { exit 0 }
for ($i = 0; $i -lt $Max; $i++) {
    $pr = (& $py -I $tool next | Select-Object -First 1)
    if (-not $pr) { break }
    Enter-GpuFlag "GPU tests of PR #$pr"
    try {
        $p = Start-Process -FilePath $py -ArgumentList "-I", "`"$tool`"", "run-one", $pr -NoNewWindow -PassThru
        $null = $p.Handle
        while (-not $p.WaitForExit(240000)) { Touch-GpuFlag }
    } finally { Exit-GpuFlag }
}
