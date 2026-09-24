"""读取 CORD (naver-clova-ix/cord-v2) 的标注，并折算成校验所需的量。

CORD 的 gt_parse 有三个超类：menu / sub_total / total。两处需要当心：
  - menu 只有一个商品时是 dict，多个时是 list
  - 金额写法不统一，交给 money.parse_amount 处理
"""
from decimal import Decimal

from money import parse_amount, q2

ZERO = Decimal("0.00")

# 差额不超过半个最小货币单位（印尼盾为 1）视为舍入，例如 CORD 41 的服务费尾差 0.32。
# 必须与 receipt.IDR["tol"] 相同：标注侧筛"自洽票据"与模型侧判"存疑"用同一把尺子，
# 否则标注侧放进来的票会被模型侧判不闭合（留出集的 CORD 41 误报即由此而来）。
TOL = Decimal("0.5")


def _as_list(node):
    """menu 可能是 dict（单品）或 list（多品），统一成 list。"""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def _scalar(value):
    """同一字段被标注多次时值是 list（如 ['46.636', '46.636']），取第一个。"""
    while isinstance(value, list):
        if not value:
            return None
        value = value[0]
    return None if isinstance(value, dict) else value


def _sum(values):
    out = ZERO
    for v in values:
        if v is not None:
            out += v
    return out


def parse_gt(gt_parse, hint=None):
    """把一条 CORD 标注折算成校验需要的字段。"""
    menu = _as_list(gt_parse.get("menu"))
    sub = gt_parse.get("sub_total") or {}
    tot = gt_parse.get("total") or {}

    # 商品行：优先用 price（该行实收），没有就退回 unitprice*cnt 的近似 itemsubtotal
    item_prices, item_discounts = [], []
    for it in menu:
        if not isinstance(it, dict):
            continue
        p = parse_amount(_scalar(it.get("price")), hint)
        if p is None:
            p = parse_amount(_scalar(it.get("itemsubtotal")), hint)
        if p is not None:
            item_prices.append(p)
        d = parse_amount(_scalar(it.get("discountprice")), hint)
        if d is not None:
            item_discounts.append(abs(d))

    # 付款行：现金、刷卡、电子钱包；total_etc 在 CORD 里是代金券等其他付款方式
    payments = [parse_amount(_scalar(tot.get(k)), hint)
                for k in ("cashprice", "creditcardprice", "emoneyprice", "total_etc")]
    payments = [p for p in payments if p is not None]

    return {
        "items": item_prices,
        "items_total": _sum(item_prices),
        "item_discount": _sum(item_discounts),
        "subtotal": parse_amount(_scalar(sub.get("subtotal_price")), hint),
        "discount": abs(parse_amount(_scalar(sub.get("discount_price")), hint) or ZERO),
        "tax": parse_amount(_scalar(sub.get("tax_price")), hint) or ZERO,
        "service": parse_amount(_scalar(sub.get("service_price")), hint) or ZERO,
        "othersvc": parse_amount(_scalar(sub.get("othersvc_price")), hint) or ZERO,
        "total": parse_amount(_scalar(tot.get("total_price")), hint),
        "cash": parse_amount(_scalar(tot.get("cashprice")), hint),
        "change": parse_amount(_scalar(tot.get("changeprice")), hint),
        "payments": payments,
        "n_items": len(item_prices),
    }


def check_items_explain_subtotal(rec, tol=TOL):
    """校验二：商品行总额能否解释小计。"""
    if rec["subtotal"] is None or not rec["items"]:
        return None
    gap = rec["items_total"] - rec["item_discount"] - rec["subtotal"]
    return gap if abs(gap) > tol else ZERO


def check_subtotal_explains_total(rec, tol=TOL):
    """校验一：小计加税费服务费减折扣，能否解释实付总额。"""
    if rec["subtotal"] is None or rec["total"] is None:
        return None
    expect = rec["subtotal"] + rec["tax"] + rec["service"] + rec["othersvc"] - rec["discount"]
    gap = rec["total"] - expect
    return gap if abs(gap) > tol else ZERO


def check_payment_explains_total(rec, tol=TOL):
    """校验四：付款减找零能否解释应付总额。票面没标注付款行时不适用。"""
    if rec["total"] is None or not rec["payments"]:
        return None
    gap = _sum(rec["payments"]) - (rec["change"] or ZERO) - rec["total"]
    return gap if abs(gap) > tol else ZERO


def money_fields(gt_parse, hint=None):
    """列出这张票上所有可被篡改的金额字段路径与原值，供篡改实验挑选目标。"""
    out = []
    def add(path, value):
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, str) and any(c.isdigit() for c in v):
                out.append((path, v))

    for i, it in enumerate(_as_list(gt_parse.get("menu"))):
        if isinstance(it, dict):
            add(f"menu[{i}].price", it.get("price"))
    for key in ("subtotal_price", "discount_price", "tax_price", "service_price"):
        add(f"sub_total.{key}", (gt_parse.get("sub_total") or {}).get(key))
    for key in ("total_price", "cashprice", "changeprice"):
        add(f"total.{key}", (gt_parse.get("total") or {}).get(key))
    return out
