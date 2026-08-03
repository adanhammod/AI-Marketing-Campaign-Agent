import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "campaign_worker"

# The graph layer MAY import the provider abstraction and its typed contracts
# (campaign_worker.providers.base / .models / .voice_models) -- that is the whole
# point of dependency injection: nodes depend on ImageProvider/VideoProvider/
# VoiceProvider, never on a specific transport or a specific concrete class.
#
# The graph layer MUST NOT import:
#   - any transport/implementation library a real provider would use, or
#   - any concrete provider implementation, including the mocks -- a mock is
#     still one specific implementation; the graph must stay agnostic to which
#     one is injected, so it may never import a mock module by name.
FORBIDDEN_IMPORTS_IN_NODES = {
    "mcp",
    "httpx",
    "boto3",
    "botocore",
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "campaign_worker.providers.mock_image_provider",
    "campaign_worker.providers.mock_video_provider",
    "campaign_worker.providers.mock_voice_provider",
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


def test_graph_nodes_never_import_transport_libraries_or_concrete_providers():
    imported = _imported_module_names(SRC / "graph" / "nodes.py")
    for forbidden in FORBIDDEN_IMPORTS_IN_NODES:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), (
            f"graph/nodes.py must not import {forbidden!r}, found in {imported}"
        )


def test_graph_nodes_may_import_the_provider_abstraction():
    # The inverse of the test above: proves the allowlisted modules really are
    # importable from graph/nodes.py today, so this boundary is exercised by real
    # code, not just permitted in principle.
    imported = _imported_module_names(SRC / "graph" / "nodes.py")
    allowed = {"campaign_worker.providers.base", "campaign_worker.providers.models"}
    assert imported & allowed, f"expected graph/nodes.py to import at least one of {allowed}, found {imported}"


def test_graph_executor_never_imports_transport_libraries_or_concrete_providers():
    # The executor may import the WorkflowRepository and provider abstractions but
    # never a concrete provider transport or implementation -- those stay inside
    # providers/ and get chosen at composition time (Task 21), not here.
    imported = _imported_module_names(SRC / "graph" / "executor.py")
    for forbidden in FORBIDDEN_IMPORTS_IN_NODES:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), (
            f"graph/executor.py must not import {forbidden!r}, found in {imported}"
        )


def test_provider_source_files_contain_no_sensitive_text_literals():
    provider_files = list((SRC / "providers").glob("*.py"))
    assert provider_files, "expected provider source files to exist"
    for path in provider_files:
        lowered = path.read_text(encoding="utf-8").lower()
        for pattern in SENSITIVE_SUBSTRINGS:
            assert pattern not in lowered, f"found sensitive-looking text {pattern!r} in {path}"
