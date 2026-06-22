"""Streamlit 网页入口与结果展示文件。

本文件包括：题目输入、运行模式选择、阶段结果渲染、最终答案展示、JSON 下载。
本文件控制：用户在网页上如何调用后端 solve_problem，以及如何查看 RAG/记忆/工具检查结果。
优化时这样操作：
- 想调整流程质量和速度，优先改 user_agent.py 的 PIPELINE_MODES。
- 想增加网页按钮、选项、展示区，再改本文件。
- 本文件不应写解题规则、RAG 内容或数学验算逻辑。

启动：streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from user_agent import PIPELINE_VERSION, solve_problem
from llm import BASE_URL, MODEL_NAME, RPM, THINKING_MODE


st.set_page_config(
    page_title="竞赛数学智能体 v6",
    page_icon="🧮",
    layout="wide",
)


STAGE_META = {
    "understanding": ("📖", "题目理解", "识别领域、抽取已知与求解目标"),
    "plan": ("🗺️", "解题规划", "拆分步骤，标出潜在陷阱"),
    "solution": ("⚙️", "路由 + 求解", "按领域分发到专用 solver，逐步推导"),
    "verifications": ("✅", "独立校验", "Verifier 与本地工具共同检查；不通过则触发自动修正"),
    "explanation": ("💡", "教学解释", "讲透直觉、关键洞察、常见陷阱、延伸"),
}


with st.sidebar:
    st.markdown("## 🧮 竞赛数学智能体 v6")
    st.caption("挑战杯 · 揭榜挂帅 · XH-202627")

    st.divider()
    st.markdown("### 当前后端")
    st.code(
        f"model:    {MODEL_NAME}\n"
        f"base_url: {BASE_URL}\n"
        f"thinking: {THINKING_MODE if THINKING_MODE is not None else '—'}\n"
        f"rpm:      {RPM if RPM else '—'}\n"
        f"pipeline: {PIPELINE_VERSION}",
        language="text",
    )

    st.divider()
    st.markdown("### 运行选项")
    run_mode = st.selectbox(
        "运行模式",
        options=["contest", "fast", "strict", "deep"],
        index=0,
        help="contest 是参赛默认模式；fast 用于接口调试；strict 用于疑难题重算；deep 是更慢的深度兜底模式。",
    )
    enable_verifier = st.checkbox("允许校验 Agent", value=True)
    max_revisions = st.number_input(
        "用户允许的最大修正轮数", min_value=0, max_value=3, value=1, step=1,
        help="实际修正轮数还会受到运行模式上限约束。"
    )

    st.divider()
    st.markdown("### 架构")
    for emoji, name, desc in STAGE_META.values():
        st.markdown(f"{emoji} **{name}** — {desc}")


@st.cache_data
def load_samples() -> list[dict]:
    """从 JSONL 文件加载示例题目。

    返回：
        示例题目字典列表。
    """
    out: list[dict] = []
    sample_files = [
        Path("examples/theoremqa_all.jsonl"),
        Path("examples/sample_problems.jsonl"),
        Path("examples/linear_algebra.jsonl"),
    ]

    for file_path in sample_files:
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        st.warning(f"解析 {file_path} 第 {line_num} 行失败：{exc}")
                        continue
                    if "problem" not in obj:
                        if "问题" in obj:
                            obj["problem"] = obj.pop("问题")
                        elif "question" in obj:
                            obj["problem"] = obj.pop("question")
                    if "problem" not in obj:
                        st.warning(f"{file_path} 第 {line_num} 行缺少 problem/question/问题 字段，已跳过")
                        continue
                    out.append(obj)
    return out


def render_text_or_items(value: Any) -> None:
    """稳定渲染模型返回的字符串或列表，避免字符串被当作字符列表逐字换行。

    参数：
        value：模型返回的文本、列表或其他可展示对象。
    """
    if value is None or value == "":
        return
    if isinstance(value, str):
        st.markdown(value)
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                st.markdown(f"- {item}")
            else:
                st.json(item, expanded=False)
        return
    st.write(value)


st.title("数学问题求解")
st.caption("演示增强流水线：读题 → 规划 → RAG/记忆增强求解 → 工具校验 → Verifier → 解释")

samples = load_samples()
sample_labels = ["（手动输入）"] + [
    f"[{s.get('id', '?')}] {s.get('topic') or s.get('domain', '?')}：{s.get('problem', '')[:40]}..."
    for s in samples
]
choice_idx = st.selectbox(
    "载入示例题（覆盖分析、复变、运筹、PDE、高代）",
    options=range(len(sample_labels)),
    format_func=lambda i: sample_labels[i],
    index=0,
)

default_problem = "" if choice_idx == 0 else samples[choice_idx - 1]["problem"]
default_id = None if choice_idx == 0 else samples[choice_idx - 1].get("id")

problem_text = st.text_area(
    "题目内容（支持 LaTeX，例如 $\\int_0^1 x^2 dx$）",
    value=default_problem,
    height=140,
    placeholder="把题目粘进来...",
    key=f"problem_{choice_idx}",
)

col_a, col_b = st.columns([1, 5])
run = col_a.button("🚀 开始求解", type="primary", use_container_width=True)


def display_field(value: Any, default: str = "未识别") -> str:
    """将页面字段统一转成可读文本，避免空值或占位符直接显示成问号。

    参数：
        value：后端返回的原始字段值。
        default：字段缺失或为空时展示的兜底文本。

    返回：
        适合页面展示的字符串。
    """
    if value is None:
        return default
    text = str(value).strip()
    return default if text in {"", "?", "??", "unknown", "None", "null", "未知"} else text


def render_understanding(d: dict) -> None:
    """渲染读题阶段，兼容单问 find 和多问 sub_questions。"""
    domain = display_field(d.get("domain"))
    subtype = display_field(d.get("subtype"))
    problem_type = display_field(d.get("problem_type"))
    source = display_field(d.get("classification_source"), "llm")
    confidence = d.get("confidence")
    confidence_text = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else display_field(confidence)
    st.markdown(f"- **领域**：`{domain}` | **子类型**：`{subtype}`")
    st.caption(f"任务类型：{problem_type} | 分类来源：{source} | 置信度：{confidence_text}")
    if d.get("sub_questions"):
        st.markdown("- **小问目标**：")
        for item in d["sub_questions"]:
            st.markdown(f"  - **{item.get('sub_idx', '?')}** {item.get('target', '')}")
    elif d.get("find"):
        st.markdown(f"- **求解目标**：{d['find']}")
    elif d.get("conclusion"):
        st.markdown(f"- **证明目标**：{d['conclusion']}")
    if d.get("given"):
        st.markdown("- **已知条件**：")
        render_text_or_items(d["given"])
    if d.get("premise"):
        st.markdown("- **证明前提**：")
        render_text_or_items(d["premise"])
    if d.get("key_concepts"):
        st.markdown("- **关键概念**：")
        render_text_or_items(d["key_concepts"])


def render_plan(d: dict) -> None:
    """渲染规划阶段，按小问标识展示每一步。"""
    st.markdown(f"**整体策略**：{d.get('strategy', '?')}")
    for step in d.get("steps", []):
        sub_idx = f" {step.get('sub_idx')}" if step.get("sub_idx") else ""
        st.markdown(f"- **步骤 {step.get('step')}{sub_idx}**　{step.get('goal', '')}　→　_{step.get('method', '')}_")
    if d.get("potential_pitfalls"):
        with st.expander("⚠️ 潜在陷阱", expanded=False):
            render_text_or_items(d["potential_pitfalls"])


def render_solution(d: dict) -> None:
    """渲染求解阶段，兼容 steps 和结构化 sub_answers。"""
    for step in d.get("steps", []):
        sub_idx = f" {step.get('sub_idx')}" if step.get("sub_idx") else ""
        st.markdown(f"**步骤 {step.get('step')}{sub_idx}**")
        detail = step.get("derivation") or step.get("operation") or ""
        render_text_or_items(detail)
        result = step.get("intermediate_result") or step.get("result")
        if result:
            st.caption(f"结果： {result}")

    if d.get("sub_answers"):
        st.markdown("**分问答案**")
        for item in d["sub_answers"]:
            title = item.get("sub_idx", "?")
            target = item.get("target")
            st.markdown(f"**{title}** {target or ''}")
            if item.get("answer"):
                st.success(item["answer"])
            render_text_or_items(item.get("proof_or_derivation"))
            if item.get("verified_hint"):
                st.caption(f"自检： {item['verified_hint']}")

    final = d.get("final_answer")
    if final:
        st.success(f"**汇总答案**： {final}")
    if d.get("verify_check") is not None:
        st.info(f"自验证： {'通过' if d.get('verify_check') else '未通过或未完成'}")


def render_verifications(d: dict) -> None:
    """渲染校验阶段，展示 Verifier 与本地工具检查结果。"""
    local_checks = d.get("local_sanity_checks", [])
    if local_checks:
        st.markdown("**本地数学工具检查**")
        for item in local_checks:
            st.warning(f"{item.get('name', 'local_check')}：{item.get('issue', '')}")
            if item.get("failed_claims"):
                st.markdown(f"- 失败结论：{', '.join(map(str, item.get('failed_claims')))}")
            if item.get("forbidden_claims"):
                st.markdown(f"- 禁止结论：{', '.join(map(str, item.get('forbidden_claims')))}")
            if item.get("counterexamples"):
                for counterexample in item.get("counterexamples", []):
                    st.markdown(f"- 反例：{counterexample}")
            if item.get("sample_table"):
                st.markdown(f"- 样例表：{item.get('sample_table')}")
            if item.get("repair_constraints"):
                st.markdown(f"- 修正约束：{'; '.join(map(str, item.get('repair_constraints')))}")
            if item.get("suggested_route"):
                st.caption(f"建议重算路线：{item.get('suggested_route')}")

    support_checks = d.get("support_checks", [])
    if support_checks:
        st.markdown("**本地支持检查**")
        for item in support_checks:
            message = f"{item.get('name', 'support_check')}：{item.get('evidence') or item.get('issue', '')}"
            if item.get("passed") is True:
                st.success(message)
            else:
                st.warning(message)
            if item.get("sample_table"):
                st.json(item.get("sample_table"), expanded=False)
            if item.get("limitations"):
                st.caption(f"限制：{item.get('limitations')}")

    verifier_conflicts = d.get("verifier_conflicts", [])
    if verifier_conflicts:
        st.markdown("**Verifier 冲突仲裁**")
        for item in verifier_conflicts:
            st.info(f"{item.get('name', 'verifier_conflict')}：{item.get('reason', '')}")
            if item.get("self_conflicting_counterexamples"):
                st.markdown("- candidate 与 expected 相同的反例：")
                st.json(item.get("self_conflicting_counterexamples"), expanded=False)
            if item.get("supported_challenged_claims"):
                st.markdown(f"- 被本地支持检查支撑的争议结论：{', '.join(map(str, item.get('supported_challenged_claims')))}")

    verifications = d.get("verifications", [])
    for i, v in enumerate(verifications, 1):
        if "error" in v:
            st.error(f"第 {i} 轮校验异常： {v['error']}")
            continue
        verified = v.get("verified")
        confidence = v.get("confidence", "—")
        icon = "✅" if verified else "❌"
        st.markdown(f"**第 {i} 轮** {icon}　置信度： `{confidence}`")
        for check in v.get("sub_question_checks", []) or []:
            mark = "✅" if check.get("verified") else "❌"
            answered = "已回答" if check.get("answered") else "未回答"
            st.markdown(f"- **{check.get('sub_idx', '?')}** {mark} {answered} {check.get('issue', '')}")
        if v.get("independent_check"):
            st.caption(f"独立验证： {v['independent_check']}")
        for item in v.get("issues", []) or []:
            st.warning(f"问题（步骤 {item.get('step', '?')} · {item.get('type', '?')}）：{item.get('description', '')}")
            if item.get("failed_claims"):
                st.markdown(f"  - 失败结论：{', '.join(map(str, item.get('failed_claims')))}")
            if item.get("forbidden_claims"):
                st.markdown(f"  - 禁止结论：{', '.join(map(str, item.get('forbidden_claims')))}")
            if item.get("counterexamples"):
                for counterexample in item.get("counterexamples", []):
                    st.markdown(f"  - 反例：{counterexample}")
            if item.get("sample_table"):
                st.markdown(f"  - 样例表：{item.get('sample_table')}")
            if item.get("repair_constraints"):
                st.markdown(f"  - 修正约束：{'; '.join(map(str, item.get('repair_constraints')))}")
            if item.get("suggested_route"):
                st.caption(f"  建议路线：{item.get('suggested_route')}")
        if v.get("failed_claims"):
            st.markdown(f"- 失败结论：{', '.join(map(str, v.get('failed_claims')))}")
        if v.get("forbidden_claims"):
            st.markdown(f"- 禁止结论：{', '.join(map(str, v.get('forbidden_claims')))}")
        if v.get("counterexamples"):
            for counterexample in v.get("counterexamples", []):
                st.markdown(f"- 反例：{counterexample}")
        if v.get("sample_table"):
            st.markdown(f"- 样例表：{v.get('sample_table')}")
        if v.get("repair_constraints"):
            st.markdown(f"- 修正约束：{'; '.join(map(str, v.get('repair_constraints')))}")
        if v.get("suggested_fix"):
            st.markdown(f"_建议修正：{v['suggested_fix']}_")
    if not verifications and not local_checks:
        st.caption("未启用校验。")
def render_explanation(d: dict) -> None:
    """渲染教学解释阶段，兼容字符串和列表两种输出。"""
    if d.get("intuition"):
        st.markdown("**🔭 直觉**")
        render_text_or_items(d["intuition"])
    if d.get("key_insight"):
        st.markdown("**🔑 关键洞察**")
        render_text_or_items(d["key_insight"])
    if d.get("common_pitfalls"):
        st.markdown("**⚠️ 常见陷阱**")
        render_text_or_items(d["common_pitfalls"])
    if d.get("extensions"):
        st.markdown("**🌱 延伸思考**")
        render_text_or_items(d["extensions"])


RENDERERS = {
    "understanding": render_understanding,
    "plan": render_plan,
    "solution": render_solution,
    "verifications": render_verifications,
    "explanation": render_explanation,
}


if run:
    if not problem_text.strip():
        st.warning("请先输入题目内容。")
        st.stop()

    st.divider()
    st.subheader("推理过程")

    placeholders = {key: st.empty() for key in STAGE_META}
    progress_bar = st.progress(0.0, text="准备调用模型 ...")
    done = {"n": 0}

    def on_stage(name: str, data) -> None:
        """接收后端阶段回调，并把阶段结果渲染到页面。"""
        done["n"] += 1
        emoji, label, _ = STAGE_META.get(name, ("", name, ""))
        progress_bar.progress(done["n"] / len(STAGE_META), text=f"{emoji} 已完成 {label}")
        with placeholders[name].container(border=True):
            st.markdown(f"#### {emoji} {label}")
            renderer = RENDERERS.get(name)
            if renderer:
                try:
                    renderer(data)
                except Exception as exc:
                    st.json(data)
                    st.caption(f"（渲染异常 fallback 为 JSON：{exc}）")
            else:
                st.json(data)

    try:
        result = solve_problem(
            problem_text,
            problem_id=default_id,
            enable_verifier=enable_verifier,
            max_revisions=int(max_revisions),
            mode=run_mode,
            on_stage=on_stage,
        )
    except Exception as exc:
        st.error(f"求解过程异常：{exc}")
        st.stop()

    progress_bar.empty()

    st.divider()
    st.subheader("📍 最终答案")
    if result.get("final_status") == "unverified":
        st.warning(result.get("final_warning") or "最终答案未通过校验，请查看校验详情。")
    elif result.get("final_status") == "locally_supported":
        st.info(result.get("final_warning") or "最终答案由本地支持检查暂时支持，请查看 Verifier 冲突仲裁。")

    if result.get("local_sanity_checks"):
        with st.expander("本地数学工具检查", expanded=True):
            for item in result["local_sanity_checks"]:
                st.warning(f"{item.get('name', 'local_check')}：{item.get('issue', '')}")

    if result.get("support_checks"):
        with st.expander("本地支持检查", expanded=result.get("final_status") == "locally_supported"):
            for item in result["support_checks"]:
                message = f"{item.get('name', 'support_check')}：{item.get('evidence') or item.get('issue', '')}"
                if item.get("passed") is True:
                    st.success(message)
                else:
                    st.warning(message)
                if item.get("sample_table"):
                    st.json(item.get("sample_table"), expanded=False)

    if result.get("verifier_conflicts"):
        with st.expander("Verifier 冲突仲裁", expanded=True):
            st.json(result.get("verifier_conflicts"), expanded=False)

    if result.get("sub_answers"):
        for item in result["sub_answers"]:
            st.markdown(f"**{item.get('sub_idx', '?')}** {item.get('target', '')}")
            if item.get("answer"):
                st.code(item["answer"], language="text")
    final_latex = result.get("answer_latex")
    final_plain = result.get("final_answer")
    if final_latex:
        st.markdown(f"$$\n{final_latex}\n$$")
    if final_plain:
        st.code(final_plain, language="text")
    if not result.get("sub_answers") and not final_latex and not final_plain:
        st.warning("未提取到最终答案，请检查 solution 阶段输出。")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("运行模式", result.get("run_mode") or run_mode)
    if result.get("final_status") == "locally_supported":
        verify_label = "本地支持"
    else:
        verify_label = "通过 ✓" if result.get("verified") else ("未通过" if result.get("verified") is False else "—")
    c2.metric("校验结果", verify_label)
    c3.metric("LLM 调用", result["metadata"]["llm_calls"])
    c4.metric("耗时", f"{result['metadata']['elapsed_ms'] / 1000:.1f} s")
    st.caption(f"路由领域：{result.get('routed_domain') or '—（兜底）'}")

    tok = result["metadata"]["tokens"]
    st.caption(
        f"Token 消耗：prompt {tok['prompt_tokens']} + completion {tok['completion_tokens']} = "
        f"total {tok['total_tokens']}"
    )

    with st.expander("检索与记忆命中", expanded=False):
        st.markdown("**RAG 检索命中**")
        st.json(result.get("retrieved_knowledge", []), expanded=False)
        st.markdown("**历史错误记忆命中**")
        st.json(result.get("memory_rules", []), expanded=False)

    st.download_button(
        "⬇️ 下载完整 JSON 结果",
        data=json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"{result.get('problem_id') or 'result'}.json",
        mime="application/json; charset=utf-8",
        use_container_width=True,
    )

    with st.expander("查看完整原始 JSON", expanded=False):
        st.json(result, expanded=False)

