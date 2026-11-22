'''
tests/conftest.py
Author: maymeridian
Description: Shared set-up for the tests: puts the test helpers and the tools on the path.
             - tests/ itself, so tests can import the synthetic voice from synth.py.
             - tools/, so tests can use the calibration and validation scripts' helpers.
'''

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # so tests can `from synth import ...`
sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))  # ...and `from calibrate import ...`
