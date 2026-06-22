"""题型路由：只负责领域识别后的短提醒拼接。

本文件包括：核心求解纪律、领域短提醒、领域别名、solver system prompt 拼接函数。
本文件控制：读题阶段识别出的领域，会给 solver 追加哪一小段方向性提醒。
优化时这样操作：
- 只放方向提醒，不放完整教材、公式大全或整题解法。
- 详细方法、常见题型模板、公式推导放入 knowledge_base/*.md。
- 能机械验证的规则放入 math_tools.py 或 symbolic_tools.py。
- 新增领域时同步补 DOMAIN_HINTS 和 _DOMAIN_ALIASES。
"""
from __future__ import annotations

from prompts import SOLVE_SYSTEM


CORE_SOLVER_RULES = """[不可截断核心规则]
- 多小问题必须逐一覆盖 required_sub_questions 中的全部 sub_idx，不得只做第一问或最后一问。
- 证明题必须从定义、任取对象、必要条件或构造对象开始，不能把待证结论当作已知。
- 最值题必须给出完整约束、候选来源、取等点或取等参数，并代回原题条件核验。
- 含参数题必须说明参数范围、临界情形和分类依据。
- 最终 JSON 不得用“显然、进一步分析、可以得到”等话术替代关键推导。
"""


DOMAIN_HINTS: dict[str, str] = {
    "calc_basic": "计算与化简：先写定义域和表达式有意义条件；优先通分、换元、配方、因式分解；结果用最简精确形式并代回检验。",
    "equation_ineq": "方程不等式：先写定义域/可行域；分式、无理、对数、参数方程要分类讨论并验根；不等式证明要写取等条件。",
    "function_basic": "函数题：先写定义域、分段、单调、奇偶、周期和符号；集合包含从任取元素开始；分段函数跨段独立讨论。",
    "trigonometry": "三角函数：先统一角度范围和周期；优先同角、诱导、和差倍半、辅助角；解方程要给通解并筛选。",
    "sequence_ineq": "数列：先确定通项、初项、公差/公比、递推关系；求和看裂项、差分、错位分组；证明看归纳、放缩或比较。",
    "analytic_geometry": "解析几何：先选坐标系、列已知点线曲线、动点范围、参数范围；射线/线段先写基点、方向、参数；最值给取等点并代回。",
    "plane_geometry": "平面几何：先画图标注共线、共圆、平行、垂直、中点、切线；证明优先全等、相似、圆幂、角追、面积法或变换。",
    "solid_geometry": "立体几何：先明确点线面、投影、截面；角度距离优先向量、法向量、截面法；线面/面面关系要写判定条件。",
    "vector": "向量：先选坐标/基底，写清起点终点；共线看比例，垂直看点积，面积体积看叉积/混合积；注意零向量边界。",
    "complex_number": "复数：先选代数/三角/指数/复平面方法；模与辐角注意多值；几何变换写旋转中心和伸缩比。",
    "calculus": "微积分：先写定义域、连续性、可导性、积分区间、奇点；极限优先等价、洛必达、泰勒；最值看端点、驻点、不可导点。",
    "linear_algebra": "线性代数：先明确矩阵、线性空间、线性相关、秩、特征值/向量；方程组看秩与解空间，齐次/非齐次分清。",
    "probability_stats": "概率统计：先写样本空间、随机变量、事件、独立性、条件概率；期望方差先定义变量再用线性性与分布。",
    "number_theory": "数论：先写整除、同余、奇偶、质因数、取整、二进制位；floor/取整/数位题优先看进位、余数、分块。",
    "combinatorics": "组合数学：先明确对象、约束、有序/无序、重复；优先分类计数、容斥、递推、生成函数、双计数、不变量。",
    "graph_theory": "图论：先明确图是否有向、带权、简单、连通；路径、匹配、染色、连通性优先用度数、割、构造、归纳。",
    "abstract_algebra": "抽象代数：先明确结构、运算、单位元、逆元、子结构、同态；同构证明给映射、良定义、单/满/双射。",
    "real_analysis": "实分析：先写极限、连续、一致连续、可微、可积、收敛方式；证明回到定义、序列、函数族、紧性、量词。",
    "ode_pde": "微分方程：先识别类型、阶数、线性/非线性、初边值条件；线性方程看特征根、通解/特解；偏微分看分离变量或特征线。",
    "optimization": "优化：先写目标、约束、变量范围、可行域；全局最优说明凸性/边界/比较；取等点并代回验证。",
    "verification": "校验：先检查是否回答全部问题、变量范围、证明闭环；判错给具体错误、反例、修正约束；不要只复述原推导。",
    "reasoning_strategy": "通用策略：先把目标转成求值、证明、构造、分类、最值或反例；卡住时用定义展开、特殊到一般、逆向分析、不变量。",
}


_DOMAIN_ALIASES: dict[str, str] = {
    "计算": "calc_basic", "化简": "calc_basic", "算术": "calc_basic", "calc": "calc_basic",
    "方程": "equation_ineq", "不等式": "equation_ineq", "不等式证明": "equation_ineq", "参数不等式": "equation_ineq", "恒成立": "equation_ineq", "ineq": "equation_ineq", "equation": "equation_ineq",
    "函数": "function_basic", "初等函数": "function_basic", "分段函数": "function_basic", "函数证明": "function_basic", "函数综合": "function_basic", "单调性": "function_basic", "奇偶性": "function_basic", "function": "function_basic",
    "三角": "trigonometry", "三角函数": "trigonometry", "trigonometry": "trigonometry",
    "数列": "sequence_ineq", "递推": "sequence_ineq", "求和": "sequence_ineq", "sequence": "sequence_ineq",
    "解析几何": "analytic_geometry", "坐标几何": "analytic_geometry", "圆锥曲线": "analytic_geometry", "椭圆": "analytic_geometry", "双曲线": "analytic_geometry", "抛物线": "analytic_geometry", "analytic geometry": "analytic_geometry",
    "平面几何": "plane_geometry", "几何": "plane_geometry", "圆": "plane_geometry", "plane geometry": "plane_geometry",
    "立体几何": "solid_geometry", "空间几何": "solid_geometry", "solid geometry": "solid_geometry",
    "向量": "vector", "平面向量": "vector", "空间向量": "vector", "vector": "vector",
    "复数": "complex_number", "复平面": "complex_number", "复变函数": "complex_number", "复分析": "complex_number", "单位根": "complex_number", "complex": "complex_number", "complex number": "complex_number",
    "微积分": "calculus", "数学分析": "calculus", "极限": "calculus", "导数": "calculus", "积分": "calculus", "微分": "calculus", "定积分": "calculus", "不定积分": "calculus", "级数": "calculus", "calculus": "calculus",
    "线性代数": "linear_algebra", "矩阵": "linear_algebra", "行列式": "linear_algebra", "矩阵分解": "linear_algebra", "特征值": "linear_algebra", "特征向量": "linear_algebra", "向量空间": "linear_algebra", "二次型": "linear_algebra", "linear algebra": "linear_algebra",
    "概率": "probability_stats", "概率论": "probability_stats", "统计": "probability_stats", "统计学": "probability_stats", "数理统计": "probability_stats", "概率统计": "probability_stats", "条件概率": "probability_stats", "probability": "probability_stats", "statistics": "probability_stats",
    "数论": "number_theory", "整数": "number_theory", "整除": "number_theory", "同余": "number_theory", "floor": "number_theory", "取整": "number_theory", "二进制": "number_theory", "数位和": "number_theory", "素数": "number_theory", "质数": "number_theory", "number theory": "number_theory",
    "组合": "combinatorics", "组合数学": "combinatorics", "计数": "combinatorics", "combinatorics": "combinatorics",
    "图论": "graph_theory", "图": "graph_theory", "最短路": "graph_theory", "匹配": "graph_theory", "染色": "graph_theory", "graph theory": "graph_theory", "树": "graph_theory", "网络流": "graph_theory",
    "抽象代数": "abstract_algebra", "群论": "abstract_algebra", "环论": "abstract_algebra", "abstract algebra": "abstract_algebra",
    "实分析": "real_analysis", "实变函数": "real_analysis", "real analysis": "real_analysis",
    "微分方程": "ode_pde", "常微分方程": "ode_pde", "偏微分方程": "ode_pde", "ode": "ode_pde", "pde": "ode_pde",
    "优化": "optimization", "最优化": "optimization", "运筹": "optimization", "运筹学": "optimization", "线性规划": "optimization", "非线性规划": "optimization", "动态规划": "optimization", "optimization": "optimization",
    "证明": "reasoning_strategy", "求证": "reasoning_strategy", "推理": "reasoning_strategy", "reasoning": "reasoning_strategy",
    "校验": "verification", "验证": "verification", "verification": "verification", "审稿": "verification",
}


def _normalize_domain(domain: str | None) -> str | None:
    """把读题阶段返回的领域名称规范化为 DOMAIN_HINTS 的键。

    参数：
        domain：读题阶段识别出的领域，可以是中文、英文或 None。

    返回：
        规范化后的领域键；无法识别时返回 None。
    """
    if not domain:
        return None
    raw = str(domain).strip()
    lowered = raw.lower()
    if lowered in DOMAIN_HINTS:
        return lowered
    if raw in DOMAIN_HINTS:
        return raw
    return _DOMAIN_ALIASES.get(lowered) or _DOMAIN_ALIASES.get(raw)


def get_domain_hint(keyword: str) -> str | None:
    """根据关键词获取对应领域的短提示。

    参数：
        keyword：领域名、题型名或别名。

    返回：
        领域短提示；未命中时返回 None。
    """
    domain_key = _normalize_domain(keyword)
    return DOMAIN_HINTS.get(domain_key) if domain_key else None


def list_all_domains() -> list[str]:
    """列出所有标准领域键。

    返回：
        DOMAIN_HINTS 中可用的领域键列表。
    """
    return list(DOMAIN_HINTS.keys())


def search_domains(query: str) -> list[tuple[str, str]]:
    """按别名模糊搜索领域短提示。

    参数：
        query：用户输入的领域关键词。

    返回：
        若干二元组，格式为 (领域键, 领域短提示)。
    """
    query_lower = query.lower().strip()
    results: list[tuple[str, str]] = []
    for alias, domain in _DOMAIN_ALIASES.items():
        if query_lower and query_lower in alias.lower():
            hint = DOMAIN_HINTS.get(domain, "")
            pair = (domain, hint)
            if hint and pair not in results:
                results.append(pair)
    return results


def get_solver_system(domain: str | None, *, max_hint_chars: int = 800) -> tuple[str, str | None]:
    """根据题目领域生成 solver system prompt，并限制短提示长度。

    参数：
        domain：读题阶段识别出的数学领域，可以是中文、英文或 None。
        max_hint_chars：领域短提醒最多保留的字符数，避免 routing 与 RAG 重复拖慢。

    返回：
        二元组：第一项为 solver 使用的 system prompt；第二项为命中的领域键，未命中时为 None。
    """
    routed_domain = _normalize_domain(domain)
    if not routed_domain:
        return SOLVE_SYSTEM + "\n\n" + CORE_SOLVER_RULES, None

    hint = DOMAIN_HINTS.get(routed_domain)
    if not hint:
        return SOLVE_SYSTEM + "\n\n" + CORE_SOLVER_RULES, None

    if max_hint_chars > 0 and len(hint) > max_hint_chars:
        hint = hint[:max_hint_chars].rstrip() + "\n- 以上为截断短提醒；详细方法由 RAG 知识库按题目检索补充。"

    solver_system = SOLVE_SYSTEM + "\n\n" + CORE_SOLVER_RULES + "\n\n[领域短提醒]\n" + hint
    return solver_system, routed_domain
