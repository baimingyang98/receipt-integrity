"""生成 04：CORD train 上的确认性评测（预先登记）。

与 01（CORD test / validation）分开，01 保持为 validation 那次运行的原样记录。
单元格代码里不写反斜杠转义，换行一律用单独的 print()。
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).parent


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.splitlines(True)}


def code(s):
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": s.splitlines(True)}


CLONE = '''import subprocess, sys
from pathlib import Path

REPO = "https://github.com/baimingyang98/receipt-integrity.git"
ROOT = Path("/content/receipt-integrity")
if ROOT.exists():
    subprocess.run(["git", "-C", str(ROOT), "pull", "-q"], check=False)
else:
    subprocess.run(["git", "clone", "-q", REPO, str(ROOT)], check=True)
sys.path.insert(0, str(ROOT / "src"))

for m in ("money", "receipt", "chain", "cord", "tamper", "baseline"):
    sys.modules.pop(m, None)
import money, receipt, chain, cord, tamper, baseline

ver = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %s"],
                     capture_output=True, text=True).stdout.strip()
print("仓库版本:", ver)
print("并发:", chain.CONCURRENCY)'''


cells = [
    md("""# CORD train · 确认性评测（预先登记）

**要回答的问题**：本项目（模型只转写、代码核对）的篡改检出率，是否高于对照 B（把同样的算术关系
写进提示词、让模型自己核算）。前两次预先登记的评测方向一致但不显著：
CORD validation 5 : 1（p = 0.22），商业发票 2 : 0（p = 0.50）。

**设计**（写于结果出来之前，见仓库 README）：

- 数据：CORD-v2 **train** 划分（800 张，从未使用）。按标注筛出各条校验全部自洽的票，取前 300 张；
  偶数号篡改（整块重绘、只改一个金额、随机种子 42），奇数号不动
- 三组：本项目（代码冻结于本次运行的 commit，多数判定）、对照 B、对照 A（直接问）；
  同一批图片、同一模型、各读 3 次、同样的多数规则
- **主检验**：篡改票上逐张比较本项目与对照 B 是否检出，精确 McNemar 双侧检验，α = 0.05
- 次要：本项目 vs 对照 A；各组误报率；单次识别层面；本项目 + 小计缺失修正
  （同一批读数，只改代码侧判定）
- 功效：效应与 validation 相同时约 0.98；按两次评测合并的保守估计约 0.92
- **只跑一次**，结果原样报告

**运行**：Colab CPU 即可。约 2700 次请求，并发 10，预计 40–70 分钟。
对照 A 放在最后，时间不够可以不跑——主检验不依赖它。
"""),

    md("## 1. 装依赖、配 API"),
    code('''!pip install -q datasets langchain-core langchain-deepseek

import os
os.environ["RECEIPT_CONCURRENCY"] = "10"    # 只影响速度：各次请求相互独立，温度为 0
try:
    from google.colab import userdata
    os.environ["DEEPSEEK_API_KEY"] = userdata.get("DEEPSEEK_API_KEY")
except Exception as e:
    raise SystemExit(f"请先在左栏钥匙图标里加 DEEPSEEK_API_KEY：{e}")
print("API key 已就绪")'''),

    md("## 2. 拉取代码仓库"),
    code(CLONE),

    md("""## 3. 选票

只读标注列筛选，不解码全部 800 张图像（整体解码会占满 Colab 内存）；选出的 300 张再单独取图。"""),
    code('''import json
from datasets import load_dataset

N_TOTAL = 300
ds = load_dataset("naver-clova-ix/cord-v2", split="train")
pool = []
for i, gt_str in enumerate(ds["ground_truth"]):
    gt = json.loads(gt_str)
    rec = cord.parse_gt(gt["gt_parse"])
    c1 = cord.check_subtotal_explains_total(rec)
    c2 = cord.check_items_explain_subtotal(rec)
    c4 = cord.check_payment_explains_total(rec)
    if c1 is not None and c2 is not None and c1 == 0 and c2 == 0 and c4 in (None, 0):
        pool.append({"i": i, "cid": f"train#{i}", "gt": gt, "rec": rec})

EVAL_SET = pool[:N_TOTAL]
for s in EVAL_SET:
    s["image"] = ds[s["i"]]["image"]
print(f"train 共 {len(ds)} 张，各条校验全部自洽 {len(pool)} 张，本次评测 {len(EVAL_SET)} 张")'''),

    md("## 4. 篡改（与 CORD validation 同一方法）"),
    code('''import random
import time

rng = random.Random(42)
plan = []
for k, s in enumerate(EVAL_SET):
    img, info = s["image"], None
    if k % 2 == 0:
        targets = cord.money_fields(s["gt"]["gt_parse"])
        rng.shuffle(targets)
        for field, old in targets:
            img2, info = tamper.tamper_cord(s["image"], s["gt"]["valid_line"], field, old,
                                            seed=rng.randrange(10 ** 6))
            if info:
                img = img2
                break
    plan.append({"k": k, "cid": s["cid"], "tampered": bool(info), "image": img, "info": info})

urls = [chain.pil_data_url(p["image"]) for p in plan]     # 三组共用同一批图片
made = sum(p["tampered"] for p in plan)
print(f"{len(plan)} 张里篡改 {made} 张，未改 {len(plan) - made} 张")'''),

    md("## 5. 本项目：模型只转写，代码核对"),
    code('''READS = 3
t0 = time.time()
readings = chain.read_many(chain.build_chain(), urls, reads=READS)
print(f"本项目：{len(plan)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")

ours, ours_fb, ours_reads, ours_m = [], [], [], []
for k, p in enumerate(plan):
    recs = [r for r in (receipt.parse_reading(x) for x in readings[k]) if r]
    m = receipt.merge(recs, locale=receipt.IDR)
    mf = receipt.merge(recs, locale=receipt.IDR, fallback=True)
    ours_m.append(m)
    ours.append(m["verdict"] if m else "识别失败")
    ours_fb.append(mf["verdict"] if mf else "识别失败")
    ours_reads.append((m["reads_flagged"], m["reads"]) if m else None)

# 顺带：未篡改那一半的字段准确率（E2）
fields = ["subtotal", "total", "items_total"]
hit, n_c = {f: 0 for f in fields}, 0
for k, p in enumerate(plan):
    m = ours_m[k]
    if p["tampered"] or m is None:
        continue
    n_c += 1
    g = EVAL_SET[k]["rec"]
    want = {"subtotal": g["subtotal"], "total": g["total"], "items_total": g["items_total"]}
    for f in fields:
        hit[f] += want[f] is not None and m[f] == want[f]
print(f"未篡改 {n_c} 张的字段准确率：" + "  ".join(f"{f} {hit[f]}/{n_c}" for f in fields))'''),

    md("## 6. 对照 B：把算术关系写进提示词，模型自己核算"),
    code('''t0 = time.time()
rd_b = chain.read_many(chain.build_chain(system=baseline.SELF_CHECK_PROMPT), urls, reads=READS,
                       instruction=baseline.SELF_CHECK_INSTRUCTION)
res_b = [baseline.vote(rd_b[k], baseline.self_check_flags) for k in range(len(plan))]
print(f"对照 B：{len(plan)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")'''),

    md("## 7. 对照 A：直接问是否被篡改（次要，时间不够可跳过）"),
    code('''t0 = time.time()
rd_a = chain.read_many(chain.build_chain(system=baseline.ASK_PROMPT), urls, reads=READS,
                       instruction=baseline.ASK_INSTRUCTION)
res_a = [baseline.vote(rd_a[k], baseline.ask_flags) for k in range(len(plan))]
print(f"对照 A：{len(plan)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")'''),

    md("## 8. 结果"),
    code('''from math import comb, sqrt


def wilson(k, n, z=1.96):
    if n == 0:
        return "—"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{max(c - h, 0):.0%}–{min(c + h, 1):.0%}"


def mcnemar(x, y):
    """精确 McNemar 双侧检验：x、y 为两种做法各自独有的检出张数。"""
    n = x + y
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(x, y) + 1)) / 2 ** n)


T = [k for k, p in enumerate(plan) if p["tampered"]]
C = [k for k, p in enumerate(plan) if not p["tampered"]]
arms = {"本项目": ours, "本项目 + 小计缺失修正": ours_fb,
        "对照 B 模型自己核算": [r[0] for r in res_b]}
if "res_a" in globals():
    arms["对照 A 直接问"] = [r[0] for r in res_a]

print(f"篡改 {len(T)} 张，未篡改 {len(C)} 张（判定不是「存疑」一律算未检出）")
print()
for name, v in arms.items():
    tp = sum(v[k] == "存疑" for k in T)
    fp = sum(v[k] == "存疑" for k in C)
    print(f"  {name}")
    print(f"      TPR {tp}/{len(T)} = {tp / len(T):.1%}（95% CI {wilson(tp, len(T))}）    "
          f"FPR {fp}/{len(C)} = {fp / len(C):.1%}（95% CI {wilson(fp, len(C))}）")


def paired(a, b):
    x = sum(arms[a][k] == "存疑" and arms[b][k] != "存疑" for k in T)
    y = sum(arms[b][k] == "存疑" and arms[a][k] != "存疑" for k in T)
    return x, y, mcnemar(x, y)


x, y, p_main = paired("本项目", "对照 B 模型自己核算")
print()
print("主检验（预先登记）：篡改票上逐张比较 本项目 vs 对照 B")
print(f"  只有本项目检出 {x} 张，只有对照 B 检出 {y} 张，精确 McNemar 双侧 p = {p_main:.4f}")
print(f"  结论：{'显著（p < 0.05）' if p_main < 0.05 else '不显著（p ≥ 0.05）'}")
print()
print("次要比较：")
for a, b in [("本项目", "对照 A 直接问"), ("本项目 + 小计缺失修正", "对照 B 模型自己核算"),
             ("本项目 + 小计缺失修正", "本项目")]:
    if a in arms and b in arms:
        x2, y2, p2 = paired(a, b)
        print(f"  {a} vs {b}：{x2} : {y2}，p = {p2:.4f}")

print()
print("单次识别层面（每张 3 次各自判定，不经投票）：")
per_read = {"本项目": ours_reads, "对照 B 模型自己核算": [(r[1], r[2]) if r[2] else None for r in res_b]}
if "res_a" in globals():
    per_read["对照 A 直接问"] = [(r[1], r[2]) if r[2] else None for r in res_a]
per_read_summary = {}
for name, ps in per_read.items():
    kt = sum(ps[k][0] for k in T if ps[k])
    nt = sum(ps[k][1] for k in T if ps[k])
    kc = sum(ps[k][0] for k in C if ps[k])
    nc = sum(ps[k][1] for k in C if ps[k])
    per_read_summary[name] = {"篡改票": f"{kt}/{nt}", "未篡改票": f"{kc}/{nc}"}
    print(f"  {name}：篡改票上判存疑 {kt}/{nt} 次，未篡改票上判存疑 {kc}/{nc} 次")

print()
print("本项目与对照 B 判定不一致的篡改票：")
for k in T:
    if (ours[k] == "存疑") != (res_b[k][0] == "存疑"):
        info = plan[k]["info"]
        reason = rd_b[k][0].get("reason") if rd_b[k] else "无读数"
        print(f"  {plan[k]['cid']:10s} {info['field']:26s} {info['old']} -> {info['new']}   "
              f"本项目={ours[k]}  B={res_b[k][0]}({res_b[k][1]}/{res_b[k][2]})  B 的理由：{reason}")

print()
print("误报明细：")
for name, v in arms.items():
    fps = [plan[k]["cid"] for k in C if v[k] == "存疑"]
    if fps:
        print(f"  {name}：{fps}")'''),

    md("## 9. 存结果"),
    code('''out = Path("/content/results/cord_train")
out.mkdir(parents=True, exist_ok=True)
summary = {
    "数据划分": "train", "评测张数": len(plan), "篡改张数": len(T), "未篡改张数": len(C),
    "主检验": {"本项目独有检出": x, "对照B独有检出": y, "p": round(p_main, 6)},
    "各组": {name: {"TPR": f"{sum(v[k] == '存疑' for k in T)}/{len(T)}",
                    "FPR": f"{sum(v[k] == '存疑' for k in C)}/{len(C)}"} for name, v in arms.items()},
    "单次识别": per_read_summary,
    "E2_未篡改字段命中": {f: f"{hit[f]}/{n_c}" for f in fields},
    "READS": READS, "代码版本": ver,
}
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
raw = {p["cid"]: {"tampered": p["tampered"], "info": p["info"], "本项目": readings[k],
                  "对照B": rd_b[k], "对照A": globals().get("rd_a", {}).get(k)}
       for k, p in enumerate(plan)}
(out / "raw_readings.json").write_text(json.dumps(raw, ensure_ascii=False, default=str), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("已存至", out)'''),
]

nb = {"cells": cells,
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python"},
                   "colab": {"provenance": [], "toc_visible": True}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "notebooks" / "04_cord_confirm.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("已生成:", out, "-", len(cells), "格")
