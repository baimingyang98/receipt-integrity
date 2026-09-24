"""统一的校验层：把模型的转写折算成可判定的量，跑各条校验，再跨多次识别投票。

校验全部在这里执行，从不进入提示词。模型不知道自己被什么标准检查，
也就无法朝那个标准编数字。
"""
import re
from decimal import Decimal

from money import parse_amount

ZERO = Decimal("0.00")

# 模型没报折扣位置时的地区默认值：
#   港式超市   折扣印在商品区，SUBTOTAL 已扣过  -> discount_in_subtotal=True
#   印尼 CORD  整单折扣印在小计之后，尚未扣过   -> discount_in_subtotal=False
# 模型报了 above_subtotal 时以版面为准——CORD 的单品折扣就印在小计上方、已计入小计，
# 只按地区一刀切会把它多扣一次。
HK = {"discount_in_subtotal": True}
IDR = {"discount_in_subtotal": False}

CHECKS = ("c1", "c2", "c4")


def _present(v):
    """小计、应付这类合计行不会是 0；读成 0 当作票面没有这一行。"""
    return None if v is None or v == 0 else v


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


def _above(flag):
    """above_subtotal 可能被报成字符串；认不出就当没报。"""
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str) and flag.strip().lower() in ("true", "false"):
        return flag.strip().lower() == "true"
    return None


def _discounts(raw, rounding, hint):
    """返回 (小计上方的折扣, 小计下方的折扣, 位置未知的折扣, 标签印证条数)。"""
    if isinstance(raw, (int, float, str, Decimal)):
        raw = [raw]
    if not isinstance(raw, list):
        return [], [], [], 0
    above, below, unplaced, confirmed = [], [], [], 0
    for e in raw:
        label, pos = None, None
        if isinstance(e, dict):
            label = e.get("label", e.get("text", e.get("description")))
            pos = _above(e.get("above_subtotal"))
            e = e.get("amount", e.get("value", e.get("price")))
        v = parse_amount(e, hint)
        if v is None or v == 0:
            continue
        v = abs(v)
        # 舍入行被误放进折扣里时可识别：它等于 |rounding|
        if rounding != 0 and v == abs(rounding):
            continue
        {True: above, False: below, None: unplaced}[pos].append(v)
        confirmed += _label_confirms(label, v)
    return above, below, unplaced, confirmed


def parse_reading(payload, hint=None):
    """把一次转写折算成校验所需的量；无法解析返回 None。

    票面没印的合计行保持为 None，不用其他字段倒算补上——倒算出来的数必然自洽，
    会让对应的校验空转通过。
    """
    if not isinstance(payload, dict):
        return None
    sub = _present(parse_amount(payload.get("subtotal"), hint))
    printed_total = _present(parse_amount(payload.get("total"), hint))
    pay_raw = payload.get("payments")
    if pay_raw is None and "total_paid" in payload:   # 旧版字段：只有一个实付
        pay_raw = [payload["total_paid"]]
    payments = _amounts(pay_raw, hint)
    change = abs(parse_amount(payload.get("change"), hint) or ZERO)
    paid = sum(payments, ZERO) - change if payments else None
    if sub is None and printed_total is None and paid is None:
        return None

    rounding = parse_amount(payload.get("rounding"), hint) or ZERO
    tax = parse_amount(payload.get("tax"), hint) or ZERO
    service = parse_amount(payload.get("service"), hint) or ZERO
    above, below, unplaced, confirmed = _discounts(payload.get("discounts"), rounding, hint)
    items = _amounts(payload.get("items"), hint)
    return {
        "items_total": sum(items, ZERO), "n_items": len(items),
        "disc_above": sum(above, ZERO), "disc_below": sum(below, ZERO),
        "disc_unplaced": sum(unplaced, ZERO),
        "discount_total": sum(above + below + unplaced, ZERO), "labels_ok": confirmed,
        "subtotal": sub, "tax": tax, "service": service, "rounding": rounding,
        # 应付总额：票面印了就用印的；没印（港式票只有付款行）就是付款减找零
        "total": printed_total if printed_total is not None else paid,
        "total_printed": printed_total is not None,
        "paid": paid,
    }


def checks(rec, tol=Decimal("0.05"), locale=HK):
    """各条校验的差额。差额为 None 表示这条校验在这张票上不适用。"""
    if locale["discount_in_subtotal"]:
        d_in, d_out = rec["disc_above"] + rec["disc_unplaced"], rec["disc_below"]
    else:
        d_in, d_out = rec["disc_above"], rec["disc_below"] + rec["disc_unplaced"]
    sub, total = rec["subtotal"], rec["total"]

    # 校验一：应付 = 小计 - 小计之后的折扣 + 税 + 服务费 + 舍入
    c1 = None
    if sub is not None and total is not None:
        c1 = total - (sub - d_out + rec["tax"] + rec["service"] + rec["rounding"])
    # 校验二：商品行 - 小计之前的折扣 = 小计
    c2 = None
    if sub is not None and rec["n_items"]:
        c2 = rec["items_total"] - d_in - sub
    # 校验四：付款 - 找零 = 应付。票面同时印了应付总额与付款行才适用
    c4 = None
    if rec["total_printed"] and rec["paid"] is not None:
        c4 = rec["paid"] - total

    def ok(gap):
        return None if gap is None else abs(gap) <= tol

    return {"c1_gap": c1, "c1_pass": ok(c1), "c2_gap": c2, "c2_pass": ok(c2),
            "c4_gap": c4, "c4_pass": ok(c4), "labels_ok": rec["labels_ok"]}


def verdict(rec, tol=Decimal("0.05"), locale=HK):
    """可信 / 存疑 / 无法核验，以及失败的是哪几条。

    一条校验都不适用时判「无法核验」，而不是默认可信。
    """
    c = checks(rec, tol, locale)
    applicable = [n for n in CHECKS if c[f"{n}_pass"] is not None]
    if not applicable:
        return "无法核验", [], c
    failed = [n for n in applicable if not c[f"{n}_pass"]]
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
    pool = recs
    for n in CHECKS:
        keep = [r for r in pool if checks(r, tol, locale)[f"{n}_pass"] is not False]
        pool = keep or pool
    best = max(r["labels_ok"] for r in pool)
    pool = [r for r in pool if r["labels_ok"] == best]

    out = {}
    for f in ("subtotal", "tax", "service", "rounding", "total", "total_printed", "paid",
              "disc_above", "disc_below", "disc_unplaced", "discount_total", "items_total"):
        vals = [r[f] for r in pool]
        v, n = Counter(vals).most_common(1)[0]
        out[f] = v if n > 1 or len(pool) == 1 else pool[0][f]
    out["n_items"] = pool[0]["n_items"]
    out["labels_ok"] = best
    out["reads"] = len(recs)
    out["pool"] = len(pool)
    # 单次识别里有几次本身就判存疑——共识若是「可信」而这里不为 0，
    # 说明是自洽优先的排序把存疑的那几次筛掉了
    out["reads_flagged"] = sum(verdict(r, tol, locale)[0] == "存疑" for r in recs)
    return out
