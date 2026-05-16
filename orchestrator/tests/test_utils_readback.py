import json


def test_read_back_json_recovers_codex_call_artifact(tmp_path):
    from orchestrator.utils import read_back_json

    target = tmp_path / ".socmate" / "block_diagram.json"
    isolated = tmp_path / "codex-call-abc123" / ".socmate" / "block_diagram.json"
    isolated.parent.mkdir(parents=True)
    isolated.write_text(json.dumps({"blocks": [{"name": "stage0"}]}))

    result, ok = read_back_json(
        target,
        str(target),
        {"blocks": [], "connections": [], "reasoning": "", "questions": []},
        context="block_diagram",
    )

    assert ok is True
    assert result["blocks"] == [{"name": "stage0"}]
    assert json.loads(target.read_text())["blocks"] == [{"name": "stage0"}]
