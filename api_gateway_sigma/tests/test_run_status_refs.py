"""Страховка: все run_status.<attr>, на которые ссылается sigma app.py, существуют.

run_status.py монтируется в sigma из api_gateway/; опечатка в имени функции
(например, flow_get вместо get_flow) глушится try/except и молча ломает
досылку ранних FlowMap-событий в SSE.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _defined(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_sigma_app_run_status_refs_exist():
    defined = _defined(ROOT / "api_gateway" / "run_status.py")
    tree = ast.parse((ROOT / "api_gateway_sigma" / "app.py").read_text(encoding="utf-8"))
    used = {
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "run_status"
    }
    missing = sorted(used - defined)
    assert not missing, f"run_status не содержит: {missing}"
    assert "get_flow" in used
