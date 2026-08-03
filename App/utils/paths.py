"""
User-facing path resolution for AVA file tools.

Maps friendly names like "desktop", "documents", or "~" to real OS paths
instead of creating a relative folder in the process working directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union


def user_home() -> Path:
    return Path.home().expanduser().resolve()


def _first_existing_dir(candidates: list[Path]) -> Optional[Path]:
    for p in candidates:
        try:
            if p and p.is_dir():
                return p.resolve()
        except OSError:
            continue
    return None


def get_desktop() -> Path:
    """Real Desktop folder (Windows/OneDrive-aware), never a relative 'desktop' cwd folder."""
    home = user_home()
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""
    userprofile = os.environ.get("USERPROFILE") or str(home)

    candidates = [
        Path(userprofile) / "Desktop",
        home / "Desktop",
        Path(onedrive) / "Desktop" if onedrive else None,
        home / "OneDrive" / "Desktop",
        home / "OneDrive - Personal" / "Desktop",
    ]
    found = _first_existing_dir([c for c in candidates if c is not None])
    if found:
        return found

    # Create classic Desktop if missing
    fallback = Path(userprofile) / "Desktop"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()
    except OSError:
        return home


def get_documents() -> Path:
    home = user_home()
    userprofile = os.environ.get("USERPROFILE") or str(home)
    onedrive = os.environ.get("OneDrive") or ""
    candidates = [
        Path(userprofile) / "Documents",
        home / "Documents",
        Path(onedrive) / "Documents" if onedrive else None,
        home / "OneDrive" / "Documents",
    ]
    found = _first_existing_dir([c for c in candidates if c is not None])
    if found:
        return found
    fallback = Path(userprofile) / "Documents"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()
    except OSError:
        return home


def get_downloads() -> Path:
    home = user_home()
    userprofile = os.environ.get("USERPROFILE") or str(home)
    candidates = [
        Path(userprofile) / "Downloads",
        home / "Downloads",
    ]
    found = _first_existing_dir(candidates)
    if found:
        return found
    fallback = Path(userprofile) / "Downloads"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()
    except OSError:
        return home


# Friendly single-token aliases → resolvers
_ALIASES = {
    "desktop": get_desktop,
    "desk": get_desktop,
    "documents": get_documents,
    "docs": get_documents,
    "mydocuments": get_documents,
    "my documents": get_documents,
    "downloads": get_downloads,
    "download": get_downloads,
    "home": user_home,
    "~": user_home,
    "user": user_home,
    "userprofile": user_home,
    "temp": lambda: Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"),
    "tmp": lambda: Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"),
}


def _expand_env(path_str: str) -> str:
    """Expand %VAR% (Windows) and $VAR / ${VAR}."""
    # Windows %USERPROFILE%
    def win_env(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))

    out = re.sub(r"%([^%]+)%", win_env, path_str)
    out = os.path.expandvars(out)
    return out


def resolve_location(location: Union[str, Path, None], *, create: bool = True) -> Path:
    """
    Resolve a user/LLM-provided location string to an absolute directory path.

    Accepts:
      - "desktop", "documents", "downloads", "home", "~"
      - "~/Desktop", "%USERPROFILE%\\Desktop"
      - absolute paths
      - relative paths (resolved against cwd — last resort)

    Never treats bare "desktop" as ./desktop in the working directory.
    """
    if location is None or str(location).strip() == "":
        path = get_desktop()
    else:
        raw = str(location).strip().strip('"').strip("'")
        raw = _expand_env(raw)

        # Normalize separators for alias matching
        key = raw.replace("\\", "/").strip().lower().rstrip("/")

        # Bare alias
        if key in _ALIASES:
            path = Path(_ALIASES[key]())
        # ~/… or ~\…
        elif raw.startswith("~"):
            rest = raw[1:].lstrip("\\/")
            path = user_home() / rest if rest else user_home()
        else:
            # Alias as first segment: desktop/foo, Desktop\\reports
            parts = re.split(r"[\\/]+", key)
            if parts and parts[0] in _ALIASES:
                base = Path(_ALIASES[parts[0]]())
                rest_parts = Path(raw.replace("\\", "/")).parts[1:]
                path = base.joinpath(*rest_parts) if rest_parts else base
            else:
                path = Path(raw)
                if not path.is_absolute():
                    # Relative non-alias: still resolve to absolute under cwd
                    # but only after we already handled desktop/documents aliases.
                    path = (Path.cwd() / path).resolve()
                else:
                    path = path.expanduser()
                    try:
                        path = path.resolve()
                    except OSError:
                        path = path.absolute()

    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return path


def resolve_file_path(
    filename: str,
    location: Union[str, Path, None] = None,
    *,
    default_ext: Optional[str] = None,
) -> Path:
    """
    Build absolute file path from filename + location.
    If filename is already absolute, location is ignored.
    """
    name = (filename or "untitled").strip().strip('"').strip("'")
    name = _expand_env(name)

    p = Path(name)
    if p.is_absolute() or name.startswith("~") or "%" in name:
        # Full path given as filename
        if name.startswith("~"):
            rest = name[1:].lstrip("\\/")
            p = user_home() / rest if rest else user_home()
        else:
            p = Path(_expand_env(name)).expanduser()
        if default_ext and p.suffix == "":
            p = p.with_suffix(
                default_ext if default_ext.startswith(".") else f".{default_ext}"
            )
        parent = p.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p

    directory = resolve_location(location, create=True)
    if default_ext and not name.lower().endswith(
        default_ext if default_ext.startswith(".") else f".{default_ext}"
    ):
        # Only append if caller expects it (caller may already handle ext)
        pass
    return directory / name


def research_output_dir() -> Path:
    """Preferred folder for full research reports (user Desktop / AVA_Research)."""
    desk = get_desktop()
    out = desk / "AVA_Research"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to Documents
        out = get_documents() / "AVA_Research"
        out.mkdir(parents=True, exist_ok=True)
    return out.resolve()
