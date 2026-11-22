'''
src/echo_voice/__init__.py
Author: maymeridian
Description: Practise a voice impression and measure how close you get.
             - The package's version, and what most callers need: load or decode a recording,
               analyze it into a Voice, and compare a take with its target.
'''

__version__ = "1.1.1"

from .analysis import AnalysisError, Voice, analyze, decode, load
from .scoring import compare

__all__ = ["AnalysisError", "Voice", "__version__", "analyze", "compare", "decode", "load"]
