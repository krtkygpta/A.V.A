from .media.vision import *
from .productivity.calendar import calendar
from .productivity.document_creator import *
from .system.bash_executor import *
from .system.notifiers import (
    continuous_ping,
    ping,
    ring_timer,
    send_notification,
    stop_ping,
)
from .system.phone_call import make_phone_call
from .system.sandbox import *
# from .system.smart_home import *
from .system.smart_home_agent import run_smarthome_agent
from .system.opencode_client import (
    run_opencode,
    get_session_store,
    sessions_prompt_block,
    list_native_sessions,
)
from .system.screenshot import screenshot
from .system.system_control import *
from .system.time_tools import *
from .web.internet import *


def __getattr__(name):
    if name == "research":
        from .web.deep_research import research

        return research
    if name == "get_google_ai_response":
        from .web.internet import get_google_ai_response

        return get_google_ai_response
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
