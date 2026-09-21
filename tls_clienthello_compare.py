"""比較「直連」與「走 tunnel」時送往 llm.chutes.ai 的 TLS ClientHello，
用來判定 Chromium 直連是否被本機防毒（ESET/Kaspersky）改寫。

作法：
  1. 用內建 pktmon 抓封包（需系統管理員）
  2. 轉成 pcapng 後解析出 TLS ClientHello
  3. 比對兩次的 JA3 / ALPN / extension 清單 / 原始 bytes

用法（請以「系統管理員」身分執行）：
    .venv\\Scripts\\python.exe tls_clienthello_compare.py
    .venv\\Scripts\\python.exe tls_clienthello_compare.py --ip 34.111.142.178
    .venv\\Scripts\\python.exe tls_clienthello_compare.py --self-test   # 不需管理員

判讀：
    JA3 相同        -> 直連的 ClientHello 沒被改寫（防毒不是透過改 TLS 內容）
    JA3 不同        -> 直連的 ClientHello 被改寫，極可能就是防毒 SSL 攔截
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

WORK_DIR = Path(tempfile.gettempdir()) / "genaiwrapper_tls"
_TLS_HANDSHAKE = 0x16
_CLIENT_HELLO = 0x01

# TLS extension ids
_EXT_SERVER_NAME = 0x0000
_EXT_SUPPORTED_GROUPS = 0x000A
_EXT_EC_POINT_FORMATS = 0x000B
_EXT_ALPN = 0x0010


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def run(cmd: list[str]) -> str:
    """執行外部命令並回傳輸出（容忍非 UTF-8）"""
    proc = subprocess.run(cmd, capture_output=True)
    out = proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode("utf-8", errors="replace")
    return out.strip()


# --------------------------------------------------------------------------- #
# pcapng / pcap 解析
# --------------------------------------------------------------------------- #
def parse_pcapng(path: Path) -> list[bytes]:
    """解析 pcapng，回傳每個封包的位元組（link-layer 標頭保留）"""
    data = path.read_bytes()
    packets: list[bytes] = []
    offset = 0
    endian = "<"
    while offset + 12 <= len(data):
        block_type = struct.unpack_from(endian + "I", data, offset)[0]
        if block_type == 0x0A0D0D0A:  # Section Header Block
            magic = data[offset + 8 : offset + 12]
            endian = "<" if magic == b"\x4d\x3c\x2b\x1a" else ">"
            total = struct.unpack_from(endian + "I", data, offset + 4)[0]
        else:
            total = struct.unpack_from(endian + "I", data, offset + 4)[0]
        if total < 12 or offset + total > len(data):
            break

        # Enhanced Packet Block
        if block_type == 0x00000006:
            caplen = struct.unpack_from(endian + "I", data, offset + 20)[0]
            packets.append(data[offset + 28 : offset + 28 + caplen])
        # Simple Packet Block
        elif block_type == 0x00000003:
            origlen = struct.unpack_from(endian + "I", data, offset + 8)[0]
            packets.append(data[offset + 12 : offset + 12 + origlen])
        # obsolete Packet Block
        elif block_type == 0x00000002:
            caplen = struct.unpack_from(endian + "I", data, offset + 20)[0]
            packets.append(data[offset + 28 : offset + 28 + caplen])

        offset += total
    return packets


def parse_pcap(path: Path) -> list[bytes]:
    """解析 classic pcap（給非 pktmon 的來源備用）"""
    data = path.read_bytes()
    magic = data[:4]
    endian = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    offset = 24
    packets: list[bytes] = []
    while offset + 16 <= len(data):
        incl_len = struct.unpack_from(endian + "I", data, offset + 8)[0]
        packets.append(data[offset + 16 : offset + 16 + incl_len])
        offset += 16 + incl_len
    return packets


def read_packets(path: Path) -> list[bytes]:
    head = path.read_bytes()[:4]
    if head == b"\x0a\x0d\x0d\x0a":
        return parse_pcapng(path)
    return parse_pcap(path)


def _find_ip_offset(pkt: bytes) -> int | None:
    """在封包中找出 IPv4 標頭的起點（相容 Ethernet / VLAN / pktmon / offload 的 total_len=0）"""
    for off in range(0, min(len(pkt) - 20, 64)):
        if pkt[off] >> 4 != 4 or (pkt[off] & 0x0F) < 5:
            continue
        proto = pkt[off + 9]
        if proto not in (6, 17):  # TCP / UDP
            continue
        total_len = struct.unpack_from(">H", pkt, off + 2)[0]
        # total_len==0 是網卡 offload/TSO 常見情況，仍視為合法
        if total_len != 0 and not 20 <= total_len <= 65535:
            continue
        return off
    return None


def parse_ipv4_tcp(pkt: bytes) -> tuple[bytes, bytes, int, int, int, bytes] | None:
    """回傳 (src_ip, dst_ip, sport, dport, tcp_seq, tcp_payload)"""
    off = _find_ip_offset(pkt)
    if off is None:
        return None
    ip = pkt[off:]
    ihl = (ip[0] & 0x0F) * 4
    if ip[9] != 6:  # TCP
        return None
    src, dst = ip[12:16], ip[16:20]
    tcp = ip[ihl:]
    if len(tcp) < 20:
        return None
    sport = struct.unpack_from(">H", tcp, 0)[0]
    dport = struct.unpack_from(">H", tcp, 2)[0]
    seq = struct.unpack_from(">I", tcp, 4)[0]
    data_off = (tcp[12] >> 4) * 4
    return src, dst, sport, dport, seq, tcp[data_off:]


def build_flows(packets: list[bytes]) -> dict[tuple, bytes]:
    """依 TCP sequence number 重組每條 flow（同時去掉重複擷取的封包）"""
    segments: dict[tuple, dict[int, bytes]] = {}
    for pkt in packets:
        parsed = parse_ipv4_tcp(pkt)
        if not parsed:
            continue
        src, dst, sport, dport, seq, payload = parsed
        if not payload:
            continue
        segments.setdefault((src, sport, dst, dport), {})[seq] = payload

    flows: dict[tuple, bytes] = {}
    for key, seg in segments.items():
        buf = bytearray()
        next_seq: int | None = None
        for sq in sorted(seg):
            if next_seq is None:
                next_seq = sq
            if sq > next_seq:
                break  # 有缺口，停止
            data = seg[sq]
            if sq + len(data) > next_seq:
                buf += data[next_seq - sq :]
                next_seq = sq + len(data)
        flows[key] = bytes(buf)
    return flows


# --------------------------------------------------------------------------- #
# TLS ClientHello 解析
# --------------------------------------------------------------------------- #
def parse_client_hello(buf: bytes) -> dict | None:
    """從一段 bytes 解析 ClientHello，回傳欄位；失敗回傳 None"""
    if len(buf) < 45 or buf[0] != _TLS_HANDSHAKE or buf[5] != _CLIENT_HELLO:
        return None
    record_len = struct.unpack_from(">H", buf, 3)[0]
    body = buf[5 : 5 + record_len]
    if len(body) < 40:
        return None

    hs_len = int.from_bytes(body[1:4], "big")
    body = body[: 4 + hs_len]
    ver = struct.unpack_from(">H", body, 4)[0]
    cursor = 4 + 2 + 32  # handshake hdr + client version + random

    sid_len = body[cursor]
    cursor += 1 + sid_len

    cs_len = struct.unpack_from(">H", body, cursor)[0]
    cursor += 2
    cipher_suites = [struct.unpack_from(">H", body, cursor + i)[0] for i in range(0, cs_len, 2)]
    cursor += cs_len

    comp_len = body[cursor]
    cursor += 1 + comp_len

    extensions: list[int] = []
    curves: list[int] = []
    ec_formats: list[int] = []
    alpn: list[str] = []
    sni: str | None = None

    if cursor + 2 <= len(body):
        ext_total = struct.unpack_from(">H", body, cursor)[0]
        cursor += 2
        ext_end = min(cursor + ext_total, len(body))
        while cursor + 4 <= ext_end:
            ext_type, ext_len = struct.unpack_from(">HH", body, cursor)
            cursor += 4
            ext_data = body[cursor : cursor + ext_len]
            cursor += ext_len
            extensions.append(ext_type)
            if ext_type == _EXT_SERVER_NAME and len(ext_data) > 3:
                name_len = struct.unpack_from(">H", ext_data, 3)[0]
                sni = ext_data[5 : 5 + name_len].decode("ascii", errors="replace")
            elif ext_type == _EXT_ALPN and len(ext_data) > 2:
                pos = 2
                while pos < len(ext_data):
                    ln = ext_data[pos]
                    alpn.append(ext_data[pos + 1 : pos + 1 + ln].decode("ascii", errors="replace"))
                    pos += 1 + ln
            elif ext_type == _EXT_SUPPORTED_GROUPS and len(ext_data) > 2:
                cnt = struct.unpack_from(">H", ext_data, 0)[0]
                curves = [struct.unpack_from(">H", ext_data, 2 + i)[0] for i in range(0, cnt, 2)]
            elif ext_type == _EXT_EC_POINT_FORMATS and len(ext_data) >= 1:
                ec_formats = list(ext_data[1 : 1 + ext_data[0]])

    ja3_raw = ",".join(
        [
            str(ver),
            "-".join(str(c) for c in cipher_suites),
            "-".join(str(e) for e in extensions),
            "-".join(str(c) for c in curves),
            "-".join(str(e) for e in ec_formats),
        ]
    )
    return {
        "version": ver,
        "cipher_suites": cipher_suites,
        "extensions": extensions,
        "curves": curves,
        "ec_formats": ec_formats,
        "alpn": alpn,
        "sni": sni,
        "ja3": hashlib.md5(ja3_raw.encode()).hexdigest(),
        "sha256": hashlib.sha256(buf[: 5 + record_len]).hexdigest(),
        "length": 5 + record_len,
        "raw": buf[: 5 + record_len],
    }


def extract_client_hellos(packets: list[bytes], target_ip: str) -> list[dict]:
    """從封包中取出送往 target_ip 的 ClientHello"""
    target = socket.inet_aton(target_ip)
    hellos: list[dict] = []
    for key, blob in build_flows(packets).items():
        if key[2] != target:
            continue
        idx = blob.find(b"\x16\x03")
        while idx != -1:
            info = parse_client_hello(blob[idx:])
            if info and info["sni"]:
                hellos.append(info)
                break
            idx = blob.find(b"\x16\x03", idx + 3)
    return hellos


# --------------------------------------------------------------------------- #
# 擷取流程
# --------------------------------------------------------------------------- #
def capture(target_ip: str, phase: str) -> list[dict]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    etl = WORK_DIR / f"{phase}.etl"
    pcapng = WORK_DIR / f"{phase}.pcapng"
    for f in (etl, pcapng):
        f.unlink(missing_ok=True)

    run(["pktmon", "filter", "remove"])
    print(run(["pktmon", "start", "--capture", "--pkt-size", "0", "--file", str(etl)]) or "(capture started)")

    input(f">>> 現在「開啟」Cherry Studio 並送出一次對話，看到結果後按 Enter 停止擷取（{phase}）...")

    print(run(["pktmon", "stop"]))
    out = run(["pktmon", "etl2pcap", str(etl), "--out", str(pcapng)])
    if not pcapng.exists():
        print("轉檔失敗:", out)
        return []

    packets = read_packets(pcapng)
    hellos = extract_client_hellos(packets, target_ip)

    # 順便列出 port 443 的遠端 IP，方便判斷流量到底去哪
    remote_ips: dict[str, int] = {}
    for pkt in packets:
        parsed = parse_ipv4_tcp(pkt)
        if not parsed:
            continue
        src, dst, sport, dport, _seq, _payload = parsed
        if 443 not in (sport, dport):
            continue
        remote = socket.inet_ntoa(dst if dport == 443 else src)
        remote_ips[remote] = remote_ips.get(remote, 0) + 1
    top = ", ".join(f"{ip}({n})" for ip, n in sorted(remote_ips.items(), key=lambda kv: -kv[1])[:6])
    print(f"    {phase}: 讀到 {len(packets)} 個封包，找到 {len(hellos)} 個送往 {target_ip} 的 ClientHello")
    print(f"    {phase}: port 443 遠端 IP -> {top or '(無)'}")
    if not hellos:
        print("    ⚠ 沒抓到 ClientHello：很可能是「既有連線被重複使用」，握手在擷取前就完成了。")
        print("      請先完全關閉 Cherry Studio，再開始擷取，然後重新開啟並送出對話。")
    return hellos


def describe(info: dict) -> None:
    print(f"    SNI            : {info['sni']}")
    print(f"    ClientHello len: {info['length']}")
    print(f"    TLS version    : {info['version']}")
    print(f"    ALPN           : {info['alpn']}")
    print(f"    cipher suites  : {len(info['cipher_suites'])} 個")
    print(f"    extensions     : {len(info['extensions'])} 個 -> {[hex(e) for e in info['extensions']]}")
    print(f"    curves         : {len(info['curves'])} 個")
    print(f"    JA3            : {info['ja3']}")
    print(f"    SHA256         : {info['sha256']}")
    print(f"    前 64 bytes    : {info['raw'][:64].hex()}")


def compare(direct: list[dict], tunnel: list[dict]) -> None:
    print("\n" + "=" * 70)
    if not direct or not tunnel:
        print("其中一次沒抓到 ClientHello，無法比對。")
        return
    d, t = direct[0], tunnel[0]

    print("=== 直連 (direct) ===")
    describe(d)
    print("=== 走 tunnel ===")
    describe(t)

    # 若 tunnel 的 ClientHello 沒有 h2，很可能抓到的是 proxy 自己的（即用了 MITM 模式而非 --tunnel）
    if "h2" not in t["alpn"]:
        print("\n⚠ tunnel 的 ALPN 不含 h2，但 Chromium 一定會提 h2。")
        print("   很可能抓到的是 proxy 自己（httpx/OpenSSL）的 ClientHello —— 請確認是用 --tunnel 模式。")
        print("   本次比對結果不可信，請重新以 --tunnel 再跑一次。")
        return

    print("\n=== 判定 ===")
    if d["ja3"] == t["ja3"] and d["sha256"] == t["sha256"]:
        print("兩者 ClientHello 完全相同 -> 直連沒有被改寫，防毒不是透過改 TLS 內容。")
        print("（那就需要往『連線層』或『請求內容』再查。）")
        return

    print("兩個 ClientHello 不同 -> 直連版本被改寫，極可能就是防毒 SSL 攔截！")
    if d["ja3"] != t["ja3"]:
        print(f"  JA3: direct={d['ja3']}  tunnel={t['ja3']}")
    if d["alpn"] != t["alpn"]:
        print(f"  ALPN 不同: direct={d['alpn']}  tunnel={t['alpn']}")
    if d["extensions"] != t["extensions"]:
        only_d = [hex(e) for e in d["extensions"] if e not in t["extensions"]]
        only_t = [hex(e) for e in t["extensions"] if e not in d["extensions"]]
        print(f"  只有 direct 有的 extension: {only_d}")
        print(f"  只有 tunnel 有的 extension: {only_t}")
    if len(d["cipher_suites"]) != len(t["cipher_suites"]):
        print(f"  cipher suite 數量: direct={len(d['cipher_suites'])}  tunnel={len(t['cipher_suites'])}")
    print("\n結論：tunnel 版本才是 Chromium 原始指紋（loopback 不被防毒攔）；")
    print("      direct 版本若明顯不同，即為防毒改寫的證據。")


# --------------------------------------------------------------------------- #
# 自我測試：用 MemoryBIO 產生真的 ClientHello，包成 pcapng 後解析
# --------------------------------------------------------------------------- #
def self_test() -> int:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    sslobj = ctx.wrap_bio(incoming, outgoing, server_hostname="llm.chutes.ai")
    try:
        sslobj.do_handshake()
    except ssl.SSLWantReadError:
        pass
    client_hello = outgoing.read()
    print(f"產生 ClientHello {len(client_hello)} bytes")

    # 組出 Ethernet + IPv4 + TCP 封包
    src_ip = socket.inet_aton("192.168.1.10")
    dst_ip = socket.inet_aton("34.111.142.178")
    tcp = struct.pack(">HHIIBBHHH", 51000, 443, 0, 0, 5 << 4, 0x18, 65535, 0, 0) + client_hello
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0, src_ip, dst_ip) + tcp
    eth = b"\xaa\xbb\xcc\xdd\xee\xff\x11\x22\x33\x44\x55\x66\x08\x00" + ip

    shb = struct.pack("<IIIHHq", 0x0A0D0D0A, 28, 0x1A2B3C4D, 1, 0, -1) + struct.pack("<I", 28)
    idb = struct.pack("<IIHHI", 0x00000001, 20, 1, 0, 0) + struct.pack("<I", 20)
    pad = (-len(eth)) % 4
    epb_len = 32 + len(eth) + pad
    epb = (
        struct.pack("<IIIIIII", 0x00000006, epb_len, 0, 0, 0, len(eth), len(eth))
        + eth
        + b"\x00" * pad
        + struct.pack("<I", epb_len)
    )
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    path = WORK_DIR / "selftest.pcapng"
    path.write_bytes(shb + idb + epb)

    packets = read_packets(path)
    hellos = extract_client_hellos(packets, "34.111.142.178")
    print(f"解析到 {len(packets)} 個封包，{len(hellos)} 個 ClientHello")
    if not hellos:
        print("自我測試失敗：解析不到 ClientHello")
        return 1
    describe(hellos[0])
    ok = hellos[0]["sni"] == "llm.chutes.ai" and hellos[0]["ja3"]
    print("自我測試:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def inspect(path: Path, target_ip: str | None = None) -> None:
    """列出擷取檔中所有 port 443 連線與其 ClientHello（診斷用）"""
    packets = read_packets(path)
    print(f"封包數: {len(packets)}")

    flows = build_flows(packets)
    remote_ips: dict[str, int] = {}
    tls_flows: list[tuple[tuple, bytes]] = []
    for key, blob in flows.items():
        src, sport, dst, dport = key
        if 443 not in (sport, dport):
            continue
        remote = dst if dport == 443 else src
        name = socket.inet_ntoa(remote)
        remote_ips[name] = remote_ips.get(name, 0) + 1
        tls_flows.append((key, blob))

    print("\nport 443 遠端 IP 統計 (以 flow 數計):")
    for ip, count in sorted(remote_ips.items(), key=lambda kv: -kv[1]):
        mark = "  <-- target" if ip == target_ip else ""
        print(f"    {ip}: {count} flows{mark}")

    print(f"\nClientHello 掃描（共 {len(tls_flows)} 條 port 443 flow）:")
    found = 0
    for key, raw in tls_flows:
        src, sport, dst, dport = key
        idx = raw.find(b"\x16\x03")
        info = parse_client_hello(raw[idx:]) if idx != -1 else None
        if info:
            found += 1
            print(
                f"    {socket.inet_ntoa(src)}:{sport} -> {socket.inet_ntoa(dst)}:{dport}  "
                f"SNI={info['sni']}  len={info['length']}  ALPN={info['alpn']}  JA3={info['ja3']}"
            )
        elif raw:
            print(
                f"    {socket.inet_ntoa(src)}:{sport} -> {socket.inet_ntoa(dst)}:{dport}  "
                f"(無 ClientHello, payload {len(raw)} bytes, 開頭 {raw[:8].hex()})"
            )
    print(f"總計 {found} 個 ClientHello")


def main() -> int:
    parser = argparse.ArgumentParser(description="比對 直連 vs tunnel 的 TLS ClientHello")
    parser.add_argument("--ip", default="34.111.142.178", help="目標 IP")
    parser.add_argument("--self-test", action="store_true", help="不抓封包，驗證解析器")
    parser.add_argument("--inspect", metavar="PCAPNG", help="分析已擷取的檔案")
    args = parser.parse_args()

    if args.inspect:
        inspect(Path(args.inspect), args.ip)
        return 0

    if args.self_test:
        return self_test()

    if not shutil.which("pktmon"):
        print("找不到 pktmon（應內建於 Windows 10/11）。")
        return 1
    if not is_admin():
        print("需要系統管理員權限才能使用 pktmon。請以「系統管理員」開啟 PowerShell 後再執行。")
        return 1

    print("=" * 70)
    print("步驟 1：Chromium 直連（proxy 設「無」）")
    print("  先完全關閉 Cherry Studio（確定沒有殘留背景程序），再按 Enter 開始擷取。")
    input("  [關閉 Cherry Studio 後按 Enter] ...")
    direct = capture(args.ip, "direct")

    print("\n" + "=" * 70)
    print("步驟 2：走本機 tunnel")
    print("  1) 開一個新的 PowerShell 視窗執行：.venv\\Scripts\\python.exe mitm_proxy.py --tunnel")
    print("     （一定要是 --tunnel 模式；若 8888 被之前的 MITM 模式佔用，請先關掉那個）")
    print("  2) Cherry Studio proxy 設為「自訂」http://127.0.0.1:8888")
    print("  3) 再完全關閉 Cherry Studio，按 Enter 開始擷取。")
    input("  [關閉 Cherry Studio 後按 Enter] ...")
    tunnel = capture(args.ip, "tunnel")

    compare(direct, tunnel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
