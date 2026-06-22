"""失败复盘命令入口：读取 result JSON 并输出应沉淀到哪一层。

本文件包括：命令行参数解析、result JSON 读取、失败复盘输出。
本文件控制：维护者如何把一次失败结果转成 memory、knowledge_base、math_tools、prompts 或 user_agent 的改动建议。
优化时这样操作：
- 平时运行：python failure_review.py "result (24).json"
- 如果 result 文件在项目外，可以传绝对路径或相对路径。
- 本脚本只生成建议，不会自动写入 memory/knowledge_base，也不会修改代码。
- 复盘规则请改 failure_rules.py，不要把规则散落在本文件里。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from failure_rules import review_result


def load_result_json(path: Path) -> dict[str, Any]:
    """读取一次求解结果 JSON。

    参数：
        path：result JSON 文件路径。

    返回：
        result 字典。
    """
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("result JSON 顶层必须是对象。")
    return data


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    返回：
        argparse.ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(
        description="读取 result JSON，输出失败复盘和沉淀建议。",
    )
    parser.add_argument(
        "result_path",
        help="result JSON 路径，例如 ..\\..\\result (24).json 或 C:\\path\\result.json",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="可选：把复盘 JSON 写入指定文件；不填则打印到终端。",
    )
    return parser


def main() -> None:
    """命令行入口：读取 result，生成复盘报告。"""
    parser = build_parser()
    args = parser.parse_args()
    result_path = Path(args.result_path).expanduser().resolve()
    result = load_result_json(result_path)
    review = review_result(result)
    review["source_file"] = str(result_path)
    output_text = json.dumps(review, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.write_text(output_text + "\n", encoding="utf-8")
        print(f"已写入失败复盘：{output_path}")
    else:
        sys.stdout.buffer.write((output_text + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
