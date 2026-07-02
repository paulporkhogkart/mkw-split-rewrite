"""Per-segment bidir rule: the KART FLOURISH mattes bidirectionally (its backward anchor is the
settled plate-free pose; forward-only propagation heals real see-through holes shut by the tail —
wario pirate buggybud ring, tail alpha 1.000 -> 0.103 bidir). Spawn/idle/char stay forward-only
(the position-weighted crossfade can bleed backward-anchor mistakes into a loop)."""
import matte_matanyone as mm


def test_kart_flourish_defaults_to_bidir(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_bidir(True, "flourish") is True


def test_kart_spawn_and_idle_stay_forward_only(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_bidir(True, "spawn") is False
    assert mm.segment_bidir(True, "idle") is False


def test_char_segments_stay_forward_only(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_bidir(False, "flourish") is False
    assert mm.segment_bidir(False, "idle") is False


def test_env_1_forces_bidir_everywhere(monkeypatch):
    monkeypatch.setenv("MATTE_MATANYONE_BIDIR", "1")
    assert mm.segment_bidir(False, "idle") is True


def test_env_0_forces_forward_only_everywhere(monkeypatch):
    monkeypatch.setenv("MATTE_MATANYONE_BIDIR", "0")
    assert mm.segment_bidir(True, "flourish") is False
