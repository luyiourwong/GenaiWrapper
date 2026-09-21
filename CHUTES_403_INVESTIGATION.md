# `llm.chutes.ai` 403 調查報告

> 調查日期：2026-09-21
> 症狀：Cherry Studio（Electron/Chromium）連 `https://llm.chutes.ai/v1/chat/completions` 固定 403，但同一顆 API key 用 curl 正常。
> 後續更正：本報告早期曾誤判為「防毒 SSL 攔截」，最終結論是 **HTTP/2 指紋**，詳見第 6 節與第 10 節。

---

## 1. 結論（TL;DR）

**根因：`llm.chutes.ai` 前面的 Google 邊緣（Google Front End / Cloud Armor）會對「特定的 HTTP/2 連線指紋」回傳 403。**

- **強制 HTTP/1.1 → 一律 200**（20/20 種指紋全部通過，含 Cherry Studio）。
- Cherry Studio（Chromium/Electron）走 h2 → 403。
- tunnel proxy（保留 Chromium 的 h2）→ 403。
- MITM proxy（由 httpx 以 HTTP/1.1 重新發起）→ 200。

**與 Bearer key 格式無關**（request 內容逐 byte 相同），也不是速率限制（三次重跑失敗集合完全一致）。

**解法**

| 對象 | 做法 | 狀態 |
|---|---|---|
| Cherry Studio | 啟動參數加 `--disable-http2` | ✅ 實測有效 |
| 任何 client | 用會以 HTTP/1.1 重新發起的 MITM relay | ✅ 實測有效 |
| chutes 端 | 檢查 Cloud Armor 的 HTTP/2 指紋規則 | 建議（根本解） |

---

## 2. 環境

| 項目 | 值 |
|---|---|
| OS | Windows 11 Pro |
| 有問題的 client | Cherry Studio 2.0.14（Electron / Chromium） |
| 對照 client | curl 8.7.1 (Schannel，無 HTTP/2)、Node 22 (undici h1 / `node:http2`)、Python httpx、curl_cffi 0.16.3 (BoringSSL，可選 h1/h2) |
| Endpoint | `https://llm.chutes.ai/v1/chat/completions` |
| 上游解析 | `llm.chutes.ai` → `34.111.142.178`（無 AAAA、無 HTTPS RR） |
| 重現範圍 | 兩台機器、兩個不同地點皆重現 |

---

## 3. 症狀

同一顆 API key、同一份 body：

| 路徑 | 誰送 TLS/h2 到 chutes | HTTP 版本 | 結果 |
|---|---|---|---|
| Cherry Studio 直連 | Chromium/Electron | **h2** | **403** |
| Cherry Studio 走 tunnel proxy | Chromium/Electron（原封不動） | **h2** | **403** |
| Cherry Studio 走 MITM proxy | 我們的 proxy（httpx） | **h1** | **200** |
| curl / Node h1 / httpx | 各自 | h1 | 200 |
| Node `http2` / curl_cffi 部分版本 | 各自 | h2 | 200 或 403（見第 4 節） |

### 403 回應（來自 Google 邊緣，非 Chutes API 錯誤）

```
HTTP/1.1 403
alt-svc: clear
content-length: 134
content-type: text/html; charset=UTF-8
date: ...
document-policy: include-js-call-stacks-in-crash-reports
via: 1.1 google

<!doctype html><meta charset="utf-8"><meta name=viewport content="width=device-width, initial-scale=1"><title>403</title>403 Forbidden
```

### 正常 200 回應（對照）

```
server: nginx
x-chutes-invocationid: ...
x-chutes-rl-user: 60
via: 1.1 google
alt-svc: clear
```

> `alt-svc: clear` 在 200 也會出現，**不是** 403 專屬線索（一度被誤判為 HTTP/3 線索）。

---

## 4. 決定性證據

### 4.1 指紋矩陣：h2 vs h1（`chutes_fingerprints.py`）

固定同一份 key/header/body，只換 TLS/HTTP 指紋：

| impersonate | h2 (預設) | **強制 h1 (`--http1`)** |
|---|---|---|
| chrome99 / 104 / 110 / 116 / 120 | 200 | 200 |
| chrome124 | 200 | 200 |
| chrome131 | 200 | 200 |
| **chrome133a** | **403** | **200** |
| **chrome136** | **403** | **200** |
| **chrome142** | **403** | **200** |
| **chrome145** | **403** | **200** |
| **chrome146** | **403** | **200** |
| chrome150 / chrome(latest) | 200 | 200 |
| edge99 / edge101 | 200 | 200 |
| firefox133 / firefox147 | 200 | 200 |
| **safari17_0** | **403** | **200** |
| safari18_0 | 200 | 200 |

**強制 HTTP/1.1 後 20/20 全部 200** → 與 TLS 指紋無關，問題在 **HTTP/2**。

### 4.2 確定性（非速率限制）

- `--repeat 3`：三輪失敗集合**完全相同**（chrome133a、chrome136、chrome142、chrome145、chrome146、safari17_0）。
- `--delay 3`：失敗集合**不變** → 不是 rate limit。

### 4.3 不是單純的 JA3

- `safari17_0`（403）與 `safari18_0`（200）**濾掉 GREASE 後的 JA3 完全相同**（`5a527c775ff4ae29b4f0c77b113f9625`）。
- 暴力掃描所有可從 ClientHello 取得的特徵（extension 集合/順序、signature_algorithms、supported_groups、key_share、cipher suites、supported_versions、長度、session_id 長度…），**沒有任何單一特徵**能分開 403 與 200。
- → 是**複合指紋**，而 `--http1` 的結果指出關鍵在 **HTTP/2 層**（SETTINGS / WINDOW_UPDATE / priority / pseudo-header 順序等）。

### 4.4 Cherry Studio 的 ClientHello 是原始 Chromium（未被防毒改寫）

從 `pktmon` 擷取 Cherry Studio 直連時送往 `34.111.142.178` 的 ClientHello：

```
SNI=llm.chutes.ai  len=1723  ALPN=['h2','http/1.1']  JA3=931d4f5d4917deb2ba08e3979b91dc36
ext : fafa ff01 0 17 2d 1b 44cd b 23 d 2b fe0d 10 33 12 5 a 1a1a
curve: eaea 11ec(X25519MLKEM768) 1d 17 18
```

屬現代 Chrome 的正常指紋（GREASE、ECH GREASE `0xfe0d`、delegated credentials `0x44cd`、post-quantum `0x11ec`），**沒有被 ESET 改寫**。

---

## 5. 排除清單

| 假設 | 排除理由 |
|---|---|
| Bearer key 格式 / 內容 | MITM 攔到的 header 與 body 和可通的 curl 請求完全相同 |
| 所有 header（含 `sec-fetch-*`、缺 `Accept`） / User-Agent | 全套照抄給 curl / Node(h1,h2) 皆 200 |
| HTTP/1.1 vs HTTP/2（作為「TLS 差異」） | 見第 4 節；問題確定在 h2 層 |
| TLS 指紋 (JA3/JA4) | 濾掉 GREASE 後 JA3 相同的兩者結果不同；強制 h1 後連「被擋的版本」都 200 |
| HTTP/3 (QUIC) | 以管理員封鎖 outbound UDP/443（規則 Enabled=True 已驗證）後直連仍 403；且無 HTTPS RR |
| ECH | `llm.chutes.ai` 無 HTTPS RR（DNS type 65 無 Answer） |
| DNS / 連錯節點 | 系統 DNS 與 DoH 皆 `34.111.142.178`；`Get-NetTCPConnection` 證實直連就是該 IP |
| 系統 Proxy / WPAD | `ProxyEnable=0`、無 `AutoConfigURL` |
| IPv6 | 無 AAAA；該機無 IPv6 路由 |
| 速率限制 | `--repeat 3` 失敗集合一致、`--delay 3` 無效 |
| alt-svc 快取 | 刪除 Chromium `Network Persistent State` 後仍 403 |
| **防毒 SSL 攔截（ESET/Kaspersky）** | Edge 確實被 MITM（憑證 issuer = `ESET SSL Filter CA`），但 **Cherry Studio 沒有**：實測其 ClientHello 為原始 Chromium（第 4.4 節）。此為早期誤判，已排除 |

---

## 6. 根因

`llm.chutes.ai` 由 Google Front End 提供，前面掛 Cloud Armor。該邊緣層對**特定 HTTP/2 連線指紋**回傳 403（Google 的通用 HTML 403 頁），但對 HTTP/1.1 一律放行。

- 觸發條件在 **HTTP/2 層**（可能是 SETTINGS（含 GREASE settings）、WINDOW_UPDATE、priority frames、pseudo-header 順序，或 TLS+h2 的複合簽章）。
- Cherry Studio（Electron/Chromium）的 h2 指紋正好落在被擋的集合內。
- 其他 API 前端沒有這種規則，因此只有 chutes 會中 —— 這也是「為什麼只有 chutes 有事」的答案。

---

## 7. 解法

### 7.1 Cherry Studio：停用 HTTP/2（已實測有效）

在 Cherry Studio 捷徑的「目標」後面加上：

```
--disable-http2
```

例：
```
"C:\Users\<你>\AppData\Local\Programs\Cherry Studio\Cherry Studio.exe" --disable-http2
```

### 7.2 通用：以 HTTP/1.1 重新發起的 MITM relay（已實測有效）

因為必须是「由另一個 stack 用 h1 重新發起」，**tunnel proxy 無效**（保留 h2），要用 **MITM 模式**：

```powershell
.venv\Scripts\python.exe mitm_proxy.py     # 不是 --tunnel
# Cherry Studio proxy = 自訂 http://127.0.0.1:8888
```

### 7.3 根本解：chutes 端調整

請 chutes 檢查 Cloud Armor 是否對 HTTP/2 指紋（含 GREASE settings / priority）設定過嚴規則，導致正牌瀏覽器/Electron 被擋。

---

## 8. 建議的回報方式

### 8.1 給 Cherry Studio（建議加 FAQ，而非改程式）

> **Q：某些 OpenAI 相容供應商出現 403 Forbidden（例如 llm.chutes.ai），但同樣的 key 用 curl 正常？**
>
> **A：** 該供應商前面的 CDN/WAF 可能對 HTTP/2 指紋有嚴格規則，導致 Chromium/Electron 的連線被擋。可在 Cherry Studio 執行檔後面加上 `--disable-http2` 啟動參數強制使用 HTTP/1.1（實測可解）。這是 Chromium 內建參數，不需修改程式。

（若 Cherry 願意，也可在「網路」設定提供「強制 HTTP/1.1」選項。）

### 8.2 給 Chutes（bug report）

```markdown
# llm.chutes.ai returns edge 403 for specific HTTP/2 fingerprints; HTTP/1.1 always works

## Summary
Requests to https://llm.chutes.ai/v1/chat/completions from Chromium/Electron clients
(and some browser fingerprints) are rejected with an HTTP/2-level 403 from the Google edge.
Forcing HTTP/1.1 makes every request succeed (20/20 fingerprints).

## Evidence
- Same API key / headers / body:
  - Cherry Studio 2.0.14 (Electron, HTTP/2) -> 403
  - same request relayed over HTTP/1.1      -> 200
  - curl / Node(http1) / httpx              -> 200
- Fingerprint matrix (curl_cffi impersonation, HTTP/2):
  403: chrome133a, chrome136, chrome142, chrome145, chrome146, safari17_0
  200: chrome99/104/110/116/120/124/131/150/latest, edge99/101, firefox133/147, safari18_0
- The same matrix with HTTP/1.1 forced -> **all 200**.
- Deterministic: three consecutive runs and a 3s-per-request run produced the identical 403 set
  (not rate limiting).
- Not a simple JA3 rule: safari17_0 (403) and safari18_0 (200) have the identical GREASE-filtered JA3.
- The 403 is the generic Google Front End page:
  `via: 1.1 google`, `content-type: text/html; charset=UTF-8`,
  `<!doctype html>...<title>403</title>403 Forbidden`.

## Ruled out
API key, headers, body, DNS/IP, IPv4/IPv6, system proxy, HTTP/3 (UDP 443 blocked), ECH (no HTTPS RR),
antivirus TLS interception (captured ClientHello is a genuine modern Chromium one).

## Request
Please review the Cloud Armor rule(s) that reject specific HTTP/2 fingerprints
(possibly related to HTTP/2 SETTINGS / GREASE settings / priority / pseudo-header ordering),
so that standard Chromium/Electron clients are not blocked.
```

---

## 9. 附錄：本次使用的工具

皆為獨立腳本，**未修改專案任何 tracked 程式碼**。

| 檔案 | 用途 |
|---|---|
| `echo_auth.py` | 簡易 Echo 伺服器，解析傳入的 `Authorization` header |
| `chutes_bisect.py` | Header / HTTP 版本 / IPv4-6 比對（curl + Node h1/h2） |
| `chutes_chrome.py` | 以 curl_cffi 模擬各瀏覽器指紋 |
| `chutes_fingerprints.py` | **指紋矩陣**：`--repeat`、`--delay`、`--http1` |
| `clienthello_versions.py` | 在本機擷取 curl_cffi 各版本的 ClientHello（比對 JA3/extension） |
| `tls_clienthello_compare.py` | 用 pktmon 擷取並比對直連 vs tunnel 的 ClientHello（`--inspect`、`--self-test`） |
| `mitm_proxy.py` | HTTPS proxy（MITM 模式看內容並以 h1 轉發；`--tunnel` 為純 TCP 轉發） |

### 常用指令

```powershell
# 指紋矩陣（需 CHUTES_API_KEY）
$env:CHUTES_API_KEY="cpk_..."
.venv\Scripts\python.exe chutes_fingerprints.py            # 預設 h2
.venv\Scripts\python.exe chutes_fingerprints.py --http1   # 強制 h1（全部 200）
.venv\Scripts\python.exe chutes_fingerprints.py --repeat 3# 驗證確定性
.venv\Scripts\python.exe chutes_fingerprints.py --delay 3 # 排除速率限制

# 本機擷取 curl_cffi 各版本 ClientHello（不需 key/管理員）
.venv\Scripts\python.exe clienthello_versions.py

# HTTP/1.1 relay（不需憑證、不碰專案）
.venv\Scripts\python.exe mitm_proxy.py --tunnel   # 保留 h2 -> 仍會被擋
.venv\Scripts\python.exe mitm_proxy.py            # MITM，以 h1 轉發 -> 可通
```

### 清理本次調查的暫時變更

```powershell
Remove-NetFirewallRule -DisplayName "Block QUIC"
uv pip uninstall --python .venv\Scripts\python.exe curl_cffi
Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like "*GenaiWrapper MITM CA*" | Remove-Item
Remove-Item -Recurse -Force "$env:TEMP\genaiwrapper_mitm", "$env:TEMP\genaiwrapper_tls"
```

---

## 10. 更正說明

本報告初版把根因判為「本機防毒（ESET）SSL 攔截」，**該結論錯誤**，原因如下：

- Edge 的憑證 issuer 確實是 `ESET SSL Filter CA`，但那只是證明 **Edge** 被攔；
- 以 `pktmon` 實際擷取 **Cherry Studio** 直連的 ClientHello，得到的是**原始現代 Chromium 指紋**（第 4.4 節），代表 Cherry Studio 這條連線**沒有**被防毒 MITM。

後續以「同一 key、同一 body、只換 TLS/HTTP 指紋」的矩陣實驗，才定位到真正原因是 **HTTP/2 指紋**（強制 h1 後 20/20 全 200）。
