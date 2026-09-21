"""HTTPS MITM Proxy（診斷用，獨立於 GenaiWrapper 專案）。

用途：攔截 Electron/Chromium App（如 Cherry Studio）送往上游的 HTTPS 請求，
完整印出 method / URL / headers / body，再原樣轉發到真正的上游，
用來確認 App 實際送出的內容，以及「經過一個乾淨的 proxy 路徑」是否能通。

啟動：
    .venv\\Scripts\\python.exe mitm_proxy.py [--port 8888]

使用步驟：
    1. Cherry Studio → 設定 → Proxy = http://127.0.0.1:8888
    2. 讓 Cherry Studio 信任本工具產生的憑證（二選一）：
       A) 以 --ignore-certificate-errors 啟動 Cherry Studio（最快）
       B) 把 CA 匯入目前使用者的根憑證：
          Import-Certificate -FilePath "<CA .crt 路徑>" -CertStoreLocation Cert:\\CurrentUser\\Root
    3. 觸發一次請求，看 console 或 mitm_capture.log

注意：本工具對客戶端只提供 HTTP/1.1（無法在攔截時模擬 h2/h3），
      目的是看「請求內容」與驗證「乾淨 proxy 路徑」，不是重現傳輸指紋。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("mitm")

HOP_BY_HOP = {
    "connection",
    "proxy-connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

CERT_DIR = Path(tempfile.gettempdir()) / "genaiwrapper_mitm"
CAPTURE_LOG = CERT_DIR / "mitm_capture.log"


def log(message: str) -> None:
    """同時輸出到 console 與捕獲記錄檔"""
    print(message, flush=True)
    try:
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        with CAPTURE_LOG.open("a", encoding="utf-8") as fp:
            fp.write(f"{datetime.now().isoformat()} {message}\n")
    except OSError:
        pass


class CertAuthority:
    """建立/快取 MITM 用的 CA 與各 host 的葉憑證"""

    def __init__(self) -> None:
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        self.ca_cert_path = CERT_DIR / "ca.pem"
        self.ca_key_path = CERT_DIR / "ca_key.pem"
        self.ca_crt_path = CERT_DIR / "ca.crt"  # DER，供 Windows 匯入
        self._leaf_cache: dict[str, ssl.SSLContext] = {}

        if self.ca_cert_path.exists() and self.ca_key_path.exists():
            self._ca_key = serialization.load_pem_private_key(self.ca_key_path.read_bytes(), password=None)
            self._ca_cert = x509.load_pem_x509_certificate(self.ca_cert_path.read_bytes())
        else:
            self._ca_key, self._ca_cert = self._make_ca()
            self.ca_key_path.write_bytes(
                self._ca_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            self.ca_cert_path.write_bytes(self._ca_cert.public_bytes(serialization.Encoding.PEM))
            self.ca_crt_path.write_bytes(self._ca_cert.public_bytes(serialization.Encoding.DER))

    def _make_ca(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GenaiWrapper MITM CA")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        return key, cert

    def ssl_context(self, host: str) -> ssl.SSLContext:
        """取得（必要時產生）指定 host 的 server SSLContext"""
        if host in self._leaf_cache:
            return self._leaf_cache[host]

        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(self._ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .sign(self._ca_key, hashes.SHA256())
        )

        cert_path = CERT_DIR / f"{host}.pem"
        key_path = CERT_DIR / f"{host}.key"
        cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        ctx.set_alpn_protocols(["http/1.1"])
        self._leaf_cache[host] = ctx
        return ctx


def parse_headers(raw: bytes) -> dict[str, str]:
    """把 header 區塊解析成 dict（保留原始大小寫）"""
    headers: dict[str, str] = {}
    for line in raw.decode("latin1").split("\r\n"):
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip()] = value.strip()
    return headers


async def read_body(reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
    """依 Content-Length / chunked 讀取 request body"""
    lowered = {k.lower(): v for k, v in headers.items()}
    if "chunked" in lowered.get("transfer-encoding", "").lower():
        chunks: list[bytes] = []
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size = int(size_line.strip().split(b";")[0] or b"0", 16)
            if size == 0:
                await reader.readuntil(b"\r\n")
                break
            chunks.append(await reader.readexactly(size))
            await reader.readexactly(2)
        return b"".join(chunks)
    length = lowered.get("content-length")
    if length:
        return await reader.readexactly(int(length))
    return b""


def format_body(body: bytes) -> str:
    """body 盡量以文字呈現，順便驗證是否為合法 JSON"""
    if not body:
        return "<empty>"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary {len(body)} bytes> {body[:64].hex()}"
    try:
        return json.dumps(json.loads(text), ensure_ascii=False)
    except json.JSONDecodeError:
        return text


async def forward(
    writer: asyncio.StreamWriter,
    scheme: str,
    host: str,
    method: str,
    target: str,
    headers: dict[str, str],
    body: bytes,
) -> None:
    """記錄並轉發一個請求，把上游回應回寫給客戶端"""
    url = target if target.lower().startswith(("http://", "https://")) else f"{scheme}://{host}{target}"

    log("")
    log("=" * 70)
    log(f">>> {method} {url}")
    for name, value in headers.items():
        log(f"    {name}: {value!r}")
    log(f"    <body {len(body)} bytes> {format_body(body)}")

    fwd_headers = {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in {"host", "content-length", "accept-encoding"}
    }
    fwd_headers["accept-encoding"] = "identity"

    try:
        async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
            async with client.stream(method, url, headers=fwd_headers, content=body) as resp:
                log(f"<<< {resp.status_code} {resp.reason_phrase}")
                for name, value in resp.headers.items():
                    log(f"    {name}: {value!r}")
                writer.write(f"HTTP/1.1 {resp.status_code} {resp.reason_phrase}\r\n".encode("latin1"))
                for name, value in resp.headers.items():
                    if name.lower() in HOP_BY_HOP or name.lower() in {"content-encoding", "content-length"}:
                        continue
                    writer.write(f"{name}: {value}\r\n".encode("latin1"))
                writer.write(b"Connection: close\r\n\r\n")
                await writer.drain()
                async for chunk in resp.aiter_raw():
                    writer.write(chunk)
                    await writer.drain()
    except Exception as exc:  # noqa: BLE001
        log(f"!!! 轉發失敗: {exc}")
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        await writer.drain()


async def serve_requests(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    scheme: str,
    default_host: str,
) -> None:
    """在（可能已加密的）連線上處理單一請求"""
    request_line = (await reader.readuntil(b"\r\n")).decode("latin1").strip()
    if not request_line:
        return
    parts = request_line.split(" ")
    method, target = parts[0], parts[1]

    headers_raw = await reader.readuntil(b"\r\n\r\n")
    headers = parse_headers(headers_raw)
    body = await read_body(reader, headers)

    host = headers.get("host", default_host)
    await forward(writer, scheme, host, method, target, headers, body)


async def tunnel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str) -> None:
    """不做 MITM，直接把 TCP 通道接去上游（--tunnel 模式）"""
    host, _, port = target.partition(":")
    up_reader, up_writer = await asyncio.open_connection(host, int(port or 443))
    counts = {"up": 0, "down": 0}

    async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter, key: str) -> None:
        try:
            while data := await src.read(65536):
                counts[key] += len(data)
                dst.write(data)
                await dst.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            dst.close()

    await asyncio.gather(pipe(reader, up_writer, "up"), pipe(up_reader, writer, "down"))
    # 由 server->client 的位元組量可粗判結果：403 錯誤頁很小、串流回覆很大
    log(f"[TUNNEL] {target}  client->server={counts['up']}  server->client={counts['down']}")


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ca: CertAuthority,
    use_tunnel: bool = False,
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        request_line = (await reader.readuntil(b"\r\n")).decode("latin1").strip()
        if not request_line:
            return
        parts = request_line.split(" ")
        method, target = parts[0], parts[1]
        headers = parse_headers(await reader.readuntil(b"\r\n\r\n"))

        if method.upper() == "CONNECT":
            host, _, port = target.partition(":")
            log(f"[CONNECT] {target}  from {peer}")
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            if use_tunnel:
                await tunnel(reader, writer, target)
                return
            # 由 asyncio.start_server 建立的 StreamWriter 會自動以 server_side 進行 TLS
            await writer.start_tls(ca.ssl_context(host))
            await serve_requests(reader, writer, "https", host)
        else:
            # 純 HTTP proxy 請求（absolute-form）
            body = await read_body(reader, headers)
            host = headers.get("host", "")
            await forward(writer, "http", host, method, target, headers, body)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as exc:  # noqa: BLE001
        log(f"!!! 連線處理失敗: {exc!r}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="HTTPS MITM proxy（診斷用）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--tunnel", action="store_true", help="只做 TCP 轉發，不做 MITM（不需憑證）")
    args = parser.parse_args()

    ca = CertAuthority()
    log("=" * 70)
    log(f"MITM proxy listening on http://{args.host}:{args.port}  (tunnel={args.tunnel})")
    log(f"CA 憑證 (PEM): {ca.ca_cert_path}")
    log(f"CA 憑證 (DER/匯入用): {ca.ca_crt_path}")
    log("Cherry Studio proxy 設定為 http://%s:%s" % (args.host, args.port))
    log("信任憑證二選一：")
    log("  A) Cherry Studio 捷徑加 --ignore-certificate-errors 啟動")
    log(f'  B) Import-Certificate -FilePath "{ca.ca_crt_path}" -CertStoreLocation Cert:\\CurrentUser\\Root')
    log(f"捕獲記錄檔: {CAPTURE_LOG}")
    log("=" * 70)

    async def run() -> None:
        server = await asyncio.start_server(lambda r, w: handle_client(r, w, ca, args.tunnel), args.host, args.port)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("MITM proxy 停止")


if __name__ == "__main__":
    sys.exit(main())
