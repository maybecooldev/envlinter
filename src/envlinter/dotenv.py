"""Parser for ``.env`` files.

Deliberately stricter than a plain ``KEY=VALUE`` split: dotenv is a shell
``source``-able format, and most real bugs come from the corners where
people's expectations of "it's just a key and a string" are wrong.
Unquoted values end at the first unescaped ``#``, quoted values may span
lines, and single quotes are literal while double quotes expand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["DotenvFile", "Entry", "parse"]

#: A key is a shell identifier. Anything else cannot be ``export``-ed safely.
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_EXPORT_RE = re.compile(r"^export\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")

_UNQUOTED_ASSIGN_RE = re.compile(r"^(?P<key>[^\s=#]+)\s*=(?P<value>.*)$")


@dataclass(frozen=True)
class Entry:
    """A single ``KEY=VALUE`` binding and where it came from."""

    key: str
    value: str
    line: int
    quoted: bool
    exported: bool
    #: Everything after an unquoted value's inline comment, if any.
    trailing_comment: str | None = None
    #: Path of the file this came from, for diagnostics.
    source: str = ""

    @property
    def is_empty(self) -> bool:
        return self.value == ""


@dataclass
class DotenvFile:
    """Parsed contents of a single ``.env`` file."""

    path: str
    entries: list[Entry] = field(default_factory=list)
    #: Problems found while parsing, as ``(line, message, hint)`` triples.
    parse_errors: list[tuple[int, str, str]] = field(default_factory=list)
    has_crlf: bool = False

    def as_dict(self) -> dict[str, str]:
        """Last definition wins, matching shell semantics."""
        return {entry.key: entry.value for entry in self.entries}

    def keys(self) -> list[str]:
        return [entry.key for entry in self.entries]


def _read_text(path: str) -> str:
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", errors="replace")


def _expand(value: str, lookup, escapes: bool) -> str:
    """Expand ``$VAR`` / ``${VAR}``, and backslash escapes when ``escapes``.

    Shell semantics: unquoted and double-quoted values both interpolate,
    single-quoted ones never do. Backslash escapes only apply inside double
    quotes, which is why ``escapes`` is separate from the interpolation.
    """
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if escapes and ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == "$":
            j = i + 1
            braced = j < len(value) and value[j] == "{"
            if braced:
                j += 1
            start = j
            while j < len(value) and (value[j].isalnum() or value[j] == "_"):
                j += 1
            name = value[start:j]
            if braced:
                if j < len(value) and value[j] == "}":
                    j += 1
                else:
                    out.append(ch)
                    i += 1
                    continue
            if name:
                out.append(lookup(name))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _close_quote(text: str, start: int) -> int:
    """Index of the closing quote for the one opened at ``start``, or -1.

    Newlines are not a terminator: multi-line quoted values (PEM keys, CA
    bundles) are the main reason to parse this format properly rather than
    splitting on ``=``.
    """
    quote = text[start]
    i = start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return -1


def parse(
    path: str, environ: dict[str, str] | None = None, display_path: str | None = None
) -> DotenvFile:
    """Parse the dotenv file at ``path``.

    ``environ`` supplies values for ``$VAR`` expansion; the file's own earlier
    definitions take precedence, then the process environment. ``display_path``
    is what diagnostics report, so a linter can show ``.env`` rather than the
    absolute path it happened to open.
    """
    result = DotenvFile(path=display_path or path)
    try:
        text = _read_text(path)
    except OSError as exc:
        result.parse_errors.append((0, f"cannot read file: {exc.strerror or exc}", ""))
        return result

    if "\r\n" in text:
        result.has_crlf = True

    base = dict(environ or {})
    resolved: dict[str, str] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line_no = i + 1
        line = lines[i].rstrip("\r")
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exported = False
        match = _EXPORT_RE.match(line)
        if match:
            exported = True
            key = match.group("key")
            rest = line[match.end() :]
        else:
            match = _UNQUOTED_ASSIGN_RE.match(line)
            if not match:
                if "=" in line and not line.startswith(" "):
                    key = line.split("=", 1)[0]
                    result.parse_errors.append(
                        (line_no, f"invalid key {key!r}", "Keys must match [A-Za-z_][A-Za-z0-9_]*")
                    )
                continue
            rest = match.group("value")
            key = match.group("key")

        if not KEY_RE.match(key):
            result.parse_errors.append(
                (line_no, f"invalid key {key!r}", "Keys must match [A-Za-z_][A-Za-z0-9_]*")
            )
            continue

        if rest.startswith(("'", '"')):
            close = _close_quote(rest, 0)
            while close == -1 and i < len(lines):
                rest += "\n" + lines[i].rstrip("\r")
                i += 1
                close = _close_quote(rest, 0)
            if close == -1:
                result.parse_errors.append(
                    (
                        line_no,
                        f"unterminated quote for {key}",
                        "Add the closing quote or drop the opening one",
                    )
                )
                continue
            raw_value = rest[1:close]
            quoted = True
            tail = rest[close + 1 :].strip()
            comment = None
            if tail and not tail.startswith("#"):
                result.parse_errors.append(
                    (line_no, f"trailing content after value for {key}", f"Got {tail!r}")
                )
            elif tail:
                comment = tail[1:].strip()
        else:
            raw_value, comment = _split_inline_comment(rest)
            quoted = False

        def lookup(name: str, _r=resolved, _b=base) -> str:
            return _r.get(name, _b.get(name, ""))

        if quoted and rest.startswith("'"):
            # Single quotes are literal; only newlines are normalised so a
            # multi-line value stays on one line in the reported dict.
            value = raw_value.replace("\n", "\\n")
        else:
            value = _expand(raw_value, lookup, escapes=quoted)

        resolved[key] = value
        result.entries.append(
            Entry(
                key=key,
                value=value,
                line=line_no,
                quoted=quoted,
                exported=exported,
                trailing_comment=comment,
                source=result.path,
            )
        )

    return result


def _split_inline_comment(rest: str) -> tuple[str, str | None]:
    """Split an unquoted value from a trailing ``# comment``."""
    hash_at = rest.find("#")
    if hash_at == -1:
        return rest.strip(), None
    value = rest[:hash_at].strip()
    comment = rest[hash_at + 1 :].strip()
    return value, comment or None
