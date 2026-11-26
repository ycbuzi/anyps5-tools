$c = "$env:USERPROFILE\.local\bin\claude.exe"
$w = New-Object -ComObject WScript.Shell
$l = $w.CreateShortcut("$env:USERPROFILE\Desktop\Claude Code.lnk")
$l.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$l.Arguments = "-NoExit -Command & '$c'"
$l.WorkingDirectory = 'C:\x64win'
$l.IconLocation = $c
$l.Save()
"$(& $c --version), desktop shortcut: $(Test-Path "$env:USERPROFILE\Desktop\Claude Code.lnk")"
