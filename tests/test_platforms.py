from app.platforms.registry import PLATFORMS, IntegrationStatus, detect_platform_running


def test_no_platform_claims_native_integration():
    for info in PLATFORMS.values():
        assert info.native_api == IntegrationStatus.NOT_IMPLEMENTED
        assert info.capture == IntegrationStatus.VERIFIED_LOOPBACK


def test_detect_platform_running():
    assert detect_platform_running("zoom", {"zoom.exe", "explorer.exe"}) is True
    assert detect_platform_running("teams", {"explorer.exe"}) is False
    assert detect_platform_running("meet", {"msedge.exe"}) is True
    assert detect_platform_running("other", set()) is None
    assert detect_platform_running("unknown", set()) is None
