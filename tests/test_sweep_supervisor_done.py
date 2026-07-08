import os

import claims
from supervisor import ProcessSupervisor


def test_done_count_uses_manifest_without_claims(tmp_path):
    sup = ProcessSupervisor(str(tmp_path), on_line=lambda *a: None)
    man = tmp_path / "manifest.json"
    man.write_text('{"a": {"status": "done"}, "b": {"status": "error"}}')
    assert sup.process_done_count(str(man)) == 1


def test_done_count_uses_claims_when_given(tmp_path):
    sup = ProcessSupervisor(str(tmp_path), on_line=lambda *a: None)
    d = str(tmp_path / "claims")
    claims.try_claim(d, "a", "m"); claims.mark_done(d, "a")
    claims.try_claim(d, "b", "m"); claims.mark_done(d, "b")
    claims.try_claim(d, "c", "m")                         # claimed, not done
    assert sup.process_done_count(str(tmp_path / "manifest.json"), claims_dir=d) == 2
