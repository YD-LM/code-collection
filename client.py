"""命令行入口。

用法：
    # 跑单题
    python cli.py --problem "求 \\int_0^1 x^2 dx"

    # 批量跑评测集（支持 JSON 数组 / JSONL 两种题库）
    python cli.py --file examples/sample_problems.jsonl --out results.jsonl

    # 输出仅含 id 和 final_answer 的简版（用于提交）
    python cli.py --file dataset.jsonl --out results.jsonl --submission submission.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from user_agent import solve_problem

# 状态常量
STATUS_OK = "ok"
STATUS_EXCEPTION = "exception"
PREFIX_FAIL = "fail@"


def main() -> None:
    parser = argparse.ArgumentParser(description="Intern-S1 数学 Agent")
    parser.add_argument("--problem", help="单道题目文本")
    parser.add_argument("--id", default=None, help="单题模式下的 id")
    parser.add_argument("--file", help="批量题目文件路径（支持 JSON / JSONL）")
    parser.add_argument("--out", default="results.jsonl", help="完整结果 JSONL 输出路径")
    parser.add_argument(
        "--submission",
        default=None,
        help="可选：简版提交文件路径，仅含 {id, final_answer}",
    )
    parser.add_argument(
        "--no-verifier",
        action="store_true",
        help="关闭校验 Agent（更省 token，但失去自纠错能力）",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        default=1,
        help="校验未通过时的最大修正轮数（默认 1）",
    )
    args = parser.parse_args()

    # 禁止同时使用 --problem 和 --file
    if args.problem and args.file:
        parser.error("参数冲突：--problem 和 --file 不能同时使用")

    solve_kwargs = {
        "enable_verifier": not args.no_verifier,
        "max_revisions": args.max_revisions,
    }

    # 单题模式
    if args.problem:
        result = solve_problem(args.problem, problem_id=args.id, **solve_kwargs)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    # 必须指定文件
    if not args.file:
        parser.error("必须指定 --problem 或 --file")

    file_path = Path(args.file)
    if not file_path.exists():
        parser.error(f"文件不存在: {args.file}")

    # 自动兼容 JSON 数组 / JSONL 格式题库
    try:
        content = file_path.read_text(encoding="utf-8").strip()
        if content.startswith("["):
            # 标准 JSON 数组
            problems = json.loads(content)
        else:
            # JSONL 逐行解析
            problems = []
            with file_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        problems.append(json.loads(line))
    except json.JSONDecodeError:
        parser.error(f"文件 {args.file} 格式非法，不是合法 JSON / JSONL")

    out_path = Path(args.out)
    sub_path = Path(args.submission) if args.submission else None

    # 批量处理写入 JSONL
    with out_path.open("w", encoding="utf-8") as fout:
        fsub = sub_path.open("w", encoding="utf-8") if sub_path else None
        try:
            total = len(problems)
            for idx, item in enumerate(problems, 1):
                pid = item.get("id")
                problem_text = item.get("problem", "")
                try:
                    res = solve_problem(problem_text, problem_id=pid,** solve_kwargs)
                    status = STATUS_OK if "error_stage" not in res else f"{PREFIX_FAIL}{res['error_stage']}"
                except Exception as e:
                    res = {
                        "problem_id": pid,
                        "problem": problem_text,
                        "error": str(e)
                    }
                    status = STATUS_EXCEPTION

                # 写入完整结果
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                # 写入提交文件
                if fsub:
                    sub_data = {"id": pid, "final_answer": res.get("final_answer")}
                    fsub.write(json.dumps(sub_data, ensure_ascii=False) + "\n")

                print(f"[{idx}/{total}] {pid} -> {status}")
        finally:
            if fsub:
                fsub.close()
            fout.flush()


if __name__ == "__main__":
    main()
