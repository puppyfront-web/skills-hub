"""
答案等价归一与对错判定模块。

小学数学特有的答案等价问题：
- 1/2 = 0.5（分数↔小数）
- 2小时30分 = 150分钟（时间单位换算）
- 3.0 = 3（小数尾零）
- 一百 = 100（中文数字↔阿拉伯数字）
- 带单位 vs 不带单位（"8米" = "8"）
- 选择题 B = b（大小写）

判定策略：
1. 先归一化（去空格、统一符号、去单位）
2. 尝试数值比较（能转成数字的，比数值相等）
3. 数值不行则字符串比较
4. 都不行标记 need_review
"""

import re
from fractions import Fraction

# ---------------------------------------------------------------------------
# 中文数字 → 阿拉伯数字
# ---------------------------------------------------------------------------

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2,
    "三": 3, "叁": 3, "四": 4, "肆": 4, "五": 5, "伍": 5,
    "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
    "九": 9, "玖": 9, "十": 10, "拾": 10, "百": 100, "佰": 100,
    "千": 1000, "仟": 1000, "万": 10000,
}


def cn_to_arabic(text: str) -> str:
    """尝试把中文数字转成阿拉伯数字。转不了返回原文。"""
    text = text.strip()
    # 简单单字：一、二、三...
    if len(text) == 1 and text in _CN_DIGITS and _CN_DIGITS[text] < 10:
        return str(_CN_DIGITS[text])
    # "十" 开头：十二 → 12, 十 → 10
    if text.startswith("十") or text.startswith("拾"):
        rest = text[1:]
        if not rest:
            return "10"
        if len(rest) == 1 and rest in _CN_DIGITS and _CN_DIGITS[rest] < 10:
            return str(10 + _CN_DIGITS[rest])
    # "几十几"：二十三 → 23
    m = re.match(r"^([二三四五六七八九两])十([一二三四五六七八九])$", text)
    if m:
        tens = _CN_DIGITS[m.group(1)]
        ones = _CN_DIGITS[m.group(2)]
        return str(tens * 10 + ones)
    # "几十"：二十 → 20
    m = re.match(r"^([二三四五六七八九两])十$", text)
    if m:
        return str(_CN_DIGITS[m.group(1)] * 10)
    # "几百" 之类暂不处理，交给 AI
    return text


# ---------------------------------------------------------------------------
# 单位剥离
# ---------------------------------------------------------------------------

# 小学数学常见单位
_UNITS = [
    # 长度
    "米", "分米", "厘米", "毫米", "千米", "公里",
    # 面积
    "平方米", "平方分米", "平方厘米", "平方毫米",
    # 体积/容积
    "立方米", "立方分米", "立方厘米", "升", "毫升",
    # 重量
    "千克", "克", "吨", "公斤", "斤",
    # 时间
    "小时", "分钟", "秒", "时", "分", "天", "日",
    # 货币
    "元", "角", "分", "块钱",
    # 其他
    "个", "只", "本", "支", "棵", "朵", "页", "题", "人", "名",
]

_UNIT_PATTERN = re.compile(
    r"^(-?[\d./]+)\s*(" + "|".join(_UNITS) + r")$"
)


def strip_unit(text: str) -> tuple:
    """剥离答案里的单位。返回 (数值部分, 单位或None)。"""
    text = text.strip()
    # 先匹配"数字+单位"
    m = _UNIT_PATTERN.match(text)
    if m:
        return m.group(1), m.group(2)
    # 纯数字
    if re.match(r"^[\d./\-]+$", text):
        return text, None
    return text, None


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

def normalize_answer(answer: str | None) -> str:
    """答案归一化：去空格、统一符号、统一大小写、转中文数字、剥单位。"""
    if answer is None:
        return ""
    s = str(answer).strip()
    if not s:
        return ""
    # 去所有空格
    s = re.sub(r"\s+", "", s)
    # 判断题符号先转文字（避免被运算符归一化吃掉：× → *）
    s = s.replace("√", "对").replace("√", "对")
    # 统一运算符
    s = s.replace("×", "*").replace("·", "*")
    s = s.replace("÷", "/")
    s = s.replace("－", "-").replace("—", "-")
    s = s.replace("＝", "=")
    s = s.replace("（", "(").replace("）", ")")
    # 剥离单位（"8米" → "8"）
    s, _unit = strip_unit(s)
    # 选项字母统一大写
    if re.match(r"^[a-zA-Z]$", s):
        s = s.upper()
    # 中文数字转阿拉伯
    s = cn_to_arabic(s)
    # 去常见前缀如 "="
    s = s.lstrip("=").strip()
    return s


# ---------------------------------------------------------------------------
# 数值比较
# ---------------------------------------------------------------------------

def to_number(text: str):
    """尝试把答案转成数字（int 或 float 或 Fraction）。转不了返回 None。"""
    s = text.strip()
    if not s:
        return None
    # 整数
    try:
        return int(s)
    except ValueError:
        pass
    # 小数
    try:
        f = float(s)
        return f
    except ValueError:
        pass
    # 分数 a/b
    m = re.match(r"^(-?\d+)/(\d+)$", s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den != 0:
            return Fraction(num, den)
    # 带分数 a b/c
    m = re.match(r"^(-?\d+)\s+(\d+)/(\d+)$", s)
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den != 0:
            return Fraction(whole * den + num, den) if whole >= 0 else Fraction(whole * den - num, den)
    return None


def values_equal(a: str, b: str) -> bool | None:
    """比较两个答案的数值是否相等。

    返回 True/False 表示能确定，None 表示无法数值比较。
    """
    na, nb = to_number(a), to_number(b)
    if na is None or nb is None:
        return None
    # 分数和小数能比：Fraction(1,2) == 0.5
    try:
        return na == nb
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 答案提取（从完整作答里抽出最终结果）
# ---------------------------------------------------------------------------

# 匹配"答：xxx"或"答 xxx"
_ANSWER_PREFIX = re.compile(r"答\s*[:：]?\s*")
# 匹配算式的等号后的结果，如 "36÷9=4" → "4"，"6×5=30（元）" → "30"
_EQ_RESULT = re.compile(r"=\s*([^=（(]+)")


def extract_final_answer(raw_answer: str | None) -> str:
    """从学生的完整作答里提取最终答案。

    应用题学生常写完整过程，如：
      "36÷9=4（个）；答：用36元可以买4个茶杯。"
      "6×5=30（元）；答：共30元。"
      "35÷7=5(个)"
    判定时只该比最终结果（等号后的值），而非算式里的第一个数字。

    提取策略（按优先级）：
    1. 如果有"答：xxx"，取"答"后面的内容里最后一个数字（通常是结论）
    2. 否则取最后一个等号后的数字
    3. 都没有，返回原文（让上层归一化处理）
    """
    if not raw_answer:
        return ""
    s = str(raw_answer).strip()

    # 策略1：找"答：xxx"，取里面的数字（应用题结论）
    # "答：用36元可以买4个茶杯" → 取最后一个数字 4
    m = _ANSWER_PREFIX.search(s)
    if m:
        tail = s[m.end():]
        nums = re.findall(r"[\d.]+(?:/[\d.]+)?", tail)
        if nums:
            return nums[-1]  # 结论里的数字

    # 策略2：取最后一个等号后的数字（算式结果）
    # "36÷9=4（个）" → 4
    eq_matches = _EQ_RESULT.findall(s)
    if eq_matches:
        last_result = eq_matches[-1].strip()
        nums = re.findall(r"[\d.]+(?:/[\d.]+)?", last_result)
        if nums:
            return nums[0]

    # 策略3：如果整体就是一个数字/分数，直接返回
    if re.match(r"^[\d./]+$", s):
        return s

    # 都没匹配上，返回原文（保持原行为）
    return s


# ---------------------------------------------------------------------------
# 对错判定（主入口）
# ---------------------------------------------------------------------------

def judge(student_answer: str | None, correct_answer: str | None) -> dict:
    """判定学生作答是否正确。

    返回:
    {
      "is_correct": bool,
      "normalized_student": str,
      "normalized_correct": str,
      "method": "numeric | string | empty | unknown",
      "note": str  # 判定说明
    }
    """
    # 预处理：从完整作答里提取最终答案（处理应用题的算式作答）
    extracted_student = extract_final_answer(student_answer)
    ns = normalize_answer(extracted_student)
    nc = normalize_answer(correct_answer)

    # 学生未作答
    if not ns:
        return {
            "is_correct": False,
            "normalized_student": "",
            "normalized_correct": nc,
            "method": "empty",
            "note": "学生未作答",
        }

    # 标准答案缺失（AI 没解出来）
    if not nc:
        return {
            "is_correct": False,
            "normalized_student": ns,
            "normalized_correct": "",
            "method": "unknown",
            "note": "未能获取标准答案，需教师判定",
        }

    # 1. 数值比较（核心路径：处理 1/2=0.5、3.0=3 等）
    eq = values_equal(ns, nc)
    if eq is not None:
        return {
            "is_correct": eq,
            "normalized_student": ns,
            "normalized_correct": nc,
            "method": "numeric",
            "note": "数值比较" + ("（含单位剥离）" if ns != normalize_answer(student_answer or "") else ""),
        }

    # 2. 字符串精确比较（选项 B、判断题"对/错"等）
    if ns == nc:
        return {
            "is_correct": True,
            "normalized_student": ns,
            "normalized_correct": nc,
            "method": "string",
            "note": "字符串匹配",
        }

    # 3. 判断题等价表达：对/正确/√/T/True 都算对；错/错误/×/F/False 都算错
    if _judge_equiv(ns, nc):
        return {
            "is_correct": True,
            "normalized_student": ns,
            "normalized_correct": nc,
            "method": "string",
            "note": "判断题等价表达",
        }

    # 4. 无法确定
    return {
        "is_correct": False,
        "normalized_student": ns,
        "normalized_correct": nc,
        "method": "unknown",
        "note": "答案形式不匹配，建议教师复核",
    }


def _judge_equiv(a: str, b: str) -> bool:
    """判断题等价表达。注意 × 已被 normalize 成 *。"""
    true_set = {"对", "正确", "T", "TRUE", "是", "Y", "YES"}
    false_set = {"错", "错误", "*", "X", "F", "FALSE", "否", "N", "NO"}
    a_up = a.upper()
    b_up = b.upper()
    if a_up in true_set and b_up in true_set:
        return True
    if a_up in false_set and b_up in false_set:
        return True
    return False


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="答案对错判定")
    parser.add_argument("student", help="学生作答")
    parser.add_argument("correct", help="标准答案")
    args = parser.parse_args()
    r = judge(args.student, args.correct)
    print(f"学生: {args.student!r} → 归一化: {r['normalized_student']!r}")
    print(f"标准: {args.correct!r} → 归一化: {r['normalized_correct']!r}")
    print(f"判定: {'✓ 对' if r['is_correct'] else '✗ 错'}  方法: {r['method']}  说明: {r['note']}")
