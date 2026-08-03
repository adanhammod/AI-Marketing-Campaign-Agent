import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "campaign_worker"

FORBIDDEN_IMPORTS_IN_NODES = {
    "campaign_worker.providers",
    "mcp",
    "httpx",
    "subprocess",
    "boto3",
}

SENSITIVE_SUBSTRINGS = {
    "api_key",
    "apikey",
    "client_secret",
    "password",
    "bearer ",
    "authorization:",
}


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_graph_nodes_do_not_import_any_provider_or_transport_library():
    imported = _imported_module_names(SRC / "graph" / "nodes.py")
    for forbidden in FORBIDDEN_IMPORTS_IN_NODES:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), (
            f"graph/nodes.py must not import {forbidden!r}, found in {imported}"
        )


def test_graph_executor_does_not_import_provider_transport_libraries():
    # The executor may import the WorkflowRepository abstraction but never a concrete
    # provider transport (mcp SDK, httpx, subprocess) -- those stay inside providers/.
    imported = _imported_module_names(SRC / "graph" / "executor.py")
    for forbidden in ("mcp", "httpx", "subprocess"):
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported)


def test_provider_source_files_contain_no_sensitive_text_literals():
    provider_files = list((SRC / "providers").glob("*.py"))
    assert provider_files, "expected provider source files to exist"
    for path in provider_files:
        lowered = path.read_text(encoding="utf-8").lower()
        for pattern in SENSITIVE_SUBSTRINGS:
            assert pattern not in lowered, f"found sensitive-looking text {pattern!r} in {path}"
