"""E5：视觉模型会自发把票据算平吗？三种转写方式对比。

背景：E4 发现模型在转写阶段就把被篡改的票据"修正"回自洽状态——
  T1 把 816.10 的付款行报成 316.10；
  T2 如实报出被改的 98.80，却把另一行 22.90 改写成 15.90 来对冲。
提示词里并没有任何等式，这是模型自带的"票据应当平账"先验。

假设：模型能对冲，前提是它同时看见整张票。把视野切到单行，它就没有对冲的对象。

用法：把本文件内容粘进 02_hk_demo.ipynb 的新一格（需要先跑完前面的格子，
      变量 chain / HK / ch 已存在）。约 12 次请求。
"""
import json

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek
from PIL import Image

llm = ChatDeepSeek(model=chain.MODEL, temperature=0, max_retries=3, timeout=180)  # noqa: F821

# 条件 B：明确告知票面可能被改动
WARN = ("\n\n注意：这张票据可能已被人为改动，票面数字未必平账。"
        "照抄你看到的，不要修正，也不要让它对得上。")

# 条件 C：只看一行，极简提示词
LINE_SYS = """你在读一张票据上裁出来的一行。只报这一行印的内容，不要推断、不要计算。
只回一个 JSON：{{"label": 这一行左侧的文字, "amount": 金额栏的数字}}"""

CROPS = {"T1": (280, 1212, 760, 1240),   # 只含 OCTOPUS 那一行
         "T2": (280, 812, 760, 848)}     # 只含 FARMFRESH QTY/金额那一行

CASES = {"T1": ("receipt2_T1_payment.jpg", "付款行 316.10 -> 816.10", "816.10"),
         "T2": ("receipt2_T2_item.jpg", "商品行 91.80 -> 98.80", "98.80")}

READS = 2


def ask(system, question, url):
    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", [{"type": "text", "text": "{q}"},
                   {"type": "image_url", "image_url": {"url": "{u}"}}]),
    ])
    return (prompt | llm | JsonOutputParser()).invoke({"q": question, "u": url})


def item_sum(payload):
    from decimal import Decimal
    import money  # noqa: F821
    vals = [money.parse_amount(v) for v in (payload.get("items") or [])]
    return sum((v for v in vals if v is not None), Decimal("0"))


for tag, (fname, desc, expect) in CASES.items():
    path = HK / fname                                              # noqa: F821
    full_url = chain.image_data_url(path)                          # noqa: F821
    crop = Image.open(path).crop(CROPS[tag])
    crop_url = chain.pil_data_url(crop)                            # noqa: F821

    print("=" * 74)
    print(f"{tag}  {desc}   票面真实印着 {expect}")

    for cond, system, question, url in [
        ("A 整票转写（现状）", chain.SYSTEM_PROMPT, chain.INSTRUCTION, full_url),      # noqa: F821
        ("B 整票+告知可能被改", chain.SYSTEM_PROMPT + WARN, chain.INSTRUCTION, full_url),  # noqa: F821
        ("C 单行裁剪转写", LINE_SYS, "这一行印的金额是多少？", crop_url),
    ]:
        got = []
        for _ in range(READS):
            try:
                out = ask(system, question, url)
            except Exception as e:
                got.append(f"失败:{type(e).__name__}")
                continue
            if cond.startswith("C"):
                got.append(str(out.get("amount")))
            elif tag == "T1":   # 港式票没有 TOTAL 行，OCTOPUS 在付款行里
                pays = out.get("payments") or [out.get("total_paid")]
                got.append(str([p.get("amount") if isinstance(p, dict) else p for p in pays]))
            else:
                got.append(f"{item_sum(out)}(含98.80={'98.8' in str(out.get('items'))})")
        hit = any(expect.rstrip("0").rstrip(".") in g.replace(",", "") for g in got)
        print(f"  {cond:22s} {got}   {'照抄' if hit else '被抹平'}")

print("=" * 74)
print("若 C 报出了真实数字而 A 没有，说明对冲发生在'同时看见整张票'这个前提下，")
print("按行转写即可消除——这决定了产品该怎么拆解，而不只是提示词怎么写。")
