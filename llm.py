"""Intern-S1 / OpenAI-compatible client: chat calls, JSON extraction, retries."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

API_KEY = os.environ.get("INTERN_S1_API_KEY")
BASE_URL = os.environ.get("BASE_URL") or os.environ.get("INTERN_S1_BASE_URL", "https://chat.intern-ai.org.cn/api/v1/")
MODEL_NAME = os.environ.get("MODEL_NAME") or os.environ.get("INTERN_S1_MODEL", "intern-s1")


def _parse_bool(v: str | None) -> bool | None:
    """解析 .env 中的布尔配置。

    参数：
        v：环境变量字符串。

    返回：
        布尔值；未配置时返回 None。
    """
    if v is None or v == "":
        return None
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(v: str | None) -> int | None:
    """解析 .env 中的正整数配置。

    参数：
        v：环境变量字符串。

    返回：
        正整数；未配置或非法时返回 None。
    """
    if v is None or v == "":
        return None
    try:
        n = int(v)
        return n if n > 0 else None
    except ValueError:
        return None


THINKING_MODE = _parse_bool(os.environ.get("THINKING_MODE") or os.environ.get("INTERN_S1_THINKING_MODE"))
if THINKING_MODE is None:
    THINKING_MODE = True
RPM = _parse_int(os.environ.get("RPM") or os.environ.get("INTERN_S1_RPM"))
REQUEST_TIMEOUT = _parse_int(os.environ.get("TIMEOUT") or os.environ.get("INTERN_S1_TIMEOUT")) or 120

if not API_KEY:
    raise RuntimeError("Environment variable INTERN_S1_API_KEY is not set. Please configure .env first.")

_client: Any | None = None


class JsonExtractionError(ValueError):
    """JSON 抽取失败时保留原始输出片段，便于定位模型跑偏。"""

    def __init__(self, text: str, detail: str | None = None) -> None:
        self.text = text
        self.preview = text[:1200]
        message = detail or "无法从模型输出中解析 JSON"
        super().__init__(f"{message}（前 1200 字符）：{self.preview}")


class _RateLimiter:
    """进程内滑动窗口限速器，避免超过接口 RPM。"""

    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self.window: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        """必要时等待，直到当前请求可以发送。"""
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


def _get_client() -> Any:
    """延迟初始化 OpenAI 兼容客户端。

    返回：
        OpenAI 客户端对象。
    """
    global _client
    if _client is None:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("Python package 'openai' is required to call Intern-S1.") from exc
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client


def chat(
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    thinking_mode: bool | None = None,
) -> tuple[str, Any]:
    """执行一次聊天补全请求。

    参数：
        messages：OpenAI chat messages。
        temperature：采样温度。
        max_tokens：最大输出 token。
        thinking_mode：单次请求的思考模式；None 表示使用全局默认值。

    返回：
        二元组，第一项为输出文本，第二项为 usage。
    """
    if _limiter is not None:
        _limiter.acquire()

    effective_thinking = THINKING_MODE if thinking_mode is None else thinking_mode
    extra_body = {"thinking_mode": effective_thinking} if effective_thinking is not None else None

    kwargs: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": REQUEST_TIMEOUT,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    resp = _get_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or "", resp.usage


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_DECODER = json.JSONDecoder()


def _loads_json_object(candidate: str) -> dict | None:
    """尝试把字符串解析为 JSON 对象。

    参数：
        candidate：候选 JSON 字符串。

    返回：
        JSON 对象；如果是单元素对象数组，也返回该对象；失败时返回 None。
    """
    try:
        value = json.loads(candidate.strip())
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return None


def _scan_json_object(text: str) -> dict | None:
    """从混杂输出中扫描第一个可解析 JSON 对象。

    参数：
        text：模型原始输出。

    返回：
        JSON 对象；未找到时返回 None。
    """
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _ = _JSON_DECODER.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return value[0]
    return None


def extract_json(text: str) -> dict:
    """尽量从模型输出中抽取一个 JSON 对象。

    参数：
        text：模型原始输出。

    返回：
        JSON 对象。
    """
    text = text.strip()

    parsed = _loads_json_object(text)
    if parsed is not None:
        return parsed

    for block in _JSON_BLOCK.finditer(text):
        parsed = _loads_json_object(block.group(1))
        if parsed is not None:
            return parsed

    parsed = _scan_json_object(text)
    if parsed is not None:
        return parsed

    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        parsed = _loads_json_object(text[start : end + 1])
        if parsed is not None:
            return parsed

    raise JsonExtractionError(text)


def _repair_json_output(content: str) -> tuple[dict, Any]:
    """调用模型把不合法输出修复成单个 JSON 对象。

    参数：
        content：上一轮模型原始输出。

    返回：
        二元组，第一项为修复后的 JSON 对象，第二项为 usage。
    """
    repair_content, repair_usage = chat(
        [
            {
                "role": "system",
                "content": (
                    "你负责把不合法的模型输出修复成一个合法 JSON 对象。"
                    "保留数学内容和原有字段名，只输出 JSON，不要输出 markdown 或解释。"
                ),
            },
            {
                "role": "user",
                "content": "请把下面内容修复成单个合法 JSON 对象：\n" + content[:12000],
            },
        ],
        temperature=0.0,
        max_tokens=4096,
        thinking_mode=False,
    )
    return extract_json(repair_content), repair_usage


def ask_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_retries: int = 2,
    thinking_mode: bool | None = None,
) -> tuple[dict, Any]:
    """请求 JSON；先解析，再修复畸形输出，最后带反馈重试。

    参数：
        system：system prompt。
        user：user prompt。
        temperature：采样温度。
        max_retries：解析失败后的重试次数。
        thinking_mode：单次请求的思考模式；None 表示使用全局默认值。

    返回：
        二元组，第一项为 JSON 对象，第二项为 usage。
    """
    last_err: Exception | None = None
    current_user = user
    effective_thinking = THINKING_MODE if thinking_mode is None else thinking_mode

    for attempt in range(max_retries + 1):
        content = ""
        try:
            content, usage = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": current_user},
                ],
                temperature=temperature,
                thinking_mode=effective_thinking,
            )
            return extract_json(content), usage
        except (JsonExtractionError, ValueError, json.JSONDecodeError) as exc:
            last_err = exc
            if content:
                try:
                    return _repair_json_output(content)
                except (JsonExtractionError, ValueError, json.JSONDecodeError) as repair_err:
                    last_err = repair_err
            if attempt >= max_retries:
                break
            current_user = (
                user
                + "\n\n[纠错] 上次输出无法解析为 JSON。请严格只输出一个合法 JSON 对象，"
                + "不要包含 markdown、注释、解释文字或前后空白。"
                + "\n\n[上次原始输出]\n"
                + content[:6000]
            )

    assert last_err is not None
    raise last_err
