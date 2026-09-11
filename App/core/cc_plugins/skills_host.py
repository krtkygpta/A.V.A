"""
Claude Code / Agent Skills loader.

Drop a skill folder (SKILL.md + optional scripts/references) into
App/functions/plugins/ or App/functions/skills/. AVA lists it to the main
agent and loads the full body only when use_skill / /name is used.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.cc_plugins.frontmatter import as_str_list, coerce_bool, parse_frontmatter


def substitute_vars(text: str, env: dict[str, str]) -> str:
    out = text or ""
    for key in sorted(env, key=len, reverse=True):
        out = out.replace("${" + key + "}", env[key])
    return out


_SKIP = {"__pycache__", ".git", ".venv", "venv", "node_modules", "examples", ".claude-plugin"}

_BUILTIN_SLASH = {
    "/help",
    "/commands",
    "/theme",
    "/themes",
    "/mode",
    "/clear",
    "/tasks",
    "/task",
    "/history",
    "/chats",
    "/conversations",
    "/taskside",
    "/sidebar",
    "/quiet",
    "/logs",
    "/sys",
    "/settings",
    "/config",
    "/set",
    "/new",
    "/quit",
    "/skills",
    "/plugins",
}


def plugins_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "functions" / "plugins"


def skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "functions" / "skills"


def project_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


@dataclass
class SkillSpec:
    name: str
    source: str  # folder label (plugin pack name or "skills")
    description: str
    body: str
    path: Path
    skill_dir: Path
    when_to_use: str = ""
    disable_model_invocation: bool = False
    user_invocable: bool = True
    context: str = ""
    arguments: list[str] = field(default_factory=list)
    argument_hint: str = ""

    @property
    def qualified(self) -> str:
        if self.source and self.source != self.name:
            return f"{self.source}:{self.name}"
        return self.name

    @property
    def slash(self) -> str:
        return f"/{self.name}"

    def listing_line(self) -> str:
        desc = self.when_to_use or self.description
        if len(desc) > 160:
            desc = desc[:157] + "..."
        extra = " (type /name to run)" if self.disable_model_invocation else ""
        return f"{self.slash} — {desc}{extra}"


class SkillHost:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self.skills: dict[str, SkillSpec] = {}
        self.errors: list[str] = []
        self._loaded = False
        self._initialized = True

    def load(self, *directories: Path) -> None:
        with self._lock:
            if self._loaded:
                return
            self._scan(directories or default_skill_roots())

    def reload(self, *directories: Path) -> str:
        with self._lock:
            self.skills.clear()
            self.errors.clear()
            self._loaded = False
            self._scan(directories or default_skill_roots())
        return self.summary()

    def _scan(self, directories) -> None:
        roots = []
        for d in directories:
            p = Path(d)
            if p.is_dir():
                roots.append(p)
        seen_files: set[Path] = set()
        for root in roots:
            for skill_md, source in discover_skill_files(root):
                try:
                    key = skill_md.resolve()
                except Exception:
                    key = skill_md
                if key in seen_files:
                    continue
                seen_files.add(key)
                try:
                    spec = skill_from_file(skill_md, source=source)
                except Exception as e:
                    self.errors.append(f"{skill_md}: {e}")
                    continue
                if spec:
                    self._index(spec)
        self._register_tools()
        self._loaded = True
        print(f"[Skills] Loaded {len(self._unique())} Claude Code skill(s)")

    def _index(self, skill: SkillSpec) -> None:
        for key in (skill.name, skill.qualified, skill.slash.lstrip("/")):
            k = _norm(key)
            if k and k not in self.skills:
                self.skills[k] = skill

    def _unique(self) -> list[SkillSpec]:
        seen = set()
        out = []
        for s in self.skills.values():
            mark = str(s.path)
            if mark in seen:
                continue
            seen.add(mark)
            out.append(s)
        out.sort(key=lambda s: s.name)
        return out

    def summary(self) -> str:
        items = self._unique()
        if not items:
            return (
                "No Claude Code skills loaded. Drop a folder with SKILL.md into "
                f"{plugins_dir()} or {skills_dir()}."
            )
        lines = [f"{len(items)} skill(s):"]
        for s in items:
            lines.append(f"- {s.slash}  ({s.path.parent})")
        if self.errors:
            lines.append("Errors: " + "; ".join(self.errors[:5]))
        return "\n".join(lines)

    def catalog_for_main(self) -> str:
        visible = [s for s in self._unique() if not s.disable_model_invocation]
        if not visible and not self._unique():
            return ""
        lines = [
            "SKILLS — when the user request matches a skill, call use_skill(name, arguments) "
            "and follow the returned instructions. User can also type /name."
        ]
        for s in visible[:50]:
            lines.append(f"- {s.listing_line()}")
        user_only = [s for s in self._unique() if s.disable_model_invocation]
        if user_only:
            lines.append(
                "User-only (do not auto-invoke): " + ", ".join(s.slash for s in user_only)
            )
        return "\n".join(lines)

    def get(self, name: str) -> Optional[SkillSpec]:
        if not name:
            return None
        self.load()
        return self.skills.get(_norm(name.lstrip("/")))

    def render(self, name: str, arguments: str = "") -> str:
        skill = self.get(name)
        if not skill:
            known = ", ".join(s.slash for s in self._unique()[:25]) or "(none)"
            return f"Unknown skill {name!r}. Known: {known}"
        return render_skill(skill, arguments)

    def match_slash(self, text: str) -> Optional[tuple[SkillSpec, str]]:
        raw = (text or "").strip()
        if not raw.startswith("/"):
            return None
        first = raw.split(None, 1)[0]
        if first.lower() in _BUILTIN_SLASH:
            return None
        name, _, args = raw[1:].partition(" ")
        skill = self.get(name)
        if not skill or not skill.user_invocable:
            return None
        return skill, args.strip()

    def slash_entries(self) -> list[SkillSpec]:
        return [s for s in self._unique() if s.user_invocable]

    def _register_tools(self) -> None:
        from core.tool_registry import registry

        if "use_skill" not in registry.all_tools():
            registry.register(
                name="use_skill",
                func=_use_skill_tool,
                description=(
                    "Load a Claude Code skill's full instructions. "
                    "Call this when a listed SKILL matches the user's request "
                    "or they typed /skill-name, then follow the returned steps."
                ),
                params={
                    "name": {
                        "type": "string",
                        "description": "Skill name or /name",
                    },
                    "arguments": {
                        "type": "string",
                        "description": "Optional arguments ($ARGUMENTS in the skill)",
                    },
                },
                required=["name"],
            )
        if "list_skills" not in registry.all_tools():
            registry.register(
                name="list_skills",
                func=_list_skills_tool,
                description="List installed Claude Code skills. Set reload=true to rescan folders.",
                params={
                    "reload": {
                        "type": "boolean",
                        "description": "Rescan plugins/ and skills/ folders first",
                    }
                },
                required=[],
            )


def default_skill_roots() -> list[Path]:
    return [plugins_dir(), skills_dir()]


def discover_skill_files(root: Path) -> list[tuple[Path, str]]:
    """Find SKILL.md (and commands/*.md) under a drop folder."""
    found: list[tuple[Path, str]] = []
    root = Path(root)
    if not root.is_dir():
        return found

    def add(md: Path, source: str) -> None:
        if md.is_file() and md.suffix.lower() == ".md":
            found.append((md, source))

    # Dedicated skills root: each child is a skill
    if root.name.lower() == "skills":
        for child in _iter_dirs(root):
            add(child / "SKILL.md", "skills")
        for md in sorted(root.glob("*.md")):
            add(md, "skills")
        return found

    # plugins/ itself: each subdir may be a skill or a pack
    for child in _iter_dirs(root):
        name = child.name
        add(child / "SKILL.md", name)
        skills_sub = child / "skills"
        if skills_sub.is_dir():
            for pack in _iter_dirs(skills_sub):
                add(pack / "SKILL.md", name)
            add(skills_sub / "SKILL.md", name)
        commands = child / "commands"
        if commands.is_dir():
            for md in sorted(commands.glob("*.md")):
                add(md, name)
    return found


def _iter_dirs(path: Path):
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return
    for child in children:
        if child.is_dir() and child.name not in _SKIP and not child.name.startswith("."):
            yield child


def skill_from_file(path: Path, source: str = "") -> Optional[SkillSpec]:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if path.name.lower() == "skill.md":
        name = str(meta.get("name") or path.parent.name).strip()
        skill_dir = path.parent
    else:
        name = str(meta.get("name") or path.stem).strip()
        skill_dir = path.parent
    name = _safe_id(name)
    desc = str(meta.get("description") or "").strip()
    if not desc:
        desc = (body.strip().split("\n\n", 1)[0] if body.strip() else name)[:200]
    return SkillSpec(
        name=name,
        source=source or path.parent.name,
        description=desc,
        body=body,
        path=path,
        skill_dir=skill_dir,
        when_to_use=str(meta.get("when_to_use") or meta.get("whenToUse") or ""),
        disable_model_invocation=coerce_bool(
            meta.get("disable-model-invocation") or meta.get("disable_model_invocation")
        ),
        user_invocable=coerce_bool(
            meta.get("user-invocable") if "user-invocable" in meta else meta.get("user_invocable"),
            True,
        ),
        context=str(meta.get("context") or "").strip().lower(),
        arguments=as_str_list(meta.get("arguments")),
        argument_hint=str(meta.get("argument-hint") or meta.get("argument_hint") or ""),
    )


def render_skill(skill: SkillSpec, arguments: str = "") -> str:
    env = {
        "CLAUDE_SKILL_DIR": str(skill.skill_dir.resolve()),
        "CLAUDE_PLUGIN_ROOT": str(skill.skill_dir.resolve()),
        "CLAUDE_PROJECT_DIR": str(project_dir()),
        "CLAUDE_SESSION_ID": os.getenv("AVA_SESSION_ID") or "",
        "ARGUMENTS": arguments or "",
    }
    body = substitute_vars(skill.body, env)
    parts = _split_args(arguments or "")
    for i, val in enumerate(parts):
        body = body.replace(f"$ARGUMENTS[{i}]", val)
        body = body.replace(f"${i}", val)
    for i, arg_name in enumerate(skill.arguments):
        val = parts[i] if i < len(parts) else ""
        body = re.sub(rf"\${arg_name}\b", val, body)
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", arguments or "")
    elif arguments:
        body = body.rstrip() + f"\n\nARGUMENTS: {arguments}"

    if os.getenv("AVA_DISABLE_SKILL_SHELL") == "1":
        body = re.sub(r"!`[^`]+`", "[shell injection disabled]", body)
    else:
        body = _expand_bang_commands(body, env)

    return (
        f"[SKILL {skill.slash}]\n"
        f"Follow these instructions. Supporting files: {skill.skill_dir}\n\n"
        f"{body}"
    )


def _expand_bang_commands(body: str, env: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        cmd = match.group(1)
        code, out, err = _run_shell(cmd, env)
        text = (out or "").strip()
        if code != 0 and err:
            text = (text + ("\n" if text else "") + err.strip()).strip()
        if len(text) > 8000:
            text = text[:8000] + "\n..."
        return text or "(no output)"

    return re.sub(r"!`([^`]+)`", repl, body)


def _run_shell(command: str, env: dict[str, str]) -> tuple[int, str, str]:
    import subprocess
    import sys

    full_env = os.environ.copy()
    full_env.update(env)
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=env.get("CLAUDE_PROJECT_DIR") or None,
            env=full_env,
            shell=True,
            creationflags=flags,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def _split_args(arguments: str) -> list[str]:
    if not arguments:
        return []
    return [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', arguments)]


def _safe_id(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-:.]+", "-", (name or "").strip()).strip("-")
    return s or "unnamed"


def _norm(name: str) -> str:
    return (name or "").strip().lstrip("/").lower().replace(" ", "-")


def _use_skill_tool(name: str, arguments: str = "") -> str:
    host = get_host()
    host.load()
    return host.render(name, arguments)


def _list_skills_tool(reload: bool = False) -> str:
    host = get_host()
    if reload:
        return host.reload()
    host.load()
    return host.summary()


def get_host() -> SkillHost:
    return SkillHost()
