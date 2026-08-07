import io
import json

from dkg.cli.hook_health import run


def test_hook_health_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("DKG_HOME", str(tmp_path / ".dkg"))
    buf = io.StringIO()
    rc = run(out=buf)
    payload = json.loads(buf.getvalue())
    assert rc == 0
    assert payload["ok"] is True
    assert "capabilities" in payload
