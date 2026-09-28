"""离线测试：商业发票的校验与篡改。不调用任何 API，也不需要 tesseract。"""
import copy
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import invoice  # noqa: E402
import tamper  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"  {'通过' if cond else '失败'}  {name}{('  ' + detail) if detail else ''}")


fixture = json.loads((ROOT / "tests" / "fixtures" / "invoices_valtest_gt.json").read_text(encoding="utf-8"))

# ---------------------------------------------------------------- E1：标注侧

print("标注侧（validation + test，共 %d 张）" % len(fixture))
clean = []
for x in fixture:
    rec = invoice.from_gt(x["gt_parse"])
    if rec["complete"] and invoice.verdict(rec)[0] == "可信":
        clean.append(x)
check("标注完整且自洽的发票 71 张（validation 47 + test 24）", len(clean) == 71, f"得 {len(clean)}")

g = {"items": [{"item_qty": "4,00", "item_net_price": "28,08", "total_net_worth": "112,32",
                "item_vat": "10%", "item_gross_worth": "123,55"}],
     "summary": {"total_net_worth": "$ 112,32", "total_vat": "$11,23", "total_gross_worth": "$ 123,55"}}
rec = invoice.from_gt(g)
check("标注把 item_net_worth 错写成 total_net_worth 也能读",
      rec["complete"] and rec["items"][0]["net"] == Decimal("112.32"))

# ---------------------------------------------------------------- 模型侧


def reading(gt_parse):
    """按标注构造一次逐字无误的转写（含一行分税率小计，与 Total 行相同）。"""
    items = [{"qty": it["item_qty"], "net_price": it["item_net_price"],
              "net_worth": it.get("item_net_worth", it.get("total_net_worth")),
              "vat": it["item_vat"], "gross_worth": it["item_gross_worth"]}
             for it in gt_parse["items"]]
    s = gt_parse["summary"]
    total = {"net_worth": s["total_net_worth"], "vat_amount": s["total_vat"],
             "gross_worth": s["total_gross_worth"]}
    row = {"vat": items[0]["vat"], **{k: v.replace("$", "").strip() for k, v in total.items()}}
    return {"items": items, "summary_rows": [row], "total": total}


print("\n模型侧")
flagged = [i for i, x in enumerate(clean)
           if invoice.verdict(invoice.parse_reading(reading(x["gt_parse"])))[0] != "可信"]
check("71 张逐字无误的转写，无一误报", not flagged, f"误报 {flagged}")

# 取一张多行发票做篡改用例
base = next(x["gt_parse"] for x in clean if len(x["gt_parse"]["items"]) >= 3)
p0 = reading(base)


def failed_after(edit):
    p = copy.deepcopy(p0)
    edit(p)
    return invoice.verdict(invoice.parse_reading(p))[1]


def bump(s, delta="0,01"):
    """把 "1 484,95" 这样的串加上 delta，保持欧式写法。"""
    v = invoice._num(s) + invoice._num(delta)
    return f"{v:.2f}".replace(".", ",")


f = failed_after(lambda p: p["items"][1].update(net_price=bump(p["items"][1]["net_price"], "1,00")))
check("改单价 -> 只有行内数量×单价失败", f == ["L1"], f"失败项={f}")
f = failed_after(lambda p: p["items"][1].update(net_worth=bump(p["items"][1]["net_worth"])))
check("行金额改 1 分 -> 行内两条与行金额之和都失败", f == ["L1", "L2", "S1"], f"失败项={f}")
f = failed_after(lambda p: p["items"][2].update(gross_worth=bump(p["items"][2]["gross_worth"], "0,02")))
check("改含税金额 -> 只有行内含税一条失败", f == ["L2"], f"失败项={f}")
f = failed_after(lambda p: p["total"].update(gross_worth=bump(p["total"]["gross_worth"], "10,00")))
check("只改 Total 行含税合计 -> 不含税+税、两处一致失败", f == ["S2", "S4"], f"失败项={f}")
f = failed_after(lambda p: p["total"].update(vat_amount=bump(p["total"]["vat_amount"])))
# 税率一条核的是分税率小计行，那一行没改；Total 行的改动由 S2、S4 抓到
check("Total 行税额改 1 分 -> 不含税+税、两处一致失败", f == ["S2", "S4"], f"失败项={f}")
p = copy.deepcopy(p0)
p["summary_rows"] = []
p["total"]["vat_amount"] = bump(p["total"]["vat_amount"])
f = invoice.verdict(invoice.parse_reading(p))[1]
check("没读到分税率小计行时，税率一条改用 Total 行核", f == ["S2", "S3"], f"失败项={f}")

p = copy.deepcopy(p0)
p["items"][1]["qty"] = bump(p["items"][1]["qty"], "1,00")
p["items"][1]["net_worth"] = invoice._num(p["items"][1]["qty"]) * invoice._num(p["items"][1]["net_price"])
p["items"][1]["net_worth"] = f"{p['items'][1]['net_worth']:.2f}".replace(".", ",")
f = invoice.verdict(invoice.parse_reading(p))[1]
check("数量与行金额一起改平 -> 行内含税、合计仍失败", "L1" not in f and {"L2", "S1"} <= set(f),
      f"失败项={f}")

v, failed, c = invoice.verdict(invoice.parse_reading({"items": [], "total": {}}) or
                               {"items": [], "rows": [], "total_net": None, "total_vat": None,
                                "total_gross": None})
check("什么都没读到 -> 无法核验", v == "无法核验", f"得 {v}")

good = invoice.parse_reading(p0)
bad = invoice.parse_reading(copy.deepcopy(p0) | {"total": dict(p0["total"], gross_worth="$ 1,00")})
m = invoice.merge([bad, bad, good])
check("投票：2 次存疑 -> 共识存疑", m["verdict"] == "存疑" and m["reads_flagged"] == 2,
      f"{m['verdict']} {m['reads_flagged']}/{m['reads']}")
m = invoice.merge([bad, good, good])
check("投票：1 次存疑 -> 共识可信，合计取多数值",
      m["verdict"] == "可信" and m["total_gross"] == good["total_gross"], f"{m['verdict']}")

# ---------------------------------------------------------------- OCR 定位与篡改

print("\nOCR 定位与篡改")
line = lambda n: (1, 1, n)   # noqa: E731
words = [("1", (100, 10, 110, 30), line(1)), ("484,95", (116, 10, 170, 30), line(1)),
         ("12,00", (200, 10, 250, 30), line(1)), ("12,00", (300, 10, 350, 30), line(1)),
         ("192,81", (100, 50, 160, 70), line(2)),
         ("Total", (20, 90, 70, 110), line(3)), ("$", (90, 90, 98, 110), line(3)),
         ("192,81", (100, 90, 160, 110), line(3))]
hits = tamper.find_value(words, "1 484,95")
check("被切成两个词的 '1 484,95' 能拼回来",
      len(hits) == 1 and hits[0][0] == (100, 10, 170, 30) and hits[0][2] == "1 484,95", f"{hits}")
check("出现两处的 '12,00' 返回两处", len(tamper.find_value(words, "12,00")) == 2)
hits = [h for h in tamper.find_value(words, "$ 192,81") if h[1] == "$"]
check("合计 '$ 192,81' 只取 Total 行那一处", len(hits) == 1 and hits[0][0][1] == 90, f"{hits}")

from PIL import Image, ImageDraw  # noqa: E402
import numpy as np  # noqa: E402

img = Image.new("RGB", (400, 130), "white")
d = ImageDraw.Draw(img)
f = tamper._font(20, tamper.SANS)
placed = []
for text, (x, y), ln in (("7,00", (100, 10), 1), ("3 000,00", (220, 10), 1),
                         ("Total", (20, 90), 3), ("$", (150, 90), 3), ("99,50", (170, 90), 3)):
    d.text((x, y), text, font=f, fill="black")
    placed.append((text, d.textbbox((x, y), text, font=f), line(ln)))
gt = {"items": [{"item_qty": "7,00", "item_net_price": "3 000,00"}],
      "summary": {"total_gross_worth": "$ 99,50"}}
out, info = tamper.tamper_invoice(img, placed, gt, random.Random(0))
diff = np.abs(np.array(out, float) - np.array(img, float)).sum(axis=2)
x0, y0, x1, y1 = info["box"] if info else (0, 0, 0, 0)
inside = diff[y0 - 2:y1 + 3, x0 - 2:x1 + 3].sum()
check("篡改：改了一处且只动了那个框",
      info is not None and inside > 0 and inside == diff.sum() and info["new"] != info["old"],
      f"{info and (info['field'], info['old'], info['new'])}")
fields = set()
for seed in range(30):
    _, info = tamper.tamper_invoice(img, placed, gt, random.Random(seed))
    fields.add(info["field"])
check("篡改目标覆盖商品行与合计", fields == {"items[0].qty", "items[0].net_price",
                                    "summary.total_gross_worth"}, f"{sorted(fields)}")

print(f"\n{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
