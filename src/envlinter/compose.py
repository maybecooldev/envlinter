"""Read environment-variable usage out of docker-compose files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = ["COMPOSE_FILENAMES", "ComposeFile", "load"]

COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

#: Environment variables the shell or the container runtime already provides.
#: Flagging these as "declared but unused" is technically true and practically
#: useless, so they are ignored unless the file redefines them.
BUILTIN = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "PWD",
        "HOSTNAME",
        "TZ",
        "LANG",
        "LC_ALL",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_ENV",
        "NPM_CONFIG_CACHE",
        "RUST_LOG",
        "RUST_BACKTRACE",
        "GOFLAGS",
        "GOPATH",
    }
)


@dataclass
class ComposeFile:
    """A parsed compose document, reduced to what the linter cares about."""

    path: str
    #: ``(service, var, value)`` where ``value`` is ``None`` for pass-through
    #: (``- FOO``) and ``""`` for a literal empty default.
    passed: list[tuple[str, str, str | None]] = field(default_factory=list)
    error: str | None = None

    def names(self) -> set[str]:
        return {var for _, var, _ in self.passed}


def _load_yaml(path: str):
    import yaml  # imported lazily so the linter still runs without PyYAML

    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _harvest(service_name: str, service: dict, out: list[tuple[str, str, str | None]]) -> None:
    env = service.get("environment")
    if isinstance(env, dict):
        for key, value in env.items():
            if value is None:
                out.append((service_name, str(key), None))
            else:
                out.append((service_name, str(key), str(value)))
    elif isinstance(env, list):
        for item in env:
            if not isinstance(item, str):
                continue
            if "=" in item:
                key, _, value = item.partition("=")
                out.append((service_name, key, value))
            else:
                out.append((service_name, item, None))


def load(path: str, display_path: str | None = None) -> ComposeFile:
    """Parse a compose file, recording a diagnostic instead of raising on error."""
    result = ComposeFile(path=display_path or path)
    try:
        document = _load_yaml(path)
    except ImportError:
        result.error = "PyYAML is not installed; compose files were not checked"
        return result
    except Exception as exc:  # yaml.YAMLError and friends
        result.error = f"could not parse compose file: {exc}"
        return result

    if not isinstance(document, dict):
        return result

    for service_name, service in (document.get("services") or {}).items():
        if isinstance(service, dict):
            _harvest(str(service_name), service, result.passed)
    return result


def discover(root: str) -> list[str]:
    """Compose files sitting directly in ``root``."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return [os.path.join(root, n) for n in COMPOSE_FILENAMES if n in names]
