import shutil
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import yaml
from quantx_research import certification_inputs as inputs_module
from quantx_research.certification_inputs import (
  certify_frozen_inputs,
  export_certification_inputs,
  load_certification_inputs,
)
from quantx_research.next_day_selection_config import load_next_day_selection_config

from tests.research.test_frozen_source import CODES, END, START, inputs


async def exported(tmp_path):
  origin = tmp_path / "origin"
  origin.mkdir()
  payload = load_next_day_selection_config(
    Path(__file__).resolve().parents[2]
    / "apps/research/configs/next_day_selection_v1.yaml"
  ).model_dump(mode="json")
  payload["data"].update(
    date_range=[START.isoformat(), (END - timedelta(days=1)).isoformat()],
    stock_codes=[CODES[0]],
    universe_kind="EXPLICIT",
  )
  for key, value in zip(inputs_module._HISTORY, [False, "bank", False]):
    # Identical user basenames must not overwrite different evidence files.
    folder = origin / key
    folder.mkdir()
    path = folder / "history.csv"
    pd.DataFrame(
      {"event_date": [START], "stock_code": [CODES[0]], "value": [value]}
    ).to_csv(path, index=False)
    payload["data"][key] = str(path)
  config_path = origin / "config.yaml"
  config_path.write_text(yaml.safe_dump(payload))
  source, calendar, _ = inputs()
  calendar.get_next_trading_date = AsyncMock(return_value=END)
  directory = tmp_path / "inputs"
  digest = await export_certification_inputs(
    config_path, source, calendar, directory, dataset_version="frozen-v1"
  )
  start = source.load_daily_bars.call_args.args[1]
  assert start <= START - timedelta(days=504)
  assert source.load_daily_bars.call_args.args[2] == END
  return directory, digest, origin


@pytest.mark.asyncio
async def test_portable_inputs_bind_version_hash_and_distinct_histories(
  tmp_path, monkeypatch
):
  directory, digest, origin = await exported(tmp_path)
  relocated = tmp_path / "trainer" / "bundle-id"
  relocated.parent.mkdir()
  shutil.move(directory, relocated)
  shutil.rmtree(origin)
  config, source, manifest = load_certification_inputs(
    relocated, dataset_version="frozen-v1", manifest_sha256=digest
  )
  paths = [getattr(config.data, key) for key in inputs_module._HISTORY]
  assert len(set(paths)) == 3
  assert all(path.parent == relocated for path in paths)
  assert manifest["dataset_version"] == "frozen-v1"
  assert await source.get_next_trading_date("SH", END - timedelta(days=1)) == END
  captured = []

  async def certify(config_path, *, dataset_version, output_root, source, calendar):
    loaded = load_next_day_selection_config(config_path)
    assert calendar is source
    for key in inputs_module._HISTORY:
      path = getattr(loaded.data, key)
      assert path.parent != relocated
      assert path.read_bytes() == getattr(config.data, key).read_bytes()
      captured.append(path)
    assert dataset_version == "frozen-v1"
    assert output_root == tmp_path / "datasets"
    return output_root / dataset_version

  monkeypatch.setattr(inputs_module, "certify_next_day_selection_dataset", certify)
  output = await certify_frozen_inputs(
    relocated,
    dataset_version="frozen-v1",
    manifest_sha256=digest,
    work_directory=tmp_path / "work",
    output_root=tmp_path / "datasets",
  )
  assert output == tmp_path / "datasets/frozen-v1"
  assert all(not path.exists() for path in captured)
  assert list((tmp_path / "work").iterdir()) == []


@pytest.mark.asyncio
async def test_wrong_identity_and_corrupted_history_reject_before_certification(
  tmp_path, monkeypatch
):
  directory, digest, _ = await exported(tmp_path)
  certify = AsyncMock()
  monkeypatch.setattr(inputs_module, "certify_next_day_selection_dataset", certify)
  for version, expected, error in [
    ("other-v1", digest, "identity"),
    ("frozen-v1", "0" * 64, "hash"),
  ]:
    with pytest.raises(ValueError, match=error):
      await certify_frozen_inputs(
        directory,
        dataset_version=version,
        manifest_sha256=expected,
        work_directory=tmp_path / "work",
        output_root=tmp_path / "datasets",
      )
  (directory / "historical_st_membership_path.csv").write_text("modified")
  with pytest.raises(ValueError, match="integrity"):
    await certify_frozen_inputs(
      directory,
      dataset_version="frozen-v1",
      manifest_sha256=digest,
      work_directory=tmp_path / "work",
      output_root=tmp_path / "datasets",
    )
  certify.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_history_never_publishes_export(tmp_path):
  config_path = (
    Path(__file__).resolve().parents[2]
    / "apps/research/configs/next_day_selection_v1.yaml"
  )
  source, calendar, _ = inputs()
  with pytest.raises(ValueError, match="all historical"):
    await export_certification_inputs(
      config_path, source, calendar, tmp_path / "inputs", dataset_version="frozen-v1"
    )
  source.load_daily_bars.assert_not_awaited()
  assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_certification_reference_roundtrips_through_bundle_cache(tmp_path):
  from quantx_infrastructure.training_bundle_store import (
    DirectoryBundleReader,
    materialize_bundle,
  )

  directory, digest, _ = await exported(tmp_path)
  reference = inputs_module.certification_input_reference(
    directory, dataset_version="frozen-v1", manifest_sha256=digest
  )
  store = tmp_path / "store"
  store.mkdir()
  shutil.copytree(directory, store / reference.bundle.bundle_id)
  shutil.rmtree(directory)
  cached = materialize_bundle(
    DirectoryBundleReader(store), reference.bundle, tmp_path / "cache", reserve_bytes=0
  )
  loaded, _, _ = load_certification_inputs(
    cached,
    dataset_version=reference.bundle.source_id,
    manifest_sha256=reference.manifest_sha256,
  )
  assert loaded.data.historical_st_membership_path.parent == cached
