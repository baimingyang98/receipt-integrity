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
      r["subtotal"] == Decimal("316.11") and r["total_paid"] == Decimal("316.10"),
      f"小计={r['subtotal']} 实付={r['total_paid']}")
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
