"""
Round-robin API key pool for OpenAI-compatible main LLM calls.

Config (any of these; all merged, de-duped, order preserved):
  OPENAI_API_KEYS   — JSON list in settings.json, or comma/newline-separated env
  OPENAI_API_KEY_1 … OPENAI_API_KEY_8
  OPENAI_API_KEY    — single key (always included if set)

Every generate() call takes the next key. On 429 / rate / quota errors, the
same request retries with the remaining keys before failing.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Iterable, List, Optional, Sequence


def _split_keys_blob(blob: str) -> List[str]:
    if not blob or not str(blob).strip():
        return []
    s = str(blob).strip()
    # JSON array pasted as env string: ["sk-...", "sk-..."]
    if s.startswith("["):
        try:
            import json

            data = json.loads(s)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    parts = re.split(r"[\n,;]+", s)
    return [p.strip().strip('"').strip("'") for p in parts if p.strip()]


def collect_openai_keys(
    *,
    primary: Optional[str] = None,
    multi: Optional[str | Sequence[str]] = None,
    env: bool = True,
) -> List[str]:
    """Build ordered unique key list from args + environment."""
    found: List[str] = []

    def add(item: Optional[str]) -> None:
        if not item:
            return
        k = str(item).strip()
        if not k:
            return
        low = k.lower()
        if low in ("none", "null", "changeme", "paste-here"):
            return
        # Placeholder slots in settings.json
        if "paste_" in low or low.startswith("your_") or "key_here" in low:
            return
        if k not in found:
            found.append(k)

    if multi is not None:
        if isinstance(multi, str):
            for k in _split_keys_blob(multi):
                add(k)
        else:
            for k in multi:
                add(str(k) if k is not None else "")

    add(primary)

    if env:
        for k in _split_keys_blob(os.getenv("OPENAI_API_KEYS") or ""):
            add(k)
        for i in range(1, 9):
            add(os.getenv(f"OPENAI_API_KEY_{i}"))
        add(os.getenv("OPENAI_API_KEY"))

    return found


def normalize_openai_base_url(url: Optional[str]) -> Optional[str]:
    """
    OpenAI SDK appends /chat/completions itself.
    Strip that suffix if settings still have the full path.
    """
    if not url:
        return None
    u = str(url).strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u or None


def is_rate_limit_error(exc: BaseException) -> bool:
    """True if we should try another API key."""
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    try:
        if status is not None and int(status) in (429, 402, 503):
            return True
    except (TypeError, ValueError):
        pass

    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True

    msg = str(exc).lower()
    needles = (
        "rate limit",
        "rate_limit",
        "too many requests",
        "quota",
        "429",
        "resource exhausted",
        "capacity",
        "overloaded",
        "tokens per",
        "requests per",
    )
    return any(n in msg for n in needles)


class KeyRotator:
    """Thread-safe round-robin over a list of API keys."""

    def __init__(self, keys: Iterable[str]):
        self._keys = [k.strip() for k in keys if k and str(k).strip()]
        self._lock = threading.Lock()
        self._index = 0
        if not self._keys:
            raise ValueError("KeyRotator requires at least one API key")

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def next_key(self) -> str:
        with self._lock:
            key = self._keys[self._index % len(self._keys)]
            self._index = (self._index + 1) % len(self._keys)
            return key

    def peek_order(self) -> List[str]:
        """Keys starting from current index (for retry walk)."""
        with self._lock:
            n = len(self._keys)
            start = self._index % n
            # advance for next independent call after this batch starts
            ordered = [self._keys[(start + i) % n] for i in range(n)]
            self._index = (start + 1) % n
            return ordered

    def mask(self, key: str) -> str:
        k = key or ""
        if len(k) <= 8:
            return "***"
        return f"...{k[-6:]}"


_global_rotator: Optional[KeyRotator] = None
_global_lock = threading.Lock()
_client_cache: dict[tuple[str, str], Any] = {}
_client_lock = threading.Lock()


def get_openai_key_rotator(force_reload: bool = False) -> Optional[KeyRotator]:
    """Singleton built from env / settings-loaded env."""
    global _global_rotator
    with _global_lock:
        if _global_rotator is not None and not force_reload:
            return _global_rotator
        keys = collect_openai_keys()
        if not keys:
            _global_rotator = None
            return None
        _global_rotator = KeyRotator(keys)
        print(f"[KeyRotator] OpenAI-compatible pool size={len(keys)}")
        return _global_rotator


def get_openai_client_for_key(
    api_key: str,
    base_url: Optional[str] = None,
):
    """Cached OpenAI client for one key + base_url."""
    from openai import OpenAI

    base = normalize_openai_base_url(base_url if base_url is not None else os.getenv("OPENAI_BASE_URL"))
    cache_key = (api_key, base or "")
    with _client_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            import httpx
            timeout_sec = float(os.getenv("AVA_LLM_TIMEOUT", "90.0"))
            kwargs: dict = {
                "api_key": api_key,
                "timeout": httpx.Timeout(timeout_sec, connect=10.0),
                "max_retries": 1,
            }
            if base:
                kwargs["base_url"] = base
            client = OpenAI(**kwargs)
            _client_cache[cache_key] = client
        return client


def chat_completions_create(
    *,
    model: str,
    messages: list,
    base_url: Optional[str] = None,
    api_keys: Optional[Sequence[str]] = None,
    label: str = "LLM",
    **kwargs: Any,
):
    """
    Shared chat.completions.create with round-robin keys + 429 retry.

    Used by main LLMService, research agent, memory dreamer — one pool.
    """
    rotator: Optional[KeyRotator] = None
    if api_keys:
        cleaned = collect_openai_keys(multi=api_keys, env=False)
        if cleaned:
            rotator = KeyRotator(cleaned)
    if rotator is None:
        rotator = get_openai_key_rotator()
    if rotator is None:
        # Last-ditch single env key without rotator singleton
        keys = collect_openai_keys(env=True)
        if not keys:
            raise RuntimeError(
                f"[{label}] No OPENAI_API_KEY(S) configured for OpenAI-compatible calls"
            )
        rotator = KeyRotator(keys)

    key_order = rotator.peek_order()
    last_err: Optional[BaseException] = None
    base = normalize_openai_base_url(
        base_url if base_url is not None else os.getenv("OPENAI_BASE_URL")
    )

    for i, key in enumerate(key_order):
        client = get_openai_client_for_key(key, base)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs,
            )
            if i > 0:
                print(
                    f"[{label}] ok with rotated key {rotator.mask(key)} "
                    f"after {i} fail(s)"
                )
            # Best-effort daily per-model token accounting (main / research / dreamer).
            try:
                from services.token_usage import record_usage

                record_usage(model=model, response=resp, label=label)
            except Exception as usage_err:
                print(f"[{label}] token usage record skipped: {usage_err}")
            return resp
        except Exception as e:
            last_err = e
            # Auto-recovery for models (like o1, o3, gpt-5) that reject temperature or presence_penalty
            if ("400" in str(e) or "BadRequest" in type(e).__name__) and any(k in kwargs for k in ("temperature", "presence_penalty")):
                try:
                    stripped = {k: v for k, v in kwargs.items() if k not in ("temperature", "presence_penalty")}
                    resp = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        **stripped,
                    )
                    return resp
                except Exception as e_stripped:
                    last_err = e_stripped

            if is_rate_limit_error(e) and i < len(key_order) - 1:
                print(
                    f"[{label}] rate/quota on {rotator.mask(key)}; "
                    f"next key ({i + 1}/{len(key_order)})"
                )
                continue
            raise

    assert last_err is not None
    raise last_err


def chat_completions_stream(
    *,
    model: str,
    messages: list,
    base_url: Optional[str] = None,
    api_keys: Optional[Sequence[str]] = None,
    label: str = "LLM-Stream",
    **kwargs: Any,
):
    """
    Shared chat.completions.create(stream=True) generator with key rotation.

    Yields events:
      {"type": "content", "delta": token_str}
      {"type": "finish", "content": full_text, "tool_calls": list_of_calls, "role": "assistant"}
    """
    rotator: Optional[KeyRotator] = None
    if api_keys:
        cleaned = collect_openai_keys(multi=api_keys, env=False)
        if cleaned:
            rotator = KeyRotator(cleaned)
    if rotator is None:
        rotator = get_openai_key_rotator()
    if rotator is None:
        keys = collect_openai_keys(env=True)
        if not keys:
            raise RuntimeError(
                f"[{label}] No OPENAI_API_KEY(S) configured for OpenAI-compatible calls"
            )
        rotator = KeyRotator(keys)

    key_order = rotator.peek_order()
    base = normalize_openai_base_url(
        base_url if base_url is not None else os.getenv("OPENAI_BASE_URL")
    )

    for i, key in enumerate(key_order):
        client = get_openai_client_for_key(key, base)
        try:
            try:
                stream_resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    **kwargs,
                )
            except Exception as stream_create_err:
                if ("400" in str(stream_create_err) or "BadRequest" in type(stream_create_err).__name__) and any(k in kwargs for k in ("temperature", "presence_penalty")):
                    stripped = {k: v for k, v in kwargs.items() if k not in ("temperature", "presence_penalty")}
                    stream_resp = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        stream=True,
                        **stripped,
                    )
                else:
                    raise stream_create_err

            accumulated_content = []
            accumulated_tool_calls: dict[int, dict[str, Any]] = {}

            for chunk in stream_resp:
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if not delta:
                    continue

                # Handle text delta
                text_delta = getattr(delta, "content", None)
                if text_delta:
                    accumulated_content.append(text_delta)
                    yield {"type": "content", "delta": text_delta}

                # Handle tool calls delta
                tc_deltas = getattr(delta, "tool_calls", None)
                if tc_deltas:
                    for tc in tc_deltas:
                        idx = getattr(tc, "index", 0)
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": getattr(tc, "id", "") or "",
                                "name": getattr(getattr(tc, "function", None), "name", "") or "",
                                "arguments": [],
                            }
                        else:
                            tc_id = getattr(tc, "id", None)
                            if tc_id:
                                accumulated_tool_calls[idx]["id"] = tc_id
                            fn = getattr(tc, "function", None)
                            if fn and getattr(fn, "name", None):
                                accumulated_tool_calls[idx]["name"] = fn.name

                        fn = getattr(tc, "function", None)
                        if fn and getattr(fn, "arguments", None):
                            accumulated_tool_calls[idx]["arguments"].append(fn.arguments)

            full_text = "".join(accumulated_content) if accumulated_content else None
            tool_calls_list = []
            for idx in sorted(accumulated_tool_calls.keys()):
                info = accumulated_tool_calls[idx]
                tool_calls_list.append({
                    "id": info.get("id") or f"call_{idx}",
                    "type": "function",
                    "function": {
                        "name": info.get("name") or "",
                        "arguments": "".join(info.get("arguments", [])),
                    },
                })

            if i > 0:
                print(
                    f"[{label}] stream ok with rotated key {rotator.mask(key)} "
                    f"after {i} fail(s)"
                )

            yield {
                "type": "finish",
                "role": "assistant",
                "content": full_text,
                "tool_calls": tool_calls_list,
            }
            return

        except Exception as e:
            if is_rate_limit_error(e) and i < len(key_order) - 1:
                print(
                    f"[{label}] rate/quota on {rotator.mask(key)}; "
                    f"next key ({i + 1}/{len(key_order)})"
                )
                continue
            raise
