from modules.mcp.contracts.projection import McpExposurePort, McpSkillExecutionPort


def test_exposure_and_execution_ports_are_protocols():
    assert getattr(McpExposurePort, "resolve_exposure")
    assert getattr(McpExposurePort, "revalidate_exposure")
    assert getattr(McpExposurePort, "close")
    assert getattr(McpSkillExecutionPort, "start")
    assert getattr(McpSkillExecutionPort, "await_result")
    assert getattr(McpSkillExecutionPort, "cancel")
