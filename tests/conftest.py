import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # so tests can `from synth import ...`
sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))  # ...and `from calibrate import ...`
