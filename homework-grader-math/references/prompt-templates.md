# 提示词模板与 JSON Schema

> 本文档汇总技能里所有 LLM 提示词的设计要点，便于迭代优化。
> 实际提示词代码在 `scripts/` 下各模块里。

## 1. 作业识别提示词（ocr.py: RECOGNIZE_PROMPT）

**目标**：把作业图片识别成结构化题目列表。

**输出 JSON Schema**：
```json
{
  "questions": [
    {
      "question_no": 1,
      "type": "calculation | fill_blank | choice | judge | application",
      "stem": "题面（去掉学生作答）",
      "options": ["A....", "B. ..."] 或 null,
      "student_answer": "学生作答" 或 null,
      "confidence": "high | medium | low",
      "notes": "识别备注" 或 null
    }
  ],
  "overall_confidence": "high | medium | low",
  "needs_review": ["需复核的题号或问题"],
  "raw_text_notes": "整体观察说明"
}
```

**关键设计**：
- 题面和作答分开（`25×4=` 是题面，`100` 是作答）
- 运算符号准确识别（× vs x、÷ vs +）
- 分数写 a/b，带分数写 a b/c
- 置信度分三级，低置信度提示复核

## 2. AI 解题提示词（bank.py: SOLVE_PROMPT_TEMPLATE）

**目标**：题库未命中时，AI 现场解题。

**输出 JSON Schema**：
```json
{
  "answer": "标准答案",
  "answer_steps": "解题步骤简述",
  "knowledge_points": ["知识点1", "知识点2"],
  "difficulty": 1-5,
  "confidence": 0.0-1.0
}
```

**关键设计**：
- answer 不带单位（除非题目要求）
- knowledge_points 用 `references/knowledge-map.md` 的标准命名
- confidence：计算题通常 ≥0.9，应用题按实际把握

## 3. 衍生出题提示词（bank.py: DERIVE_PROMPT_TEMPLATE）

**目标**：针对错题生成同知识点练习题。

**输出 JSON Schema**：
```json
{
  "derived_questions": [
    {
      "type": "choice | fill_blank",
      "stem": "题面",
      "options": ["A. ...", "B. ..."] 或 null,
      "answer": "标准答案",
      "knowledge_point": "本题知识点",
      "difficulty": 1-5,
      "explanation": "为什么选这个答案"
    }
  ]
}
```

**关键设计**：
- 必须考查同一知识点
- 选择题优先（便于自动批改）
- 干扰项来自学生常见错误（进位错、口诀错、运算符看错）
- 不与原题重复

## 4. 错题解析提示词（explain.py: EXPLAIN_PROMPT_TEMPLATE）

**目标**：分析错因，生成点评和解析。

**输出 JSON Schema**：
```json
{
  "error_type": "careless | conceptual | calculation | empty",
  "comment": "给学生的点评，2-3句，鼓励式",
  "solution": "题目解析，分步骤"
}
```

**关键设计**：
- comment 语气亲切，符合小学生理解水平
- comment 要指出具体错在哪（如"乘法口诀记成七八五十四，应该是七八五十六"）
- solution 分步骤说明正确解法
- 规则已能确定的错因（empty）不调 LLM，直接返回模板

---

## LLM 边界守卫原则

参考 gaokao-advisor 的设计：

1. **对错判定不调 LLM**：由 `compare.py` 确定性计算（数值比较 + 字符串匹配）
2. **错因分类规则优先**：`explain.classify_error_by_rule` 覆盖不了才调 LLM
3. **LLM 只做文案生成**：点评、解析、出题，不做判定
4. **LLM 失败有兜底**：退回规则模板，不阻断流程
5. **答案步骤优先用题库的**：题库命中的题目有预存步骤，LLM 只做润色

## 提示词迭代建议

- 识别准确率不够 → 优化 RECOGNIZE_PROMPT 的符号区分规则
- AI 解题错 → 检查 SOLVE_PROMPT 是否要求分步骤（强制分步可提高准确率）
- 衍生题质量差 → 在 DERIVE_PROMPT 里加更多干扰项来源示例
- 点评语气不对 → 在 EXPLAIN_PROMPT 里加正例/反例对照
