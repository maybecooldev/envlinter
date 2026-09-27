"""Wiring: turn a directory into a :class:`Context` and run the rules."""

from __future__ import annotations

import os

from . import compose as compose_mod
from . import dotenv, scanners
from .rules import Context, Diagnostic, analyse

__all__ = ["DEFAULT_ENV_FILES", "EXCLUDE_NAMES", "collect", "run"]

#: Checked in this order, so a more specific file wins for duplicate keys.
DEFAULT_ENV_FILES = (".env", ".env.local", ".env.example")

#: Never parsed: real secrets, generated output, or toolchain config.
EXCLUDE_NAMES = frozenset(
    {".env", ".env.local", ".env.development", ".env.production", ".venv", "venv"}
)


def collect(root: str, env_files: list[str] | None = None, include_tests: bool = False) -> Context:
    """Build a :class:`Context` for the project rooted at ``root``."""
    if env_files is None:
        env_files = [n for n in DEFAULT_ENV_FILES if os.path.isfile(os.path.join(root, n))]

    parsed: list[dotenv.DotenvFile] = []
    for name in env_files:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            parsed.append(dotenv.parse(path, environ=os.environ, display_path=name))

    compose_files = [
        compose_mod.load(p, display_path=os.path.normpath(os.path.relpath(p, root)))
        for p in compose_mod.discover(root)
    ]

    return Context(
        root=root,
        env_files=parsed,
        compose_files=compose_files,
        references=scanners.scan_tree(root, include_tests=include_tests),
    )


def run(
    root: str = ".",
    env_files: list[str] | None = None,
    enabled: frozenset[str] | None = None,
    ignore: set[str] | None = None,
    include_tests: bool = False,
) -> list[Diagnostic]:
    """Lint the project at ``root`` and return every finding."""
    ctx = collect(root, env_files, include_tests=include_tests)
    ctx.enabled = enabled
    ctx.ignore = ignore or set()
    return analyse(ctx)
