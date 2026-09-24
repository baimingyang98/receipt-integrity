"""离线测试：金额归一化 + CORD 标注解析。不调用任何 API。

第二部分不是单元测试，而是一次真实测量：在 CORD 公开标注上统计
两条算术校验的适用率与通过率——这个数字直接进 PPT。
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from money import parse_amount  # noqa: E402
import cord  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "cord_test_34.json"


def test_parse_amount():
    cases = [
        # 印尼写法：. 是千位分隔符
        ("60.000", "60000", None), ("31.000", "31000", None),
        ("1.234.567", "1234567", None),
        # 英文写法：, 是千位分隔符
        ("28,000", "28000", None), ("100,000", "100000", None),
        ("HK$1,974.30", "1974.30", None),
        # 带货币前缀 / 裸数字
        ("Rp. 111,000", "111000", None), ("91000", "91000", None),
        # 两种分隔符并存，最后出现的是小数点
        ("1.234,56", "1234.56", None), ("1,234.56", "1234.56", None),
        # 港式小数，两位不是千位分隔符
        ("$316.10", "316.10", None), ("-$16.59", "-16.59", None),
        ("(5.39)", "-5.39", None), ("0.01", "0.01", None),
        # 明确指定千位分隔符时，"60.000" 不再有歧义
        ("60.000", "60000", "."),
        # 无法解析
        ("", None, None), ("N/A", None, None), (None, None, None),
    ]
    bad = []
    for raw, want, hint in cases:
        got = parse_amount(raw, hint)
        got_s = None if got is None else str(got)
        if got_s != want:
            bad.append(f"  parse_amount({raw!r}, hint={hint!r}) = {got_s}，应为 {want}")
    print(f"金额归一化：{len(cases) - len(bad)}/{len(cases)} 通过")
    for b in bad:
        print(b)
    return not bad


def measure_cord():
    rows = json.loads(FIX.read_text(encoding="utf-8"))
    stats = {"total": len(rows), "menu_dict": 0, "menu_list": 0,
             "c1_适用": 0, "c1_通过": 0, "c2_适用": 0, "c2_通过": 0,
             "两条都适用": 0, "两条都通过": 0}
    gaps = []
    for gt in rows:
        gp = gt["gt_parse"]
        stats["menu_dict" if isinstance(gp.get("menu"), dict) else "menu_list"] += 1
        rec = cord.parse_gt(gp)
        c1 = cord.check_subtotal_explains_total(rec)
        c2 = cord.check_items_explain_subtotal(rec)
        if c1 is not None:
            stats["c1_适用"] += 1
            stats["c1_通过"] += c1 == 0
        if c2 is not None:
            stats["c2_适用"] += 1
            stats["c2_通过"] += c2 == 0
        if c1 is not None and c2 is not None:
            stats["两条都适用"] += 1
            stats["两条都通过"] += (c1 == 0 and c2 == 0)
        for name, g in (("小计->总额", c1), ("商品行->小计", c2)):
            if g not in (None, 0):
                gaps.append((name, g, rec))

    print(f"\nCORD 公开标注实测（{stats['total']} 张）")
    print(f"  menu 为 dict / list：{stats['menu_dict']} / {stats['menu_list']}")
    print(f"  校验一 小计+税费->总额   适用 {stats['c1_适用']:2d} 张，"
          f"其中自洽 {stats['c1_通过']:2d} 张")
    print(f"  校验二 商品行->小计       适用 {stats['c2_适用']:2d} 张，"
          f"其中自洽 {stats['c2_通过']:2d} 张")
    print(f"  两条都适用 {stats['两条都适用']} 张，都自洽 {stats['两条都通过']} 张"
          f"  <- 可用于篡改实验的样本")
    if gaps:
        print(f"\n  标注本身对不上的 {len(gaps)} 例（抽样 5 条）：")
        for name, g, rec in gaps[:5]:
            print(f"    {name:12s} 差 {g:>12}  小计={rec['subtotal']} "
                  f"税={rec['tax']} 服务={rec['service']} 折扣={rec['discount']} "
                  f"总额={rec['total']} 商品行数={rec['n_items']}")
    return stats


if __name__ == "__main__":
    ok = test_parse_amount()
    measure_cord()
    sys.exit(0 if ok else 1)
