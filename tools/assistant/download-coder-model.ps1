# One-off (Blu approved 2026-10-29): downloads Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf (16.75 GB) from the llama.cpp
# team's Hugging Face page (ggml-org) into C:\fragnetic_llm, resuming where it stopped, and checks it against the
# SHA-256 Hugging Face publishes. Runs as the scheduled task "AnyPS5 coder model download".
$url = "https://huggingface.co/ggml-org/Qwen3-Coder-30B-A3B-Instruct-GGUF/resolve/main/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
$part = "C:\fragnetic_llm\Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf.part"
$final = "C:\fragnetic_llm\Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
$sha = "f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335"
$log = "C:\fragnetic_llm\coder-model-download.log"
function Log([string]$t) { Add-Content -LiteralPath $log -Value ("{0}  {1}" -f (Get-Date -Format "MM-dd HH:mm:ss"), $t) }
if (Test-Path -LiteralPath $final) { Log "already downloaded"; exit 0 }
Log "downloading $url"
foreach ($try in 1..20) {
    & curl.exe -L --fail --retry 5 --retry-delay 10 -C - -o $part $url 2>&1 | Select-Object -Last 2 | ForEach-Object { Log "curl: $_" }
    if ($LASTEXITCODE -eq 0) { break }
    Log "curl exit $LASTEXITCODE (try $try); retrying in 60 s"; Start-Sleep 60
}
$got = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLower()
if ($got -eq $sha) { Move-Item -LiteralPath $part -Destination $final; Log "done: SHA-256 matches; $final" }
else { Log "SHA-256 MISMATCH ($got): the file was kept as $part and is not used" }
