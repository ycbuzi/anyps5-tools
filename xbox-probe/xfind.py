import concurrent.futures
import ipaddress
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "console"))
import deploy
import xdeploy

HOST_FILE = os.path.join(HERE, "out", ".host")


def lan_networks():
    nets = []
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        address = ipaddress.ip_address(info[4][0])
        if address.is_private and not address.is_loopback:
            nets.append(ipaddress.ip_network("%s/24" % address, strict=False))
    return list(dict.fromkeys(nets))


def open_port(host, port=11443):
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


def is_console(host):
    try:
        call = deploy.portal(host)
        return xdeploy.package(call) is not None
    except Exception:
        return False


def main():
    old = open(HOST_FILE).read().strip() if os.path.exists(HOST_FILE) else ""
    if old and open_port(old) and is_console(old):
        print("the saved Xbox address still works")
        return
    candidates = []
    for net in lan_networks():
        hosts = [str(h) for h in net.hosts()]
        with concurrent.futures.ThreadPoolExecutor(64) as pool:
            candidates += [h for h, ok in zip(hosts, pool.map(open_port, hosts)) if ok]
    for host in candidates:
        if is_console(host):
            with open(HOST_FILE, "w") as f:
                f.write(host)
            print("found the Xbox at a new address; out/.host updated")
            return
    raise SystemExit("no Xbox Device Portal found on %d candidate(s)" % len(candidates))


if __name__ == "__main__":
    main()
