"""金额归一化。

票据金额的书写方式跨地区差异很大，同一个数据集内部也不统一。CORD（印尼）里
同时出现 "60.000"、"91000"、"28,000"、"Rp. 111,000"；港式票据用 "$316.10"。
若按英文习惯把 "60.000" 读成 60.00，六万会变成六十，后面所有校验都失去意义。

规则：
  1. 去掉货币符号与非数字尾巴
  2. 只剩一种分隔符且最后一段是 3 位 -> 千位分隔符，全部删去
  3. 同时出现 "." 和 ","        -> 最后出现的那个是小数点
  4. 只剩一种分隔符且最后一段不是 3 位 -> 小数点
"""
import re
from decimal import Decimal, InvalidOperation

# 允许 "Rp. 111,000"、"HK$1,234.50"、"(5.39)"、"-$16.59"
_NUM = re.compile(r"-?\d[\d.,]*")
_CURRENCY = re.compile(r"(?i)\b(?:rp|hk|idr|usd|sgd|myr|php|thb)\b\.?\s*")


def parse_amount(value, thousands_hint=None):
    """把票据上的金额写法转成 Decimal；无法解析时返回 None。

    thousands_hint: "." 或 "," 可强制指定千位分隔符（已知地区时更稳）。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(str(value))
    if isinstance(value, float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None

    text = _CURRENCY.sub("", value).replace("$", "").replace("￥", "").replace("¥", "")
    text = text.strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").strip()

    m = _NUM.search(text)
    if not m:
        return None
    raw = m.group(0)
    neg = negative or raw.startswith("-")
    raw = raw.lstrip("-").rstrip(".,")
    if not raw:
        return None

    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        # 两种都有，最后出现的是小数点
        dec_sep = "." if raw.rfind(".") > raw.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        raw = raw.replace(thou_sep, "").replace(dec_sep, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        if thousands_hint == sep:
            raw = raw.replace(sep, "")
        else:
            tail = raw.rsplit(sep, 1)[1]
            groups = raw.split(sep)
            # 每段都是 3 位且不止一段 -> 千位分隔符（"1.234.567"）
            if len(tail) == 3 and all(len(g) == 3 for g in groups[1:]):
                raw = raw.replace(sep, "")
            else:
                raw = raw.replace(sep, ".")

    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return -amount if neg else amount


def q2(value):
    """量化到两位小数，便于比较。"""
    return None if value is None else value.quantize(Decimal("0.01"))
