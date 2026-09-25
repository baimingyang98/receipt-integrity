"""对照组：不经过本项目的校验层，直接让同一个视觉模型下判断。

对照 A「直接问」：把票据交给模型，问它有没有被篡改。这是不做任何设计时最自然的用法。
对照 B「模型自己核算」：把本项目的各条算术关系写进提示词，让模型自己读、自己算、自己判。
    这正是本项目铁律反对的做法（校验进提示词），用来检验那条铁律在数据上是否成立。

与本项目的系统用同一个模型、同样的温度、同样读 3 次、同样的多数判定规则、同一批图片，
差别只在「判断由谁来做」。
"""

ASK_PROMPT = """你是银行的票据审核员。判断这张票据的图像是否被人为篡改过——例如金额数字被改动、涂改或替换。
只回一个 JSON 对象，不要别的内容：
{{"tampered": true 或 false, "reason": "一句话理由"}}
"""
ASK_INSTRUCTION = "这张票据是否被人为篡改过？"

SELF_CHECK_PROMPT = """你在核对一张零售票据的算术是否自洽。先读出票面上的各行金额，再逐条核对：
1. 商品行金额之和 − 印在小计行上方的折扣 = 小计
2. 小计 − 印在小计行下方的折扣 + 税 + 服务费 + 舍入 = 应付总额
3. 付款金额 − 找零 = 应付总额（同一笔付款若在票面印了两遍，只算一次）
某条所需的行票面上没有，就跳过这一条。差额小于最小货币单位的一半视为成立。
只回一个 JSON 对象，不要别的内容：
{{"checks": [{{"rule": 1, "holds": true}}], "consistent": true 或 false, "reason": "一句话理由"}}
"""
SELF_CHECK_INSTRUCTION = "请核对这张票据的算术关系是否自洽。"


def _as_bool(value):
    """模型可能把布尔值写成字符串；认不出返回 None。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "是"):
            return True
        if v in ("false", "no", "否"):
            return False
    return None


def ask_flags(payload):
    """对照 A：模型说被篡改了，就算判存疑。"""
    return _as_bool(payload.get("tampered")) if isinstance(payload, dict) else None


def self_check_flags(payload):
    """对照 B：模型说算术不自洽，就算判存疑。"""
    if not isinstance(payload, dict):
        return None
    consistent = _as_bool(payload.get("consistent"))
    return None if consistent is None else not consistent


def vote(payloads, flags):
    """与本项目相同的多数规则：能解析的读数里至少一半判存疑即存疑。

    返回 (判定, 判存疑次数, 能解析的次数)。
    """
    judged = [f for f in (flags(p) for p in payloads) if f is not None]
    if not judged:
        return "无法判定", 0, 0
    n_flag = sum(judged)
    return ("存疑" if 2 * n_flag >= len(judged) else "可信"), n_flag, len(judged)
