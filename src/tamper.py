"""图像篡改：把票面上的某个金额改掉，看核验能否发现。

两种方式，用途不同：

  render_over_box  整块重绘。按框高匹配字号，取该处背景色与墨色，把新数字画上去。
                   全自动，跨 100 张图稳定，用来跑 TPR / FPR 统计量。

  copy_glyph       字形复制。从同一张票上取同字体的数字，抽出墨迹覆盖度，
                   擦掉原字后按目标处的背景与墨色重新合成。视觉真实，
                   8 倍放大才看得出，用来做定性配图。

两种都只改图像，不碰标注——否则实验无效。
"""
import random
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PAD = 4


# ---------------------------------------------------------------- 取色

def _light_median(patch):
    """背景色：取亮于中位数的像素的中位数，避开墨迹。"""
    gray = patch.mean(axis=2)
    light = patch[gray > np.median(gray)]
    return np.median(light, axis=0) if len(light) else np.median(patch.reshape(-1, 3), axis=0)


def _ink_color(patch):
    """墨色：最暗的 5% 像素的中位数。"""
    gray = patch.mean(axis=2)
    dark = patch[gray <= np.percentile(gray, 5)]
    return np.median(dark, axis=0) if len(dark) else np.array([60.0, 40.0, 40.0])


# ---------------------------------------------------------------- 改数字

def perturb_number(text, rng):
    """改动一个数字，保持字符串长度与分隔符不变，返回 (新串, 新值-旧值 的十进制位)。

    保持长度是为了重绘后仍能塞进原来的框。
    """
    digits = [i for i, c in enumerate(text) if c.isdigit()]
    if not digits:
        return None
    # 不改首位为 0，也不把唯一一位改成 0
    for _ in range(20):
        i = rng.choice(digits)
        old = text[i]
        new = rng.choice([d for d in "0123456789" if d != old])
        if i == digits[0] and new == "0" and len(digits) > 1:
            continue
        return text[:i] + new + text[i + 1:]
    return None


# ---------------------------------------------------------------- 方式一：整块重绘

MONO_BOLD = ("DejaVuSansMono-Bold.ttf", "DejaVuSansMono.ttf", "DejaVuSans-Bold.ttf")  # 小票
SANS = ("DejaVuSans.ttf",)              # 商业发票正文
SANS_BOLD = ("DejaVuSans-Bold.ttf",)    # 商业发票 Total 行


def _font(size, names=MONO_BOLD):
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:  # matplotlib 自带 DejaVu，Colab 上一定有
        import matplotlib
        p = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / names[0]
        return ImageFont.truetype(str(p), size)
    except Exception:
        return ImageFont.load_default()


def render_over_box(img, box, new_text, font_names=MONO_BOLD):
    """把 box 区域擦掉并重绘 new_text，尽量贴合原来的字号与颜色。"""
    x0, y0, x1, y1 = [int(v) for v in box]
    arr = np.array(img.convert("RGB")).astype(float)
    h, w = arr.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return img, False

    ring = arr[max(0, y0 - PAD):y1 + PAD, max(0, x0 - PAD):x1 + PAD]
    bg, ink = _light_median(ring), _ink_color(arr[y0:y1, x0:x1])

    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    d.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], fill=tuple(int(v) for v in bg))

    # 字号按框高收敛，再按框宽微调，保证新数字不溢出
    size = max(8, int((y1 - y0) * 1.05))
    for _ in range(12):
        f = _font(size, font_names)
        tw, th = d.textbbox((0, 0), new_text, font=f)[2:]
        if tw <= (x1 - x0) and th <= (y1 - y0) * 1.25:
            break
        size -= 1
        if size < 8:
            break
    f = _font(size, font_names)
    tw, th = d.textbbox((0, 0), new_text, font=f)[2:]
    d.text((x1 - tw, y0 + ((y1 - y0) - th) // 2), new_text,
           font=f, fill=tuple(int(v) for v in ink))
    return out, True


# ---------------------------------------------------------------- 方式二：字形复制

def copy_glyph(arr, tgt, src, rng):
    """用 src 处的字形覆盖 tgt 处的字形，原地修改 arr（float RGB）。"""
    tx0, ty0, tx1, ty1 = tgt
    sx0, sy0, sx1, sy1 = src

    ring = arr[ty0 - PAD:ty1 + PAD, tx0 - PAD:tx1 + PAD]
    bg, ink = _light_median(ring), _ink_color(arr[ty0:ty1, tx0:tx1])
    noise = float(np.std(ring.mean(axis=2))) * 0.35

    ex0, ey0, ex1, ey1 = tx0 - PAD, ty0 - PAD, tx1 + PAD, ty1 + PAD
    hh, ww = ey1 - ey0, ex1 - ex0
    patch = np.broadcast_to(bg, (hh, ww, 3)) + rng.normal(0, noise, (hh, ww, 1))
    arr[ey0:ey1, ex0:ex1] = np.clip(patch, 0, 255)

    src_patch = arr[sy0:sy1, sx0:sx1]
    gray = src_patch.mean(axis=2)
    s_bg, s_ink = _light_median(src_patch).mean(), gray.min()
    alpha = np.clip((s_bg - gray) / max(s_bg - s_ink, 1e-6), 0, 1)[..., None]

    sh, sw = alpha.shape[:2]
    cy, cx = (ty0 + ty1) // 2, (tx0 + tx1) // 2
    py0, px0 = cy - sh // 2, cx - sw // 2
    region = arr[py0:py0 + sh, px0:px0 + sw]
    arr[py0:py0 + sh, px0:px0 + sw] = np.clip(region * (1 - alpha) + ink * alpha, 0, 255)


# ---------------------------------------------------------------- CORD 专用

def quad_to_box(quad):
    xs = [quad[f"x{i}"] for i in (1, 2, 3, 4)]
    ys = [quad[f"y{i}"] for i in (1, 2, 3, 4)]
    return min(xs), min(ys), max(xs), max(ys)


def find_word(valid_line, category, text):
    """在标注里找到某个类别下、文字等于 text 的词，返回它的框。

    gt_parse 里的值可能带货币前缀（"Rp 35.000"），而 OCR 把 "Rp" 和数字
    切成了两个词，所以还要按纯数字部分再找一轮。
    """
    want = text.strip()
    digits = re.sub(r"[^\d.,]", "", want).strip(".,")
    words = [(w, line) for line in valid_line
             if line.get("category") == category for w in line.get("words", [])]

    for w, _ in words:                              # 完全相等
        if w.get("text", "").strip() == want:
            return quad_to_box(w["quad"])
    for w, _ in words:                              # 词里包含整串
        if want and want in w.get("text", ""):
            return quad_to_box(w["quad"])
    if digits:                                      # 只比数字部分
        for w, _ in words:
            if re.sub(r"[^\d.,]", "", w.get("text", "")).strip(".,") == digits:
                return quad_to_box(w["quad"])
    return None


def field_to_category(field):
    """"menu[0].price" -> "menu.price"；标注里的 category 不带下标。"""
    return re.sub(r"\[\d+\]", "", field)


def tamper_cord(sample_image, valid_line, field, old_text, seed=0):
    """按字段名在 CORD 图像上改动一个金额。

    返回 (新图, 说明)；定位不到目标词时返回 (原图, None)。
    """
    rng = random.Random(seed)
    box = find_word(valid_line, field_to_category(field), old_text)
    if box is None:
        return sample_image, None
    new_text = perturb_number(old_text, rng)
    if not new_text or new_text == old_text:
        return sample_image, None
    out, ok = render_over_box(sample_image, box, new_text)
    if not ok:
        return sample_image, None
    return out, {"field": field, "old": old_text, "new": new_text, "box": box}


# ---------------------------------------------------------------- 商业发票（无标注框，靠 OCR 定位）

def ocr_words(img):
    """tesseract 取词：[(文字, (x0, y0, x1, y1), 行号)]。需要 pytesseract 与 tesseract-ocr。"""
    import pytesseract
    d = pytesseract.image_to_data(img.convert("RGB"), output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        if t:
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            out.append((t, (x, y, x + w, y + h),
                        (d["block_num"][i], d["par_num"][i], d["line_num"][i])))
    return out


def _digits(s):
    return re.sub(r"[^\d,.]", "", s)


def find_value(words, text):
    """在 OCR 词表里找印着 text 的位置，返回 [(框, 前一个词, 印着的原文)]。

    "1 484,95" 会被 OCR 切成 "1" 与 "484,95"，所以同一行相邻的 1–3 个数字词拼起来也算。
    """
    want = _digits(text)
    hits = []
    if not want:
        return hits
    for i in range(len(words)):
        for n in (1, 2, 3):
            seg = words[i:i + n]
            if len(seg) < n or len({w[2] for w in seg}) > 1 or not all(_digits(w[0]) for w in seg):
                break
            if "".join(_digits(w[0]) for w in seg) == want:
                box = (min(w[1][0] for w in seg), min(w[1][1] for w in seg),
                       max(w[1][2] for w in seg), max(w[1][3] for w in seg))
                prev = words[i - 1][0] if i > 0 and words[i - 1][2] == seg[0][2] else ""
                hits.append((box, prev, " ".join(w[0] for w in seg)))
                break
    return hits


def invoice_targets(gt_parse):
    """标注里所有可改的金额与数量：[(字段, 原文)]。"""
    out = []
    items = gt_parse.get("items") or []
    for i, it in enumerate(items if isinstance(items, list) else [items]):
        if not isinstance(it, dict):
            continue
        for key, name in (("item_qty", "qty"), ("item_net_price", "net_price"),
                          ("item_net_worth", "net_worth"), ("item_gross_worth", "gross_worth")):
            v = it.get(key)
            if isinstance(v, str) and any(c.isdigit() for c in v):
                out.append((f"items[{i}].{name}", v))
    s = gt_parse.get("summary") or {}
    for key in ("total_net_worth", "total_vat", "total_gross_worth"):
        v = s.get(key)
        if isinstance(v, str) and any(c.isdigit() for c in v):
            out.append((f"summary.{key}", v))
    return out


def tamper_invoice(img, words, gt_parse, rng):
    """在商业发票上改动一个数字。返回 (新图, 说明)；找不到可改的目标返回 (原图, None)。

    - 合计在票面印了两遍（分税率小计行与 Total 行）：只改带 $ 的 Total 行
    - 同一个数在票面出现多处时（如数量为 1 时单价等于金额），分不清改的是哪一处，跳过
    """
    targets = invoice_targets(gt_parse)
    rng.shuffle(targets)
    for field, text in targets:
        hits = find_value(words, text)
        font = SANS
        if field.startswith("summary."):
            hits = [h for h in hits if h[1] == "$" or h[2].startswith("$")]
            font = SANS_BOLD
        if len(hits) != 1:
            continue
        box, _, printed = hits[0]
        new = perturb_number(printed, rng)
        if not new or new == printed:
            continue
        out, ok = render_over_box(img, box, new, font_names=font)
        if ok:
            return out, {"field": field, "old": printed, "new": new, "box": box}
    return img, None
