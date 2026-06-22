"""错误记忆：把历史失败模式按触发词自动注入 prompt。

这里不保存题目的完整答案，只保存“容易犯什么错、应该怎样检查”。
这样能提升迁移性，也避免模型在新题里机械套旧答案。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_RULE_PATH = Path(__file__).resolve().parent / "memory" / "learned_rules.json"


def load_memory_rules() -> list[dict[str, Any]]:
    """读取历史错误规则。

    返回：
        规则字典列表；文件不存在或格式不对时返回空列表。
    """
    if not _RULE_PATH.exists():
        return []
    try:
        data = json.loads(_RULE_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def retrieve_memory_rules(problem: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    """根据 trigger 从历史错误规则中找出当前题可能用到的规则。

    参数：
        problem：原始题干文本。
        top_k：最多返回的规则数量。

    返回：
        命中的规则列表，按触发词命中数量排序。
    """
    compact = problem.replace(" ", "").lower()
    hits: list[tuple[int, dict[str, Any]]] = []
    for rule in load_memory_rules():
        triggers = rule.get("trigger", [])
        if not isinstance(triggers, list):
            continue
        score = sum(1 for item in triggers if str(item).lower().replace(" ", "") in compact)
        if score > 0:
            enriched = dict(rule)
            enriched["score"] = score
            hits.append((score, enriched))

    hits.sort(key=lambda item: item[0], reverse=True)
    return [rule for _, rule in hits[:top_k]]


def format_memory_rules(rules: list[dict[str, Any]]) -> str:
    """把命中的记忆规则格式化为 prompt 段落。

    参数：
        rules：retrieve_memory_rules 返回的规则列表。

    返回：
        可拼接到 user prompt 的文本；没有命中时返回空字符串。
    """
    if not rules:
        return ""

    lines = ["[历史错误记忆]", "以下是同类题常见失败模式；必须用它们检查推导，不得直接套旧结论。"]
    for index, rule in enumerate(rules, 1):
        lines.append(f"{index}. {rule.get('id', 'unnamed_rule')}：{rule.get('mistake', '')}")
        lines.append(f"   规则：{rule.get('rule', '')}")
        lines.append(f"   校验：{rule.get('verify', '')}")
    return "\n".join(lines)

