# Run on the PC (11-09): applies azwin\vm-tune.ps1 to every Windows test VM that is running. Blu runs this himself.
$tune = Join-Path $PSScriptRoot 'azwin\vm-tune.ps1'
foreach ($vm in 'azwin', 'azws2016', 'azws2019', 'azws2022', 'azwin10', 'azwin11', 'azwin11arm') {
    & scp.exe -q -o ConnectTimeout=15 $tune "${vm}:C:/x64win/vm-tune.ps1" 2>$null
    if ($LASTEXITCODE -ne 0) { "${vm}: not reachable (stopped?), skipped"; continue }
    & ssh.exe -n -o ConnectTimeout=15 $vm 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\x64win\vm-tune.ps1' 2>$null | Select-Object -Last 1
}
