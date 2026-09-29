"""双击启动的演示程序（app/build_exe.py 把它打包成 exe）：

起本机服务 -> 用 Edge 的应用模式开一个独立窗口（没有地址栏，像普通软件；没有 Edge 时用默认浏览器）
-> 所有页面都关掉后服务自动退出（页面定时报到、关闭时告别，见 server.Presence）。

不跟踪 Edge 进程：Edge 常把新窗口交给已在运行的进程，我们启动的那个进程随即退出，
据此判断「窗口已关」会在窗口还开着时把服务停掉。

源码也能直接跑：python app/launcher.py
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

TITLE = "票据不说谎"
URL = f"http://localhost:{server.PORT}/"


def message(text):
    """exe 没有控制台，出错时用系统对话框说明。"""
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 | 0x10000 | 0x40000)  # 错误图标、前台、置顶
    else:
        print(text)


def find_edge():
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(base):
            p = Path(os.environ[base]) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.exists():
                return p
    return None


def open_window():
    """单独的配置目录：与平时用的 Edge 分开（不带扩展、不混进浏览记录）。"""
    edge = find_edge()
    if edge is None:
        webbrowser.open(URL)
        return
    profile = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "receipt-integrity" / "edge"
    subprocess.Popen([str(edge), f"--app={URL}", f"--user-data-dir={profile}",
                      "--no-first-run", "--no-default-browser-check", "--start-maximized"])


def main():
    try:
        httpd = server.make_server()
    except OSError:                                       # 已经开着一个：只再开一个窗口
        open_window()
        return
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "启动", URL,
          "密钥", "已找到" if server.api_key() else "未找到（只能回放）", flush=True)
    open_window()
    while not server.PRESENCE.should_exit():
        time.sleep(1)
    httpd.shutdown()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "停止：所有窗口都已关闭", flush=True)


if __name__ == "__main__":
    if sys.stdout is None:                                # 无控制台的 exe：日志写到 exe 旁边
        sys.stdout = sys.stderr = open(server.ROOT / "演示日志.txt", "a", encoding="utf-8", buffering=1)
    try:
        main()
    except Exception:
        traceback.print_exc()
        message("启动失败：\n\n" + traceback.format_exc(limit=3))
