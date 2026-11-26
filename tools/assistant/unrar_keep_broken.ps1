# Unpacks a .rar that is cut short (the download ends early), keeping the partly written last file instead of deleting
# it (UnRAR -kb), into the folder the pipeline takes as "already extracted by hand": <game folder>\<archive name>\.
# It tries no password, then each line of archive-passwords.txt, and never prints a password.
param([Parameter(Mandatory = $true)][string]$Archive)
$UnRar = "C:\Program Files\WinRAR\UnRAR.exe"
$a = Get-Item -LiteralPath $Archive
$stem = $a.Name -replace '(\.part0*1)?\.rar$', ''
$dest = Join-Path $a.DirectoryName $stem
New-Item -ItemType Directory -Force $dest | Out-Null
$lines = @(Get-Content -LiteralPath "D:\AnyPS5 Games\_tools\archive-passwords.txt" -ErrorAction SilentlyContinue | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
$tries = @("-p-") + @($lines | ForEach-Object { "-p" + $_ })
for ($i = 0; $i -lt $tries.Count; $i++) {
    $label = if ($i -eq 0) { "no password" } else { "password line $i" }
    $out = @(& $UnRar t -idq $tries[$i] $a.FullName 2>&1 | ForEach-Object { "$_" })
    if (($out -join ' ') -match 'Incorrect password') { continue }
    "unpacking with $label, keeping a damaged last file"
    & $UnRar x -y -o- -kb -idc $tries[$i] $a.FullName ($dest + "\") 2>&1 | ForEach-Object { "$_" -replace '\s*\[[A-Za-z0-9-]+\.(COM|NET|ORG|IO)\]\s*-?', '' } | Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*\d+%\s*$' }
    "UnRAR exit $LASTEXITCODE (3 = a file failed its checksum, expected here)"
    $n = @(Get-ChildItem -LiteralPath $dest -Recurse -File -Force).Count
    "$n files now in the folder"
    if (Get-ChildItem -LiteralPath $dest -Recurse -File -Filter eboot.bin -Force | Select-Object -First 1) { exit 0 }
    "no eboot.bin came out of it"
    exit 1
}
"none of the saved passwords opens it"
exit 1
