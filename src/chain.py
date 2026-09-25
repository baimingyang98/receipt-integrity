"""视觉模型链路：prompt -> 模型 -> JSON。

模型只做转写。提示词里没有任何等式——告诉模型的约束，模型就有动力去满足它
而不是读准，这是早期版本踩过的坑（见 docs/项目总纲.md）。

默认接 DeepSeek 视觉模型，换别家只需改 MODEL 与 build_chain 里的一行。
"""
import base64
import mimetypes
import os
from pathlib import Path

MODEL = os.environ.get("RECEIPT_MODEL", "deepseek-v4-flash-vision-exp")
CONCURRENCY = int(os.environ.get("RECEIPT_CONCURRENCY", 5))

SYSTEM_PROMPT = """你在转写一张零售票据。照抄票面上印的内容，不要做任何计算。

按 JSON 输出这些字段：
- "items"：商品行，一个商品一条，取该行金额栏的数字（行金额，不是单价），正数，按票面顺序。
  单独成行的包装费、押金也算。服务费和税不算，它们有自己的字段。
- "discounts"：每一行会让账单变小的金额——折扣、促销、优惠券、"% OFF"、"MEMBER PRICE"、"SAVE"、"REDEEM" 等。
  每条写成 {{"label": 该行印的文字, "amount": 金额栏的数字取正, "above_subtotal": 该行印在小计行上方为 true，下方为 false}}。
  舍入行不算折扣。折扣常印在它所属商品的下一行，标签形如 "Buy 2 Save $6"、"MB APP UPGRADE -$10"、"5% OFF"。
  label 逐字照抄，amount 取金额栏。
- "subtotal"：SUBTOTAL / 小计 行，照原样；票面没有这一行填 null。
- "tax"：税额行（TAX、PB1、VAT、GST 等），没有则填 0。
- "service"：服务费行（SERVICE、SVC 等），没有则填 0。
- "rounding"：舍入 / 调整行，保留正负号，没有则填 0。
- "total"：应付总额行（TOTAL、GRAND TOTAL、合计、应付金额等），照原样；票面没有这一行填 null。
- "payments"：付款行，每种付款方式一条 {{"label": 该行文字, "amount": 金额栏的数字}}——
  CASH、OCTOPUS、VISA、CREDIT CARD、DEBIT、VOUCHER 等。没有则为空列表。
- "change"：找零行（CHANGE、找续），没有则填 0。

规则：
- 只转写。不要加、减、核对或调平任何总额。
- 不要为了让账单对得上而改动任何数字。票面数字若看起来对不上，也照原样报。
- 一行一条，不合并、不编造、不遗漏。应付总额与付款行即使数字相同，也各报各的。
- **数字连同千位分隔符和小数点一起照抄**。票面写 "60.000" 就写 "60.000"，
  写 "28,000" 就写 "28,000"，不要替你换算或改写格式。
- 只回一个 JSON 对象，不要别的内容：
{{"items": [], "discounts": [{{"label": "", "amount": 0, "above_subtotal": true}}], "subtotal": 0, "tax": 0, "service": 0, "rounding": 0, "total": 0, "payments": [{{"label": "", "amount": 0}}], "change": 0}}
"""

INSTRUCTION = "照抄这张票据的各行金额与合计，按字段定义输出 JSON。"


def image_data_url(path):
    """把本地图片编码成多模态消息可用的 data URL。"""
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def pil_data_url(img, fmt="JPEG"):
    """CORD 的图像来自内存，不落盘也能编码。"""
    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format=fmt, quality=92)
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def build_chain(model=MODEL, temperature=0, timeout=180, system=SYSTEM_PROMPT):
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_deepseek import ChatDeepSeek

    llm = ChatDeepSeek(model=model, temperature=temperature,
                       max_retries=3, timeout=timeout)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", [
            {"type": "text", "text": "{instruction}"},
            {"type": "image_url", "image_url": {"url": "{image_url}"}},
        ]),
    ])
    return prompt | llm | JsonOutputParser()


def read_many(chain, urls, reads=3, concurrency=CONCURRENCY, instruction=INSTRUCTION):
    """对每张票据独立识别 reads 次，全部请求打包进一次并发批处理。

    返回 {票据下标: [原始 JSON, ...]}，失败的那次不计入。
    """
    payloads = [{"instruction": instruction, "image_url": u} for _ in range(reads) for u in urls]
    owners = [i for _ in range(reads) for i in range(len(urls))]
    out = {i: [] for i in range(len(urls))}
    try:
        raw = chain.batch(payloads, config={"max_concurrency": concurrency},
                          return_exceptions=True)
    except Exception:
        return out
    for i, item in zip(owners, raw):
        if isinstance(item, dict):
            out[i].append(item)
    return out
