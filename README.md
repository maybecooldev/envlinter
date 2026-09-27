# envlinter

Find the environment variables your project declared, read, or quietly forgot.

`.env` files drift. Someone renames a variable in one file, a compose service
keeps forwarding the old name, and the failure shows up as a blank string in
production rather than as an error. `envlinter` reads your dotenv files, your
compose services and your source code, then tells you where they disagree.

```
$ envlinter .
error   ENV006  src/app.py:4
        AWS_REGION is read but never defined
        hint: Add AWS_REGION=... to your .env, or give the read a default
error   ENV007  docker-compose.yml:1
        service 'api' forwards REDIS_URL from the host but it is never set
        hint: Add REDIS_URL=... to .env, or give it a default: REDIS_URL=${REDIS_URL:-default}
```

## Install

```sh
pipx install envlinter     # or: uv tool install envlinter
```

## What it checks

| Rule | Severity | What it means |
| --- | --- | --- |
| `ENV001` | error | Malformed dotenv: invalid key, unterminated quote |
| `ENV002` | warning | The same key is defined twice; the last one wins |
| `ENV003` | info | A key is defined with an empty value |
| `ENV004` | warning | An unquoted value has an inline `#` comment and is silently truncated |
| `ENV005` | info | A key is declared but never read anywhere in the repo |
| `ENV006` | error | A variable is read with no default and never defined |
| `ENV007` | error | A compose service forwards a variable nothing ever sets |
| `ENV008` | info | A compose service sets a variable to an empty string, not to "use the host's" |
| `ENV010` | warning | The file uses CRLF line endings |
| `ENV020` | warning | A compose file could not be parsed |

Only `ENV006` and `ENV007` are "this will break at runtime" findings. The rest
are drift and hygiene, which is why they are warnings or notes rather than
errors — a stale key in `.env` is untidy; a missing one is an outage.

## Usage

```sh
envlinter                      # lint the current directory
envlinter path/to/project
envlinter --format json        # one JSON object per line, for CI annotations
envlinter --format compact     # grep-friendly one-liners
envlinter --fail-on warning   # default is: error
envlinter --enable ENV006      # only run these rules
envlinter --ignore AWS_REGION  # never report this variable
envlinter --include-tests     # also scan tests/ (skipped by default)
envlinter --list-rules
```

Exit code is `0` when clean, `1` when something is at or above `--fail-on`, and
`2` for bad usage.

### In CI

```yaml
- run: pipx run envlinter --format compact --fail-on error
```

### Pre-commit

```yaml
- repo: local
  hooks:
    - id: envlinter
      name: envlinter
      entry: envlinter --fail-on error
      language: system
      types: [text]
      pass_filenames: false
```

## What it reads

**dotenv files** — `.env`, `.env.local`, `.env.example` by default, override
with `--env-file`. The parser follows shell semantics rather than splitting on
`=`, so quoted values may span lines, single quotes are literal, double quotes
and bare values interpolate `$VAR` and `${VAR}` from earlier lines, and an
unquoted `#` starts a comment.

**compose files** — `docker-compose.yml`/`compose.yaml` and their `compose.*`
spellings, in both the mapping and the list form of `environment`.

**source code** — `.py .js .mjs .cjs .jsx .ts .tsx .go .rs .rb .php .java .kt
.sh .bash .zsh`, skipping `node_modules`, `vendor`, `target`, `dist`, `build`,
virtualenvs and dot-directories. Test directories (`tests/`, `__tests__/`,
`spec/`, `fixtures/` and friends) are skipped too, since fixture code reads
variables that only exist inside the test that writes them — pass
`--include-tests` if you want them anyway. It looks for the accessors that mean "read
this at runtime": `os.environ[...]`, `os.getenv(...)`, `process.env.X`,
`import.meta.env.X`, `std::env::var`, `os.Getenv` and friends.

A read that supplies a default (`os.environ.get("PORT", "8080")`) is not
reported as undefined, because it cannot fail.

## How it's built

Four modules, each independently testable:

- `dotenv.py` — the parser. No I/O beyond reading the file.
- `compose.py` — reduces a compose document to the variables it touches.
- `scanners.py` — regex-based source scanning. Regex rather than a full AST
  per language on purpose: this reports on declared intent, and a pattern
  anchored to `process.env.FOO` is precise enough that the false-positive rate
  does not justify the maintenance cost of five language front ends.
- `rules.py` — pure functions from a `Context` to a list of `Diagnostic`s.

```sh
git clone https://github.com/maybecooldev/envlinter
cd envlinter
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Known limits

- Dynamic access (`process.env[key]`, `os.environ[name]`) is invisible to a
  static scanner. Variables built at runtime are not checked.
- Because there is no AST pass, a read written inside a string or a docstring
  is reported. This is deliberate: unparsing strings per language is a much
  larger project than this tool, and `--ignore` covers the handful of cases
  where it matters.
- Which files are "source" is a suffix list, not a project type. A `.py` file
  in a `fixtures/` directory will still be scanned.
- Compose files are not merged with `-f` overrides or multiple files. Only the
  ones in the project root are read.
- There is no `--fix`. The findings are judgement calls — deleting a key
  someone put there on purpose is not something a tool should do behind your
  back.

## License

MIT
