#!/usr/bin/env python3
"""
小学数学作业批改 Agent —— 主入口。

编排全流程：
  识别(ocr) → 取标准答案(bank: 题库/AI) → 判定对错(compare) → 错题解析(explain) → 报告(report)

命令行子命令：
  grade <图片> [--student <名字>]   批改一次作业（主流程）
  recognize <图片>                  仅识别，不批改（给老师校对用）
  derive <题号>                     针对上次批改的某道错题出衍生题
  student <名字>                    查学生学情汇总
  intro                             自我介绍（首次使用）
  self-check                        视觉能力自检

输出约定（Agent 契约，参考 invoice-ocr）：
  --assistant-summary-json  输出 ASSISTANT_SUMMARY_JSON:{...} 前缀行
  --agent-mode              隐式启用 summary-json，输出干净 JSON
  summary 里的 user_message 直接转发给老师，suggested_replies 是建议回复
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gateway import (
    resolve_gateway_config, gateway_base_url, check_vision_support,
    VisionNotSupportedError, VisionUnavailableError,
)
import ocr
import bank
import compare
import explain
import report

# ---------------------------------------------------------------------------
# 上次批改缓存（供 derive 命令引用）
# ---------------------------------------------------------------------------

STATE_DIR = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math"
LAST_BATCH_PATH = STATE_DIR / "last-batch.json"


def save_last_batch(batch_report: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAST_BATCH_PATH, "w", encoding="utf-8") as f:
        json.dump(batch_report, f, ensure_ascii=False, indent=2)


def load_last_batch() -> dict | None:
    if not LAST_BATCH_PATH.is_file():
        return None
    try:
        with open(LAST_BATCH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# 批改主流程
# ---------------------------------------------------------------------------

def grade_homework(image_path: str, student_name: str,
                   base_url: str, token: str,
                   model: str = "openclaw/default",
                   allow_ocr_fallback: bool = True) -> dict:
    """批改一张作业图，返回完整报告。

    流程：识别(视觉优先) → grade_homework_from_recognition。
    视觉失败时，若 allow_ocr_fallback=True，自动降级到 PaddleOCR + LLM 结构化。
    """
    # 1. 识别：视觉模型优先
    recognition = None
    recognition_source = "vision"
    try:
        recognition = ocr.recognize_image(image_path, base_url, token, model)
    except (VisionNotSupportedError, VisionUnavailableError) as e:
        # 视觉失败，尝试 OCR 兜底
        if not allow_ocr_fallback:
            raise
        try:
            import ocr_fallback
            if not ocr_fallback.is_available():
                raise RuntimeError(
                    "视觉识别失败，且 PaddleOCR 未安装无法兜底。"
                    "安装 PaddleOCR 即可启用兜底：pip install paddleocr paddlepaddle"
                ) from e
            # 走 OCR 兜底
            recognition = ocr_fallback.recognize_via_ocr(image_path, base_url, token, model)
            recognition_source = "ocr_fallback"
        except Exception as fallback_err:
            # OCR 兜底也失败，抛出组合错误
            raise RuntimeError(
                f"视觉识别失败（{type(e).__name__}），OCR 兜底也失败（{fallback_err}）。"
                f"建议：重试、换视觉模型、或把题目打字发给我用文本方式批改。"
            ) from fallback_err

    questions_raw = recognition.get("questions", []) if recognition else []

    if not questions_raw:
        return {
            "batch_id": f"BATCH_ERROR",
            "error": "未识别到题目",
            "user_message": "这张作业图我没看出题目来，能不能重新拍一张清楚点的？",
        }

    batch_report = grade_homework_from_recognition(
        recognition, student_name, base_url, token, model, image_path=image_path,
    )
    # 标记识别来源（视觉 / OCR兜底），便于报告里说明
    batch_report["recognition_source"] = recognition_source
    return batch_report


def _grade_single_question(q: dict, base_url: str, token: str,
                           model: str = "openclaw/default") -> dict:
    """批改单道题：取标准答案 → 判定对错 → 错题解析。

    返回批改结果 dict。单题的 LLM 失败不抛异常（bank/explain 内部已兜底），
    保证一道题的问题不会影响其他题。
    """
    stem = q.get("stem", "")
    options = q.get("options")
    qtype = q.get("type", "calculation")
    student_answer = q.get("student_answer")

    # 取标准答案（题库优先，AI 兜底）
    answer_info = bank.resolve_answer(stem, options, qtype, base_url, token, model)
    correct_answer = answer_info.get("correct_answer")
    knowledge_points = answer_info.get("knowledge_points", [])
    answer_steps = answer_info.get("answer_steps")

    # 无法判定的情况：AI 解不出标准答案（题面缺条件/识别丢条件等）
    # 不强行判错，标为"无法判定"，提示老师看
    if correct_answer is None:
        return {
            "question_no": q.get("question_no"),
            "internal_id": q.get("internal_id"),
            "type": qtype,
            "stem": stem,
            "options": options,
            "student_answer": student_answer,
            "correct_answer": None,
            "is_correct": None,  # None 表示无法判定，区别于 True/False
            "error_type": "undetermined",
            "comment": "这题我没能确定标准答案（可能题面条件不全），你帮我看一下学生做得对不对。",
            "solution": None,
            "knowledge_points": knowledge_points,
            "source": answer_info.get("source"),
            "confidence": answer_info.get("confidence"),
            "need_review": True,
            "recognition_confidence": q.get("confidence"),
        }

    # 判定对错
    judge_result = compare.judge(student_answer, correct_answer)
    is_correct = judge_result["is_correct"]

    # 错题解析
    error_type = None
    comment = None
    solution = None
    if not is_correct:
        rule_error = explain.classify_error_by_rule(
            stem, student_answer, correct_answer, answer_steps
        )
        explain_result = explain.explain_mistake(
            stem, student_answer, correct_answer,
            knowledge_points, answer_steps, rule_error,
            base_url, token, model,
        )
        error_type = explain_result.get("error_type", rule_error)
        comment = explain_result.get("comment", "")
        solution = explain_result.get("solution", "")
    else:
        # 对的题也给个简短解析（可选，方便讲评）
        solution = answer_steps

    return {
        "question_no": q.get("question_no"),
        "internal_id": q.get("internal_id"),
        "type": qtype,
        "stem": stem,
        "options": options,
        "student_answer": student_answer,
        "correct_answer": correct_answer,
        "is_correct": is_correct,
        "error_type": error_type,
        "comment": comment,
        "solution": solution,
        "knowledge_points": knowledge_points,
        "source": answer_info.get("source"),
        "confidence": answer_info.get("confidence"),
        "need_review": answer_info.get("need_review") or judge_result["method"] == "unknown",
        "recognition_confidence": q.get("confidence"),
    }


def grade_homework_from_recognition(recognition: dict, student_name: str,
                                    base_url: str, token: str,
                                    model: str = "openclaw/default",
                                    image_path: str = "") -> dict:
    """用已有的识别结果跑批改核心流程，返回完整报告。

    与 grade_homework 的区别：跳过 OCR 步骤，直接用结构化题目列表批改。
    用途：
    - 老师校对完识别结果后，用修正后的结果批改
    - 测试/演示批改链路（不依赖视觉模型）
    - 外部系统已有识别结果时复用批改能力

    各题的 LLM 调用（取答案、错题解析）并行执行，大幅缩短批改耗时。
    """
    questions_raw = recognition.get("questions", [])
    if not questions_raw:
        return {
            "batch_id": f"BATCH_ERROR",
            "error": "未识别到题目",
            "user_message": "没有题目可以批改。",
        }

    # 并行处理每道题：取标准答案 → 判定对错 → 错题解析
    from concurrent.futures import ThreadPoolExecutor, as_completed
    graded_questions = [None] * len(questions_raw)

    def grade_one(idx_q):
        idx, q = idx_q
        return idx, _grade_single_question(q, base_url, token, model)

    # 并发度 4（平衡速度与 API 限流）
    max_workers = min(4, len(questions_raw))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(grade_one, (i, q)): i for i, q in enumerate(questions_raw)}
        for fut in as_completed(futures):
            idx, graded_q = fut.result()
            graded_questions[idx] = graded_q

    # 生成报告
    batch_report = report.build_batch_report(image_path, graded_questions, student_name)

    # 累积学情
    if student_name:
        try:
            report.append_batch(student_name, batch_report)
        except Exception as e:
            batch_report["_study_record_error"] = str(e)

    # 缓存上次批改（供 derive 用）
    save_last_batch(batch_report)

    return batch_report


# ---------------------------------------------------------------------------
# 多学生批量批改（同一套题）
# ---------------------------------------------------------------------------

def _grade_with_cached_answer(q: dict, cached_answer: dict | None,
                              base_url: str, token: str,
                              model: str = "openclaw/default") -> dict:
    """用预缓存的标准答案批改单题（跳过 bank.resolve_answer，节省 LLM 调用）。

    cached_answer: bank.resolve_answer 的返回结构，None 表示缓存未命中（走原流程）。
    用于多学生共享答案场景。
    """
    stem = q.get("stem", "")
    student_answer = q.get("student_answer")

    # 取答案：缓存命中直接用，否则走 bank.resolve_answer
    if cached_answer is not None:
        answer_info = cached_answer
    else:
        answer_info = bank.resolve_answer(
            stem, q.get("options"), q.get("type", "calculation"),
            base_url, token, model,
        )

    correct_answer = answer_info.get("correct_answer")
    knowledge_points = answer_info.get("knowledge_points", [])
    answer_steps = answer_info.get("answer_steps")

    # 无法判定（AI 解不出）
    if correct_answer is None:
        return {
            "question_no": q.get("question_no"),
            "internal_id": q.get("internal_id"),
            "type": q.get("type", "calculation"),
            "stem": stem,
            "options": q.get("options"),
            "student_answer": student_answer,
            "correct_answer": None,
            "is_correct": None,
            "error_type": "undetermined",
            "comment": "这题我没能确定标准答案（可能题面条件不全），你帮我看一下学生做得对不对。",
            "solution": None,
            "knowledge_points": knowledge_points,
            "source": answer_info.get("source"),
            "confidence": answer_info.get("confidence"),
            "need_review": True,
            "bbox": q.get("bbox"),  # 保留坐标用于批注
            "recognition_confidence": q.get("confidence"),
        }

    judge_result = compare.judge(student_answer, correct_answer)
    is_correct = judge_result["is_correct"]

    error_type, comment, solution = None, None, None
    if not is_correct:
        rule_error = explain.classify_error_by_rule(
            stem, student_answer, correct_answer, answer_steps
        )
        explain_result = explain.explain_mistake(
            stem, student_answer, correct_answer,
            knowledge_points, answer_steps, rule_error,
            base_url, token, model,
        )
        error_type = explain_result.get("error_type", rule_error)
        comment = explain_result.get("comment", "")
        solution = explain_result.get("solution", "")
    else:
        solution = answer_steps

    return {
        "question_no": q.get("question_no"),
        "internal_id": q.get("internal_id"),
        "type": q.get("type", "calculation"),
        "stem": stem,
        "options": q.get("options"),
        "student_answer": student_answer,
        "correct_answer": correct_answer,
        "is_correct": is_correct,
        "error_type": error_type,
        "comment": comment,
        "solution": solution,
        "knowledge_points": knowledge_points,
        "source": answer_info.get("source"),
        "confidence": answer_info.get("confidence"),
        "need_review": answer_info.get("need_review") or judge_result["method"] == "unknown",
        "bbox": q.get("bbox"),
        "recognition_confidence": q.get("confidence"),
    }


def grade_multi_students(image_paths: list, student_names: list,
                         base_url: str, token: str,
                         model: str = "openclaw/default",
                         progress_callback=None) -> list:
    """多学生同一套题批量批改。

    流程：
    1. 第一张图完整识别 → 拿题面模板 + bbox + 预热答案缓存
    2. 后续学生用 recognize_answers_only 只识别作答
    3. 共享答案缓存，各自批改（节省 LLM 调用）
    4. 每学生生成一份 batch_report

    image_paths / student_names 长度必须一致。返回 [batch_report, ...]。
    progress_callback(stage, detail) 可选，用于 UI 进度反馈。
    """
    def _notify(stage, detail=""):
        if progress_callback:
            progress_callback(stage, detail)

    if len(image_paths) != len(student_names):
        raise ValueError(f"图片数({len(image_paths)})和学生数({len(student_names)})不一致")
    if not image_paths:
        return []

    # === 阶段1：第一张图完整识别，建立题面模板 + 答案缓存 ===
    _notify("识别第一份作业（建立题面模板）", f"{student_names[0]}的作业")
    first_recognition = ocr.recognize_image(image_paths[0], base_url, token, model)
    template_questions = first_recognition.get("questions", [])
    if not template_questions:
        raise RuntimeError("第一张图未识别到题目，无法作为模板")

    # 预热答案缓存：对模板里每道题取一次标准答案
    _notify("预解标准答案", f"共 {len(template_questions)} 题")
    answer_cache = {}  # key = normalize_stem(stem), value = answer_info
    cache_lock = __import__("threading").Lock()

    def _warmup_one(q):
        ans = bank.resolve_answer(
            q.get("stem", ""), q.get("options"), q.get("type", "calculation"),
            base_url, token, model,
        )
        key = bank.normalize_stem(q.get("stem", ""))
        with cache_lock:
            answer_cache[key] = ans
        return key

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, len(template_questions))) as pool:
        list(pool.map(_warmup_one, template_questions))

    # === 阶段2：批改每个学生 ===
    all_reports = []
    for idx, (img, name) in enumerate(zip(image_paths, student_names)):
        _notify(f"批改第 {idx+1}/{len(image_paths)} 个学生", name)

        if idx == 0:
            # 第一个学生直接用第一张图的识别结果
            recognition = first_recognition
        else:
            # 后续学生：仅识别作答，套用模板题面
            try:
                ans_result = ocr.recognize_answers_only(
                    img, template_questions, base_url, token, model,
                )
                # 用模板题面 + 新作答，合成 recognition
                ans_by_no = {a.get("question_no"): a for a in ans_result.get("answers", [])}
                merged_questions = []
                for tq in template_questions:
                    new_q = dict(tq)  # 复制模板题面
                    a = ans_by_no.get(tq.get("question_no"), {})
                    new_q["student_answer"] = a.get("student_answer")
                    # bbox 用这位学生的（用于批注这张图）
                    if a.get("bbox"):
                        new_q["bbox"] = a.get("bbox")
                    merged_questions.append(new_q)
                recognition = {"questions": merged_questions,
                               "overall_confidence": ans_result.get("overall_confidence", "medium")}
            except Exception as e:
                # 仅识别作答失败，降级走完整识别
                _notify(f"降级完整识别", f"{name}: {type(e).__name__}")
                try:
                    recognition = ocr.recognize_image(img, base_url, token, model)
                except Exception as e2:
                    # 完全失败，记一份错误报告
                    all_reports.append({
                        "batch_id": f"BATCH_ERROR_{idx}",
                        "student_name": name,
                        "image_path": img,
                        "summary": {"total": 0, "correct": 0, "wrong": 0,
                                    "undetermined": 0, "accuracy": 0, "need_review": 0},
                        "questions": [],
                        "error": f"识别失败: {e2}",
                        "user_message": f"{name}的作业识别失败，请重试。",
                    })
                    continue

        # 批改这位学生（复用缓存答案）
        questions_raw = recognition.get("questions", [])
        graded = [None] * len(questions_raw)

        def _grade_one(idx_q):
            i, q = idx_q
            key = bank.normalize_stem(q.get("stem", ""))
            cached = answer_cache.get(key)  # 缓存未命中则 None
            return i, _grade_with_cached_answer(q, cached, base_url, token, model)

        with ThreadPoolExecutor(max_workers=min(4, len(questions_raw))) as pool:
            futures = {pool.submit(_grade_one, (i, q)): i for i, q in enumerate(questions_raw)}
            from concurrent.futures import as_completed
            for fut in as_completed(futures):
                i, gq = fut.result()
                graded[i] = gq

        # 生成报告
        batch_report = report.build_batch_report(img, graded, name)
        batch_report["recognition_source"] = "vision" if idx == 0 else "vision"
        if name:
            try:
                report.append_batch(name, batch_report)
            except Exception:
                pass

        all_reports.append(batch_report)

    # 把最后一个学生的报告缓存为 last-batch（供 derive 用）
    if all_reports:
        save_last_batch(all_reports[-1])

    return all_reports


# ---------------------------------------------------------------------------
# 衍生出题
# ---------------------------------------------------------------------------

def derive_for_question(question_no, count: int,
                        base_url: str, token: str,
                        model: str = "openclaw/default") -> dict:
    """针对上次批改的某道题生成衍生题（错题/对的题都可以）。

    question_no 支持 int 或 str。
    """
    try:
        qno_int = int(question_no)
    except (ValueError, TypeError):
        return {"error": f"题号无效: {question_no}",
                "user_message": f"题号必须是数字。"}

    last = load_last_batch()
    if not last:
        return {"error": "没有找到上次批改记录",
                "user_message": "还没批改过作业呢，先批改一张作业图吧。"}

    # 找到指定题号
    target = None
    for q in last.get("questions", []):
        if q.get("question_no") == qno_int:
            target = q
            break
    if not target:
        return {"error": f"未找到第{qno_int}题",
                "user_message": f"上次批改里没有第{qno_int}题，题号范围是 1~{len(last.get('questions', []))}。"}

    # correct_answer 可能是 None，给个兜底避免 derive_questions 出错
    correct_answer = target.get("correct_answer") or ""

    result = bank.derive_questions(
        stem=target.get("stem", ""),
        student_answer=target.get("student_answer"),
        correct_answer=correct_answer,
        knowledge_points=target.get("knowledge_points", []),
        error_type=target.get("error_type") or "unknown",
        base_url=base_url, token=token, model=model, count=count,
    )
    result["source_question_no"] = qno_int
    result["source_stem"] = target.get("stem")
    result["source_is_correct"] = target.get("is_correct")
    return result


# ---------------------------------------------------------------------------
# Agent 摘要（输出给 AI 转发用）
# ---------------------------------------------------------------------------

def build_assistant_summary(report: dict, student_name: str = "") -> dict:
    """把批改报告转成 Agent 友好的摘要。

    user_message 直接转发给老师；suggested_replies 是建议回复。
    """
    if report.get("error"):
        return {
            "status": "error",
            "user_message": report.get("user_message", "批改遇到问题，请重试。"),
            "suggested_replies": ["重新发一张清楚的作业图"],
        }

    s = report.get("summary", {})
    need_review = s.get("need_review", 0)
    wrong = s.get("wrong", 0)

    # 主体消息
    msg_parts = []
    msg_parts.append(f"批改完成啦！共 {s.get('total',0)} 题，"
                     f"做对 {s.get('correct',0)} 题，做错 {wrong} 题，"
                     f"正确率 {int(s.get('accuracy',0)*100)}%。")
    if student_name:
        msg_parts[0] = f"{student_name}的作业：{msg_parts[0]}"

    # 如果用了 OCR 兜底，提示老师识别准确度可能稍差，建议校对
    if report.get("recognition_source") == "ocr_fallback":
        msg_parts.append(
            "（这张图视觉识别不太顺，我用了本地 OCR 兜底识别。"
            "识别结果可能有少量误差，你扫一眼题号对不对。）"
        )

    if need_review:
        msg_parts.append(f"有 {need_review} 题 AI 不太确定，你帮我看看对不对。")

    # 无法判定的题（最优先提示老师）
    undetermined_qs = [q for q in report.get("questions", []) if q.get("is_correct") is None]
    if undetermined_qs:
        msg_parts.append(
            f"有 {len(undetermined_qs)} 题我没法自动判定对错（可能题面条件没识别全），"
            f"你帮我看一下："
            + "、".join(f"第{q.get('question_no')}题" for q in undetermined_qs[:5])
        )

    # 错题简述（仅真错题，不含无法判定）
    wrong_qs = [q for q in report.get("questions", []) if q.get("is_correct") is False]
    if wrong_qs:
        wrong_brief = "错题："
        for q in wrong_qs[:3]:
            wrong_brief += f"\n  • 第{q.get('question_no')}题 {q.get('stem','')[:20]} → 正确答案：{q.get('correct_answer')}"
        if len(wrong_qs) > 3:
            wrong_brief += f"\n  • 还有 {len(wrong_qs)-3} 道错题..."
        msg_parts.append(wrong_brief)

    # 薄弱知识点
    wps = report.get("weak_points", [])
    if wps:
        kp_msg = "本次薄弱知识点：" + "、".join(w["knowledge_point"] for w in wps[:3])
        msg_parts.append(kp_msg)

    # 建议回复
    suggested = []
    if wrong_qs:
        suggested.append("看看错题解析")
        suggested.append(f"第{wrong_qs[0].get('question_no')}题出两道同类题练练")
    if wps:
        suggested.append("针对薄弱知识点出几道练习")
    suggested.append("存到学情记录")

    return {
        "status": "ok",
        "batch_id": report.get("batch_id"),
        "summary": s,
        "user_message": "\n\n".join(msg_parts),
        "suggested_replies": suggested,
    }


def emit_summary(summary: dict, agent_mode: bool = False) -> None:
    """输出摘要。agent_mode 输出干净 JSON；否则带前缀。"""
    if agent_mode:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("ASSISTANT_SUMMARY_JSON:" + json.dumps(summary, ensure_ascii=False))


def emit_progress(event: str, detail: str = "") -> None:
    """输出进度事件（stderr，不打扰 stdout）。"""
    msg = f"[{event}] {detail}" if detail else f"[{event}]"
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# 自我介绍
# ---------------------------------------------------------------------------

INTRO_TEXT = """我是你的数学作业批改助手。

我能帮你做的事：
- 你把学生的数学作业拍照发给我，我帮你认题、批改、指出错在哪
- 错题我会给点评和解析，你直接拿去讲评
- 你说「第X题出两道同类题」，我针对错题出变式练习
- 学情会自动累积，你说「看看小明的学情」，我给你汇总

你要准备的就是：把作业拍清楚发过来就行。"""


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="小学数学作业批改 Agent")
    parser.add_argument("input", nargs="?", help="作业图片路径（grade/recognize 命令）")
    parser.add_argument("--grade", action="store_true", help="批改作业")
    parser.add_argument("--recognize", action="store_true", help="仅识别不批改")
    parser.add_argument("--derive", type=int, metavar="题号", help="针对错题出衍生题")
    parser.add_argument("--derive-count", type=int, default=2, help="衍生题数量")
    parser.add_argument("--student", default="", help="学生姓名")
    parser.add_argument("--student-summary", default=None, metavar="名字", help="查学生学情")
    parser.add_argument("--intro", action="store_true", help="自我介绍")
    parser.add_argument("--self-check", action="store_true", help="视觉能力自检")
    parser.add_argument("--host", default=None)
    parser.add_argument("--gateway-port", type=int, default=None, help="兼容旧 gateway 端口（一般不用）")
    parser.add_argument("--token", default=None, help="API key（别名，等同 --api-key）")
    parser.add_argument("--api-key", default=None, help="模型服务 API key")
    parser.add_argument("--base-url", default=None, help="模型服务地址（如 https://deepkey.top）")
    parser.add_argument("--model", default=None, help="模型名（如 gpt-5.4）")
    parser.add_argument("--setup", action="store_true", help="配置模型服务（写入 config.json）")
    parser.add_argument("--config-status", action="store_true", help="查看当前配置")
    parser.add_argument("--web", action="store_true", help="启动 Web 界面")
    parser.add_argument("--port", type=int, default=7860, help="Web 服务端口")
    parser.add_argument("--render-card", action="store_true",
                        help="批改后生成卡片图（总览+错题详情）")
    parser.add_argument("--card-dir", default=None,
                        help="卡片输出目录（默认 ~/.openclaw/skill-state/homework-grader-math/cards/）")
    parser.add_argument("--batch", action="store_true",
                        help="批量批改：多学生同一套题（位置参数为多张图路径，--names 指定学生名）")
    parser.add_argument("--names", default=None,
                        help="批量批改时的学生名，按逗号分隔（如 小明,小红,小刚）")
    parser.add_argument("--annotate", action="store_true",
                        help="批改后在原图上画红勾/红叉批注（配合 --render-card 使用）")
    parser.add_argument("--agent-mode", action="store_true", help="Agent 模式：输出干净 JSON")
    parser.add_argument("--assistant-summary-json", action="store_true", help="输出摘要 JSON")
    args = parser.parse_args()

    # Web 界面：启动浏览器交互
    if args.web:
        import web
        web.launch(port=args.port)
        return

    # setup 命令：配置模型服务
    if args.setup:
        import _config
        if args.base_url or args.api_key or args.model:
            _config.update_config(
                base_url=args.base_url, api_key=args.api_key or args.token, model=args.model,
            )
            print("✅ 配置已保存：")
            print(_config.config_status_text())
        else:
            print(_config.config_status_text())
            print("\n用法：--setup --base-url <URL> --api-key <KEY> --model <MODEL>")
        return

    if args.config_status:
        import _config
        print(_config.config_status_text())
        return

    # 解析连接（技能独立配置，不依赖任何 runtime）
    api_key = args.api_key or args.token
    if args.base_url and api_key:
        # 显式参数优先
        base_url = args.base_url
        model = args.model or "gpt-5.4"
    else:
        base_url, api_key, model = resolve_gateway_config(
            host=args.host, port=args.gateway_port, token=api_key, model=args.model,
        )

    agent_mode = args.agent_mode
    want_summary = args.assistant_summary_json or agent_mode

    # 自我介绍
    if args.intro:
        if want_summary:
            emit_summary({
                "status": "intro",
                "user_message": INTRO_TEXT,
                "suggested_replies": ["发一张作业图试试", "先帮我自检一下能不能看图"],
            }, agent_mode)
        else:
            print(INTRO_TEXT)
        return

    # 视觉自检
    if args.self_check:
        ok = check_vision_support(base_url, api_key, model)
        msg = "视觉能力正常，可以看作业图啦。" if ok else "当前模型通道不支持看图，请先切换到支持图片的模型。"
        if want_summary:
            emit_summary({"status": "ready" if ok else "vision_unavailable", "user_message": msg}, agent_mode)
        else:
            print(msg)
        return

    # 查学情
    if args.student_summary:
        s = report.get_student_summary(args.student_summary)
        text = report.format_student_summary(s)
        if want_summary:
            emit_summary({"status": "ok", "user_message": text,
                          "suggested_replies": ["发张作业图继续批改"]}, agent_mode)
        else:
            print(text)
        return

    # 衍生出题
    if args.derive is not None:
        emit_progress("出题中", f"第{args.derive}题")
        result = derive_for_question(args.derive, args.derive_count, base_url, api_key, model)
        if want_summary:
            if result.get("error"):
                emit_summary({"status": "error", "user_message": result.get("user_message", "出题失败")}, agent_mode)
            else:
                derived = result.get("derived_questions", [])
                msg = f"针对第{args.derive}题（{result.get('source_stem','')[:20]}）出了 {len(derived)} 道同类练习：\n"
                for i, q in enumerate(derived, 1):
                    msg += f"\n练习{i}：{q.get('stem')}\n"
                    if q.get("options"):
                        msg += "\n".join(q["options"]) + "\n"
                    msg += f"答案：{q.get('answer')}\n"
                    if q.get("explanation"):
                        msg += f"解析：{q['explanation']}\n"
                emit_summary({
                    "status": "ok",
                    "user_message": msg,
                    "derived_questions": derived,
                    "suggested_replies": ["学生做完帮我批改", "再出几道", "换个知识点"],
                }, agent_mode)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 批量批改（多学生同一套题）
    if args.batch:
        # 收集所有图片路径（位置参数可能多个，或用目录）
        image_paths = []
        for inp in (args.input.split() if args.input else []):
            p = Path(inp)
            if p.is_dir():
                image_paths.extend(sorted(p.glob("*.jpg")) + sorted(p.glob("*.png")))
            elif p.is_file():
                image_paths.append(str(p))
        if not image_paths:
            print("❌ 未找到图片。用法：grade.py --batch img1.jpg img2.jpg --names 小明,小红", file=sys.stderr)
            sys.exit(1)

        # 学生名
        names = []
        if args.names:
            names = [n.strip() for n in args.names.replace("，", ",").split(",") if n.strip()]
        if len(names) != len(image_paths):
            # 用文件名兜底
            names = [Path(p).stem for p in image_paths]
            print(f"⚠️ 学生名与图片数不匹配，使用文件名：{names}", file=sys.stderr)

        emit_progress("批量批改", f"{len(image_paths)} 位学生")

        def batch_progress(stage, detail=""):
            emit_progress(stage, detail)

        try:
            reports = grade_multi_students(
                image_paths, names, base_url, api_key, model,
                progress_callback=batch_progress,
            )
        except Exception as e:
            print(f"❌ 批量批改失败: {e}", file=sys.stderr)
            sys.exit(1)

        # 生成批注图 + 卡片
        if args.render_card or args.annotate:
            import card_renderer
            card_dir = Path(args.card_dir) if args.card_dir else (
                Path.home() / ".openclaw" / "skill-state" / "homework-grader-math" / "cards")
            card_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n✅ 批量批改完成，共 {len(reports)} 位学生：\n")
        for r in reports:
            s = r.get("summary", {})
            name = r.get("student_name", "")
            acc = int(s.get("accuracy", 0) * 100)
            print(f"  {name}: 共{s.get('total',0)}题 ✓{s.get('correct',0)} ✗{s.get('wrong',0)} "
                  f"❓{s.get('undetermined',0)} 正确率{acc}%")

            # 批注图
            if args.annotate and r.get("image_path"):
                ann_path = str(card_dir / f"{r.get('batch_id','batch')}_annotated.jpg")
                try:
                    card_renderer.annotate_homework_image(
                        r["image_path"], r.get("questions", []), ann_path)
                    print(f"    批注图: {ann_path}")
                except Exception as e:
                    print(f"    批注失败: {e}", file=sys.stderr)

            # 总览卡
            if args.render_card:
                ov_path = str(card_dir / f"{r.get('batch_id','batch')}_overview.png")
                try:
                    card_renderer.render_overview_card(r, ov_path)
                    print(f"    总览卡: {ov_path}")
                except Exception as e:
                    print(f"    卡片失败: {e}", file=sys.stderr)
        return

    # 仅识别
    if args.recognize:
        if not args.input:
            print("请提供图片路径", file=sys.stderr); sys.exit(1)
        emit_progress("识别中")
        result = ocr.recognize_image(args.input, base_url, api_key, model)
        if want_summary:
            review_text = ocr.format_recognition_review(result)
            emit_summary({
                "status": "ok",
                "user_message": review_text,
                "suggested_replies": ["看着没问题，开始批改", "第几题识别错了"],
            }, agent_mode)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 批改（主流程）
    if args.grade or args.input:
        if not args.input:
            print("请提供作业图片路径", file=sys.stderr); sys.exit(1)
        if not Path(args.input).is_file():
            print(f"图片不存在: {args.input}", file=sys.stderr); sys.exit(1)

        emit_progress("识别中")
        try:
            emit_progress("批改中")
            batch_report = grade_homework(args.input, args.student, base_url, api_key, model)
        except VisionNotSupportedError:
            # 模型根本不支持看图
            if want_summary:
                emit_summary({"status": "vision_unsupported",
                              "user_message": "当前模型看不了图。请换一个支持图片识别的模型（如 GPT-4o、Claude 等多模态模型）。"},
                             agent_mode)
            else:
                print("视觉能力不可用：模型不支持图片", file=sys.stderr)
            return
        except VisionUnavailableError as e:
            # 模型支持看图，但服务不稳定（超时/断开/限流）
            # 注意：走到这里说明 OCR 兜底也失败了（grade_homework 内部已尝试）
            if want_summary:
                emit_summary({
                    "status": "vision_unavailable",
                    "user_message": (
                        "识别这张图时遇到了困难（视觉服务和 OCR 兜底都没成功）。"
                        "你可以：\n 1. 稍等一会儿重试\n 2. 换一个更稳定的视觉模型\n"
                        " 3. 把题目打字发给我，我用文本方式批改。"
                    ),
                    "detail": str(e)[:200],
                }, agent_mode)
            else:
                print(f"识别失败: {e}", file=sys.stderr)
            return
        except RuntimeError as e:
            # grade_homework 抛出的组合错误（视觉+OCR 都失败）
            if want_summary:
                emit_summary({
                    "status": "recognition_failed",
                    "user_message": (
                        "这张图我没识别出来。你可以：\n"
                        " 1. 重新拍一张更清楚的\n 2. 稍后重试\n"
                        " 3. 把题目和作答打字发给我，我用文本方式批改"
                    ),
                    "detail": str(e)[:300],
                }, agent_mode)
            else:
                print(f"识别失败: {e}", file=sys.stderr)
            return

        # 识别成功（含 OCR 兜底成功），进度的提示
        src = batch_report.get("recognition_source", "vision")
        if src == "ocr_fallback" and want_summary:
            # OCR 兜底成功，在摘要里加一句说明
            pass  # build_assistant_summary 会处理，这里不额外打断

        # === 生成卡片图（可选）===
        card_paths = []
        if args.render_card or args.annotate:
            try:
                import card_renderer
                card_dir = Path(args.card_dir) if args.card_dir else (
                    Path.home() / ".openclaw" / "skill-state" / "homework-grader-math" / "cards")
                card_dir.mkdir(parents=True, exist_ok=True)
                batch_id = batch_report.get("batch_id", "batch")

                if args.render_card:
                    overview_path = str(card_dir / f"{batch_id}_overview.png")
                    details_path = str(card_dir / f"{batch_id}_details.png")
                    card_renderer.render_overview_card(batch_report, overview_path)
                    card_renderer.render_wrong_details_card(batch_report, details_path)
                    card_paths = [overview_path, details_path]
                    emit_progress("卡片已生成", f"{overview_path}")

                if args.annotate:
                    ann_path = str(card_dir / f"{batch_id}_annotated.jpg")
                    card_renderer.annotate_homework_image(
                        args.input, batch_report.get("questions", []), ann_path)
                    card_paths.append(ann_path)
                    emit_progress("批注图已生成", ann_path)
            except Exception as e:
                emit_progress("卡片/批注生成失败", str(e)[:80])

        if want_summary:
            summary = build_assistant_summary(batch_report, args.student)
            # 把卡片路径塞进摘要，便于上层（Agent/飞书）转发图片
            if card_paths:
                summary["card_paths"] = card_paths
                summary["user_message"] += "\n\n📸 已生成批改卡片图，可转发给家长或发到家长群。"
                summary["suggested_replies"].append("发家长群版")
            emit_summary(summary, agent_mode)
        else:
            # 人类模式：输出文本报告
            print(report.format_report_text(batch_report))
            if card_paths:
                print("\n📸 卡片图已生成：")
                for p in card_paths:
                    print(f"   {p}")
            print("\n" + "=" * 60)
            print("（完整 JSON 报告见 stderr）")
            print(json.dumps(batch_report, ensure_ascii=False, indent=2), file=sys.stderr)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
