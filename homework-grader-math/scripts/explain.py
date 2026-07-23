"""
错因分类 + 点评 + 解析生成模块。

设计原则（参考 gaokao-advisor 的 LLM 边界守卫）：
- 对错判定由 compare.py 确定性计算（不调 LLM）
- 错因分类先用规则推断（粗心/概念/计算），规则覆盖不了再调 LLM
- 点评和解析由 LLM 生成，但语气要符合小学语境，鼓励式为主
- 有题库答案步骤时优先用，LLM 只做润色

错因类型：
- careless（粗心）：方法对，抄错数/看错符号
- conceptual（概念错）：方法/思路错，不理解知识点
- calculation（计算错）：方法对，运算过程错（进位错、口诀错）
- empty（未作答）：学生没写
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gateway import call_with_retry, extract_json_from_text

# ---------------------------------------------------------------------------
# 规则推断错因（不调 LLM，快速且确定）
# ---------------------------------------------------------------------------

def classify_error_by_rule(stem: str, student_answer: str | None,
                           correct_answer: str | None,
                           answer_steps: str | None = None) -> str:
    """用规则推断错因类型。覆盖不了返回 "unknown"（交给 LLM）。"""
    if not student_answer or not str(student_answer).strip():
        return "empty"

    if not correct_answer:
        return "unknown"

    sa = str(student_answer).strip()
    ca = str(correct_answer).strip()

    # 数字位数差异大 → 可能抄错数（粗心）
    sa_digits = re.sub(r"\D", "", sa)
    ca_digits = re.sub(r"\D", "", ca)
    if sa_digits and ca_digits:
        # 位数相同但数字接近（差 1-2 位）→ 计算错
        if len(sa_digits) == len(ca_digits):
            diff = sum(1 for a, b in zip(sa_digits, ca_digits) if a != b)
            if diff <= 2:
                return "calculation"
        # 位数不同 → 可能抄错题或概念错
        if abs(len(sa_digits) - len(ca_digits)) >= 2:
            return "conceptual"

    # 运算符看反（+看成-）：答案符号相反
    try:
        sn = float(sa)
        cn = float(ca)
        if sn * cn < 0 and abs(abs(sn) - abs(cn)) < 0.01:
            return "careless"
    except ValueError:
        pass

    return "unknown"


# ---------------------------------------------------------------------------
# LLM 错因分析 + 点评 + 解析
# ---------------------------------------------------------------------------

EXPLAIN_PROMPT_TEMPLATE = """你是小学数学老师助手。下面是一道错题，请分析错因并生成点评和解析。

题目：{stem}
学生作答：{student_answer}
正确答案：{correct_answer}
解题步骤：{answer_steps}
知识点：{knowledge_points}
规则推断的错因：{rule_error_type}

【错因分类】从以下四类中选一个：
- careless（粗心）：方法思路都对，只是抄错数、看错运算符号、写错数字
- conceptual（概念错）：方法或思路错了，没理解知识点
- calculation（计算错）：方法对，但运算过程错了（进位错、乘法口诀记错、加减算错）
- empty（未作答）：学生没写答案

【输出格式】
输出 JSON：
{{
  "error_type": "careless | conceptual | calculation | empty",
  "comment": "给学生的点评，2-3句话，鼓励式为主，符合小学生理解水平",
  "solution": "题目解析，分步骤说明正确解法，通俗易懂"
}}

【规则】
1. comment 语气要亲切，像老师跟学生说话，不要打击（不要说"太笨了""怎么这都不会"）
2. comment 要指出**具体错在哪**（如"乘法口诀记成了七八五十四，应该是七八五十六"）
3. solution 要分步骤，每步说清楚算什么
4. 如果规则推断的错因是 unknown，请你根据题目和答案判断一个最可能的类型
5. 如果学生作答为空，error_type 填 empty，comment 鼓励学生动笔尝试

只输出 JSON，不要输出其他内容。"""


def explain_mistake(stem: str, student_answer: str | None, correct_answer: str,
                    knowledge_points: list, answer_steps: str | None,
                    rule_error_type: str, base_url: str, token: str,
                    model: str = "openclaw/default") -> dict:
    """对一道错题生成错因分析 + 点评 + 解析。

    返回 {error_type, comment, solution}
    """
    # 规则已确定的（empty），不调 LLM，直接返回模板
    if rule_error_type == "empty":
        return {
            "error_type": "empty",
            "comment": f"这道题你还没有写答案哦，再读一遍题目试试看～",
            "solution": _build_solution_from_steps(stem, correct_answer, answer_steps),
        }

    # 调 LLM 做详细分析
    kp = "、".join(knowledge_points) if knowledge_points else "未标注"
    prompt = EXPLAIN_PROMPT_TEMPLATE.format(
        stem=stem,
        student_answer=student_answer or "(未作答)",
        correct_answer=correct_answer,
        answer_steps=answer_steps or "(无)",
        knowledge_points=kp,
        rule_error_type=rule_error_type,
    )

    try:
        resp = call_with_retry(base_url, token, prompt, model=model)
        result = extract_json_from_text(resp)
        result.setdefault("error_type", rule_error_type)
        result.setdefault("comment", "")
        result.setdefault("solution", "")
        return result
    except Exception as e:
        # LLM 失败，用规则兜底
        return {
            "error_type": rule_error_type if rule_error_type != "unknown" else "calculation",
            "comment": f"这题做错了，再仔细看看～",
            "solution": _build_solution_from_steps(stem, correct_answer, answer_steps),
            "_llm_error": str(e),
        }


def _build_solution_from_steps(stem: str, correct_answer: str,
                               answer_steps: str | None) -> str:
    """从已有解题步骤拼一个解析（LLM 失败时兜底）。"""
    if answer_steps:
        return f"正确答案：{correct_answer}\n解题步骤：{answer_steps}"
    return f"正确答案：{correct_answer}"


# ---------------------------------------------------------------------------
# 错因中文名
# ---------------------------------------------------------------------------

ERROR_TYPE_LABELS = {
    "careless": "粗心错",
    "conceptual": "概念错",
    "calculation": "计算错",
    "empty": "未作答",
    "unknown": "待分析",
    "undetermined": "无法判定",
}


def error_type_label(t: str) -> str:
    return ERROR_TYPE_LABELS.get(t, t)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from _gateway import resolve_gateway_config, gateway_base_url

    parser = argparse.ArgumentParser(description="错题解析")
    parser.add_argument("stem", help="题面")
    parser.add_argument("--student", required=True, help="学生作答")
    parser.add_argument("--correct", required=True, help="正确答案")
    parser.add_argument("--steps", default=None, help="解题步骤")
    parser.add_argument("--kp", action="append", default=[], dest="knowledge_points")
    args = parser.parse_args()

    rule_type = classify_error_by_rule(args.stem, args.student, args.correct, args.steps)
    print(f"规则推断错因: {rule_type}", file=sys.stderr)

    base_url, api_key, model = resolve_gateway_config()
    r = explain_mistake(
        args.stem, args.student, args.correct,
        args.knowledge_points, args.steps, rule_type,
        base_url, api_key, model=model,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2))
