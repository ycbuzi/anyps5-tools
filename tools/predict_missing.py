# Predicted missing functions: for each built game, every function its executable and modules import (from the
# relinker's own --registry lists), checked against the AnyPS5 build the game uses, with AnyPS5's tools/import_audit.py
# taken from that build's commit. A function is predicted missing when no AnyPS5 library exports it ("absent"), its
# AnyPS5 code always stops with NotImplemented ("stub"), or it stops for some inputs ("partial").
#
#   python -I predict_missing.py --game "<game folder>"     one game (after its build), then the cross-game ranking
#   python -I predict_missing.py --all                      every built game (after an AnyPS5 update)
#   python -I predict_missing.py --all --max-relinks 0      only games that already have their import lists
#
# Import lists: build-game.ps1 keeps the relinker's lists in <game>\imports\ (stamp.json says which .exe they belong
# to). A game built before that gets them from a second relinker run into C:\AnyPS5-work\predict\relink\<game>\ (the
# dump and the game folder are only read). Nothing is ever deleted; the relink folder is reused per game.
# Writes _reports\predicted\<game>.md/.json and _reports\predicted-missing.md/.json. Nothing is downloaded: names
# come from the build's own source and the NID list AnyPS5's nid_names.py already cached (%TEMP%\anyps5-nid-db).
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders stay on D: (11-03 _tools moved to C: behind a link)
REPORTS = GAMES / "_reports"
PREDICTED = REPORTS / "predicted"
WORK = Path(os.environ.get("ANYPS5_PREDICT_WORK", r"C:\AnyPS5-work\predict"))
REPO = Path(os.environ.get("ANYPS5_PREDICT_REPO", r"D:\AnyPS5\wt-pr"))
NAMES = Path(os.environ.get("ANYPS5_NID_DB") or Path(tempfile.gettempdir()) / "anyps5-nid-db" / "aerolib.csv")
MISSING = ("absent", "stub", "partial")
SEVERITY = {"absent": 0, "stub": 1, "partial": 2}
WHAT = {"absent": "no AnyPS5 library has it", "stub": "AnyPS5 has it as a stub that always stops",
        "partial": "AnyPS5 stops for some inputs"}


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def log(text):
    print(text, flush=True)


def built_games():
    for folder in sorted(GAMES.iterdir(), key=lambda p: p.name.lower()):
        if folder.is_dir() and not folder.name.startswith(("_", ".")) and (folder / "game.json").is_file() and game_exe(folder, load_json(folder / "game.json", {})):
            yield folder


def game_exe(folder, cfg):
    named = folder / ((cfg.get("exeName") or cfg.get("title") or "") + ".exe")
    if named.is_file():
        return named
    found = sorted(folder.glob("*.exe"))
    return found[0] if found else None


def build_of(cfg, settings):
    root = Path(cfg.get("anyps5Build") or settings["anyps5Build"])
    commit_file = root / "COMMIT.txt"
    commit = commit_file.read_text(encoding="utf-8-sig").split()[0] if commit_file.is_file() else ""
    return root, commit


# The build's own tools/ and core/libs/prx/ at its commit, unpacked once from git (the PR worktree stays where it is).
def source_tree(commit):
    dest = WORK / f"src-{commit}"
    if (dest / ".complete").is_file():
        return dest
    WORK.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", commit, "tools", "core/libs/prx"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"git archive {commit} failed: {result.stderr.decode(errors='replace').strip()}")
    if dest.exists():
        dest.rename(dest.with_name(dest.name + f".incomplete-{int(time.time())}"))
    staging = dest.with_name(dest.name + f".staging-{os.getpid()}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        archive.extractall(staging, filter="data")
    (staging / ".complete").write_text(commit, encoding="utf-8")
    staging.rename(dest)
    return dest


def load_audit(source):
    for name in ("import_audit", "nid_names", "progress"):
        sys.modules.pop(name, None)
    tools = str(source / "tools")
    sys.path[:] = [tools] + [entry for entry in sys.path if not entry.endswith(os.sep + "tools") and "src-" not in entry]
    import import_audit
    return import_audit


# What the build exports and which of its functions are stubs; cached per commit and libs folder.
def build_facts(audit, root, commit, source):
    libs = root / "core" / "libs" / "libs"
    files = sorted(libs.glob("*.prx"))
    signature = hashlib.sha1("|".join(f"{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}" for f in files).encode()).hexdigest()
    cache = WORK / f"facts-{commit}-{hashlib.sha1(str(root).lower().encode()).hexdigest()[:8]}.json"
    facts = load_json(cache)
    if facts and facts.get("signature") == signature:
        return facts
    log(f"reading the {len(files)} libraries of {root.name} and the stubs at {commit} (once per build)")
    exports, windows = defaultdict(set), set()
    for file in files:
        data = file.read_bytes()
        if data[:2] == b"MZ":
            windows.add(file.name)
        for name in audit.read_exports(file, data):
            exports[name].add(file.name)
    stubs, partial = audit.source_stub_classes(source / "core" / "libs" / "prx")
    facts = {"signature": signature, "root": str(root), "commit": commit,
             "exports": {name: sorted(owners) for name, owners in exports.items()}, "libraries": sorted(f.name for f in files),
             "windows": sorted(windows), "stubs": [[p, n, name] for (p, n), name in stubs.items()], "partial": [[p, n] for p, n in partial]}
    write_text(cache, json.dumps(facts))
    return facts


def exe_stamp(exe):
    st = exe.stat()
    return {"exe": exe.name, "exeSize": st.st_size, "exeTime": int(st.st_mtime)}


def kept_registries(game, exe):
    stamp = load_json(game / "imports" / "stamp.json")
    if not stamp:
        return None
    now = exe_stamp(exe)
    files = [game / "imports" / name for name in stamp.get("files", [])]
    if any(stamp.get(key) != now[key] for key in ("exeSize", "exeTime")) or not files or not all(f.is_file() for f in files):
        return None
    return files, stamp


def safe_name(text):
    return "".join(c for c in text if c.isalnum() or c in " -_().").strip()[:80] or "game"


# Same conversion as build-game.ps1 (patches and module folders included), into a work folder, only for its lists.
def relink(game, cfg, exe, root, commit):
    eboot = cfg.get("eboot") or "eboot.bin"
    dump = Path(cfg.get("dump") or "")
    if not (dump / eboot).is_file():
        dump = game / "app0"
        if not (dump / eboot).is_file():
            raise RuntimeError(f"neither the dump nor the game's app0 has {eboot}")
    relinker = root / "core" / "relinker" / "relinker.exe"
    work = WORK / "relink" / safe_name(game.name)
    work.mkdir(parents=True, exist_ok=True)
    args = [str(relinker), "--windows", "--windows-gui", "--registry"]
    patches = sorted(p for p in (game / "mods" / "patches").glob("*.toml") if not p.name.startswith("_"))
    if patches:
        merged = work / "mods.patches.toml"
        merged.write_text("".join(f"# from mods\\patches\\{p.name}\n" + p.read_text(encoding="utf-8-sig") + "\n" for p in patches), encoding="utf-8")
        args += ["--patches", str(merged)]
    else:
        args += ["--no-patches"]
    module_dirs = cfg.get("moduleDirs") or []
    for m in [module_dirs] if isinstance(module_dirs, str) else module_dirs:
        if m and m.strip("/\\") not in ("prx", "sce_module", "sce_modules"):
            args += ["--module-dir", m]
    if cfg.get("skipSceModule"):
        args += ["--skip-sce-module"]
    started = time.time() - 2
    output = work / "probe.exe"
    log(f"  relinking {game.name} once for its import lists (from {dump})")

    def run(folder):
        done = subprocess.run(args + [eboot, str(output)], cwd=folder, capture_output=True, text=True, errors="replace", timeout=3600,
                              creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        (work / "relinker-output.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
        return done

    result = run(dump)
    # A damaged sce_sys file (Silt: icon0.png without image data) stops the .exe, not the import lists: convert a copy
    # of only the eboot and the module folders.
    if result.returncode != 0 and "icon" in (result.stdout + result.stderr).lower():
        bare = work / "input"
        log("  the relinker stopped on the game's icon; relinking a copy of the eboot and modules without sce_sys")
        for rel in [eboot] + [str(Path(m) / f.name) for m in ["sce_module"] + [d for d in (module_dirs if isinstance(module_dirs, list) else [module_dirs]) if d]
                              for f in sorted((dump / m).glob("*")) if f.is_file() and f.suffix.lower() in (".prx", ".sprx") and not f.name.endswith(".guest.prx")]:
            target = bare / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != (dump / rel).stat().st_size:
                target.write_bytes((dump / rel).read_bytes())
        for m in module_dirs if isinstance(module_dirs, list) else [module_dirs]:
            if m:
                (bare / m).mkdir(parents=True, exist_ok=True)
        result = run(bare)
    if result.returncode != 0:
        raise RuntimeError(f"relinker failed ({result.returncode}): {(result.stdout + result.stderr).strip().splitlines()[-1:]}")
    made = [f for f in work.glob("*.json") if f.name.endswith((".registry.json", ".needed.json")) and f.stat().st_mtime >= started]
    if not any(f.name.endswith(".registry.json") for f in made):
        raise RuntimeError("the relinker wrote no registry")
    imports = game / "imports"
    imports.mkdir(exist_ok=True)
    names = []
    for f in sorted(made):
        target = imports / f.name
        target.write_bytes(f.read_bytes())
        names.append(f.name)
    stamp = {**exe_stamp(exe), "files": names, "build": str(root), "commit": commit, "madeBy": "predict_missing.py relinker rerun", "made": time.strftime("%Y-%m-%d %H:%M")}
    write_text(imports / "stamp.json", json.dumps(stamp, indent=2))
    return [imports / n for n in names], stamp


def guest_modules(audit, game, cfg, work):
    found = {}
    dirs = [game / "app0" / "sce_module"] + [game / "app0" / m for m in (cfg.get("moduleDirs") or []) if isinstance(m, str)]
    dirs += [p.parent for p in work.rglob("*.guest.prx")] if work.is_dir() else []
    for d in dirs:
        for f in sorted(d.glob("*.guest.prx")) if d.is_dir() else []:
            if f.name not in found:
                found[f.name] = f
    imports, exports = {}, defaultdict(set)
    for name, f in found.items():
        imports[name] = ([], [])
        for symbol in audit.pe_exports(f, f.read_bytes()):
            exports[symbol.split("#", 1)[0]].add(name)
    return imports, {k: sorted(v) for k, v in exports.items()}


def pipeline_failures():
    state = load_json(TOOLS / "auto-state.json", {}) or {}
    failures = {}
    for entry in state.values():
        if isinstance(entry, dict) and entry.get("gameDir"):
            failures[Path(entry["gameDir"]).name.lower()] = (entry.get("failure") or {}, str(entry.get("note") or ""))
    return failures


def library_dir(record):
    for provider in record.get("providers") or []:
        if provider.lower().endswith(".prx"):
            return provider[:-4]
    library = record.get("library") or ""
    for suffix in (".sprx", ".prx", ".suprx"):
        if library.lower().endswith(suffix):
            return library[: -len(suffix)]
    return library


def stop_library(failure, records):
    function = failure.get("function") or ""
    if function:
        for r in records:
            if r.get("name") == function:
                return library_dir(r).lower()
    # An AnyPS5 stop message names its library first ("NGS2: the custom Reverb module is not implemented").
    text = re.sub(r"^.*?what\(\):\s*", "", failure.get("message") or failure.get("line") or "")
    match = re.match(r"\s*([A-Za-z][A-Za-z0-9]{2,30}):\s", text)
    token = match.group(1).removeprefix("sce").removeprefix("lib").lower() if match else ""
    return f"libsce{token}" if len(token) >= 3 else ""


def real_stop(failure, records):
    names = {r.get("name") for r in records if r.get("name")}
    names |= {n.removesuffix("_nid_postfix") for n in names}
    return failure.get("function") if failure.get("function") in names else ""


def audit_game(audit, game, settings, facts_for, max_relinks, failures):
    cfg = load_json(game / "game.json", {})
    exe = game_exe(game, cfg)
    root, commit = build_of(cfg, settings)
    result = {"game": game.name, "title": cfg.get("title"), "titleId": cfg.get("titleId"), "build": str(root), "commit": commit,
              "updated": time.strftime("%Y-%m-%d %H:%M")}
    kept = kept_registries(game, exe)
    if kept is None:
        if max_relinks[0] == 0:
            result["error"] = "no import lists yet (waiting for a relinker run)"
            return result
        max_relinks[0] -= 1
        kept = relink(game, cfg, exe, root, commit)
    files, stamp = kept
    audit_module, facts, source = facts_for(root, commit)
    registries = [f for f in files if f.name.endswith(".registry.json")]
    needed = sorted({lib for f in files if f.name.endswith(".needed.json") for lib in audit_module.read_needed_registry(f)})
    per_registry = [(f, audit_module.read_registry(f)) for f in registries]
    imports = [entry for _, entries in per_registry for entry in entries]
    module_imports, module_exports = guest_modules(audit_module, game, cfg, WORK / "relink" / safe_name(game.name))
    modules = set(module_imports) | {n.removesuffix(".guest.prx") for n in module_imports}
    modules |= {f.name.split(".", 1)[1].removesuffix(".registry.json").removesuffix(".guest.prx") for f in registries if ".guest.prx." in f.name}
    modules = audit_module.ModuleNames(modules, fold=bool(facts["windows"]))
    stubs = {(p, n): name for p, n, name in facts["stubs"]}
    partial = {(p, n) for p, n in facts["partial"]}
    records, missing_libs = audit_module.audit(imports, facts["exports"], stubs, set(facts["libraries"]), modules, module_imports, module_exports,
                                               needed_libraries=needed, windows_libraries=set(facts["windows"]), partial=partial)
    if NAMES.is_file():
        audit_module.attach_names(records, names_db(audit_module))
    stops = [audit_module.current_stop(entries, records) for _, entries in per_registry]
    failure, note = failures.get(game.name.lower(), ({}, ""))
    if failure.get("function") and not real_stop(failure, records):
        failure = {**failure, "function": "", "message": failure.get("message") or re.sub(r"^.*?what\(\):\s*", "", failure.get("line") or "") or failure["function"]}
    stop_lib = stop_library(failure, records)
    predicted = []
    for r in records:
        kind = "absent" if r.get("symbol_mismatch") else r["class"]
        if kind not in MISSING:
            continue
        lib = library_dir(r)
        predicted.append({"function": audit_module.nid_names.strip_nid_postfix(r["name"]) if r.get("name") else r["nid"], "nid": r["nid"], "named": bool(r.get("name")), "class": kind,
                          "library": lib, "references": r["references"], "weak": r["weak"], "module": r.get("module"),
                          "sameLibraryAsStop": bool(stop_lib) and lib.lower().removesuffix(".native") == stop_lib.removesuffix(".native"),
                          "stopsNow": bool(failure.get("function")) and r.get("name") == failure.get("function")})
    # Most likely next: what stops the game now, then the same library as that stop (a game sets a library up call by
    # call), then never-there before always-stops before stops-sometimes, then the most call sites. Weak imports last:
    # a game checks those for null.
    predicted.sort(key=lambda p: (not p["stopsNow"], p["weak"], not p["sameLibraryAsStop"], SEVERITY[p["class"]], -p["references"], p["function"]))
    for rank, p in enumerate(predicted, 1):
        p["rank"] = rank
    counted = [r for r in records if r["class"] != "module"]
    implemented = sum(1 for r in counted if r["class"] == "implemented" and not r.get("symbol_mismatch"))
    result.update({
        "imports": {"unique": len(records), "references": sum(r["references"] for r in records),
                    "byClass": {k: sum(1 for r in records if r["class"] == k) for k in audit_module.CLASSES}},
        "implementedShare": round(100.0 * implemented / len(counted), 1) if counted else None,
        "importLists": {"files": [f.name for f in files], "madeBy": stamp.get("madeBy", "build-game.ps1")},
        "missingLibraries": missing_libs,
        "loaderStop": next(({"function": s.get("name") or s["nid"], "library": s["library"]} for s in stops if s), None),
        "currentStop": {"kind": failure.get("kind", ""), "function": failure.get("function", ""), "message": failure.get("message", ""), "note": note},
        "stopPredicted": (any(p["stopsNow"] for p in predicted) if failure.get("function") else None),
        "predicted": predicted,
    })
    return result


_names = {}


def names_db(audit_module):
    if "db" not in _names:
        _names["db"] = audit_module.load_names(NAMES)
    return _names["db"]


def game_markdown(r):
    lines = [f"# {r['game']}: predicted missing functions", "",
             f"Updated {r['updated']} against AnyPS5 {r['commit']} ({Path(r['build']).name}), by predict_missing.py.", ""]
    if r.get("error"):
        return "\n".join(lines + [f"Not checked: {r['error']}", ""])
    c = r["imports"]["byClass"]
    lines += [f"The game imports {r['imports']['unique']} functions ({r['imports']['references']} references). "
              f"Implemented: {c.get('implemented', 0)}, absent: {c.get('absent', 0)}, stub: {c.get('stub', 0)}, partial: {c.get('partial', 0)}, "
              f"the game's own modules: {c.get('module', 0)}. That is {r['implementedShare']}% implemented.", ""]
    stop = r["currentStop"]
    if stop.get("function") or stop.get("message"):
        lines += [f"Stops now at: {stop.get('function') or stop.get('message')}" + (
            (" (an AnyPS5 stop message inside a function it implements: an import list cannot show that stop; the list says what comes after it)" if r["stopPredicted"] is None
             else (" (on the predicted list)" if r["stopPredicted"] else " (not on the predicted list: AnyPS5 exports it and does not mark it as a stub)"))), ""]
    elif stop.get("note", "").startswith("runs:"):
        lines += [f"Runs now ({stop['note']}); the list says what it may still meet later.", ""]
    if r.get("missingLibraries"):
        lines += ["Libraries it needs that no AnyPS5 library or game module provides: " + ", ".join(f"{k} ({v} imports)" for k, v in r["missingLibraries"].items()), ""]
    lines += ["Most likely next first: what stops it now, the same library as that stop, functions AnyPS5 lacks before stubs before partial ones, then the most call sites.", "",
              "| # | Function | Library | AnyPS5 | Call sites | Note |", "|---|---|---|---|---|---|"]
    for p in r["predicted"]:
        note = "; ".join(x for x in ("stops the game now" if p["stopsNow"] else "", "same library as the stop" if p["sameLibraryAsStop"] and not p["stopsNow"] else "",
                                     "weak import (the game checks for it)" if p["weak"] else "", f"imported by {p['module']}" if p["module"] else "",
                                     "" if p["named"] else "name unknown (NID)") if x)
        lines.append(f"| {p['rank']} | {p['function']} | {p['library']} | {p['class']} | {p['references']} | {note} |")
    return "\n".join(lines) + "\n"


def write_ranking():
    results = []
    for f in sorted(PREDICTED.glob("*.json")):
        r = load_json(f)
        if r and not r.get("error") and (GAMES / r["game"]).is_dir():
            results.append(r)
    table = {}
    for r in results:
        for p in r["predicted"]:
            if p["weak"]:
                continue
            t = table.setdefault(p["function"], {"function": p["function"], "nid": p["nid"], "library": p["library"], "class": p["class"],
                                                 "games": [], "stopsNow": [], "references": 0})
            if r["game"] not in t["games"]:
                t["games"].append(r["game"])
            if p["stopsNow"]:
                t["stopsNow"].append(r["game"])
            t["references"] += p["references"]
            if SEVERITY[p["class"]] < SEVERITY[t["class"]]:
                t["class"] = p["class"]
    ranked = sorted(table.values(), key=lambda t: (-len(t["stopsNow"]), -len(t["games"]), SEVERITY[t["class"]], -t["references"], t["function"]))
    write_text(REPORTS / "predicted-missing.json", json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M"), "games": len(results), "functions": ranked}, indent=1))
    lines = ["# Predicted missing functions, all games", "",
             f"Updated {time.strftime('%Y-%m-%d %H:%M')} by predict_missing.py from the import lists of {len(results)} built games. "
             "Every function a game imports that its AnyPS5 build lacks (absent), has only as a stub (stub) or stops on for some inputs (partial). "
             "Weak imports are left out. Ranked by games it stops now, then games that import it, then class and call sites. "
             "Per game: predicted\\<game>.md. most-wanted.md lists only what already stopped a game.", "",
             "| # | Function | Library | AnyPS5 | Games | Stops now | Call sites | Which |", "|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(ranked[:300], 1):
        which = ", ".join(g.rsplit(" (", 1)[0] for g in t["games"][:5]) + (f" and {len(t['games']) - 5} more" if len(t["games"]) > 5 else "")
        lines.append(f"| {i} | {t['function']} | {t['library']} | {t['class']} | {len(t['games'])} | {len(t['stopsNow']) or ''} | {t['references']} | {which} |")
    if len(ranked) > 300:
        lines += ["", f"{len(ranked) - 300} more in predicted-missing.json."]
    lines += ["", "| Game | Imports | Implemented | Absent | Stub | Partial | Stops now at | On the list |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: r["implementedShare"] or 0):
        c = r["imports"]["byClass"]
        s = r["currentStop"].get("function") or (r["currentStop"].get("message") or "")[:60]
        lines.append(f"| {r['game']} | {r['imports']['unique']} | {r['implementedShare']}% | {c.get('absent', 0)} | {c.get('stub', 0)} | {c.get('partial', 0)} | {s} | "
                     f"{'' if r['stopPredicted'] is None else ('yes' if r['stopPredicted'] else 'no')} |")
    write_text(REPORTS / "predicted-missing.md", "\n".join(lines) + "\n")
    return ranked, results


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="predicted missing functions of built AnyPS5 games")
    parser.add_argument("--game", action="append", default=[], help="a game folder (repeatable)")
    parser.add_argument("--all", action="store_true", help="every built game")
    parser.add_argument("--max-relinks", type=int, default=-1, help="at most this many relinker runs for games without import lists (-1: no limit)")
    parser.add_argument("--ranking-only", action="store_true", help="only rewrite the cross-game ranking")
    args = parser.parse_args(argv)
    settings = load_json(TOOLS / "settings.json", {})
    games = [Path(g) for g in args.game] + (list(built_games()) if args.all else [])
    failures = pipeline_failures()
    loaded = {}

    def facts_for(root, commit):
        if commit not in loaded:
            source = source_tree(commit)
            loaded[commit] = (load_audit(source), source)
        audit_module, source = loaded[commit]
        return audit_module, build_facts(audit_module, root, commit, source), source

    budget = [args.max_relinks]
    problems = 0
    for game in [] if args.ranking_only else games:
        game = game.resolve()
        started = time.time()
        try:
            cfg = load_json(game / "game.json", {})
            root, commit = build_of(cfg, settings)
            if not commit:
                raise RuntimeError(f"{root} has no COMMIT.txt")
            audit_module = facts_for(root, commit)[0]
            r = audit_game(audit_module, game, settings, facts_for, budget, failures)
        except Exception as error:
            problems += 1
            r = {"game": game.name, "updated": time.strftime("%Y-%m-%d %H:%M"), "build": "", "commit": "", "error": f"{type(error).__name__}: {error}"}
        write_text(PREDICTED / f"{game.name}.json", json.dumps(r, indent=1))
        write_text(PREDICTED / f"{game.name}.md", game_markdown(r))
        if r.get("error"):
            log(f"{game.name}: not checked: {r['error']}")
        else:
            c = r["imports"]["byClass"]
            top = ", ".join(p["function"] for p in r["predicted"][:5])
            log(f"{game.name}: {r['imports']['unique']} imports, {r['implementedShare']}% implemented; absent {c['absent']}, stub {c['stub']}, partial {c['partial']}"
                f"{'' if r['stopPredicted'] is None else '; current stop on the list: ' + ('yes' if r['stopPredicted'] else 'no')}; next: {top} ({time.time() - started:.0f} s)")
    ranked, results = write_ranking()
    log(f"ranking: {len(ranked)} predicted missing functions over {len(results)} games -> {REPORTS / 'predicted-missing.md'}")
    return 1 if problems and problems == len(games) else 0


if __name__ == "__main__":
    raise SystemExit(main())
