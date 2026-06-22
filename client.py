"""Command line runner for the math agent.

python client.py --file test.jsonl
python client.py --file test.jsonl --out result.jsonl --out-dir outputs --mode fast
                                                                        规定模式
Input JSONL rows should contain at least:
    {"idx": 0, "problem": "problem text"}

For batch runs, each problem is written to one JSON file under --out-dir:
    outputs/0.json
    outputs/1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from user_agent import solve_problem

DEFAULT_TEXT_DELIMITER = "==="
PROBLEM_FIELDS = ("problem", "\u95ee\u9898", "question", "Question", "prompt", "\u9898\u76ee")


def load_problems_from_txt(file_path: Path, delimiter: str) -> list[dict[str, Any]]:
    text = file_path.read_text(encoding="utf-8")
    parts: list[str] = []
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == delimiter:
            question = "\n".join(current_lines).strip()
            if question:
                parts.append(question)
            current_lines = []
        else:
            current_lines.append(line)
    last_question = "\n".join(current_lines).strip()
    if last_question:
        parts.append(last_question)
    return [{"idx": i - 1, "problem": question} for i, question in enumerate(parts, 1)]


def load_problems_from_json_or_jsonl(file_path: Path) -> list[dict[str, Any]]:
    content = file_path.read_text(encoding="utf-8-sig").strip()
    if not content:
        return []
    if content.startswith("["):
        return json.loads(content)

    problems: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8-sig") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"Warning: skip line {line_number}, invalid JSON: {exc}", file=sys.stderr)
                continue
            if not isinstance(item, dict):
                print(f"Warning: skip line {line_number}, JSON row is not an object", file=sys.stderr)
                continue
            problems.append(item)
    return problems


def _extract_problem_text(item: dict[str, Any]) -> str:
    for field in PROBLEM_FIELDS:
        value = item.get(field)
        if value is not None:
            return str(value)
    return ""


def normalize_problems(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for seq, item in enumerate(items):
        problem = _extract_problem_text(item).strip()
        idx = item.get("idx", item.get("id", seq))
        normalized.append({"idx": idx, "problem": problem})
    return normalized


def _safe_output_name(idx: Any) -> str:
    text = str(idx)
    forbidden = '<>:"/\\|?*'
    safe = "".join("_" if ch in forbidden else ch for ch in text).strip()
    return safe or "unknown"


def _output_file_for(out_dir: Path, idx: Any) -> Path:
    return out_dir / f"{_safe_output_name(idx)}.json"


def _is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _stringify_trace_content(res: dict[str, Any]) -> str:
    solution = res.get("solution")
    if isinstance(solution, dict):
        final_answer = solution.get("final_answer")
        steps = solution.get("steps") or []
        sub_answers = solution.get("sub_answers") or []
        pieces: list[str] = []
        if final_answer:
            pieces.append(str(final_answer))
        for step in steps:
            if isinstance(step, dict):
                operation = step.get("operation") or step.get("goal") or ""
                result = step.get("result") or ""
                text = ": ".join(str(part) for part in (operation, result) if part)
                if text:
                    pieces.append(text)
        for sub_answer in sub_answers:
            if isinstance(sub_answer, dict):
                sub_idx = sub_answer.get("sub_idx") or ""
                answer = sub_answer.get("answer") or ""
                derivation = sub_answer.get("proof_or_derivation") or ""
                text = " ".join(str(part) for part in (sub_idx, answer, derivation) if part)
                if text:
                    pieces.append(text)
        if pieces:
            return "\n".join(pieces)
        return json.dumps(solution, ensure_ascii=False)
    if solution is not None:
        return str(solution)
    return str(res.get("final_answer") or "")


def _build_success_record(idx: Any, res: dict[str, Any]) -> dict[str, Any]:
    final_response = res.get("final_answer") or ""
    return {
        "idx": idx,
        "status": "success",
        "final_response": str(final_response),
        "trace": [
            {
                "step": "solve",
                "content": _stringify_trace_content(res),
            }
        ],
    }


def _build_error_record(idx: Any, exc_type: str, message: str) -> dict[str, Any]:
    return {
        "idx": idx,
        "status": "error",
        "final_response": "",
        "error": {
            "type": exc_type,
            "message": message,
        },
        "trace": [],
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Math agent batch runner")
    parser.add_argument("--problem", help="Single problem text")
    parser.add_argument("--id", default=None, help="ID for single-problem mode")
    parser.add_argument("--file", help="Problem file path, supports JSON, JSONL, or TXT")
    parser.add_argument("--delimiter", default=DEFAULT_TEXT_DELIMITER, help="Problem delimiter for TXT input")
    parser.add_argument("--out", default="result.jsonl", help="Aggregate JSONL output path")
    parser.add_argument("--out-dir", default="outputs", help="Directory for per-problem JSON files")
    parser.add_argument("--dataset-out", default=None, help="Write normalized JSONL dataset and exit")
    parser.add_argument("--submission", default=None, help="Optional submission JSONL path with {idx, final_answer}")
    parser.add_argument("--no-verifier", action="store_true", help="Disable verifier agent")
    parser.add_argument("--max-revisions", type=int, default=1, help="Maximum repair rounds after failed verification")
    parser.add_argument(
        "--mode",
        choices=["fast", "contest", "strict", "deep"],
        default="contest",
        help="Run mode. Use strict or deep to get behavior closer to the app quality settings.",
    )
    args = parser.parse_args()

    if args.problem and args.file:
        parser.error("--problem and --file cannot be used together")

    solve_kwargs = {
        "enable_verifier": not args.no_verifier,
        "max_revisions": args.max_revisions,
        "mode": args.mode,
    }

    if args.problem:
        result = solve_problem(args.problem, problem_id=args.id, **solve_kwargs)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if not args.file:
        parser.error("Please provide --problem or --file")

    file_path = Path(args.file)
    if not file_path.exists():
        parser.error(f"File does not exist: {args.file}")

    if file_path.suffix.lower() == ".txt":
        problems = load_problems_from_txt(file_path, args.delimiter)
    else:
        try:
            problems = normalize_problems(load_problems_from_json_or_jsonl(file_path))
        except json.JSONDecodeError as exc:
            parser.error(f"Invalid JSON / JSONL file {args.file}: {exc}")

    if not problems:
        parser.error("No valid problems found in input file")

    if args.dataset_out:
        dataset_path = Path(args.dataset_out)
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        with dataset_path.open("w", encoding="utf-8") as fout:
            for item in problems:
                fout.write(json.dumps({"idx": item["idx"], "problem": item["problem"]}, ensure_ascii=False) + "\n")
        print(f"Wrote normalized dataset: {dataset_path}")
        return

    out_path = Path(args.out)
    out_dir_path = Path(args.out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sub_path = Path(args.submission) if args.submission else None
    if sub_path:
        sub_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(problems)
    with out_path.open("a", encoding="utf-8") as flog:
        fsub = sub_path.open("a", encoding="utf-8") if sub_path else None
        try:
            for seq, item in enumerate(problems, 1):
                pid = item.get("idx")
                problem_text = str(item.get("problem") or "").strip()
                output_file = _output_file_for(out_dir_path, pid)

                if _is_nonempty_file(output_file):
                    print(f"[{seq}/{total}] {pid} -> skipped")
                    continue

                try:
                    if not problem_text:
                        raise ValueError("problem is empty")
                    res = solve_problem(problem_text, problem_id=str(pid), **solve_kwargs)
                    if not isinstance(res, dict):
                        raise RuntimeError("solve_problem returned a non-dict result")
                    if "error_stage" in res or res.get("error") is not None:
                        error_message = str(res.get("error") or res.get("error_stage") or "solve failed")
                        record = _build_error_record(pid, "RuntimeError", error_message)
                    else:
                        record = _build_success_record(pid, res)
                except Exception as exc:
                    record = _build_error_record(pid, type(exc).__name__, str(exc))

                _write_json(output_file, record)
                flog.write(json.dumps(record, ensure_ascii=False) + "\n")
                flog.flush()

                if fsub and record["status"] == "success":
                    fsub.write(
                        json.dumps(
                            {"idx": pid, "final_answer": record.get("final_response", "")},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    fsub.flush()

                print(f"[{seq}/{total}] {pid} -> {record['status']}")
        finally:
            if fsub:
                fsub.close()


if __name__ == "__main__":
    main()
