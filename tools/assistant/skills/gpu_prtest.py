"""GPU runs of pull requests that change GPU code, on the PC's three GPUs (11-11, Blu: "whatever the graphics test
needs, we can test the graphics part ... we don't have to post until all the test is completely green"). No AI calls.

prpost.py queues such PRs in _reports/pr-review/gpu-queue.json ({pr: head}). For each one not yet run at that head this
runs the existing helper winrun (D:\\AnyPS5\\scratchpad\\helpers\\winrun.ps1 <pr> --gpus) in its own worktree and build
folder (never the pipeline's wt-pr), with the tests of every GPU library the PR touches added, ANYPS5_REQUIRE_VULKAN=1
and the shader cache off, on the RTX 4070 SUPER, GTX 1650 SUPER and Radeon iGPU. A test that fails on a GPU counts as
new unless last night's health run of main (helpers/health/state-latest.json) failed it on the same GPU too. The
result is written as _reports/pr-review/<pr>/gpu-windows.md, which prpost.py reads: clean -> the PR's comment is posted
with the GPU line; anything else -> needs-review.md.

    python gpu_prtest.py run-one <pr>      run one PR now (used by gpu_prtest.ps1, which holds the GPU flag)
    python gpu_prtest.py next              print the next queued PR, or nothing
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REVIEW = Path(r"C:\AnyPS5 Games\_reports\pr-review")
QUEUE = REVIEW / "gpu-queue.json"
DONE = REVIEW / "gpu-done.json"
HELPERS = Path(r"D:\AnyPS5\scratchpad\helpers")
HEALTH = HELPERS / "health" / "state-latest.json"
TREE, BUILD = r"D:\AnyPS5\wt-gputest", r"D:\AnyPS5\build\gputest"
LABELS = {"GeForce RTX 4070 SUPER": "4070", "GeForce GTX 1650 SUPER": "1650", "Radeon iGPU": "Radeon"}
AREAS = [("AGC", r"libSceAgcDriver|libSceAgc\b|libSceAgc/",
          r"^agc_driver$|^agc_driver_(draw|frame)_|^agc_(initialization|commands|screenshot|draw_setup|create_shader)$"),
         ("shader recompiler", r"ShaderRecompiler|core/shader",
          r"^agc_shader|^shader|^agc_spirv|^agc_structurizer|^agc_gcn|^agc_(vop|mubuf|wave)|^agc_driver_(vop3|sdwa|scalar|flat|f32|f64|lds|float16|packed|vop|smem|vmem|ds_)"),
         ("VideoOut", r"libSceVideoOut", r"video_?out"), ("Gnm", r"libSceGnm", r"gnm")]
GPU_TEST = r"(agc|video_?out|gnm|shader)"
GENERIC = {"export", "exports", "api", "module", "types", "common", "util", "utils", "internal", "main", "impl", "state", "driver", "context"}


def selection(pr, files):
    areas = [(name, smoke) for name, pattern, smoke in AREAS if any(re.search(pattern, f) for f in files)] or [AREAS[0][::2]]
    parts = [smoke for _, smoke in areas]
    stems = set()
    for f in files:
        p = Path(f)
        if p.suffix not in (".cpp", ".c", ".hpp", ".h", ".inl") or not any(re.search(pattern, f) for _, pattern, _ in AREAS):
            continue
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", p.stem).lower().removesuffix("_tests").removesuffix("_test")
        if len(snake) >= 5 and snake not in GENERIC:
            stems.add(re.escape(snake))
    if stems:
        parts.append(rf"^{GPU_TEST}\w*({'|'.join(sorted(stems))})")
    body = subprocess.run(["gh", "pr", "view", pr, "-R", "boykopovar/AnyPS5", "--json", "body", "--jq", ".body"], capture_output=True, text=True, encoding="utf-8").stdout
    named = sorted({m for m in re.findall(r"`([A-Za-z0-9_.\-]+)`", body) if re.match(GPU_TEST, m)})
    if named:
        parts.append("^(" + "|".join(re.escape(n) for n in named) + ")$")
    return "|".join(parts), [name for name, _ in areas]


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gh_files(pr):
    r = subprocess.run(["gh", "api", "--paginate", f"repos/boykopovar/AnyPS5/pulls/{pr}/files?per_page=100", "--jq", ".[].filename"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.stdout.splitlines()


def next_pr():
    queue, done = load(QUEUE), load(DONE)
    for pr, head in sorted(queue.items(), key=lambda x: -int(x[0])):
        if done.get(pr, {}).get("head") != head:
            return pr
    return ""


def run_one(pr):
    head = load(QUEUE).get(pr, "")
    files = gh_files(pr)
    extra, areas = selection(pr, files)
    report = HELPERS / "reports" / f"pr-{pr}.md"
    started = time.time()
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPERS / "winrun.ps1"), pr, "--gpus", "--extra", extra,
           "--no-control", "--worktree", TREE, "--build", BUILD, "--timeout", "300"]
    print(f"#{pr}: winrun on three GPUs, extra tests {extra[:300]}", flush=True)
    (REVIEW / pr).mkdir(parents=True, exist_ok=True)
    with open(REVIEW / pr / "gpu-winrun.log", "w", encoding="utf-8", errors="replace") as console:
        code = subprocess.run(cmd, cwd=str(HELPERS), stdout=console, stderr=subprocess.STDOUT).returncode
    text = report.read_text(encoding="utf-8", errors="replace") if report.exists() and report.stat().st_mtime >= started - 5 else ""
    baseline = load(HEALTH).get("gpus", {})
    main_sha = load(HEALTH).get("sha", "?")
    lines, runs, new, main_too, block = [], [], [], [], None
    for line in text.splitlines():
        m = re.match(r"ctest \((.+?)\) exit (\d+): (.*)", line)
        if m:
            block = LABELS.get(m.group(1), m.group(1))
            runs.append(f"run {len(runs) + 1}: {m.group(3)} ({m.group(1)})")
            continue
        m = re.match(r"  ([A-Za-z0-9_.\-]+): (.+)", line)
        if block and m and not line.startswith("  devices") and not line.startswith("  >"):
            name = m.group(1)
            if baseline.get(block, {}).get(name, "passed") != "passed":
                main_too.append(f"{name} ({block})")
            else:
                new.append(f"{name} ({block}: {m.group(2)[:40]})")
            continue
        if not line.startswith("  "):
            block = None
    count = re.search(r"^tests \((\d+);", text, re.M)
    scope = (f"{count.group(1) if count else 'the selected'} GPU tests (the PR's own tests, the tests named after the GPU files it changes "
             f"and the {', '.join(areas)} smoke set), each run twice")
    merged = re.search(r"#\d+ head (\w+) merged onto main (\w+) -> (\w+)", text)
    build_bad = re.search(r"build \(PR\) exit [1-9]", text) or not merged
    blocked = re.search(r"STALE PULL REF|MERGE CONFLICT|MERGE FAILED|CMAKE FAILED|no tests selected", text)
    out = REVIEW / pr / "gpu-windows.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if blocked or not runs:
        why = blocked.group(0) if blocked else f"winrun exit {code}, no ctest result"
        body = [f"# #{pr} GPU run on the PC", f"head {head[:10]} not tested: {why}", "new failures: ?", ""]
    else:
        body = [f"# #{pr} GPU run on the PC (winrun, three GPUs)",
                f"head {merged.group(1)} merged into main {merged.group(2)} as {merged.group(3)}; build rc {1 if build_bad else 0}; " + "; ".join(runs),
                f"failing on main too: {' '.join(main_too) or 'none'}", f"new failures: {' '.join(new) or 'none'}", f"selection: {extra}", "",
                "## Draft comment",
                f"Tested on Windows 11 on three GPUs (RTX 4070 SUPER, GTX 1650 SUPER, Radeon iGPU), full build with Vulkan required and the shader cache off, {scope}; PR head `{merged.group(1)}` merged into main `{merged.group(2)}` as `{merged.group(3)}`.",
                f"Baseline: the nightly run of main `{main_sha}` on the same GPUs."]
    out.write_text("\n".join(body) + "\n\n## winrun report\n```\n" + text[-6000:] + "\n```\n", encoding="utf-8")
    done = load(DONE)
    done[pr] = {"head": head, "at": time.strftime("%m-%d %H:%M"), "new": new, "runs": len(runs)}
    DONE.write_text(json.dumps(done, indent=1), encoding="utf-8")
    follow = load(REVIEW / "gpu-followup.json").get(pr)
    if follow and runs and not blocked:
        if new:
            with open(REVIEW / "needs-review.md", "a", encoding="utf-8") as f:
                f.write(f"- #{pr} head {head[:10]}: gpu-windows.md: new {' '.join(new)} (already posted with the no-GPU note; comment {follow})\n")
        else:
            got = subprocess.run(["gh", "api", f"repos/boykopovar/AnyPS5/issues/comments/{follow}", "--jq", ".body"], capture_output=True, text=True, encoding="utf-8").stdout
            gpu_line = (f"**GPU:** {scope} passed on the PC's three GPUs (RTX 4070 SUPER, GTX 1650 SUPER, Radeon iGPU), "
                        f"Vulkan required and the shader cache off, merged into main `{merged.group(2)}`: " + "; ".join(runs) + ". Nothing fails that last night's run of main did not fail on the same GPU.")
            fixed = re.sub(r"\*\*Not covered: GPU\.\*\*[^\n]*", lambda _: gpu_line, got, count=1)
            if fixed != got:
                r = subprocess.run(["gh", "api", "-X", "PATCH", f"repos/boykopovar/AnyPS5/issues/comments/{follow}", "-f", f"body={fixed}", "--jq", ".html_url"], capture_output=True, text=True, encoding="utf-8")
                print(f"#{pr}: updated the posted comment with the GPU result: {r.stdout.strip() or r.stderr.strip()[:160]}", flush=True)
        return
    sys.path.insert(0, str(Path(__file__).parent))
    import prreports
    s = prreports.load()
    s["pending"].setdefault(pr, [])
    if "gpu-windows" not in s["pending"][pr]:
        s["pending"][pr].append("gpu-windows")
    prreports.save(s)
    print(f"#{pr}: {len(runs)} GPU runs, new failures: {', '.join(new) or 'none'}" + (f" ({body[1]})" if blocked or not runs else ""), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run-one":
        run_one(sys.argv[2])
    else:
        print(next_pr())
