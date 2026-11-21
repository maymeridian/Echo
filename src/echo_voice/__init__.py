"""Practise a voice impression and measure how close you get."""

__version__ = "1.1.1"

from .analysis import AnalysisError, Voice, analyze, decode, load
from .scoring import compare

__all__ = ["AnalysisError", "Voice", "__version__", "analyze", "compare", "decode", "load"]
