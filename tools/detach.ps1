param([Parameter(Mandatory)][string]$Command, [string]$WorkDir = "C:\AnyPS5 Games\_tools")
$line = "conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$($Command -replace '"', '\"')`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $line; CurrentDirectory = $WorkDir }
if ($r.ReturnValue -ne 0) { Write-Output "detach failed: $($r.ReturnValue)"; exit 1 }
Write-Output "started pid $($r.ProcessId) outside this process tree"
