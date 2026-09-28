"""生成 03：商业发票场景的实验 notebook。

与 build_notebooks.py（CORD 小票）分开：数据、字段、篡改定位方式都不同。
单元格代码里刻意不写反斜杠转义，换行一律用单独的 print()。
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

for m in ("money", "chain", "tamper", "baseline", "invoice"):
    sys.modules.pop(m, None)
import money, chain, tamper, baseline, invoice

ver = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %s"],
                     capture_output=True, text=True).stdout.strip()
print("仓库版本:", ver)'''


cells = [
    md("""# 商业发票核验 · 供应链金融 / 对公报销场景

工行杯 · 金融安全服务方向

零售小票（CORD）证明了方法可行；这里换成银行对公业务里更常见的单据——B2B 商业发票。
发票自带的冗余多得多，改一个数字要同时骗过好几条等式：

| | 校验 | 内容 |
|---|---|---|
| L1 | 行·数量×单价 | Qty × Net price = Net worth |
| L2 | 行·含税金额 | Net worth × (1 + VAT%) = Gross worth |
| S1 | 合计·行金额之和 | 各行 Net worth 之和 = Total 行 Net worth |
| S2 | 合计·不含税+税 | Total 行 Net worth + VAT = Gross worth |
| S3 | 合计·税率 | Net worth × VAT% = VAT |
| S4 | 合计·两处一致 | 分税率小计行 = Total 行（票面把合计印了两遍） |

模型只转写，六条校验全在代码侧；标注侧（E1）与模型侧用同一个 `invoice.checks()`。

**数据**：[katanaml-org/invoices-donut-data-v1](https://huggingface.co/datasets/katanaml-org/invoices-donut-data-v1)
（MIT），500 张**合成**的英文 B2B 发票，源自 Kozłowski & Weichbroth (2021)。

**两步走**：`SPLIT = "dev"` 先在 train 的 20 张上跑通流程、暴露问题；
规则定稿后改 `SPLIT = "eval"`，在从未使用的 validation + test 上只跑一次。

**运行环境**：Colab CPU 即可；篡改定位要装 tesseract（第 1 格自动装）。
"""),

    md("## 1. 装依赖、配 API"),
    code('''!apt-get -qq install -y tesseract-ocr > /dev/null
!pip install -q datasets pytesseract langchain-core langchain-deepseek

import os
try:
    from google.colab import userdata
    os.environ["DEEPSEEK_API_KEY"] = userdata.get("DEEPSEEK_API_KEY")
except Exception as e:
    raise SystemExit(f"请先在左栏钥匙图标里加 DEEPSEEK_API_KEY：{e}")
print("API key 已就绪")'''),

    md("## 2. 拉取代码仓库"),
    code(CLONE),

    md("""## 3. E1：标注本身自洽吗

只有标注完整、六条校验全部闭合的发票才进入实验——否则分不清误报来自方法还是来自数据。"""),
    code('''import json
from datasets import load_dataset

DS = "katanaml-org/invoices-donut-data-v1"
SPLIT = "dev"     # "dev"：train 前 20 张自洽发票；"eval"：validation + test，只跑一次


def load(split):
    rows = []
    for i, r in enumerate(load_dataset(DS, split=split)):
        g = json.loads(r["ground_truth"])["gt_parse"]
        rec = invoice.from_gt(g)
        rows.append({"id": f"{split}#{i}", "image": r["image"], "gt": g, "rec": rec,
                     "clean": rec["complete"] and invoice.verdict(rec)[0] == "可信"})
    return rows


rows = load("train") if SPLIT == "dev" else load("validation") + load("test")
clean = [r for r in rows if r["clean"]]
EVAL_SET = clean[:20] if SPLIT == "dev" else clean
print(f"{SPLIT}：共 {len(rows)} 张，标注完整 {sum(r['rec']['complete'] for r in rows)} 张，"
      f"全部自洽 {len(clean)} 张，本次评测 {len(EVAL_SET)} 张")'''),

    md("""## 4. E2：转写准确率

原图 2481×3508，缩到长边 1600 再送模型。比对 Total 行三个合计、商品行数、各行金额之和。"""),
    code('''import time
from decimal import Decimal
from PIL import Image

READS = 3
MAX_SIDE = 1600


def to_url(img):
    img = img.convert("RGB")
    s = MAX_SIDE / max(img.size)
    if s < 1:
        img = img.resize((round(img.size[0] * s), round(img.size[1] * s)), Image.LANCZOS)
    return chain.pil_data_url(img)


ch = chain.build_chain(system=chain.INVOICE_PROMPT)
t0 = time.time()
readings = chain.read_many(ch, [to_url(s["image"]) for s in EVAL_SET], reads=READS,
                           instruction=chain.INVOICE_INSTRUCTION)
e2_readings = readings
print(f"{len(EVAL_SET)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")


def want(rec):
    nets = [it["net"] for it in rec["items"]]
    return {"total_net": rec["total_net"], "total_vat": rec["total_vat"],
            "total_gross": rec["total_gross"], "n_items": len(rec["items"]),
            "items_net": sum(nets, Decimal("0"))}


fields = list(invoice.FIELDS)
hit = {f: 0 for f in fields}
n_ok = 0
e2_detail = []
for i, s in enumerate(EVAL_SET):
    m = invoice.merge([invoice.parse_reading(p) for p in readings[i]])
    if m is None:
        e2_detail.append((s["id"], "识别失败", None, None, False))
        continue
    n_ok += 1
    w = want(s["rec"])
    for f in fields:
        good = m[f] == w[f]
        hit[f] += good
        e2_detail.append((s["id"], f, m[f], w[f], good))

print(f"有效识别 {n_ok}/{len(EVAL_SET)} 张")
for f in fields:
    print(f"  {f:12s} 精确匹配 {hit[f]:3d}/{n_ok}  ({hit[f] / max(n_ok, 1):.0%})")
print()
print("未命中：")
for sid, f, got, exp, good in e2_detail:
    if not good:
        print(f"  {sid:14s} {f:12s} 模型={got}  标注={exp}")'''),

    md("""## 5. E3：篡改检出

偶数号篡改、奇数号不动。篡改先用 tesseract 找到数字的位置，再按原字体（DejaVu Sans）
整块重绘一个数字；合计只改 Total 行，分税率小计行保持原值。"""),
    code('''import random

rng = random.Random(42)
plan = []
t0 = time.time()
for i, s in enumerate(EVAL_SET):
    img, info = s["image"].convert("RGB"), None
    if i % 2 == 0:
        img2, info = tamper.tamper_invoice(img, tamper.ocr_words(img), s["gt"], rng)
        if info:
            img = img2
    plan.append({"idx": i, "id": s["id"], "tampered": bool(info), "image": img, "info": info})

made = sum(p["tampered"] for p in plan)
print(f"OCR 与篡改耗时 {time.time() - t0:.0f}s；{len(plan)} 张里篡改 {made} 张，未改 {len(plan) - made} 张")
for p in plan:
    if p["info"]:
        print(f"  {p['id']:14s} {p['info']['field']:28s} {p['info']['old']} -> {p['info']['new']}")'''),

    md("看一眼改得像不像（原图 / 篡改后，框附近放大 3 倍）："),
    code('''from IPython.display import display

for p in [p for p in plan if p["tampered"]][:3]:
    x0, y0, x1, y1 = p["info"]["box"]
    box = (x0 - 120, y0 - 25, x1 + 40, y1 + 25)
    a = EVAL_SET[p["idx"]]["image"].convert("RGB").crop(box)
    b = p["image"].crop(box)
    sheet = Image.new("RGB", (a.width * 3, a.height * 6), "white")
    sheet.paste(a.resize((a.width * 3, a.height * 3)), (0, 0))
    sheet.paste(b.resize((b.width * 3, b.height * 3)), (0, a.height * 3))
    print(p["id"], p["info"]["field"], p["info"]["old"], "->", p["info"]["new"])
    display(sheet)'''),

    code('''t0 = time.time()
readings = chain.read_many(ch, [to_url(p["image"]) for p in plan], reads=READS,
                           instruction=chain.INVOICE_INSTRUCTION)
print(f"{len(plan)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")

tp = fp = tn = fn = unk = 0
detail = []
for i, p in enumerate(plan):
    m = invoice.merge([invoice.parse_reading(x) for x in readings[i]])
    v, failed, fr = ("识别失败", [], "-") if m is None else (
        m["verdict"], m["failed"], f"{m['reads_flagged']}/{m['reads']}")
    flagged = v == "存疑"
    if v not in ("存疑", "可信"):
        unk += 1
    elif p["tampered"]:
        tp += flagged
        fn += not flagged
    else:
        fp += flagged
        tn += not flagged
    detail.append((p["idx"], p["id"], p["tampered"], (p["info"] or {}).get("field", ""), v, failed, fr))

n_t, n_c = tp + fn, fp + tn
print()
print(f"评测集：{SPLIT}")
print(f"被篡改 {n_t} 张：检出 {tp}，漏检 {fn}   TPR = {tp / max(n_t, 1):.0%}")
print(f"未篡改 {n_c} 张：误报 {fp}，正确 {tn}   FPR = {fp / max(n_c, 1):.0%}")
if unk:
    print(f"另有 {unk} 张识别失败或无法核验，不计入上面两行")
print()
for idx, sid, t, f, v, failed, fr in detail:
    names = [invoice.NAMES[n] for n in failed]
    print(f"  #{idx:3d} {sid:14s} {'已篡改' if t else '未篡改'}  {f:28s} 判定={v}  "
          f"失败项={names or '无'}  单次存疑={fr}")


def read_value(payload, field):
    """从一次原始转写里取出被篡改的那一格。"""
    if field.startswith("summary."):
        key = {"total_net_worth": "net_worth", "total_vat": "vat_amount",
               "total_gross_worth": "gross_worth"}[field.split(".")[1]]
        return (payload.get("total") or {}).get(key)
    k, name = int(field.split("[")[1].split("]")[0]), field.split(".")[1]
    items = payload.get("items") or []
    return items[k].get(name) if k < len(items) and isinstance(items[k], dict) else None


missed = [p for p, d in zip(plan, detail) if p["tampered"] and d[4] != "存疑"]
if missed:
    print()
    print("漏检的票：被改的那一格，模型逐次读成了什么")
for p in missed:
    got = [read_value(x, p["info"]["field"]) for x in readings[p["idx"]]]
    print(f"  {p['id']:14s} {p['info']['field']:28s} 票面 {p['info']['new']}（原 {p['info']['old']}）  读成 {got}")'''),

    md("""## 6. 对照组

同一批图片、同一个模型、同样读 3 次、同样的多数规则，只换判断由谁来做：
对照 A 直接问模型是否被篡改；对照 B 把六条关系写进提示词，让模型自己算、自己判。"""),
    code('''arms = {
    "对照 A 直接问": (baseline.ASK_PROMPT, baseline.ASK_INSTRUCTION, baseline.ask_flags),
    "对照 B 模型自己核算": (baseline.INVOICE_SELF_CHECK_PROMPT,
                        baseline.INVOICE_SELF_CHECK_INSTRUCTION, baseline.self_check_flags),
}
urls = [to_url(p["image"]) for p in plan]
arm_readings, arm_result = {}, {}
for name, (system, instruction, flags) in arms.items():
    t0 = time.time()
    rd = chain.read_many(chain.build_chain(system=system), urls, reads=READS, instruction=instruction)
    arm_readings[name] = rd
    arm_result[name] = [baseline.vote(rd[i], flags) for i in range(len(plan))]
    print(f"{name}：{len(plan)} 张 x {READS} 次，耗时 {time.time() - t0:.0f}s")


def rates(verdicts):
    t = [v for v, p in zip(verdicts, plan) if p["tampered"] and v in ("存疑", "可信")]
    c = [v for v, p in zip(verdicts, plan) if not p["tampered"] and v in ("存疑", "可信")]
    return (sum(v == "存疑" for v in t), len(t), sum(v == "存疑" for v in c), len(c),
            sum(v not in ("存疑", "可信") for v in verdicts))


ours = [d[4] for d in detail]
table = {"本项目（代码核对）": rates(ours)}
for name in arms:
    table[name] = rates([r[0] for r in arm_result[name]])
print()
for name, (k, nt, f, nc, u) in table.items():
    print(f"  {name}")
    print(f"      TPR {k}/{nt} = {k / max(nt, 1):.0%}    FPR {f}/{nc} = {f / max(nc, 1):.0%}"
          + (f"    无法判定 {u}" if u else ""))

A, B = arm_result["对照 A 直接问"], arm_result["对照 B 模型自己核算"]
print()
print("逐张（本项目 / A / B）：")
for i, p in enumerate(plan):
    print(f"  #{i:3d} {p['id']:14s} {'已篡改' if p['tampered'] else '未篡改'}  "
          f"{ours[i]} / {A[i][0]}({A[i][1]}/{A[i][2]}) / {B[i][0]}({B[i][1]}/{B[i][2]})")

missed_b = [i for i, p in enumerate(plan) if p["tampered"] and B[i][0] == "可信"]
if missed_b:
    print()
    print("对照 B 漏检的票，模型给的理由（第 1 次）：")
for i in missed_b[:8]:
    info, rd = plan[i]["info"], arm_readings["对照 B 模型自己核算"][i]
    print(f"  {plan[i]['id']}  {info['field']} {info['old']} -> {info['new']}：{rd[0].get('reason') if rd else '无读数'}")'''),

    md("## 7. 存结果"),
    code('''import csv

out = Path(f"/content/results/invoice_{SPLIT}")
out.mkdir(parents=True, exist_ok=True)
summary = {
    "数据划分": SPLIT, "评测张数": len(EVAL_SET),
    "E2_有效识别": n_ok, "E2_字段命中": {f: hit[f] for f in fields},
    "E3_篡改张数": n_t, "E3_TPR": round(tp / max(n_t, 1), 4),
    "E3_未篡改张数": n_c, "E3_FPR": round(fp / max(n_c, 1), 4), "E3_无法判定": unk,
    "对照组": {k: {"检出": f"{v[0]}/{v[1]}", "TPR": round(v[0] / max(v[1], 1), 4),
                   "误报": f"{v[2]}/{v[3]}", "FPR": round(v[2] / max(v[3], 1), 4)}
               for k, v in globals().get("table", {}).items()},
    "READS": READS, "代码版本": ver,
}
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
with (out / "e3_detail.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["idx", "id", "tampered", "field", "verdict", "failed", "reads_flagged"])
    for r in detail:
        w.writerow([*r[:5], "|".join(r[5]), r[6]])
raw = {"E2": {s["id"]: e2_readings[i] for i, s in enumerate(EVAL_SET)},
       "E3": {p["id"]: {"tampered": p["tampered"], "info": p["info"], "reads": readings[p["idx"]]}
              for p in plan},
       "对照组": {name: {p["id"]: rd[p["idx"]] for p in plan}
                  for name, rd in globals().get("arm_readings", {}).items()}}
(out / "raw_readings.json").write_text(json.dumps(raw, ensure_ascii=False, default=str), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("已存至", out)'''),
]

nb = {"cells": cells,
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python"},
                   "colab": {"provenance": [], "toc_visible": True}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "notebooks" / "03_invoice_eval.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("已生成:", out, "-", len(cells), "格")
