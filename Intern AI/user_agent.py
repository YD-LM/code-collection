"""数学 Agent 主流程：读题 -> 规划 -> 路由+求解 -> 校验(可修正) -> 解释 -> JSON。

各阶段尽量隔离：任意阶段失败会把 error 写入结果并跳到后续可执行的阶段，不抛出。
"""
from __future__ import annotations

import json
import time
from typing import Any

from llm import MODEL_NAME, ask_json
from prompts import EXPLAIN_SYSTEM, PLAN_SYSTEM, READ_SYSTEM, VERIFY_SYSTEM
from routing import get_solver_system


def _ctx(label: str, obj: Any) -> str:
    return f"{label}:\n{json.dumps(obj, ensure_ascii=False, indent=2)}"


class _Tally:
    def __init__(self) -> None:
        self.tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.calls = 0

    def add(self, usage: Any) -> None:
        if not usage:
            return
        self.calls += 1
        for k in self.tokens:
            self.tokens[k] += getattr(usage, k, 0) or 0


def _safe_ask(tally: _Tally, system: str, user: str, *, temperature: float) -> tuple[dict | None, str | None]:
    """包一层异常捕获；成功返回 (data, None)，失败返回 (None, err_msg)。"""
    try:
        data, usage = ask_json(system, user, temperature=temperature)
        tally.add(usage)
        return data, None
    except Exception as e:
        return None, str(e)


def solve_problem(
    problem: str,
    problem_id: str | None = None,
    *,
    enable_verifier: bool = True,
    max_revisions: int = 1,
    on_stage=None,
) -> dict:
    """on_stage(name, data): 可选回调，每完成一个阶段触发一次。
    name ∈ {understanding, plan, solution, verifications, explanation}
    """
    def _fire(name: str, data) -> None:
        if on_stage is not None:
            try:
                on_stage(name, data)
            except Exception:
                pass  # UI 回调不应阻塞主流程
    t0 = time.time()
    tally = _Tally()
    result: dict[str, Any] = {
        "problem_id": problem_id,
        "problem": problem,
        "model": MODEL_NAME,
    }

    # 1) 读题
    understanding, err = _safe_ask(
        tally, READ_SYSTEM, f"题目：{problem}", temperature=0.1
    )
    if err:
        result["understanding"] = {"error": err}
        result["error_stage"] = "understanding"
        return _finalize(result, tally, t0)
    result["understanding"] = understanding
    _fire("understanding", understanding)

    # 2) 规划
    plan_user = f"题目：{problem}\n\n{_ctx('题目理解', understanding)}"
    plan, err = _safe_ask(tally, PLAN_SYSTEM, plan_user, temperature=0.2)
    if err:
        result["plan"] = {"error": err}
        result["error_stage"] = "plan"
        return _finalize(result, tally, t0)
    result["plan"] = plan
    _fire("plan", plan)

    # 3) 路由 + 求解
    solver_system, routed_domain = get_solver_system(understanding.get("domain"))
    result["routed_domain"] = routed_domain  # None 表示走兜底
    solve_user = (
        f"题目：{problem}\n\n"
        f"{_ctx('题目理解', understanding)}\n\n"
        f"{_ctx('解题计划', plan)}"
    )
    solution, err = _safe_ask(tally, solver_system, solve_user, temperature=0.1)
    if err:
        result["solution"] = {"error": err}
        result["error_stage"] = "solution"
        return _finalize(result, tally, t0)
    result["solution"] = solution
    _fire("solution", solution)

    # 4) 校验 + （可选）修正
    verifications: list[dict] = []
    if enable_verifier:
        for revision in range(max_revisions + 1):
            verify_user = (
                f"题目：{problem}\n\n{_ctx('候选解答', solution)}"
            )
            verification, err = _safe_ask(
                tally, VERIFY_SYSTEM, verify_user, temperature=0.0
            )
            if err:
                verifications.append({"error": err})
                break
            verifications.append(verification)
            if verification.get("verified") or revision >= max_revisions:
                break

            # 触发修正：把审稿反馈拼回 solver
            revise_user = (
                solve_user
                + "\n\n"
                + _ctx("审稿反馈（请针对性修正后重出完整解答）", verification)
            )
            revised, err = _safe_ask(
                tally, solver_system, revise_user, temperature=0.1
            )
            if err:
                # 修正失败，保留原解
                break
            solution = revised
            result["solution"] = solution

    result["verifications"] = verifications
    if verifications:
        last = verifications[-1]
        result["verified"] = bool(last.get("verified")) if "verified" in last else None
    _fire("verifications", {"verifications": verifications, "final_solution": solution})

    # 5) 解释
    explain_user = f"题目：{problem}\n\n{_ctx('求解过程', solution)}"
    explanation, err = _safe_ask(
        tally, EXPLAIN_SYSTEM, explain_user, temperature=0.4
    )
    if err:
        result["explanation"] = {"error": err}
    else:
        result["explanation"] = explanation
        _fire("explanation", explanation)

    # 顶层暴露答案，便于评测脚本直接取
    if isinstance(solution, dict):
        result["final_answer"] = solution.get("final_answer")
        result["answer_latex"] = solution.get("answer_latex")

    return _finalize(result, tally, t0)


def _finalize(result: dict, tally: _Tally, t0: float) -> dict:
    result["metadata"] = {
        "elapsed_ms": int((time.time() - t0) * 1000),
        "llm_calls": tally.calls,
        "tokens": tally.tokens,
    }
    return result
