"""Make the standalone script importable as `analyze_grammar` in tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
