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


CLONE = '''import subprocess, sys
from pathlib import Path

REPO = "https://github.com/baimingyang98/receipt-integrity.git"
ROOT = Path("/content/receipt-integrity")
if ROOT.exists():
    subprocess.run(["git", "-C", str(ROOT), "pull", "-q"], check=False)
else:
    subprocess.run(["git", "clone", "-q", REPO, str(ROOT)], check=True)
sys.path.insert(0, str(ROOT / "src"))

for m in ("money", "receipt", "chain", "cord", "tamper"):
    sys.modules.pop(m, None)
import money, receipt, chain, cord, tamper

ver = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %s"],
                     capture_output=True, text=True).stdout.strip()
print("仓库版本:", ver)'''


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

print("API key 已就绪")'''),

    md("""## 2. 拉取代码仓库

源码在公开仓库 [receipt-integrity](https://github.com/baimingyang98/receipt-integrity)，
不内嵌在 notebook 里。改完代码 push 一次，重跑本格即可拿到最新版本——
本格会打印实际拉到的 commit，实验结果可追溯到具体版本。"""),
    code(CLONE),

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
                    "c2": cord.check_items_explain_subtotal(rec),
                    "c4": cord.check_payment_explains_total(rec)})

# 校验四（付款-找零=应付）只在印了付款行的票上适用，不作为入选门槛，但适用时必须自洽
both = [s for s in samples if s["c1"] is not None and s["c2"] is not None]
clean = [s for s in both if s["c1"] == 0 and s["c2"] == 0 and s["c4"] in (None, 0)]
print(f"总计 {len(samples)} 张")
for k, name in (("c1", "校验一"), ("c2", "校验二"), ("c4", "校验四")):
    app = [s for s in samples if s[k] is not None]
    print(f"  {name}适用 {len(app)} 张，自洽 {sum(s[k] == 0 for s in app)} 张")
print(f"  一、二都适用 {len(both)} 张，全部自洽 {len(clean)} 张  <- 可用于篡改实验")
print(f"  标注层面就不自洽：{len(both) - len(clean)} 张（{(len(both)-len(clean))/max(len(both),1):.0%}）")'''),

    md("""## 4. E2 字段抽取准确率

模型只做转写，金额在 Python 侧汇总，再与 CORD 标注逐字段比对。

**开发集与留出集。** 自洽票据的前 20 张在前两轮里被用来发现和修正规则（开发集）。
规则定稿后，最终数字只在其余的票上跑一次（留出集）——这些票从未参与调规则，
数字才算数。`EVAL_SET` 切换两者。"""),
    code('''DEV, HOLDOUT = clean[:20], clean[20:]
EVAL_SET = HOLDOUT
EVAL_NAME = "留出集" if EVAL_SET is HOLDOUT else "开发集"
READS = 3

from decimal import Decimal
import time

pool = EVAL_SET
print(f"评测集：{EVAL_NAME}，{len(pool)} 张")
urls = [chain.pil_data_url(s["image"]) for s in pool]

t0 = time.time()
ch = chain.build_chain()
readings = chain.read_many(ch, urls, reads=READS)
e2_readings = readings    # E3 会覆盖 readings，这里留一份供下一格诊断
print(f"{len(pool)} 张 x {READS} 次 = {len(pool)*READS} 次请求，耗时 {time.time()-t0:.0f}s")

fields = ["subtotal", "total", "items_total"]
hit = {f: 0 for f in fields}
n_ok = 0
e2_detail = []    # (idx, CORD 编号, 字段, 模型值, 标注值, 是否命中)
for i, s in enumerate(pool):
    cid = s["gt"].get("meta", {}).get("image_id", "?")
    recs = [receipt.parse_reading(p, hint=".") for p in readings[i]]
    m = receipt.merge([r for r in recs if r], locale=receipt.IDR)
    if m is None:
        e2_detail.append((i, cid, "识别失败", None, None, False)); continue
    n_ok += 1
    want = {"subtotal": s["rec"]["subtotal"], "total": s["rec"]["total"],
            "items_total": s["rec"]["items_total"]}
    for f in fields:
        good = want[f] is not None and m[f] == want[f]
        hit[f] += good
        e2_detail.append((i, cid, f, m[f], want[f], good))

print(f"\\n有效识别 {n_ok}/{len(pool)} 张")
for f in fields:
    print(f"  {f:12s} 精确匹配 {hit[f]:3d}/{n_ok}  ({hit[f]/max(n_ok,1):.0%})")'''),

    md("""### E2 未命中的逐张明细

不发请求，只看上一格的结果。几类已知原因会自动标出——它们是**字段定义与 CORD 标注
口径不一致**，不是模型读错，要和真正的误读分开计。"""),
    code('''def why(f, got, want, g):
    if f == "total" and g["cash"] is not None and got == g["cash"]:
        return "<- 报的是 CASH（收现金额），不是 TOTAL"
    if f == "items_total" and g["service"] and got == want + g["service"]:
        return "<- 服务费被算进了商品行"
    if f == "items_total" and g["item_discount"] and got == want - g["item_discount"]:
        return "<- 商品行报的是折后价"
    return ""

misses = [r for r in e2_detail if not r[5]]
print(f"未命中 {len(misses)} 项")
for i, cid, f, got, want, _ in misses:
    if f == "识别失败":
        print(f"  #{i:2d}  CORD {cid:>3}  识别失败"); continue
    g = EVAL_SET[i]["rec"]
    print(f"  #{i:2d}  CORD {cid:>3}  {f:11s} 模型={got}  标注={want}  {why(f, got, want, g)}")
    if f == "total":
        raw = [(x.get("total"), x.get("payments"), x.get("change")) for x in e2_readings[i]]
        print(f"        模型 (total, payments, change)，逐次: {raw}")
        print(f"        标注 CASH={g['cash']}  CHANGE={g['change']}")
    if f == "items_total":
        raw = e2_readings[i][0].get("items") if e2_readings[i] else None
        print(f"        模型 items（第 1 次）: {raw}")
        print(f"        标注 items          : {[str(p) for p in g['items']]}"
              f"  服务费={g['service']}  单品折扣={g['item_discount']}")'''),

    md("""## 5. E3 篡改检出

从两条校验都自洽的票据里取一批，随机一半做图像篡改（整块重绘，按框高匹配字号，
取该处背景与墨色），另一半保持原样。两组混在一起送检，统计：

- **TPR**：被改过的票里，判为存疑的比例
- **FPR**：没改过的票里，误判为存疑的比例

只报检出率是不诚实的，两个都要。"""),
    code('''import random
rng = random.Random(42)

pool = EVAL_SET     # 与 E2 同一批票，偶数号篡改、奇数号不动
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
    plan.append({"idx": i, "cid": s["gt"].get("meta", {}).get("image_id", "?"),
                 "tampered": bool(info), "image": img, "info": info})

made = sum(p["tampered"] for p in plan)
print(f"{len(plan)} 张里成功篡改 {made} 张，未改 {len(plan)-made} 张")
for p in plan[:6]:
    if p["info"]:
        print(f"  #{p['idx']}: {p['info']['field']}  {p['info']['old']} -> {p['info']['new']}")'''),

    code('''urls = [chain.pil_data_url(p["image"]) for p in plan]
t0 = time.time()
readings = chain.read_many(ch, urls, reads=READS)
print(f"{len(plan)} 张 x {READS} 次，耗时 {time.time()-t0:.0f}s\\n")

tp = fp = tn = fn = unk = 0
old_tp = old_fp = 0    # 旧投票规则（自洽优先）在同一批读数上的结果，只作对照
fp_misread = 0         # 误报里，合并后的读数本身就与标注不符的张数
detail = []    # (idx, CORD 编号, 是否篡改, 篡改字段, 判定, 失败项, 单次存疑次数, 旧规则判定)
fld = lambda p: (p["info"] or {}).get("field", "")
for i, p in enumerate(plan):
    recs = [r for r in (receipt.parse_reading(x, hint=".") for x in readings[i]) if r]
    m = receipt.merge(recs, locale=receipt.IDR)
    if m is None:
        v, failed, fr, v_old = "识别失败", [], "-", "识别失败"
    else:
        v, failed, fr = m["verdict"], m["failed"], f"{m['reads_flagged']}/{m['reads']}"
        v_old = receipt.merge(recs, locale=receipt.IDR, rule="consistent")["verdict"]
    flagged = (v == "存疑")
    if v not in ("存疑", "可信"):
        unk += 1
    elif p["tampered"]:
        tp += flagged; fn += not flagged; old_tp += (v_old == "存疑")
    else:
        fp += flagged; tn += not flagged; old_fp += (v_old == "存疑")
        if flagged:
            g = EVAL_SET[i]["rec"]
            fp_misread += any(g[f] is not None and m[f] != g[f]
                              for f in ("subtotal", "total", "items_total"))
    detail.append((p["idx"], p["cid"], p["tampered"], fld(p), v, failed, fr, v_old))

n_t, n_c = tp + fn, fp + tn
print(f"评测集：{EVAL_NAME}")
print(f"被篡改 {n_t} 张：检出 {tp}，漏检 {fn}   TPR = {tp/max(n_t,1):.0%}")
print(f"未篡改 {n_c} 张：误报 {fp}，正确 {tn}   FPR = {fp/max(n_c,1):.0%}")
if fp:
    print(f"  误报中 {fp_misread} 张的读数本身与标注不符——识别错误被正确告警，不是规则误判")
if unk:
    print(f"另有 {unk} 张识别失败或无法核验，不计入上面两行")
print(f"对照：旧投票规则（自洽优先）在同一批读数上  "
      f"TPR = {old_tp/max(n_t,1):.0%}   FPR = {old_fp/max(n_c,1):.0%}")
print("\\n逐张（单次存疑 = 各次识别单独判存疑的次数，至少一半存疑即判存疑）：")
for idx, cid, t, f, v, failed, fr, v_old in detail:
    print(f"  #{idx:3d}  CORD {cid:>3}  {'已篡改' if t else '未篡改'}  {f:26s}  "
          f"判定={v:4s}  失败项={failed or '无'}  单次存疑={fr}"
          + (f"  旧规则={v_old}" if v_old != v else ""))

# 漏检的那几张，把逐次原始读数摆出来：模型是读错了、抹平了，还是读对了却被投票筛掉
missed = [p for p, d in zip(plan, detail) if p["tampered"] and d[4] == "可信"]
if missed:
    print("\\n漏检的逐次原始读数（票面实际印的是改后的值）：")
for p in missed:
    info = p["info"]
    print(f"  #{p['idx']}  CORD {p['cid']}  {info['field']}  {info['old']} -> {info['new']}")
    for x in readings[p["idx"]]:
        if info["field"].startswith("menu"):
            print(f"      items={x.get('items')}  subtotal={x.get('subtotal')}")
        else:
            print(f"      subtotal={x.get('subtotal')}  total={x.get('total')}  "
                  f"payments={x.get('payments')}  change={x.get('change')}")'''),

    md("""## 6. 存结果

把三个实验的数字落盘，PPT 直接引用，不要凭记忆重打。"""),
    code('''import csv, json
out = Path("/content/results")
out.mkdir(parents=True, exist_ok=True)

summary = {
    "E1_总数": len(samples), "E1_两条都适用": len(both), "E1_都自洽": len(clean),
    "评测集": EVAL_NAME,
    "E2_评测张数": len(EVAL_SET), "E2_有效识别": n_ok,
    "E2_字段命中": {f: hit[f] for f in fields},
    "E3_篡改张数": n_t, "E3_TPR": round(tp / max(n_t, 1), 4),
    "E3_未篡改张数": n_c, "E3_FPR": round(fp / max(n_c, 1), 4),
    "E3_误报中读数有误": fp_misread, "E3_无法判定": unk,
    "E3_旧规则_TPR": round(old_tp / max(n_t, 1), 4),
    "E3_旧规则_FPR": round(old_fp / max(n_c, 1), 4),
    "投票规则": "多数单次判定", "READS": READS, "代码版本": ver,
}
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
with (out / "e2_detail.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["idx", "cord_id", "field", "model", "gt", "match"])
    w.writerows(e2_detail)
with (out / "e3_detail.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["idx", "cord_id", "tampered", "field", "verdict", "failed",
                "reads_flagged", "verdict_old_rule"])
    for r in detail:
        w.writerow([*r[:5], "|".join(r[5]), r[6], r[7]])
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("\\n已存至 /content/results/")'''),

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
