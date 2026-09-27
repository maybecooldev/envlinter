import json

import pytest

from envlinter.cli import main


@pytest.fixture
def sample(tmp_path):
    (tmp_path / ".env").write_text("USED=1\nSTALE=2\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'import os\nos.environ["USED"]\nos.environ["MISSING"]\n', encoding="utf-8"
    )
    return str(tmp_path)


def test_clean_project_exits_zero(tmp_path, capsys):
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text('os.environ["A"]\n', encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_errors_exit_one(sample, capsys):
    assert main([sample]) == 1
    assert "MISSING" in capsys.readouterr().out


def test_fail_on_warning_catches_infos(sample, capsys):
    assert main([sample, "--fail-on", "warning"]) == 1


def test_fail_on_info_is_the_strictest(sample, capsys):
    assert main([sample, "--fail-on", "info"]) == 1


def test_json_output_is_one_object_per_line(sample, capsys):
    main([sample, "--format", "json"])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert parsed
    assert all({"rule", "severity", "path", "line"} <= set(item) for item in parsed)


def test_compact_output(sample, capsys):
    main([sample, "--format", "compact"])
    assert "ENV006 error" in capsys.readouterr().out


def test_ignore_flag_silences_a_variable(tmp_path, capsys):
    (tmp_path / "app.py").write_text('os.environ["NOISY"]\n', encoding="utf-8")
    assert main([str(tmp_path), "--ignore", "NOISY"]) == 0


def test_enable_flag_narrows_rules(tmp_path, capsys):
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    main([str(tmp_path), "--enable", "ENV006"])
    assert "ENV005" not in capsys.readouterr().out


def test_list_rules(capsys):
    assert main(["--list-rules"]) == 0
    assert "ENV006" in capsys.readouterr().out


def test_default_path_is_cwd(sample, monkeypatch, capsys):
    monkeypatch.chdir(sample)
    assert main([]) == 1
    assert "MISSING" in capsys.readouterr().out
