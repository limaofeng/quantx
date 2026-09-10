import json

import pandas as pd
import pytest
from quantx_research import certified_panel_reader as reader
from quantx_research import next_day_selection_dataset as dataset
from quantx_research import next_day_selection_training as training
from quantx_research.artifacts import file_sha256, fingerprint, write_json

from tests.research.test_next_day_selection_workflow import _dataset, _spec


def rewrite_panel(root, panel, *, update_fingerprint=True):
  path = root / "training-panel.parquet"
  panel.to_parquet(path, index=False, row_group_size=11)
  manifest = json.loads((root / "manifest.json").read_text())
  manifest["training_panel_sha256"] = file_sha256(path)
  manifest["training_panel_bytes"] = path.stat().st_size
  manifest["files"][path.name] = {"sha256": manifest["training_panel_sha256"], "bytes": path.stat().st_size}
  if update_fingerprint:
    manifest["data_fingerprint"] = training._data_fingerprint(panel)
  manifest.pop("manifest_sha256")
  manifest["manifest_sha256"] = fingerprint(manifest)
  write_json(root / "manifest.json", manifest)
  return manifest


@pytest.mark.parametrize("categorical", [False, True])
def test_batch_projection_preserves_sorted_fingerprint_and_request_scope(tmp_path, monkeypatch, categorical):
  root = _dataset(tmp_path)
  panel = pd.read_parquet(root / "training-panel.parquet")
  if categorical:
    panel["stock_code"] = pd.Categorical(panel["stock_code"], categories=["600000.SH", "000001.SZ"], ordered=True)
  panel = panel.sample(frac=1, random_state=7).reset_index(drop=True)
  manifest = rewrite_panel(root, panel)
  config_payload = _spec(tmp_path)
  config_payload["data"].update(date_range=["2019-01-01", "2021-12-01"], stock_codes=["600000.SH"])
  config = training._config_from_spec(config_payload)
  expected = training._project_certified_panel(panel, manifest, config)
  monkeypatch.setattr(reader, "BATCH_ROWS", 7)
  monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("whole panel materialized"))
  actual, loaded = training._load_certified_panel(root, config)
  actual = training._project_certified_panel(actual, loaded, config)
  pd.testing.assert_frame_equal(actual, expected)
  assert loaded["data_fingerprint"] == manifest["data_fingerprint"]
  assert dataset.load_certified_dataset_manifest(root) == manifest


def test_unselected_rows_still_undergo_full_fingerprint_validation(tmp_path, monkeypatch):
  root = _dataset(tmp_path)
  panel = pd.read_parquet(root / "training-panel.parquet")
  panel.loc[panel.stock_code == "000001.SZ", "label"] = 0.5
  rewrite_panel(root, panel, update_fingerprint=False)
  payload = _spec(tmp_path)
  payload["data"]["stock_codes"] = ["600000.SH"]
  monkeypatch.setattr(reader, "BATCH_ROWS", 7)
  with pytest.raises(ValueError, match="内容哈希不匹配"):
    training._load_certified_panel(root, training._config_from_spec(payload))


def test_source_changes_during_streaming_are_rejected(tmp_path, monkeypatch):
  root = _dataset(tmp_path)
  original = reader.read_panel

  def read_and_change(path, manifest, **kwargs):
    result = original(path, manifest, **kwargs)
    with path.open("ab") as output:
      output.write(b"changed after verification")
    return result

  monkeypatch.setattr(reader, "read_panel", read_and_change)
  with pytest.raises(ValueError, match="读取期间发生变化"):
    dataset.load_certified_dataset_manifest(root)
