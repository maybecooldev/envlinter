"""End-to-end rule tests: build a throwaway project on disk, lint it, assert."""

import json

from envlinter import compose, dotenv, rules, scanners
from envlinter.diagnose import run


def project(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


def rules_fired(diagnostics):
    return {d.rule for d in diagnostics}


def by_rule(diagnostics, rule):
    return [d for d in diagnostics if d.rule == rule]


class TestUndefinedReads:
    def test_reports_a_read_with_no_definition(self, tmp_path):
        root = project(tmp_path, {"app.py": 'x = os.environ["DATABASE_URL"]\n'})
        (d,) = by_rule(run(root), "ENV006")
        assert d.severity == "error"
        assert "DATABASE_URL" in d.message

    def test_defined_in_dotenv_is_silent(self, tmp_path):
        root = project(
            tmp_path,
            {".env": "DATABASE_URL=postgres://x\n", "app.py": 'os.environ["DATABASE_URL"]\n'},
        )
        assert "ENV006" not in rules_fired(run(root))

    def test_read_with_a_default_is_silent(self, tmp_path):
        root = project(tmp_path, {"app.py": 'os.environ.get("PORT", "8080")\n'})
        assert "ENV006" not in rules_fired(run(root))

    def test_reported_once_per_file(self, tmp_path):
        root = project(tmp_path, {"app.py": 'os.environ["A"]\nos.environ["A"]\n'})
        assert len(by_rule(run(root), "ENV006")) == 1

    def test_ignore_flag_suppresses_it(self, tmp_path):
        root = project(tmp_path, {"app.py": 'os.environ["NOISY"]\n'})
        assert "ENV006" not in rules_fired(run(root, ignore={"NOISY"}))


class TestUnusedDeclarations:
    def test_declared_but_unread(self, tmp_path):
        root = project(tmp_path, {".env": "OLD_FLAG=1\n"})
        (d,) = by_rule(run(root), "ENV005")
        assert d.severity == "info"

    def test_read_somewhere_is_silent(self, tmp_path):
        root = project(tmp_path, {".env": "FLAG=1\n", "app.py": 'os.environ["FLAG"]\n'})
        assert "ENV005" not in rules_fired(run(root))

    def test_builtins_are_never_called_unused(self, tmp_path):
        root = project(tmp_path, {".env": "PATH=/usr/bin\nLANG=C\n"})
        assert "ENV005" not in rules_fired(run(root))

    def test_a_real_file_gitignores_is_still_read(self, tmp_path):
        root = project(tmp_path, {".env": "FLAG=1\n", "src/deep/mod.py": 'os.environ["FLAG"]\n'})
        assert "ENV005" not in rules_fired(run(root))


class TestDotenvHygiene:
    def test_duplicate_key(self, tmp_path):
        root = project(tmp_path, {".env": "A=1\nA=2\n"})
        (d,) = by_rule(run(root), "ENV002")
        assert "line 1" in d.message

    def test_empty_value_is_informational(self, tmp_path):
        root = project(tmp_path, {".env": "OPTIONAL=\n", "a.py": 'os.environ["OPTIONAL"]\n'})
        (d,) = by_rule(run(root), "ENV003")
        assert d.severity == "info"

    def test_inline_comment_truncation(self, tmp_path):
        root = project(tmp_path, {".env": "MSG=hello # greeting\n", "a.py": 'os.environ["MSG"]\n'})
        (d,) = by_rule(run(root), "ENV004")
        assert "truncated" in d.message
        assert "hello greeting" in d.hint

    def test_quoted_value_with_a_hash_is_fine(self, tmp_path):
        root = project(tmp_path, {".env": 'MSG="red # blue"\n', "a.py": 'os.environ["MSG"]\n'})
        assert "ENV004" not in rules_fired(run(root))

    def test_invalid_key(self, tmp_path):
        root = project(tmp_path, {".env": "1BAD=x\n"})
        (d,) = by_rule(run(root), "ENV001")
        assert d.severity == "error"

    def test_crlf(self, tmp_path):
        root = project(
            tmp_path, {".env": "A=1\r\nB=2\r\n", "a.py": 'os.environ["A"]\nos.environ["B"]\n'}
        )
        assert "ENV010" in rules_fired(run(root))


class TestCompose:
    COMPOSE = "services:\n  api:\n    environment:\n      - DATABASE_URL\n      - DEBUG\n"

    def test_pass_through_without_a_definition_is_an_error(self, tmp_path):
        root = project(tmp_path, {"docker-compose.yml": self.COMPOSE, ".env": "DEBUG=1\n"})
        (d,) = by_rule(run(root), "ENV007")
        assert "DATABASE_URL" in d.message
        assert "never set" in d.message

    def test_both_undefined_vars_are_reported(self, tmp_path):
        root = project(tmp_path, {"docker-compose.yml": self.COMPOSE})
        assert len(by_rule(run(root), "ENV007")) == 2

    def test_defined_in_dotenv_is_silent(self, tmp_path):
        root = project(
            tmp_path, {"docker-compose.yml": self.COMPOSE, ".env": "DATABASE_URL=x\nDEBUG=1\n"}
        )
        assert "ENV007" not in rules_fired(run(root))

    def test_mapping_form_with_default_is_silent(self, tmp_path):
        compose_yaml = (
            "services:\n  api:\n    environment:\n"
            '      DATABASE_URL: postgres://localhost\n      DEBUG: "1"\n'
        )
        root = project(tmp_path, {"docker-compose.yml": compose_yaml})
        assert "ENV007" not in rules_fired(run(root))

    def test_null_value_means_pass_through(self, tmp_path):
        yaml_text = "services:\n  api:\n    environment:\n      DATABASE_URL:\n"
        root = project(tmp_path, {"docker-compose.yml": yaml_text})
        assert "ENV007" in rules_fired(run(root))

    def test_empty_default_is_flagged(self, tmp_path):
        yaml_text = 'services:\n  api:\n    environment:\n      DEBUG: ""\n'
        root = project(tmp_path, {"docker-compose.yml": yaml_text})
        assert "ENV008" in rules_fired(run(root))

    def test_compose_yaml_suffix(self, tmp_path):
        root = project(tmp_path, {"compose.yaml": self.COMPOSE})
        assert "ENV007" in rules_fired(run(root))

    def test_malformed_compose_reports_instead_of_crashing(self, tmp_path):
        root = project(tmp_path, {"docker-compose.yml": "services: [oops\n"})
        assert "ENV020" in rules_fired(run(root))


class TestRuleSelection:
    def test_enable_narrows_the_run(self, tmp_path):
        root = project(tmp_path, {".env": "A=1\n", "app.py": 'os.environ["MISSING"]\n'})
        found = rules_fired(run(root, enabled=frozenset({"ENV006"})))
        assert found == {"ENV006"}

    def test_disable_by_omitting_from_a_partial_set(self, tmp_path):
        root = project(tmp_path, {".env": "A=1\n"})
        found = rules_fired(run(root, enabled=frozenset({"ENV006"})))
        assert "ENV005" not in found


class TestOutputShape:
    def test_findings_are_sorted_by_position(self, tmp_path):
        root = project(
            tmp_path,
            {".env": "B=1\nA=1\n", "z.py": 'os.environ["Z1"]\n', "a.py": 'os.environ["Z2"]\n'},
        )
        paths = [d.path for d in run(root) if d.rule == "ENV006"]
        assert paths == sorted(paths)

    def test_diagnostic_serialises_to_json(self):
        d = rules.Diagnostic("ENV006", "error", "a.py", 3, "msg", "hint")
        assert json.loads(json.dumps(d.to_dict()))["rule"] == "ENV006"

    def test_text_format_includes_the_hint(self):
        d = rules.Diagnostic("ENV006", "error", "a.py", 3, "msg", "do the thing")
        assert "do the thing" in d.format()


class TestParserUnits:
    def test_compose_harvests_both_forms(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            "services:\n  a:\n    environment:\n      - ONE\n      - TWO=2\n"
            "  b:\n    environment:\n      THREE: three\n      FOUR:\n"
        )
        parsed = compose.load(str(path))
        assert parsed.names() == {"ONE", "TWO", "THREE", "FOUR"}
        assert ("a", "ONE", None) in parsed.passed
        assert ("a", "TWO", "2") in parsed.passed
        assert ("b", "FOUR", None) in parsed.passed

    def test_entry_records_its_source_file(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("A=1\n")
        (entry,) = dotenv.parse(str(path)).entries
        assert entry.source == str(path)

    def test_reference_optionality(self):
        assert scanners.Reference("A", "a.py", 1, "getter", True).optional
        assert not scanners.Reference("A", "a.py", 1, "getenv").optional
