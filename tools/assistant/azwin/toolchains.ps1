# Installs the Windows toolchain matrix on anyps5-win from official GitHub releases (11-08). Re-runnable.
# C:\tools\tc\<name>: gcc13/gcc14 x UCRT/MSVCRT (WinLibs), clang18/19/20 (llvm-mingw, UCRT). GCC 15 UCRT is C:\tools\mingw64.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
$dl = 'C:\x64win\dl'; $tc = 'C:\tools\tc'
New-Item -ItemType Directory -Force $dl, $tc | Out-Null
$wl = 'https://github.com/brechtsanders/winlibs_mingw/releases/download'
$lm = 'https://github.com/mstorsjo/llvm-mingw/releases/download'
$set = [ordered]@{
    'gcc13-ucrt'   = "$wl/13.3.0posix-11.0.1-ucrt-r1/winlibs-x86_64-posix-seh-gcc-13.3.0-mingw-w64ucrt-11.0.1-r1.zip"
    'gcc13-msvcrt' = "$wl/13.3.0posix-11.0.1-msvcrt-r1/winlibs-x86_64-posix-seh-gcc-13.3.0-mingw-w64msvcrt-11.0.1-r1.zip"
    'gcc14-ucrt'   = "$wl/14.3.0posix-12.0.0-ucrt-r1/winlibs-x86_64-posix-seh-gcc-14.3.0-mingw-w64ucrt-12.0.0-r1.zip"
    'gcc14-msvcrt' = "$wl/14.3.0posix-12.0.0-msvcrt-r1/winlibs-x86_64-posix-seh-gcc-14.3.0-mingw-w64msvcrt-12.0.0-r1.zip"
    'clang18'      = "$lm/20240619/llvm-mingw-20240619-ucrt-x86_64.zip"
    'clang19'      = "$lm/20250114/llvm-mingw-20250114-ucrt-x86_64.zip"
    'clang20'      = "$lm/20250709/llvm-mingw-20250709-ucrt-x86_64.zip"
}
foreach ($k in $set.Keys) {
    $dest = "$tc\$k"
    if (Test-Path "$dest\.ok") { "$k already installed"; continue }
    $zip = "$dl\$k.zip"
    Invoke-WebRequest -UseBasicParsing $set[$k] -OutFile $zip
    Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $dest | Out-Null
    tar -xf $zip -C $dest
    $bin = (Get-ChildItem $dest -Directory | Select-Object -First 1).FullName + '\bin'
    $cc = if ($k -like 'clang*') { "$bin\clang++.exe" } else { "$bin\g++.exe" }
    if (Test-Path $cc) { Set-Content "$dest\.ok" $bin; Remove-Item $zip; "$k -> $(& $cc --version | Select-Object -First 1)" } else { "$k FAILED: no $cc" }
}
