"""把演示打包成一个 exe：

    python app/build_exe.py          # -> dist/票据不说谎.exe

PyInstaller 装在 build/venv 里，不动你自己的 Python 环境。exe 内含页面、示例图，以及打包那一刻
app/cache/ 与 dist/cache/ 里的全部回放记录（所以先用实时识别把示例跑齐再打包）。exe 不含密钥：
它从自己旁边或上一级目录的 .env、或环境变量 DEEPSEEK_API_KEY 读取。
"""
import os
import subprocess
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
BUILD = ROOT / "build"
VENV = BUILD / "venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
NAME = "票据不说谎"
SEP = ";" if os.name == "nt" else ":"


def main():
    if not PY.exists():
        print("创建打包用的虚拟环境", VENV)
        venv.create(VENV, with_pip=True)
    subprocess.run([str(PY), "-m", "pip", "install", "-q", "pyinstaller"], check=True)

    # 回放记录：源码运行写在 app/cache/，exe 运行写在 dist/cache/；同名取较新的
    newest = {}
    for f in list((APP / "cache").glob("*.json")) + list((ROOT / "dist" / "cache").glob("*.json")):
        if f.name not in newest or f.stat().st_mtime > newest[f.name].stat().st_mtime:
            newest[f.name] = f
    cache = sorted(newest.values())
    data = [(APP / "index.html", "app"), (APP / "icon.ico", "app"), (APP / "examples", "app/examples")]
    data += [(f, "app/cache") for f in cache]
    args = [str(PY), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", NAME, "--icon", str(APP / "icon.ico"),
            "--paths", str(ROOT / "src"), "--paths", str(APP),
            "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD)]
    for src, dest in data:
        args += ["--add-data", f"{src}{SEP}{dest}"]
    subprocess.run(args + [str(APP / "launcher.py")], check=True)
    print(f"\n完成：{ROOT / 'dist' / (NAME + '.exe')}（内含 {len(cache)} 条回放记录）")


if __name__ == "__main__":
    main()
