import sys
from pathlib import Path

_SERVER_DIR = str(Path(__file__).resolve().parent)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from app import run_server

__all__ = ["run_server"]
