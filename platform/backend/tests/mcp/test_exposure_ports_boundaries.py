from pathlib import Path


def test_exposure_context_does_not_import_transport_or_mcp_projection_runtime():
    source = Path("platform/backend/modules/mcp/context/exposure.py").read_text()
    forbidden = ("modules.mcp.transports", "modules.mcp.routers", "SkillRegistry", "ToolCatalog")
    assert not any(item in source for item in forbidden)


def test_exposure_contract_has_no_v2_or_legacy_fallback():
    source = Path("platform/backend/modules/mcp/context/exposure.py").read_text()
    assert "V2" not in source
    assert "legacy" not in source.lower()
