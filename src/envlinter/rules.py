"""The lint rules themselves.

Each rule is a pure function over a :class:`Context` that appends
:class:`Diagnostic` objects. Keeping them pure makes the whole engine
testable without touching the filesystem, and makes it possible to enable
or disable individual rules by id.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import compose as compose_mod
from . import dotenv, scanners

__all__ = ["RULES", "SEVERITIES", "Context", "Diagnostic", "analyse"]

SEVERITIES = ("error", "warning", "info")


@dataclass(frozen=True)
class Diagnostic:
    """One finding, in a form the CLI and the JSON output can both render."""

    rule: str
    severity: str
    path: str
    line: int
    message: str
    hint: str = ""

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    def format(self) -> str:
        head = f"{self.severity:<7} {self.rule}  {self.location}\n        {self.message}"
        return f"{head}\n        hint: {self.hint}" if self.hint else head

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass
class Context:
    """Everything the rules are allowed to look at."""

    root: str = "."
    env_files: list[dotenv.DotenvFile] = field(default_factory=list)
    compose_files: list[compose_mod.ComposeFile] = field(default_factory=list)
    references: list[scanners.Reference] = field(default_factory=list)
    enabled: frozenset[str] | None = None
    #: Variable names the user has told us to stop reporting on.
    ignore: set[str] = field(default_factory=set)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def declared(self) -> dict[str, dotenv.Entry]:
        """All keys across all env files; the last file listed wins."""
        out: dict[str, dotenv.Entry] = {}
        for env_file in self.env_files:
            for entry in env_file.entries:
                out[entry.key] = entry
        return out

    def is_enabled(self, *rules: str) -> bool:
        if self.enabled is None:
            return True
        return any(rule in self.enabled for rule in rules)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def _env_syntax(ctx: Context) -> None:
    """Malformed dotenv syntax: bad keys, unterminated quotes, CRLF."""
    for env_file in ctx.env_files:
        for line, message, hint in env_file.parse_errors:
            ctx.diagnostics.append(
                Diagnostic("ENV001", "error", env_file.path, line, message, hint)
            )
        if env_file.has_crlf:
            ctx.diagnostics.append(
                Diagnostic(
                    "ENV010",
                    "warning",
                    env_file.path,
                    1,
                    "file uses CRLF line endings",
                    "Convert to LF; some loaders leave a stray \\r in the last value",
                )
            )


def _duplicate_keys(ctx: Context) -> None:
    for env_file in ctx.env_files:
        seen: dict[str, int] = {}
        for entry in env_file.entries:
            if entry.key in seen:
                ctx.diagnostics.append(
                    Diagnostic(
                        "ENV002",
                        "warning",
                        env_file.path,
                        entry.line,
                        f"{entry.key} is defined more than once (first at line {seen[entry.key]})",
                        "The last definition silently wins",
                    )
                )
            else:
                seen[entry.key] = entry.line


def _empty_values(ctx: Context) -> None:
    declared = ctx.declared()
    for key, entry in declared.items():
        if key in ctx.ignore or not entry.is_empty:
            continue
        ctx.diagnostics.append(
            Diagnostic(
                "ENV003",
                "info",
                entry.source,
                entry.line,
                f"{key} is defined with an empty value",
                "Intentional in dev? Keep it, but be aware some runtimes read it as 'unset'",
            )
        )


def _unquoted_with_spaces(ctx: Context) -> None:
    for env_file in ctx.env_files:
        for entry in env_file.entries:
            if entry.quoted or entry.trailing_comment is None or key_shadowed(ctx, entry.key):
                continue
            ctx.diagnostics.append(
                Diagnostic(
                    "ENV004",
                    "warning",
                    env_file.path,
                    entry.line,
                    f"{entry.key} has an inline comment; the value is truncated at '#'",
                    "Quote the value: " + f'{entry.key}="{entry.value} {entry.trailing_comment}"',
                )
            )


def key_shadowed(ctx: Context, key: str) -> bool:
    return any(key in env_file.as_dict() for env_file in ctx.env_files[1:])


def _unused_declarations(ctx: Context) -> None:
    used = {ref.name for ref in ctx.references}
    for key, entry in ctx.declared().items():
        if key in ctx.ignore or key in used or key in compose_mod.BUILTIN:
            continue
        ctx.diagnostics.append(
            Diagnostic(
                "ENV005",
                "info",
                entry.source,
                entry.line,
                f"{key} is declared but never read in this repository",
                "Remove it, or pass it to a container that is not in this tree",
            )
        )


def _undefined_reads(ctx: Context) -> None:
    declared = set(ctx.declared())
    for compose_file in ctx.compose_files:
        declared |= compose_file.names()
    reported: set[tuple[str, str]] = set()
    for ref in ctx.references:
        if (
            ref.optional
            or ref.name in ctx.ignore
            or ref.name in declared
            or ref.name in compose_mod.BUILTIN
        ):
            continue
        key = (ref.path, ref.name)
        if key in reported:
            continue
        reported.add(key)
        ctx.diagnostics.append(
            Diagnostic(
                "ENV006",
                "error",
                ref.path,
                ref.line,
                f"{ref.name} is read but never defined",
                f"Add {ref.name}=... to your .env, or give the read a default",
            )
        )


def _compose_pass_through(ctx: Context) -> None:
    """A compose service forwarding a variable the repository never defines."""
    declared = set(ctx.declared())
    for compose_file in ctx.compose_files:
        if compose_file.error:
            ctx.diagnostics.append(
                Diagnostic("ENV020", "warning", compose_file.path, 1, compose_file.error, "")
            )
            continue
        for service, name, value in compose_file.passed:
            if (
                value is not None
                or name in declared
                or name in ctx.ignore
                or name in compose_mod.BUILTIN
            ):
                continue
            ctx.diagnostics.append(
                Diagnostic(
                    "ENV007",
                    "error",
                    compose_file.path,
                    1,
                    f"service '{service}' forwards {name} from the host but it is never set",
                    f"Add {name}=... to .env, or give it a default: {name}=${{{name}:-default}}",
                )
            )


def _compose_empty_default(ctx: Context) -> None:
    for compose_file in ctx.compose_files:
        for service, name, value in compose_file.passed:
            if value == "" and name not in ctx.ignore:
                ctx.diagnostics.append(
                    Diagnostic(
                        "ENV008",
                        "info",
                        compose_file.path,
                        1,
                        f"service '{service}' sets {name} to an empty string",
                        "Compose treats an empty value as 'set to empty', not 'use the host value'",
                    )
                )


#: (ids, implementation) pairs. One implementation may emit several ids, and
#: disabling any one of them disables the whole check that produces it.
RULES: tuple[tuple[tuple[str, ...], Callable[[Context], None]], ...] = (
    (("ENV001", "ENV010"), _env_syntax),
    (("ENV002",), _duplicate_keys),
    (("ENV003",), _empty_values),
    (("ENV004",), _unquoted_with_spaces),
    (("ENV005",), _unused_declarations),
    (("ENV006",), _undefined_reads),
    (("ENV007", "ENV020"), _compose_pass_through),
    (("ENV008",), _compose_empty_default),
)


def analyse(ctx: Context) -> list[Diagnostic]:
    """Run every enabled rule and return findings sorted by position."""
    ctx.diagnostics = []
    for ids, func in RULES:
        if ctx.is_enabled(*ids):
            func(ctx)
    ctx.diagnostics.sort(key=lambda d: (d.path, d.line, d.rule))
    return ctx.diagnostics
