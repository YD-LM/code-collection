"""题型路由：根据 understanding.domain 选择专用 solver 提示。

设计：基础 SOLVE_SYSTEM 不变，按领域追加一段"特化指引"。
未识别的领域走基础 prompt（兜底），不报错。
"""
from __future__ import annotations

from prompts import SOLVE_SYSTEM


DOMAIN_HINTS: dict[str, str] = {
    "analysis": (
        "数学分析 / 微积分要点：\n"
        "- 计算积分前先判定可积性、奇点位置、是否瑕积分。\n"
        "- 换元后必须改换上下限；分部积分仔细核对边界项符号。\n"
        "- 级数 / 数列极限：先判别收敛性，再求值；交换求和与积分需 Fubini 或一致收敛条件。"
    ),
    "complex_analysis": (
        "复分析要点：\n"
        "- 留数法计算实积分：明确围道、列出全部奇点、分类（极点 / 本性奇点）。\n"
        "- 分支切口与多值函数（log, z^a）：写清主值分支。\n"
        "- Cauchy 定理使用条件：解析性 + 单连通 + 围道闭合。"
    ),
    "linear_algebra": (
        "线性代数要点：\n"
        "- 矩阵问题先看维数、秩、行列式、特征值四件套。\n"
        "- 特征空间维数（几何重数）≤ 代数重数；可对角化判据。\n"
        "- 二次型：正交变换标准形 / 合同变换规范形要区分。"
    ),
    "ode_pde": (
        "微分方程要点：\n"
        "- ODE：先识别类型（可分离 / 一阶线性 / 齐次 / 伯努利 / 恰当 / 高阶线性常系数）再套方法。\n"
        "- PDE：先判类型（椭圆 / 抛物 / 双曲）；常用法：分离变量 / 特征线 / Fourier / Green 函数。\n"
        "- 边界条件类型（Dirichlet / Neumann / Robin）决定本征函数族。"
    ),
    "probability_stats": (
        "概率统计要点：\n"
        "- 先明确样本空间与事件，确认是否独立 / 互斥。\n"
        "- 条件概率：贝叶斯公式 + 全概率公式配套。\n"
        "- 分布计算：MGF / 特征函数 / 卷积 三选一，按场景。"
    ),
    "number_theory": (
        "数论要点：\n"
        "- 模运算优先：费马小定理 / 欧拉定理 / 中国剩余定理 / 二次互反律。\n"
        "- 整除 / 同余 / 素分解：先做规范分解再讨论。"
    ),
    "combinatorics": (
        "组合数学要点：\n"
        "- 计数三板斧：分类 + 分步 + 容斥。\n"
        "- 生成函数（普通 / 指数型）处理递推；双射 / 对称性证恒等式。"
    ),
    "topology": (
        "拓扑学要点：\n"
        "- 紧性 / 连通性 / 可分性：构造覆盖或反证。\n"
        "- 代数拓扑：基本群 / 同调群计算，Mayer-Vietoris、van Kampen。"
    ),
    "geometry": (
        "几何要点：\n"
        "- 解析法（坐标 / 向量 / 复数）vs 综合法（变换 / 对称）选最省力的。\n"
        "- 圆锥曲线：用极坐标或参数化常更简。"
    ),
    "optimization": (
        "运筹 / 优化要点：\n"
        "- 线性规划：单纯形 / 对偶 / 互补松弛；先判可行域顶点。\n"
        "- 凸优化：KKT 必要条件，Slater 条件下充分。\n"
        "- 整数规划：松弛 + 分支定界 / 割平面。"
    ),
}


# 中文别名 → 英文规范键
_DOMAIN_ALIASES: dict[str, str] = {
    "数学分析": "analysis",
    "微积分": "analysis",
    "实分析": "analysis",
    "高等数学": "analysis",
    "复分析": "complex_analysis",
    "复变函数": "complex_analysis",
    "线性代数": "linear_algebra",
    "矩阵论": "linear_algebra",
    "抽象代数": "linear_algebra",
    "代数": "linear_algebra",
    "微分方程": "ode_pde",
    "常微分方程": "ode_pde",
    "偏微分方程": "ode_pde",
    "ode": "ode_pde",
    "pde": "ode_pde",
    "概率论": "probability_stats",
    "概率统计": "probability_stats",
    "统计学": "probability_stats",
    "数理统计": "probability_stats",
    "数论": "number_theory",
    "初等数论": "number_theory",
    "组合数学": "combinatorics",
    "组合": "combinatorics",
    "图论": "combinatorics",
    "拓扑学": "topology",
    "代数拓扑": "topology",
    "点集拓扑": "topology",
    "几何": "geometry",
    "解析几何": "geometry",
    "微分几何": "geometry",
    "运筹学": "optimization",
    "最优化": "optimization",
    "凸优化": "optimization",
    "线性规划": "optimization",
}


def normalize_domain(domain: str | None) -> str | None:
    """把模型给出的领域名归一到 DOMAIN_HINTS 的键；找不到返回 None。"""
    if not domain:
        return None
    d = domain.strip().lower()
    if d in DOMAIN_HINTS:
        return d
    for alias, canonical in _DOMAIN_ALIASES.items():
        if alias.lower() in d or d in alias.lower():
            return canonical
    return None


def get_solver_system(domain: str | None) -> tuple[str, str | None]:
    """返回 (拼接好的 solver system prompt, 归一化的领域键)。"""
    key = normalize_domain(domain)
    if key and key in DOMAIN_HINTS:
        return SOLVE_SYSTEM + "\n\n[领域特化指引]\n" + DOMAIN_HINTS[key], key
    return SOLVE_SYSTEM, None
