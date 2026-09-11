"""Minimal YAML frontmatter parser for SKILL.md / agent markdown.

Handles the subset Claude Code and Agent Skills actually use:
scalars, booleans, numbers, >, |, inline [lists], and dash lists.
Does not require PyYAML.
"""

from __future__ import annotations

from typing import Any, Optional


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    raw = text or ""
    if not raw.lstrip().startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    # Allow a BOM / leading blank
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != "---":
        return {}, raw
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, raw
    meta = _parse_yaml_block("\n".join(lines[start + 1 : end]))
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "yes", "on", "1"):
        return True
    if s in ("false", "no", "off", "0"):
        return False
    return default


def as_str_list(value: Any) -> list[str]:
    if value is None or value is False:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).replace(",", " ")
    return [p.strip() for p in text.split() if p.strip()]


def _parse_yaml_block(src: str) -> dict[str, Any]:
    items = _tokenize_lines(src)
    data, _ = _parse_map(items, 0, 0)
    return data


def _tokenize_lines(src: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in (src or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        out.append((indent, raw.strip()))
    return out


def _parse_map(
    items: list[tuple[int, str]], idx: int, indent: int
) -> tuple[dict[str, Any], int]:
    data: dict[str, Any] = {}
    n = len(items)
    while idx < n:
        ind, line = items[idx]
        if ind < indent:
            break
        if ind > indent:
            # orphan indent — skip
            idx += 1
            continue
        if line.startswith("- "):
            break
        if ":" not in line:
            idx += 1
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        idx += 1
        if rest in (">", ">-", "|", "|-"):
            folded, idx = _parse_folded(items, idx, indent + 1, literal=rest.startswith("|"))
            data[key] = folded
        elif rest == "":
            if idx < n and items[idx][0] > indent:
                next_ind, next_line = items[idx]
                if next_line.startswith("- "):
                    lst, idx = _parse_list(items, idx, next_ind)
                    data[key] = lst
                else:
                    nested, idx = _parse_map(items, idx, next_ind)
                    data[key] = nested
            else:
                data[key] = ""
        else:
            data[key] = _parse_scalar(rest)
    return data, idx


def _parse_list(
    items: list[tuple[int, str]], idx: int, indent: int
) -> tuple[list[Any], int]:
    out: list[Any] = []
    n = len(items)
    while idx < n:
        ind, line = items[idx]
        if ind < indent:
            break
        if not line.startswith("- "):
            break
        rest = line[2:].strip()
        idx += 1
        if rest == "":
            if idx < n and items[idx][0] > indent:
                nested, idx = _parse_map(items, idx, items[idx][0])
                out.append(nested)
            else:
                out.append("")
        elif ":" in rest and not rest.startswith("{") and not _is_quoted(rest):
            # inline map start: "- name: foo"
            key, val = rest.split(":", 1)
            node = {key.strip(): _parse_scalar(val.strip())}
            if idx < n and items[idx][0] > indent:
                extra, idx = _parse_map(items, idx, items[idx][0])
                node.update(extra)
            out.append(node)
        else:
            out.append(_parse_scalar(rest))
    return out, idx


def _parse_folded(
    items: list[tuple[int, str]], idx: int, min_indent: int, *, literal: bool
) -> tuple[str, int]:
    chunks: list[str] = []
    n = len(items)
    while idx < n and items[idx][0] >= min_indent:
        chunks.append(items[idx][1])
        idx += 1
    text = "\n".join(chunks) if literal else " ".join(chunks)
    return text, idx


def _is_quoted(s: str) -> bool:
    return len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]


def _parse_scalar(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return ""
    if s in ("null", "Null", "~"):
        return None
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if _is_quoted(s):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        parts = []
        buf = ""
        in_q = False
        qch = ""
        for ch in inner:
            if in_q:
                if ch == qch:
                    in_q = False
                else:
                    buf += ch
                continue
            if ch in "\"'":
                in_q = True
                qch = ch
                continue
            if ch == ",":
                parts.append(_parse_scalar(buf.strip()))
                buf = ""
            else:
                buf += ch
        if buf.strip():
            parts.append(_parse_scalar(buf.strip()))
        return parts
    try:
        if s.startswith("0") and s not in ("0", "0.0") and "." not in s:
            return s
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s
