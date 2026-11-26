import sys, time, base64, urllib.parse, urllib.error, xdeploy as d
pkgfile, name, version, wait = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
d.PACKAGE, d.NAME = pkgfile, name
call = d.probe.portal("192.0.2.1"); call("GET", "/api/os/info")
b64 = lambda s: urllib.parse.quote(base64.b64encode(s.encode()).decode(), safe="")
import xlibrary
def fetch(pkg, filename):
    try: return call("GET", "/api/filesystem/apps/file?knownfolderid=LocalAppData&packagefullname=%s&filename=%s&path=%s" % (urllib.parse.quote(pkg["PackageFullName"]), filename, urllib.parse.quote(chr(92) + "LocalState")))
    except urllib.error.HTTPError: return None
pkg = d.package(call)
kept = {}
if pkg:
    for f in ("settings.json",):
        data = fetch(pkg, f)
        if data: kept[f] = data
    try: call("DELETE", "/api/taskmanager/app?package=%s&forcestop=true" % b64(pkg["PackageFullName"]))
    except urllib.error.HTTPError: pass
    time.sleep(2)
d.install(call)
for i in range(10):
    pkg = d.package(call)
    if pkg and version in pkg["PackageFullName"]: break
    time.sleep(3)
if pkg and version not in pkg["PackageFullName"]:
    print("update not applied over", pkg["PackageFullName"])
    try: call("DELETE", "/api/app/packagemanager/package?package=%s" % urllib.parse.quote(pkg["PackageFullName"])); print("removed", pkg["PackageFullName"])
    except urllib.error.HTTPError as e: print("remove", e.code, e.read()[:200])
    for i in range(30):
        if not d.package(call): break
        time.sleep(2)
    d.install(call)
for i in range(75):
    pkg = d.package(call)
    if pkg and version in pkg["PackageFullName"]: break
    time.sleep(4)
print(pkg["PackageFullName"])
for f, data in kept.items():
    if not fetch(pkg, f): xlibrary.upload(call, pkg, f, data); print("restored", f)
if not fetch(pkg, "library.json"):
    sys.argv = ["xlibrary.py", "192.0.2.1"]; print("library was cleared, sending it again"); xlibrary.main()
try: d.launch(call, pkg); print("launched")
except urllib.error.HTTPError as e: print("launch", e.code, e.read()[:200])
time.sleep(wait); print(d.log(call, pkg)[-2500:])
