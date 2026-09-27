from envlinter import scanners


def names(refs):
    return [r.name for r in refs]


class TestPythonAccessors:
    def test_getenv(self):
        assert names(scanners.scan_text('x = os.getenv("API_URL")\n', "a.py")) == ["API_URL"]

    def test_environ_subscript(self):
        assert names(scanners.scan_text('x = os.environ["API_URL"]\n', "a.py")) == ["API_URL"]

    def test_environ_get_with_default(self):
        assert names(scanners.scan_text('os.environ.get("PORT", "8080")', "a.py")) == ["PORT"]

    def test_bare_getenv(self):
        assert names(scanners.scan_text('getenv("HOME_DIR")', "a.py")) == ["HOME_DIR"]


class TestJsAccessors:
    def test_process_env_dot(self):
        assert names(scanners.scan_text("const u = process.env.API_URL;\n", "a.ts")) == ["API_URL"]

    def test_process_env_bracket(self):
        assert names(scanners.scan_text("process.env['API_URL']", "a.ts")) == ["API_URL"]

    def test_vite_import_meta_env(self):
        assert names(scanners.scan_text("import.meta.env.VITE_KEY", "a.ts")) == ["VITE_KEY"]


class TestOtherLanguages:
    def test_rust(self):
        assert names(scanners.scan_text('std::env::var("HOME_DIR")?', "a.rs")) == ["HOME_DIR"]

    def test_go(self):
        assert names(scanners.scan_text('os.Getenv("HOME_DIR")', "a.go")) == ["HOME_DIR"]

    def test_go_lookupenv(self):
        assert names(scanners.scan_text('os.LookupEnv("HOME_DIR")', "a.go")) == ["HOME_DIR"]


class TestLineNumbers:
    def test_reports_the_reading_line(self):
        text = "a = 1\nb = 2\nc = os.getenv('LATE')\n"
        (ref,) = scanners.scan_text(text, "a.py")
        assert ref.line == 3
        assert ref.location == "a.py:3"

    def test_blanked_comments_keep_line_count(self):
        text = "# os.getenv('NOPE')\nx = 1\ny = os.getenv('YES')\n"
        assert names(scanners.scan_text(text, "a.py")) == ["YES"]


class TestNoise:
    def test_comment_lines_are_skipped(self):
        text = "# process.env.NOPE\n// process.env.ALSO_NOPE\n"
        assert scanners.scan_text(text, "a.ts") == []

    def test_builtin_node_env_is_not_reported(self):
        assert scanners.scan_text("process.env.NODE_ENV", "a.ts") == []

    def test_repeated_read_of_one_name_reports_once_per_line(self):
        text = "a = os.getenv('X')\nb = os.getenv('X')\n"
        assert len(scanners.scan_text(text, "a.py")) == 2


class TestTreeWalking:
    def test_skips_vendor_directories(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.ts").write_text("process.env.FROM_DEP")
        (tmp_path / "app.ts").write_text("process.env.FROM_APP")
        found = names(scanners.scan_tree(str(tmp_path)))
        assert found == ["FROM_APP"]

    def test_ignores_non_source_extensions(self, tmp_path):
        (tmp_path / "notes.md").write_text("process.env.FROM_MD")
        (tmp_path / "data.json").write_text('{"process.env.FROM_JSON": 1}')
        assert scanners.scan_tree(str(tmp_path)) == []

    def test_reads_go_and_rs_besides_py(self, tmp_path):
        (tmp_path / "main.go").write_text('os.Getenv("GO_VAR")')
        (tmp_path / "lib.rs").write_text('env::var("RS_VAR")?;')
        assert sorted(names(scanners.scan_tree(str(tmp_path)))) == ["GO_VAR", "RS_VAR"]
