from .media.vision import *
from .media.music_player import *

from .productivity.document_creator import *
from .productivity.calendar import calendar

from .system.system_control import *
from .system.smart_home import *
from .system.smart_home_agent import run_smarthome_agent
from .system.notifiers import ring_timer, send_notification
from .system.time_tools import *
from .system.sandbox import *
from .system.bash_executor import *

from .web.internet import *

def __getattr__(name):
    if name == 'research':
        from .web.deep_research import research
        return research
    if name == 'get_google_ai_response':
        from .web.internet import get_google_ai_response
        return get_google_ai_response
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")