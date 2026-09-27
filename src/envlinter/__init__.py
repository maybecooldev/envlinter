"""envlinter — find the environment variables your project declared, read,
or forgot."""

from .compose import ComposeFile
from .compose import load as load_compose
from .diagnose import Diagnostic, run
from .dotenv import DotenvFile, parse
from .scanners import Reference, scan_text, scan_tree

__version__ = "0.1.0"

__all__ = [
    "ComposeFile",
    "Diagnostic",
    "DotenvFile",
    "Reference",
    "__version__",
    "load_compose",
    "parse",
    "run",
    "scan_text",
    "scan_tree",
]
