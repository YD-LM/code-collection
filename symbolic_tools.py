"""SymPy 辅助工具：提供可机械验证的代数与轨迹检查。

本文件包括：表达式等价检查、方程代点验证、轨迹方程代点验证。
本文件控制：需要符号计算才能稳定判断的局部检查，不负责完整解题，也不直接调用 LLM。
优化时这样操作：
- 想增加新的硬计算能力，优先在这里封装成小函数，再由 math_tools.py 调用。
- 不要让本文件解析整道自然语言题；它只接收已结构化的表达式、点、变量名。
- 参赛运行环境需要安装 sympy；未安装时函数会返回明确的工具不可用结果。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SymbolicCheckResult:
    """符号工具检查结果。

    参数：
        name：检查名称。
        passed：检查是否通过。
        issue：未通过或工具不可用时的说明。
        details：可选的结构化细节，便于写入 JSON 结果。
    """

    name: str
    passed: bool
    issue: str = ""
    details: dict[str, Any] | None = None

    def to_issue(self) -> dict[str, Any] | None:
        """把失败结果转换成 math_tools 统一使用的问题字典。

        返回：
            失败时返回问题字典；通过时返回 None。
        """
        if self.passed:
            return None
        payload: dict[str, Any] = {"name": self.name, "passed": False, "issue": self.issue}
        if self.details:
            payload["details"] = self.details
        return payload


def _load_sympy():
    """延迟导入 SymPy，避免缺依赖时影响网页启动。

    返回：
        二元组，第一项为 sympy 模块或 None，第二项为错误信息或 None。
    """
    try:
        import sympy as sp
    except ModuleNotFoundError:
        return None, "当前 Python 环境未安装 sympy，无法执行符号硬校验。请在运行环境安装 sympy 后重试。"
    return sp, None


def _normalize_equation_text(equation: str) -> str:
    """把常见数学文本整理成 SymPy 更容易解析的表达式文本。

    参数：
        equation：方程或表达式文本。

    返回：
        替换 Unicode 幂、等号和 LaTeX 基础写法后的文本。
    """
    text = str(equation).strip()
    replacements = {
        "，": ",",
        "（": "(",
        "）": ")",
        "²": "**2",
        "³": "**3",
        "^": "**",
        "\\cdot": "*",
        "\\left": "",
        "\\right": "",
        "\\sqrt": "sqrt",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _parse_relation(sp: Any, relation_text: str, variables: dict[str, Any]):
    """把方程文本解析成左减右的 SymPy 表达式。

    参数：
        sp：SymPy 模块。
        relation_text：方程或表达式；含等号时按左边减右边处理。
        variables：变量名到 SymPy 符号的映射。

    返回：
        可代入求值的 SymPy 表达式。
    """
    text = _normalize_equation_text(relation_text)
    local_dict = {**variables, "sqrt": sp.sqrt}
    if "=" in text:
        left, right = text.split("=", 1)
        return sp.sympify(left, locals=local_dict) - sp.sympify(right, locals=local_dict)
    return sp.sympify(text, locals=local_dict)


def verify_expression_equivalent(lhs: str, rhs: str, variable_names: list[str] | None = None) -> SymbolicCheckResult:
    """验证两个表达式是否符号等价。

    参数：
        lhs：左侧表达式。
        rhs：右侧表达式。
        variable_names：表达式中允许出现的变量名；未传入时使用 x,y,m,n,t。

    返回：
        符号检查结果。
    """
    sp, err = _load_sympy()
    if err:
        return SymbolicCheckResult("sympy_unavailable", False, err)
    names = variable_names or ["x", "y", "m", "n", "t"]
    variables = {name: sp.symbols(name) for name in names}
    try:
        diff = _parse_relation(sp, lhs, variables) - _parse_relation(sp, rhs, variables)
        passed = sp.simplify(diff) == 0
    except Exception as exc:
        return SymbolicCheckResult("symbolic_equivalence_parse_error", False, f"表达式等价检查解析失败：{exc}")
    return SymbolicCheckResult("symbolic_equivalence", bool(passed), "两个表达式不等价。" if not passed else "")


def verify_point_on_equation(equation: str, point: dict[str, Any], *, tolerance: float = 1e-9) -> SymbolicCheckResult:
    """验证点是否满足给定方程或轨迹方程。

    参数：
        equation：方程文本，例如 x^2+(y+4)^2=18。
        point：点坐标字典，例如 {"x": 3, "y": -1}。
        tolerance：数值近似为零的容差。

    返回：
        符号检查结果。
    """
    sp, err = _load_sympy()
    if err:
        return SymbolicCheckResult("sympy_unavailable", False, err)
    variables = {name: sp.symbols(name) for name in point.keys()}
    try:
        expr = _parse_relation(sp, equation, variables)
        substitutions = {variables[name]: sp.sympify(value) for name, value in point.items()}
        exact_value = sp.simplify(expr.subs(substitutions))
        numeric_value = abs(float(sp.N(exact_value)))
    except Exception as exc:
        return SymbolicCheckResult("point_equation_parse_error", False, f"轨迹方程代点验证解析失败：{exc}")

    passed = exact_value == 0 or numeric_value <= tolerance
    details = {"equation": equation, "point": point, "residual": str(exact_value)}
    issue = "候选取等点或代入点不满足轨迹方程。" if not passed else ""
    return SymbolicCheckResult("point_on_equation", bool(passed), issue, details)


def verify_point_on_trajectory(trajectory_equation: str, point: dict[str, Any]) -> SymbolicCheckResult:
    """验证点是否在轨迹方程上，作为解析几何最值题的硬校验入口。

    参数：
        trajectory_equation：轨迹方程文本。
        point：待验证点坐标字典。

    返回：
        符号检查结果。
    """
    result = verify_point_on_equation(trajectory_equation, point)
    if result.name == "point_on_equation":
        result.name = "point_on_trajectory"
        if not result.passed:
            result.issue = "候选点不满足给出的轨迹方程，取等或轨迹推导需要重算。"
    return result
