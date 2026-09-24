"""生成 Colab 实验 notebook。src/ 改动后重跑本脚本，notebook 自动同步。"""
import json
import pathlib

ROOT = pathlib.Path(__file__).parent
SRC = ROOT / "src"


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.splitlines(True)}


def code(s):
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": s.splitlines(True)}


def writefile(name):
    return code(f"%%writefile /content/ghb/src/{name}\n"
                + (SRC / name).read_text(encoding="utf-8"))


cells = [
    md("""# 票据可信度核验 · CORD 公开数据集实验

工行杯 · 金融安全服务方向

三个实验：

| | 测什么 | 指标 |
|---|---|---|
| E1 | 公开票据本身有多少是自洽的 | 误报基线 |
| E2 | 模型读出的字段与标注是否一致 | 各字段精确匹配率 |
| E3 | 被篡改的票据能否被发现 | TPR / FPR |

**运行环境**：Colab CPU 即可。模型在服务端，GPU 用不上。
需要 Colab Secrets 里有 `DEEPSEEK_API_KEY`。
"""),

    md("## 1. 装依赖与配置 API"),
    code('''!pip install -q datasets langchain-core langchain-deepseek

import os
from pathlib import Path
try:
    from google.colab import userdata
    os.environ["DEEPSEEK_API_KEY"] = userdata.get("DEEPSEEK_API_KEY")
except Exception as e:
    raise SystemExit(f"请先在左栏钥匙图标里加 DEEPSEEK_API_KEY：{e}")

Path("/content/ghb/src").mkdir(parents=True, exist_ok=True)
print("API key 已就绪")'''),

    md("## 2. 写入源码\n\n与本地 `ghb/src/` 保持一致，改动后重跑 `build_notebooks.py` 即可同步。"),
    writefile("money.py"),
    writefile("receipt.py"),
    writefile("chain.py"),
    writefile("cord.py"),
    writefile("tamper.py"),
    code('''import sys
sys.path.insert(0, "/content/ghb/src")
for m in ("money", "receipt", "chain", "cord", "tamper"):
    sys.modules.pop(m, None)
import money, receipt, chain, cord, tamper
print("源码已加载")'''),

    md("""## 3. 载入 CORD 并做 E1

CORD-v2 的 test 划分共 100 张。先只看标注本身：两条算术校验各自适用多少张、
其中多少张自洽。**不自洽的那部分就是误报基线**——后面报 FPR 时要拿它作参照。"""),
    code('''import json
from datasets import load_dataset

ds = load_dataset("naver-clova-ix/cord-v2", split="test")
samples = []
for row in ds:
    gt = json.loads(row["ground_truth"])
    rec = cord.parse_gt(gt["gt_parse"])
    samples.append({"image": row["image"], "gt": gt, "rec": rec,
                    "c1": cord.check_subtotal_explains_total(rec),
                    "c2": cord.check_items_explain_subtotal(rec)})

both = [s for s in samples if s["c1"] is not None and s["c2"] is not None]
clean = [s for s in both if s["c1"] == 0 and s["c2"] == 0]
print(f"总计 {len(samples)} 张")
print(f"  校验一适用 {sum(s['c1'] is not None for s in samples)} 张，"
      f"自洽 {sum(s['c1'] == 0 for s in samples if s['c1'] is not None)} 张")
print(f"  校验二适用 {sum(s['c2'] is not None for s in samples)} 张，"
      f"自洽 {sum(s['c2'] == 0 for s in samples if s['c2'] is not None)} 张")
print(f"  两条都适用 {len(both)} 张，都自洽 {len(clean)} 张  <- 可用于篡改实验")
print(f"  标注层面就不自洽：{len(both) - len(clean)} 张（{(len(both)-len(clean))/max(len(both),1):.0%}）")'''),

    md("""## 4. E2 字段抽取准确率

模型只做转写，金额在 Python 侧汇总，再与 CORD 标注逐字段比对。
`N_EVAL` 控制规模——每张要发 `READS` 次请求，先小后大。"""),
    code('''N_EVAL = 20      # 先跑 20 张确认流程，再放大
READS = 3

from decimal import Decimal
import time

pool = clean[:N_EVAL]
urls = [chain.pil_data_url(s["image"]) for s in pool]

t0 = time.time()
ch = chain.build_chain()
readings = chain.read_many(ch, urls, reads=READS)
print(f"{len(pool)} 张 x {READS} 次 = {len(pool)*READS} 次请求，耗时 {time.time()-t0:.0f}s")

fields = ["subtotal", "total_paid", "items_total"]
hit = {f: 0 for f in fields}
n_ok = 0
rows = []
for i, s in enumerate(pool):
    recs = [receipt.parse_reading(p, hint=".") for p in readings[i]]
    m = receipt.merge([r for r in recs if r], locale=receipt.IDR)
    if m is None:
        rows.append((i, "识别失败", None, None)); continue
    n_ok += 1
    want = {"subtotal": s["rec"]["subtotal"], "total_paid": s["rec"]["total"],
            "items_total": s["rec"]["items_total"]}
    for f in fields:
        if want[f] is not None and m[f] == want[f]:
            hit[f] += 1
    rows.append((i, m["subtotal"], m["total_paid"], want["subtotal"]))

print(f"\\n有效识别 {n_ok}/{len(pool)} 张")
for f in fields:
    print(f"  {f:12s} 精确匹配 {hit[f]:3d}/{n_ok}  ({hit[f]/max(n_ok,1):.0%})")'''),

    md("""## 5. E3 篡改检出

从两条校验都自洽的票据里取一批，随机一半做图像篡改（整块重绘，按框高匹配字号，
取该处背景与墨色），另一半保持原样。两组混在一起送检，统计：

- **TPR**：被改过的票里，判为存疑的比例
- **FPR**：没改过的票里，误判为存疑的比例

只报检出率是不诚实的，两个都要。"""),
    code('''import random
N_TAMPER = 20     # 一半改一半不改
rng = random.Random(42)

pool = clean[:N_TAMPER]
plan = []
for i, s in enumerate(pool):
    do_tamper = (i % 2 == 0)
    img, info = s["image"], None
    if do_tamper:
        targets = cord.money_fields(s["gt"]["gt_parse"])
        rng.shuffle(targets)
        for field, old in targets:
            img2, info = tamper.tamper_cord(s["image"], s["gt"]["valid_line"],
                                            field, old, seed=rng.randrange(10**6))
            if info:
                img = img2
                break
    plan.append({"idx": i, "tampered": bool(info), "image": img, "info": info})

made = sum(p["tampered"] for p in plan)
print(f"{len(plan)} 张里成功篡改 {made} 张，未改 {len(plan)-made} 张")
for p in plan[:6]:
    if p["info"]:
        print(f"  #{p['idx']}: {p['info']['field']}  {p['info']['old']} -> {p['info']['new']}")'''),

    code('''urls = [chain.pil_data_url(p["image"]) for p in plan]
t0 = time.time()
readings = chain.read_many(ch, urls, reads=READS)
print(f"{len(plan)} 张 x {READS} 次，耗时 {time.time()-t0:.0f}s\\n")

tp = fp = tn = fn = 0
detail = []
for i, p in enumerate(plan):
    recs = [receipt.parse_reading(x, hint=".") for x in readings[i]]
    m = receipt.merge([r for r in recs if r], locale=receipt.IDR)
    if m is None:
        detail.append((p["idx"], p["tampered"], "识别失败", [])); continue
    v, failed, c = receipt.verdict(m, locale=receipt.IDR)
    flagged = (v == "存疑")
    if p["tampered"]:
        tp += flagged; fn += not flagged
    else:
        fp += flagged; tn += not flagged
    detail.append((p["idx"], p["tampered"], v, failed))

n_t, n_c = tp + fn, fp + tn
print(f"被篡改 {n_t} 张：检出 {tp}，漏检 {fn}   TPR = {tp/max(n_t,1):.0%}")
print(f"未篡改 {n_c} 张：误报 {fp}，正确 {tn}   FPR = {fp/max(n_c,1):.0%}")
print("\\n逐张：")
for idx, t, v, failed in detail:
    print(f"  #{idx:3d}  {'已篡改' if t else '未篡改'}  判定={v:4s}  失败项={failed or '无'}")'''),

    md("""## 6. 存结果

把三个实验的数字落盘，PPT 直接引用，不要凭记忆重打。"""),
    code('''import csv, json
out = Path("/content/ghb/results")
out.mkdir(exist_ok=True)

summary = {
    "E1_总数": len(samples), "E1_两条都适用": len(both), "E1_都自洽": len(clean),
    "E2_评测张数": len(clean[:N_EVAL]), "E2_有效识别": n_ok,
    "E2_字段命中": {f: hit[f] for f in fields},
    "E3_篡改张数": n_t, "E3_TPR": round(tp / max(n_t, 1), 4),
    "E3_未篡改张数": n_c, "E3_FPR": round(fp / max(n_c, 1), 4),
    "READS": READS,
}
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
with (out / "e3_detail.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["idx", "tampered", "verdict", "failed"])
    for r in detail:
        w.writerow([r[0], r[1], r[2], "|".join(r[3])])
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("\\n已存至 /content/ghb/results/")'''),

    md("""## 怎么用这些数字

- E1 的"标注层面就不自洽"比例，是误报的**下界**：这些票据连标注都对不上，
  系统判它存疑并非纯粹的错误
- E2 说明转写本身够不够准；若准确率低，E3 的 FPR 会被抬高
- E3 的 TPR / FPR 一起报。若 FPR 明显高于 E1 的基线，差值来自识别误差而非方法缺陷

**局限**：查的是票面算术是否自洽，不是像素取证。造假者若把商品行、小计、
付款行一并改成彼此吻合的数字，三条校验都会通过。本方法提高的是伪造成本。
"""),
]

def dump(cells, name):
    nb = {"cells": cells,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                      "name": "python3"},
                       "language_info": {"name": "python"},
                       "colab": {"provenance": [], "toc_visible": True}},
          "nbformat": 4, "nbformat_minor": 0}
    out = ROOT / "notebooks" / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已生成:", out, "-", len(cells), "格")


dump(cells, "01_cord_eval.ipynb")
