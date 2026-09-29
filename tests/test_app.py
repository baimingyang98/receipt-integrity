"""离线测试：演示服务。模型调用换成假的，不发任何网络请求。"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp()
os.environ["RECEIPT_CACHE_DIR"] = TMP            # 缓存写到临时目录，不碰 app/cache
os.environ["DEEPSEEK_API_KEY"] = "test-only-not-a-key"
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"  {'通过' if cond else '失败'}  {name}{('  ' + detail) if detail else ''}")


# CORD 36（validation）被改后的一次如实读数：税额 1,427
CORD36 = {"items": ["17,272"], "subtotal": "17,272", "tax": "1,427", "total": "18,999",
          "payments": [{"label": "CASH", "amount": "18,999"}], "change": 0, "discounts": []}
INVOICE = {"items": [{"qty": "5,00", "net_price": "12,00", "net_worth": "60,00", "vat": "10%", "gross_worth": "66,00"},
                     {"qty": "4,00", "net_price": "28,08", "net_worth": "117,32", "vat": "10%", "gross_worth": "123,55"}],
           "summary_rows": [{"vat": "10%", "net_worth": "172,32", "vat_amount": "17,23", "gross_worth": "189,55"}],
           "total": {"net_worth": "$ 172,32", "vat_amount": "$ 17,23", "gross_worth": "$ 189,55"}}

print("解析与判定")
check("模型回复带 ```json 围栏也能解析",
      server.parse_json('好的：\n```json\n{"a": 1}\n```') == {"a": 1})
r = server.analyze([CORD36] * 3, "idr")
c1 = r["checks"][0]
check("CORD 36 税额被改 -> 存疑，校验一给出算式与差额",
      r["verdict"] == "存疑" and r["failed"] == ["校验一 应付闭合"]
      and c1["calc"] == "小计 17,272 + 税 1,427 = 18,699" and c1["gap"] == "+300",
      f"{r['verdict']} {c1['calc']} 差 {c1['gap']}")
r = server.analyze([INVOICE] * 3, "invoice")
l1 = r["checks"][0]
check("发票第 2 行金额被改 -> 指到第 2 行", r["verdict"] == "存疑" and l1["calc"].startswith("第 2 行"),
      f"{l1['calc']} / {l1['printed']}")
r = server.analyze([CORD36, {"_error": "Timeout"}, CORD36], "idr")
check("单次调用失败不影响其余两次", r["reads"] == 2 and r["per_read"][1] == "识别失败", f"{r['per_read']}")

print("\n实时识别与回放")
calls = []


def fake(system, instruction, data_url, key):
    calls.append(system[:10])
    return CORD36


payloads, src = server.read("data:image/jpeg;base64,AAAA", "idr", 3, "live", fake)
check("实时识别调 3 次并写缓存", len(calls) == 3 and src["source"] == "live"
      and len(list(Path(TMP).glob("*.json"))) == 1)
payloads2, src2 = server.read("data:image/jpeg;base64,AAAA", "idr", 3, "replay", fake)
check("回放不再调用模型，返回同一批读数与识别时间",
      len(calls) == 3 and payloads2 == payloads and src2["source"] == "replay" and src2["at"] == src["at"])
try:
    server.read("data:image/jpeg;base64,BBBB", "idr", 3, "replay", fake)
    check("没识别过的图不能回放", False)
except LookupError:
    check("没识别过的图不能回放", True)
check("提示词与实验一致（去掉模板转义后）",
      server.DOC_TYPES["idr"]["system"] == server.chain.SYSTEM_PROMPT.replace("{{", "{").replace("}}", "}")
      and "{{" not in server.DOC_TYPES["invoice"]["system"])

print("\nHTTP 接口")
server.Handler.model_call = staticmethod(lambda s, i, u, k: INVOICE if "发票" in s else CORD36)
httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"
status = json.loads(urllib.request.urlopen(base + "/api/status").read())
check("/api/status", status["has_key"] and "invoice" in status["doc_types"])
ex = json.loads(urllib.request.urlopen(base + "/api/examples").read())
check("/api/examples 列出 6 张示例，图片都能取到",
      len(ex["examples"]) == 6
      and all(urllib.request.urlopen(base + "/examples/" + e["file"]).status == 200 for e in ex["examples"]))
req = urllib.request.Request(base + "/api/check", method="POST", headers={"Content-Type": "application/json"},
                             data=json.dumps({"example": "invoice_61356291_row2_net.jpg", "doc_type": "invoice",
                                              "reads": 3, "mode": "live", "image": ""}).encode())
out = json.loads(urllib.request.urlopen(req).read())
check("/api/check 返回判定、算式与共识读数",
      out["verdict"] == "存疑" and out["source"] == "live" and len(out["checks"]) == 6 and bool(out["ledger"]),
      f"{out['verdict']} {out['failed']}")
try:
    urllib.request.urlopen(base + "/examples/..%2F..%2Fsrc%2Fchain.py")
    check("示例路径不能越出 examples 目录", False)
except urllib.error.HTTPError as e:
    check("示例路径不能越出 examples 目录", e.code == 404)
sock = socket.create_connection(("127.0.0.1", httpd.server_address[1]))
sock.sendall(b"GET /api/alive HTTP/1.1\r\nHost: x\r\n\r\n")
got = b""
while b": alive" not in got:
    got += sock.recv(1024)
check("/api/alive 连着时计为一个打开的页面", server.PRESENCE.open == 1 and b"text/event-stream" in got)
sock.close()                                     # 相当于关掉窗口：连接被断开
for _ in range(40):
    if server.PRESENCE.open == 0:
        break
    time.sleep(0.25)
check("连接断开后几秒内计为已关", server.PRESENCE.open == 0)
httpd.shutdown()

print("\nexe 何时退出（server.Presence，假时钟）")
clock = [0.0]
p = server.Presence(now=lambda: clock[0])
clock[0] = 100
check("启动后页面还没连上：不退出", not p.should_exit())
clock[0] = 121
check("启动 2 分钟都没有页面连上（窗口没打开）：退出", p.should_exit())
p = server.Presence(now=lambda: clock[0])
p.connect()
clock[0] += 3600
check("页面一直开着（哪怕最小化一小时）：不退出", not p.should_exit())
p.disconnect(); p.connect()                      # 刷新：先断开再连上
clock[0] += 60
check("刷新页面：不退出", not p.should_exit())
p.connect(); p.disconnect()
check("开着两个窗口、关掉一个：不退出", not p.should_exit())
p.disconnect()
clock[0] += 3
check("最后一个窗口刚关：先等 5 秒（可能是刷新）", not p.should_exit())
clock[0] += 3
check("最后一个窗口关掉 5 秒后：退出", p.should_exit())

print(f"\n{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
