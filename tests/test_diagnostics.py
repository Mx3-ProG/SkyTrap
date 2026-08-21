from skytrap.core.diagnostics import parse_diagnostics


def test_parses_gcc_clang_style_error():
    output = "main.c:12:5: error: expected ';' before 'return'"
    diagnostics = parse_diagnostics(output, "c")

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.file == "main.c"
    assert d.line == 12
    assert d.column == 5
    assert d.severity == "error"
    assert "expected ';'" in d.message


def test_parses_rustc_style_error():
    output = (
        "error[E0384]: cannot assign twice to immutable variable `x`\n"
        "  --> src/main.rs:3:5\n"
        "  |\n"
        "3 |     x = 2;\n"
    )
    diagnostics = parse_diagnostics(output, "rust")

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.file == "src/main.rs"
    assert d.line == 3
    assert d.column == 5
    assert d.code == "E0384"
    assert d.severity == "error"


def test_parses_go_style_error():
    output = "./main.go:10:2: undefined: fmt"
    diagnostics = parse_diagnostics(output, "go")

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.file == "./main.go"
    assert d.line == 10
    assert d.column == 2
    assert "undefined: fmt" in d.message


def test_parses_dotnet_style_error():
    output = "Program.cs(15,9): error CS0103: The name 'foo' does not exist in the current context"
    diagnostics = parse_diagnostics(output, "csharp")

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.file == "Program.cs"
    assert d.line == 15
    assert d.column == 9
    assert d.code == "CS0103"


def test_unknown_language_returns_empty_list():
    assert parse_diagnostics("anything at all", "cobol") == []


def test_no_match_returns_empty_list():
    assert parse_diagnostics("Compilation succeeded, no errors.", "rust") == []
