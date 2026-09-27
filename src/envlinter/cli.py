"""Command line interface.

Exit codes are meant for CI: 0 clean, 1 findings at or above ``--fail-on``,
2 bad usage. ``--format json`` emits one finding per line so the output can
be piped straight into ``jq`` or a CI annotation parser.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .diagnose import DEFAULT_ENV_FILES, run
from .rules import SEVERITIES

_RANK = {name: index for index, name in enumerate(SEVERITIES)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envlinter",
        description="Check that your .env files, compose services and source code agree.",
    )
    parser.add_argument("path", nargs="?", default=".", help="project root (default: .)")
    parser.add_argument(
        "--env-file",
        action="append",
        dest="env_files",
        metavar="NAME",
        help="dotenv file to check; repeatable (default: " + ", ".join(DEFAULT_ENV_FILES) + ")",
    )
    parser.add_argument(
        "--enable",
        action="append",
        metavar="RULE",
        help="only run these rules; repeatable, e.g. --enable ENV006",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        metavar="VAR",
        help="never report this variable name; repeatable",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="also scan test directories, which are skipped by default",
    )
    parser.add_argument(
        "--fail-on",
        choices=SEVERITIES,
        default="error",
        help="lowest severity that makes the command fail (default: error)",
    )
    parser.add_argument("--format", choices=("text", "json", "compact"), default="text")
    parser.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    parser.add_argument("--version", action="version", version=f"envlinter {__version__}")
    return parser


def _render(diagnostics, fmt: str) -> str:
    if fmt == "json":
        return "\n".join(json.dumps(d.to_dict(), sort_keys=True) for d in diagnostics)
    if fmt == "compact":
        return "\n".join(f"{d.rule} {d.severity} {d.location} {d.message}" for d in diagnostics)
    return "\n".join(d.format() for d in diagnostics)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_rules:
        from .rules import RULES

        for ids, _ in RULES:
            print(" ".join(ids))
        return 0

    diagnostics = run(
        root=args.path,
        env_files=args.env_files,
        enabled=frozenset(args.enable) if args.enable else None,
        ignore=set(args.ignore or ()),
        include_tests=args.include_tests,
    )

    output = _render(diagnostics, args.format)
    if output:
        print(output)

    threshold = _RANK[args.fail_on]
    failed = any(_RANK[d.severity] <= threshold for d in diagnostics)
    if failed:
        print(
            f"\n{len(diagnostics)} finding(s); {sum(1 for d in diagnostics if _RANK[d.severity] <= threshold)} at or above {args.fail_on}.",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
