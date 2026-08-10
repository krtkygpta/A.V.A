"""
Daily token usage accounting for OpenAI-compatible chat completions.

Persists under Server/data/token_usage/YYYY-MM-DD.json:

{
  "date": "2026-08-08",
  "updated_at": "...",
  "total": {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N, "requests": N},
  "by_model": {
    "gpt-4o-mini": {
      "prompt_tokens": N,
      "completion_tokens": N,
      "total_tokens": N,
      "requests": N,
      "by_label": {"MainLLM": {...}, "Research": {...}}
    }
  }
}
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# Server/services/token_usage.py → Server/data/token_usage
_SERVER_ROOT = Path(__file__).resolve().parent.parent
_USAGE_DIR = _SERVER_ROOT / "data" / "token_usage"
_lock = threading.Lock()


def _empty_bucket() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
    }


def _path_for(day: str) -> Path:
    return _USAGE_DIR / f"{day}.json"


def _load_day(day: str) -> dict[str, Any]:
    path = _path_for(day)
    if not path.exists():
        return {
            "date": day,
            "updated_at": None,
            "total": _empty_bucket(),
            "by_model": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("date", day)
        data.setdefault("total", _empty_bucket())
        data.setdefault("by_model", {})
        return data
    except Exception:
        return {
            "date": day,
            "updated_at": None,
            "total": _empty_bucket(),
            "by_model": {},
        }


def _save_day(day: str, data: dict[str, Any]) -> None:
    _USAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(day)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _add_to_bucket(bucket: dict[str, Any], prompt: int, completion: int, total: int) -> None:
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens") or 0) + prompt
    bucket["completion_tokens"] = int(bucket.get("completion_tokens") or 0) + completion
    bucket["total_tokens"] = int(bucket.get("total_tokens") or 0) + total
    bucket["requests"] = int(bucket.get("requests") or 0) + 1


def extract_usage(response: Any) -> tuple[int, int, int]:
    """Pull prompt/completion/total tokens from an OpenAI-style response."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0, 0

    def _get(obj: Any, *names: str) -> int:
        for name in names:
            if isinstance(obj, dict):
                val = obj.get(name)
            else:
                val = getattr(obj, name, None)
            if val is not None:
                try:
                    return max(0, int(val))
                except (TypeError, ValueError):
                    continue
        return 0

    prompt = _get(usage, "prompt_tokens", "input_tokens", "prompt")
    completion = _get(usage, "completion_tokens", "output_tokens", "completion")
    total = _get(usage, "total_tokens", "total")
    if total <= 0:
        total = prompt + completion
    return prompt, completion, total


def record_usage(
    *,
    model: str,
    response: Any = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    label: str = "LLM",
    day: Optional[str] = None,
) -> dict[str, Any]:
    """
    Record one completion's token usage for the given model/day.

    Prefer passing the raw API response; explicit token ints override extraction.
    Thread-safe; failures never raise to callers (usage tracking is best-effort).
    """
    try:
        if prompt_tokens is None or completion_tokens is None or total_tokens is None:
            p, c, t = extract_usage(response)
            prompt_tokens = p if prompt_tokens is None else prompt_tokens
            completion_tokens = c if completion_tokens is None else completion_tokens
            total_tokens = t if total_tokens is None else total_tokens

        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens or 0))
        if total <= 0 and (prompt > 0 or completion > 0):
            total = prompt + completion
        if prompt == 0 and completion == 0 and total == 0:
            # Still count the request so we can see call volume.
            pass

        model_id = (model or "unknown").strip() or "unknown"
        label_id = (label or "LLM").strip() or "LLM"
        day_key = (day or date.today().isoformat()).strip()

        with _lock:
            data = _load_day(day_key)
            _add_to_bucket(data["total"], prompt, completion, total)

            by_model = data.setdefault("by_model", {})
            model_bucket = by_model.get(model_id)
            if not isinstance(model_bucket, dict):
                model_bucket = _empty_bucket()
                model_bucket["by_label"] = {}
                by_model[model_id] = model_bucket
            _add_to_bucket(model_bucket, prompt, completion, total)

            by_label = model_bucket.setdefault("by_label", {})
            label_bucket = by_label.get(label_id)
            if not isinstance(label_bucket, dict):
                label_bucket = _empty_bucket()
                by_label[label_id] = label_bucket
            _add_to_bucket(label_bucket, prompt, completion, total)

            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_day(day_key, data)
            return data
    except Exception as exc:
        print(f"[TokenUsage] record failed: {exc}")
        return {}


def get_usage(day: Optional[str] = None) -> dict[str, Any]:
    """Return usage snapshot for one day (default: today)."""
    day_key = (day or date.today().isoformat()).strip()
    with _lock:
        return _load_day(day_key)


def list_usage_days(limit: int = 30) -> list[str]:
    """List available usage day keys, newest first."""
    _USAGE_DIR.mkdir(parents=True, exist_ok=True)
    days = sorted(
        (p.stem for p in _USAGE_DIR.glob("*.json") if p.is_file()),
        reverse=True,
    )
    return days[: max(1, int(limit or 30))]


def get_usage_summary(
    day: Optional[str] = None,
    *,
    include_days: bool = True,
    days_limit: int = 14,
) -> dict[str, Any]:
    """API-friendly summary for one day + optional recent day list."""
    day_key = (day or date.today().isoformat()).strip()
    snapshot = get_usage(day_key)
    out: dict[str, Any] = {
        "status": "ok",
        "date": day_key,
        "total": snapshot.get("total") or _empty_bucket(),
        "by_model": snapshot.get("by_model") or {},
        "updated_at": snapshot.get("updated_at"),
    }
    if include_days:
        out["available_days"] = list_usage_days(limit=days_limit)
    return out
