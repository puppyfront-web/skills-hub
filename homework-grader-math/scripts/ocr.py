"""
作业图片识别模块。

输入：作业图片路径
输出：结构化题目列表（题面、题型、学生作答、识别置信度）

调用视觉大模型，用一份精心设计的提示词识别小学数学作业。
提示词内嵌完整 JSON schema，要求模型"只输出 JSON"。
识别结果中每道题带置信度，低于阈值的会被标记为需教师复核。
"""

import json
import sys
from pathlib import Path

# 让同目录脚本可互相 import
sys.path.insert(0, str(Path(__file__).parent))
from _gateway import (
    call_with_retry, extract_json_from_text, is_blind_response,
    VisionNotSupportedError, VisionUnavailableError,
)

# ---------------------------------------------------------------------------
# 识别提示词
# ---------------------------------------------------------------------------

RECOGNIZE_PROMPT = """分析这张小学数学作业图片，识别出每一道题目和学生的手写作答。

【识别要求】
1. 识别每一道题的**题面**（印刷或手写的题目内容，如 "25 × 4 ="、"3.5 + 2.8 ="）
2. 识别学生的**作答**（手写的答案，如 "= 100"、"= 6.3"）
3. 判断**题型**：calculation(计算题) / fill_blank(填空题) / choice(选择题) / judge(判断题) / application(应用题)
4. 选择题要识别选项（如 A.24×4 B.25×4），并标注学生选了哪个
5. 如果题面有图形（几何图形、统计图等），在 needs_review 中标注 "图形题需手动录入"
6. 作答区域如果看不清、被涂抹、或学生未作答，student_answer 填 null，confidence 填 "low"

【题型判断规则】
- calculation：单纯算式，如 "25 × 4 ="、"3/4 + 1/4 ="
- fill_blank：有括号或横线待填，如 "3 × ( ) = 12"、"72 ÷ 9 = ___"
- choice：有 ABCD 选项
- judge：题目要求判断对错/正误
- application：有文字描述的实际应用题

【输出格式】
输出 JSON，严格遵循以下 schema：
{
  "questions": [
    {
      "question_no": 1,
      "type": "calculation | fill_blank | choice | judge | application",
      "stem": "题面原文（如 '25 × 4 ='），去掉学生作答部分",
      "options": ["A.24×4", "B.25×4", ...] 或 null（非选择题填null）,
      "student_answer": "学生作答原文（如 '100' 或 'B' 或 null）",
      "bbox": [x1, y1, x2, y2],
      "confidence": "high | medium | low",
      "notes": "识别备注（如 '字迹潦草'、'学生未作答'、'图形题'）或 null"
    }
  ],
  "overall_confidence": "high | medium | low",
  "needs_review": ["需要教师复核的题号或问题描述"],
  "raw_text_notes": "整页作业的整体观察说明"
}

【bbox 坐标规则（重要）】
每道题必须标注其在图片中的位置框 bbox:
- 格式：[x1, y1, x2, y2]，分别是左上角和右下角的坐标
- 坐标系：**归一化到 0-1**（左上角原点，x 向右增，y 向下增）
  例：图左上角的题 bbox 约为 [0.05, 0.05, 0.4, 0.1]；图中间偏右的题约为 [0.5, 0.5, 0.9, 0.55]
- bbox 应覆盖这道题的题面+学生作答区域（含等号后的答案）
- 用于后续在原图上画批注符号，必须尽量准确

【关键规则】
1. 按题目在图片中的**从上到下、从左到右**顺序编号
2. **题面和作答要分开**：题面是题目本身，作答是学生写的答案。例如 "25 × 4 = 100"，题面是 "25 × 4 ="，作答是 "100"
3. 数字、运算符号（+ − × ÷ = ＜ ＞）要准确识别，尤其区分：× 和 x、÷ 和 +、1 和 7、0 和 6
4. 分数写成 a/b 形式（如 3/4），带分数写成 a b/c（如 2 1/3）
5. 小数点要准确（3.5 不要识别成 35）
6. 如果学生作答被涂改，取最终可见的答案
7. confidence 判断标准：
   - high：题面和作答都清晰可辨
   - medium：有一处不太确定
   - low：多处模糊或学生未作答

【应用题识别规则（非常重要）】
应用题的题面必须**完整识别所有条件**，否则无法批改：
- 所有数字条件都要包含：单价、数量、总价、时间、距离、速度等
  例："用36元可以买几个茶杯？每个9元" —— "每个9元"是解题必需条件，不能丢
- 题目编号（①②③、(1)(2)）要保留
- 如果应用题配有表格/图例（如商品价格表），把表格内容也写进 stem
- 如果一道应用题分多行排版，把所有行合并成完整题面，不要拆成多道题
- 应用题的 confidence 要保守：条件多、文字长，容易漏，默认 medium

【共享条件规则（非常重要，常见错误）】
多道应用题常**共享同一个条件**（如商品价格表、路程信息）。每道题的题面都必须包含它解题所需的全部条件，**不能依赖其他题的题面**：
- 例：题目有价格表"手套8元，毛巾5元，茶杯9元，帽子6元"，然后问：
  (1) 买6块毛巾多少钱？
  (2) 用36元可以买几个茶杯？
  (3) 32元可以买几双手套？
  → **每道题的 stem 都要带上价格表**，不能只写"(2)用36元可以买几个茶杯？"（缺了"茶杯9元"就解不出）
  → 正确做法：(2) 的 stem 应为"手套8元，毛巾5元，茶杯9元，帽子6元。用36元可以买几个茶杯？"
- 同理，共享路程/时间/人数等背景信息的多道题，每题都要带完整背景

只输出 JSON，不要输出其他内容。"""


# ---------------------------------------------------------------------------
# 识别主流程
# ---------------------------------------------------------------------------

def recognize_image(image_path: str, base_url: str, token: str,
                    model: str = "openclaw/default",
                    timeout: int = 90) -> dict:
    """识别一张作业图片，返回结构化题目列表。

    返回 schema 同 RECOGNIZE_PROMPT。
    - 若视觉模型明确不支持图片 → 抛 VisionNotSupportedError
    - 若视觉服务不稳定（超时/断开）→ 抛 VisionUnavailableError（可重试/换模型）
    """
    try:
        # 视觉请求重试 1 次即可（服务不稳定时多重试只是让老师干等）
        resp = call_with_retry(
            base_url, token, RECOGNIZE_PROMPT,
            image_path=image_path, model=model, timeout=timeout, retries=1,
        )
    except VisionNotSupportedError:
        raise
    except Exception as e:
        # 区分错误类型，给上层更精准的兜底信息
        raise VisionUnavailableError(
            f"视觉识别请求失败（服务可能不稳定或超时）: {type(e).__name__}: {e}"
        ) from e

    # 模型说看不到图片
    if is_blind_response(resp):
        raise VisionNotSupportedError("模型回复看不到图片，可能当前模型通道不支持视觉。")

    # 解析 JSON
    try:
        result = extract_json_from_text(resp)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"模型回复无法解析为 JSON: {e}\n原始回复前 500 字: {resp[:500]}") from e

    # 基本校验
    if "questions" not in result:
        raise RuntimeError(f"识别结果缺少 questions 字段: {result}")

    # 给每道题补一个内部 id
    for i, q in enumerate(result["questions"]):
        q.setdefault("question_no", i + 1)
        q.setdefault("type", "calculation")
        q.setdefault("stem", "")
        q.setdefault("options", None)
        q.setdefault("student_answer", None)
        q.setdefault("bbox", None)  # 归一化坐标 [x1,y1,x2,y2]，用于原图批注
        q.setdefault("confidence", "medium")
        q.setdefault("notes", None)
        q["internal_id"] = f"Q{i + 1:03d}"

    return result


# ---------------------------------------------------------------------------
# 仅识别作答（多学生共享题面场景）
# ---------------------------------------------------------------------------

ANSWERS_ONLY_PROMPT_TEMPLATE = """这是另一位学生的作业（和题面模板是同一套题）。
请只识别这位学生的**作答**，不重新识别题面。

【题面模板】（题号、题面已固定）
{template}

【你的任务】
对照上面的题面模板，识别这张图里每位学生每一道题的作答，并给出每题的 bbox。
- 题号必须和模板一一对应（第1题对模板第1题）
- 只填 student_answer 和 bbox，不要改 type/stem/options
- 学生未作答的题，student_answer 填 null
- bbox 是这道题在图片中的位置框 [x1,y1,x2,y2]，归一化到 0-1（左上角原点）

【输出格式】
输出 JSON：
{{
  "answers": [
    {{
      "question_no": 1,
      "student_answer": "学生作答" 或 null,
      "bbox": [x1, y1, x2, y2]
    }},
    ...
  ],
  "overall_confidence": "high | medium | low"
}}

题号必须覆盖模板里的每一道题。只输出 JSON，不要其他内容。"""


def recognize_answers_only(image_path: str, template_questions: list,
                           base_url: str, token: str,
                           model: str = "openclaw/default",
                           timeout: int = 90) -> dict:
    """给定题面模板，仅识别另一位学生的作答。

    用于"多学生同一套题"场景：第一张图完整识别作为模板，后续学生用此函数
    只回填 student_answer，省 token、提速、避免题面漂移。

    返回 {"answers": [{"question_no","student_answer","bbox"}], "overall_confidence"}
    """
    # 构造模板描述
    tmpl_lines = []
    for q in template_questions:
        opts = f" 选项:{q.get('options')}" if q.get("options") else ""
        tmpl_lines.append(f"第{q.get('question_no')}题 [{q.get('type','')}] {q.get('stem','')}{opts}")
    template_text = "\n".join(tmpl_lines)

    prompt = ANSWERS_ONLY_PROMPT_TEMPLATE.format(template=template_text)

    try:
        resp = call_with_retry(
            base_url, token, prompt,
            image_path=image_path, model=model, timeout=timeout, retries=1,
        )
    except VisionNotSupportedError:
        raise
    except Exception as e:
        raise VisionUnavailableError(
            f"仅识别作答请求失败: {type(e).__name__}: {e}"
        ) from e

    if is_blind_response(resp):
        raise VisionUnavailableError("模型回复看不到图片")

    try:
        result = extract_json_from_text(resp)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"仅识别作答回复无法解析为 JSON: {e}\n前500字: {resp[:500]}") from e

    result.setdefault("answers", [])
    result.setdefault("overall_confidence", "medium")
    # 按 question_no 排序，补齐缺失题号
    by_no = {a.get("question_no"): a for a in result["answers"]}
    for q in template_questions:
        qno = q.get("question_no")
        if qno not in by_no:
            result["answers"].append({"question_no": qno, "student_answer": None, "bbox": None})
    result["answers"].sort(key=lambda a: a.get("question_no", 0))
    return result


def low_confidence_questions(result: dict, threshold: str = "medium") -> list:
    """返回置信度低于阈值（不含）的题目，用于提示教师复核。

    threshold: "high" → 返回 medium 和 low 的
               "medium" → 返回 low 的（默认）
    """
    order = {"low": 0, "medium": 1, "high": 2}
    thresh = order.get(threshold, 1)
    return [q for q in result.get("questions", [])
            if order.get(q.get("confidence", "medium"), 1) < thresh]


def format_recognition_review(result: dict) -> str:
    """把识别结果格式化成给老师看的校对文本（纯文本表格）。

    老师在这一步校对识别是否正确，可修改后再进入批改。
    """
    lines = []
    lines.append(f"识别到 {len(result.get('questions', []))} 道题：")
    lines.append("")
    lines.append(f"{'题号':<4} {'题型':<12} {'题面':<24} {'学生作答':<12} {'置信度':<8}")
    lines.append("-" * 70)
    for q in result.get("questions", []):
        no = str(q.get("question_no", ""))
        qtype = q.get("type", "")
        stem = (q.get("stem", "") or "")[:22]
        ans = (q.get("student_answer") or "(未作答)")[:10]
        conf = q.get("confidence", "")
        mark = " ⚠️" if conf in ("low", "medium") else ""
        lines.append(f"{no:<4} {qtype:<12} {stem:<24} {ans:<12} {conf:<8}{mark}")

    # 需复核的
    needs = result.get("needs_review", [])
    low_qs = low_confidence_questions(result, "medium")
    if low_qs or needs:
        lines.append("")
        lines.append("需要你重点看一下的：")
        for q in low_qs:
            lines.append(f"  • 第{q.get('question_no')}题：置信度{q.get('confidence')}，"
                         f"题面「{q.get('stem','')}」，作答「{q.get('student_answer') or '未作答'}」"
                         + (f"（{q.get('notes')}）" if q.get("notes") else ""))
        for n in needs:
            lines.append(f"  • {n}")

    notes = result.get("raw_text_notes")
    if notes:
        lines.append("")
        lines.append(f"整体说明：{notes}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 自检与命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from _gateway import resolve_gateway_config, check_vision_support

    parser = argparse.ArgumentParser(description="作业图片识别")
    parser.add_argument("image", help="作业图片路径")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--self-check", action="store_true", help="先自检视觉能力")
    args = parser.parse_args()

    base_url, api_key, model = resolve_gateway_config(
        host=args.host, port=args.port, token=args.token, model=args.model,
    )

    if args.self_check:
        ok = check_vision_support(base_url, api_key, model)
        print(f"视觉能力自检: {'通过' if ok else '不通过'}", file=sys.stderr)
        if not ok:
            print("当前模型通道不支持看图，无法识别作业。", file=sys.stderr)
            sys.exit(1)

    result = recognize_image(args.image, base_url, api_key, model)
    print(json.dumps(result, ensure_ascii=False, indent=2))
