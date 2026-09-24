"""生成 E4：港式票据篡改的定性演示 notebook。

与 build_notebooks.py 分开，是因为 E4 用的是字形复制式篡改，
只在港式票据上成立，跟 CORD 的统计实验没有共用逻辑。
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).parent
SRC = ROOT / "src"
REPO = "https://raw.githubusercontent.com/baimingyang98/FTEC5660/main/public_test"


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.splitlines(True)}


def code(s):
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": s.splitlines(True)}


def writefile(name):
    return code(f"%%writefile /content/ghb/src/{name}\n"
                + (SRC / name).read_text(encoding="utf-8"))


cells = [
    md("""# E4 港式票据 · 篡改的定性演示

工行杯 · 金融安全服务方向

E3 在 CORD 上给统计量，E4 在港式票据上给**画面**。两件 E3 做不到的事：

1. **校验三**（折扣标签自带金额）只在港式票据上成立——CORD 的印尼票据没有这种冗余
2. **字形复制式篡改**视觉上几乎看不出来，比整块重绘更能说明"肉眼靠不住"

票据从公开仓库下载，篡改样本在本 notebook 里现场生成，不需要上传任何文件。
"""),

    md("## 1. 装依赖、配 API、取数据"),
    code('''!pip install -q langchain-core langchain-deepseek

import os
import urllib.request
from pathlib import Path

try:
    from google.colab import userdata
    os.environ["DEEPSEEK_API_KEY"] = userdata.get("DEEPSEEK_API_KEY")
except Exception as e:
    raise SystemExit(f"请先在左栏钥匙图标里加 DEEPSEEK_API_KEY：{e}")

REPO = "''' + REPO + '''"
WORK = Path("/content/ghb")
(WORK / "src").mkdir(parents=True, exist_ok=True)
(WORK / "hk").mkdir(exist_ok=True)

for i in range(1, 8):
    urllib.request.urlretrieve(f"{REPO}/receipt{i}.jpg", WORK / "hk" / f"receipt{i}.jpg")
urllib.request.urlretrieve(f"{REPO}/ground_truth.json", WORK / "hk" / "ground_truth.json")
print("已下载:", sorted(p.name for p in (WORK / "hk").iterdir()))'''),

    md("## 2. 写入源码"),
    writefile("money.py"),
    writefile("receipt.py"),
    writefile("chain.py"),
    writefile("tamper.py"),
    code('''import sys
sys.path.insert(0, "/content/ghb/src")
for m in ("money", "receipt", "chain", "tamper"):
    sys.modules.pop(m, None)
import money, receipt, chain, tamper
print("源码已加载")'''),

    md("""## 3. 生成篡改样本（字形复制）

从同一张票上取同字体的数字，抽出墨迹覆盖度，擦掉原字后按目标处的背景色与墨色
重新合成。字体、墨色、噪点全都一致，不是画图软件重绘。

坐标来自对 receipt2.jpg 金额栏的自动字符切分：

| 位置 | 坐标 | 内容 |
|---|---|---|
| `OCTOPUS_3` | (655, 1216, 664, 1236) | `$316.10` 的首位 3 |
| `FARM_1` | (674, 823, 681, 843) | `$91.80` 的 1 |
| `FARM_8` | (695, 823, 704, 843) | `$91.80` 的 8，作字形来源 |
| `YAKULT_8` | (674, 869, 682, 889) | `$28.90` 的 8，作字形来源 |

两个样本各自只被一条校验抓到，正好论证三条校验的分工。"""),
    code('''import numpy as np
from PIL import Image

OCTOPUS_3 = (655, 1216, 664, 1236)
FARM_1    = (674, 823, 681, 843)
FARM_8    = (695, 823, 704, 843)
YAKULT_8  = (674, 869, 682, 889)

HK = Path("/content/ghb/hk")


def make(out_name, edits, note):
    arr = np.array(Image.open(HK / "receipt2.jpg").convert("RGB")).astype(float)
    rng = np.random.default_rng(0)
    for tgt, src in edits:
        tamper.copy_glyph(arr, tgt, src, rng)
    Image.fromarray(arr.astype(np.uint8)).save(HK / out_name, quality=92, subsampling=0)
    print(f"  {out_name:26s} {note}")


print("生成篡改样本：")
# 付款行虚增：小计与舍入未动，付款行不再等于 小计+舍入
make("receipt2_T1_payment.jpg", [(OCTOPUS_3, FARM_8)],
     "T1 付款行 316.10 -> 816.10（虚增 500）")
# 商品行虚增：付款行照样闭合，只有商品行解释不了小计
make("receipt2_T2_item.jpg", [(FARM_1, YAKULT_8)],
     "T2 商品行 91.80 -> 98.80（虚增 7）")'''),

    md("## 4. 看一眼改得像不像"),
    code('''from IPython.display import display

files = ["receipt2.jpg", "receipt2_T1_payment.jpg", "receipt2_T2_item.jpg"]
for box, title in [((635, 1210, 730, 1242), "付款行   原图 / T1 / T2"),
                   ((640, 818, 725, 848), "商品行   原图 / T1 / T2")]:
    S = 6
    w, h = (box[2] - box[0]) * S, (box[3] - box[1]) * S
    sheet = Image.new("RGB", (w, h * 3), "white")
    for i, f in enumerate(files):
        sheet.paste(Image.open(HK / f).crop(box).resize((w, h), Image.LANCZOS), (0, i * h))
    print(title)
    display(sheet)

print("这是 6 倍放大。正常尺寸下看不出改动——肉眼不是可靠的核验手段。")'''),

    md("""## 5. 送检

三张一起跑：未篡改的对照组、T1、T2。用 `receipt.HK` 地区参数
（港式 SUBTOTAL 是折扣**后**金额，与 CORD 相反）。"""),
    code('''import time

READS = 5
targets = [("对照组 未篡改", "receipt2.jpg", "两条校验都通过 -> 可信"),
           ("T1 付款行 316.10->816.10", "receipt2_T1_payment.jpg",
            "只有校验一失败，差 +500.00"),
           ("T2 商品行 91.80->98.80", "receipt2_T2_item.jpg",
            "只有校验二失败，差 +7.00")]

ch = chain.build_chain()
urls = [chain.image_data_url(HK / f) for _, f, _ in targets]
t0 = time.time()
readings = chain.read_many(ch, urls, reads=READS)
print(f"{len(targets)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")
print()

for i, (name, _, expect) in enumerate(targets):
    recs = [r for r in (receipt.parse_reading(p) for p in readings[i]) if r]
    m = receipt.merge(recs, locale=receipt.HK)
    print("=" * 70)
    print(name)
    if m is None:
        print("  全部识别失败")
        continue
    v, failed, c = receipt.verdict(m, locale=receipt.HK)
    print(f"  有效识别 {m['reads']}/{READS}   参与共识 {m['pool']} 次   "
          f"标签印证 {m['labels_ok']} 条")
    print(f"  小计={m['subtotal']}  实付={m['total_paid']}  "
          f"折扣={m['discount_total']}  商品行={m['items_total']}")
    print(f"  校验一 付款行闭合      差 {c['c1_gap']:+}   "
          f"{'通过' if c['c1_pass'] else '不通过'}")
    print(f"  校验二 商品行解释小计  差 {c['c2_gap']:+}   "
          f"{'通过' if c['c2_pass'] else '不通过'}")
    print(f"  判定：{v}" + (f"（失败项 {failed}）" if failed else ""))
    print(f"  预期：{expect}")'''),

    md("""## 6. 未篡改的 7 张 —— 港式票据上的误报基线

CORD 上标注本身就有约两成不自洽，港式这 7 张是逐张人工核对过的，
理论上误报应为 0。跑出来若不是 0，差额就来自识别误差，而非方法缺陷。"""),
    code('''urls = [chain.image_data_url(HK / f"receipt{i}.jpg") for i in range(1, 8)]
t0 = time.time()
readings = chain.read_many(ch, urls, reads=READS)
print(f"7 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")
print()

flagged = 0
for i in range(7):
    recs = [r for r in (receipt.parse_reading(p) for p in readings[i]) if r]
    m = receipt.merge(recs, locale=receipt.HK)
    if m is None:
        print(f"  receipt{i + 1}.jpg  识别失败")
        continue
    v, failed, c = receipt.verdict(m, locale=receipt.HK)
    flagged += (v == "存疑")
    print(f"  receipt{i + 1}.jpg  判定={v}  c1差={c['c1_gap']:+8}  "
          f"c2差={c['c2_gap']:+8}  标签印证={m['labels_ok']}  "
          f"有效识别={m['reads']}/{READS}")
print()
print(f"误报 {flagged}/7   FPR = {flagged / 7:.0%}")'''),

    md("""## 7. 这些结果怎么进 PPT

- 第 4 格的两张对比图 → "篡改在视觉上不明显"那一页的配图
- 第 5 格的三行判定 → 三条校验分工的表格；T1 与 T2 各自只被一条抓到是关键
- 第 6 格的 FPR → 与 CORD 的误报基线对照，说明差额来自哪里

**局限**（这句要写进 PPT，别等评委问）：查的是票面算术是否自洽，不是像素取证。
造假者若把商品行、小计、付款行一并改成彼此吻合的数字，三条校验都会通过。
本方法提高的是伪造成本——要同时骗过三条相互独立的约束，远难于改动单处。
"""),
]

nb = {"cells": cells,
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                  "name": "python3"},
                   "language_info": {"name": "python"},
                   "colab": {"provenance": [], "toc_visible": True}},
      "nbformat": 4, "nbformat_minor": 0}

out = ROOT / "notebooks" / "02_hk_demo.ipynb"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("已生成:", out, "-", len(cells), "格")
