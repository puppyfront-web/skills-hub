"""
批改报告生成 + 学情累积模块。

两类输出：
1. 单次批改报告：本次作业的逐题结果（给老师看）
2. 学情累积：把每次批改的答题记录存到本地 JSON，可查历史、统计薄弱知识点

学情存储：~/.openclaw/skill-state/homework-grader-math/study-records.json
结构：按学生累积答题记录，每条带时间戳 + 知识点 + 对错 + 错因
"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys = __import__("sys")
sys.path.insert(0, str(Path(__file__).parent))
from explain import error_type_label

STATE_DIR = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math"
RECORDS_PATH = STATE_DIR / "study-records.json"


# ---------------------------------------------------------------------------
# 学情读写
# ---------------------------------------------------------------------------

def load_records() -> dict:
    """加载学情记录。"""
    if not RECORDS_PATH.is_file():
        return {"students": {}, "batches": []}
    try:
        with open(RECORDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"students": {}, "batches": []}


def save_records(records: dict) -> None:
    """保存学情记录。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(RECORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def append_batch(student_name: str, batch_report: dict) -> None:
    """把一次批改的答题记录追加到学情库。

    batch_report 是 grade.py 生成的完整批改结果。
    """
    records = load_records()
    now = datetime.now().isoformat(timespec="seconds")

    # 记录批次
    batch_summary = {
        "batch_id": batch_report.get("batch_id"),
        "student_name": student_name,
        "date": batch_report.get("date", now),
        "image_path": batch_report.get("image_path"),
        "total": batch_report.get("summary", {}).get("total", 0),
        "correct": batch_report.get("summary", {}).get("correct", 0),
        "wrong": batch_report.get("summary", {}).get("wrong", 0),
        "recorded_at": now,
    }
    records.setdefault("batches", []).append(batch_summary)

    # 按学生累积答题明细
    student = records.setdefault("students", {}).setdefault(
        student_name, {"name": student_name, "answer_records": []}
    )
    for q in batch_report.get("questions", []):
        rec = {
            "batch_id": batch_report.get("batch_id"),
            "date": batch_report.get("date", now),
            "question_no": q.get("question_no"),
            "stem": q.get("stem"),
            "type": q.get("type"),
            "student_answer": q.get("student_answer"),
            "correct_answer": q.get("correct_answer"),
            "is_correct": q.get("is_correct"),
            "error_type": q.get("error_type"),
            "knowledge_points": q.get("knowledge_points", []),
            "source": q.get("source"),
            "confidence": q.get("confidence"),
            "need_review": q.get("need_review", False),
            "recorded_at": now,
        }
        student["answer_records"].append(rec)

    save_records(records)


# ---------------------------------------------------------------------------
# 单次批改报告（给老师看）
# ---------------------------------------------------------------------------

def build_batch_report(image_path: str, questions: list, student_name: str = "") -> dict:
    """从批改结果构建报告。

    questions: 每道题的完整结果（含 is_correct, error_type, comment, solution 等）
    返回完整报告 dict。
    """
    total = len(questions)
    # 三态统计：is_correct 为 True(对) / False(错) / None(无法判定)
    correct = sum(1 for q in questions if q.get("is_correct") is True)
    undetermined = sum(1 for q in questions if q.get("is_correct") is None)
    wrong = total - correct - undetermined
    need_review = sum(1 for q in questions if q.get("need_review"))

    # 错题列表（不含无法判定的，无法判定的单独列）
    wrong_questions = [q for q in questions if q.get("is_correct") is False]
    undetermined_questions = [q for q in questions if q.get("is_correct") is None]

    # 错因统计
    error_stats = Counter(q.get("error_type", "unknown") for q in wrong_questions)

    # 知识点统计
    kp_stats = defaultdict(lambda: {"total": 0, "wrong": 0})
    for q in questions:
        for kp in q.get("knowledge_points", []):
            kp_stats[kp]["total"] += 1
            if q.get("is_correct") is False:  # 仅真错题计入，无法判定不算
                kp_stats[kp]["wrong"] += 1

    # 薄弱知识点（错误率 ≥ 50%）
    weak_points = [
        {"knowledge_point": kp, "wrong": v["wrong"], "total": v["total"],
         "wrong_rate": round(v["wrong"] / v["total"], 2)}
        for kp, v in kp_stats.items()
        if v["total"] >= 1 and v["wrong"] / v["total"] >= 0.5
    ]
    weak_points.sort(key=lambda x: x["wrong_rate"], reverse=True)

    return {
        "batch_id": f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "date": datetime.now().isoformat(timespec="seconds"),
        "image_path": image_path,
        "student_name": student_name,
        "summary": {
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "undetermined": undetermined,  # 无法判定对错的题数
            "accuracy": round(correct / total, 2) if total else 0,
            "need_review": need_review,
        },
        "error_stats": dict(error_stats),
        "weak_points": weak_points,
        "questions": questions,
    }


def format_report_text(report: dict) -> str:
    """把批改报告格式化成给老师看的纯文本。

    不展示 JSON/路径/技术词，用自然语言 + 表格。
    """
    s = report.get("summary", {})
    lines = []
    lines.append(f"📊 批改完成（{report.get('student_name') or '学生'}的作业）")
    lines.append("")
    lines.append(f"共 {s.get('total', 0)} 题：✓ 对 {s.get('correct', 0)} 题  ✗ 错 {s.get('wrong', 0)} 题"
                 f"  正确率 {int(s.get('accuracy', 0) * 100)}%")
    if s.get("undetermined", 0):
        lines.append(f"❓ 有 {s['undetermined']} 题无法自动判定（建议你手动看一下）")
    if s.get("need_review"):
        lines.append(f"⚠️ 有 {s['need_review']} 题需要你重点看一下（AI 不太确定）")
    lines.append("")

    # 逐题结果
    lines.append("逐题结果：")
    lines.append(f"{'题号':<4} {'判定':<4} {'学生答案':<10} {'正确答案':<10} {'错因':<8} {'来源':<6}")
    lines.append("-" * 60)
    for q in report.get("questions", []):
        no = str(q.get("question_no", ""))
        # 三态判定符号
        ic = q.get("is_correct")
        if ic is True:
            judge = "✓"
        elif ic is False:
            judge = "✗"
        else:
            judge = "❓"  # 无法判定
        sa = (str(q.get("student_answer") or "未作答"))[:8]
        ca = (str(q.get("correct_answer") or "?"))[:8]
        et = error_type_label(q.get("error_type", "")) if ic is False else ("—" if ic else "无法判定")
        src = "题库" if q.get("source") == "bank" else "AI"
        if q.get("need_review"):
            src += "⚠️"
        lines.append(f"{no:<4} {judge:<4} {sa:<10} {ca:<10} {et:<8} {src:<6}")
    lines.append("")

    # 无法判定的题（单独列，优先提示老师看）
    undetermined_qs = [q for q in report.get("questions", []) if q.get("is_correct") is None]
    if undetermined_qs:
        lines.append("❓ 以下题目无法自动判定，请你帮忙看一下：")
        lines.append("")
        for q in undetermined_qs:
            lines.append(f"【第{q.get('question_no')}题】{q.get('stem')}")
            lines.append(f"  学生作答：{q.get('student_answer') or '未作答'}")
            if q.get("comment"):
                lines.append(f"  说明：{q['comment']}")
            lines.append("")
        lines.append("")

    # 错题解析
    wrong_qs = [q for q in report.get("questions", []) if q.get("is_correct") is False]
    if wrong_qs:
        lines.append("📝 错题解析：")
        lines.append("")
        for q in wrong_qs:
            lines.append(f"【第{q.get('question_no')}题】{q.get('stem')}")
            lines.append(f"  学生答案：{q.get('student_answer') or '未作答'}  →  正确答案：{q.get('correct_answer')}")
            lines.append(f"  错因：{error_type_label(q.get('error_type', ''))}")
            if q.get("knowledge_points"):
                lines.append(f"  知识点：{'、'.join(q['knowledge_points'])}")
            if q.get("comment"):
                lines.append(f"  点评：{q['comment']}")
            if q.get("solution"):
                lines.append(f"  解析：{q['solution']}")
            lines.append("")

    # 薄弱知识点
    wps = report.get("weak_points", [])
    if wps:
        lines.append("🎯 本次薄弱知识点：")
        for wp in wps[:3]:
            lines.append(f"  • {wp['knowledge_point']}：错 {wp['wrong']}/{wp['total']}（错误率 {int(wp['wrong_rate'] * 100)}%）")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 学情查询（历史统计）
# ---------------------------------------------------------------------------

def get_student_summary(student_name: str) -> dict:
    """查某个学生的学情汇总。"""
    records = load_records()
    student = records.get("students", {}).get(student_name)
    if not student:
        return {"name": student_name, "found": False}

    recs = student.get("answer_records", [])
    if not recs:
        return {"name": student_name, "found": True, "total": 0}

    total = len(recs)
    correct = sum(1 for r in recs if r.get("is_correct"))
    wrong = total - correct

    # 知识点统计
    kp_stats = defaultdict(lambda: {"total": 0, "wrong": 0})
    for r in recs:
        for kp in r.get("knowledge_points", []):
            kp_stats[kp]["total"] += 1
            if not r.get("is_correct"):
                kp_stats[kp]["wrong"] += 1

    weak = [
        {"knowledge_point": kp, "wrong": v["wrong"], "total": v["total"],
         "wrong_rate": round(v["wrong"] / v["total"], 2)}
        for kp, v in kp_stats.items()
        if v["wrong"] / v["total"] >= 0.5
    ]
    weak.sort(key=lambda x: x["wrong_rate"], reverse=True)

    return {
        "name": student_name,
        "found": True,
        "total_questions": total,
        "correct": correct,
        "wrong": wrong,
        "accuracy": round(correct / total, 2) if total else 0,
        "weak_points": weak[:5],
        "recent_wrong": [
            {"stem": r.get("stem"), "error_type": r.get("error_type"),
             "knowledge_points": r.get("knowledge_points"), "date": r.get("date")}
            for r in recs if not r.get("is_correct")
        ][-10:],
    }


def format_student_summary(summary: dict) -> str:
    """格式化学情汇总为文本。"""
    if not summary.get("found"):
        return f"还没有 {summary.get('name')} 的学情记录。"

    lines = []
    lines.append(f"📚 {summary['name']} 的学情汇总")
    lines.append("")
    lines.append(f"累计答题：{summary.get('total_questions', 0)} 题  "
                 f"✓ {summary.get('correct', 0)}  ✗ {summary.get('wrong', 0)}  "
                 f"正确率 {int(summary.get('accuracy', 0) * 100)}%")
    lines.append("")

    wps = summary.get("weak_points", [])
    if wps:
        lines.append("需要重点关注的薄弱知识点：")
        for wp in wps:
            lines.append(f"  • {wp['knowledge_point']}：错 {wp['wrong']}/{wp['total']}（错误率 {int(wp['wrong_rate'] * 100)}%）")
    else:
        lines.append("暂无明显的薄弱知识点，继续加油～")
    lines.append("")

    recent = summary.get("recent_wrong", [])
    if recent:
        lines.append("近期错题：")
        for r in recent[-5:]:
            kp = "、".join(r.get("knowledge_points", []))
            lines.append(f"  • [{r.get('date', '')[:10]}] {r.get('stem','')[:25]}  知识点：{kp}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="学情报告")
    sub = parser.add_subparsers(dest="cmd")

    p_student = sub.add_parser("student", help="查学生学情")
    p_student.add_argument("name", help="学生姓名")

    p_batches = sub.add_parser("batches", help="列出历史批次")

    args = parser.parse_args()

    if args.cmd == "student":
        s = get_student_summary(args.name)
        print(format_student_summary(s))
    elif args.cmd == "batches":
        records = load_records()
        batches = records.get("batches", [])
        print(f"历史批改共 {len(batches)} 次：")
        for b in batches[-20:]:
            print(f"  [{b.get('date','')[:10]}] {b.get('student_name','')}  "
                  f"{b.get('correct',0)}✓/{b.get('wrong',0)}✗/{b.get('total',0)}")
    else:
        parser.print_help()
