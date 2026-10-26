from pathlib import Path
import ast


def test_projection_has_no_transport_or_router_imports():
    path = Path(__file__).parents[2] / "modules/mcp/core/projection.py"
    tree = ast.parse(path.read_text())
    source = path.read_text()
    forbidden = ("WebSocket", "CapabilityRuntime", "GameSessionRouter", "LocalModRouter", "GatewayRouter", "importlib")
    assert not any(token in source for token in forbidden)
    assert not any(isinstance(node, ast.ImportFrom) and ".transports" in (node.module or "") for node in tree.body)


def test_projection_is_deterministic_source():
    source = (Path(__file__).parents[2] / "modules/mcp/core/projection.py").read_text()
    assert "sort_keys=True" in source
    assert "resolve_catalog" in source
