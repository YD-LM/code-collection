# AGENTS.md

本文件是本项目的智能体协作规则。所有后续代码修改、结果分析、文档维护、失败复盘，都应优先遵守这里的约定。

本项目运行环境主要是 Windows，且路径中可能包含中文。因此必须特别注意 UTF-8、PowerShell 编码、中文路径和 JSON 读写问题。

当 PowerShell 显示中文乱码时，默认先怀疑终端编码，不要直接重写源文件。

## 1. 基本协作规则

- 使用中文回答。
- 修改代码前，先复述用户需求，并给出修改方案，等待用户确认后再动手。
- 修改任何文件前，必须先读取该文件。
- 不要自动执行 `git commit`、`git push`。
- 不要自动启动后端服务，除非用户明确要求。
- 不要无边界阅读大量文件；先限定调查范围。
- 不要删除、覆盖、回滚用户已有改动。
- 不要为了解决一个单题错误，把大段题目答案硬编码进主流程。

## 2. Windows / PowerShell / UTF-8 规则

本项目经常遇到以下问题：

- PowerShell 显示中文乱码。
- PowerShell 管道传中文源码给 Python 时乱码。
- 中文路径通过管道传递后被 Windows 转码。
- 文件本身是 UTF-8，但终端显示成乱码。
- JSON 中中文或数学符号被错误转义或损坏。

因此所有文件读写必须遵守：

```python
Path(path).read_text(encoding="utf-8")
Path(path).write_text(text, encoding="utf-8")
json.dumps(data, ensure_ascii=False, indent=2)
json.loads(Path(path).read_text(encoding="utf-8-sig"))
```

读取 result JSON 时，优先使用 `encoding="utf-8-sig"`，以兼容可能带 BOM 的文件。

## 3. PowerShell 操作约定

在 PowerShell 中执行涉及中文输出的命令前，优先设置：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

读取文件时优先使用：

```powershell
Get-Content -LiteralPath "路径" -Encoding UTF8 -Raw
```

路径中有中文或空格时，必须使用 `-LiteralPath`。不要依赖未指定编码的 `Get-Content`。

## 4. 禁止的高风险写法

不要把包含中文的大段 Python 源码通过 PowerShell 管道直接传给 Python，例如：

```powershell
@'
这里有中文源码
'@ | python -
```

这很容易触发 Windows 管道编码问题。

如果必须临时执行 Python：

1. 优先只在 `python -c` 中使用 ASCII 内容。
2. 或使用 Unicode 转义表达中文。
3. 或把逻辑写成项目内明确的 `.py` 文件，再运行该文件。

不要用 PowerShell 管道传输中文源码作为常规方案。

## 5. 判断“文件乱码”还是“终端显示乱码”

如果 PowerShell 显示中文乱码，不要立刻认为文件坏了。

先用 Python 检查真实内容：

```python
from pathlib import Path

p = Path("目标文件")
s = p.read_text(encoding="utf-8")
print(s[:500].encode("unicode_escape").decode("ascii"))
```

如果看到类似 `\u5931\u8d25\u590d\u76d8`，说明文件本身是正常 UTF-8，只是终端显示有问题。

检查文件是否真的损坏，可以搜索：

```text
????
�
涓
鈱
鈭
```

注意：不要只因为 PowerShell 输出异常就直接修文件。

## 6. JSON 输出规则

所有模型输出、结果下载、复盘报告都必须是 UTF-8 JSON。

写 JSON：

```python
Path(path).write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
```

读 JSON：

```python
json.loads(Path(path).read_text(encoding="utf-8-sig"))
```

终端打印 JSON 时，如果包含数学符号，例如 `⌊`、`∑`，优先使用：

```python
sys.stdout.buffer.write((text + "\n").encode("utf-8"))
```

避免 Windows 控制台编码导致打印失败。

## 7. result 失败复盘流程

遇到错题，不要直接凭印象补规则。固定按下面流程走：

```text
1. 下载 resultXX.json
2. 跑 failure_review.py
3. 看系统建议沉淀到哪一层
4. 用户确认
5. 再写入 memory / knowledge_base / math_tools / symbolic_tools / prompts / user_agent
```

命令示例：

```powershell
python failure_review.py "../../result (24).json"
```

如果要保存复盘报告：

```powershell
python failure_review.py "../../result (24).json" -o review_result24.json
```

重点看这些字段：

```text
findings
recommended_targets
memory_candidate
knowledge_draft
next_steps
```

## 8. result 分析时先判断错在哪一层

分析失败结果时，必须先判断问题属于哪一类：

| 现象 | 优先检查 |
|---|---|
| 只做第一问 | prompts.py、user_agent.py、多问覆盖检查 |
| 答案算错 | knowledge_base、math_tools、symbolic_tools |
| verifier 误判 | prompts.py、user_agent.py、本地证据仲裁 |
| RAG 没命中 | knowledge_base 标题、触发词、rag.py |
| 速度太慢 | user_agent.py 的模式配置、RAG 长度、thinking、verifier |
| JSON 解析失败 | prompts.py、llm.py、schema repair |
| 页面显示异常 | app.py |
| final_answer 为空但 answer_latex 有内容 | user_agent.py、app.py |

不要把所有问题都归因到 prompt。

## 9. 各文件职责边界

### app.py

只负责页面：

- 模式选择
- 输入框
- 结果展示
- JSON 下载
- 页面排版

不要在 `app.py` 写数学逻辑。

### user_agent.py

负责主流程：

- 调用 solver
- 调用 verifier
- 调用 RAG
- 调用 memory
- 调用本地工具检查
- 控制修正轮数
- 判断最终状态

不要在 `user_agent.py` 塞大段数学知识。

### prompts.py

负责模型行为规范：

- JSON 格式
- 多问全答
- 证明完整性
- verifier 规则
- 修正时的输出要求

不要在 `prompts.py` 写某道题的固定答案。

### routing.py

只放短领域提醒。

适合：

```text
解析几何：先列坐标系、动点范围、参数限制；最值题必须给取等点并代回。
```

不适合：

```text
完整椭圆公式大全、十几段题型教材、完整例题解法。
```

### knowledge_base/*.md

放 RAG 可检索的详细方法：

- 题型模板
- 常见错误
- 标准做法
- 验证方法
- 触发词

### memory/learned_rules.json

放历史错误经验：

- 短规则
- 触发词
- 常见误判
- 避坑提醒

不要放长篇教材。

### math_tools.py

放本地可机械判断的检查：

- 多问是否漏答
- 小样例枚举
- 候选公式代入
- 取等点检查
- 明显反例检查

### symbolic_tools.py

放 SymPy 或符号验证：

- 表达式等价
- 方程残差
- 代点验证
- 轨迹方程验证
- 简单恒等式验证

## 10. 不要一点一点硬编码单题

如果某道题错了，不要第一反应写：

```python
if "某道题原文" in problem:
    return "固定答案"
```

应该先判断它属于哪种可复用能力：

| 错误类型 | 应沉淀到 |
|---|---|
| 缺解题方法 | knowledge_base |
| 常犯同类错误 | memory |
| 可枚举验证 | math_tools |
| 可符号计算验证 | symbolic_tools |
| verifier 行为不可靠 | prompts / user_agent |
| 流程策略有问题 | user_agent |
| 展示或下载问题 | app |

## 11. 新增知识库模板

新增 `knowledge_base/*.md` 时，建议使用：

```md
# 题型名称

触发词：
- xxx
- xxx
- xxx

适用场景：
说明这类方法适合什么题。

标准做法：
1. 第一步
2. 第二步
3. 第三步

常见错误：
- 错误一
- 错误二

验证方法：
- 如何代回
- 如何构造小样例
- 如何判断取等点
```

标题和触发词非常重要，会影响 RAG 命中率。

## 12. 新增本地工具规则模板

新增 `math_tools.py` 检查时，优先写成独立函数：

```python
def check_xxx(problem: str, solution_text: str) -> list[dict]:
    """检查某类可机械判断的错误。

    参数：
        problem：题目文本。
        solution_text：候选解答文本。

    返回：
        检查结果列表。每项包含 name、passed、severity、issue。
    """
```

规则原则：

- 能明确反驳，才标高风险。
- 只能说明证明不足，就标“证明不足”，不要直接判答案错。
- 不要把单题答案硬编码成长期规则。
- 每个检查函数只负责一类错误。

## 13. verifier 仲裁规则

如果 verifier 给出反例，必须检查：

- candidate 和 expected 是否真的不同。
- 反例是否满足原题条件。
- 反例是否只是文字断言，没有计算过程。
- 本地工具是否支持候选答案。

如果出现：

```text
candidate == expected
```

不能把它当反例。

这种情况应标记为：

```text
verifier_conflict
```

并交给本地工具或人工复核。

## 14. 速度优化原则

不要一慢就删 verifier，也不要一错就开 deep。

优先排查：

1. 是否开启 deep。
2. thinking_solve 是否开启。
3. thinking_verify 是否开启。
4. RAG top_k 是否太大。
5. knowledge_base 片段是否过长。
6. verifier 是否重复长输出。
7. API 是否超时或限速。

推荐模式：

```text
fast：测试接口和页面
contest：默认参赛模式
strict：contest 错题复算
deep：最后兜底，不作为默认
```

## 15. 修改后必须验证

每次修改后至少做：

```powershell
python -m py_compile 文件1.py 文件2.py
```

如果改了失败复盘：

```powershell
python failure_review.py "../../result (24).json"
```

如果改了 JSON 读写：

- 检查中文是否正常。
- 检查数学符号是否正常。
- 检查是否出现 `????` 或 `�`。

如果改了 Word / Markdown 文档：

- 检查是否 UTF-8。
- 检查标题结构。
- 检查是否有乱码。
- 如果环境有 LibreOffice，再做 docx 渲染检查。

## 16. 文档同步规则

如果改了维护逻辑，需要同步：

- README.md
- AGENTS.md
- v6_参赛版维护教程.md

如果 Word 版教程仍在使用，也要同步：

- v6_参赛版维护教程.docx

不要只改代码，不改维护说明。
