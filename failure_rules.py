"""失败复盘规则库：把 result JSON 转成可执行的维护建议。

本文件包括：失败类型判断、触发特征提取、沉淀层级建议、memory/RAG/tool 草稿生成。
本文件控制：failure_review.py 如何判断一次失败应该沉淀到 memory、knowledge_base、math_tools、prompts 或 user_agent。
优化时这样操作：
- 新增失败模式时，优先添加一个独立的 detect_xxx 函数。
- 不要在这里调用 LLM，也不要直接修改 memory、knowledge_base 或代码文件。
- 只输出“建议”，由维护者确认后再写入对应层级，避免错误经验污染系统。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewFinding:
    """一次失败复盘发现。

    参数：
        failure_type：失败类型，例如 solver_wrong_formula 或 verifier_conflict。
        severity：严重程度，数值越高越需要优先处理。
        reason：为什么判定为该失败类型。
        evidence：从 result JSON 中摘出的关键证据。
        recommended_targets：建议沉淀到哪些文件或系统层。
    """

    failure_type: str
    severity: int
    reason: str
    evidence: list[str] = field(default_factory=list)
    recommended_targets: list[str] = field(default_factory=list)


def compact_text(value: Any) -> str:
    """把任意对象压成便于关键词判断的文本。

    参数：
        value：result JSON 中的任意字段。

    返回：
        小写、去空白后的文本。
    """
    return re.sub(r"\s+", "", str(value or "").lower())


def flatten_result_text(result: dict[str, Any]) -> str:
    """把题干、理解、计划、答案和校验反馈压成检索文本。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        拼接后的纯文本。
    """
    parts: list[str] = []
    for key in ("problem", "answer_latex", "final_answer", "routed_domain", "final_status"):
        if result.get(key):
            parts.append(str(result.get(key)))
    for key in ("understanding", "plan", "solution", "verifications", "local_sanity_checks", "support_checks", "verifier_conflicts"):
        if result.get(key) is not None:
            parts.append(str(result.get(key)))
    return "\n".join(parts)


def extract_triggers(result: dict[str, Any]) -> list[str]:
    """提取可用于 memory/RAG 的题目触发特征。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        去重后的触发词列表。
    """
    text = compact_text(result.get("problem", ""))
    candidates = [
        "floor", "\\lfloor", "⌊", "取整",
        "sum", "\\sum", "∑", "和式",
        "2^k", "2^{k}", "2^{k+1}", "二进制",
        "椭圆", "轨迹", "最值", "函数", "单调", "概率", "矩阵", "特征值",
    ]
    seen: set[str] = set()
    triggers: list[str] = []
    for item in candidates:
        if compact_text(item) in text and item not in seen:
            seen.add(item)
            triggers.append(item)
    domain = result.get("understanding", {}).get("domain") if isinstance(result.get("understanding"), dict) else None
    subtype = result.get("understanding", {}).get("subtype") if isinstance(result.get("understanding"), dict) else None
    for item in (domain, subtype, result.get("routed_domain")):
        if item and str(item) not in seen:
            seen.add(str(item))
            triggers.append(str(item))
    return triggers


def _counterexample_equal_expected(counterexample: dict[str, Any]) -> bool:
    """判断 verifier 的反例是否其实 candidate 与 expected 相同。

    参数：
        counterexample：verifier 输出的单个 counterexample。

    返回：
        candidate 与 expected 去空白后相同则返回 True。
    """
    candidate = compact_text(counterexample.get("candidate"))
    expected = compact_text(counterexample.get("expected"))
    return bool(candidate and expected and candidate == expected)


def detect_verifier_conflict(result: dict[str, Any]) -> list[ReviewFinding]:
    """识别 verifier 自相矛盾或误杀候选答案的情况。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        相关复盘发现列表。
    """
    findings: list[ReviewFinding] = []
    answer = str(result.get("answer_latex") or result.get("final_answer") or "")
    for index, verification in enumerate(result.get("verifications", []) or [], 1):
        if not isinstance(verification, dict):
            continue
        conflicts = []
        for counterexample in verification.get("counterexamples", []) or []:
            if isinstance(counterexample, dict) and _counterexample_equal_expected(counterexample):
                conflicts.append(str(counterexample))
        forbidden = "\n".join(map(str, verification.get("forbidden_claims", []) or []))
        if conflicts:
            findings.append(ReviewFinding(
                failure_type="verifier_conflict",
                severity=3,
                reason=f"第 {index} 轮 verifier 把 candidate 与 expected 相同的样例当成反例。",
                evidence=conflicts[:3],
                recommended_targets=["user_agent", "math_tools", "prompts"],
            ))
        if answer and compact_text(answer) and compact_text(answer) in compact_text(forbidden):
            findings.append(ReviewFinding(
                failure_type="verifier_forbids_final_answer",
                severity=3,
                reason=f"第 {index} 轮 verifier 把当前最终答案加入 forbidden_claims，需要本地证据仲裁。",
                evidence=[f"answer={answer}", f"forbidden_claims={forbidden}"],
                recommended_targets=["user_agent", "math_tools"],
            ))
    return findings


def detect_missing_rag_or_memory(result: dict[str, Any]) -> list[ReviewFinding]:
    """识别题目命中特征但 RAG 或记忆没有提供帮助的情况。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        相关复盘发现列表。
    """
    triggers = extract_triggers(result)
    findings: list[ReviewFinding] = []
    if triggers and not result.get("retrieved_knowledge"):
        findings.append(ReviewFinding(
            failure_type="missing_rag_template",
            severity=2,
            reason="题目有明显触发特征，但 retrieved_knowledge 为空，说明 knowledge_base 可能缺少方法模板。",
            evidence=[", ".join(triggers)],
            recommended_targets=["knowledge_base"],
        ))
    if triggers and not result.get("memory_rules"):
        findings.append(ReviewFinding(
            failure_type="missing_memory_rule",
            severity=2,
            reason="题目有明显触发特征，但 memory_rules 为空，说明历史避坑规则没有沉淀或未命中。",
            evidence=[", ".join(triggers)],
            recommended_targets=["memory"],
        ))
    return findings


def detect_solver_or_repair_failure(result: dict[str, Any]) -> list[ReviewFinding]:
    """识别 solver 错误、修正无效或最终答案抽取异常。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        相关复盘发现列表。
    """
    findings: list[ReviewFinding] = []
    final_status = result.get("final_status")
    answer_latex = str(result.get("answer_latex") or "")
    final_answer = str(result.get("final_answer") or "")
    if final_status == "unverified":
        findings.append(ReviewFinding(
            failure_type="unverified_result",
            severity=2,
            reason="最终状态为 unverified，需要判断是 solver 错、verifier 误杀还是工具缺失。",
            evidence=[f"answer_latex={answer_latex}", f"final_answer={final_answer}"],
            recommended_targets=["failure_review"],
        ))
    if answer_latex and not final_answer:
        findings.append(ReviewFinding(
            failure_type="final_answer_empty_but_latex_exists",
            severity=1,
            reason="answer_latex 有内容但 final_answer 为空，可能影响评测脚本读取最终答案。",
            evidence=[f"answer_latex={answer_latex}"],
            recommended_targets=["user_agent", "app"],
        ))
    verifications = result.get("verifications", []) or []
    if len(verifications) >= 2 and not result.get("verified"):
        findings.append(ReviewFinding(
            failure_type="repair_not_effective",
            severity=2,
            reason="经过至少两轮校验/修正仍未通过，repair 可能缺少硬证据或被错误反馈带偏。",
            evidence=[f"verification_rounds={len(verifications)}"],
            recommended_targets=["user_agent", "math_tools", "prompts"],
        ))
    return findings


def detect_missing_local_tool(result: dict[str, Any]) -> list[ReviewFinding]:
    """识别适合机械验证但本地工具没有产出证据的情况。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        相关复盘发现列表。
    """
    text = compact_text(flatten_result_text(result))
    has_enumerable_structure = any(key in text for key in ["floor", "\\lfloor", "⌊", "sum", "\\sum", "2^k", "2^{k+1}"])
    has_local_evidence = bool(result.get("local_sanity_checks") or result.get("support_checks"))
    if has_enumerable_structure and not has_local_evidence:
        return [ReviewFinding(
            failure_type="missing_local_support_check",
            severity=3,
            reason="题目或解答包含可小样例枚举的结构，但 local_sanity_checks 与 support_checks 都为空；需要本地支持/反驳证据。",
            evidence=["contains floor/sum/power-like enumerable structure"],
            recommended_targets=["math_tools", "user_agent"],
        )]
    return []


def build_memory_candidate(result: dict[str, Any], findings: list[ReviewFinding]) -> dict[str, Any]:
    """根据复盘结果生成待确认的 memory 规则草稿。

    参数：
        result：一次完整求解结果 JSON。
        findings：复盘发现列表。

    返回：
        可人工确认后写入 memory/learned_rules.json 的规则草稿。
    """
    triggers = extract_triggers(result)
    return {
        "id": "reviewed_failure_rule",
        "trigger": triggers,
        "mistake": "根据失败复盘生成：" + "；".join(f.reason for f in findings[:3]),
        "rule": "遇到同类题时，先检查是否有可机械验证的样例、代回或边界条件；不要只依赖 verifier 的文字判断。",
        "verify": "优先构造小样例表或代回验证；若 verifier 反例中 candidate 与 expected 相同，应标记为 verifier_conflict。",
    }


def build_knowledge_draft(result: dict[str, Any], findings: list[ReviewFinding]) -> str:
    """根据复盘结果生成 RAG 知识库草稿文本。

    参数：
        result：一次完整求解结果 JSON。
        findings：复盘发现列表。

    返回：
        可人工整理后放入 knowledge_base/*.md 的 Markdown 草稿。
    """
    triggers = extract_triggers(result)
    lines = [
        "## 失败复盘生成的方法模板草稿",
        "",
        "触发词：" + "、".join(triggers),
        "",
        "适用场景：题目出现可枚举变量、取整、求和、参数代入或可直接验算的候选公式。",
        "",
        "常见失败模式：",
    ]
    for finding in findings:
        lines.append(f"- {finding.failure_type}：{finding.reason}")
    lines.extend([
        "",
        "维护建议：",
        "1. 先构造小样例或代回点，得到本地可复核证据。",
        "2. 如果候选答案被样例支持，而 verifier 反对，检查 verifier 的 counterexamples 是否自相矛盾。",
        "3. 如果同类题反复出现，补充 math_tools 的支持/反驳检查，而不是只加长 prompt。",
    ])
    return "\n".join(lines)


def review_result(result: dict[str, Any]) -> dict[str, Any]:
    """对一次 result JSON 做完整失败复盘。

    参数：
        result：一次完整求解结果 JSON。

    返回：
        包含失败类型、根因、建议沉淀层级和草稿内容的复盘字典。
    """
    detectors = [
        detect_verifier_conflict,
        detect_missing_rag_or_memory,
        detect_solver_or_repair_failure,
        detect_missing_local_tool,
    ]
    findings: list[ReviewFinding] = []
    for detector in detectors:
        findings.extend(detector(result))
    findings.sort(key=lambda item: item.severity, reverse=True)
    targets: list[str] = []
    for finding in findings:
        for target in finding.recommended_targets:
            if target not in targets:
                targets.append(target)
    return {
        "problem_id": result.get("problem_id"),
        "final_status": result.get("final_status"),
        "verified": result.get("verified"),
        "answer_latex": result.get("answer_latex"),
        "triggers": extract_triggers(result),
        "findings": [finding.__dict__ for finding in findings],
        "recommended_targets": targets,
        "memory_candidate": build_memory_candidate(result, findings) if findings else {},
        "knowledge_draft": build_knowledge_draft(result, findings) if findings else "",
        "next_steps": [
            "人工确认 memory_candidate 是否准确，再写入 memory/learned_rules.json。",
            "人工整理 knowledge_draft，再放入 knowledge_base/*.md。",
            "若 recommended_targets 包含 math_tools，再考虑新增可机械验证的支持/反驳检查。",
            "若 recommended_targets 包含 user_agent，再检查 verifier 与本地证据冲突时的仲裁逻辑。",
        ],
    }
