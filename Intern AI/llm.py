"""intern-s1 / OpenAI 兼容客户端：chat 调用 + JSON 解析 + 流控 + thinking_mode。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY = os.environ.get("INTERN_S1_API_KEY")
BASE_URL = os.environ.get("INTERN_S1_BASE_URL", "https://chat.intern-ai.org.cn/api/v1/")
MODEL_NAME = os.environ.get("INTERN_S1_MODEL", "intern-s1")


def _parse_bool(v: str | None) -> bool | None:
    if v is None or v == "":
        return None
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        n = int(v)
        return n if n > 0 else None
    except ValueError:
        return None


THINKING_MODE = _parse_bool(os.environ.get("INTERN_S1_THINKING_MODE"))
RPM = _parse_int(os.environ.get("INTERN_S1_RPM"))

if not API_KEY:
    raise RuntimeError("环境变量 INTERN_S1_API_KEY 未设置，请参考 .env.example 配置")

_client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


class _RateLimiter:
    """线程安全的滑动窗口流控（per process）。"""

    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self.window: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.time()
            while self.window and self.window[0] < now - 60:
                self.window.popleft()
            if len(self.window) >= self.rpm:
                sleep_for = 60 - (now - self.window[0]) + 0.05
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.time()
                while self.window and self.window[0] < now - 60:
                    self.window.popleft()
            self.window.append(time.time())


_limiter = _RateLimiter(RPM) if RPM else None


def chat(
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    thinking_mode: bool | None = None,
) -> tuple[str, Any]:
    """单次 chat completion。thinking_mode=None 时走全局默认。"""
    if _limiter is not None:
        _limiter.acquire()

    effective_thinking = thinking_mode if thinking_mode is not None else THINKING_MODE
    extra_body = {"thinking_mode": effective_thinking} if effective_thinking is not None else None

    kwargs: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    resp = _client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or "", resp.usage


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """从模型输出中鲁棒地抽取 JSON：直接 parse -> 代码块 -> 首尾大括号截取。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = _JSON_BLOCK.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从输出中解析 JSON（前 300 字符）：{text[:300]}")


def ask_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_retries: int = 2,
    thinking_mode: bool | None = None,
) -> tuple[dict, Any]:
    """要求模型返回 JSON；解析失败时附加纠错指令重试。"""
    last_err: Exception | None = None
    current_user = user
    for attempt in range(max_retries + 1):
        try:
            content, usage = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": current_user},
                ],
                temperature=temperature,
                thinking_mode=thinking_mode,
            )
            return extract_json(content), usage
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            current_user = (
                user
                + "\n\n[纠错] 上次输出无法被解析为 JSON。请严格只输出纯 JSON 对象，"
                "不要包含 ```json 代码块标记、注释、解释文字或前后空白。"
            )
    assert last_err is not None
    raise last_err
