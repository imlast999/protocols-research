"""
config.py (Root backward-compatibility shim)
Exposes configuration settings from src/defi_research/config.py.
"""
import sys
from pathlib import Path

_src_path = str(Path(__file__).resolve().parent / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from defi_research.config import *  # noqa: F401, F403
