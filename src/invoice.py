"""商业发票（B2B）：标注与模型读数的解析、各条算术校验、跨次投票。

数据：katanaml-org/invoices-donut-data-v1（MIT 许可），500 张合成的英文 B2B 发票，
源自 Kozłowski & Weichbroth (2021) "Samples of electronic invoices", Mendeley Data。

商业发票自带的冗余比零售小票多得多：

  行内  L1  数量 × 单价 = 不含税金额
        L2  不含税金额 × (1 + 税率) = 含税金额
  合计  S1  各行不含税金额之和 = 不含税合计
        S2  不含税合计 + 税额 = 含税合计
        S3  不含税合计 × 税率 = 税额
        S4  按税率分列的小计行 = Total 行（票面把合计印了两遍）

改一个数字，要同时骗过所在行的两条与合计区的几条。

标注侧（E1 筛自洽发票）与模型侧（判存疑）调用同一个 checks()，两边的尺子天然一致。
与 receipt.py 一样：模型只转写，所有算术都在这里做，从不写进提示词。
"""
from collections import Counter
from decimal import Decimal

from money import parse_amount

ZERO = Decimal("0")
# 半分：四舍五入到分能解释的差额放过，改动 1 分即判不闭合。
# 标注实测：L2、S3 的差额全部在 0.005 以内，L1、S2 几乎全为 0。
TOL = Decimal("0.005")

CHECKS = ("L1", "L2", "S1", "S2", "S3", "S4")
NAMES = {"L1": "行·数量×单价", "L2": "行·含税金额", "S1": "合计·行金额之和",
         "S2": "合计·不含税+税", "S3": "合计·税率", "S4": "合计·两处一致"}


def _num(v):
    return parse_amount(v) if v is not None else None


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _item(qty, price, net, rate, gross):
    return {"qty": _num(qty), "price": _num(price), "net": _num(net),
            "rate": _num(rate), "gross": _num(gross)}


def from_gt(gt_parse):
    """标注 -> 校验用的记录。标注偶有把 item_net_worth 错写成 total_net_worth。"""
    items = []
    for it in _as_list(gt_parse.get("items")):
        if isinstance(it, dict):
            items.append(_item(it.get("item_qty"), it.get("item_net_price"),
                               it.get("item_net_worth", it.get("total_net_worth")),
                               it.get("item_vat"), it.get("item_gross_worth")))
    s = gt_parse.get("summary") or {}
    rec = {"items": items, "rows": [],
           "total_net": _num(s.get("total_net_worth")), "total_vat": _num(s.get("total_vat")),
           "total_gross": _num(s.get("total_gross_worth"))}
    rec["complete"] = bool(items) and all(v is not None for it in items for v in it.values())
    return rec


def parse_reading(payload):
    """模型的一次转写 -> 校验用的记录；无法解析返回 None。"""
    if not isinstance(payload, dict):
        return None
    items = [_item(it.get("qty"), it.get("net_price"), it.get("net_worth"),
                   it.get("vat"), it.get("gross_worth"))
             for it in _as_list(payload.get("items")) if isinstance(it, dict)]
    rows = [{"rate": _num(r.get("vat")), "net": _num(r.get("net_worth")),
             "vat": _num(r.get("vat_amount")), "gross": _num(r.get("gross_worth"))}
            for r in _as_list(payload.get("summary_rows")) if isinstance(r, dict)]
    t = payload.get("total") if isinstance(payload.get("total"), dict) else {}
    rec = {"items": items, "rows": rows, "total_net": _num(t.get("net_worth")),
           "total_vat": _num(t.get("vat_amount")), "total_gross": _num(t.get("gross_worth"))}
    if not items and rec["total_gross"] is None:
        return None
    return rec


def _worst(gaps):
    """一组 (差额, 位置) 里取绝对值最大的；空则 None。"""
    gaps = [g for g in gaps if g[0] is not None]
    return max(gaps, key=lambda g: abs(g[0])) if gaps else (None, None)


def checks(rec, tol=TOL):
    """各条校验的最大差额与所在位置。差额为 None 表示这条在这张票上不适用。"""
    items, rows = rec["items"], rec["rows"]
    tn, tv, tg = rec["total_net"], rec["total_vat"], rec["total_gross"]
    out = {}

    def put(name, gap, where=None):
        out[name] = {"gap": gap, "where": where,
                     "pass": None if gap is None else abs(gap) <= tol}

    put("L1", *_worst([(it["qty"] * it["price"] - it["net"], i) for i, it in enumerate(items)
                       if None not in (it["qty"], it["price"], it["net"])]))
    put("L2", *_worst([(it["net"] * (1 + it["rate"] / 100) - it["gross"], i)
                       for i, it in enumerate(items)
                       if None not in (it["net"], it["rate"], it["gross"])]))
    nets = [it["net"] for it in items]
    put("S1", sum(nets, ZERO) - tn if items and tn is not None and None not in nets else None)
    put("S2", tn + tv - tg if None not in (tn, tv, tg) else None)

    # 税率：票面有分税率小计行就逐行核，否则各行税率一致时用合计核
    if rows:
        put("S3", *_worst([(r["net"] * r["rate"] / 100 - r["vat"], i) for i, r in enumerate(rows)
                           if None not in (r["net"], r["rate"], r["vat"])]))
    else:
        rates = {it["rate"] for it in items if it["rate"] is not None}
        put("S3", tn * rates.pop() / 100 - tv
            if len(rates) == 1 and None not in (tn, tv) else None)

    # 合计印了两遍：分税率小计行之和应等于 Total 行（标注里没有小计行，标注侧不适用）
    if rows and None not in (tn, tv, tg):
        sums = [(sum((r[k] for r in rows if r[k] is not None), ZERO) - t, k)
                for k, t in (("net", tn), ("vat", tv), ("gross", tg))]
        put("S4", *_worst(sums))
    else:
        put("S4", None)
    return out


def verdict(rec, tol=TOL):
    """可信 / 存疑 / 无法核验，以及失败的是哪几条。"""
    c = checks(rec, tol)
    applicable = [n for n in CHECKS if c[n]["pass"] is not None]
    if not applicable:
        return "无法核验", [], c
    failed = [n for n in applicable if not c[n]["pass"]]
    return ("存疑" if failed else "可信"), failed, c


FIELDS = ("total_net", "total_vat", "total_gross", "n_items", "items_net")


def merge(readings, tol=TOL):
    """跨多次识别取共识：判定取各次单独判定的多数（至少一半存疑即存疑），
    字段取票数最多的值。规则与 receipt.merge 的默认规则相同。"""
    recs = [r for r in readings if r]
    if not recs:
        return None
    per = [verdict(r, tol) for r in recs]
    judged = [(v, failed) for v, failed, _ in per if v != "无法核验"]
    order = sorted(range(len(recs)), key=lambda i: len(per[i][1]))   # 平票时失败项少的优先

    def value(r, f):
        if f == "n_items":
            return len(r["items"])
        if f == "items_net":
            nets = [it["net"] for it in r["items"]]
            return None if None in nets else sum(nets, ZERO)
        return r[f]

    out = {}
    for f in FIELDS:
        vals = [value(recs[i], f) for i in order]
        v, n = Counter(vals).most_common(1)[0]
        out[f] = v if n > 1 or len(vals) == 1 else vals[0]
    out["reads"] = len(recs)
    out["reads_flagged"] = sum(v == "存疑" for v, _ in judged)
    if not judged:
        out["verdict"], out["failed"] = "无法核验", []
    elif 2 * out["reads_flagged"] >= len(judged):
        out["verdict"] = "存疑"
        out["failed"] = ([n for n in CHECKS
                          if 2 * sum(n in failed for _, failed in judged) >= len(judged)]
                         or [n for n in CHECKS if any(n in failed for _, failed in judged)])
    else:
        out["verdict"], out["failed"] = "可信", []
    return out
