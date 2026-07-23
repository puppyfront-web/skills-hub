"""
OCR 兜底识别模块：视觉模型失败时，用 PaddleOCR 提取文本 + LLM 结构化。

流程：
  PaddleOCR 提取文本行（本地，无网络）
      ↓
  文本 LLM 把噪声文本结构化成题目列表（云端，纯文本请求，比视觉请求稳定得多）
      ↓
  输出与 ocr.recognize_image 相同的 schema

为什么需要这一层：
- 视觉模型有时不稳定（超时/限流/中转站问题），但文本模型通常很稳定
- PaddleOCR 能离线识别手写体，但输出的文本行有粘连、噪声、错序
- 用文本 LLM 做二次结构化，能修正 OCR 的粘连问题，且文本请求比视觉请求快很多

设计参考：invoice-ocr 技能的 EXTRACT_TEXT_PROMPT 模式。
"""

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gateway import call_with_retry, extract_json_from_text

# ---------------------------------------------------------------------------
# PaddleOCR 引擎懒加载单例
# ---------------------------------------------------------------------------

_paddle_engine = None
_engine_lock = threading.Lock()


def _create_engine():
    """创建 PaddleOCR 引擎，兼容 v2/v3 API。"""
    from paddleocr import PaddleOCR
    try:
        return PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
    except Exception as e:
        if "show_log" in str(e) or "Unknown argument" in str(e):
            # v3 不再支持 show_log 参数
            return PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
            )
        raise


def _get_engine():
    global _paddle_engine
    if _paddle_engine is None:
        with _engine_lock:
            if _paddle_engine is None:
                _paddle_engine = _create_engine()
    return _paddle_engine


def is_available() -> bool:
    """检查 PaddleOCR 是否可用（依赖是否安装）。"""
    try:
        import paddleocr  # noqa: F401
        import paddle  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 文本行提取
# ---------------------------------------------------------------------------

def _collect_lines(result, min_score: float = 0.3) -> list:
    """把 PaddleOCR 的结果归一化成 [(text, score), ...] 文本行列表。

    兼容 v2（list of list）和 v3（dict with rec_texts）两种返回格式。
    """
    lines = []
    for page in result or []:
        # v3 格式：dict with rec_texts / rec_scores
        if isinstance(page, dict) and "rec_texts" in page:
            scores = page.get("rec_scores") or []
            for idx, text in enumerate(page.get("rec_texts") or []):
                score = scores[idx] if idx < len(scores) else 1
                if text and float(score) > min_score:
                    lines.append((str(text), round(float(score), 2)))
            continue
        # v2 格式：list of [box, (text, score)]
        if isinstance(page, list):
            for item in page:
                if (isinstance(item, (list, tuple))
                        and len(item) > 1
                        and isinstance(item[1], (list, tuple))
                        and len(item[1]) > 1
                        and float(item[1][1]) > min_score):
                    lines.append((str(item[1][0]), round(float(item[1][1]), 2)))
    return lines


def extract_text(image_path: str) -> tuple:
    """用 PaddleOCR 提取图片中的文本。

    返回 (text_lines, detail)：
    - text_lines: list of str，每行一个识别出的文本
    - detail: dict，含 line_count、耗时等调试信息
    识别失败返回 ([], {"error": "..."})。
    """
    if not is_available():
        return [], {"error": "PaddleOCR 未安装，无法走 OCR 兜底。安装：pip install paddleocr paddlepaddle"}

    import time
    t0 = time.time()
    try:
        engine = _get_engine()
    except Exception as e:
        return [], {"error": f"PaddleOCR 引擎初始化失败: {e}"}

    try:
        if hasattr(engine, "predict"):
            result = engine.predict(image_path)
        else:
            result = engine.ocr(image_path, cls=True)
    except Exception as e:
        return [], {"error": f"PaddleOCR 识别失败: {e}"}

    elapsed = round(time.time() - t0, 1)
    scored_lines = _collect_lines(result)
    text_lines = [t for t, _ in scored_lines]
    return text_lines, {
        "line_count": len(text_lines),
        "elapsed_seconds": elapsed,
        "raw_lines": scored_lines,  # 带置信度，供 LLM 参考
    }


# ---------------------------------------------------------------------------
# LLM 结构化：把 OCR 文本变成题目列表
# ---------------------------------------------------------------------------

STRUCTURE_PROMPT_TEMPLATE = """下面是 OCR 从一张小学数学作业图片里识别出的文本行。
OCR 识别可能有错：同行多题粘连、数字混淆、顺序错乱、混入题干说明文字等。
请你根据这些文本，还原出图片里每一道**题目**和学生的**作答**。

OCR 识别出的文本行（按出现顺序）：
---
{ocr_text}
---

【你的任务】
1. 把粘连的行拆成单独的题（如 "9÷9=172÷8=9" 其实是 "9÷9=1" 和 "72÷8=9" 两道题）
2. 区分**题面**和**学生作答**：题面是题目本身（如 "9÷9="），作答是学生写的答案（如 "1"）
3. 过滤掉非题目内容（如页眉"年月日"、说明性文字"答：再过5个星期..."保留为应用题作答）
4. 按题号顺序（从上到下、从左到右）排列
5. 修正明显的 OCR 错误（如 ÷ 识别成 +、数字混淆），但要保守——不确定的标 low confidence

【输出格式】
输出 JSON，严格遵循以下 schema：
{{
  "questions": [
    {{
      "question_no": 1,
      "type": "calculation | fill_blank | choice | judge | application",
      "stem": "题面（如 '9÷9='）",
      "options": ["A. ...", "B. ..."] 或 null,
      "student_answer": "学生作答（如 '1'）" 或 null,
      "bbox": null,
      "confidence": "high | medium | low",
      "notes": "备注（如 'OCR粘连已拆分'、'数字可能识别错'）或 null"
    }}
  ],
  "overall_confidence": "high | medium | low",
  "needs_review": ["需要教师复核的题号或问题"],
  "raw_text_notes": "OCR 文本整体观察说明",
  "ocr_corrected": ["你修正过的 OCR 错误，如 '172÷8=9 → 拆分为 9÷9=1 和 72÷8=9'"]
}}

【规则】
1. type 判断：纯算式→calculation；有括号待填→fill_blank；有ABCD→choice；判断对错→judge；文字描述实际问题→application
2. 应用题：题干（描述性文字）放 stem，学生的算式和"答：..."放 student_answer
3. 如果一行明显是"答：xxx"，把它归到对应应用题的 student_answer
4. 走迷宫、连线等特殊题型：能识别的算式都当作 calculation 题
5. confidence：OCR 清晰、拆分有把握→high；有不确定→medium；严重粘连或模糊→low

只输出 JSON，不要输出其他内容。"""


def structure_with_llm(text_lines: list, base_url: str, token: str,
                       model: str = "openclaw/default") -> dict:
    """用文本 LLM 把 OCR 文本结构化成题目列表。

    返回与 ocr.recognize_image 相同 schema 的 dict。
    """
    # 拼接 OCR 文本（带行号，帮 LLM 理解结构）
    numbered = "\n".join(f"{i+1}. {line}" for i, line in enumerate(text_lines))
    prompt = STRUCTURE_PROMPT_TEMPLATE.format(ocr_text=numbered)

    resp = call_with_retry(base_url, token, prompt, model=model)
    try:
        result = extract_json_from_text(resp)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"OCR 文本结构化失败（LLM 回复无法解析为 JSON）: {e}\n"
            f"OCR 原始文本前 300 字: {' | '.join(text_lines)[:300]}"
        ) from e

    # 补字段 + 给每道题加 internal_id
    result.setdefault("questions", [])
    result.setdefault("overall_confidence", "medium")
    result.setdefault("needs_review", [])
    result.setdefault("raw_text_notes", "")
    result.setdefault("ocr_corrected", [])
    result["_source"] = "ocr_fallback"  # 标记来源，便于报告里区分

    for i, q in enumerate(result["questions"]):
        q.setdefault("question_no", i + 1)
        q.setdefault("type", "calculation")
        q.setdefault("stem", "")
        q.setdefault("options", None)
        q.setdefault("student_answer", None)
        q.setdefault("bbox", None)  # OCR 兜底无法给出准确坐标
        q.setdefault("confidence", "medium")
        q.setdefault("notes", None)
        q["internal_id"] = f"Q{i + 1:03d}"

    return result


# ---------------------------------------------------------------------------
# 离线规则解析（LLM 不可用时的最后兜底）
# ---------------------------------------------------------------------------

import re as _re

# 匹配完整的算式（含等号和结果），如 "9÷9=1"、"42÷6=7"、"6×5=30（元)"
# 支持粘连："9÷9=172÷8=9" 会拆成两道
_CALC_PATTERN = _re.compile(r'(\d+(?:\.\d+)?(?:\s*[/÷×*+\-]\s*\d+(?:\.\d+)?)*)\s*=\s*(\d+(?:\.\d+)?(?:/\d+)?)')
# 带单位的算式结果，如 "35÷7=5(个)"、"6×5=30（元)"
_CALC_WITH_UNIT = _re.compile(r'(\d+(?:\.\d+)?\s*[÷×*+\-/]\s*\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)(?:[（(]([^)）]*)[)）])?')


def _calc_is_valid(a: int, op: str, b: int, c: int) -> bool:
    """校验算式 a op b = c 是否数学正确。用于过滤 OCR 粘连导致的错拆。

    小学数学范围：非负整数，除法必须整除。
    """
    try:
        if op in ("÷", "/"):
            if b == 0:
                return False
            return a % b == 0 and a // b == c
        elif op in ("×", "*"):
            return a * b == c
        elif op == "+":
            return a + b == c
        elif op == "-":
            return a - b == c
    except (ValueError, TypeError):
        return False
    return False


def structure_by_rules(text_lines: list) -> dict:
    """纯离线规则解析：从 OCR 文本拆分题目，不依赖 LLM。

    策略：
    1. 抽计算题：用正则匹配"算式=结果"，自动拆分粘连行
    2. 抽应用题：用"答：xxx"反向关联题干

    局限：计算题解析准确；应用题条件可能不全，标 low confidence。
    """
    full_text = "\n".join(text_lines)
    questions = []
    qno = 0

    # === 第1步：抽计算题（核心，小学数学主力）===
    # 策略：只处理"清晰的独立算式行"，保守优先（宁缺毋滥）
    # - "42÷6=7" 这种独立的 → 识别
    # - "9÷9=172÷8=9" 这种粘连的 → 标记为需老师校对，不强行拆（避免拆错）
    #
    # 清晰行特征：一行就是"A op B = C"（可能有空格分隔的多道，但每道独立）

    ops = "[÷×*+\-/]"
    # 清晰的独立算式：A op B = C，A/B/C 都是合理位数的小整数（小学数学一般 1-4 位）
    clean_unit = _re.compile(r'(?<![\d.])'  # 前面不能是数字/点（排除粘连）
                             r'(\d{1,3})\s*(' + ops + r')\s*(\d{1,3})\s*=\s*(\d{1,4})'
                             r'(?![\d])')  # 后面不能紧跟数字（排除粘连）

    needs_review_lines = []  # 记录粘连行，提示老师

    for line in text_lines:
        line_s = line.strip()
        if not line_s or line_s.startswith("答"):
            continue

        # 先判断这行是否有算式
        if not _re.search(r'\d+' + ops + r'\d+', line_s):
            continue

        # 找清晰算式
        matches = list(clean_unit.finditer(line_s))
        if matches:
            for m in matches:
                a, op, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
                op_norm = op.replace("*", "×").replace("/", "÷")
                # 数学正确性校验：只保留算得对的算式（过滤粘连导致的错拆）
                if not _calc_is_valid(int(a), op, int(b), int(c)):
                    continue
                qno += 1
                questions.append({
                    "question_no": qno,
                    "type": "calculation",
                    "stem": f"{a}{op_norm}{b}=",
                    "options": None,
                    "student_answer": c,
                    "bbox": None,
                    "confidence": "medium",
                    "notes": "OCR兜底+规则解析",
                    "internal_id": f"Q{qno:03d}",
                })
        else:
            # 有算式但匹配不上清晰模式 → 可能是粘连行，标记
            needs_review_lines.append(line_s)

    # === 第2步：抽应用题（用"答：xxx"反向找题干）===
    # 应用题特征：有"答：xxx"结论行，往前找题干
    for i, line in enumerate(text_lines):
        line_s = line.strip()
        if not (line_s.startswith("答") or line_s.startswith("答：")):
            continue
        # 提取"答"后面的作答
        answer_text = _re.sub(r'^答\s*[:：]?\s*', '', line_s)

        # 往前找题干（含应用题关键词的行）
        stem_parts = []
        for j in range(i - 1, max(i - 8, -1), -1):
            prev = text_lines[j].strip()
            if not prev or prev.startswith("答"):
                continue
            # 题干特征：含中文描述、含应用题关键词、不是纯算式行
            has_kw = any(kw in prev for kw in ["买", "几", "多少", "一共", "可以买", "可以分", "再过", "距离", "带", "绳子", "星期", "开幕", "段"])
            is_calc = bool(_re.fullmatch(r'[\d\s÷×*+\-/=.（）()元个段双块]+', prev))
            if has_kw and not is_calc:
                stem_parts.insert(0, prev)
            elif stem_parts and not is_calc:
                # 题干延续行
                stem_parts.insert(0, prev)
            if len(stem_parts) >= 3:
                break

        # 也从当前行及附近找算式作答（如"35÷7=5(个)"）
        calc_answer = None
        for j in range(max(i - 3, 0), min(i + 1, len(text_lines))):
            cm = _re.search(r'(\d+(?:\.\d+)?[÷×*+\-/]\d+(?:\.\d+)?)=(\d+(?:\.\d+)?)', text_lines[j])
            if cm:
                calc_answer = f"{cm.group(1)}={cm.group(2)}"
                break

        if stem_parts:
            stem = " ".join(stem_parts)[:200]
            full_answer = answer_text
            if calc_answer and calc_answer.replace("=","") not in full_answer:
                full_answer = f"{calc_answer}；{full_answer}"
            qno += 1
            questions.append({
                "question_no": qno,
                "type": "application",
                "stem": stem,
                "options": None,
                "student_answer": full_answer,
                "bbox": None,
                "confidence": "low",  # 应用题规则解析不可靠
                "notes": "OCR兜底+规则解析，应用题条件可能不全，建议核对",
                "internal_id": f"Q{qno:03d}",
            })

    needs_review_msgs = ["OCR兜底+规则解析，题目内容建议老师校对"]
    if needs_review_lines:
        needs_review_msgs.append(f"以下行含粘连算式未能自动拆分，请老师手动补录：")
        for nl in needs_review_lines[:5]:
            needs_review_msgs.append(f"  - {nl[:50]}")

    return {
        "questions": questions,
        "overall_confidence": "medium",
        "needs_review": needs_review_msgs,
        "raw_text_notes": f"共识别 {len(text_lines)} 行 OCR 文本，规则解析出 {len(questions)} 道题（保守模式，粘连行未拆）",
        "ocr_corrected": [],
        "_source": "ocr_fallback_rules",
    }


# ---------------------------------------------------------------------------
# 主入口：OCR 兜底识别
# ---------------------------------------------------------------------------

def recognize_via_ocr(image_path: str, base_url: str, token: str,
                      model: str = "openclaw/default",
                      prefer_llm: bool = True) -> dict:
    """视觉兜底：PaddleOCR 提取文本 + 结构化（LLM 优先，失败降级规则）。

    返回与 ocr.recognize_image 相同 schema 的 dict。
    - prefer_llm=True（默认）：先试 LLM 结构化，失败降级到离线规则
    - prefer_llm=False：直接用离线规则解析（完全不依赖网络）
    """
    if not is_available():
        raise RuntimeError(
            "OCR 兜底不可用：未安装 PaddleOCR。安装命令：pip install paddleocr paddlepaddle"
        )

    # 1. PaddleOCR 提取文本（离线）
    text_lines, detail = extract_text(image_path)
    if not text_lines:
        raise RuntimeError(f"PaddleOCR 未识别出任何文本: {detail.get('error', '未知错误')}")

    # 2. 结构化：LLM 优先，失败降级规则
    result = None
    if prefer_llm:
        try:
            result = structure_with_llm(text_lines, base_url, token, model)
            result["_structure_method"] = "llm"
        except Exception as llm_err:
            # LLM 失败，降级到离线规则解析
            result = structure_by_rules(text_lines)
            result["_structure_method"] = "rules"
            result["_llm_error"] = str(llm_err)[:200]

    if result is None:
        result = structure_by_rules(text_lines)
        result["_structure_method"] = "rules"

    result["_ocr_detail"] = detail
    return result
