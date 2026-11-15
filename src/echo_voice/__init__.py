"""Practice a voice impression and measure how close you get."""

from .analysis import AnalysisError, Voice, analyze, decode, load
from .scoring import compare

__all__ = ["AnalysisError", "Voice", "analyze", "compare", "decode", "load"]
