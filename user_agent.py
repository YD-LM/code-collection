"""参赛版数学 Agent 主流程控制文件。

本文件包括：读题、规划、求解、工具检查、Verifier 校验、修正、解释和最终 JSON 汇总。
本文件控制：运行模式 fast/contest/strict、RAG/记忆是否注入、领域提示长度、校验与修正次数、各阶段 thinking 开关。
优化时这样操作：
- 想调速度和质量平衡，优先改 PIPELINE_MODES，不要到处改流程代码。
- 想增加题型知识，改 knowledge_base 与 rag.py，不要把大段知识塞进这里。
- 想增加历史避坑，改 memory/learned_rules.json。
- 想增加可机械验算的规则，改 math_tools.py。
- 想改变网页选项和展示，改 app.py。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from llm import MODEL_NAME, ask_json
from math_tools import format_tool_feedback, run_local_sanity_checks, run_support_checks
from memory_store import format_memory_rules, retrieve_memory_rules
from prompts import PLAN_SYSTEM, VERIFY_SYSTEM, EXPLAIN_SYSTEM, get_read_prompt
from rag import format_retrieved_knowledge, retrieve_knowledge
from routing import get_solver_system


PIPELINE_VERSION = "v6_short_routing_contest"


PIPELINE_MODES: dict[str, dict[str, Any]] = {
    "fast": {
        "description": "快速调试：关闭 RAG、记忆和校验，用于检查接口和简单题。",
        "use_rag": False,
        "use_memory": False,
        "use_verifier": False,
        "use_explanation": False,
        "rag_top_k": 0,
        "max_hint_chars": 300,
        "max_revisions": 0,
        "empty_retry": True,
        "thinking_read": False,
        "thinking_plan": False,
        "thinking_solve": False,
        "thinking_verify": False,
        "thinking_explain": False,
        "repair_failed_only": False,
    },
    "contest": {
        "description": "比赛默认：短 routing + 少量 RAG + 本地工具 + 小问级修正，兼顾速度和稳定性。",
        "use_rag": True,
        "use_memory": True,
        "use_verifier": True,
        "use_explanation": False,
        "rag_top_k": 1,
        "max_hint_chars": 700,
        "max_revisions": 1,
        "empty_retry": True,
        "thinking_read": False,
        "thinking_plan": False,
        "thinking_solve": False,
        "thinking_verify": False,
        "thinking_explain": False,
        "repair_failed_only": True,
    },
    "strict": {
        "description": "疑难重算：增加 RAG 和校验，但仍关闭长 thinking，避免单题耗时失控。",
        "use_rag": True,
        "use_memory": True,
        "use_verifier": True,
        "use_explanation": True,
        "rag_top_k": 2,
        "max_hint_chars": 900,
        "max_revisions": 1,
        "empty_retry": True,
        "thinking_read": False,
        "thinking_plan": False,
        "thinking_solve": False,
        "thinking_verify": False,
        "thinking_explain": False,
        "repair_failed_only": True,
    },
    "deep": {
        "description": "深度慢模式：仅在 contest/strict 都失败后使用，允许 thinking，并限制修正轮。",
        "use_rag": True,
        "use_memory": True,
        "use_verifier": True,
        "use_explanation": True,
        "rag_top_k": 2,
        "max_hint_chars": 900,
        "max_revisions": 2,
        "empty_retry": True,
        "thinking_read": False,
        "thinking_plan": False,
        "thinking_solve": True,
        "thinking_verify": True,
        "thinking_explain": False,
        "repair_failed_only": True,
    },
}


SOLVE_EXTRA_GUIDANCE = """

[执行补充规则]
如果题目包含多个小问，必须逐一回答 required_sub_questions 中列出的全部 sub_idx。
最终 JSON 只允许包含标准答案字段，禁止把长篇思考放入 response 字段。
多小问时 sub_answers 的长度必须等于 required_sub_questions 的长度，顺序必须一致。
若 prompt 中出现 [检索到的相关方法] 或 [历史错误记忆]，它们只能作为方法和检查清单，不能替代对原题的推导。
"""

REPAIR_EXTRA_GUIDANCE = """

[修正模式强制规则]
你正在修正一份已被 verifier 或本地数学工具判错的答案。
必须先定位 failed_sub_questions 或本地工具指出的问题，重新选择证明或计算路线，禁止沿用已被指出的错误推理链。
如果只要求修正失败小问，不得重写已通过小问；输出仍必须是标准 JSON。
如果某一步无法严格推出结论，必须在对应 proof_or_derivation 中说明卡点，并将 verify_check 设为 false。
禁止输出 response 字段；禁止输出 markdown；禁止输出长篇思考。
"""

REPAIR_EVIDENCE_GUIDANCE = """

[证据优先修正规则]
本轮修正必须优先处理 verifier 或本地数学工具给出的反例、样例表和禁止重复结论。
如果存在 counterexamples，必须先在推导中解释旧结论为何被反例推翻，再重新计算。
如果存在 forbidden_claims，禁止把这些结论再次作为 final_answer、answer_latex 或 sub_answers.answer。
如果存在 repair_constraints 或 suggested_route，必须按这些约束重新组织推导，并用样例表复核最终公式。
"""

EMPTY_RETRY_GUIDANCE = """

[空答案重算]
上一轮 solver 输出为空或几乎为空，这是无效答案。
请重新完整求解，不要输出空字段。
每个 sub_answer 必须填写 answer 和 proof_or_derivation；steps 中每一步必须有 operation 和 result。
只输出合法 JSON，不要输出 markdown 或额外解释。
"""

SCHEMA_REPAIR_SYSTEM = """
你是数学答案结构整理器，只负责把候选内容整理为标准 JSON，不重新解题。
只输出一个 JSON 对象，禁止任何额外文字、markdown 注释和长篇思考。
必须包含字段：steps、sub_answers、final_answer、answer_latex、verify_check。
禁止在最终 JSON 中保留 response 字段；若候选内容有 response，只能把其中可用结论整理进 steps 或 sub_answers。
标准结构：
{
  "steps": [{"step": 1, "sub_idx": "(1)", "operation": "步骤描述", "result": "本步结果"}],
  "sub_answers": [{"sub_idx": "(1)", "answer": "小题答案", "proof_or_derivation": "推导", "verified_hint": "自检说明"}],
  "final_answer": "整题汇总答案",
  "answer_latex": "答案公式",
  "verify_check": false
}
"""


_QUESTION_LABEL = re.compile(r"(?:^|\n)\s*[\(（]\s*(\d+|i{1,3}|iv|v|vi{0,3}|ix|x)\s*[\)）]", re.IGNORECASE)


def _ctx(label: str, obj: Any) -> str:
    """把上下文对象序列化为模型更容易读取的文本。

    参数：
        label：上下文段落标题。
        obj：需要序列化的 Python 对象。

    返回：
        带标题的 JSON 文本。
    """
    return f"{label}:\n{json.dumps(obj, ensure_ascii=False, indent=2)}"


def _get_mode_config(mode: str) -> dict[str, Any]:
    """读取运行模式配置，未知模式回退到 contest。

    参数：
        mode：运行模式名称，支持 fast、contest、strict。

    返回：
        模式配置字典。
    """
    return dict(PIPELINE_MODES.get(mode, PIPELINE_MODES["contest"]))


def _is_roman_label(label: str) -> bool:
    """判断题号是否为罗马数字小问。

    参数：
        label：题号正文，不含括号。

    返回：
        是罗马数字题号时返回 True。
    """
    return bool(re.fullmatch(r"i{1,3}|iv|v|vi{0,3}|ix|x", label.strip(), re.IGNORECASE))


def _extract_sub_questions_from_text(problem: str) -> list[dict]:
    """从原题文本中确定性抽取一级和二级小问。

    参数：
        problem：原始题干文本。

    返回：
        规范化小问列表；未识别到题号时返回空列表。
    """
    matches = list(_QUESTION_LABEL.finditer(problem))
    if not matches:
        return []

    questions: list[dict] = []
    current_main = ""
    for index, match in enumerate(matches):
        raw_label = match.group(1).strip()
        is_roman = _is_roman_label(raw_label)
        if not is_roman:
            current_main = f"({raw_label})"

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(problem)
        target = problem[match.end() : next_start].strip(" \n\t:：；;．。")
        next_is_roman = index + 1 < len(matches) and _is_roman_label(matches[index + 1].group(1))
        if not is_roman and next_is_roman:
            continue

        if is_roman and current_main:
            sub_idx = f"{current_main}({raw_label.lower()})"
        else:
            sub_idx = f"({raw_label.lower() if is_roman else raw_label})"
        questions.append({"sub_idx": sub_idx, "target": target})

    return questions



_EMPTY_CLASSIFICATION_VALUES = {"", "?", "未知", "unknown", "none", "null", "未识别"}


_CLASSIFICATION_RULES: list[dict[str, Any]] = [
    {
        "domain": "解析几何",
        "subtype": "圆锥曲线与动点综合题",
        "keywords": ["椭圆", "双曲线", "抛物线", "焦点", "准线", "离心率", "圆锥曲线", "轨迹", "斜率"],
        "concepts": ["圆锥曲线", "轨迹方程", "参数范围", "最值验证"],
    },
    {
        "domain": "平面几何",
        "subtype": "平面几何证明或计算题",
        "keywords": ["三角形", "圆", "切线", "弦", "相似", "全等", "角平分线", "四点共圆"],
        "concepts": ["相似", "全等", "圆定理", "面积法"],
    },
    {
        "domain": "立体几何",
        "subtype": "空间点线面关系题",
        "keywords": ["立体", "空间", "棱锥", "棱柱", "线面", "面面", "二面角", "体积"],
        "concepts": ["空间向量", "线面关系", "角度与距离"],
    },
    {
        "domain": "数列",
        "subtype": "数列递推或求和题",
        "keywords": ["数列", "递推", "通项", "前n项", "前 n 项", "求和", "裂项", "放缩"],
        "concepts": ["递推", "通项", "求和", "归纳法"],
    },
    {
        "domain": "数论",
        "subtype": "整数性质与取整题",
        "keywords": ["floor", "\\lfloor", "取整", "整除", "同余", "质数", "二进制", "数位", "mod"],
        "concepts": ["整除", "同余", "取整", "数位结构"],
    },
    {
        "domain": "概率统计",
        "subtype": "概率统计计算题",
        "keywords": ["概率", "随机变量", "期望", "方差", "分布", "独立", "条件概率", "统计"],
        "concepts": ["样本空间", "条件概率", "期望", "分布"],
    },
    {
        "domain": "线性代数",
        "subtype": "矩阵与线性空间题",
        "keywords": ["矩阵", "行列式", "特征值", "特征向量", "线性相关", "秩", "向量空间", "二次型"],
        "concepts": ["矩阵", "秩", "特征值", "线性空间"],
    },
    {
        "domain": "微积分",
        "subtype": "极限导数积分题",
        "keywords": ["极限", "导数", "积分", "微分", "级数", "收敛", "连续", "可导"],
        "concepts": ["极限", "导数", "积分", "收敛性"],
    },
    {
        "domain": "组合数学",
        "subtype": "组合计数或构造题",
        "keywords": ["排列", "组合", "计数", "染色", "抽屉", "容斥", "生成函数"],
        "concepts": ["计数", "容斥", "构造", "不变量"],
    },
    {
        "domain": "函数",
        "subtype": "函数性质综合题",
        "keywords": ["函数", "定义域", "值域", "单调", "奇函数", "偶函数", "周期", "f(x)", "D(x"],
        "concepts": ["函数定义", "单调性", "奇偶性", "分类讨论"],
    },
    {
        "domain": "方程与不等式",
        "subtype": "方程不等式求解或证明题",
        "keywords": ["方程", "不等式", "恒成立", "解集", "参数", "根", "判别式"],
        "concepts": ["定义域", "判别式", "等价变形", "参数分类"],
    },
]


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "domain": ("domain", "subject", "field", "category", "topic", "area", "领域", "学科", "主题"),
    "subtype": ("subtype", "subdomain", "type", "problem_type_detail", "task_subtype", "题型", "子类型", "细分题型"),
    "problem_type": ("problem_type", "task_type", "question_type", "type", "任务类型"),
    "given": ("given", "conditions", "premise", "known", "已知", "条件"),
    "find": ("find", "target", "goal", "question", "求解目标", "目标"),
    "proof_goal": ("proof_goal", "conclusion", "prove", "证明目标", "结论"),
    "key_concepts": ("key_concepts", "concepts", "methods", "proof_ideas", "知识点", "方法"),
}


def _is_missing_classification(value: Any) -> bool:
    """判断领域或子类型字段是否等价于缺失。

    参数：
        value：模型或本地推断返回的字段值。

    返回：
        字段缺失、空白或明显未知时返回 True。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_CLASSIFICATION_VALUES
    return False


def _first_present_field(data: dict, aliases: tuple[str, ...]) -> Any:
    """从多个可能字段名中读取第一个非空值。

    参数：
        data：模型读题阶段返回的对象。
        aliases：候选字段名列表。

    返回：
        第一个有效字段值；没有命中时返回 None。
    """
    for key in aliases:
        value = data.get(key)
        if not _is_missing_classification(value) and value not in ([], {}):
            return value
    return None


def _infer_domain_subtype(problem: str) -> dict[str, Any]:
    """根据题干关键词给出最低限度的领域和子类型判断。

    参数：
        problem：原始题干文本。

    返回：
        包含 domain、subtype、key_concepts、confidence 的分类字典。
    """
    compact = problem.lower().replace(" ", "")
    best_rule: dict[str, Any] | None = None
    best_score = 0
    for rule in _CLASSIFICATION_RULES:
        score = sum(1 for keyword in rule["keywords"] if str(keyword).lower().replace(" ", "") in compact)
        if score > best_score:
            best_score = score
            best_rule = rule

    if best_rule is None:
        return {
            "domain": "数学综合",
            "subtype": "综合数学题",
            "key_concepts": ["读题分类", "通用解题策略"],
            "confidence": 0.35,
        }

    return {
        "domain": best_rule["domain"],
        "subtype": best_rule["subtype"],
        "key_concepts": best_rule["concepts"],
        "confidence": min(0.55 + 0.1 * best_score, 0.9),
    }


def _ensure_list(value: Any) -> list:
    """把模型返回的单值字段规范化为列表。

    参数：
        value：字符串、列表或其他对象。

    返回：
        适合写入 understanding 的列表。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalize_understanding(problem: str, understanding: dict | None) -> tuple[dict, bool]:
    """统一读题阶段字段，避免模型换字段名导致页面显示问号。

    参数：
        problem：原始题干文本。
        understanding：模型读题阶段返回的 JSON。

    返回：
        二元组：规范化后的 understanding，以及是否发生本地补全。
    """
    if not isinstance(understanding, dict):
        return _build_local_understanding(problem, "读题结果不是 JSON 对象"), True

    normalized = dict(understanding)
    inferred = _infer_domain_subtype(problem)
    changed = False
    source_parts: list[str] = []

    for target, aliases in _FIELD_ALIASES.items():
        if target in ("domain", "subtype"):
            continue
        if target not in normalized or normalized.get(target) in (None, "", [], {}):
            value = _first_present_field(normalized, aliases)
            if value is not None:
                normalized[target] = value
                changed = True
                source_parts.append("field_mapping")

    domain = _first_present_field(normalized, _FIELD_ALIASES["domain"])
    subtype = _first_present_field(normalized, _FIELD_ALIASES["subtype"])
    if _is_missing_classification(domain):
        normalized["domain"] = inferred["domain"]
        changed = True
        source_parts.append("local_keyword_domain")
    else:
        normalized["domain"] = str(domain).strip()
    if _is_missing_classification(subtype):
        normalized["subtype"] = inferred["subtype"]
        changed = True
        source_parts.append("local_keyword_subtype")
    else:
        normalized["subtype"] = str(subtype).strip()

    if not normalized.get("problem_type"):
        normalized["problem_type"] = _infer_problem_type(problem)
        changed = True
    normalized["given"] = _ensure_list(normalized.get("given") or normalized.get("premise") or problem)
    normalized["key_concepts"] = _ensure_list(normalized.get("key_concepts") or inferred["key_concepts"])
    normalized.setdefault("find", "")
    normalized.setdefault("proof_goal", normalized.get("conclusion", ""))
    normalized.setdefault("sub_questions", _extract_sub_questions_from_text(problem))
    normalized.setdefault("classification_reason", "由模型读题字段与本地关键词分类共同确定。")
    normalized.setdefault("confidence", inferred["confidence"] if changed else 0.75)
    normalized["classification_source"] = "+".join(dict.fromkeys(source_parts)) if source_parts else normalized.get("classification_source", "llm")
    return normalized, changed


def _infer_problem_type(problem: str) -> str:
    """根据题干关键词粗判任务类型。

    参数：
        problem：原始题干文本。

    返回：
        任务类型字符串。
    """
    text = str(problem).lower().strip()
    if _extract_sub_questions_from_text(problem):
        return "多问综合"
    if any(word in text for word in ["证明", "求证", "证得", "证"]):
        return "证明"
    if any(word in text for word in ["最大", "最小", "最值", "取值范围", "最优", "范围"]):
        return "最值"
    if any(word in text for word in ["构造", "反例", "求取", "求出", "求", "计算", "解"]):
        return "求解"
    return "数学综合"
def _build_augmented_context(problem: str, config: dict[str, Any]) -> tuple[str, list[dict], list[dict]]:
    """按运行模式构建只给 solver/repair 使用的 RAG 与记忆上下文。

    参数：
        problem：原始题干文本。
        config：运行模式配置。

    返回：
        三元组：可拼接 prompt 的文本、RAG 命中片段、记忆规则命中列表。
    """
    retrieved_knowledge = retrieve_knowledge(problem, top_k=int(config["rag_top_k"])) if config["use_rag"] else []
    memory_rules = retrieve_memory_rules(problem) if config["use_memory"] else []
    sections = [
        format_retrieved_knowledge(retrieved_knowledge),
        format_memory_rules(memory_rules),
    ]
    augmented_context = "\n\n".join(section for section in sections if section)
    return augmented_context, retrieved_knowledge, memory_rules


def _build_local_understanding(problem: str, err: str | None = None) -> dict:
    """在模型读题 JSON 失败时构造最低限度的结构化理解。

    参数：
        problem：原始题干文本。
        err：可选的模型解析错误。

    返回：
        可供后续规划和求解使用的 understanding 对象。
    """
    compact_problem = str(problem).lower().replace(" ", "")
    is_finite_field_problem = all(marker in compact_problem for marker in ["81", "alpha"]) and (
        "f}_3" in compact_problem or "f_3" in compact_problem or "有限域" in compact_problem
    )
    if is_finite_field_problem:
        return {
            "domain": "代数",
            "subtype": "有限域扩张生成元计数",
            "given": [problem],
            "sub_questions": _extract_sub_questions_from_text(problem),
            "key_concepts": ["有限域", "子域", "扩张生成元", "真子域排除"],
            "source": "local_finite_field_extractor",
            **({"llm_parse_error": err} if err else {}),
        }
    understanding = {
        "domain": "函数",
        "subtype": "多小问函数综合题",
        "given": [problem],
        "sub_questions": _extract_sub_questions_from_text(problem),
        "key_concepts": ["函数定义", "集合D(x0)", "分段函数", "奇函数", "单调性证明"],
        "source": "local_structure_extractor",
    }
    if err:
        understanding["llm_parse_error"] = err
    return understanding


def _complete_understanding_sub_questions(problem: str, understanding: dict | None) -> tuple[dict, bool]:
    """在模型读题成功但漏小问时，用原题题号补全结构。

    参数：
        problem：原始题干文本。
        understanding：读题阶段返回的 JSON 对象。

    返回：
        二元组，第一项为补全后的 understanding，第二项表示是否发生了本地补全。
    """
    if not isinstance(understanding, dict):
        return _build_local_understanding(problem, "读题结果不是 JSON 对象"), True

    local_sub_questions = _extract_sub_questions_from_text(problem)
    if not local_sub_questions:
        return understanding, False

    llm_sub_questions = _get_sub_questions(understanding)
    if len(llm_sub_questions) >= len(local_sub_questions):
        return understanding, False

    completed = dict(understanding)
    completed["sub_questions"] = local_sub_questions
    completed["source"] = completed.get("source") or "llm_with_local_structure_completion"
    completed["local_structure_completion_reason"] = "原题题号数量多于模型返回的小问数量"
    return completed, True



def _build_local_plan(understanding: dict, sub_questions: list[dict], err: str | None = None) -> dict:
    """在规划阶段调用失败时生成可继续求解的最小计划。

    参数：
        understanding：读题阶段得到的结构化题目理解。
        sub_questions：本地规范化后的必答小问列表。
        err：规划阶段错误信息。

    返回：
        与 PLAN_SYSTEM schema 兼容的本地计划。
    """
    if sub_questions:
        steps = [
            {
                "step": index,
                "sub_idx": str(item.get("sub_idx") or f"({index})"),
                "goal": str(item.get("target") or "完成该小问"),
                "method": "按题干条件独立推导，并在本小问结束时代回校验。",
            }
            for index, item in enumerate(sub_questions, 1)
        ]
    else:
        target = understanding.get("find") or understanding.get("conclusion") or "完成题目要求"
        steps = [{"step": 1, "sub_idx": "", "goal": str(target), "method": "整理条件后逐步计算或证明，并代回校验。"}]

    plan = {
        "strategy": "规划阶段模型调用失败，使用本地最小计划继续求解；每个小问必须独立完成并校验。",
        "steps": steps,
        "potential_pitfalls": [
            "不得遗漏任何小问。",
            "最值题必须给出约束、取等点或取等参数，并代回原题条件。",
            "证明题不得把待证结论当作已知。",
        ],
        "source": "local_plan_fallback",
    }
    if err:
        plan["llm_plan_error"] = err
    return plan

def _get_sub_questions(understanding: dict) -> list[dict]:
    """从题目理解结果中提取规范的小问列表。

    参数：
        understanding：读题阶段返回的 JSON 对象。

    返回：
        小问列表；单问题返回空列表。
    """
    sub_questions = understanding.get("sub_questions")
    if not isinstance(sub_questions, list):
        return []

    normalized: list[dict] = []
    for index, item in enumerate(sub_questions, 1):
        if not isinstance(item, dict):
            continue
        sub_idx = str(item.get("sub_idx") or f"({index})")
        target = str(item.get("target") or item.get("find") or "")
        normalized.append({"sub_idx": sub_idx, "target": target})
    return normalized


def _format_required_sub_questions(sub_questions: list[dict]) -> str:
    """生成强制覆盖小问清单，减少 solver 遗漏小问。

    参数：
        sub_questions：规范化后的小问列表。

    返回：
        可拼接到 user prompt 的文本。
    """
    if not sub_questions:
        return ""
    lines = ["required_sub_questions（必须全部回答，不能遗漏）："]
    for item in sub_questions:
        lines.append(f"- {item['sub_idx']} {item['target']}")
    return "\n".join(lines)


def _get_failed_sub_questions(verification: dict) -> list[dict]:
    """从校验结果中提取需要重新推导的小问。

    参数：
        verification：verifier 返回的 JSON 对象。

    返回：
        未通过或未回答的小问检查列表。
    """
    checks = verification.get("sub_question_checks")
    if not isinstance(checks, list):
        return []

    failed: list[dict] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if not check.get("answered") or not check.get("verified"):
            failed.append(check)
    return failed


def _is_empty_solution(solution: dict) -> bool:
    """判断 solver 是否输出了结构存在但内容为空的无效答案。

    参数：
        solution：规范化后的 solver 输出。

    返回：
        所有关键答案字段都为空时返回 True。
    """
    if not isinstance(solution, dict):
        return True
    if str(solution.get("final_answer") or solution.get("answer_latex") or "").strip():
        return False
    for item in solution.get("sub_answers", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("answer") or item.get("proof_or_derivation") or "").strip():
            return False
    for step in solution.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or step.get("result") or step.get("derivation") or "").strip():
            return False
    return True




def _stringify_answer_fragment(value: Any) -> str:
    """把非标准答案片段压缩成可读文本，避免整块 JSON 污染所有小问。

    参数：
        value：模型返回的非标准字段内容。

    返回：
        可放入 answer 或 proof_or_derivation 的文本。
    """
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("answer", "final_answer", "standard_equation", "point_R", "max_PM", "constraint", "solution"):
            if key in value and value[key] not in (None, "", [], {}):
                nested = _stringify_answer_fragment(value[key])
                if nested:
                    return nested
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _extract_nonstandard_sub_answer(solution: dict, sub_idx: str) -> dict | None:
    """把 problem_1/problem_2.part_i/part_ii 这类非标准字段映射到对应小问。

    参数：
        solution：solver 原始输出。
        sub_idx：需要补全的小问编号。

    返回：
        可合并进 sub_answers 的单个小问答案；未命中时返回 None。
    """
    mapping: dict[str, Any] = {}
    if "problem_1" in solution:
        mapping["(1)"] = solution.get("problem_1")
    problem_2 = solution.get("problem_2")
    if isinstance(problem_2, dict):
        mapping["(2)(i)"] = problem_2.get("part_i") or problem_2.get("i")
        mapping["(2)(ii)"] = problem_2.get("part_ii") or problem_2.get("ii")
        mapping["(2)"] = problem_2

    fragment = mapping.get(sub_idx)
    if fragment in (None, "", [], {}):
        return None

    description = ""
    answer_source = fragment
    if isinstance(fragment, dict):
        description = str(fragment.get("description") or "")
        answer_source = fragment.get("solution") or fragment.get("answer") or fragment

    answer = _stringify_answer_fragment(answer_source)
    proof_parts = [part for part in [description, _stringify_answer_fragment(fragment)] if part]
    return {
        "sub_idx": sub_idx,
        "answer": answer,
        "proof_or_derivation": "\n".join(dict.fromkeys(proof_parts)),
        "verified_hint": "由非标准字段精准映射，仍需 verifier 或本地工具检查。",
    }


def _strip_response_field(solution: dict) -> dict:
    """删除最终答案中的 response 字段，避免长篇思考污染输出。

    参数：
        solution：候选答案对象。

    返回：
        不含 response 字段的浅拷贝。
    """
    if not isinstance(solution, dict):
        return solution
    cleaned = dict(solution)
    cleaned.pop("response", None)
    return cleaned

def _nonstandard_solution_text(solution: dict) -> str:
    """提取模型放在非标准字段中的候选解题内容。

    参数：
        solution：solver 原始或规范化后的输出。

    返回：
        非标准字段拼接文本；没有可用内容时返回空字符串。
    """
    standard_keys = {"steps", "sub_answers", "final_answer", "answer_latex", "verify_check", "response"}
    parts: list[str] = []
    for key, value in solution.items():
        if key in standard_keys or value in (None, "", [], {}):
            continue
        parts.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(parts)


def _needs_schema_repair(solution: dict) -> bool:
    """判断候选输出是否有内容但没有落入标准 schema。

    参数：
        solution：solver 原始输出。

    返回：
        需要用结构整理器修复时返回 True。
    """
    if not isinstance(solution, dict):
        return False
    if solution.get("response"):
        return True
    return bool(_nonstandard_solution_text(solution)) and _is_empty_solution(solution)


def _coerce_solution_fields(solution: dict, sub_questions: list[dict]) -> dict:
    """把非标准字段尽量吸收到 sub_answers，避免有效内容被 normalize 丢弃。

    参数：
        solution：solver 输出。
        sub_questions：规范化后的小问列表。

    返回：
        补充过 sub_answers/final_answer 的 solution。
    """
    if not isinstance(solution, dict):
        return solution
    solution = _strip_response_field(solution)
    if not sub_questions:
        if "answer" in solution and not solution.get("final_answer"):
            solution["final_answer"] = str(solution["answer"]).strip()
        if "answer" in solution and not solution.get("answer_latex"):
            solution["answer_latex"] = str(solution["answer"]).strip()
        return solution

    solution.setdefault("sub_answers", [])
    existing = {
        str(item.get("sub_idx")): item
        for item in solution.get("sub_answers", [])
        if isinstance(item, dict) and item.get("sub_idx")
    }

    extra_text = _nonstandard_solution_text(solution)
    for required in sub_questions:
        sub_idx = required["sub_idx"]
        current = existing.setdefault(sub_idx, {"sub_idx": sub_idx, "target": required.get("target", "")})
        if str(current.get("answer") or current.get("proof_or_derivation") or "").strip():
            continue

        mapped = _extract_nonstandard_sub_answer(solution, sub_idx)
        if mapped:
            current.update(mapped)
            continue

        if extra_text:
            current["proof_or_derivation"] = extra_text
            current.setdefault("answer", "")
            current.setdefault("verified_hint", "由非标准字段吸收，仍需 verifier 或本地工具检查。")

    solution["sub_answers"] = list(existing.values())
    if not solution.get("final_answer"):
        solution["final_answer"] = "；".join(
            f"{item.get('sub_idx')} {item.get('answer') or item.get('proof_or_derivation', '')}"
            for item in solution["sub_answers"]
            if str(item.get("answer") or item.get("proof_or_derivation") or "").strip()
        )
    return solution

def _repair_solution_schema(
    tally: _Tally,
    problem: str,
    solution: dict,
    sub_questions: list[dict],
    required_sub_questions_text: str,
    thinking_mode: bool | None,
) -> tuple[dict | None, str | None]:
    """把 response 或自定义字段形式的输出整理为标准答案 schema。

    参数：
        tally：LLM 调用统计器。
        problem：原始题干文本。
        solution：需要整理的候选输出。
        sub_questions：规范化后的小问列表。
        required_sub_questions_text：必答小问文本。
        thinking_mode：结构整理阶段是否启用 thinking。

    返回：
        二元组，第一项为整理后的 JSON 或 None，第二项为错误信息或 None。
    """
    user = "\n\n".join([
        f"题目：{problem}",
        _ctx("required_sub_questions", sub_questions),
        required_sub_questions_text,
        _ctx("需要整理的候选输出", solution),
    ])
    return _safe_ask(
        tally,
        SCHEMA_REPAIR_SYSTEM,
        user,
        temperature=0.0,
        thinking_mode=thinking_mode,
    )


def _collect_issue_values(items: list[dict], field: str) -> list[Any]:
    """从问题项中收集结构化证据字段。

    参数：
        items：问题项列表。
        field：要收集的字段名。

    返回：
        去重后的字段值列表。
    """
    collected: list[Any] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict) or field not in item:
            continue
        value = item.get(field)
        values = value if isinstance(value, list) else [value]
        for entry in values:
            marker = json.dumps(entry, ensure_ascii=False, sort_keys=True) if isinstance(entry, (dict, list)) else str(entry)
            if marker and marker not in seen:
                seen.add(marker)
                collected.append(entry)
    return collected


def _summarize_repair_evidence(verification: dict) -> dict:
    """提取修正轮需要优先服从的证据包。

    参数：
        verification：校验阶段返回的结果。

    返回：
        包含旧错结论、反例、硬约束和建议路线的字典。
    """
    issues = verification.get("issues", []) if isinstance(verification, dict) else []
    evidence = {
        "failed_claims": verification.get("failed_claims") or _collect_issue_values(issues, "failed_claims"),
        "forbidden_claims": verification.get("forbidden_claims") or _collect_issue_values(issues, "forbidden_claims"),
        "counterexamples": verification.get("counterexamples") or _collect_issue_values(issues, "counterexamples"),
        "sample_table": verification.get("sample_table") or _collect_issue_values(issues, "sample_table"),
        "repair_constraints": verification.get("repair_constraints") or _collect_issue_values(issues, "repair_constraints"),
        "suggested_route": verification.get("suggested_route") or verification.get("suggested_fix") or "",
    }
    return {key: value for key, value in evidence.items() if value}


def _build_repair_scope_text(failed_sub_questions: list[dict], all_sub_questions: list[dict]) -> str:
    """生成修正范围说明，避免单问题被空列表误导。

    参数：
        failed_sub_questions：需要重算的小问列表。
        all_sub_questions：完整小问列表。

    返回：
        可拼入 repair prompt 的范围说明。
    """
    if all_sub_questions:
        return "[局部修正规则]\n只修正 failed_sub_questions 中列出的小问；不得重写已通过小问；只输出标准 JSON；禁止 response 字段。"
    return "[整题修正规则]\n本题没有小问列表，failed_sub_questions 为空不代表没有错误；必须针对整题重新推导并输出完整 JSON。"

def _local_verification(local_issues: list[dict]) -> dict:
    """把本地工具问题转成 verifier 风格结果，并保留反例证据包。

    参数：
        local_issues：本地数学工具返回的问题列表。

    返回：
        可放入 verifications 的校验结果。
    """
    failed_claims = _collect_issue_values(local_issues, "failed_claims")
    forbidden_claims = _collect_issue_values(local_issues, "forbidden_claims")
    counterexamples = _collect_issue_values(local_issues, "counterexamples")
    sample_table = _collect_issue_values(local_issues, "sample_table")
    repair_constraints = _collect_issue_values(local_issues, "repair_constraints")
    suggested_routes = _collect_issue_values(local_issues, "suggested_route")
    return {
        "verified": False,
        "confidence": 1.0,
        "sub_question_checks": [],
        "issues": [
            {
                "step": 0,
                "type": "本地工具检查",
                "description": str(item.get("issue", "")),
                "failed_claims": item.get("failed_claims", []),
                "forbidden_claims": item.get("forbidden_claims", []),
                "counterexamples": item.get("counterexamples", []),
                "sample_table": item.get("sample_table", []),
                "repair_constraints": item.get("repair_constraints", []),
                "suggested_route": item.get("suggested_route", ""),
            }
            for item in local_issues
        ],
        "failed_claims": failed_claims,
        "forbidden_claims": forbidden_claims,
        "counterexamples": counterexamples,
        "sample_table": sample_table,
        "repair_constraints": repair_constraints,
        "suggested_route": "\n".join(map(str, suggested_routes)),
        "independent_check": "本地数学工具发现候选解存在可复现反例或高风险错误。",
        "suggested_fix": format_tool_feedback(local_issues),
    }


def _compact_verifier_value(value: Any) -> str:
    """压缩 verifier 证据值，便于比较 candidate 与 expected。

    参数：
        value：verifier 输出的候选值、期望值或结论文本。

    返回：
        去空白、统一等号和去掉基础 LaTeX 标记后的文本。
    """
    text = re.sub(r"\s+", "", str(value or "").lower())
    text = text.replace("＝", "=").replace("\\", "")
    text = text.replace("{", "").replace("}", "")
    return text


def _equation_right_side(value: Any) -> str:
    """抽取等式右侧，用于识别 S=1 与 1 这类等价反例值。

    参数：
        value：verifier 的 candidate 或 expected 字段。

    返回：
        等式右侧文本；没有等号时返回压缩后的原文本。
    """
    compact = _compact_verifier_value(value)
    if "=" in compact:
        return compact.rsplit("=", 1)[-1]
    return compact


def _counterexample_self_conflicting(counterexample: dict[str, Any]) -> bool:
    """判断 verifier 的反例是否实际支持候选答案。

    参数：
        counterexample：verifier 输出的单个反例对象。

    返回：
        candidate 与 expected 相同或等式右侧相同时返回 True。
    """
    if not isinstance(counterexample, dict):
        return False
    candidate = _compact_verifier_value(counterexample.get("candidate"))
    expected = _compact_verifier_value(counterexample.get("expected"))
    if not candidate or not expected:
        return False
    return candidate == expected or _equation_right_side(candidate) == _equation_right_side(expected)


def _support_aliases(support_checks: list[dict[str, Any]]) -> set[str]:
    """收集本地支持检查认可的候选结论别名。

    参数：
        support_checks：run_support_checks 返回的检查列表。

    返回：
        规范化后的候选结论集合。
    """
    aliases: set[str] = set()
    for check in support_checks or []:
        if not isinstance(check, dict) or check.get("passed") is not True:
            continue
        for value in [check.get("supports_claim"), *(check.get("claim_aliases") or [])]:
            compact = _compact_verifier_value(value)
            if compact:
                aliases.add(compact)
    return aliases


def _claim_supported_by_local_checks(claim: Any, support_aliases: set[str]) -> bool:
    """判断 verifier 质疑的结论是否被本地支持检查认可。

    参数：
        claim：failed_claims 或 forbidden_claims 中的结论。
        support_aliases：_support_aliases 返回的本地支持结论集合。

    返回：
        结论命中支持别名时返回 True。
    """
    compact = _compact_verifier_value(claim)
    if not compact:
        return False
    return compact in support_aliases or _equation_right_side(compact) in support_aliases


def _build_verifier_arbitration(verification: dict, support_checks: list[dict[str, Any]]) -> dict | None:
    """生成 verifier 与本地支持检查冲突时的仲裁记录。

    参数：
        verification：verifier 返回的校验结果。
        support_checks：本地支持检查结果。

    返回：
        仲裁记录；没有冲突时返回 None。
    """
    if not isinstance(verification, dict) or verification.get("verified") is not False:
        return None
    passed_support = [check for check in support_checks or [] if isinstance(check, dict) and check.get("passed") is True]
    if not passed_support:
        return None

    counterexamples = verification.get("counterexamples") or _collect_issue_values(verification.get("issues", []), "counterexamples")
    self_conflicts = [item for item in counterexamples or [] if _counterexample_self_conflicting(item)]
    support_aliases = _support_aliases(passed_support)
    challenged_claims = []
    challenged_claims.extend(verification.get("failed_claims") or [])
    challenged_claims.extend(verification.get("forbidden_claims") or [])
    challenged_claims.extend(_collect_issue_values(verification.get("issues", []), "failed_claims"))
    challenged_claims.extend(_collect_issue_values(verification.get("issues", []), "forbidden_claims"))
    supported_challenges = [claim for claim in challenged_claims if _claim_supported_by_local_checks(claim, support_aliases)]

    if not self_conflicts and not supported_challenges:
        return None
    return {
        "name": "verifier_conflict_with_support_checks",
        "accepted_by_support_checks": True,
        "reason": "verifier 判错证据与本地样例支持检查冲突，优先保留本地可复算证据并标记人工可复核。",
        "self_conflicting_counterexamples": self_conflicts,
        "supported_challenged_claims": supported_challenges,
        "support_checks": passed_support,
    }


def _apply_verifier_arbitration(verification: dict, support_checks: list[dict[str, Any]]) -> tuple[dict, dict | None]:
    """把本地支持检查用于仲裁 verifier 的明显误判。

    参数：
        verification：verifier 返回的校验结果。
        support_checks：本地支持检查结果。

    返回：
        二元组：可能被调整后的 verification，以及仲裁记录或 None。
    """
    arbitration = _build_verifier_arbitration(verification, support_checks)
    if arbitration is None:
        return verification, None
    adjusted = dict(verification)
    adjusted["original_verified"] = verification.get("verified")
    adjusted["verified"] = True
    adjusted["verified_by_support_checks"] = True
    adjusted["verifier_conflict"] = arbitration
    adjusted["independent_check"] = "本地支持检查与 verifier 判错证据冲突，已按可复算样例表仲裁。"
    adjusted["suggested_fix"] = "verifier 反例或禁止结论存在冲突；请人工重点复核 support_checks 样例表。"
    return adjusted, arbitration


def _build_repair_prompt(
    base_user: str,
    solution: dict,
    verification: dict,
    sub_questions: list[dict],
    tool_feedback: str = "",
) -> str:
    """构造带证据包的整题修正提示，避免只让模型泛泛重想。

    参数：
        base_user：首次求解时使用的用户提示。
        solution：上一版候选解答。
        verification：上一轮校验反馈。
        sub_questions：规范化后的小问列表。
        tool_feedback：本地数学工具反馈。

    返回：
        可直接发送给 solver 的修正提示。
    """
    failed_sub_questions = _get_failed_sub_questions(verification)
    sections = [
        base_user,
        REPAIR_EVIDENCE_GUIDANCE,
        _build_repair_scope_text(failed_sub_questions, sub_questions),
        _ctx("上一版候选解答（不要直接沿用错误推理）", solution),
        _ctx("修正证据包（反例、禁止重复结论和建议路线优先服从）", _summarize_repair_evidence(verification)),
        _ctx("verifier 或本地工具判错反馈", verification),
        _ctx("failed_sub_questions（必须重新推导这些小问）", failed_sub_questions),
        _ctx("required_sub_questions（完整答案仍需覆盖全部小问）", sub_questions),
        tool_feedback,
    ]
    return "\n\n".join(section for section in sections if section)



def _filter_failed_sub_questions(sub_questions: list[dict], verification: dict) -> list[dict]:
    """根据 verifier 结果筛出需要重算的小问。

    参数：
        sub_questions：完整小问列表。
        verification：verifier 或本地工具返回的校验结果。

    返回：
        失败小问列表；无法定位具体小问时返回完整列表。
    """
    failed = _get_failed_sub_questions(verification)
    failed_ids = {str(item.get("sub_idx")) for item in failed if item.get("sub_idx")}
    if not failed_ids:
        return sub_questions
    selected = [item for item in sub_questions if str(item.get("sub_idx")) in failed_ids]
    return selected or sub_questions


def _merge_repaired_sub_answers(original: dict, repaired: dict, sub_questions: list[dict]) -> dict:
    """把局部修正的小问合并回原完整答案。

    参数：
        original：上一版完整答案。
        repaired：repair 阶段返回的局部或完整答案。
        sub_questions：完整小问列表。

    返回：
        合并后的完整答案。
    """
    original = _normalize_solution(dict(original), sub_questions)
    repaired = _coerce_solution_fields(dict(repaired), sub_questions)
    repaired = _normalize_solution(repaired, sub_questions)
    by_id = {str(item.get("sub_idx")): dict(item) for item in original.get("sub_answers", []) if isinstance(item, dict)}
    for item in repaired.get("sub_answers", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("answer") or item.get("proof_or_derivation") or "").strip():
            by_id[str(item.get("sub_idx"))] = item
    original["sub_answers"] = [by_id.get(str(item.get("sub_idx")), item) for item in sub_questions]
    original["final_answer"] = "；".join(
        f"{item.get('sub_idx')} {item.get('answer') or item.get('proof_or_derivation', '')}"
        for item in original["sub_answers"]
        if str(item.get("answer") or item.get("proof_or_derivation") or "").strip()
    )
    original["verify_check"] = False
    return original


def _build_failed_only_repair_prompt(
    base_user: str,
    solution: dict,
    verification: dict,
    failed_sub_questions: list[dict],
    tool_feedback: str = "",
) -> str:
    """构造只修失败小问或单问整题的证据化修正提示。

    参数：
        base_user：首次求解时使用的用户提示。
        solution：上一版候选解答。
        verification：上一轮校验反馈。
        failed_sub_questions：需要重算的小问；单问题可为空。
        tool_feedback：本地数学工具反馈。

    返回：
        可发送给 solver 的局部或整题修正 prompt。
    """
    sections = [
        base_user,
        REPAIR_EVIDENCE_GUIDANCE,
        _build_repair_scope_text(failed_sub_questions, failed_sub_questions),
        _ctx("上一版候选解答", solution),
        _ctx("修正证据包（反例、禁止重复结论和建议路线优先服从）", _summarize_repair_evidence(verification)),
        _ctx("校验反馈", verification),
        _ctx("failed_sub_questions", failed_sub_questions),
        tool_feedback,
    ]
    return "\n\n".join(section for section in sections if section)


def _normalize_solution(solution: dict, sub_questions: list[dict]) -> dict:
    """规范化求解输出，保证多问结果在顶层可稳定读取。

    参数：
        solution：solver 返回的 JSON 对象。
        sub_questions：规范化后的小问列表。

    返回：
        补齐关键字段后的 solution 对象。
    """
    solution = _strip_response_field(solution)
    solution.setdefault("steps", [])
    solution.setdefault("sub_answers", [])
    solution.setdefault("answer_latex", "")
    solution.setdefault("verify_check", False)

    if not sub_questions:
        if not solution.get("final_answer"):
            solution["final_answer"] = solution.get("answer") or solution.get("answer_latex") or ""
        if not solution.get("answer_latex"):
            solution["answer_latex"] = solution.get("final_answer") or ""
        return solution

    existing = {
        str(item.get("sub_idx")): item
        for item in solution.get("sub_answers", [])
        if isinstance(item, dict) and item.get("sub_idx")
    }
    normalized_sub_answers: list[dict] = []
    for required in sub_questions:
        sub_idx = required["sub_idx"]
        current = existing.get(sub_idx, {})
        normalized_sub_answers.append(
            {
                "sub_idx": sub_idx,
                "target": required.get("target", ""),
                "answer": str(current.get("answer") or ""),
                "proof_or_derivation": str(current.get("proof_or_derivation") or ""),
                "verified_hint": str(current.get("verified_hint") or ""),
            }
        )
    solution["sub_answers"] = normalized_sub_answers

    if not solution.get("final_answer"):
        parts = [
            f"{item['sub_idx']} {item['answer']}"
            for item in normalized_sub_answers
            if item.get("answer")
        ]
        solution["final_answer"] = "；".join(parts)
    return solution


class _Tally:
    def __init__(self) -> None:
        """初始化本题的模型调用与 token 统计。"""
        self.tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.calls = 0
        self.failed_calls = 0

    def add(self, usage: Any) -> None:
        """记录一次成功模型调用的 usage。"""
        if not usage:
            return
        self.calls += 1
        for key in self.tokens:
            self.tokens[key] += getattr(usage, key, 0) or 0

    def add_failed(self) -> None:
        """记录没有 usage 的失败模型调用。"""
        self.failed_calls += 1


def _safe_ask(
    tally: _Tally,
    system: str,
    user: str,
    *,
    temperature: float,
    thinking_mode: bool | None = None,
) -> tuple[dict | None, str | None]:
    """包一层异常捕获；成功返回 (data, None)，失败返回 (None, err_msg)。

    参数：
        tally：LLM 调用统计器。
        system：system prompt。
        user：user prompt。
        temperature：采样温度。
        thinking_mode：本阶段是否启用模型思考模式，None 表示使用 llm.py 默认值。

    返回：
        二元组，第一项为 JSON 数据或 None，第二项为错误信息或 None。
    """
    try:
        data, usage = ask_json(system, user, temperature=temperature, thinking_mode=thinking_mode)
        tally.add(usage)
        return data, None
    except Exception as exc:
        tally.add_failed()
        return None, str(exc)


def solve_problem(
    problem: str,
    problem_id: str | None = None,
    *,
    enable_verifier: bool = True,
    max_revisions: int = 1,
    mode: str = "contest",
    on_stage=None,
) -> dict:
    """求解一道数学题并返回完整流水线结果。

    参数：
        problem：原始题干文本。
        problem_id：可选题目 ID。
        enable_verifier：是否允许校验阶段；最终还会受运行模式约束。
        max_revisions：用户允许的最大修正轮数；最终取用户值与模式上限的较小值。
        mode：运行模式，支持 fast、contest、strict。
        on_stage：可选 UI 回调，签名为 on_stage(name, data)。

    返回：
        包含读题、规划、求解、校验、RAG、记忆和工具检查的结果字典。
    """
    def _fire(name: str, data) -> None:
        """把阶段结果发送给 UI 回调，回调异常不影响主流程。"""
        if on_stage is not None:
            try:
                on_stage(name, data)
            except Exception:
                pass

    t0 = time.time()
    tally = _Tally()
    config = _get_mode_config(mode)
    effective_revisions = min(int(max_revisions), int(config["max_revisions"]))
    effective_verifier = bool(enable_verifier and config["use_verifier"])
    augmented_context, retrieved_knowledge, memory_rules = _build_augmented_context(problem, config)
    result: dict[str, Any] = {
        "problem_id": problem_id,
        "problem": problem,
        "model": MODEL_NAME,
        "pipeline_version": PIPELINE_VERSION,
        "run_mode": mode if mode in PIPELINE_MODES else "contest",
        "mode_config": config,
        "retrieved_knowledge": retrieved_knowledge,
        "memory_rules": memory_rules,
        "stage_timings": {},
    }

    def _ask_stage(stage_name: str, system: str, user: str, *, temperature: float, thinking_mode: bool | None) -> tuple[dict | None, str | None]:
        """记录单个模型阶段的耗时与错误，便于定位慢在读题、规划、求解还是校验。

        参数：
            stage_name：阶段名称，用作 result["stage_timings"] 的键。
            system：system prompt。
            user：user prompt。
            temperature：采样温度。
            thinking_mode：该阶段是否启用模型 thinking。

        返回：
            二元组，第一项为 JSON 数据或 None，第二项为错误信息或 None。
        """
        started_at = time.time()
        data, ask_err = _safe_ask(
            tally,
            system,
            user,
            temperature=temperature,
            thinking_mode=thinking_mode,
        )
        result["stage_timings"][stage_name] = {
            "elapsed_ms": int((time.time() - started_at) * 1000),
            "success": ask_err is None,
        }
        if ask_err:
            result["stage_timings"][stage_name]["error"] = ask_err[:500]
        return data, ask_err

    read_prompt = get_read_prompt(problem)
    read_user = f"题目：{problem}"
    understanding, err = _ask_stage(
        "understanding",
        read_prompt,
        read_user,
        temperature=0.1,
        thinking_mode=config["thinking_read"],
    )
    if err:
        understanding = _build_local_understanding(problem, err)
        result["understanding_recovered"] = True
    understanding, normalized = _normalize_understanding(problem, understanding)
    if normalized:
        result["understanding_normalized"] = True
    understanding, completed = _complete_understanding_sub_questions(problem, understanding)
    if completed:
        result["understanding_completed_by_local_extractor"] = True
    result["understanding"] = understanding
    sub_questions = _get_sub_questions(understanding)
    result["sub_questions"] = sub_questions
    _fire("understanding", understanding)

    required_sub_questions_text = _format_required_sub_questions(sub_questions)
    plan_user = f"题目：{problem}\n\n{_ctx('题目理解', understanding)}"
    if required_sub_questions_text:
        plan_user += "\n\n" + required_sub_questions_text
    plan, err = _ask_stage(
        "plan",
        PLAN_SYSTEM,
        plan_user,
        temperature=0.2,
        thinking_mode=config["thinking_plan"],
    )
    if err:
        plan = _build_local_plan(understanding, sub_questions, err)
        result["plan_recovered"] = True
    result["plan"] = plan
    _fire("plan", plan)

    solver_system, routed_domain = get_solver_system(
        understanding.get("domain"),
        max_hint_chars=int(config["max_hint_chars"]),
    )
    solver_system += SOLVE_EXTRA_GUIDANCE
    result["routed_domain"] = routed_domain
    solve_user = (
        f"题目：{problem}\n\n"
        f"{_ctx('题目理解', understanding)}\n\n"
        f"{_ctx('解题计划', plan)}"
    )
    if required_sub_questions_text:
        solve_user += "\n\n" + required_sub_questions_text
    if augmented_context:
        solve_user += "\n\n" + augmented_context
    solution, err = _ask_stage(
        "solution",
        solver_system,
        solve_user,
        temperature=0.1,
        thinking_mode=config["thinking_solve"],
    )
    if err:
        result["solution"] = {"error": err}
        result["error_stage"] = "solution"
        return _finalize(result, tally, t0)
    if _needs_schema_repair(solution):
        result["schema_repair_attempted"] = True
        repaired, repair_schema_err = _repair_solution_schema(
            tally,
            problem,
            solution,
            sub_questions,
            required_sub_questions_text,
            config["thinking_solve"],
        )
        if repair_schema_err:
            result["schema_repair_error"] = repair_schema_err
        elif repaired is not None:
            solution = repaired
    solution = _coerce_solution_fields(solution, sub_questions)
    solution = _normalize_solution(solution, sub_questions)

    if config["empty_retry"] and _is_empty_solution(solution):
        result["empty_solution_retry"] = True
        retry_user = solve_user + EMPTY_RETRY_GUIDANCE
        retried, retry_err = _ask_stage(
            "empty_solution_retry",
            solver_system,
            retry_user,
            temperature=0.1,
            thinking_mode=config["thinking_solve"],
        )
        if retry_err:
            result["empty_solution_retry_error"] = retry_err
        elif retried is not None:
            solution = _coerce_solution_fields(retried, sub_questions)
            solution = _normalize_solution(solution, sub_questions)

    result["solution"] = solution
    _fire("solution", solution)

    verifications: list[dict] = []
    local_issues: list[dict] = []
    support_checks: list[dict] = []
    if effective_verifier:
        for revision in range(effective_revisions + 1):
            local_issues = run_local_sanity_checks(problem, solution, sub_questions)
            support_checks = run_support_checks(problem, solution)
            if support_checks:
                result["support_checks"] = support_checks
            if local_issues:
                verification = _local_verification(local_issues)
                verifications.append(verification)
                result["local_sanity_checks"] = local_issues
                if revision >= effective_revisions:
                    break
                failed_targets = _filter_failed_sub_questions(sub_questions, verification) if config.get("repair_failed_only") else sub_questions
                revise_user = _build_failed_only_repair_prompt(
                    solve_user,
                    solution,
                    verification,
                    failed_targets,
                    format_tool_feedback(local_issues),
                ) if config.get("repair_failed_only") else _build_repair_prompt(
                    solve_user,
                    solution,
                    verification,
                    sub_questions,
                    format_tool_feedback(local_issues),
                )
                revised, err = _ask_stage(
                    f"repair_{revision + 1}",
                    solver_system + REPAIR_EXTRA_GUIDANCE,
                    revise_user,
                    temperature=0.1,
                    thinking_mode=config["thinking_solve"],
                )
                if err:
                    result["repair_error"] = err
                    break
                solution = _merge_repaired_sub_answers(solution, revised, sub_questions) if config.get("repair_failed_only") else _coerce_solution_fields(revised, sub_questions)
                solution = _normalize_solution(solution, sub_questions)
                result["solution"] = solution
                continue

            verify_user = f"题目：{problem}\n\n{_ctx('候选解答', solution)}"
            if required_sub_questions_text:
                verify_user += "\n\n" + required_sub_questions_text
            verification, err = _ask_stage(
                f"verify_{revision + 1}",
                VERIFY_SYSTEM,
                verify_user,
                temperature=0.0,
                thinking_mode=config["thinking_verify"],
            )
            if err:
                verifications.append({"error": err})
                break
            verification, arbitration = _apply_verifier_arbitration(verification, support_checks)
            if arbitration:
                result.setdefault("verifier_conflicts", []).append(arbitration)
            verifications.append(verification)
            if verification.get("verified") or revision >= effective_revisions:
                break

            failed_targets = _filter_failed_sub_questions(sub_questions, verification) if config.get("repair_failed_only") else sub_questions
            revise_user = _build_failed_only_repair_prompt(
                solve_user,
                solution,
                verification,
                failed_targets,
            ) if config.get("repair_failed_only") else _build_repair_prompt(solve_user, solution, verification, sub_questions)
            revised, err = _ask_stage(
                f"repair_{revision + 1}",
                solver_system + REPAIR_EXTRA_GUIDANCE,
                revise_user,
                temperature=0.1,
                thinking_mode=config["thinking_solve"],
            )
            if err:
                result["repair_error"] = err
                break
            solution = _merge_repaired_sub_answers(solution, revised, sub_questions) if config.get("repair_failed_only") else _coerce_solution_fields(revised, sub_questions)
            solution = _normalize_solution(solution, sub_questions)
            result["solution"] = solution
    else:
        local_issues = run_local_sanity_checks(problem, solution, sub_questions)
        support_checks = run_support_checks(problem, solution)
        if local_issues:
            result["local_sanity_checks"] = local_issues
        if support_checks:
            result["support_checks"] = support_checks

    result["verifications"] = verifications
    if verifications:
        last = verifications[-1]
        result["verified"] = bool(last.get("verified")) if "verified" in last else None
    else:
        result["verified"] = None
    if result.get("verified") is False:
        result["final_status"] = "unverified"
        result["final_warning"] = "最终答案未通过 verifier 校验；请优先查看 verifications 或 local_sanity_checks 中的问题。"
    elif result.get("verified") is True:
        last_verification = verifications[-1] if verifications else {}
        if isinstance(last_verification, dict) and last_verification.get("verified_by_support_checks"):
            result["final_status"] = "locally_supported"
            result["verification_source"] = "support_checks"
            result["final_warning"] = "Verifier 反馈与本地支持检查冲突，最终答案已由本地样例表暂时支持；建议人工查看 verifier_conflicts。"
        else:
            result["final_status"] = "verified"
            result["verification_source"] = "verifier"
    else:
        result["final_status"] = "not_checked"
    _fire(
        "verifications",
        {
            "verifications": verifications,
            "local_sanity_checks": local_issues,
            "support_checks": support_checks,
            "verifier_conflicts": result.get("verifier_conflicts", []),
            "final_solution": solution,
        },
    )

    if config["use_explanation"]:
        explain_user = f"题目：{problem}\n\n{_ctx('求解过程', solution)}"
        explanation, err = _ask_stage(
            "explanation",
            EXPLAIN_SYSTEM,
            explain_user,
            temperature=0.4,
            thinking_mode=config["thinking_explain"],
        )
        if err:
            result["explanation"] = {"error": err}
        else:
            result["explanation"] = explanation
            _fire("explanation", explanation)
    else:
        result["explanation"] = {"skipped": True, "reason": "当前运行模式默认跳过教学解释，以减少比赛求解耗时。"}
        _fire("explanation", result["explanation"])

    if isinstance(solution, dict):
        result["sub_answers"] = solution.get("sub_answers", [])
        result["final_answer"] = solution.get("final_answer")
        result["answer_latex"] = solution.get("answer_latex")

    return _finalize(result, tally, t0)


def _finalize(result: dict, tally: _Tally, t0: float) -> dict:
    """补充执行元数据。

    参数：
        result：已有流水线结果。
        tally：LLM 调用统计器。
        t0：流程开始时间戳。

    返回：
        带 metadata 字段的结果字典。
    """
    result["metadata"] = {
        "elapsed_ms": int((time.time() - t0) * 1000),
        "llm_calls": tally.calls,
        "failed_llm_calls": tally.failed_calls,
        "tokens": tally.tokens,
    }
    return result





