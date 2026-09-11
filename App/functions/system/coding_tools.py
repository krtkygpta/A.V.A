"""
coding_tools.py - Precision coding tools for AVA and delegate coding workers.

Provides 5 focused tools inspired by minimal coding harnesses (like Pi):
1. read_file: inspect file contents with line numbering, line slicing, and pagination limits.
2. edit_file: surgical search-and-replace of unique contiguous text chunks.
3. write_file: create or overwrite whole files cleanly.
4. search_code: fast grep across codebase/directory with line numbers.
5. list_dir: directory tree listing with file sizes and depth limit.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from utils.paths import resolve_file_path, resolve_location
except ImportError:
    try:
        from App.utils.paths import resolve_file_path, resolve_location
    except ImportError:
        def resolve_file_path(filename: str, location: Optional[str] = None) -> Path:
            p = Path(filename).expanduser()
            if p.is_absolute():
                return p
            loc = Path(location).expanduser() if location else Path.cwd()
            return (loc / p).resolve()

        def resolve_location(location: Optional[str] = None, create: bool = False) -> Path:
            p = Path(location).expanduser() if location else Path.cwd()
            if create:
                p.mkdir(parents=True, exist_ok=True)
            return p.resolve()


def _normalize_target_path(path: str, location: Optional[str] = None) -> Path:
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("File path cannot be empty.")
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    if location and str(location).strip():
        loc = Path(str(location).strip()).expanduser()
        return (loc / p).resolve()
    # Default to current working directory
    return (Path.cwd() / p).resolve()


def read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    max_lines: int = 400,
    location: Optional[str] = None,
) -> str:
    """
    Read the contents of a file with line numbers (1-indexed).
    Supports partial slicing with start_line and end_line.
    """
    try:
        full_path = _normalize_target_path(path, location)
        if not full_path.exists():
            return json.dumps({
                "status": "error",
                "error": f"File does not exist: {full_path}"
            })
        if full_path.is_dir():
            return json.dumps({
                "status": "error",
                "error": f"Path is a directory, not a file: {full_path}. Use list_dir instead."
            })

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = full_path.read_text(encoding="latin-1")
            except Exception as e:
                return json.dumps({
                    "status": "error",
                    "error": f"Unable to decode file text: {e}"
                })

        lines = content.splitlines()
        total_lines = len(lines)

        s_line = max(1, int(start_line)) if start_line is not None else 1
        e_line = min(total_lines, int(end_line)) if end_line is not None else total_lines

        if s_line > total_lines:
            return json.dumps({
                "status": "error",
                "error": f"start_line ({s_line}) exceeds total lines in file ({total_lines})."
            })
        if s_line > e_line:
            return json.dumps({
                "status": "error",
                "error": f"start_line ({s_line}) cannot be greater than end_line ({e_line})."
            })

        slice_lines = lines[s_line - 1 : e_line]
        capped = False
        if len(slice_lines) > max_lines:
            slice_lines = slice_lines[:max_lines]
            capped = True

        formatted: List[str] = []
        for idx, line in enumerate(slice_lines, start=s_line):
            formatted.append(f"{idx}: {line}")

        result_text = "\n".join(formatted)
        if capped:
            result_text += f"\n... [Truncated: showing {len(slice_lines)} of {total_lines} lines. Specify start_line/end_line to view remaining content]"

        return json.dumps({
            "status": "success",
            "path": str(full_path),
            "start_line": s_line,
            "end_line": s_line + len(slice_lines) - 1,
            "total_lines": total_lines,
            "content": result_text,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def edit_file(
    path: str,
    target_content: str,
    replacement_content: str,
    allow_multiple: bool = False,
    location: Optional[str] = None,
) -> str:
    """
    Surgically replace a block of text in an existing file.
    Validates uniqueness to prevent unintended duplicate edits.
    """
    try:
        full_path = _normalize_target_path(path, location)
        if not full_path.exists():
            return json.dumps({
                "status": "error",
                "error": f"File not found: {full_path}"
            })

        text = full_path.read_text(encoding="utf-8")
        target = str(target_content or "")
        replacement = str(replacement_content if replacement_content is not None else "")

        if not target:
            return json.dumps({
                "status": "error",
                "error": "target_content cannot be empty."
            })

        # Normalize carriage returns for cross-platform matching
        normalized_text = text.replace("\r\n", "\n")
        normalized_target = target.replace("\r\n", "\n")
        normalized_replacement = replacement.replace("\r\n", "\n")

        count = normalized_text.count(normalized_target)
        if count == 0:
            # Check if whitespace differences might be the cause
            return json.dumps({
                "status": "error",
                "error": "target_content not found in file. Make sure exact indentation and lines match."
            })
        if count > 1 and not allow_multiple:
            return json.dumps({
                "status": "error",
                "error": f"target_content appears {count} times in the file. Provide more surrounding lines for unique matching, or set allow_multiple=true."
            })

        if allow_multiple:
            new_text = normalized_text.replace(normalized_target, normalized_replacement)
        else:
            new_text = normalized_text.replace(normalized_target, normalized_replacement, 1)

        full_path.write_text(new_text, encoding="utf-8")
        return json.dumps({
            "status": "success",
            "path": str(full_path),
            "occurrences_replaced": count if allow_multiple else 1,
            "message": f"Successfully updated {full_path}"
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def write_file(
    path: str,
    content: str,
    overwrite: bool = True,
    location: Optional[str] = None,
) -> str:
    """
    Create or overwrite a file with given content.
    Automatically creates parent directories.
    """
    try:
        full_path = _normalize_target_path(path, location)
        if full_path.exists() and not overwrite:
            return json.dumps({
                "status": "error",
                "error": f"File already exists: {full_path}. Set overwrite=true to replace."
            })

        full_path.parent.mkdir(parents=True, exist_ok=True)
        text = str(content if content is not None else "")
        full_path.write_text(text, encoding="utf-8")
        lines = len(text.splitlines())
        return json.dumps({
            "status": "success",
            "path": str(full_path),
            "lines_written": lines,
            "bytes": len(text.encode("utf-8")),
            "message": f"File written successfully: {full_path}"
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def search_code(
    query: str,
    path: Optional[str] = None,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 50,
) -> str:
    """
    Search for query within files in a directory or single file.
    Excludes .git, __pycache__, node_modules, .venv, etc.
    """
    try:
        root = _normalize_target_path(path or ".")
        if not root.exists():
            return json.dumps({"status": "error", "error": f"Path does not exist: {root}"})

        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query if is_regex else re.escape(query), flags)

        ignored_dirs = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            ".idea",
            ".vscode",
            "dist",
            "build",
            ".pytest_cache",
        }

        results: List[Dict[str, Any]] = []

        def check_file(file_path: Path):
            if len(results) >= max_results:
                return
            try:
                # Skip large files (> 2MB)
                if file_path.stat().st_size > 2 * 1024 * 1024:
                    return
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, start=1):
                        if pattern.search(line):
                            results.append({
                                "file": str(file_path),
                                "line": line_no,
                                "content": line.rstrip("\r\n"),
                            })
                            if len(results) >= max_results:
                                break
            except Exception:
                pass

        if root.is_file():
            check_file(root)
        else:
            for current_root, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
                for fname in files:
                    if fname.endswith((".pyc", ".png", ".jpg", ".jpeg", ".ico", ".bin", ".exe", ".pdf", ".zip")):
                        continue
                    check_file(Path(current_root) / fname)
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break

        return json.dumps({
            "status": "success",
            "query": query,
            "count": len(results),
            "truncated": len(results) >= max_results,
            "results": results,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def list_dir(
    path: Optional[str] = None,
    max_depth: int = 2,
    location: Optional[str] = None,
) -> str:
    """
    List contents of a directory with depth limit, sizes, and file types.
    """
    try:
        root = _normalize_target_path(path or ".", location)
        if not root.exists():
            return json.dumps({"status": "error", "error": f"Directory not found: {root}"})
        if not root.is_dir():
            return json.dumps({"status": "error", "error": f"Path is not a directory: {root}"})

        ignored = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        entries: List[Dict[str, Any]] = []

        def recurse(curr: Path, depth: int):
            if depth > max_depth:
                return
            try:
                for item in sorted(curr.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    if item.name in ignored or item.name.startswith("."):
                        continue
                    rel = str(item.relative_to(root))
                    if item.is_dir():
                        entries.append({"path": rel, "type": "directory"})
                        recurse(item, depth + 1)
                    else:
                        size = item.stat().st_size
                        entries.append({"path": rel, "type": "file", "size": size})
            except Exception:
                pass

        recurse(root, 1)
        return json.dumps({
            "status": "success",
            "root": str(root),
            "count": len(entries),
            "entries": entries[:250],
            "truncated": len(entries) > 250,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
