"""Item 3 — Tree-sitter powered CodeParser.

The one place tree-sitter is invoked. The LLM never gets shell access to it —
`SymbolIndex`, `DependencyGraph` and the `ast_grep_search` tool all go through
this module, so parsing stays deterministic, sandboxed to the workspace, and
easy to extend to new languages (add a grammar + an entry in `_LANGUAGES`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

try:
    import tree_sitter
except ImportError:  # pragma: no cover - exercised via CodeParser.available() in tests
    tree_sitter = None  # type: ignore[assignment]


class Symbol(BaseModel):
    name: str
    kind: str  # function | class | method | component | selector | element
    start_line: int
    end_line: int


class ParsedFile(BaseModel):
    path: str
    language: str
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)


@dataclass
class _LanguageSpec:
    module_name: str
    loader: str = "language"  # attribute/callable on the module returning a PyCapsule
    extensions: tuple[str, ...] = field(default_factory=tuple)


_LANGUAGES: dict[str, _LanguageSpec] = {
    "python": _LanguageSpec("tree_sitter_python", extensions=(".py",)),
    "javascript": _LanguageSpec("tree_sitter_javascript", extensions=(".js", ".jsx", ".mjs", ".cjs")),
    "typescript": _LanguageSpec("tree_sitter_typescript", loader="language_typescript", extensions=(".ts",)),
    "tsx": _LanguageSpec("tree_sitter_typescript", loader="language_tsx", extensions=(".tsx",)),
    "html": _LanguageSpec("tree_sitter_html", extensions=(".html", ".htm")),
    "css": _LanguageSpec("tree_sitter_css", extensions=(".css",)),
}

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ext: lang for lang, spec in _LANGUAGES.items() for ext in spec.extensions
}
# JSX is grammatically a subset the JS grammar already handles.
_EXTENSION_TO_LANGUAGE[".jsx"] = "javascript"


class CodeParser:
    """Thin, cached wrapper around tree-sitter. `available()` degrades
    gracefully (returns False) when the tree-sitter package or a specific
    grammar isn't installed — callers must treat that as "no AST evidence",
    never as "the code doesn't exist"."""

    def __init__(self) -> None:
        self._parsers: dict[str, "tree_sitter.Parser"] = {}
        self._load_errors: dict[str, str] = {}

    @staticmethod
    def available() -> bool:
        return tree_sitter is not None

    def supported_languages(self) -> list[str]:
        if tree_sitter is None:
            return []
        return [lang for lang in _LANGUAGES if self._parser_for(lang) is not None]

    @staticmethod
    def language_for_path(path: str) -> str | None:
        return _EXTENSION_TO_LANGUAGE.get(Path(path).suffix.lower())

    def _parser_for(self, language: str) -> "tree_sitter.Parser | None":
        if tree_sitter is None:
            return None
        if language in self._parsers:
            return self._parsers[language]
        if language in self._load_errors:
            return None
        spec = _LANGUAGES.get(language)
        if spec is None:
            return None
        try:
            module = __import__(spec.module_name)
            loader = getattr(module, spec.loader)
            ts_language = tree_sitter.Language(loader())
            parser = tree_sitter.Parser(ts_language)
        except Exception as exc:  # noqa: BLE001 - a missing/incompatible grammar is a soft failure
            self._load_errors[language] = str(exc)
            return None
        self._parsers[language] = parser
        return parser

    def parse_source(self, source: str, language: str, *, path: str = "") -> ParsedFile | None:
        parser = self._parser_for(language)
        if parser is None:
            return None
        tree = parser.parse(source.encode("utf-8", errors="replace"))
        extractor = _EXTRACTORS.get(language, _extract_generic)
        symbols, imports, exports, calls = extractor(tree.root_node, source.encode("utf-8", errors="replace"))
        return ParsedFile(path=path, language=language, symbols=symbols, imports=imports, exports=exports, calls=calls)

    def parse_file(self, absolute_path: Path, *, relative_path: str) -> ParsedFile | None:
        language = self.language_for_path(relative_path)
        if language is None:
            return None
        try:
            source = absolute_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return self.parse_source(source, language, path=relative_path)


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _child_by_field(node, field_name: str):
    return node.child_by_field_name(field_name)


def _is_inside(node, ancestor_type: str) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type == ancestor_type:
            return True
        parent = parent.parent
    return False


def _extract_python(root, source: bytes):
    symbols: list[Symbol] = []
    imports: list[str] = []
    calls: list[str] = []

    def walk(node):
        if node.type == "class_definition":
            name_node = _child_by_field(node, "name")
            if name_node is not None:
                symbols.append(
                    Symbol(name=_text(name_node, source), kind="class", start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1)
                )
        elif node.type == "function_definition":
            name_node = _child_by_field(node, "name")
            if name_node is not None:
                kind = "method" if _is_inside(node, "class_definition") else "function"
                symbols.append(
                    Symbol(name=_text(name_node, source), kind=kind, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1)
                )
        elif node.type in {"import_statement", "import_from_statement"}:
            imports.append(_text(node, source).strip())
        elif node.type == "call":
            fn = node.child(0)
            if fn is not None:
                calls.append(_text(fn, source))
        for child in node.children:
            walk(child)

    walk(root)
    return symbols, imports, [], calls


_JS_FUNCTION_TYPES = {"function_declaration", "generator_function_declaration"}


def _js_symbol_name(node, source: bytes) -> str | None:
    name_node = _child_by_field(node, "name")
    if name_node is not None:
        return _text(name_node, source)
    return None


def _extract_javascript_like(root, source: bytes):
    symbols: list[Symbol] = []
    imports: list[str] = []
    exports: list[str] = []
    calls: list[str] = []

    def add(name: str, kind: str, node) -> None:
        symbols.append(Symbol(name=name, kind=kind, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))

    def walk(node):
        if node.type in _JS_FUNCTION_TYPES:
            name = _js_symbol_name(node, source)
            if name:
                add(name, "function", node)
        elif node.type in {"class_declaration", "class"}:
            name = _js_symbol_name(node, source)
            if name:
                add(name, "class", node)
        elif node.type == "method_definition":
            name_node = _child_by_field(node, "name")
            if name_node is not None:
                add(_text(name_node, source), "method", node)
        elif node.type == "variable_declarator":
            name_node = _child_by_field(node, "name")
            value_node = _child_by_field(node, "value")
            if name_node is not None and value_node is not None and value_node.type in {
                "arrow_function",
                "function",
                "function_expression",
            }:
                name = _text(name_node, source)
                kind = "component" if name[:1].isupper() else "function"
                add(name, kind, node)
        elif node.type in {"import_statement", "import_clause"}:
            if node.type == "import_statement":
                imports.append(_text(node, source).strip())
        elif node.type in {"export_statement"}:
            exports.append(_text(node, source).strip().splitlines()[0][:120])
        elif node.type == "call_expression":
            fn = _child_by_field(node, "function")
            if fn is not None:
                calls.append(_text(fn, source))
        for child in node.children:
            walk(child)

    walk(root)
    return symbols, imports, exports, calls


def _extract_html(root, source: bytes):
    symbols: list[Symbol] = []
    imports: list[str] = []

    def walk(node):
        if node.type in {"element", "script_element", "style_element"}:
            start_tag = next((c for c in node.children if c.type == "start_tag"), None)
            if start_tag is not None:
                tag_name_node = next((c for c in start_tag.children if c.type == "tag_name"), None)
                if tag_name_node is not None:
                    tag = _text(tag_name_node, source)
                    if tag in {"script", "link"}:
                        for attr in start_tag.children:
                            if attr.type == "attribute":
                                attr_text = _text(attr, source)
                                if attr_text.startswith(("src=", "href=")):
                                    imports.append(attr_text.split("=", 1)[1].strip("\"'"))
                    symbols.append(
                        Symbol(name=tag, kind="element", start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1)
                    )
        for child in node.children:
            walk(child)

    walk(root)
    return symbols, imports, [], []


def _extract_css(root, source: bytes):
    symbols: list[Symbol] = []

    def walk(node):
        if node.type == "rule_set":
            selectors = next((c for c in node.children if c.type == "selectors"), None)
            if selectors is not None:
                symbols.append(
                    Symbol(
                        name=_text(selectors, source).strip()[:80],
                        kind="selector",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    )
                )
        for child in node.children:
            walk(child)

    walk(root)
    return symbols, [], [], []


def _extract_generic(root, source: bytes):
    return [], [], [], []


_EXTRACTORS = {
    "python": _extract_python,
    "javascript": _extract_javascript_like,
    "typescript": _extract_javascript_like,
    "tsx": _extract_javascript_like,
    "html": _extract_html,
    "css": _extract_css,
}
