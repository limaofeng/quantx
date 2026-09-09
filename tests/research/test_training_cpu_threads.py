import os
from types import SimpleNamespace

import numpy as np
import pandas as pd
from quantx_domain.selection_factors import selection_feature_columns
from quantx_domain.stock_selection_training import ResolvedBackend
from quantx_infrastructure import training_host_guard as guard
from quantx_research import next_day_selection_gpu as gpu
from quantx_research import next_day_selection_training as training
from quantx_research.next_day_selection_config import NextDaySelectionConfig


def test_actual_cpu_fit_uses_admitted_threads_without_gpu_probe(monkeypatch):
  monkeypatch.setattr(
    guard._active,
    "guard",
    SimpleNamespace(
      process=SimpleNamespace(pid=os.getpid()), policy=SimpleNamespace(cpu_threads=2)
    ),
    raising=False,
  )
  monkeypatch.setenv("OMP_NUM_THREADS", "999")

  def forbidden(*args, **kwargs):
    raise AssertionError("CPU fitting must not inspect or initialize a GPU")

  for name in ("_run_nvidia_smi", "_environment_evidence", "probe_lightgbm_gpu"):
    monkeypatch.setattr(gpu, name, forbidden)
  monkeypatch.setattr(training, "_start_gpu_runtime_sampler", forbidden)
  rng = np.random.default_rng(42)
  panel = pd.DataFrame(
    {name: rng.normal(size=80) for name in selection_feature_columns()}
  )
  panel["event_date"] = pd.date_range("2025-01-01", periods=80)
  panel["label"] = np.tile([0, 1], 40)
  config = NextDaySelectionConfig.model_validate(
    {
      "data": {"date_range": ["2025-01-01", "2025-04-01"]},
    }
  )
  fitted = training._fit_family(
    "LIGHTGBM",
    {"num_leaves": 7, "reg_lambda": 1.0},
    panel.iloc[:60],
    panel.iloc[60:],
    config,
    resolved_backend=ResolvedBackend.CPU,
  )
  assert fitted.model.booster_.params["num_threads"] == 2
  assert fitted.model.booster_.params["device_type"] == "cpu"
  assert fitted.model.n_jobs == 2
  assert np.isfinite(training._predict(fitted, panel.iloc[60:])[2]).all()
