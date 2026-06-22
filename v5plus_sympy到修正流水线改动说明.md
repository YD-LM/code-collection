# v5plus SymPy 与修正流水线改动说明

本文档记录从“加入 SymPy 工具层”开始，到本次针对 result19/result20 的速度、正确率和结构稳定性改造。适用于 `kelong/code-collection.v5plus`。

## 1. 改动目标

参赛目标以正确率为主，但必须避免单题在 `solution` 或 `repair` 阶段耗时 5 到 10 分钟仍无法产出可用 JSON。本轮改造的核心思路是：

- 默认模式不依赖长 thinking，而是依赖结构化输出、RAG、记忆、本地工具和小问级修正。
- 将长 thinking 保留给 `deep` 模式，只在普通模式失败后使用。
- 禁止模型把长篇思考塞进 `response` 字段；系统仍兼容读取旧式 `response`，但最终不保留。
- 将修正轮从“整题重写”改为“只修失败小问”。
- 将 SymPy 放在工具层，用于表达式、方程、轨迹代点等硬校验。

## 2. `symbolic_tools.py`

位置：`kelong/code-collection.v5plus/symbolic_tools.py`

新增文件，职责是封装 SymPy 能力，不直接调用 LLM，也不解析整道自然语言题。

主要内容：

- `SymbolicCheckResult`：统一表示符号工具检查结果。
- `_load_sympy()`：延迟导入 SymPy，避免缺依赖时网页启动失败。
- `_normalize_equation_text()`：整理常见公式文本，例如 `^`、`²`、`\sqrt`。
- `verify_expression_equivalent(lhs, rhs, variable_names=None)`：检查两个表达式是否等价。
- `verify_point_on_equation(equation, point, tolerance=1e-9)`：把点代入方程，检查是否满足。
- `verify_point_on_trajectory(trajectory_equation, point)`：轨迹方程代点验证入口。

维护建议：

- 新增符号计算功能时，先在这里写小函数，再由 `math_tools.py` 调用。
- 不要把自然语言题目解析放在这里。
- 实际运行环境需要安装 `sympy`，否则相关检查会返回 `sympy_unavailable`。

## 3. `math_tools.py`

位置：`kelong/code-collection.v5plus/math_tools.py`

本文件是本地数学检查入口。本轮加入了 SymPy 轨迹代点验证，并保留原有多问覆盖、函数分段、解析几何最值完整性检查。

关键位置和函数：

- 顶部导入：`from symbolic_tools import verify_point_on_trajectory`
- `_extract_trajectory_equation(solution_text)`：从候选答案中抽取“轨迹方程为 ...”这类结构。
- `_extract_named_point(solution_text, point_name="P")`：抽取 `P=(x,y)` 这类点坐标。
- `check_trajectory_point_substitution(problem, solution_text)`：如果同时抽到轨迹方程和点坐标，则调用 SymPy 做代点验证。
- `sanity_check_inversion_ellipse(...)`：汇总解析几何相关检查，并接入轨迹代点验证。
- `run_local_sanity_checks(...)`：统一调度本地检查。

设计原则：

- 缺少清晰轨迹或点坐标时不硬猜，避免误判。
- 如果当前 Python 环境没有 SymPy，不把 `sympy_unavailable` 当成数学错误，不触发无意义修正轮。
- 本地工具只做可机械判断的事，方法知识仍放 RAG，历史错因放 memory。

## 4. `user_agent.py`

位置：`kelong/code-collection.v5plus/user_agent.py`

这是本轮改动最大的文件，负责流水线控制。

### 4.1 模式配置

位置：`PIPELINE_MODES`

改动：

- `contest`：参赛默认模式，少 thinking，使用 RAG、记忆、本地工具和小问级修正。
- `strict`：疑难重算模式，但默认关闭 `thinking_solve` 和 `thinking_verify`，避免单题耗时失控。
- `deep`：新增深度慢模式，允许 `thinking_solve=True`、`thinking_verify=True`，只建议在 contest/strict 失败后使用。
- 每个模式新增 `repair_failed_only`，控制是否只修失败小问。

### 4.2 Prompt 文本修复

位置：

- `SOLVE_EXTRA_GUIDANCE`
- `REPAIR_EXTRA_GUIDANCE`
- `EMPTY_RETRY_GUIDANCE`
- `SCHEMA_REPAIR_SYSTEM`

改动：

- 修复此前中文乱码和 `????`。
- 明确禁止输出 `response` 字段。
- `SCHEMA_REPAIR_SYSTEM` 只负责结构整理，不重新解题。

### 4.3 本地 plan fallback 修复

位置：`_build_local_plan(...)`

改动：

- 修复真实存在的 `????` 文本。
- 规划阶段失败时生成可用的最小计划，而不是直接中断。

### 4.4 response 兼容但不保留

位置：

- `_strip_response_field(solution)`
- `_coerce_solution_fields(...)`
- `_normalize_solution(...)`

改动：

- 模型仍可能返回 `{"response": "..."}`，系统兼容处理。
- 最终答案中删除 `response`，不鼓励模型继续输出长篇思考。
- `SCHEMA_REPAIR_SYSTEM` 也要求不保留 `response`。

### 4.5 非标准字段精准映射

位置：

- `_stringify_answer_fragment(value)`
- `_extract_nonstandard_sub_answer(solution, sub_idx)`
- `_coerce_solution_fields(...)`

解决的问题：

`result20` 中模型输出了：

```json
{
  "problem_1": {...},
  "problem_2": {
    "part_i": {...},
    "part_ii": {...}
  }
}
```

以前会把整块内容重复塞进每个小问。现在映射为：

- `problem_1 -> (1)`
- `problem_2.part_i -> (2)(i)`
- `problem_2.part_ii -> (2)(ii)`

### 4.6 小问级修正

位置：

- `_filter_failed_sub_questions(sub_questions, verification)`
- `_build_failed_only_repair_prompt(...)`
- `_merge_repaired_sub_answers(original, repaired, sub_questions)`
- `solve_problem(...)` 中 repair 分支

改动：

- verifier 或本地工具指出失败后，只把失败小问交给 repair。
- repair 返回后只合并被修正的小问，不整题重写。
- 目标是减少 `repair_1` 从数百秒膨胀，并避免已正确小问被改坏。

## 5. `llm.py`

位置：`kelong/code-collection.v5plus/llm.py`

改动：

- 新增 `REQUEST_TIMEOUT = _parse_int(os.environ.get("INTERN_S1_TIMEOUT")) or 120`
- 在 `chat()` 请求参数中加入 `timeout: REQUEST_TIMEOUT`
- `ask_json()` 默认 `max_retries` 从 2 改为 1

作用：

- 防止 `solution` 或 `repair` 阶段像 result19/result20 一样长时间卡住。
- 减少 JSON 解析失败后的重复长时间重试。

可通过 `.env` 调整：

```env
INTERN_S1_TIMEOUT=120
```

## 6. `prompts.py`

位置：`SOLVE_SYSTEM`

改动：

- 强化 JSON 规范：禁止输出 `response` 字段。
- 固定输出字段之外禁止新增字段。

作用：

减少模型把长篇思考放入 `response` 或自定义字段的概率。

## 7. `app.py`

位置：运行模式选择 `st.selectbox`

改动：

- 增加 `deep` 模式选项。
- 更新 help 文本：`deep` 是更慢的深度模式。

## 8. result19/result20 对应修复点

### result19

问题：

- `solution` 阶段耗时约 557 秒。
- 模型输出 `response` 长文，导致 JSON 解析失败。

对应修复：

- strict 默认关闭长 thinking。
- 新增请求 timeout。
- 禁止 response 输出，但兼容 response 输入。
- schema repair 只整理结构，不重新解题。

### result20

问题：

- `repair_1` 耗时约 375 秒。
- 非标准 `problem_1/problem_2` 字段被粗暴吸收，污染全部小问。
- 最终答案仍缺取等与代回验证。

对应修复：

- 小问级 repair，只修失败小问。
- 精准映射 `problem_1/problem_2.part_i/part_ii`。
- 保留本地工具和 SymPy 校验入口。

## 9. 后续优化建议

- 如果 `deep` 模式仍经常超时，可以进一步把 deep 的 `max_revisions` 降为 1。
- 可以在 `math_tools.py` 中继续增加解析几何硬检查，例如椭圆点代入、斜率关系代入、距离乘积代入。
- RAG 知识库中应补充“解析几何最值必须给取等点和代回验证”的短模板。
- 如果模型继续输出自定义字段，可继续扩展 `_extract_nonstandard_sub_answer()` 的映射规则。
