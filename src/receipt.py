"""统一的校验层：把模型的转写折算成可判定的量，跑三条校验，再跨多次识别投票。

三条校验全部在这里执行，从不进入提示词。模型不知道自己被什么标准检查，
也就无法朝那个标准编数字。
"""
import re
from decimal import Decimal

from money import parse_amount

ZERO = Decimal("0.00")


def _amounts(raw, hint):
    """把一列金额归一化成正数 Decimal。"""
    if isinstance(raw, (int, float, str, Decimal)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        if isinstance(e, dict):
            e = e.get("amount", e.get("value", e.get("price")))
        v = parse_amount(e, hint)
        if v is not None and v != 0:
            out.append(abs(v))
    return out


def _label_confirms(label, amount):
    """标签文字里是否重复印了这笔金额。

    港式折扣行常把金额写进标签本身（"Buy 3 Save $9.8"），一个数字印了两遍，
    标签与金额栏互为佐证。模型只被要求照抄标签，并不知道这里在比对。
    """
    if not isinstance(label, str):
        return False
    for m in re.finditer(r"\d+(?:[.,]\d+)?", label):
        v = parse_amount(m.group(0))
        if v is not None and abs(v) == amount:
            return True
    return False


def _discounts(raw, rounding, hint):
    """返回 (折扣金额列表, 标签印证条数)。"""
    if isinstance(raw, (int, float, str, Decimal)):
        raw = [raw]
    if not isinstance(raw, list):
        return [], 0
    amounts, confirmed = [], 0
    for e in raw:
        label = None
        if isinstance(e, dict):
            label = e.get("label", e.get("text", e.get("description")))
            e = e.get("amount", e.get("value", e.get("price")))
        v = parse_amount(e, hint)
        if v is None or v == 0:
            continue
        v = abs(v)
        # 舍入行被误放进折扣里时可识别：它等于 |rounding|
        if rounding != 0 and v == abs(rounding):
            continue
        amounts.append(v)
        confirmed += _label_confirms(label, v)
    return amounts, confirmed


def parse_reading(payload, hint=None):
    """把一次转写折算成校验所需的量；无法解析返回 None。"""
    if not isinstance(payload, dict):
        return None
    sub = parse_amount(payload.get("subtotal"), hint)
    total = parse_amount(payload.get("total_paid"), hint)
    if sub is None and total is None:
        return None
    rounding = parse_amount(payload.get("rounding"), hint) or ZERO
    tax = parse_amount(payload.get("tax"), hint) or ZERO
    service = parse_amount(payload.get("service"), hint) or ZERO
    discounts, confirmed = _discounts(payload.get("discounts"), rounding, hint)
    items = _amounts(payload.get("items"), hint)
    disc_total = sum(discounts, ZERO)
    items_total = sum(items, ZERO)
    if sub is None:
        sub = total - tax - service + disc_total - rounding
    if total is None:
        total = sub + tax + service - disc_total + rounding
    return {
        "items_total": items_total, "n_items": len(items),
        "discount_total": disc_total, "labels_ok": confirmed,
        "subtotal": sub, "tax": tax, "service": service,
        "rounding": rounding, "total_paid": total,
    }


# 「小计」的语义跨地区不同，这是实务里必须配置的参数，不该靠猜：
#   港式超市   SUBTOTAL 印的是折扣「后」金额  -> discount_in_subtotal=True
#   印尼 CORD  subtotal_price 是折扣「前」金额 -> discount_in_subtotal=False
# 同一条公式套两地必错一头，所以把它显式化。
HK = {"discount_in_subtotal": True}
IDR = {"discount_in_subtotal": False}


def checks(rec, tol=Decimal("0.05"), locale=HK):
    """三条校验的差额。返回 None 表示这条校验在这张票上不适用。"""
    in_sub = locale["discount_in_subtotal"]
    # 校验一：实付 = 小计 + 税 + 服务费 + 舍入（小计若为折扣前，还要减去折扣）
    expect_total = (rec["subtotal"] + rec["tax"] + rec["service"] + rec["rounding"]
                    - (ZERO if in_sub else rec["discount_total"]))
    c1 = rec["total_paid"] - expect_total
    # 校验二：商品行总额（小计若为折扣后，要减去折扣）应等于小计
    c2 = None
    if rec["n_items"]:
        expect_sub = rec["items_total"] - (rec["discount_total"] if in_sub else ZERO)
        c2 = expect_sub - rec["subtotal"]
    return {
        "c1_gap": c1, "c1_pass": abs(c1) <= tol,
        "c2_gap": c2, "c2_pass": None if c2 is None else abs(c2) <= tol,
        "labels_ok": rec["labels_ok"],
    }


def verdict(rec, tol=Decimal("0.05"), locale=HK):
    """可信 / 存疑，以及失败的是哪几条。"""
    c = checks(rec, tol, locale)
    failed = []
    if not c["c1_pass"]:
        failed.append("c1")
    if c["c2_pass"] is False:
        failed.append("c2")
    return ("存疑" if failed else "可信"), failed, c


def merge(readings, tol=Decimal("0.05"), locale=HK):
    """跨多次识别取共识。

    证据强弱：自洽的优先 -> 标签印证多的优先 -> 多数投票。
    单次识别的随机误差在这里被消掉，而排序依据全部来自代码侧。
    """
    from collections import Counter

    recs = [r for r in readings if r]
    if not recs:
        return None
    ok = [r for r in recs if checks(r, tol, locale)["c1_pass"]] or recs
    consistent = [r for r in ok if checks(r, tol, locale)["c2_pass"] is not False] or ok
    best = max(r["labels_ok"] for r in consistent)
    pool = [r for r in consistent if r["labels_ok"] == best]

    out = {}
    for f in ("subtotal", "tax", "service", "rounding", "total_paid",
              "discount_total", "items_total"):
        vals = [r[f] for r in pool]
        v, n = Counter(vals).most_common(1)[0]
        out[f] = v if n > 1 or len(pool) == 1 else pool[0][f]
    out["n_items"] = pool[0]["n_items"]
    out["labels_ok"] = best
    out["reads"] = len(recs)
    out["pool"] = len(pool)
    return out
