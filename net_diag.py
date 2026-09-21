"""手機熱點/網路路徑診斷：分辨「新連線建不起來」與「頻寬不足」。

症狀組合（小米 17T Pro 熱點）：
    - 第 1 個 request 200，第 2 個就 timeout
    - YouTube 影片（含 4K）順，但留言/清單等 XHR 出不來
    => 大流量長連線 OK、大量短命新連線反而壞 => 通常是路徑 MTU、IPv6、
       NAT/conntrack 或 DNS，而不是頻寬。

3 秒鑑別：
    ping -f -l 1472 1.1.1.1     :: 失敗
    ping -f -l 1400 1.1.1.1     :: 若這個過 → 就是 MTU

用法（在「有問題的熱點」上跑，跑完再在正常網路跑一次當 baseline 對照）：
    .venv\\Scripts\\python.exe net_diag.py

判讀看最後的 SUMMARY；每個 FAIL/WARN 旁邊都有對應的可能原因。
"""

from __future__ import annotations

import argparse
import logging
import re
import socket
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

logger = logging.getLogger("net_diag")

SMALL_URL = "https://www.gstatic.com/generate_204"
BIG_URL = "https://speed.cloudflare.com/__down?bytes=50000000&measId={}"
DNS_HOSTS = [
    "www.youtube.com",
    "youtubei.googleapis.com",
    "i.ytimg.com",
    "redirector.googlevideo.com",
    "fonts.gstatic.com",
    "speed.cloudflare.com",
]
SEQUENTIAL_N = 8
PARALLEL_N = 12
DNS_N = 3


@dataclass
class Result:
    name: str
    verdict: str  # OK / WARN / FAIL
    detail: str
    hints: list[str] = field(default_factory=list)


RESULTS: list[Result] = []


def add(name: str, verdict: str, detail: str, *hints: str) -> None:
    RESULTS.append(Result(name, verdict, detail, list(hints)))
    logger.info("[%s] %-22s %s", verdict, name, detail)


# ---------------------------------------------------------------- MTU / 介面
def run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        return out.stdout.decode("cp950", "replace")
    except Exception as exc:  # noqa: BLE001
        return f"<{exc}>"


def probe_mtu(host: str = "1.1.1.1") -> Result | None:
    """用 ping -f（DF bit）二分搜尋 IPv4 路徑 MTU。"""
    lo, hi = 1200, 1472
    best = 0
    for size in range(lo, hi + 1, 8):  # 粗掃
        out = run(["ping", "-n", "1", "-w", "1500", "-f", "-l", str(size), host])
        if "(0%" in out:
            best = size
    if best == 0:
        return None
    for size in range(best, min(best + 8, hi + 1)):  # 細掃
        out = run(["ping", "-n", "1", "-w", "1500", "-f", "-l", str(size), host])
        if "(0%" in out:
            best = size
    path_mtu = best + 28
    if path_mtu >= 1500:
        return Result(f"MTU(path->{host})", "OK", f"payload {best} => path MTU {path_mtu}")
    return Result(
        f"MTU(path->{host})",
        "WARN",
        f"payload {best} => path MTU {path_mtu}",
        [
            f"路徑 MTU {path_mtu} < 1500：把 PC 的 Wi-Fi 介面 MTU 設為 {max(path_mtu - 8, 1280)}"
            '（netsh interface ipv4 set subinterface "Wi-Fi" mtu=1400 store=persistent）',
            "伺服器憑證鏈/TLS handshake 常需要 >1.2KB 的單一 TCP 段，PMTUD 被黑洞就會"
            "「新連線卡住、已建立的長連線照跑」——與「影片順、請求壞」高度相符。",
        ],
    )


def show_subinterfaces() -> None:
    txt = run(["netsh", "interface", "ipv4", "show", "subinterfaces"])
    logger.info("IPv4 介面 MTU:\n%s", txt.strip())


def show_ip_config() -> bool:
    txt = run(["ipconfig"])
    has_v6 = bool(re.search(r"(2001|2404|240e|2409|2a0):[0-9a-f:]+", txt, re.I))
    logger.info("偵測到全域 IPv6 位址: %s", has_v6)
    return has_v6


# ---------------------------------------------------------------- TLS / HTTP
def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def http_probe(
    url: str,
    *,
    impersonate: str | None = "chrome",
    http_version: str | None = "h2",
    force4: bool = False,
    timeout: float = 12.0,
) -> tuple[float, int | None, str]:
    """回傳 (ttfb_sec, status, note)。失敗時 status = None。"""
    from curl_cffi import CurlHttpVersion, requests
    from curl_cffi.const import CurlIpResolve, CurlOpt

    ver = {
        "h1": CurlHttpVersion.V1_1,
        "h2": CurlHttpVersion.V2_0,
        "h3": CurlHttpVersion.V3,
        None: CurlHttpVersion.NONE,
    }[http_version]
    kw: dict = {"impersonate": impersonate, "http_version": ver, "timeout": timeout}
    if force4:
        kw["curl_options"] = {CurlOpt.IPRESOLVE: CurlIpResolve.V4}
    t0 = time.perf_counter()
    try:
        r = requests.get(url, **kw)
        return time.perf_counter() - t0, r.status_code, ""
    except Exception as exc:  # noqa: BLE001
        return time.perf_counter() - t0, None, type(exc).__name__


def test_sequential(url: str) -> None:
    """每次都是全新 TCP+TLS 連線（模擬一頁要打好幾個新連線）。"""
    ok, fail, times = 0, [], []
    for i in range(SEQUENTIAL_N):
        dt, status, note = http_probe(url, timeout=10.0)
        if status is not None:
            ok += 1
            times.append(dt)
            logger.info("  seq#%d %.2fs -> %s", i + 1, dt, status)
        else:
            fail.append((i + 1, round(dt, 1), note))
            logger.warning("  seq#%d %.2fs -> FAIL %s", i + 1, dt, note)
    med = f"{statistics.median(times):.2f}s" if times else "-"
    if fail and ok:
        add(
            f"sequential h2 ({url.split('/')[2]})",
            "FAIL",
            f"{SEQUENTIAL_N} 次新連線：{ok} 成功 / {len(fail)} 失敗，中位數 {med}，失敗={fail}",
            "新連線「時好時壞」：指向 NAT/conntrack 表滿、手機熱點硬體 NAT offload"
            "（硬體加速只 cover 少量 flow，其餘掉到慢路徑）。",
            "排除法：改插 USB 網路共享（不走 Wi-Fi 無線段）跑同一支腳本。"
            "USB 也一樣壞 → 手機 NAT/電信端；USB 正常 → Wi-Fi 熱點這一段。",
        )
    elif fail:
        add(
            f"sequential h2 ({url.split('/')[2]})",
            "FAIL",
            f"{SEQUENTIAL_N}/{SEQUENTIAL_N} 全失敗，耗時={fail}",
            "全部失敗通常是 MTU/PMTUD 黑洞或 IPv6 路徑不通（見下方測試）。",
        )
    else:
        add(f"sequential h2 ({url.split('/')[2]})", "OK", f"{ok}/{SEQUENTIAL_N} 成功，中位數 {med}")


def test_parallel(url: str) -> None:
    with ThreadPoolExecutor(max_workers=PARALLEL_N) as pool:
        res = list(pool.map(lambda _: http_probe(url, timeout=15.0), range(PARALLEL_N)))
    ok = sum(1 for _, s, _ in res if s is not None)
    fails = [(round(d, 1), n) for d, s, n in res if s is None]
    if ok == PARALLEL_N:
        add("parallel h2 x%d" % PARALLEL_N, "OK", f"{ok}/{PARALLEL_N} 成功")
    elif ok == 0:
        add(
            "parallel h2 x%d" % PARALLEL_N,
            "FAIL",
            f"0/{PARALLEL_N} 成功，失敗={fails}",
            "連單一併發都全掛 → 幾乎確定是路徑 MTU/IPv6，不是 conntrack 上限。",
        )
    else:
        add(
            "parallel h2 x%d" % PARALLEL_N,
            "WARN" if ok >= PARALLEL_N * 0.7 else "FAIL",
            f"{ok}/{PARALLEL_N} 成功，失敗={fails}",
            "併發越多越失敗、單獨跑都正常 → 手機端 NAT/conntrack 或 session 表不足／"
            "熱點 CPU 慢路徑過載（硬體加速只 cover 少數 flow）。",
        )


def test_http_versions(url: str) -> None:
    """h1 vs h2 vs h3、以及 Chrome 大型 TLS handshake 的差異。"""
    rows = []
    for ver in ("h1", "h2", "h3"):
        dt, status, note = http_probe(url, http_version=ver, timeout=15.0)
        rows.append((ver, status, round(dt, 2), note))
        logger.info("  %s -> status=%s %.2fs %s", ver, status, dt, note)
    ok1 = next((s for v, s, _, _ in rows if v == "h1"), None)
    ok2 = next((s for v, s, _, _ in rows if v == "h2"), None)
    ok3 = next((s for v, s, _, _ in rows if v == "h3"), None)
    summary = "  ".join(f"{v}:{'ok' if s else 'FAIL'}" for v, s, _, _ in rows)
    if ok1 and not ok2:
        add(
            "h1 vs h2 vs h3",
            "FAIL",
            summary,
            "h1 通、h2 不通：h2 連線把大量資料壓在少數封包節奏上，遇到 MTU/掉包特別敏感；"
            "也是本專案已知的 llm.chutes.ai「h2 指紋」403 那條線的另一種表現。",
            "暫時性解法：client 加 --disable-http2 或強制 h1。",
        )
    elif ok1 and not ok3:
        add(
            "h1 vs h2 vs h3",
            "WARN",
            summary,
            "h2 通、h3 不通：UDP 443 被擋或熱點對 QUIC/fragmentation 處理壞。"
            "Chrome 會先試 QUIC 再 fallback，這段等待就是「忽好忽壞」的來源。",
            "解法：chrome://flags 關 QUIC，或熱點換設定。",
        )
    else:
        add("h1 vs h2 vs h3", "OK", summary)


def test_ip_families(url: str) -> None:
    host = url.split("/")[2]
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    fams = sorted({("v6" if i[0] is socket.AF_INET6 else "v4") for i in infos})
    v6_addr = next((i[4][0] for i in infos if i[0] is socket.AF_INET6), None)
    dt4, s4, n4 = http_probe(url, force4=True, timeout=12.0)
    dt6, s6, n6 = None, None, ""
    if v6_addr:
        try:
            t0 = time.perf_counter()
            with socket.create_connection((v6_addr, 443), timeout=8):
                dt6 = time.perf_counter() - t0
            s6 = 443
        except Exception as exc:  # noqa: BLE001
            dt6 = time.perf_counter() - t0
            n6 = type(exc).__name__
    v6_txt = f"v6={v6_addr}" if v6_addr else "v6=no AAAA"
    if v6_addr:
        v6_txt += f" -> {'ok ' + format(dt6, '.2f') + 's' if s6 else 'FAIL ' + n6}"
    detail = f"DNS 提供 {fams}; v4={'ok' if s4 else 'FAIL'} ({dt4:.2f}s) {v6_txt}"
    if v6_addr and not s6 and s4:
        add(
            "IPv4 vs IPv6",
            "FAIL",
            detail,
            "IPv6 連不上、IPv4 可以：熱點發了 IPv6 前綴但上游 path MTU/防火牆壞掉。"
            "Chrome 會先試 IPv6 再退回 IPv4（Happy Eyeballs），所以「時好時壞、很慢」。",
            "最有效的一招：PC 端 Wi-Fi 介面關掉 IPv6，或手機熱點進階設定關閉 IPv6/改用 IPv4-only APN。",
        )
    elif s4:
        add("IPv4 vs IPv6", "OK", detail)
    else:
        add(
            "IPv4 vs IPv6",
            "FAIL",
            detail,
            "IPv4 都不通，先確認熱點上行本身。",
        )


def test_dns() -> None:
    per_host: dict[str, list[float]] = {}
    fails: list[str] = []
    for host in DNS_HOSTS:
        for _ in range(DNS_N):
            t0 = time.perf_counter()
            try:
                socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
                per_host.setdefault(host, []).append(time.perf_counter() - t0)
            except Exception as exc:  # noqa: BLE001
                fails.append(f"{host}:{type(exc).__name__}")
    worst = max((statistics.median(v) for v in per_host.values()), default=0.0)
    med_line = ", ".join(f"{h.split('.')[0]}={statistics.median(v) * 1000:.0f}ms" for h, v in per_host.items())
    if fails:
        add(
            "DNS",
            "FAIL",
            f"失敗 {fails}; {med_line}",
            "熱點 DNS proxy(192.168.x.1)壞 → 新網域解析不到，"
            "但已連線的影片續傳不受影響。解法：PC 改用手動 DNS 1.1.1.1 / 8.8.8.8。",
        )
    elif worst > 0.5:
        add(
            "DNS",
            "WARN",
            f"最慢中位數 {worst * 1000:.0f}ms; {med_line}",
            "DNS 慢會讓「一頁開很多不同網域」(留言/清單) 卡住，而單一長連線影片無感。",
        )
    else:
        add("DNS", "OK", f"最慢中位數 {worst * 1000:.0f}ms; {med_line}")


def test_sustained() -> None:
    """大流量長連線：對照 YouTube 4K 很順這件事。"""
    from curl_cffi import requests

    t0 = time.perf_counter()
    got = 0
    try:
        r = requests.get(BIG_URL.format(uuid.uuid4().hex[:8]), impersonate="chrome", timeout=20, stream=True)
        if r.status_code != 200:
            add("sustained downlink", "WARN", f"測試端點回 {r.status_code}，跳過（非你的網路問題）")
            return
        for chunk in r.iter_content(65536):
            got += len(chunk)
            if time.perf_counter() - t0 > 6:
                break
        r.close()
    except Exception as exc:  # noqa: BLE001
        add("sustained downlink", "FAIL", f"{got / 125000:.2f} Mbps 後失敗：{exc}")
        return
    mbps = got * 8 / max(time.perf_counter() - t0, 1e-6) / 1e6
    add(
        "sustained downlink",
        "OK" if mbps > 5 else "WARN",
        f"{mbps:.1f} Mbps（{got / 1e6:.1f} MB / {time.perf_counter() - t0:.1f}s）",
        "長連線大流量沒問題，所以「影片順、請求壞」不是頻寬問題，而是新連線建立/多 flow 的管理問題。",
    )


def main() -> None:
    _fix_console()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=SMALL_URL, help="額外測試的 URL（例如 https://llm.chutes.ai）")
    args = ap.parse_args()

    logger.info("=== 介面 / MTU ===")
    show_subinterfaces()
    show_ip_config()
    mtu = probe_mtu()
    if mtu:
        RESULTS.append(mtu)
        logger.info("[%s] %-22s %s", mtu.verdict, mtu.name, mtu.detail)

    targets = [SMALL_URL, "https://www.youtube.com/"]
    if args.target not in targets:
        targets.append(args.target)

    for url in targets:
        host = url.split("/")[2]
        logger.info("\n=== %s ===", host)
        test_sequential(url)
        test_parallel(url)
        test_http_versions(url)
        test_ip_families(url)

    logger.info("\n=== DNS ===")
    test_dns()
    logger.info("\n=== 長連線吞吐 ===")
    test_sustained()

    logger.info("\n================= SUMMARY =================")
    for r in RESULTS:
        logger.info("%-4s %-26s %s", r.verdict, r.name, r.detail)
        for h in r.hints:
            logger.info("       -> %s", h)
    bad = [r for r in RESULTS if r.verdict != "OK"]
    logger.info("\n%d 項非 OK。把上面整段貼給我看就能收斂原因。", len(bad))


if __name__ == "__main__":
    sys.exit(main())
