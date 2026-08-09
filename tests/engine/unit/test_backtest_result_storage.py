import json
from datetime import datetime
from pathlib import Path

import pytest
from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage


@pytest.mark.asyncio
async def test_load_latest_grid_book_snapshot_returns_last_snapshot(tmp_path):
    result_path = tmp_path / "backtest.jsonl"
    records = [
        {"_type": "decision_trace", "decision_id": "trace-1"},
        {"_type": "grid_book_snapshot", "version": 1, "levels": []},
        {"_type": "trade_intent", "intent_id": "intent-1"},
        {
            "_type": "grid_book_snapshot",
            "version": 2,
            "levels": [{"grid_id": "grid-2"}],
        },
    ]
    result_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n{bad-json\n",
        encoding="utf-8",
    )

    snapshot = await BacktestResultStorage.load_latest_grid_book_snapshot(
        str(result_path)
    )

    assert snapshot is not None
    assert snapshot["version"] == 2
    assert snapshot["levels"][0]["grid_id"] == "grid-2"


@pytest.mark.asyncio
async def test_load_latest_grid_book_snapshot_missing_file_returns_none(tmp_path):
    snapshot = await BacktestResultStorage.load_latest_grid_book_snapshot(
        str(tmp_path / "missing.jsonl")
    )

    assert snapshot is None


@pytest.mark.asyncio
async def test_grid_book_snapshots_are_coalesced_by_semantic_state(tmp_path):
    storage = BacktestResultStorage(
        backtest_id="bt-grid-book",
        result_dir=str(tmp_path),
    )

    storage.add_grid_book_snapshot({
        "run_id": "run-1",
        "reason": "tick",
        "levels": [{"grid_id": "grid-1", "status": "PLANNED"}],
        "updated_at": "2026-05-15T09:30:00",
    })
    storage.add_grid_book_snapshot({
        "run_id": "run-1",
        "reason": "tick",
        "levels": [{"grid_id": "grid-1", "status": "PLANNED"}],
        "updated_at": "2026-05-15T09:30:01",
    })
    storage.add_grid_book_snapshot({
        "run_id": "run-1",
        "reason": "tick",
        "levels": [{"grid_id": "grid-1", "status": "MONITORING"}],
        "updated_at": "2026-05-15T09:30:02",
    })

    result_path = await storage.flush()
    records = [
        json.loads(line)
        for line in Path(result_path).read_text(encoding="utf-8").splitlines()
    ]
    grid_books = [
        record for record in records if record.get("_type") == "grid_book_snapshot"
    ]

    assert len(grid_books) == 2
    assert grid_books[0]["levels"][0]["status"] == "PLANNED"
    assert grid_books[1]["levels"][0]["status"] == "MONITORING"


@pytest.mark.asyncio
async def test_indexed_layout_writes_query_summaries_and_manifest(tmp_path):
    storage = BacktestResultStorage(
        backtest_id="bt-indexed",
        strategy_run_id="run-indexed",
        version=2,
        result_dir=str(tmp_path),
    )
    storage.add_trace({
        "id": "decision-1",
        "run_id": "run-indexed",
        "trace_id": "trace-1",
        "tags": ["strategy_output"],
        "input_summary": {"timestamp": "2026-05-01T09:30:00"},
        "output_summary": {"action": "hold"},
        "trade_intents": [],
    })
    storage.add_trace({
        "id": "decision-2",
        "run_id": "run-indexed",
        "trace_id": "trace-2",
        "tags": ["strategy_output"],
        "input_summary": {"timestamp": "2026-05-01T09:31:00"},
        "output_summary": {"action": "buy"},
        "trade_intents": [{"id": "intent-1", "intent_id": "intent-1"}],
    })
    storage.add_trade_intent({
        "id": "intent-1",
        "intent_id": "intent-1",
        "trace_id": "trace-2",
        "direction": "BUY",
        "instrument_code": "000001.SZ",
        "status": "PENDING",
    })
    storage.add_trade_intent({
        "id": "intent-1",
        "intent_id": "intent-1",
        "trace_id": "trace-2",
        "direction": "BUY",
        "instrument_code": "000001.SZ",
        "status": "FILLED",
        "limit_price_hint": 10.23,
        "executed_volume": 100,
    })
    storage.add_grid_book_snapshot({
        "run_id": "run-indexed",
        "version": 2,
        "levels": [{"grid_id": "grid-final"}],
    })
    storage.add_log("INFO", "回测版本开始执行", "backtest")
    storage.add_log("ERROR", "回测版本执行异常", "backtest")

    manifest_path = Path(await storage.flush())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_dir = manifest_path.parent

    assert manifest["schema_version"] == 3
    assert manifest["audit_mode"] == "events_only"
    assert manifest["strategy_run_id"] == "run-indexed"
    assert manifest["version"] == 2
    assert manifest["artifacts"]["raw_trace"] is None
    assert manifest["artifacts"]["raw_index"] is None
    for key in [
        "decision_events",
        "no_trade_rollups",
        "execution_summary",
        "execution_logs",
        "latest_grid_book",
    ]:
        assert (artifact_dir / manifest["artifacts"][key]).exists()

    decision_events = [
        json.loads(line)
        for line in (artifact_dir / "decision_events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    no_trade_rollups = [
        json.loads(line)
        for line in (artifact_dir / "no_trade_rollups.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    executions = [
        json.loads(line)
        for line in (artifact_dir / "execution_summary.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    execution_logs_path = artifact_dir / "execution_logs.jsonl"
    execution_logs = [
        json.loads(line)
        for line in execution_logs_path.read_text(encoding="utf-8").splitlines()
    ]
    snapshot = await BacktestResultStorage.load_latest_grid_book_snapshot(
        str(manifest_path)
    )
    from quantx_api.gqlapi.resolvers.strategies import StrategyResolver

    resolver_decisions = StrategyResolver._load_backtest_decision_records(
        str(manifest_path),
        limit=50,
    )
    resolver_executions = StrategyResolver._load_backtest_intent_records(
        str(manifest_path),
        limit=50,
    )
    tail_logs = StrategyResolver._read_strategy_log_page(
        run_id="run-indexed",
        file_path=str(execution_logs_path),
        record_type=None,
        cursor=None,
        limit=1,
        before=False,
        tail=True,
    )
    older_logs = StrategyResolver._read_strategy_log_page(
        run_id="run-indexed",
        file_path=str(execution_logs_path),
        record_type=None,
        cursor=tail_logs["start_cursor"],
        limit=1,
        before=True,
        tail=False,
    )

    assert [item["id"] for item in decision_events] == ["decision-2"]
    assert len(no_trade_rollups) == 1
    assert no_trade_rollups[0]["count"] == 1
    assert len(executions) == 1
    assert executions[0]["status"] == "FILLED"
    assert manifest["artifacts"]["execution_logs"] == "execution_logs.jsonl"
    assert manifest["counts"]["execution_logs"] == 2
    assert [item["message"] for item in execution_logs] == [
        "回测版本开始执行",
        "回测版本执行异常",
    ]
    assert snapshot["levels"][0]["grid_id"] == "grid-final"
    assert [item["id"] for item in resolver_decisions] == ["decision-2"]
    assert resolver_decisions[0]["trade_intents"][0]["limit_price_hint"] == 10.23
    assert [item["status"] for item in resolver_executions] == ["FILLED"]
    assert [item.message for item in tail_logs["entries"]] == ["回测版本执行异常"]
    assert tail_logs["has_previous_page"] is True
    assert [item.message for item in older_logs["entries"]] == ["回测版本开始执行"]


@pytest.mark.asyncio
async def test_indexed_layout_sorts_mixed_timestamp_types(tmp_path):
    storage = BacktestResultStorage(
        backtest_id="bt-mixed-time",
        strategy_run_id="run-mixed-time",
        version=1,
        result_dir=str(tmp_path),
    )
    storage.add_trade_intent({
        "id": "intent-datetime",
        "intent_id": "intent-datetime",
        "direction": "SELL",
        "instrument_code": "000001.SZ",
        "status": "FILLED",
        "executed_time": datetime(2026, 5, 1, 9, 31),
    })
    storage.add_trade_intent({
        "id": "intent-string",
        "intent_id": "intent-string",
        "direction": "BUY",
        "instrument_code": "000001.SZ",
        "status": "FILLED",
        "executed_time": "2026-05-01T09:30:00",
    })

    manifest_path = Path(await storage.flush())
    executions = [
        json.loads(line)
        for line in (manifest_path.parent / "execution_summary.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert [item["intent_id"] for item in executions] == [
        "intent-string",
        "intent-datetime",
    ]


@pytest.mark.asyncio
async def test_events_only_rolls_up_large_no_trade_tick_stream(tmp_path):
    storage = BacktestResultStorage(
        backtest_id="bt-compact",
        strategy_run_id="run-compact",
        version=1,
        result_dir=str(tmp_path),
    )
    for index in range(100000):
        storage.add_trace({
            "id": f"decision-{index}",
            "run_id": "run-compact",
            "trace_id": f"trace-{index}",
            "tags": ["strategy_output"],
            "input_summary": {
                "timestamp": "2026-05-01T09:30:00",
                "cadence": "TICK",
                "market_context": {
                    "instrument_code": "000001.SZ",
                    "market_state": "NEUTRAL",
                    "data_quality": "INSUFFICIENT",
                    "price": 10.0,
                    "source_fingerprint": "x" * 2000,
                    "metrics": {"huge": "y" * 2000},
                    "instrument_master": {
                        "instrument_code": "000001.SZ",
                        "source_summary": {"huge": "z" * 2000},
                    },
                },
            },
            "output_summary": {
                "trade_intent_count": 0,
                "trace_payload": {
                    "reason": "tick_no_trade",
                    "bar_key": "2026-05-01 09:30",
                    "block_events": [
                        {"block_reason": "risk", "message": "blocked"}
                        for _ in range(8)
                    ],
                    "huge_payload": "k" * 2000,
                },
            },
            "trade_intents": [],
        })

    manifest_path = Path(await storage.flush())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    decision_events = [
        json.loads(line)
        for line in (manifest_path.parent / "decision_events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    rollups = [
        json.loads(line)
        for line in (manifest_path.parent / "no_trade_rollups.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert manifest["artifacts"]["raw_trace"] is None
    assert not (manifest_path.parent / "raw_trace.jsonl").exists()
    assert len(decision_events) == 0
    assert len(rollups) == 1
    assert rollups[0]["count"] == 100000
    assert rollups[0]["reason"] == "tick_no_trade"
    assert rollups[0]["last_price"] == 10.0


@pytest.mark.asyncio
async def test_key_decision_events_are_preserved_in_events_only_mode(tmp_path):
    storage = BacktestResultStorage(
        backtest_id="bt-events",
        strategy_run_id="run-events",
        version=1,
        result_dir=str(tmp_path),
    )
    storage.add_trace({
        "id": "trade-decision",
        "run_id": "run-events",
        "trace_id": "trace-trade",
        "tags": ["strategy_output"],
        "input_summary": {
            "timestamp": "2026-05-01T09:30:00",
            "cadence": "TICK",
            "market_context": {"metrics": {"huge": "x" * 2000}, "price": 10.0},
        },
        "output_summary": {"trace_payload": {"reason": "buy_signal"}},
        "trade_intents": [
            {
                "id": "intent-1",
                "intent_id": "intent-1",
                "metadata": {"large": "y" * 2000},
            }
        ],
    })
    storage.add_trace({
        "id": "patch-decision",
        "run_id": "run-events",
        "trace_id": "trace-patch",
        "tags": ["strategy_output"],
        "input_summary": {"timestamp": "2026-05-01T09:31:00"},
        "output_summary": {"trace_payload": {"reason": "state_update"}},
        "state_patch": {"set": {"flag": True}},
        "trade_intents": [],
    })
    storage.add_trace({
        "id": "risk-decision",
        "run_id": "run-events",
        "trace_id": "trace-risk",
        "tags": ["risk_blocked"],
        "risk_decision": {"allowed": False, "reason_code": "LIMIT_UP"},
        "trade_intents": [],
        "reason": "LIMIT_UP",
    })

    manifest_path = Path(await storage.flush())
    events = [
        json.loads(line)
        for line in (manifest_path.parent / "decision_events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    rollups = (manifest_path.parent / "no_trade_rollups.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()

    assert [event["id"] for event in events] == [
        "trade-decision",
        "patch-decision",
        "risk-decision",
    ]
    assert events[0]["trade_intents"][0]["intent_id"] == "intent-1"
    assert "metadata" not in events[0]["trade_intents"][0]
    assert "metrics" not in events[0]["input_summary"]["market_context"]
    assert rollups == []


@pytest.mark.asyncio
async def test_full_audit_mode_writes_raw_trace(tmp_path):
    storage = BacktestResultStorage(
        backtest_id="bt-full",
        strategy_run_id="run-full",
        version=1,
        result_dir=str(tmp_path),
        audit_mode="full",
    )
    storage.add_trace({
        "id": "decision-full",
        "run_id": "run-full",
        "trace_id": "trace-full",
        "tags": ["strategy_output"],
        "input_summary": {"timestamp": "2026-05-01T09:30:00"},
        "output_summary": {"trace_payload": {"reason": "tick_no_trade"}},
        "trade_intents": [],
    })

    manifest_path = Path(await storage.flush())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_dir = manifest_path.parent

    assert manifest["audit_mode"] == "full"
    assert manifest["artifacts"]["raw_trace"] == "raw_trace.jsonl"
    assert (artifact_dir / "raw_trace.jsonl").exists()
    assert (artifact_dir / "raw_trace.index.jsonl").exists()
