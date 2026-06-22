# code-collection.v6

这是 v6 竞赛数学 Agent 版本。

v6 的核心设计是：

```text
短 routing + 强 prompts + 精准 RAG + 本地工具硬检查 + 轻 verifier + deep 兜底
```

## 先看哪份文档

维护和优化请优先看：

- `AGENTS.md`
- `v6_参赛版维护教程.md`
- `v6_参赛版维护教程.docx`

如果需要对照旧版，请回到对应的 v5plus 或待合并版本文件夹查看；v6 文件夹内以本教程为准。

## 失败后固定复盘流程

遇到错题时，不要直接凭印象补规则。固定按下面流程走：

```text
1. 下载 resultXX.json
2. 跑 failure_review.py
3. 看系统建议沉淀到哪一层
4. 你确认
5. 再写入 memory / knowledge_base / math_tools / user_agent
```

命令示例：

```powershell
python failure_review.py "../../result (24).json"
```

相关文件：

- `failure_review.py`：读取 result JSON，输出失败复盘报告；只生成建议，不自动改任何文件。
- `failure_rules.py`：固定失败模式判断规则，例如 verifier 自相矛盾、RAG 未命中、缺少本地工具支持检查。

复盘报告会提示应该沉淀到哪一层：

- `memory/learned_rules.json`：同类错误的短避坑规则。
- `knowledge_base/*.md`：题型方法模板和常见做法。
- `math_tools.py` / `symbolic_tools.py`：能机械验证的检查。
- `prompts.py`：模型通用纪律和 verifier 输出规范。
- `user_agent.py`：流程仲裁、修正策略、运行模式。

## 新增文件维护约定

以后新增 Python 文件时，文件开头先写清楚三件事：

- 本文件包括什么能力。
- 本文件控制哪条流程、入口或判断。
- 后续优化时应该怎么改，哪些内容不该塞进来。

公共函数要写清入参和返回值；没有返回值时不需要写返回项。这样做是为了让后续队友能快速判断文件边界，不需要靠聊天记录恢复上下文。

## 每个文件大概负责什么

- `app.py`：网页入口、模式选择、结果展示、JSON 下载。
- `user_agent.py`：主流水线，控制 RAG、记忆、校验、修正、thinking 和运行模式。
- `prompts.py`：模型通用纪律，例如多问全答、JSON 格式、证明完整性。
- `routing.py`：领域识别后的短提醒，只放方向提示，不放完整教材。
- `rag.py`：从 `knowledge_base/*.md` 检索相关方法片段。
- `knowledge_base/`：详细方法、常见题型模板、证明套路和验证方法。
- `math_tools.py`：本地可机械判断的检查规则。
- `symbolic_tools.py`：SymPy 等符号计算验证工具。
- `memory/learned_rules.json`：历史错因和避坑规则。
- `llm.py` / `client.py`：API 调用、JSON 解析、超时和重试策略。

## 调优优先级

1. 缺方法，先补 `knowledge_base/*.md`。
2. 常犯同类错，再补 `math_tools.py` 或 `symbolic_tools.py`。
3. 输出格式或证明纪律差，再改 `prompts.py`。
4. 速度和质量平衡，先改 `user_agent.py` 的 `PIPELINE_MODES`。
5. 页面展示问题，再改 `app.py`。
6. 不要把 routing 重新写成长百科。
