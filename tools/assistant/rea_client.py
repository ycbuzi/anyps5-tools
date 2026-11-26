"""A small MCP client for REA (reverse engineering: Ghidra/rizin based binary analysis), so the offline assistant can
open a game's program or an AnyPS5 library and decompile, search and cross-reference it. REA runs locally
(C:\\Users\\user\\nodejs\\rea.cmd mcp, Ghidra in C:\\Users\\user\\tools); no internet is needed.

usage: python -I rea_client.py list                      (the tools REA offers)
       python -I rea_client.py call <tool> '<json args>'
"""
import json
import os
import subprocess
import sys
import threading

REA = [r"%USERPROFILE%\nodejs\rea.cmd", "mcp"]
ENV = {"GHIDRA_INSTALL_DIR": r"%USERPROFILE%\tools\ghidra_12.1.4_PUBLIC", "JAVA_HOME": r"%USERPROFILE%\tools\jdk-21"}


class Rea:
    """One REA server for the assistant's whole session (opening a binary takes time; REA keeps it open)."""

    def __init__(self):
        env = dict(os.environ, **ENV)
        env["PATH"] = os.path.dirname(REA[0]) + os.pathsep + env.get("PATH", "")
        self.proc = subprocess.Popen(["cmd.exe", "/d", "/c"] + REA, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=env, creationflags=0x08000000)
        self.next_id = 0
        self.lock = threading.Lock()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "anyps5-assistant", "version": "1"}})
        self.notify("notifications/initialized")

    def notify(self, method, params=None):
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method, params, timeout=900):
        with self.lock:
            self.next_id += 1
            rid = self.next_id
            self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n").encode())
            self.proc.stdin.flush()
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("REA stopped")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message", str(msg["error"])))
                    return msg.get("result", {})

    def tools(self):
        return self.request("tools/list", {}).get("tools", [])

    def call(self, name, args):
        result = self.request("tools/call", {"name": name, "arguments": args})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        if result.get("structuredContent") and not parts:
            parts = [json.dumps(result["structuredContent"])]
        return ("ERROR: " if result.get("isError") else "") + "\n".join(parts)

    def close(self):
        try:
            self.proc.kill()
        except OSError:
            pass


if __name__ == "__main__":
    rea = Rea()
    try:
        if sys.argv[1] == "list":
            for t in rea.tools():
                print(t["name"], "|", (t.get("description") or "").split("\n")[0][:160], "|", ",".join(t.get("inputSchema", {}).get("required", [])))
        else:
            print(rea.call(sys.argv[2], json.loads(sys.argv[3] if len(sys.argv) > 3 else "{}")))
    finally:
        rea.close()
