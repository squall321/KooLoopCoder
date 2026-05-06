"""pytest config: add agent dir to sys.path so 'loopcoder' resolves without install."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]  # .../LoopCoder/agent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
