"""命令行入口。

用法：
    # 跑单题
    python cli.py --problem "求 \\int_0^1 x^2 dx"

    # 批量跑评测集（题目 JSON：[{"id": "...", "problem": "..."}]）
    python cli.py --file examples/sample_problems.json --out results.jsonl

    # 输出仅含 id 和 final_answer 的简版（用于提交）
    python cli.py --file dataset.json --out results.jsonl --submission submission.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，需要强制 UTF-8 才能打印中文 JSON
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from user_agent import solve_problem


def main() -> None:
    p = argparse.ArgumentParser(description="intern-s1 数学 Agent")
    p.add_argument("--problem", help="单道题目文本")
    p.add_argument("--id", default=None, help="单题模式下的 id")
    p.add_argument("--file", help="批量题目 JSON 文件路径")
    p.add_argument("--out", default="results.jsonl", help="完整结果 JSONL 输出路径")
    p.add_argument(
        "--submission",
        default=None,
        help="可选：简版提交文件路径，仅含 {id, final_answer}",
    )
    p.add_argument(
        "--no-verifier",
        action="store_true",
        help="关闭校验 Agent（更省 token，但失去自纠错能力）",
    )
    p.add_argument(
        "--max-revisions",
        type=int,
        default=1,
        help="校验未通过时的最大修正轮数（默认 1）",
    )
    args = p.parse_args()
    solve_kwargs = {
        "enable_verifier": not args.no_verifier,
        "max_revisions": args.max_revisions,
    }

    if args.problem:
        result = solve_problem(args.problem, problem_id=args.id, **solve_kwargs)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if not args.file:
        p.error("必须指定 --problem 或 --file")

    problems = json.loads(Path(args.file).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    sub_path = Path(args.submission) if args.submission else None

    with out_path.open("w", encoding="utf-8") as fout, (
        sub_path.open("w", encoding="utf-8") if sub_path else _noop()
    ) as fsub:
        for i, item in enumerate(problems, 1):
            pid = item.get("id")
            try:
                r = solve_problem(item["problem"], problem_id=pid, **solve_kwargs)
                status = "ok" if "error_stage" not in r else f"fail@{r['error_stage']}"
            except Exception as e:
                r = {"problem_id": pid, "problem": item.get("problem"), "error": str(e)}
                status = "exception"

            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
            fout.flush()
            if fsub:
                fsub.write(
                    json.dumps(
                        {"id": pid, "final_answer": r.get("final_answer")},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                fsub.flush()
            print(f"[{i}/{len(problems)}] {pid} -> {status}")


class _noop:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    main()
