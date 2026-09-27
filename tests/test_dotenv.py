import pytest

from envlinter import dotenv


def write(tmp_path, text, name=".env"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestBasicSyntax:
    def test_simple_pairs(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A=1\nB=two\n"))
        assert parsed.as_dict() == {"A": "1", "B": "two"}
        assert not parsed.parse_errors

    def test_export_prefix_is_stripped(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "export TOKEN=abc\n"))
        (entry,) = parsed.entries
        assert entry.key == "TOKEN"
        assert entry.value == "abc"
        assert entry.exported

    def test_blank_lines_and_comments_ignored(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "\n# a comment\n\nA=1\n"))
        assert parsed.keys() == ["A"]

    def test_last_definition_wins(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A=1\nA=2\n"))
        assert parsed.as_dict()["A"] == "2"
        assert [e.line for e in parsed.entries] == [1, 2]


class TestQuoting:
    def test_double_quotes_preserve_spaces(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, 'A="hello world"\n'))
        assert parsed.as_dict()["A"] == "hello world"

    def test_single_quotes_are_literal(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A='$HOME'\n"))
        assert parsed.as_dict()["A"] == "$HOME"

    def test_unquoted_value_stops_at_comment(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A=value # note\n"))
        entry = parsed.entries[0]
        assert entry.value == "value"
        assert entry.trailing_comment == "note"

    def test_hash_inside_quotes_is_not_a_comment(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, 'A="red#blue" # note\n'))
        assert parsed.as_dict()["A"] == "red#blue"

    def test_multiline_double_quoted(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, 'KEY="-----BEGIN-----\nbody\n-----END-----"\n'))
        assert parsed.as_dict()["KEY"] == "-----BEGIN-----\nbody\n-----END-----"

    def test_escape_sequences_expand(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, 'A="line1\\nline2"\n'))
        assert parsed.as_dict()["A"] == "line1\nline2"

    def test_unterminated_quote_is_reported(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, 'A="never closed\n'))
        assert any("unterminated quote" in msg for _, msg, _ in parsed.parse_errors)


class TestExpansion:
    def test_expands_earlier_definition(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "HOST=db.internal\nURL=postgres://$HOST:5432\n"))
        assert parsed.as_dict()["URL"] == "postgres://db.internal:5432"

    def test_braced_expansion(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "HOST=db\nURL=${HOST}/x\n"))
        assert parsed.as_dict()["URL"] == "db/x"

    def test_falls_back_to_process_environ(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A=$OUTER\n"), environ={"OUTER": "from-env"})
        assert parsed.as_dict()["A"] == "from-env"

    def test_unknown_variable_expands_to_empty(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A=${NOPE}\n"))
        assert parsed.as_dict()["A"] == ""


class TestDiagnostics:
    @pytest.mark.parametrize("line", ["1BAD=x", "with-dash=x", "with space=x"])
    def test_invalid_keys_rejected(self, tmp_path, line):
        parsed = dotenv.parse(write(tmp_path, f"{line}\n"))
        assert any("invalid key" in msg for _, msg, _ in parsed.parse_errors)
        assert parsed.keys() == []

    def test_crlf_detected_and_stripped(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "A=1\r\nB=2\r\n"))
        assert parsed.has_crlf
        assert parsed.as_dict() == {"A": "1", "B": "2"}

    def test_bom_is_stripped(self, tmp_path):
        parsed = dotenv.parse(write(tmp_path, "﻿A=1\n"))
        assert parsed.as_dict() == {"A": "1"}

    def test_missing_file_reports_instead_of_raising(self, tmp_path):
        parsed = dotenv.parse(str(tmp_path / "nope.env"))
        assert parsed.entries == []
        assert parsed.parse_errors
