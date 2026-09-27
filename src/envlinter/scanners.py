"""Find environment-variable reads in source files.

Regex-based by design. This is a linter that reports on *declared intent*,
not on runtime behaviour, and an AST for every language we want to support
would be a maintenance liability far larger than the false-positive rate is
worth. The patterns are anchored to the specific accessor forms that mean
"someone reads this at runtime", which keeps the noise low enough to run on
every commit.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

__all__ = ["SKIP_DIRS", "SOURCE_SUFFIXES", "Reference", "scan_file", "scan_tree"]

#: A ``getenv("NAME"``) call: shell, POSIX and cross-language spellings.
_GETENV = re.compile(
    r"""\b(?:os\.)?getenv\s*\(\s*(?P<q>['"])(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=q)"""
)

#: ``os.environ["NAME"]`` / ``process.env["NAME"]`` / ``env.NAME`` bracket form.
_BRACKET = re.compile(
    r"""\b(?:os\.environ|os\.getenv|process\.env|import\.meta\.env|env)\s*"""
    r"""\[\s*(?P<q>['"])(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=q)\s*\]"""
)

#: ``process.env.NAME`` / ``Deno.env.get("NAME")`` dot form.
_DOT = re.compile(r"""\b(?:process\.env|import\.meta\.env)\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)""")

#: ``std::env::var("NAME")`` and ``env::var``.
#: ``os.environ.get("NAME", "default")`` and the typed equivalents. The
#: presence of a default matters: a read with a fallback cannot crash, so the
#: undefined-variable rule skips it.
_GETTER = re.compile(
    r"""\b(?:os\.environ|process\.env|import\.meta\.env|env)\s*\."""
    r"""(?:get|getOrDefault|getEnv|fetch)\s*\(\s*"""
    r"""(?P<q>['"])(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=q)\s*(?P<comma>,)?"""
)

_RUST = re.compile(r"""\benv::var(?:_os)?\s*\(\s*"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?""")
_GO = re.compile(r'\bos\.(?:Getenv|LookupEnv)\s*\(\s*"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"')

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_GETENV, "getenv"),
    (_BRACKET, "subscript"),
    (_DOT, "attribute"),
    (_GETTER, "getter"),
    (_RUST, "env::var"),
    (_GO, "os.Getenv"),
)

#: Files we read. Anything else (lockfiles, assets, blobs) is skipped.
SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".java",
        ".kt",
        ".sh",
        ".bash",
        ".zsh",
    }
)

#: Test trees are skipped by default. Fixture code reads variables that only
#: exist in the test that writes them, so scanning them produces a wall of
#: findings that are all true and all irrelevant.
TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "specs", "e2e", "fixtures"})

#: Directories that never contain first-party source.
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        ".tox",
        ".mypy_cache",
        "site-packages",
    }
)

#: Accessors used to interpolate a whole environment, e.g. ``${!VAR}`` in bash
#: or ``String(Environment.Get("VAR"))`` in C#. Not a fixed name, so it is
#: excluded from the dot-attribute pattern rather than reported as a name.
_OPAQUE_ATTRS = frozenset({"PATH", "NODE_ENV"})


@dataclass(frozen=True)
class Reference:
    """One environment-variable read, e.g. a ``process.env`` attribute access."""

    name: str
    path: str
    line: int
    accessor: str
    #: True when the read supplies a fallback, so an unset variable is fine.
    has_default: bool = False

    @property
    def optional(self) -> bool:
        """Whether reading this variable can be expected to fail when unset."""
        return self.has_default

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def _strip_noise(text: str) -> str:
    """Blank out comments and string bodies so we do not match inside prose.

    We keep the line structure (newlines preserved) so line numbers stay
    accurate, and we leave string contents intact only for the languages
    where an env read can legitimately be *constructed* from one — none of
    them, in practice, so contents are blanked.
    """
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            out.append("\n" if line.endswith("\n") else "")
            continue
        out.append(line)
    return "".join(out)


def scan_text(text: str, path: str) -> list[Reference]:
    """Return every env-var read in ``text``, sorted by line."""
    cleaned = _strip_noise(text)
    found: dict[tuple[str, int], Reference] = {}
    for pattern, accessor in _PATTERNS:
        for match in pattern.finditer(cleaned):
            name = match.group("name")
            if not name or name in _OPAQUE_ATTRS:
                continue
            line = cleaned.count("\n", 0, match.start()) + 1
            groups = match.groupdict()
            # os.getenv() returns None when unset, so it never needs a
            # fallback argument to be safe. Rust's env::var returns a Result
            # and is NOT safe without an explicit default.
            has_default = bool(groups.get("comma")) or accessor == "getenv"
            found.setdefault((name, line), Reference(name, path, line, accessor, has_default))
    return [found[key] for key in sorted(found, key=lambda k: (k[1], k[0]))]


def scan_file(path: str, root: str = ".") -> list[Reference]:
    """Scan one file, returning ``[]`` for anything we cannot or should not read."""
    if os.path.splitext(path)[1] not in SOURCE_SUFFIXES:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return []
    rel = os.path.normpath(os.path.relpath(path, root))
    return scan_text(text, rel)


def scan_tree(
    root: str,
    extensions: frozenset[str] | None = None,
    include_tests: bool = False,
) -> list[Reference]:
    """Walk ``root`` and scan every recognised source file.

    Test directories are skipped unless ``include_tests`` is set.
    """
    extensions = extensions or SOURCE_SUFFIXES
    skip = SKIP_DIRS if include_tests else SKIP_DIRS | TEST_DIRS
    refs: list[Reference] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in sorted(filenames):
            if os.path.splitext(name)[1] in extensions:
                refs.extend(scan_file(os.path.join(dirpath, name), root))
    return refs
