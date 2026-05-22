"""
Code Dependency Graph

Builds a {nodes, edges} graph of the JARVIS Python codebase suitable for
force-graph rendering in the Coder/Brain pane. Each node is a module file;
each edge is an `import` relationship from importer → imported.

Uses Python's stdlib `ast` module — accurate for Python and avoids the
tree-sitter native build. JS/TS support can layer on later via
tree-sitter-javascript / tree-sitter-typescript.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any

JARVIS_ROOT = Path.home() / "jarvis"
EXCLUDE_PARTS = ("venv", "node_modules", "__pycache__", ".git", "dist", "build", ".pytest_cache")


def _is_skipped(p: Path) -> bool:
    return any(part in p.parts for part in EXCLUDE_PARTS)


def _module_name(file_path: Path, root: Path) -> str:
    rel = file_path.relative_to(root)
    if rel.name == "__init__.py":
        rel = rel.parent
    else:
        rel = rel.with_suffix("")
    return ".".join(rel.parts) if rel.parts else file_path.stem


def _imports_from_file(path: Path) -> list[str]:
    imports: list[str] = []
    try:
        text = path.read_text(errors="replace")
        tree = ast.parse(text, filename=str(path))
    except Exception:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
            elif node.level > 0 and node.names:
                # Relative imports — best-effort placeholder
                imports.append("." * node.level + (node.names[0].name or ""))
    return imports


def _file_stats(path: Path) -> dict[str, int]:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return {"lines": 0, "funcs": 0, "classes": 0}
    try:
        tree = ast.parse(text)
    except Exception:
        return {"lines": len(text.splitlines()), "funcs": 0, "classes": 0}
    funcs = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    classes = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    return {"lines": len(text.splitlines()), "funcs": funcs, "classes": classes}


def graph(root: Path | None = None, scope: str = "internal") -> dict[str, Any]:
    """
    Build the dependency graph.

    scope:
      "internal" — only edges between project modules (default)
      "all"      — include external imports as separate nodes
    """
    root = root or JARVIS_ROOT
    t0 = time.time()

    files: list[Path] = [p for p in root.rglob("*.py") if not _is_skipped(p)]
    project_modules: dict[str, Path] = {}
    for f in files:
        mod = _module_name(f, root)
        project_modules[mod] = f
        # Also register the deepest descendant under each ancestor namespace
        # so `from agent.core import bus` matches `agent.core.bus`
        parts = mod.split(".")
        for i in range(1, len(parts)):
            ancestor = ".".join(parts[:i])
            project_modules.setdefault(ancestor, f.parent)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    for f in files:
        mod = _module_name(f, root)
        stats = _file_stats(f)
        nodes[mod] = {
            "id": mod,
            "kind": "module",
            "label": mod,
            "file": str(f.relative_to(root)),
            **stats,
        }
        for imp in _imports_from_file(f):
            target = imp
            if imp.startswith("."):
                # Resolve relative import
                base_parts = mod.split(".")
                level = len(imp) - len(imp.lstrip("."))
                tail = imp.lstrip(".")
                base = base_parts[:max(0, len(base_parts) - level)]
                target = ".".join(base + ([tail] if tail else []))

            if scope == "internal":
                # Only keep internal edges
                if not any(target == m or target.startswith(m + ".") for m in project_modules):
                    continue
            else:
                # External — register a separate node
                if target not in project_modules and target not in nodes:
                    nodes[target] = {
                        "id": target, "kind": "external", "label": target,
                    }

            edges.append({"source": mod, "target": target, "kind": "import"})

    # Compute degree
    in_degree: dict[str, int] = {}
    out_degree: dict[str, int] = {}
    for e in edges:
        out_degree[e["source"]] = out_degree.get(e["source"], 0) + 1
        in_degree[e["target"]] = in_degree.get(e["target"], 0) + 1
    for n in nodes.values():
        n["in_degree"] = in_degree.get(n["id"], 0)
        n["out_degree"] = out_degree.get(n["id"], 0)

    return {
        "ts": t0,
        "duration_s": round(time.time() - t0, 2),
        "scope": scope,
        "summary": {
            "modules": len(nodes),
            "edges": len(edges),
            "files_scanned": len(files),
        },
        "nodes": list(nodes.values()),
        "edges": edges,
    }
