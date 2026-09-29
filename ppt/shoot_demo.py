"""用无头 Edge 截演示界面的真实截图，供 PPT 使用。回放模式：读 app/cache/ 里的真实识别记录，不调用模型。

    python app/server.py            # 先在另一个窗口起服务（端口不是 8765 时设 RECEIPT_PORT）
    python ppt/shoot_demo.py        # -> ppt/assets/demo_*.png，1600x1000 视口、2 倍像素

依赖 websocket-client（Anaconda 自带）。
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

HERE = Path(__file__).resolve().parent
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe"
PORT = int(os.environ.get("RECEIPT_PORT", 8765))
DEBUG_PORT = 9333
W, H, SCALE = 1600, 1000, 2
SHOTS = [("demo_cord36_tax.png", "cord_val36_tax_price.jpg")]
READY = ("!!document.querySelector('.verdict') && [...document.images].filter(i => i.getAttribute('src'))"
         ".every(i => i.complete && i.naturalWidth > 0)")          # 放大层里那张空 img 不算


def main():
    profile = tempfile.mkdtemp(prefix="edge-shot-")
    edge = subprocess.Popen([str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                             f"--user-data-dir={profile}", f"--remote-debugging-port={DEBUG_PORT}", "about:blank"])
    try:
        for _ in range(50):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read())
                break
            except OSError:
                time.sleep(0.2)
        page = next(t for t in targets if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        seq = [0]

        def cdp(method, **params):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq[0]:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        cdp("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=SCALE, mobile=False)
        for out, example in SHOTS:
            cdp("Page.navigate", url=f"http://localhost:{PORT}/?example={example}&mode=replay&auto=1")
            for _ in range(100):                       # 等判定出来、图片全部加载完
                if cdp("Runtime.evaluate", expression=READY, returnByValue=True)["result"].get("value"):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(f"{example}：结果没有出现。服务开着吗？这张有回放记录吗？")
            time.sleep(0.5)
            data = cdp("Page.captureScreenshot", format="png")["data"]
            (HERE / "assets" / out).write_bytes(base64.b64decode(data))
            print("写入", HERE / "assets" / out)
        ws.close()
    finally:
        edge.terminate()
        edge.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
