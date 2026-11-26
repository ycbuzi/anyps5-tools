"""Streams files as a tar over plain TCP to a receiver such as the Quest's qtest/qrecv.py (one tar per connection,
member paths as given by ARCNAME). usage: python -I send_tar.py HOST PORT SOURCE ARCNAME [SOURCE ARCNAME ...]
e.g. send_tar.py 192.0.2.1 9073 "D:\\AnyPS5 Games\\Stray (PPSA02100)\\app0" app0"""
import socket
import sys
import tarfile
import time


def main():
    host, port, pairs = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
    if not pairs or len(pairs) % 2:
        sys.exit(__doc__)
    start = time.time()
    with socket.create_connection((host, port), timeout=60) as sock:
        sock.settimeout(None)
        with sock.makefile("wb", buffering=1024 * 1024) as stream, tarfile.open(fileobj=stream, mode="w|") as tar:
            for src, arc in zip(pairs[0::2], pairs[1::2]):
                tar.add(src, arcname=arc)
                print(f"{time.strftime('%H:%M:%S')} sent {src} as {arc}", flush=True)
    print(f"{time.strftime('%H:%M:%S')} done in {time.time() - start:.0f} s", flush=True)


if __name__ == "__main__":
    main()
