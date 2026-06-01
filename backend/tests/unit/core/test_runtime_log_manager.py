import json

import pytest

from core.runtime_log_manager import RuntimeLogManager


@pytest.mark.asyncio
async def test_runtime_log_manager_flush_writes_configured_file(tmp_path):
    manager = RuntimeLogManager()
    log_path = tmp_path / "execution_logs.jsonl"

    manager.configure_file(run_id="run-log", file_path=str(log_path))
    manager.append(
        run_id="run-log",
        level="INFO",
        message="回测执行开始",
        source="executor",
    )

    await manager.flush("run-log")

    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["run_id"] == "run-log"
    assert records[0]["level"] == "INFO"
    assert records[0]["message"] == "回测执行开始"
    assert records[0]["source"] == "executor"
