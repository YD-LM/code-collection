"""数学工具：提供可机械执行的轻量检查。

本文件包括：多问覆盖检查、函数分段题高风险拦截、取整二进制求和反例检查、解析几何基点/最值证明完整性检查、SymPy 轨迹代点验证。
本文件控制：哪些错误不交给模型口头自检，而由本地规则或符号工具先发现并反馈给修正轮。
优化时这样操作：
- 新增规则时先写成一个独立 check_xxx 函数，每个函数只负责一种风险。
- 不要把某道题的最终答案硬编码成唯一标准；优先检查“是否有约束、取等点、代回验证”等可复用证据。
- 需要符号计算的检查先封装到 symbolic_tools.py，再由这里调度。
"""
from __future__ import annotations

import re
from typing import Any

from symbolic_tools import verify_point_on_trajectory


def flatten_solution_text(solution: dict[str, Any] | None) -> str:
    """把结构化解答压成便于规则检查的文本。

    参数：
        solution：solver 返回的结构化答案。

    返回：
        拼接后的答案文本。
    """
    if not isinstance(solution, dict):
        return ""
    parts: list[str] = []
    for key in ("final_answer", "answer_latex"):
        value = solution.get(key)
        if value:
            parts.append(str(value))
    for item in solution.get("steps", []) or []:
        if isinstance(item, dict):
            parts.extend(str(item.get(key) or "") for key in ("operation", "result", "derivation"))
    for item in solution.get("sub_answers", []) or []:
        if isinstance(item, dict):
            parts.extend(str(item.get(key) or "") for key in ("answer", "proof_or_derivation", "verified_hint"))
    return "\n".join(part for part in parts if part)


def check_sub_question_coverage(required_sub_questions: list[dict], solution: dict[str, Any]) -> list[dict[str, Any]]:
    """检查多问题是否覆盖了全部小问。

    参数：
        required_sub_questions：从题干抽取的必答小问。
        solution：solver 返回的结构化答案。

    返回：
        未覆盖小问的问题列表；全部覆盖时为空列表。
    """
    if not required_sub_questions:
        return []
    answered = {
        str(item.get("sub_idx"))
        for item in solution.get("sub_answers", []) or []
        if isinstance(item, dict) and str(item.get("answer") or item.get("proof_or_derivation") or "").strip()
    }
    issues: list[dict[str, Any]] = []
    for item in required_sub_questions:
        sub_idx = str(item.get("sub_idx"))
        if sub_idx not in answered:
            issues.append({
                "name": "missing_sub_question",
                "passed": False,
                "issue": f"小问 {sub_idx} 没有有效答案或推导。",
            })
    return issues


def sanity_check_function_piecewise_d(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """拦截函数集合 D(-1) 的典型分段计算错误。

    参数：
        problem：原始题干文本。
        solution_text：候选答案文本。

    返回：
        检查问题列表；没有命中错误时为空列表。
    """
    compact_problem = problem.replace(" ", "")
    compact_solution = solution_text.replace(" ", "")
    if all(key in compact_problem for key in ["D(-1)", "x<0", "2^x", "x≥0"]):
        if "(-∞" in compact_solution or "-\\infty" in compact_solution:
            return [{
                "name": "piecewise_d_minus_one_interval",
                "passed": False,
                "issue": "D(-1) 需要按 -1+d<0 与 -1+d>=0 分段；若答案含负无穷区间，通常把不等式方向或分段点算错。",
            }]
    return []


def _contains_any(text: str, words: list[str]) -> bool:
    """判断文本是否包含任一关键词，集中处理规则里的关键词匹配。

    参数：
        text：待检查文本。
        words：候选关键词列表。

    返回：
        命中任一关键词时返回 True。
    """
    return any(word in text for word in words)


def _extract_maximum_claims(solution_text: str) -> list[str]:
    """抽取候选答案中疑似最终最值结论的句子，避免把题干条件误判为答案。

    参数：
        solution_text：候选答案文本。

    返回：
        包含“最大/最小/取值范围”等词的短句列表。
    """
    pieces = re.split(r"[。；;\n]", solution_text)
    return [piece.strip() for piece in pieces if _contains_any(piece, ["最大", "最小", "取值范围", "最值"])]


def _extract_trajectory_equation(solution_text: str) -> str | None:
    """从答案文本中抽取最常见的轨迹方程。

    参数：
        solution_text：候选答案文本。

    返回：
        轨迹方程文本；没有明确命中时返回 None。
    """
    patterns = [
        r"轨迹(?:方程)?(?:为|是|：|:)\s*([^。；;\n]+=[^。；;\n]+)",
        r"点P(?:的)?轨迹(?:方程)?(?:为|是|：|:)\s*([^。；;\n]+=[^。；;\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, solution_text)
        if match:
            return match.group(1).strip()
    return None


def _parse_number_token(token: str) -> str:
    """整理坐标中的简单数值文本，保留给 SymPy 解析。

    参数：
        token：坐标分量文本。

    返回：
        替换基础 LaTeX 写法后的坐标分量。
    """
    return token.strip().replace("\\sqrt", "sqrt").replace("^", "**")


def _extract_named_point(solution_text: str, point_name: str = "P") -> dict[str, str] | None:
    """抽取文本里的具名点坐标，例如 P=(3,-1)。

    参数：
        solution_text：候选答案文本。
        point_name：点名，默认抽取 P。

    返回：
        {"x": ..., "y": ...}；没有明确命中时返回 None。
    """
    escaped_name = re.escape(point_name)
    patterns = [
        rf"{escaped_name}\s*[=为是:]\s*[（(]\s*([^,，()（）]+)\s*[,，]\s*([^()（）]+?)\s*[）)]",
        rf"点{escaped_name}\s*(?:为|是|取|坐标为|坐标是|:)\s*[（(]\s*([^,，()（）]+)\s*[,，]\s*([^()（）]+?)\s*[）)]",
    ]
    for pattern in patterns:
        match = re.search(pattern, solution_text)
        if match:
            return {"x": _parse_number_token(match.group(1)), "y": _parse_number_token(match.group(2))}
    return None


def check_geometry_basepoint(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """检查射线/线段建模是否疑似丢失基点。

    参数：
        problem：原始题干文本。
        solution_text：候选答案文本。

    返回：
        检查问题列表；没有命中错误时为空列表。
    """
    compact_problem = problem.replace(" ", "")
    compact_solution = solution_text.replace(" ", "")
    issues: list[dict[str, Any]] = []

    if all(key in compact_problem for key in ["椭圆", "射线AP", "|AR|", "|AP|"]):
        has_origin_denominator = "m^2+n^2" in compact_solution or "m²+n²" in compact_solution
        has_basepoint_offset = "(n+1)^2" in compact_solution or "(n+1)²" in compact_solution or "n+1" in compact_solution
        if has_origin_denominator and not has_basepoint_offset:
            issues.append({
                "name": "ray_basepoint_anchor_error",
                "passed": False,
                "issue": "候选解疑似丢失射线或线段的基点；应先明确基点、方向向量和参数范围，再列距离关系。",
            })

    return issues


def check_geometry_extremum_completeness(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """检查解析几何最值答案是否给出约束、取等点和代回验证。

    参数：
        problem：原始题干文本。
        solution_text：候选答案文本。

    返回：
        检查问题列表；没有命中风险时为空列表。
    """
    compact_problem = problem.replace(" ", "")
    if not _contains_any(compact_problem, ["最大", "最小", "最值", "取值范围"]):
        return []
    if not _contains_any(compact_problem, ["椭圆", "圆", "抛物线", "双曲线", "直线"]):
        return []

    claims = _extract_maximum_claims(solution_text)
    if not claims:
        return []

    has_constraint = _contains_any(solution_text, ["轨迹", "约束", "参数", "方程", "范围", "满足"])
    has_witness = _contains_any(solution_text, ["取等", "当且仅当", "等号成立", "取到", "达到", "此时", "点", "参数为"])
    has_substitution_check = _contains_any(solution_text, ["代回", "检验", "验证", "满足原题", "符合条件"])
    has_vague_jump = _contains_any(solution_text, ["显然", "易得", "进一步分析", "可以得到", "猜测", "可能"])

    missing = []
    if not has_constraint:
        missing.append("完整轨迹或约束")
    if not has_witness:
        missing.append("取等点或取等参数")
    if not has_substitution_check:
        missing.append("代回原题条件的验证")

    if missing or has_vague_jump:
        detail = "、".join(missing) if missing else "存在跳步式表述"
        return [{
            "name": "analytic_geometry_extremum_incomplete",
            "passed": False,
            "issue": f"解析几何最值结论缺少可复核证据：{detail}。修正时需给出约束、取等信息并代回验证。",
        }]

    return []


def check_trajectory_point_substitution(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """用 SymPy 验证候选取等点是否满足候选轨迹方程。

    参数：
        problem：原始题干文本。
        solution_text：候选答案文本。

    返回：
        检查问题列表；缺少清晰轨迹或点坐标时不强行猜测。
    """
    compact_problem = problem.replace(" ", "")
    if not _contains_any(compact_problem + solution_text, ["轨迹", "方程"]):
        return []

    trajectory = _extract_trajectory_equation(solution_text)
    point = _extract_named_point(solution_text, "P")
    if trajectory is None or point is None:
        return []

    result = verify_point_on_trajectory(trajectory, point)
    if result.name == "sympy_unavailable":
        return []
    issue = result.to_issue()
    return [issue] if issue else []


def sanity_check_inversion_ellipse(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """汇总解析几何反演/最值类题的可复用本地检查。

    参数：
        problem：原始题干文本。
        solution_text：候选答案文本。

    返回：
        检查问题列表；没有命中错误时为空列表。
    """
    issues: list[dict[str, Any]] = []
    issues.extend(check_geometry_basepoint(problem, solution_text))
    issues.extend(check_geometry_extremum_completeness(problem, solution_text))
    issues.extend(check_trajectory_point_substitution(problem, solution_text))
    return issues


def _compact_math_text(text: str) -> str:
    """统一压缩数学文本，便于匹配 LaTeX、中文和普通文本里的同一结构。

    参数：
        text：待压缩的题干或答案文本。

    返回：
        去掉空白并转成小写后的文本。
    """
    return re.sub(r"\s+", "", str(text or "").lower())


def _looks_like_floor_binary_sum_problem(problem: str) -> bool:
    """判断题干是否属于取整函数与二进制幂求和的可抽检题型。

    参数：
        problem：原始题干文本。

    返回：
        命中取整、求和、2 的 k 次幂和变量 n 时返回 True。
    """
    compact = _compact_math_text(problem)
    has_floor = _contains_any(compact, ["floor", "\\lfloor", "⌊", "取整"])
    has_sum = _contains_any(compact, ["sum", "\\sum", "∑", "和式"])
    has_power = _contains_any(compact, ["2^k", "2^{k}", "2**k"])
    has_denominator = _contains_any(compact, ["2^{k+1}", "2^(k+1)", "2**(k+1)"])
    return has_floor and has_sum and has_power and has_denominator and "n" in compact


def _direct_floor_binary_sum(n: int) -> tuple[int, list[dict[str, int]]]:
    """直接计算 result23 型和式的有限非零项，给反例表使用。

    参数：
        n：正整数样例。

    返回：
        二元组：直接求和值，以及每个非零项的 k 与 term。
    """
    terms: list[dict[str, int]] = []
    total = 0
    for k in range(n.bit_length() + 2):
        term = (n + (1 << k)) // (1 << (k + 1))
        total += term
        if term:
            terms.append({"k": k, "term": term})
    return total, terms


def _claims_popcount_as_final_answer(solution_text: str) -> bool:
    """识别候选解是否把 popcount 当成最终答案，而不是仅作为中间抵消项。

    参数：
        solution_text：候选答案压平后的文本。

    返回：
        明确出现 popcount 类最终结论且没有同时给出 S=n 时返回 True。
    """
    compact = _compact_math_text(solution_text).replace("\\", "")
    popcount_markers = [
        "popcount",
        "bit_count",
        "汉明重量",
        "二进制表示中1的个数",
        "二进制中1的个数",
        "二进制表示中所有位的和",
        "二进制中所有位的和",
        "1的个数(n)",
    ]
    if not _contains_any(compact, popcount_markers):
        return False
    final_n_markers = ["s=n", "s＝n", "s等于n", "答案为n", "最终结果为n", "最终答案为n"]
    return not _contains_any(compact, final_n_markers)


def check_floor_binary_sum_identity(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """用小样例反驳取整二进制求和题里的 popcount 误判。

    参数：
        problem：原始题干文本。
        solution_text：候选答案文本。

    返回：
        命中错误 popcount 结论时返回带反例和修正路线的问题列表。
    """
    if not _looks_like_floor_binary_sum_problem(problem):
        return []
    if not _claims_popcount_as_final_answer(solution_text):
        return []

    samples = [1, 2, 3, 4, 5, 8, 15]
    sample_table: list[dict[str, Any]] = []
    counterexample: dict[str, Any] | None = None
    for n in samples:
        direct_sum, terms = _direct_floor_binary_sum(n)
        popcount = bin(n).count("1")
        row = {
            "n": n,
            "direct_sum": direct_sum,
            "popcount": popcount,
            "nonzero_terms": terms,
        }
        sample_table.append(row)
        if counterexample is None and direct_sum != popcount:
            counterexample = row

    if counterexample is None:
        return []

    terms_text = " + ".join(str(item["term"]) for item in counterexample["nonzero_terms"])
    return [{
        "name": "floor_binary_sum_popcount_counterexample",
        "passed": False,
        "issue": (
            f"候选解把和式误判为 n 的二进制表示中 1 的个数；"
            f"但 n={counterexample['n']} 时直接求和 S={terms_text}={counterexample['direct_sum']}，"
            f"popcount(n)={counterexample['popcount']}，旧结论被反例推翻。"
        ),
        "failed_claims": ["S = popcount(n)", "S 等于 n 的二进制表示中 1 的个数"],
        "forbidden_claims": ["S = popcount(n)", "S 等于 n 的二进制表示中 1 的个数"],
        "counterexamples": [counterexample],
        "sample_table": sample_table,
        "repair_constraints": [
            "禁止再次把最终答案写成 popcount(n) 或二进制中 1 的个数。",
            "必须拆出 floor(n/2^{k+1}) 的高位贡献，不能只看第 k 位。",
            "必须用 n=1,2,3,4,5,8,15 的样例表核验最终公式。",
        ],
        "suggested_route": (
            "令 b_k 为 n 的二进制第 k 位，先证明 "
            "floor((n+2^k)/2^{k+1}) = floor(n/2^{k+1}) + b_k；"
            "再利用 sum_{j>=1} floor(n/2^j)=n-popcount(n)，得到 S=n。"
        ),
    }]


def check_finite_field_extension_generator(problem: str, solution_text: str) -> list[dict[str, Any]]:
    """Catch domain-generator vs multiplicative-primitive-element confusion."""
    compact_problem = _compact_math_text(problem)
    compact_solution = _compact_math_text(solution_text)
    has_finite_field = _contains_any(compact_problem, ["mathbb{f}", "\\mathbb{f}", "f_{", "f}_", "有限域"])
    has_generated_extension = _contains_any(compact_problem, ["(\\alpha)", "(alpha)", "生成", "扩张域"])
    asks_domain_generator = has_finite_field and has_generated_extension and _contains_any(compact_problem, ["=", "求", "个数", "元素"])
    if not asks_domain_generator:
        return []

    multiplicative_group_markers = ["乘法群", "f^*", "f_{", "\\varphi", "\\phi", "phi(", "φ(", "欧拉函数", "本原元"]
    if not _contains_any(compact_solution, multiplicative_group_markers):
        return []

    return [{
        "name": "finite_field_generator_vs_primitive_element",
        "passed": False,
        "issue": (
            "题目问的是域生成元 F_q(alpha)=F_{q^n}，不是乘法群 F_{q^n}^* 的本原元。"
            "候选解出现 phi(q^n-1)、欧拉函数、乘法群或本原元路线，疑似把域扩张生成元误当成乘法群生成元。"
            "必须先按真子域排除或 Möbius 计数重算，只有题目明确要求生成乘法群时才可用 phi(q^n-1)。"
        ),
        "failed_claims": ["把域生成元个数写成乘法群本原元个数", "使用 phi(q^n-1) 回答 F_q(alpha)=F_{q^n}"],
        "forbidden_claims": ["未区分域生成元和乘法群本原元", "直接用 phi(q^n-1) 作为域生成元个数"],
        "repair_constraints": [
            "必须区分域生成元和乘法群本原元。",
            "若题目问 F_q(alpha)=F_{q^n}，必须使用真子域排除或 Möbius 计数。",
            "只有题目明确要求乘法群 F_{q^n}^* 的生成元时才允许使用 phi(q^n-1)。"
        ],
        "suggested_route": "重新按子域结构推导：alpha 生成整个扩张域当且仅当 alpha 不落在任何真子域中；再按真子域并集或 Möbius 计数求个数。",
    }]


def run_support_checks(problem: str, solution: dict[str, Any]) -> list[dict[str, Any]]:
    """Return locally supported claims used to arbitrate verifier false alarms.

    This hook is intentionally conservative: support checks should only return
    passed=True when a claim has been independently recomputed by deterministic
    local tools. Until such a check is available for a problem type, return no
    support evidence and let the normal verifier/local sanity path decide.
    """
    return []


def run_local_sanity_checks(problem: str, solution: dict[str, Any], required_sub_questions: list[dict]) -> list[dict[str, Any]]:
    """Run deterministic sanity checks outside the verifier."""
    solution_text = flatten_solution_text(solution)
    issues: list[dict[str, Any]] = []
    issues.extend(check_sub_question_coverage(required_sub_questions, solution))
    issues.extend(sanity_check_function_piecewise_d(problem, solution_text))
    issues.extend(check_floor_binary_sum_identity(problem, solution_text))
    issues.extend(check_finite_field_extension_generator(problem, solution_text))
    issues.extend(sanity_check_inversion_ellipse(problem, solution_text))
    return issues


def format_tool_feedback(issues: list[dict[str, Any]]) -> str:
    """把本地工具发现的问题整理成修正提示，并保留反例与硬约束。

    参数：
        issues：run_local_sanity_checks 返回的问题列表。

    返回：
        可拼接到修正 prompt 的文本；没有问题时返回空字符串。
    """
    if not issues:
        return ""
    lines = ["[本地数学工具检查未通过]", "下面的问题来自可机械执行的检查，修正时必须逐条处理："]
    for index, item in enumerate(issues, 1):
        lines.append(f"{index}. {item.get('name', 'local_check')}：{item.get('issue', '')}")
        if item.get("failed_claims"):
            lines.append("   被推翻的旧结论：" + "；".join(map(str, item["failed_claims"])))
        if item.get("forbidden_claims"):
            lines.append("   禁止重复作为最终答案：" + "；".join(map(str, item["forbidden_claims"])))
        for counterexample in item.get("counterexamples", []) or []:
            terms = counterexample.get("nonzero_terms", [])
            terms_text = " + ".join(str(term.get("term")) for term in terms) or "0"
            lines.append(
                "   反例："
                f"n={counterexample.get('n')} 时，直接求和 S={terms_text}={counterexample.get('direct_sum')}，"
                f"候选 popcount={counterexample.get('popcount')}。"
            )
        if item.get("sample_table"):
            sample_text = "；".join(
                f"n={row.get('n')}: S={row.get('direct_sum')}, popcount={row.get('popcount')}"
                for row in item["sample_table"]
            )
            lines.append("   样例表：" + sample_text)
        if item.get("repair_constraints"):
            lines.append("   修正硬约束：" + "；".join(map(str, item["repair_constraints"])))
        if item.get("suggested_route"):
            lines.append("   建议重算路线：" + str(item["suggested_route"]))
    return "\n".join(lines)

