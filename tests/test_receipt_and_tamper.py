"""离线测试：校验层与篡改模块。不调用任何 API。"""
import random
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import receipt  # noqa: E402
import tamper  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"  {'通过' if cond else '失败'}  {name}{('  ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 校验层

def hk_reading(**over):
    """一次港式票据的转写（receipt2 真值）。"""
    d = {"items": [392.20], "subtotal": "316.11", "rounding": "-0.01",
         "tax": 0, "service": 0, "total_paid": "316.10",
         "discounts": [{"label": "Buy 3 Save $9.8", "amount": "9.80"},
                       {"label": "5% OFF (CU-SCO)", "amount": "16.59"},
                       {"label": "MB APP UPGRADE -$10", "amount": "10.00"},
                       {"label": "Buy 2 Save $6", "amount": "6.00"},
                       {"label": "Buy 2 Save $5.9", "amount": "5.90"},
                       {"label": "Buy 2 Save $5.8", "amount": "5.80"},
                       {"label": "Buy 2 Save $2", "amount": "2.00"},
                       {"label": "App Upgrad$350-$20_C", "amount": "20.00"}]}
    d.update(over)
    return d


def idr_reading(**over):
    """一次印尼票据的转写，金额用 '.' 作千位分隔符。"""
    d = {"items": ["50.000", "41.000"], "subtotal": "91.000", "tax": "9.100",
         "service": 0, "rounding": 0, "total_paid": "100.100", "discounts": []}
    d.update(over)
    return d


print("校验层")
r = receipt.parse_reading(hk_reading())
check("港式：小计与实付解析正确",
      r["subtotal"] == Decimal("316.11") and r["total"] == Decimal("316.10"),
      f"小计={r['subtotal']} 应付={r['total']}")
check("港式：折扣合计 76.09", r["discount_total"] == Decimal("76.09"),
      f"得 {r['discount_total']}")
check("港式：标签印证 7 条（5% OFF 那条印证不了）", r["labels_ok"] == 7,
      f"得 {r['labels_ok']}")
v, failed, c = receipt.verdict(r)
check("港式：未篡改判可信", v == "可信", f"c1差={c['c1_gap']} c2差={c['c2_gap']}")

r = receipt.parse_reading(idr_reading())
check("印尼：'91.000' 解析为 91000", r["subtotal"] == Decimal("91000"),
      f"得 {r['subtotal']}")
v, failed, c = receipt.verdict(r)
check("印尼：含税自洽判可信", v == "可信", f"c1差={c['c1_gap']} c2差={c['c2_gap']}")

# 篡改后应被对应的那条校验抓到
v, failed, c = receipt.verdict(receipt.parse_reading(hk_reading(total_paid="816.10")))
check("港式：实付被改大 -> 只有校验一失败", v == "存疑" and failed == ["c1"],
      f"失败项={failed} c1差={c['c1_gap']}")

v, failed, c = receipt.verdict(receipt.parse_reading(hk_reading(items=[399.20])))
check("港式：商品行被改 -> 只有校验二失败", v == "存疑" and failed == ["c2"],
      f"失败项={failed} c2差={c['c2_gap']}")

v, failed, c = receipt.verdict(receipt.parse_reading(idr_reading(total_paid="180.100")), locale=receipt.IDR)
check("印尼：实付被改大 -> 校验一失败", v == "存疑" and "c1" in failed,
      f"c1差={c['c1_gap']}")

# 投票：3 次里 1 次读错，共识应取对的那个
bad = receipt.parse_reading(hk_reading(
    discounts=hk_reading()["discounts"][:-1] + [{"label": "App Upgrad$350-$20_C",
                                                 "amount": "21.00"}]))
good = receipt.parse_reading(hk_reading())
m = receipt.merge([bad, good, good])
check("投票：少数错读被多数盖过", m["discount_total"] == Decimal("76.09"),
      f"得 {m['discount_total']}，reads={m['reads']} pool={m['pool']}")

# ---------------------------------------------------------------- CORD 回归
# 以下几例都来自 E3 第一轮（commit 2fbf8ae）的实测结果。

import json  # noqa: E402
import cord  # noqa: E402

fixture = json.loads((ROOT / "tests" / "fixtures" / "cord_test_34.json").read_text(encoding="utf-8"))
gts = {x["meta"]["image_id"]: cord.parse_gt(x["gt_parse"]) for x in fixture}


def cord_reading(g, **over):
    """按 CORD 标注构造一次逐字无误的转写。单品折扣印在小计上方，整单折扣印在下方。"""
    disc = []
    if g["item_discount"]:
        disc.append({"label": "DISC", "amount": str(g["item_discount"]), "above_subtotal": True})
    if g["discount"]:
        disc.append({"label": "DISC", "amount": str(g["discount"]), "above_subtotal": False})
    d = {"items": [str(p) for p in g["items"]], "discounts": disc,
         "subtotal": str(g["subtotal"]), "tax": str(g["tax"]),
         "service": str(g["service"] + g["othersvc"]), "rounding": 0,
         "total": str(g["total"]), "payments": [str(p) for p in g["payments"]],
         "change": str(g["change"] or 0)}
    d.update(over)
    return d


def idr(payload):
    return receipt.verdict(receipt.parse_reading(payload), locale=receipt.IDR)


clean = [i for i, g in gts.items()
         if cord.check_subtotal_explains_total(g) == 0
         and cord.check_items_explain_subtotal(g) == 0
         and cord.check_payment_explains_total(g) in (None, 0)]
flagged = [i for i in clean if idr(cord_reading(gts[i]))[0] != "可信"]
check(f"CORD：{len(clean)} 张自洽标注逐字转写，无一误报", not flagged, f"误报 {flagged}")

# 第一轮 FPR 20% 的来源：单品折扣已计入小计，却被当成整单折扣又扣一次
unplaced = []
for i in clean:
    p = cord_reading(gts[i])
    p["discounts"] = [{k: v for k, v in e.items() if k != "above_subtotal"} for e in p["discounts"]]
    if idr(p)[0] == "存疑":
        unplaced.append(i)
check("CORD：不报折扣位置时，恰是 31、33 两张被误报", unplaced == [31, 33], f"得 {unplaced}")

# 第一轮漏检：TOTAL 被改，但下方 CASH 行仍印原值
v, failed, c = idr(cord_reading(gts[3], total="11,100"))
check("CORD 3：TOTAL 11,000->11,100 照实读出 -> 校验一、四失败",
      v == "存疑" and failed == ["c1", "c4"], f"失败项={failed}")
v, failed, c = idr(cord_reading(gts[5], total="32.000"))
check("CORD 5：TOTAL 31.000->32.000 照实读出 -> 校验一、四失败",
      v == "存疑" and failed == ["c1", "c4"], f"失败项={failed}")
# 第一轮漏检：CASH 被改，旧校验里没有任何一条覆盖付款行
v, failed, c = idr(cord_reading(gts[10], payments=["20,070"]))
check("CORD 10：CASH 20,000->20,070 -> 只有校验四失败",
      v == "存疑" and failed == ["c4"], f"失败项={failed} c4差={c['c4_gap']}")

v, failed, c = idr({"items": ["50.000"], "subtotal": None, "total": "50.000", "payments": []})
check("缺小计且无付款行 -> 无法核验，而不是默认可信", v == "无法核验", f"得 {v}")

# 自洽优先的投票会让一次「抹平」压过两次如实读出，reads_flagged 把这件事暴露出来
honest = receipt.parse_reading(cord_reading(gts[3], total="11,100"))
masked = receipt.parse_reading(cord_reading(gts[3], total="11,000", payments=["11,000"]))
m = receipt.merge([honest, honest, masked], locale=receipt.IDR)
v, _, _ = receipt.verdict(m, locale=receipt.IDR)
check("投票：2 次如实 + 1 次抹平 -> 共识可信，但 reads_flagged=2 留下痕迹",
      v == "可信" and m["reads_flagged"] == 2, f"判定={v} reads_flagged={m['reads_flagged']}")

# ---------------------------------------------------------------- 篡改模块

print("\n篡改模块")
rng = random.Random(0)
cases = ["60.000", "28,000", "316.10", "91000", "1,974.30"]
ok_len = ok_diff = ok_sep = 0
for t in cases:
    n = tamper.perturb_number(t, rng)
    ok_len += n is not None and len(n) == len(t)
    ok_diff += n is not None and n != t
    ok_sep += n is not None and [c for c in n if not c.isdigit()] == [c for c in t if not c.isdigit()]
check("改数字：长度不变", ok_len == len(cases), f"{ok_len}/{len(cases)}")
check("改数字：值确实变了", ok_diff == len(cases), f"{ok_diff}/{len(cases)}")
check("改数字：分隔符位置不变", ok_sep == len(cases), f"{ok_sep}/{len(cases)}")

from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402

src = ROOT / "data" / "hk" / "receipt2.jpg"
if src.exists():
    img = Image.open(src)
    box = (643, 1216, 720, 1236)  # "$316.10" 整串
    out, ok = tamper.render_over_box(img, box, "$816.10")
    diff = np.abs(np.array(out.convert("RGB"), float)
                  - np.array(img.convert("RGB"), float))
    inside = diff[1216:1236, 643:720].mean()
    outside = (diff.sum() - diff[1210:1242, 637:726].sum()) / diff.size
    check("整块重绘：框内像素被改动", ok and inside > 5, f"框内平均差={inside:.1f}")
    check("整块重绘：框外未被波及", outside < 0.01, f"框外平均差={outside:.4f}")
    out.save(ROOT / "data" / "hk" / "_render_demo.jpg", quality=92)
else:
    check("整块重绘", False, "找不到 data/hk/receipt2.jpg")

print(f"\n{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
