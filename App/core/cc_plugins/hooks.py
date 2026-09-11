"""${VAR} substitution used by skill rendering."""

from __future__ import annotations


def substitute_vars(text: str, env: dict[str, str]) -> str:
    out = text or ""
    for key in sorted(env, key=len, reverse=True):
        out = out.replace("${" + key + "}", env[key])
    return out
