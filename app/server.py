"""票据核验演示服务。只用 Python 标准库，本机运行：

    python app/server.py              # 打开 http://localhost:8765

模型密钥：环境变量 DEEPSEEK_API_KEY，或在仓库根目录建 .env 文件写一行
DEEPSEEK_API_KEY=...（.env 已在 .gitignore 里，不会被提交）。

提示词、判定规则与实验完全相同（src/chain.py、src/receipt.py、src/invoice.py）。
每次真实识别的模型原始输出都存进 app/cache/。现场网络或接口出问题时切到「回放」，
展示的是之前那次真实识别的结果，页面上标明识别时间——不是编造的数据。
"""
import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)          # 由 app/build_exe.py 打包成 exe 时为真
if FROZEN:
    APP = Path(sys._MEIPASS) / "app"            # 页面、示例、打包时的回放记录：exe 内只读
    ROOT = Path(sys.executable).resolve().parent  # exe 所在目录：.env 与新的回放记录放这里
else:
    APP = Path(__file__).resolve().parent
    ROOT = APP.parent
sys.path.insert(0, str(APP.parent / "src"))
import chain  # noqa: E402  只用到提示词常量；chain 在模块顶层不依赖 langchain
import invoice  # noqa: E402
import receipt  # noqa: E402
from money import fmt  # noqa: E402

CACHE = Path(os.environ.get("RECEIPT_CACHE_DIR", ROOT / "cache" if FROZEN else APP / "cache"))
SEED = APP / "cache" if FROZEN else CACHE       # exe 里只读的回放记录；写入一律进 CACHE
EXAMPLES = APP / "examples"
API_URL = os.environ.get("RECEIPT_API_URL", "https://api.deepseek.com/chat/completions")
PORT = int(os.environ.get("RECEIPT_PORT", 8765))


def _unescape(template):
    """chain 里的提示词是 LangChain 模板，花括号写成了 {{ }}；直接调接口时还原。"""
    return template.replace("{{", "{").replace("}}", "}")


DOC_TYPES = {
    "hk": {"label": "港式小票", "system": _unescape(chain.SYSTEM_PROMPT),
           "instruction": chain.INSTRUCTION, "locale": receipt.HK},
    "idr": {"label": "印尼小票", "system": _unescape(chain.SYSTEM_PROMPT),
            "instruction": chain.INSTRUCTION, "locale": receipt.IDR},
    "invoice": {"label": "商业发票", "system": _unescape(chain.INVOICE_PROMPT),
                "instruction": chain.INVOICE_INSTRUCTION},
}


def api_key():
    """环境变量优先；否则读 .env（源码运行：仓库根目录；exe：exe 所在目录或上一级，如 dist/ 的上一级）。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    for env in [ROOT / ".env"] + ([ROOT.parent / ".env"] if FROZEN else []):
        if key or not env.exists():
            continue
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key or None


def parse_json(text):
    """模型回复里取出 JSON 对象：容忍 ```json 围栏与前后多余文字。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复里没有 JSON 对象")
    return json.loads(text[start:end + 1])


def call_model(system, instruction, data_url, key, timeout=180):
    """一次识别：与实验相同的消息结构（system 提示词 + 文字指令 + 图像），温度 0。"""
    body = {"model": chain.MODEL, "temperature": 0, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": [{"type": "text", "text": instruction},
                                     {"type": "image_url", "image_url": {"url": data_url}}]}]}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        reply = json.loads(resp.read().decode("utf-8"))
    return parse_json(reply["choices"][0]["message"]["content"])


def cache_key(data_url, doc_type):
    return hashlib.sha256((doc_type + "|" + data_url).encode("utf-8")).hexdigest()[:20]


def cached(key):
    """回放记录的路径：先找新写入的，再找 exe 里打包的；都没有返回 None。"""
    for d in (CACHE, SEED):
        if (d / f"{key}.json").exists():
            return d / f"{key}.json"
    return None


def read(data_url, doc_type, reads, mode, model_call=call_model):
    """返回 (原始读数列表, 来源说明)。mode 为 live 时调用模型并写缓存，replay 时读缓存。"""
    path = CACHE / f"{cache_key(data_url, doc_type)}.json"
    if mode == "replay":
        path = cached(cache_key(data_url, doc_type))
        if path is None:
            raise LookupError("这张图还没有真实识别过的记录，无法回放。请先用「实时识别」跑一次。")
        saved = json.loads(path.read_text(encoding="utf-8"))
        return saved["payloads"], {"source": "replay", "at": saved["at"], "model": saved["model"]}

    key = api_key()
    if not key:
        raise PermissionError("没有找到 DEEPSEEK_API_KEY。设置环境变量，或在仓库根目录的 .env 里写一行。")
    spec = DOC_TYPES[doc_type]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=reads) as pool:
        futures = [pool.submit(model_call, spec["system"], spec["instruction"], data_url, key)
                   for _ in range(reads)]
        payloads = []
        for f in futures:
            try:
                payloads.append(f.result())
            except Exception as e:                       # 单次失败不影响其余几次
                payloads.append({"_error": f"{type(e).__name__}: {e}"})
    if all("_error" in p for p in payloads):
        raise ConnectionError("模型调用全部失败：" + payloads[0]["_error"])
    CACHE.mkdir(parents=True, exist_ok=True)
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps({"at": at, "model": chain.MODEL, "doc_type": doc_type,
                                "payloads": payloads}, ensure_ascii=False, indent=1), encoding="utf-8")
    return payloads, {"source": "live", "at": at, "model": chain.MODEL,
                      "seconds": round(time.time() - t0, 1)}


def _rows(explained):
    out = []
    for r in explained:
        out.append({"name": r["name"], "rule": r["rule"], "calc": r["calc"], "printed": r["printed"],
                    "gap": None if r["gap"] is None else fmt(r["gap"], signed=True),
                    "pass": r["pass"], "missing": r["missing"]})
    return out


def analyze(payloads, doc_type):
    """与实验同一套判定：各次单独判定 -> 多数；再挑一次与共识一致的读数展开成算式。"""
    spec = DOC_TYPES[doc_type]
    if doc_type == "invoice":
        recs = [invoice.parse_reading(p) if "_error" not in p else None for p in payloads]
        valid = [r for r in recs if r]
        m = invoice.merge(valid)
        per = [invoice.verdict(r)[0] if r else "识别失败" for r in recs]
        names = invoice.NAMES
    else:
        loc = spec["locale"]
        recs = [receipt.parse_reading(p) if "_error" not in p else None for p in payloads]
        valid = [r for r in recs if r]
        m = receipt.merge(valid, locale=loc)
        per = [receipt.verdict(r, locale=loc)[0] if r else "识别失败" for r in recs]
        names = receipt.NAMES
    if m is None:
        return {"verdict": "识别失败", "per_read": per, "checks": [], "ledger": [], "items": []}

    rep = next((r for r, v in zip(recs, per) if r and v == m["verdict"]), valid[0])
    if doc_type == "invoice":
        checks = _rows(invoice.explain(rep))
        ledger = [("商品行数", str(len(rep["items"]))), ("不含税合计", fmt(m["total_net"])),
                  ("税额", fmt(m["total_vat"])), ("含税合计", fmt(m["total_gross"]))]
        items = [[fmt(it["qty"]), fmt(it["price"]), fmt(it["net"]),
                  (fmt(it["rate"]) + "%") if it["rate"] is not None else "—", fmt(it["gross"])]
                 for it in rep["items"]]
        labels_ok = None
    else:
        checks = _rows(receipt.explain(rep, locale=spec["locale"]))
        ledger = [("商品行合计", fmt(m["items_total"])), ("折扣合计", fmt(m["discount_total"])),
                  ("小计", fmt(m["subtotal"])), ("税", fmt(m["tax"])), ("服务费", fmt(m["service"])),
                  ("应付", fmt(m["total"])), ("付款 − 找零", fmt(m["paid"]))]
        items = []
        labels_ok = m["labels_ok"] if doc_type == "hk" else None
    return {"verdict": m["verdict"], "failed": [names[n] for n in m["failed"]],
            "reads_flagged": m["reads_flagged"], "reads": m["reads"], "per_read": per,
            "checks": checks, "ledger": ledger, "items": items, "labels_ok": labels_ok}


class Presence:
    """还有几个页面开着。每个页面打开期间一直连着 /api/alive；窗口关掉、Edge 退出时操作系统会断开
    这条连接——不依赖页面临走前「告别」（Edge 整体退出时告别请求会被丢掉），也不受最小化时计时器降频影响。
    exe（app/launcher.py）据此在所有窗口都关掉后退出；python app/server.py 运行时不用它。"""

    GRACE = 5        # 最后一个页面断开后再等几秒：刷新页面是先断再连
    NEVER = 120      # 启动后这么久都没有页面连上（窗口没打开）也退出

    def __init__(self, now=time.time):
        self.now, self.start = now, now()
        self.lock, self.open, self.ever, self.last = threading.Lock(), 0, False, self.start

    def connect(self):
        with self.lock:
            self.open += 1
            self.ever = True

    def disconnect(self):
        with self.lock:
            self.open -= 1
            self.last = self.now()

    def should_exit(self):
        t = self.now()
        with self.lock:
            if self.open > 0:
                return False
            return t - self.last > self.GRACE if self.ever else t - self.start > self.NEVER


PRESENCE = Presence()


class Handler(BaseHTTPRequestHandler):
    model_call = staticmethod(call_model)     # 测试时替换

    def _alive(self):
        """页面开着就一直连着（EventSource）；每 2 秒写一行注释，写不出去 = 页面已关。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        PRESENCE.connect()
        try:
            while True:
                self.wfile.write(b": alive\n\n")
                self.wfile.flush()
                time.sleep(2)
        except OSError:
            pass
        finally:
            PRESENCE.disconnect()

    def log_message(self, fmt_, *args):
        sys.stderr.write("  " + (fmt_ % args) + "\n")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, (APP / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/alive":
            return self._alive()
        if path == "/favicon.ico" and (APP / "icon.ico").exists():
            return self._send(200, (APP / "icon.ico").read_bytes(), "image/x-icon")
        if path == "/api/status":
            n = len({p.name for d in {CACHE, SEED} if d.exists() for p in d.glob("*.json")})
            return self._send(200, {"has_key": api_key() is not None, "model": chain.MODEL,
                                    "cached": n, "doc_types": {k: v["label"] for k, v in DOC_TYPES.items()}})
        if path == "/api/examples":
            manifest = json.loads((EXAMPLES / "examples.json").read_text(encoding="utf-8"))
            for ex in manifest["examples"]:
                url = _data_url(EXAMPLES / ex["file"])
                ex["cached"] = cached(cache_key(url, ex["type"])) is not None
            return self._send(200, manifest)
        if path.startswith("/examples/"):
            f = (EXAMPLES / path[len("/examples/"):]).resolve()
            if f.parent == EXAMPLES.resolve() and f.suffix.lower() in (".jpg", ".jpeg", ".png") and f.exists():
                return self._send(200, f.read_bytes(), "image/png" if f.suffix == ".png" else "image/jpeg")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/check":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            doc_type, mode = body["doc_type"], body.get("mode", "live")
            reads = max(1, min(5, int(body.get("reads", 3))))
            if doc_type not in DOC_TYPES:
                raise ValueError(f"未知单据类型 {doc_type}")
            data_url = body["image"]
            if body.get("example"):                   # 示例图用服务端的原文件，保证与缓存一致
                data_url = _data_url(EXAMPLES / Path(body["example"]).name)
            payloads, source = read(data_url, doc_type, reads, mode, self.model_call)
            result = analyze(payloads, doc_type)
            result.update(source)
            return self._send(200, result)
        except (LookupError, PermissionError, ConnectionError, ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})


def _data_url(path):
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 允许第二个进程绑同一端口，两个服务同时在线、请求随机落到其中一个
    allow_reuse_address = os.name != "nt"


def make_server(port=PORT):
    """绑定端口；已被占用（多半是已经开着一个演示）时抛 OSError。"""
    return Server(("127.0.0.1", port), Handler)


def main():
    try:
        server = make_server()
    except OSError:
        print(f"端口 {PORT} 已被占用：多半已经开着一个演示服务，直接打开 http://localhost:{PORT} 即可；"
              "要重启就先关掉那个窗口。")
        return
    key = "已找到" if api_key() else "未找到（只能回放）"
    print(f"票据核验演示：http://localhost:{PORT}    模型 {chain.MODEL}    密钥 {key}")
    print("保持此窗口开着；按 Ctrl+C 停止服务（在终端里复制文字也会触发 Ctrl+C）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("服务已停止（收到 Ctrl+C）")


if __name__ == "__main__":
    main()
