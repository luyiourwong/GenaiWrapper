"""在本地擷取 curl_cffi 各 impersonate 版本的 TLS ClientHello，與 Cherry Studio 的比對。

不需要 API key、不需要管理員：起一個只讀 ClientHello 的本機 TCP server，
讓 curl_cffi 連上去，讀出第一個 TLS record 並解析。

用法：
    .venv\\Scripts\\python.exe clienthello_versions.py
"""

from __future__ import annotations

import socket
import threading
from typing import Any

from curl_cffi import requests

from tls_clienthello_compare import parse_client_hello

HOST, PORT = "127.0.0.1", 9443
_captured: dict[str, Any] = {}


def _server_loop(ready: threading.Event) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(8)
    ready.set()
    while True:
        conn, _ = srv.accept()
        try:
            conn.settimeout(3.0)
            buf = bytearray()
            # 讀到第一個 TLS record 完整為止
            while len(buf) < 5:
                buf += conn.recv(4096)
            need = 5 + int.from_bytes(buf[3:5], "big")
            while len(buf) < need:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            info = parse_client_hello(bytes(buf))
            if info:
                _captured["last"] = info
        except Exception:  # noqa: BLE001
            pass
        finally:
            conn.close()


def main() -> None:
    targets = [
        "chrome99",
        "chrome104",
        "chrome110",
        "chrome116",
        "chrome120",
        "chrome124",
        "chrome131",
        "chrome133a",
        "chrome136",
        "chrome142",
        "chrome145",
        "chrome146",
        "chrome150",
        "chrome",
        "edge99",
        "edge101",
        "firefox133",
        "firefox147",
        "safari17_0",
        "safari18_0",
    ]

    ready = threading.Event()
    threading.Thread(target=_server_loop, args=(ready,), daemon=True).start()
    ready.wait(2)

    print(f"{'impersonate':<16} {'len':>5}  {'ALPN':<20} JA3")
    print("-" * 90)
    for target in targets:
        _captured.clear()
        try:
            requests.get(f"https://{HOST}:{PORT}/", impersonate=target, verify=False, timeout=3)
        except Exception:  # noqa: BLE001
            pass
        info = _captured.get("last")
        if not info:
            print(f"{target:<16} {'--':>5}  (沒抓到)")
            continue
        print(f"{target:<16} {info['length']:>5}  {str(info['alpn']):<20} {info['ja3']}")

    print("\nCherry Studio 實測: len=1723  ALPN=['h2', 'http/1.1']  JA3=931d4f5d4917deb2ba08e3979b91dc36")


if __name__ == "__main__":
    main()
