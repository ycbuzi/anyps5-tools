"""Video clips and recent gameplay for AnyPS5 OS (sysCall "recording").

gpu-screen-recorder does both the recent-gameplay replay buffer (SIGUSR1 saves it, SIGINT stops it) and plain
recordings; wf-recorder is the fallback for plain recordings only. Recorder PIDs live in
~/.local/share/anyps5/recording.json, videos in ~/AnyPS5 Captures/Videos/.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "native": None}
FORMATS = ("webm", "mp4")
LENGTHS = (15, 30, 60, 300, 900)
GSR = "gpu-screen-recorder"
WFR = "wf-recorder"
SIGINT = signal.SIGINT
SIGUSR1 = getattr(signal, "SIGUSR1", 10)
SIGKILL = getattr(signal, "SIGKILL", 9)
STOP_TIMEOUT = 15.0
SAVE_TIMEOUT = 30.0
START_CHECK = 1.5
_procs = {}
_lock = threading.Lock()


def video_dir():
    return os.path.join(os.path.expanduser("~"), "AnyPS5 Captures", "Videos")


def state_dir():
    return os.path.join(os.path.expanduser("~"), ".local", "share", "anyps5")


def state_path():
    return os.path.join(state_dir(), "recording.json")


def log_path(kind):
    return os.path.join(state_dir(), "recording-%s.log" % kind)


def _load():
    try:
        with open(state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state):
    os.makedirs(state_dir(), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_path())


def _reap(pid):
    p = _procs.get(pid)
    if p is not None:
        if p.poll() is not None:
            _procs.pop(pid, None)
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError, AttributeError):
        pass


def _alive(pid, name):
    if not isinstance(pid, int) or pid <= 0:
        return False
    _reap(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid, "r") as f:
            if f.read().rsplit(")", 1)[1].split()[0] in ("Z", "X"):
                return False
        with open("/proc/%d/comm" % pid, "r") as f:
            return f.read().strip() == name[:15]
    except (OSError, IndexError):
        return True


def _entry(state, kind):
    e = state.get(kind)
    if isinstance(e, dict) and _alive(e.get("pid"), e.get("backend", "")):
        return e
    return None


def _backend():
    if shutil.which(GSR):
        return GSR
    if shutil.which(WFR):
        return WFR
    return None


def _options(args, need_seconds=False):
    a = args if isinstance(args, dict) else {}
    res = a.get("resolution", "720p")
    if not isinstance(res, str) or res not in RESOLUTIONS:
        raise ValueError("Video Clip Resolution must be 720p, 1080p or native")
    fmt = a.get("format", "webm")
    if not isinstance(fmt, str) or fmt not in FORMATS:
        raise ValueError("Video Clip File Type must be webm or mp4")
    mic = a.get("mic", False)
    if not isinstance(mic, bool):
        raise ValueError("mic must be true or false")
    o = {"resolution": res, "format": fmt, "mic": mic}
    if need_seconds:
        o["seconds"] = _seconds(a.get("seconds", 60))
    return o


def _seconds(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or int(v) not in LENGTHS:
        raise ValueError("Recent gameplay length must be one of %s seconds" % ", ".join(str(x) for x in LENGTHS))
    return int(v)


def _gsr_cmd(o, out, seconds=None):
    cmd = [shutil.which(GSR), "-w", "screen", "-f", "60", "-c", o["format"]]
    if o["format"] == "webm":
        cmd += ["-k", "vp9", "-ac", "opus"]
    else:
        cmd += ["-k", "auto", "-ac", "aac"]
    size = RESOLUTIONS[o["resolution"]]
    if size:
        cmd += ["-s", "%dx%d" % size]
    cmd += ["-a", "default_output|default_input" if o["mic"] else "default_output"]
    if seconds:
        cmd += ["-r", str(seconds)]
    return cmd + ["-o", out]


def _monitor_source():
    if not shutil.which("pactl"):
        return None
    try:
        r = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sink = r.stdout.strip() if r.returncode == 0 else ""
    if not re.fullmatch(r"[A-Za-z0-9._:@-]{1,200}", sink):
        return None
    return sink + ".monitor"


def _wfr_cmd(o, out):
    cmd = [shutil.which(WFR), "-y", "-r", "60", "-f", out]
    if o["format"] == "webm":
        cmd += ["-c", "libvpx-vp9", "-C", "libopus"]
    else:
        cmd += ["-c", "libx264", "-C", "aac"]
    size = RESOLUTIONS[o["resolution"]]
    if size:
        cmd += ["-F", "scale=-2:%d" % size[1]]
    mon = _monitor_source()
    if mon:
        cmd.append("--audio=" + mon)
    return cmd


def _tail(path, lines=4):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return " ".join(l.strip() for l in f.read().splitlines()[-lines:] if l.strip())
    except OSError:
        return ""


def _spawn(cmd, kind):
    os.makedirs(state_dir(), exist_ok=True)
    os.makedirs(video_dir(), exist_ok=True)
    with open(log_path(kind), "wb") as lf:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
    try:
        p.wait(timeout=START_CHECK)
    except subprocess.TimeoutExpired:
        _procs[p.pid] = p
        return p.pid
    raise RuntimeError("%s stopped at once (exit %s): %s" % (os.path.basename(cmd[0]), p.returncode, _tail(log_path(kind)) or "no output"))


def _stop_pid(pid, name, sig=SIGINT, timeout=STOP_TIMEOUT):
    try:
        os.kill(pid, sig)
    except OSError:
        return True
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _alive(pid, name):
            _procs.pop(pid, None)
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, SIGKILL)
    except OSError:
        pass
    _procs.pop(pid, None)
    return False


def _videos():
    d = video_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return {}
    out = {}
    for n in names:
        if n.startswith(".") or not n.lower().endswith((".mp4", ".webm")):
            continue
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out[n] = (st.st_size, st.st_mtime)
    return out


def status(args=None):
    state = _load()
    rec, rep = _entry(state, "record"), _entry(state, "replay")
    clean = {k: v for k, v in (("record", rec), ("replay", rep)) if v}
    if clean != state and _lock.acquire(False):
        try:
            fresh = _load()
            _save({k: v for k, v in fresh.items() if k in ("record", "replay") and _entry(fresh, k)})
        finally:
            _lock.release()
    b = _backend()
    reason = ""
    if not b:
        reason = "No screen recorder found. Install gpu-screen-recorder (or wf-recorder for recordings only)."
    elif b == WFR:
        reason = "wf-recorder records video clips but cannot keep recent gameplay or add your microphone; install gpu-screen-recorder for both."
    return {
        "supported": bool(b),
        "backend": b or "",
        "replaySupported": b == GSR,
        "micSupported": b == GSR,
        "recording": bool(rec),
        "replay": bool(rep),
        "reason": reason,
        "videoDir": video_dir(),
        "file": rec.get("file") if rec else None,
        "startedAt": rec.get("started") if rec else None,
        "replaySeconds": rep.get("seconds") if rep else None,
        "replayStartedAt": rep.get("started") if rep else None,
        "replayOptions": {k: rep.get(k) for k in ("resolution", "format", "mic")} if rep else None,
        "platform": "linux",
    }


def start(args):
    o = _options(args)
    b = _backend()
    if not b:
        raise NotImplementedError("No screen recorder found (gpu-screen-recorder or wf-recorder)")
    state = _load()
    if _entry(state, "record"):
        raise RuntimeError("A recording is already running")
    path = os.path.join(video_dir(), "Recording_%s.%s" % (time.strftime("%Y-%m-%d_%H-%M-%S"), o["format"]))
    cmd = _gsr_cmd(o, path) if b == GSR else _wfr_cmd(o, path)
    pid = _spawn(cmd, "record")
    state = _load()
    state["record"] = {"pid": pid, "backend": b, "file": path, "started": int(time.time()),
                       "resolution": o["resolution"], "format": o["format"], "mic": o["mic"] and b == GSR}
    _save(state)
    return {"recording": True, "file": path, "backend": b, "micIncluded": o["mic"] and b == GSR}


def stop(args=None):
    state = _load()
    rec = _entry(state, "record")
    if not rec:
        state.pop("record", None)
        _save(state)
        return {"recording": False, "stopped": False, "file": None, "saved": False}
    clean = _stop_pid(rec["pid"], rec["backend"])
    state = _load()
    state.pop("record", None)
    _save(state)
    path = rec.get("file")
    saved = bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    return {"recording": False, "stopped": True, "clean": clean, "file": path, "saved": saved}


def replay_start(args):
    o = _options(args, need_seconds=True)
    if not shutil.which(GSR):
        raise NotImplementedError("Recent gameplay needs gpu-screen-recorder")
    state = _load()
    rep = _entry(state, "replay")
    if rep and all(rep.get(k) == o[k] for k in ("resolution", "format", "mic", "seconds")):
        return {"replay": True, "restarted": False, "seconds": rep["seconds"]}
    if rep:
        _stop_pid(rep["pid"], GSR)
    pid = _spawn(_gsr_cmd(o, video_dir(), o["seconds"]), "replay")
    state = _load()
    state["replay"] = dict(o, pid=pid, backend=GSR, started=int(time.time()))
    _save(state)
    return {"replay": True, "restarted": bool(rep), "seconds": o["seconds"]}


def replay_stop(args=None):
    state = _load()
    rep = _entry(state, "replay")
    if rep:
        _stop_pid(rep["pid"], GSR)
    state = _load()
    state.pop("replay", None)
    _save(state)
    return {"replay": False, "stopped": bool(rep)}


def _trim(path, seconds):
    if not shutil.which("ffmpeg"):
        return False
    d, n = os.path.split(path)
    tmp = os.path.join(d, ".trim-" + n)
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-sseof", "-%d" % seconds, "-i", path,
                            "-c", "copy", tmp], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is None or r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, path)
    return True


def save_recent(args):
    a = args if isinstance(args, dict) else {}
    state = _load()
    rep = _entry(state, "replay")
    if not rep:
        raise RuntimeError("Recent gameplay is not being recorded")
    buf = int(rep.get("seconds", 0))
    want = _seconds(a["seconds"]) if a.get("seconds") is not None else buf
    if want > buf:
        raise ValueError("Only the last %d seconds are kept; raise the recent gameplay length first" % buf)
    have = max(0, int(time.time()) - int(rep.get("started", 0)))
    before = _videos()
    os.kill(rep["pid"], SIGUSR1)
    end = time.monotonic() + SAVE_TIMEOUT
    found, last = None, None
    while time.monotonic() < end:
        time.sleep(0.5)
        now = _videos()
        new = sorted((n for n in now if n not in before), key=lambda n: now[n][1])
        if new:
            cur = (new[-1], now[new[-1]][0])
            if cur == last and cur[1] > 0:
                found = cur[0]
                break
            last = cur
    if not found:
        raise RuntimeError("gpu-screen-recorder did not save the recent gameplay: %s" % (_tail(log_path("replay")) or "no output"))
    path = os.path.join(video_dir(), found)
    trimmed = want < buf and want < have and _trim(path, want)
    length = want if trimmed else min(buf, have)
    return {"saved": True, "file": path, "seconds": length, "trimmed": bool(trimmed)}


def list_videos(args=None):
    v = _videos()
    d = video_dir()
    items = [{"name": n, "path": os.path.join(d, n), "size": s, "mtime": int(m),
              "kind": "recent" if n.startswith("Replay_") else "recording" if n.startswith("Recording_") else "video"}
             for n, (s, m) in v.items()]
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"dir": d, "videos": items}


ACTIONS = {"status": status, "start": start, "stop": stop, "replayStart": replay_start, "replayStop": replay_stop,
           "saveRecent": save_recent, "list": list_videos}


def handle(action, args):
    fn = ACTIONS.get(action)
    if fn is None:
        raise NotImplementedError("recording has no action %r" % (action,))
    a = args if isinstance(args, dict) else {}
    if fn in (status, list_videos):
        return fn(a)
    with _lock:
        return fn(a)
