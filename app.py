"""Streamlit Web Demo：交互式展示五阶段数学 Agent。

启动：streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from user_agent import solve_problem
from llm import BASE_URL, MODEL_NAME, RPM, THINKING_MODE


st.set_page_config(
    page_title="intern-s1 数学智能体",
    page_icon="🧮",
    layout="wide",
)


STAGE_META = {
    "understanding": ("📖", "题目理解", "识别领域、抽取已知与求解目标"),
    "plan": ("🗺️", "解题规划", "拆分步骤，标出潜在陷阱"),
    "solution": ("⚙️", "路由 + 求解", "按领域分发到专用 solver，逐步推导"),
    "verifications": ("✅", "独立校验", "Verifier 独立验证；不通过则触发自动修正"),
    "explanation": ("💡", "教学解释", "讲透直觉、关键洞察、常见陷阱、延伸"),
}


# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("## 🧮 intern-s1 数学智能体")
    st.caption("挑战杯 · 揭榜挂帅 · XH-202627")

    st.divider()
    st.markdown("### 当前后端")
    st.code(
        f"model:    {MODEL_NAME}\n"
        f"base_url: {BASE_URL}\n"
        f"thinking: {THINKING_MODE if THINKING_MODE is not None else '—'}\n"
        f"rpm:      {RPM if RPM else '—'}",
        language="text",
    )

    st.divider()
    st.markdown("### 运行选项")
    enable_verifier = st.checkbox("启用校验 Agent（推荐）", value=True)
    max_revisions = st.number_input(
        "最大修正轮数", min_value=0, max_value=3, value=1, step=1
    )

    st.divider()
    st.markdown("### 架构")
    for emoji, name, desc in STAGE_META.values():
        st.markdown(f"{emoji} **{name}** — {desc}")


# ---------- Sample loader ----------
# ---------- Sample loader (JSONL格式) ----------
@st.cache_data
def load_samples() -> list[dict]:
    """从 JSONL 文件加载示例题目"""
    out: list[dict] = []
    
    # 支持多个 JSONL 文件
    sample_files = [
        Path("examples/sample_problems.jsonl"),
        Path("examples/linear_algebra.jsonl"),
        Path("examples/theoremqa_all.jsonl"),
    ]
    
    for file_path in sample_files:
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line:  # 跳过空行
                        try:
                            obj = json.loads(line)
                            # 兼容不同键名：将 "问题"/"question" 统一映射为 "problem"
                            if "problem" not in obj:
                                if "问题" in obj:
                                    obj["problem"] = obj.pop("问题")
                                elif "question" in obj:
                                    obj["problem"] = obj.pop("question")
                            if "problem" not in obj:
                                st.warning(f"{file_path} 第 {line_num} 行缺少 'problem'/'question'/'问题' 字段，已跳过")
                                continue
                            out.append(obj)
                        except json.JSONDecodeError as e:
                            st.warning(f"解析 {file_path} 第 {line_num} 行失败：{e}")
    
    return out


# ---------- Main ----------
st.title("数学问题求解")
st.caption("演示五阶段流水线：读题 → 规划 → 路由 + 求解 → 校验 → 解释")

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

default_problem = "" if choice_idx == 0 else samples[choice_idx - 1].get("problem", "")
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


def render_understanding(d: dict) -> None:
    st.markdown(f"- **领域**：`{d.get('domain', '?')}`　|　**子类型**：`{d.get('subtype', '?')}`")
    st.markdown(f"- **求解目标**：{d.get('find', '?')}")
    if d.get("given"):
        st.markdown("- **已知条件**：")
        for g in d["given"]:
            st.markdown(f"  - {g}")
    if d.get("key_concepts"):
        st.markdown("- **关键概念**：" + "、".join(d["key_concepts"]))


def render_plan(d: dict) -> None:
    st.markdown(f"**整体策略**：{d.get('strategy', '?')}")
    for step in d.get("steps", []):
        st.markdown(f"- **步骤 {step.get('step')}**　{step.get('goal', '')}　→　_{step.get('method', '')}_")
    if d.get("potential_pitfalls"):
        with st.expander("⚠️ 潜在陷阱", expanded=False):
            for p in d["potential_pitfalls"]:
                st.markdown(f"- {p}")


def render_solution(d: dict) -> None:
    for step in d.get("steps", []):
        st.markdown(f"**步骤 {step.get('step')}**")
        st.markdown(step.get("derivation", ""))
        if step.get("intermediate_result"):
            st.caption(f"中间结果： {step['intermediate_result']}")
    final = d.get("final_answer")
    if final:
        st.success(f"**初步答案**： {final}")
    if d.get("verification"):
        st.info(f"自验证： {d['verification']}")


def render_verifications(d: dict) -> None:
    verifications = d.get("verifications", [])
    for i, v in enumerate(verifications, 1):
        if "error" in v:
            st.error(f"第 {i} 轮校验异常： {v['error']}")
            continue
        verified = v.get("verified")
        confidence = v.get("confidence", "—")
        icon = "✅" if verified else "❌"
        st.markdown(f"**第 {i} 轮** {icon}　置信度： `{confidence}`")
        if v.get("independent_check"):
            st.caption(f"独立验证： {v['independent_check']}")
        issues = v.get("issues", [])
        if issues:
            for it in issues:
                st.warning(f"问题（步骤 {it.get('step', '?')} · {it.get('type', '?')}）：{it.get('description', '')}")
        if v.get("suggested_fix"):
            st.markdown(f"_建议修正：{v['suggested_fix']}_")
    if not verifications:
        st.caption("未启用校验。")


def render_explanation(d: dict) -> None:
    if d.get("intuition"):
        st.markdown(f"**🔭 直觉**　{d['intuition']}")
    if d.get("key_insight"):
        st.markdown(f"**🔑 关键洞察**　{d['key_insight']}")
    if d.get("common_pitfalls"):
        st.markdown("**⚠️ 常见陷阱**")
        for p in d["common_pitfalls"]:
            st.markdown(f"- {p}")
    if d.get("extensions"):
        st.markdown("**🌱 延伸思考**")
        for e in d["extensions"]:
            st.markdown(f"- {e}")


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

    placeholders = {k: st.empty() for k in STAGE_META}
    progress_bar = st.progress(0.0, text="准备调用 intern-s1 ...")
    done = {"n": 0}

    def on_stage(name: str, data) -> None:
        done["n"] += 1
        emoji, label, _ = STAGE_META.get(name, ("", name, ""))
        progress_bar.progress(done["n"] / len(STAGE_META), text=f"{emoji} 已完成 {label}")
        with placeholders[name].container(border=True):
            st.markdown(f"#### {emoji} {label}")
            renderer = RENDERERS.get(name)
            if renderer:
                try:
                    renderer(data)
                except Exception as e:
                    st.json(data)
                    st.caption(f"（渲染异常 fallback 为 JSON：{e}）")
            else:
                st.json(data)

    try:
        result = solve_problem(
            problem_text,
            problem_id=default_id,
            enable_verifier=enable_verifier,
            max_revisions=int(max_revisions),
            on_stage=on_stage,
        )
    except Exception as e:
        st.error(f"求解过程异常：{e}")
        st.stop()

    progress_bar.empty()

    # ---- 最终结果 ----
    st.divider()
    st.subheader("📍 最终答案")

    final_latex = result.get("answer_latex")
    final_plain = result.get("final_answer")
    if final_latex:
        st.markdown(f"$$\n{final_latex}\n$$")
    if final_plain:
        st.code(final_plain, language="text")
    if not final_latex and not final_plain:
        st.warning("未提取到最终答案，请检查 solution 阶段输出。")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("路由领域", result.get("routed_domain") or "—（兜底）")
    c2.metric("校验结果", "通过 ✓" if result.get("verified") else ("未通过" if result.get("verified") is False else "—"))
    c3.metric("LLM 调用", result["metadata"]["llm_calls"])
    c4.metric("耗时", f"{result['metadata']['elapsed_ms'] / 1000:.1f} s")

    tok = result["metadata"]["tokens"]
    st.caption(
        f"Token 消耗：prompt {tok['prompt_tokens']} + completion {tok['completion_tokens']} = "
        f"total {tok['total_tokens']}"
    )

    st.download_button(
        "⬇️ 下载完整 JSON 结果",
        data=json.dumps(result, ensure_ascii=False, indent=2),
        file_name=f"{result.get('problem_id') or 'result'}.json",
        mime="application/json",
        use_container_width=True,
    )

    with st.expander("查看完整原始 JSON", expanded=False):
        st.json(result, expanded=False)
