"""build_camera_source must open the CONFIGURED device or fail loudly - never silently
fall back to DirectShow index 0 (which is whatever enumerates first, e.g. OBS Virtual
Camera). The browser preview and the engine must always be on the same camera."""
import pytest

from mkw_tracker.utils import camera


class _FakeCap:
    def __init__(self, index, device_name=""):
        self.index = index
        self.device_name = device_name


@pytest.fixture
def dshow(monkeypatch):
    monkeypatch.setattr(camera, "list_dshow_video_devices",
                        lambda: ["OBS Virtual Camera", "Elgato 4K X"])
    monkeypatch.setattr(camera, "_device_has_p010", lambda name: False)
    monkeypatch.setattr(camera, "VideoCaptureSource", _FakeCap)


def test_configured_sdr_device_opens_by_its_own_index(dshow):
    cap = camera.build_camera_source(device_name="Elgato 4K X")
    assert (cap.index, cap.device_name) == (1, "Elgato 4K X")


def test_unknown_configured_device_raises_instead_of_opening_index_0(dshow):
    with pytest.raises(ValueError, match="Elgato 4K X \\(0fd9:009b\\)"):
        camera.build_camera_source(device_name="Elgato 4K X (0fd9:009b)")
