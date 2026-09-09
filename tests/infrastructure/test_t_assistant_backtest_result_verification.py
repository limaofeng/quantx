"""Result exports cannot substitute for the durable frame chain."""

import json
import sqlite3

import pytest
from quantx_infrastructure.services.t_assistant_backtest_store import (
  TAssistantBacktestStore,
)


@pytest.mark.parametrize(
  "damage", [None, "export", "frame", "truncated", "version", "unfinished", "expected"]
)
def test_result_requires_exact_finished_fact_chain(tmp_path, damage):
  store = TAssistantBacktestStore.create(
    tmp_path,
    frozen={
      name: {"synthetic": True}
      for name in ("config", "data", "code", "broker", "timeline", "initial_account")
    },
  )
  previous = store.manifest["hash"]
  for index in range(2):
    previous = store.commit_frame(
      index=index, previous=previous, facts={"sample": index}
    )
  result = store.finish({"synthetic": True})
  expected = result["hash"]
  if damage == "export":
    (store.directory / "result.json").write_text(
      json.dumps({"hash": expected, "material": {}})
    )
  elif damage == "expected":
    expected = "0" * 64
  elif damage:
    with sqlite3.connect(store.directory / "facts.sqlite3") as db:
      if damage == "frame":
        db.execute("UPDATE backtest_frames SET facts='{}' WHERE frame_index=0")
      elif damage == "truncated":
        db.execute("DELETE FROM backtest_frames WHERE frame_index=1")
      elif damage == "version":
        db.execute("UPDATE t_assistant_backtest_versions SET manifest='{}'")
      else:
        db.execute("UPDATE t_assistant_executions SET status='RUNNING'")
  if damage:
    with pytest.raises(ValueError, match="BACKTEST_"):
      store.read_verified_result(expected_hash=expected)
  else:
    assert store.read_verified_result(expected_hash=expected) == result
