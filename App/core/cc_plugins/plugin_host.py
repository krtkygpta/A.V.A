"""Compatibility shim — skills live in skills_host."""

from core.cc_plugins.skills_host import SkillHost as PluginHost
from core.cc_plugins.skills_host import get_host, plugins_dir

__all__ = ["PluginHost", "get_host", "plugins_dir"]
