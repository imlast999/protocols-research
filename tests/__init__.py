"""
tests package initialization.
Ensures the 'src' directory is accessible on sys.path for test discovery.
"""

import sys
from pathlib import Path

# Add src/ directory to sys.path so defi_research is importable in offline tests
src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
