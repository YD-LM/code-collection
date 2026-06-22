"""轻量 RAG：从本地知识库检索与题目相关的解题方法。

这个版本先用关键词检索，优点是稳定、透明、便于维护。
之后如果题库和方法库变大，可以把 retrieve_knowledge 换成向量检索，调用方不用改。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge_base"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class RetrievedChunk:
    """一次检索命中的知识片段。

    参数：
        source：知识片段来源文件名。
        score：关键词命中分数。
        text：可直接拼进 prompt 的知识文本。
    """

    source: str
    score: int
    text: str


def _tokenize(text: str) -> set[str]:
    """把题干或知识片段切成粗粒度关键词。

    参数：
        text：待切分文本。

    返回：
        去重后的关键词集合。
    """
    return {token.lower() for token in _TOKEN_RE.findall(text) if len(token.strip()) >= 2}


def _split_markdown_sections(text: str) -> list[str]:
    """按 Markdown 标题切分知识库，避免整篇文档一次塞进 prompt。

    参数：
        text：Markdown 文件内容。

    返回：
        可独立检索的知识片段列表。
    """
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def retrieve_knowledge(problem: str, *, top_k: int = 3) -> list[dict]:
    """从 knowledge_base 检索与题目相关的方法片段。

    参数：
        problem：原始题干文本。
        top_k：最多返回的片段数量。

    返回：
        按相关性排序的片段字典列表，字段为 source、score、text。
    """
    query_tokens = _tokenize(problem)
    if not query_tokens or not _KNOWLEDGE_DIR.exists():
        return []

    hits: list[RetrievedChunk] = []
    for path in sorted(_KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for section in _split_markdown_sections(text):
            section_tokens = _tokenize(section)
            score = len(query_tokens & section_tokens)
            if score > 0:
                hits.append(RetrievedChunk(path.name, score, section))

    hits.sort(key=lambda item: item.score, reverse=True)
    return [hit.__dict__ for hit in hits[:top_k]]


def format_retrieved_knowledge(chunks: list[dict]) -> str:
    """把检索结果格式化为模型可读的 prompt 段落。

    参数：
        chunks：retrieve_knowledge 返回的片段列表。

    返回：
        可拼接到 user prompt 的文本；没有命中时返回空字符串。
    """
    if not chunks:
        return ""

    lines = ["[检索到的相关方法]", "以下内容是辅助方法，不是最终答案；必须结合原题重新推导。"]
    for index, chunk in enumerate(chunks, 1):
        lines.append(f"{index}. 来源：{chunk['source']}，相关度：{chunk['score']}")
        lines.append(str(chunk["text"]))
    return "\n".join(lines)
