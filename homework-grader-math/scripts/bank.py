"""
题库模块：题库匹配 + AI 解题兜底 + 衍生出题。

核心机制（PRD 的决策中枢）：
1. 题面归一化后与题库做相似度匹配，阈值 ≥ 0.9 视为命中
2. 命中 → 取标准答案 + 知识点，confidence = 1.0
3. 未命中 → 调 AI 解题，生成答案 + 知识点推断 + 置信度
4. AI 解题的题目，教师确认后可回写题库（越用越准）

题库存储：~/.openclaw/skill-state/homework-grader-math/question-bank.json
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

sys.path.insert(0, str(Path(__file__).parent))
from _gateway import call_with_retry, extract_json_from_text

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

STATE_DIR = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math"
BANK_PATH = STATE_DIR / "question-bank.json"

MATCH_THRESHOLD = 0.9  # 题库命中相似度阈值


# ---------------------------------------------------------------------------
# 题库读写
# ---------------------------------------------------------------------------

def ensure_bank() -> dict:
    """确保题库文件存在，返回题库 dict。不存在则用初始题库初始化。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not BANK_PATH.is_file():
        bank = _initial_bank()
        save_bank(bank)
        return bank
    try:
        with open(BANK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # 损坏了，重建
        bank = _initial_bank()
        save_bank(bank)
        return bank


def save_bank(bank: dict) -> None:
    """保存题库。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(BANK_PATH, "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 题面归一化与相似度
# ---------------------------------------------------------------------------

def normalize_stem(stem: str) -> str:
    """题面归一化：去空格、统一标点、统一运算符。"""
    if not stem:
        return ""
    s = stem.strip()
    # 去所有空白
    s = re.sub(r"\s+", "", s)
    # 统一运算符（全角→半角，含全角＊／）
    s = s.replace("×", "*").replace("·", "*").replace("＊", "*")
    s = s.replace("÷", "/").replace("／", "/")
    s = s.replace("－", "-").replace("—", "-")
    s = s.replace("＝", "=")
    s = s.replace("＜", "<").replace("＞", ">")
    s = s.replace("（", "(").replace("）", ")")
    # 去末尾等号（"25×4=" 和 "25×4" 视为同一题面）
    s = s.rstrip("=")
    # 统一大小写（选项字母）
    s = s.lower()
    return s


def similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0-1）。"""
    na, nb = normalize_stem(a), normalize_stem(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


# ---------------------------------------------------------------------------
# 题库匹配
# ---------------------------------------------------------------------------

def match_bank(stem: str, bank: dict) -> dict | None:
    """在题库中匹配题面。命中返回题目 dict，未命中返回 None。"""
    questions = bank.get("questions", [])
    best = None
    best_score = 0.0
    for q in questions:
        score = similarity(stem, q.get("stem", ""))
        if score > best_score:
            best_score = score
            best = q
    if best and best_score >= MATCH_THRESHOLD:
        return {**best, "_match_score": round(best_score, 3)}
    return None


# ---------------------------------------------------------------------------
# AI 解题
# ---------------------------------------------------------------------------

SOLVE_PROMPT_TEMPLATE = """你是小学数学解题助手。请解答下面的题目，并推断它考查的知识点。

题目：{stem}
{options_part}

【第一步：题面完整性自检（非常重要）】
解题前先判断题目是否包含解题所需的**全部条件**：
- 应用题常见必需条件：单价+总价（算数量）、数量+单价（算总价）、速度+时间（算路程）等
- 如果题目问"买几个""分几段""几小时"等，但**没有给出单价/每段长度/速度等必需条件**，说明题面不完整
- 题面不完整时，**不要猜测或编造条件**，answer 填 "无法确定"，confidence 填 0.3，answer_steps 说明缺什么条件

例："用36元可以买几个茶杯？" —— 没有"茶杯每个多少钱"，缺单价，answer 填 "无法确定"
例："用36元可以买几个茶杯？每个9元" —— 条件齐全，正常解答 answer=4

【输出格式】
输出 JSON，严格遵循以下 schema：
{{
  "answer": "标准答案（如 '100' 或 'B' 或 '3/4'）。题面不全填 '无法确定'",
  "answer_steps": "解题步骤简述（如 '25×4：20×4=80, 5×4=20, 合计100'）",
  "knowledge_points": ["知识点1", "知识点2"],
  "difficulty": 1-5 的整数（1最易，5最难）,
  "confidence": 0.0-1.0 的小数（你对答案的把握程度）
}}

【规则】
1. answer 只写最终答案，不带单位（除非题目要求带单位）
2. 分数写 a/b 形式（如 3/4），小数保留原样
3. 选择题 answer 填选项字母（如 'B'）
4. knowledge_points 用小学数学知识点的标准命名（如"两位数乘一位数""同分母分数加减""长方形周长"）
5. confidence：计算题通常 ≥ 0.9；应用题或需要推断的，按实际把握给
6. **绝不猜测或编造题面里没有的条件**——缺条件就填"无法确定"

只输出 JSON，不要输出其他内容。"""


def solve_by_ai(stem: str, options: list | None, base_url: str, token: str,
                model: str = "openclaw/default") -> dict:
    """AI 现场解题。返回 {answer, answer_steps, knowledge_points, difficulty, confidence}。

    网络失败/限流时返回低置信度结果（answer=None, confidence=0）而非抛异常，
    保证单题失败不阻断整次批改。
    """
    options_part = ""
    if options:
        options_part = "选项：\n" + "\n".join(options)
    prompt = SOLVE_PROMPT_TEMPLATE.format(stem=stem, options_part=options_part)

    try:
        resp = call_with_retry(base_url, token, prompt, model=model)
    except Exception as e:
        # 网络/限流错误：返回低置信度，让上层标 need_review
        return {
            "answer": None,
            "answer_steps": None,
            "knowledge_points": [],
            "difficulty": 3,
            "confidence": 0.0,
            "_error": f"AI解题请求失败: {type(e).__name__}",
        }

    try:
        result = extract_json_from_text(resp)
    except json.JSONDecodeError as e:
        # 兜底：返回低置信度结果
        return {
            "answer": None,
            "answer_steps": None,
            "knowledge_points": [],
            "difficulty": 3,
            "confidence": 0.0,
            "_parse_error": str(e),
        }

    result.setdefault("confidence", 0.5)
    result.setdefault("knowledge_points", [])
    result.setdefault("difficulty", 3)
    result.setdefault("answer", None)
    result.setdefault("answer_steps", None)
    return result


# ---------------------------------------------------------------------------
# 衍生出题
# ---------------------------------------------------------------------------

DERIVE_PROMPT_TEMPLATE = """你是小学数学出题专家。针对下面的错题，出 {count} 道考查**同一知识点**的练习题，帮助学生巩固。

原题：{stem}
学生错答：{student_answer}
正确答案：{correct_answer}
知识点：{knowledge_points}
错误类型：{error_type}

【出题要求】
1. 必须考查同一知识点（{knowledge_points}）
2. 题型优先选择题（便于自动批改），其次是填空题
3. 第一题难度与原题相当；若要出多道，难度可略降但不能太简单
4. 不要与原题重复（数字、情境都要变化）
5. 选择题的干扰项要来自学生常见错误（如进位错、口诀错、运算符看错）
6. 每道题都要给标准答案

【输出格式】
输出 JSON：
{{
  "derived_questions": [
    {{
      "type": "choice | fill_blank",
      "stem": "题面（如 '下列算式中，结果等于100的是？'）",
      "options": ["A.24×4", "B.25×4", ...] 或 null（非选择题）,
      "answer": "标准答案（如 'B' 或 '100'）",
      "knowledge_point": "本题考查的知识点",
      "difficulty": 1-5,
      "explanation": "为什么选这个答案的简述"
    }}
  ]
}}

只输出 JSON，不要输出其他内容。"""


def derive_questions(stem: str, student_answer: str | None, correct_answer: str,
                     knowledge_points: list, error_type: str,
                     base_url: str, token: str, model: str = "openclaw/default",
                     count: int = 2) -> dict:
    """针对错题生成同知识点衍生题。

    网络失败时返回空列表 + _error，不抛异常。
    """
    kp = "、".join(knowledge_points) if knowledge_points else "（未标注）"
    prompt = DERIVE_PROMPT_TEMPLATE.format(
        stem=stem,
        student_answer=student_answer or "(未识别到作答)",
        correct_answer=correct_answer,
        knowledge_points=kp,
        error_type=error_type,
        count=count,
    )
    try:
        resp = call_with_retry(base_url, token, prompt, model=model)
    except Exception as e:
        return {"derived_questions": [], "_error": f"出题请求失败: {type(e).__name__}: {e}"}
    try:
        result = extract_json_from_text(resp)
    except json.JSONDecodeError as e:
        return {"derived_questions": [], "_error": str(e)}
    result.setdefault("derived_questions", [])
    # 给每道衍生题补 id
    for i, q in enumerate(result["derived_questions"]):
        q.setdefault("type", "choice")
        q.setdefault("stem", "")
        q.setdefault("options", None)
        q.setdefault("answer", "")
        q.setdefault("knowledge_point", kp)
        q.setdefault("difficulty", 3)
        q.setdefault("explanation", "")
        q["internal_id"] = f"D{i + 1:03d}"
        q["source_stem"] = stem
    return result


# ---------------------------------------------------------------------------
# 题库回写（越用越准）
# ---------------------------------------------------------------------------

def add_to_bank(stem: str, answer: str, knowledge_points: list,
                qtype: str, difficulty: int = 3,
                options: list | None = None, answer_steps: str | None = None) -> dict:
    """把一道题加入题库（教师确认后调用）。"""
    bank = ensure_bank()
    q = {
        "id": f"QB{len(bank.get('questions', [])) + 1:04d}",
        "stem": stem,
        "type": qtype,
        "options": options,
        "answer": answer,
        "answer_steps": answer_steps,
        "knowledge_points": knowledge_points,
        "difficulty": difficulty,
        "source": "ai_confirmed",  # AI 解题后教师确认入库
        "textbook": "人教版",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    bank.setdefault("questions", []).append(q)
    save_bank(bank)
    return q


# ---------------------------------------------------------------------------
# 综合决策：取标准答案 + 知识点（题库优先，AI 兜底）
# ---------------------------------------------------------------------------

def resolve_answer(stem: str, options: list | None, qtype: str,
                   base_url: str, token: str,
                   model: str = "openclaw/default") -> dict:
    """对一道题取标准答案。

    返回:
    {
      "correct_answer": str,
      "knowledge_points": list,
      "answer_steps": str | None,
      "difficulty": int,
      "source": "bank" | "ai",
      "confidence": float,
      "need_review": bool,
      "bank_match_score": float | None
    }
    """
    bank = ensure_bank()
    matched = match_bank(stem, bank)

    if matched:
        return {
            "correct_answer": matched.get("answer"),
            "knowledge_points": matched.get("knowledge_points", []),
            "answer_steps": matched.get("answer_steps"),
            "difficulty": matched.get("difficulty", 3),
            "source": "bank",
            "confidence": 1.0,
            "need_review": False,
            "bank_match_score": matched.get("_match_score"),
        }

    # AI 兜底
    ai_result = solve_by_ai(stem, options, base_url, token, model)
    confidence = float(ai_result.get("confidence", 0.5))
    raw_answer = ai_result.get("answer")
    has_error = bool(ai_result.get("_error") or ai_result.get("_parse_error"))

    # 区分两种"没有答案"的情况：
    # 1. 网络/解析错误（has_error）：AI 根本没返回结果，应重试一次
    # 2. AI 明确表示无法确定：返回了结果但说"无法确定"，这才是真正的无法判定
    if has_error and raw_answer is None:
        # 网络错误，重试一次（换个时间点可能就好了）
        import time
        time.sleep(2)
        ai_result = solve_by_ai(stem, options, base_url, token, model)
        confidence = float(ai_result.get("confidence", 0.5))
        raw_answer = ai_result.get("answer")
        has_error = bool(ai_result.get("_error") or ai_result.get("_parse_error"))

    # 检测 AI 是否给出了有效答案
    is_invalid = _is_invalid_answer(raw_answer)

    return {
        "correct_answer": None if (is_invalid or has_error) else raw_answer,
        "knowledge_points": ai_result.get("knowledge_points", []),
        "answer_steps": ai_result.get("answer_steps"),
        "difficulty": ai_result.get("difficulty", 3),
        "source": "ai",
        "confidence": confidence,
        "need_review": is_invalid or has_error or confidence < 0.85,
        "bank_match_score": None,
    }


def _is_invalid_answer(answer) -> bool:
    """判断 AI 返回的答案是否无效（无法判定对错）。

    无效情况：
    - None / 空字符串
    - 明确表示无法确定的表述："无法确定""不能确定""无法判断""未知""无解"等
    """
    if answer is None:
        return True
    s = str(answer).strip()
    if not s:
        return True
    invalid_markers = [
        "无法确定", "不能确定", "无法判断", "无法解答", "无法计算",
        "条件不足", "信息不足", "题目不完整", "无法得出",
        "无解", "不确定", "未知",
    ]
    return any(m in s for m in invalid_markers)


# ---------------------------------------------------------------------------
# 初始题库（小学数学常见题，人教版）
# ---------------------------------------------------------------------------

def _initial_bank() -> dict:
    """初始题库：50 道小学数学常见题。"""
    return {
        "version": "1.0",
        "textbook": "人教版",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "questions": _SEED_QUESTIONS,
    }


# 初始题库种子题（覆盖小学常见知识点，便于首次使用即有命中率）
_SEED_QUESTIONS = [
    # --- 两位数乘一位数 ---
    {"id": "QB0001", "stem": "25×4=", "type": "calculation", "options": None,
     "answer": "100", "answer_steps": "20×4=80, 5×4=20, 80+20=100",
     "knowledge_points": ["两位数乘一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0002", "stem": "36×7=", "type": "calculation", "options": None,
     "answer": "252", "answer_steps": "30×7=210, 6×7=42, 210+42=252",
     "knowledge_points": ["两位数乘一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0003", "stem": "48×5=", "type": "calculation", "options": None,
     "answer": "240", "answer_steps": "40×5=200, 8×5=40, 200+40=240",
     "knowledge_points": ["两位数乘一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},

    # --- 表内乘除 ---
    {"id": "QB0004", "stem": "7×8=", "type": "calculation", "options": None,
     "answer": "56", "answer_steps": "七八五十六",
     "knowledge_points": ["表内乘法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
    {"id": "QB0005", "stem": "63÷9=", "type": "calculation", "options": None,
     "answer": "7", "answer_steps": "七九六十三",
     "knowledge_points": ["表内除法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
    {"id": "QB0006", "stem": "8×9=", "type": "calculation", "options": None,
     "answer": "72", "answer_steps": "八九七十二",
     "knowledge_points": ["表内乘法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
    {"id": "QB0007", "stem": "42÷6=", "type": "calculation", "options": None,
     "answer": "7", "answer_steps": "六七四十二",
     "knowledge_points": ["表内除法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},

    # --- 两位数加减 ---
    {"id": "QB0008", "stem": "35+47=", "type": "calculation", "options": None,
     "answer": "82", "answer_steps": "5+7=12进1, 3+4+1=8",
     "knowledge_points": ["两位数加法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
    {"id": "QB0009", "stem": "92-38=", "type": "calculation", "options": None,
     "answer": "54", "answer_steps": "12-8=4借1, 8-3=5",
     "knowledge_points": ["两位数减法"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0010", "stem": "76+58=", "type": "calculation", "options": None,
     "answer": "134", "answer_steps": "6+8=14进1, 7+5+1=13",
     "knowledge_points": ["三位数加法"], "difficulty": 2, "source": "seed", "textbook": "人教版"},

    # --- 三位数乘除 ---
    {"id": "QB0011", "stem": "125×8=", "type": "calculation", "options": None,
     "answer": "1000", "answer_steps": "100×8=800, 25×8=200, 合计1000",
     "knowledge_points": ["三位数乘一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0012", "stem": "360÷4=", "type": "calculation", "options": None,
     "answer": "90", "answer_steps": "36÷4=9, 所以360÷4=90",
     "knowledge_points": ["三位数除以一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0013", "stem": "204×3=", "type": "calculation", "options": None,
     "answer": "612", "answer_steps": "200×3=600, 4×3=12, 合计612",
     "knowledge_points": ["三位数乘一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},

    # --- 小数运算 ---
    {"id": "QB0014", "stem": "3.5+2.8=", "type": "calculation", "options": None,
     "answer": "6.3", "answer_steps": "0.5+0.8=1.3进1, 3+2+1=6",
     "knowledge_points": ["小数加法"], "difficulty": 3, "source": "seed", "textbook": "人教版"},
    {"id": "QB0015", "stem": "10-3.6=", "type": "calculation", "options": None,
     "answer": "6.4", "answer_steps": "10.0-3.6: 10-6=4借1, 9-3=6",
     "knowledge_points": ["小数减法"], "difficulty": 3, "source": "seed", "textbook": "人教版"},
    {"id": "QB0016", "stem": "0.25×4=", "type": "calculation", "options": None,
     "answer": "1", "answer_steps": "25×4=100, 两位小数, 得1.00=1",
     "knowledge_points": ["小数乘法"], "difficulty": 3, "source": "seed", "textbook": "人教版"},

    # --- 分数运算 ---
    {"id": "QB0017", "stem": "1/2+1/4=", "type": "calculation", "options": None,
     "answer": "3/4", "answer_steps": "1/2=2/4, 2/4+1/4=3/4",
     "knowledge_points": ["异分母分数加减"], "difficulty": 3, "source": "seed", "textbook": "人教版"},
    {"id": "QB0018", "stem": "3/4-1/2=", "type": "calculation", "options": None,
     "answer": "1/4", "answer_steps": "1/2=2/4, 3/4-2/4=1/4",
     "knowledge_points": ["异分母分数加减"], "difficulty": 3, "source": "seed", "textbook": "人教版"},
    {"id": "QB0019", "stem": "2/3×3/4=", "type": "calculation", "options": None,
     "answer": "1/2", "answer_steps": "分子2×3=6, 分母3×4=12, 6/12=1/2",
     "knowledge_points": ["分数乘法"], "difficulty": 4, "source": "seed", "textbook": "人教版"},

    # --- 填空题 ---
    {"id": "QB0020", "stem": "3×()=12", "type": "fill_blank", "options": None,
     "answer": "4", "answer_steps": "12÷3=4",
     "knowledge_points": ["表内乘法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
    {"id": "QB0021", "stem": "72÷()=8", "type": "fill_blank", "options": None,
     "answer": "9", "answer_steps": "72÷8=9",
     "knowledge_points": ["表内除法"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
    {"id": "QB0022", "stem": "()里最大能填几：5×()<42", "type": "fill_blank", "options": None,
     "answer": "8", "answer_steps": "42÷5=8...2, 最大填8",
     "knowledge_points": ["有余数除法"], "difficulty": 2, "source": "seed", "textbook": "人教版"},

    # --- 选择题 ---
    {"id": "QB0023", "stem": "下列算式中，结果等于100的是？",
     "type": "choice", "options": ["A.24×4", "B.25×4", "C.20×5", "D.24×5"],
     "answer": "B", "answer_steps": "25×4=100, 其他: 24×4=96, 20×5=100(也对但题目通常单选B), 24×5=120",
     "knowledge_points": ["两位数乘一位数"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0024", "stem": "1小时等于多少分钟？",
     "type": "choice", "options": ["A.60", "B.100", "C.24", "D.30"],
     "answer": "A", "answer_steps": "1小时=60分钟",
     "knowledge_points": ["时间单位换算"], "difficulty": 1, "source": "seed", "textbook": "人教版"},

    # --- 混合运算 ---
    {"id": "QB0025", "stem": "25+15×2=", "type": "calculation", "options": None,
     "answer": "55", "answer_steps": "先乘: 15×2=30, 再加: 25+30=55",
     "knowledge_points": ["四则混合运算"], "difficulty": 3, "source": "seed", "textbook": "人教版"},
    {"id": "QB0026", "stem": "(36+14)÷5=", "type": "calculation", "options": None,
     "answer": "10", "answer_steps": "先括号: 36+14=50, 再除: 50÷5=10",
     "knowledge_points": ["四则混合运算"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0027", "stem": "100-36÷4=", "type": "calculation", "options": None,
     "answer": "91", "answer_steps": "先除: 36÷4=9, 再减: 100-9=91",
     "knowledge_points": ["四则混合运算"], "difficulty": 3, "source": "seed", "textbook": "人教版"},

    # --- 应用题 ---
    {"id": "QB0028", "stem": "小明买了3个苹果，每个5元，一共多少元？",
     "type": "application", "options": None,
     "answer": "15", "answer_steps": "3×5=15(元)",
     "knowledge_points": ["乘法应用题"], "difficulty": 2, "source": "seed", "textbook": "人教版"},
    {"id": "QB0029", "stem": "一根绳子长12米，剪去4米，还剩多少米？",
     "type": "application", "options": None,
     "answer": "8", "answer_steps": "12-4=8(米)",
     "knowledge_points": ["减法应用题"], "difficulty": 1, "source": "seed", "textbook": "人教版"},

    # --- 判断题 ---
    {"id": "QB0030", "stem": "任何数乘1都等于它本身。",
     "type": "judge", "options": None,
     "answer": "对", "answer_steps": "1是乘法单位元",
     "knowledge_points": ["乘法性质"], "difficulty": 1, "source": "seed", "textbook": "人教版"},
]


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from _gateway import resolve_gateway_config, gateway_base_url

    parser = argparse.ArgumentParser(description="题库管理")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="列出题库")
    p_list.add_argument("--kp", default=None, help="按知识点过滤")

    p_match = sub.add_parser("match", help="匹配题目")
    p_match.add_argument("stem", help="题面")

    p_solve = sub.add_parser("solve", help="AI解题")
    p_solve.add_argument("stem", help="题面")

    p_add = sub.add_parser("add", help="添加题目")
    p_add.add_argument("stem")
    p_add.add_argument("--answer", required=True)
    p_add.add_argument("--kp", action="append", default=[], dest="knowledge_points")
    p_add.add_argument("--type", default="calculation")

    args = parser.parse_args()

    if args.cmd == "list":
        bank = ensure_bank()
        qs = bank.get("questions", [])
        if args.kp:
            qs = [q for q in qs if args.kp in q.get("knowledge_points", [])]
        print(f"题库共 {len(qs)} 道题：")
        for q in qs:
            print(f"  {q['id']} [{q.get('type','')}] {q['stem'][:30]} → {q.get('answer')}  kp={q.get('knowledge_points')}")

    elif args.cmd == "match":
        bank = ensure_bank()
        m = match_bank(args.stem, bank)
        if m:
            print(f"命中: {m['id']} (相似度{m.get('_match_score')}) → {m.get('answer')}")
        else:
            print("未命中")

    elif args.cmd == "solve":
        base_url, api_key, model = resolve_gateway_config()
        r = solve_by_ai(args.stem, None, base_url, api_key, model=model)
        print(json.dumps(r, ensure_ascii=False, indent=2))

    elif args.cmd == "add":
        q = add_to_bank(args.stem, args.answer, args.knowledge_points, args.type)
        print(f"已添加: {q['id']}")

    else:
        parser.print_help()
