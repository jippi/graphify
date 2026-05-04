"""Tests for barrel `index.{js,ts}` re-export extraction.

`export ... from './foo.ts'` is a re-export — structurally an `export_statement`
node in tree-sitter, but with the same module-path semantics as an `import`.
Without explicit handling, barrel files (shadcn UI, GraphQL codegen output,
internal SDK indexes) appear as orphans and the components they expose can't
be reached via import-graph BFS even though they're heavily used.

This test file pins the supported re-export shapes and confirms unrelated
export shapes (locals, default values, type-only exports) don't accidentally
produce phantom edges.
"""

from pathlib import Path

from graphify.extract import _make_id, extract_js


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _import_targets(result: dict) -> set[str]:
    return {str(e.get("target") or "") for e in result["edges"]
            if e.get("relation") in ("imports", "imports_from")}


# ── Re-export shapes that must produce edges ────────────────────────────────


def test_named_reexport_with_from(tmp_path):
    """The canonical barrel pattern: `export { Foo } from './foo.ts'`."""
    target = _write(tmp_path / "foo.ts", "export const Foo = 1")
    importer = _write(tmp_path / "index.ts",
                      "export { Foo } from './foo.ts'\n")
    result = extract_js(importer)
    expected = _make_id(str(target))
    assert expected in _import_targets(result)


def test_default_renamed_reexport(tmp_path):
    """shadcn pattern: `export { default as FlexRender } from './x.svelte'`.
    The most common Svelte barrel shape."""
    target = _write(tmp_path / "flex-render.svelte", "<div></div>")
    importer = _write(tmp_path / "index.ts", """\
export { default as FlexRender } from './flex-render.svelte'
""")
    result = extract_js(importer)
    expected = _make_id(str(target))
    assert expected in _import_targets(result)


def test_namespace_reexport(tmp_path):
    """Wildcard re-export: `export * from './foo.ts'`."""
    target = _write(tmp_path / "foo.ts", "export const a = 1; export const b = 2")
    importer = _write(tmp_path / "index.ts", "export * from './foo.ts'\n")
    result = extract_js(importer)
    expected = _make_id(str(target))
    assert expected in _import_targets(result)


def test_namespace_aliased_reexport(tmp_path):
    """`export * as Foo from './foo.ts'` — names the entire namespace."""
    target = _write(tmp_path / "foo.ts", "export const a = 1")
    importer = _write(tmp_path / "index.ts",
                      "export * as Foo from './foo.ts'\n")
    result = extract_js(importer)
    expected = _make_id(str(target))
    assert expected in _import_targets(result)


def test_type_only_reexport(tmp_path):
    """TypeScript: `export type { Foo } from './foo.ts'` — type-only re-export."""
    target = _write(tmp_path / "foo.ts", "export type Foo = string")
    importer = _write(tmp_path / "index.ts",
                      "export type { Foo } from './foo.ts'\n")
    result = extract_js(importer)
    expected = _make_id(str(target))
    assert expected in _import_targets(result)


def test_multiple_reexports_in_one_file(tmp_path):
    """Realistic barrel: many re-exports in one index.ts. All should resolve."""
    a = _write(tmp_path / "a.ts", "export const X = 1")
    b = _write(tmp_path / "b.ts", "export const Y = 2")
    c = _write(tmp_path / "c.ts", "export const Z = 3")
    importer = _write(tmp_path / "index.ts", """\
export { X } from './a.ts'
export { Y } from './b.ts'
export * from './c.ts'
""")
    result = extract_js(importer)
    targets = _import_targets(result)
    assert _make_id(str(a)) in targets
    assert _make_id(str(b)) in targets
    assert _make_id(str(c)) in targets


def test_reexport_alongside_imports(tmp_path):
    """A file that mixes regular imports AND re-exports."""
    helper = _write(tmp_path / "helper.ts", "export const _internal = 1")
    foo = _write(tmp_path / "foo.ts", "export const Foo = 1")
    importer = _write(tmp_path / "index.ts", """\
import { _internal } from './helper.ts'
export { Foo } from './foo.ts'
console.log(_internal)
""")
    result = extract_js(importer)
    targets = _import_targets(result)
    assert _make_id(str(helper)) in targets
    assert _make_id(str(foo)) in targets


# ── Export shapes that must NOT produce edges (regression guards) ───────────


def test_local_const_export_no_edge(tmp_path):
    """`export const x = 1` — declares a local, no module reference."""
    importer = _write(tmp_path / "page.ts", "export const x = 1\n")
    result = extract_js(importer)
    assert not _import_targets(result), \
        f"local const export must not emit imports edges; got {_import_targets(result)}"


def test_local_function_export_no_edge(tmp_path):
    importer = _write(tmp_path / "page.ts",
                      "export function f() { return 1 }\n")
    result = extract_js(importer)
    assert not _import_targets(result)


def test_default_export_value_no_edge(tmp_path):
    """`export default 'literal'` — no module reference. The string-literal
    here is a VALUE, not a module specifier; must not be misread as one."""
    importer = _write(tmp_path / "page.ts",
                      "export default 'just-a-value'\n")
    result = extract_js(importer)
    targets = _import_targets(result)
    assert not targets, (
        f"export default of a string literal must not emit edges; "
        f"got {targets}"
    )


def test_export_clause_without_from_no_edge(tmp_path):
    """`export { x }` — re-exports a local symbol, not a module."""
    importer = _write(tmp_path / "page.ts", """\
const x = 1
export { x }
""")
    result = extract_js(importer)
    assert not _import_targets(result)


# ── Real-world barrel pattern (shadcn-style) ─────────────────────────────────


def test_shadcn_barrel_makes_components_reachable(tmp_path):
    """End-to-end: a directory of components plus an `index.ts` re-exporting
    them. After this fix, `index.ts` has outgoing edges to each component,
    so the components are reachable from any consumer that imports the barrel."""
    component_a = _write(tmp_path / "calendar-cell.svelte", "<div></div>")
    component_b = _write(tmp_path / "calendar-grid.svelte", "<div></div>")
    component_c = _write(tmp_path / "calendar-header.svelte", "<div></div>")
    barrel = _write(tmp_path / "index.ts", """\
export { default as CalendarCell } from './calendar-cell.svelte'
export { default as CalendarGrid } from './calendar-grid.svelte'
export { default as CalendarHeader } from './calendar-header.svelte'
""")
    result = extract_js(barrel)
    targets = _import_targets(result)
    assert _make_id(str(component_a)) in targets
    assert _make_id(str(component_b)) in targets
    assert _make_id(str(component_c)) in targets
