"""LightGBM OpenCL capability probing and qualification evidence.

No function in this module is called by the CPU inference path.  Importing the
module is safe on a CPU-only machine; GPU initialization only occurs inside the
explicit probe/qualification operations.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from quantx_domain.stock_selection_training import (
  DEFAULT_GPU_MAX_MEMORY_FRACTION,
  GateConclusion,
  GpuQualificationStatus,
  stable_json_sha256,
)
from quantx_infrastructure.training_host_guard import (
  monitor_training_gpu_memory,
  training_cpu_threads,
)

from quantx_research.artifacts import write_json

try:  # A CPU-only development environment may not have the optional wheel.
  import lightgbm as lgb
except (ImportError, OSError):  # pragma: no cover - depends on host wheel
  lgb = None  # type: ignore[assignment]


GPU_QUALIFICATION_VERSION = "next-day-selection-gpu-v2"
GPU_BRIER_RELATIVE_TOLERANCE = 0.005
GPU_ECE_ABSOLUTE_TOLERANCE = 0.002
GPU_TOP20_OVERLAP_MINIMUM = 0.90
GPU_MIN_SPEEDUP = 0.20
GPU_QUALIFICATION_ENV = "QUANTX_LIGHTGBM_GPU_QUALIFICATION"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_OFFICIAL_GPU_WHEEL_SHA256 = "37089ee95664b6550a7189d887dbf098e3eadab03537e411f52c63c121e3ba4b"
_OFFICIAL_WHEEL_FIELDS = frozenset({
  "schema_version", "source", "lightgbm_version", "wheel_sha256",
  "binary_sha256", "platform", "use_gpu",
})
_BUILD_EVIDENCE_FIELDS = frozenset(
  {
    "schema_version",
    "lightgbm_version",
    "wheel",
    "wheel_sha256",
    "use_gpu",
    "cmake_definitions",
    "platform",
    "cmake",
    "visual_studio_cl",
    "boost_root_configured",
    "boost_librarydir_configured",
    "opencl_include_dir_configured",
    "opencl_library_configured",
    "source_revision",
    "wheel_metadata_version",
    "python",
    "python_abi",
    "built_at_utc",
  }
)
_PUBLIC_BUILD_EVIDENCE_FIELDS = frozenset(
  {
    "schema_version",
    "lightgbm_version",
    "wheel_sha256",
    "wheel_metadata_version",
    "use_gpu",
    "platform",
    "cmake_gpu_enabled",
    "source_revision",
    "cmake",
    "visual_studio_cl",
  }
)


def _package_version(name: str) -> str | None:
  try:
    return importlib.metadata.version(name)
  except importlib.metadata.PackageNotFoundError:
    return None


def _run_nvidia_smi() -> dict[str, Any]:
  """Read non-sensitive GPU facts; never record serial/UUID/path fields."""

  query = "name,memory.total,memory.free,driver_version"
  try:
    completed = subprocess.run(
      ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
      check=True,
      capture_output=True,
      text=True,
      timeout=3,
      encoding="utf-8",
      errors="replace",
    )
  except (OSError, subprocess.SubprocessError):
    return {}
  first = completed.stdout.strip().splitlines()
  if not first:
    return {}
  fields = [item.strip() for item in first[0].split(",")]
  if len(fields) < 4:
    return {}
  result: dict[str, Any] = {
    "model": fields[0][:128],
    "driver_version": fields[3][:32],
  }
  for output_key, index in (("memory_total_mib", 1), ("memory_free_mib", 2)):
    try:
      parsed = float(fields[index])
    except (TypeError, ValueError):
      continue
    if math.isfinite(parsed) and parsed >= 0:
      result[output_key] = parsed
  return result


def _opencl_devices() -> dict[str, Any]:
  try:
    import pyopencl  # type: ignore[import-not-found]
  except ImportError:
    return {}
  platforms: list[dict[str, Any]] = []
  try:
    for platform_id, item in enumerate(pyopencl.get_platforms()):
      devices: list[dict[str, Any]] = []
      for device_id, device in enumerate(item.get_devices()):
        devices.append(
          {
            "device_id": device_id,
            "name": str(getattr(device, "name", ""))[:128],
            "vendor": str(getattr(device, "vendor", ""))[:128],
            "global_memory_mib": round(
              float(getattr(device, "global_mem_size", 0)) / (1024 * 1024), 2
            ),
          }
        )
      platforms.append(
        {
          "platform_id": platform_id,
          "name": str(getattr(item, "name", ""))[:128],
          "vendor": str(getattr(item, "vendor", ""))[:128],
          "devices": devices,
        }
      )
  except Exception:
    return {}
  return {"platforms": platforms}


def _environment_evidence() -> dict[str, Any]:
  gpu = _run_nvidia_smi()
  return {
    "os": platform.platform()[:160],
    "python": sys.version.split()[0],
    "lightgbm": getattr(lgb, "__version__", None),
    "sklearn": _package_version("scikit-learn"),
    "numpy": np.__version__,
    "gpu": gpu,
    "opencl": _opencl_devices(),
  }


def cpu_training_available() -> bool:
  try:
    from sklearn.linear_model import LogisticRegression

    if lgb is None:
      return False
    x = np.arange(80, dtype=float).reshape(40, 2)
    y = np.tile([0, 1], 20)
    LogisticRegression(max_iter=20).fit(x, y)
    model = lgb.LGBMClassifier(
      n_estimators=1,
      num_leaves=2,
      min_child_samples=1,
      device_type="cpu",
      n_jobs=1,
      verbosity=-1,
    ).fit(x, y)
    return bool(np.isfinite(model.predict_proba(x)).all())
  except Exception:
    return False


def gpu_requirement_hash() -> str:
  """Return the stable environment requirement coordinate for GPU evidence."""

  device = _run_nvidia_smi()
  binary_hash = None
  if lgb is not None:
    from lightgbm.libpath import _find_lib_path

    binary_hash = hashlib.sha256(Path(_find_lib_path()[0]).read_bytes()).hexdigest()
  return stable_json_sha256(
    {
      "qualification_version": GPU_QUALIFICATION_VERSION,
      "binary_sha256": binary_hash,
      "gpu_model": device.get("model"),
      "gpu_driver": device.get("driver_version"),
      "opencl": _opencl_devices(),
      "lightgbm": getattr(lgb, "__version__", None),
      "python": sys.version.split()[0],
      "python_implementation": platform.python_implementation(),
      "numpy_major_minor": ".".join(np.__version__.split(".")[:2]),
      "device_type": "gpu",
      "max_bin": 63,
      "gpu_use_dp_modes": [False, True],
    }
  )


def _default_qualification_path() -> Path:
  configured = os.environ.get(GPU_QUALIFICATION_ENV, "").strip()
  if configured:
    return Path(configured)
  return Path(__file__).resolve().parents[4] / ".runtime" / "research-gpu" / "lightgbm-qualification.json"


def _is_link_like(path: Path) -> bool:
  """Return true for symlinks, junctions, and other reparse links."""

  if path.is_symlink() or os.path.islink(str(path)):
    return True
  is_junction = getattr(path, "is_junction", None)
  if is_junction is None:
    return False
  try:
    return bool(is_junction())
  except OSError:
    # An uninspectable reparse point must never become a write target.
    return True


def _gpu_build_probe() -> tuple[bool, str | None]:
  """Attempt one tiny GPU fit, classifying build and runtime failures."""

  if lgb is None:
    return False, "LightGBM Python wheel unavailable"
  x = np.asarray([[0.0], [1.0], [0.2], [0.8]], dtype=np.float32)
  y = np.asarray([0, 1, 0, 1], dtype=np.int32)
  try:
    model = lgb.LGBMClassifier(
      objective="binary",
      n_estimators=1,
      num_leaves=3,
      max_bin=63,
      device_type="gpu",
      verbosity=-1,
    )
    model.fit(x, y)
    return True, None
  except Exception as exc:  # LightGBM exposes build/runtime errors as text.
    message = str(exc)
    lowered = message.lower()
    build_markers = (
      "gpu tree learner was not enabled",
      "gpu support is not enabled",
      "opencl library not found",
      "no gpu support",
      "built without gpu",
    )
    if any(marker in lowered for marker in build_markers):
      return False, "GPU tree learner 未包含在当前 LightGBM 构建中"
    return False, "OpenCL GPU 运行时探针失败"


def _load_qualification(path: str | Path | None) -> dict[str, Any] | None:
  if path is None:
    return None
  candidate = Path(path)
  _reject_path_links(candidate)
  if _is_link_like(candidate) or not candidate.is_file():
    return None
  try:
    payload = json.loads(
      candidate.read_text(encoding="utf-8"),
      parse_constant=lambda token: (_ for _ in ()).throw(
        ValueError(f"GPU 资格证书不允许非有限值: {token}")
      ),
    )
  except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
    return None
  return payload if isinstance(payload, dict) else None


def _integrity_digest(payload: Mapping[str, Any]) -> str:
  """Hash the unsigned certificate payload without pretending it is signed."""

  return stable_json_sha256(
    {key: value for key, value in payload.items() if key != "integrity"}
  )


def _qualification_is_complete(
  payload: Mapping[str, Any] | None,
  *,
  requirement_hash: str | None,
) -> bool:
  if not payload or payload.get("schema_version") != 1:
    return False
  if payload.get("qualification_version") != GPU_QUALIFICATION_VERSION:
    return False
  if payload.get("status") != GpuQualificationStatus.GPU_AVAILABLE.value:
    return False
  expected_requirement_hash = requirement_hash or gpu_requirement_hash()
  if payload.get("requirement_hash") != expected_requirement_hash:
    return False
  required_fields = {
    "schema_version",
    "qualification_version",
    "status",
    "requirement_hash",
    "evidence",
    "evidence_sha256",
    "brier_relative_difference",
    "ece_absolute_difference",
    "fp64_brier_relative_difference",
    "fp64_ece_absolute_difference",
    "top20_overlap",
    "fp64_top20_overlap",
    "speedup",
    "minimum_sample_count",
    "build_evidence_sha256",
    "build_evidence",
    "peak_memory_fraction",
    "memory_sampling_available",
    "model_cpu_loadable",
    "cpu_reload_max_abs_difference",
    "fp64_cpu_reload_max_abs_difference",
    "no_non_finite",
    "conclusion_not_flipped",
    "repeat_consistent",
    "repeat_count",
    "fp32_repeat_count",
    "fp64_runs",
    "environment",
    "integrity",
  }
  if set(payload) != required_fields:
    return False
  integrity = payload.get("integrity")
  if not isinstance(integrity, Mapping) or set(integrity) != {
    "algorithm",
    "kind",
    "signed",
    "digest",
  }:
    return False
  evidence_hash = payload.get("evidence_sha256")
  evidence = payload.get("evidence")
  if not isinstance(evidence_hash, str) or not isinstance(evidence, Mapping):
    return False
  try:
    if stable_json_sha256(evidence) != evidence_hash:
      return False
  except (TypeError, ValueError):
    return False
  minimum_sample_count = payload.get("minimum_sample_count")
  # Predictions cover the held-out split, whereas the acceleration threshold
  # is the size of the complete certified panel used for the benchmark.
  labels_count = evidence.get("labels_count")
  if (
    isinstance(minimum_sample_count, bool)
    or not isinstance(minimum_sample_count, int)
    or minimum_sample_count < 1
    or isinstance(labels_count, bool)
    or not isinstance(labels_count, int)
    or not 0 < labels_count <= minimum_sample_count
  ):
    return False
  build_evidence_hash = payload.get("build_evidence_sha256")
  build_evidence = payload.get("build_evidence")
  if (
    not isinstance(build_evidence_hash, str)
    or not _HASH_RE.fullmatch(build_evidence_hash)
    or not isinstance(build_evidence, Mapping)
    or not _valid_public_build_evidence(build_evidence)
  ):
    return False
  if evidence.get("build_evidence_sha256") != build_evidence_hash:
    return False
  environment = payload.get("environment")
  if (
    not isinstance(environment, Mapping)
    or environment.get("build_evidence_sha256") != build_evidence_hash
  ):
    return False
  required_numeric = (
    "brier_relative_difference",
    "ece_absolute_difference",
    "top20_overlap",
    "speedup",
    "peak_memory_fraction",
  )
  for key in required_numeric:
    try:
      if not math.isfinite(float(payload.get(key))):
        return False
    except (TypeError, ValueError):
      return False
  if (
    integrity.get("algorithm") != "sha256"
    or integrity.get("kind") != "UNSIGNED_DIGEST"
    or integrity.get("signed") is not False
    or integrity.get("digest") != _integrity_digest(payload)
  ):
    return False
  for key in ("fp64_brier_relative_difference", "fp64_ece_absolute_difference", "fp64_top20_overlap"):
    try:
      if not math.isfinite(float(payload.get(key))):
        return False
    except (TypeError, ValueError):
      return False
  try:
    cpu_reload = float(payload["cpu_reload_max_abs_difference"])
    fp64_reload = float(payload["fp64_cpu_reload_max_abs_difference"])
  except (TypeError, ValueError, KeyError):
    return False
  if not math.isfinite(cpu_reload) or not math.isfinite(fp64_reload):
    return False
  return (
    isinstance(payload.get("repeat_count"), int)
    and not isinstance(payload.get("repeat_count"), bool)
    and payload.get("repeat_count", 0) >= 3
    and isinstance(payload.get("fp32_repeat_count"), int)
    and not isinstance(payload.get("fp32_repeat_count"), bool)
    and payload.get("fp32_repeat_count", 0) == payload.get("repeat_count")
    and isinstance(payload.get("fp64_runs"), int)
    and not isinstance(payload.get("fp64_runs"), bool)
    and payload.get("fp64_runs", 0) == payload.get("repeat_count")
    and float(payload["brier_relative_difference"]) <= GPU_BRIER_RELATIVE_TOLERANCE
    and float(payload["ece_absolute_difference"]) <= GPU_ECE_ABSOLUTE_TOLERANCE
    and float(payload["fp64_brier_relative_difference"]) <= GPU_BRIER_RELATIVE_TOLERANCE
    and float(payload["fp64_ece_absolute_difference"]) <= GPU_ECE_ABSOLUTE_TOLERANCE
    and float(payload["top20_overlap"]) >= GPU_TOP20_OVERLAP_MINIMUM
    and float(payload["fp64_top20_overlap"]) >= GPU_TOP20_OVERLAP_MINIMUM
    and float(payload["speedup"]) >= GPU_MIN_SPEEDUP
    and float(payload.get("peak_memory_fraction", 1.0)) <= DEFAULT_GPU_MAX_MEMORY_FRACTION
    and payload.get("memory_sampling_available") is True
    and cpu_reload <= 1e-6
    and fp64_reload <= 1e-6
    and bool(payload.get("no_non_finite", False))
    and bool(payload.get("conclusion_not_flipped", False))
    and bool(payload.get("repeat_consistent", False))
    and bool(payload.get("model_cpu_loadable", False))
  )


def _minimum_sample_count_from_manifest(manifest: Mapping[str, Any]) -> int:
  """Bind the GPU threshold to the certified golden panel's real size."""

  quality = manifest.get("quality")
  if not isinstance(quality, Mapping):
    raise ValueError("认证黄金面板缺少质量证据")
  sample_count = quality.get("sample_count")
  if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
    raise ValueError("认证黄金面板 sample_count 非法")
  return sample_count


def probe_lightgbm_gpu(
  *,
  qualification_path: str | Path | None = None,
  requirement_hash: str | None = None,
  sample_count: int | None = None,
  estimated_memory_fraction: float | None = None,
) -> dict[str, Any]:
  """Return a redacted, gate-ready CPU/OpenCL capability report."""

  environment = _environment_evidence()
  gpu = environment.get("gpu") or {}

  def available_memory_mib() -> float | None:
    try:
      value = float(gpu.get("memory_free_mib"))
    except (AttributeError, TypeError, ValueError):
      return None
    return value if math.isfinite(value) and value >= 0.0 else None

  available_memory = available_memory_mib()

  def qualification_projection(
    status: str,
    *,
    acceleration: float | None = None,
    minimum_sample_count: int | None = None,
    peak_memory_fraction: float | None = None,
    evidence_sha256: str | None = None,
    gates_passed: bool = False,
  ) -> dict[str, Any]:
    return {
      "status": status,
      "acceleration": acceleration,
      "minimum_sample_count": minimum_sample_count,
      "peak_memory_fraction": peak_memory_fraction,
      "gates_passed": gates_passed,
      "evidence_sha256": evidence_sha256,
    }

  result: dict[str, Any] = {
    "cpu_available": cpu_training_available(),
    "status": GpuQualificationStatus.CPU_AVAILABLE.value,
    "environment": environment,
    "qualification_version": GPU_QUALIFICATION_VERSION,
    "requirement_hash": requirement_hash or gpu_requirement_hash(),
    "available_memory_mib": available_memory,
  }
  build_ok, probe_error = _gpu_build_probe()
  if not build_ok:
    status = (
      GpuQualificationStatus.GPU_UNAVAILABLE_BUILD.value
      if probe_error and "构建" in probe_error
      else GpuQualificationStatus.GPU_UNAVAILABLE_RUNTIME.value
    )
    # Missing Python LightGBM is a build/dependency failure rather than a
    # runtime GPU fault.
    if lgb is None:
      status = GpuQualificationStatus.GPU_UNAVAILABLE_BUILD.value
    result["status"] = status
    result["qualification"] = qualification_projection(status)
    result["reason"] = probe_error
    return result

  qualification_path = qualification_path or _default_qualification_path()
  requirement_hash = requirement_hash or gpu_requirement_hash()
  qualification = _load_qualification(qualification_path)
  try:
    if estimated_memory_fraction is not None:
      memory_fraction: float | None = float(estimated_memory_fraction)
    elif "memory_total_mib" in gpu and "memory_free_mib" in gpu:
      memory_fraction = 1.0 - float(gpu["memory_free_mib"]) / max(
        float(gpu["memory_total_mib"]), 1.0
      )
    elif (
      qualification is not None
      and qualification.get("peak_memory_fraction") is not None
    ):
      memory_fraction = float(qualification["peak_memory_fraction"])
    else:
      memory_fraction = None
  except (TypeError, ValueError, ZeroDivisionError):
    memory_fraction = math.inf
  if memory_fraction is not None and (
    not math.isfinite(memory_fraction)
    or memory_fraction > DEFAULT_GPU_MAX_MEMORY_FRACTION
  ):
    status = GpuQualificationStatus.GPU_INSUFFICIENT_MEMORY.value
    result["status"] = status
    result["qualification"] = qualification_projection(
      status, peak_memory_fraction=memory_fraction
    )
    return result

  if not _qualification_is_complete(qualification, requirement_hash=requirement_hash):
    status = GpuQualificationStatus.GPU_UNQUALIFIED.value
    result["status"] = status
    result["qualification"] = qualification_projection(
      status, peak_memory_fraction=memory_fraction
    )
    result["reason"] = "缺少与当前 requirement hash 匹配的完整资格证据"
    return result
  if available_memory is None:
    status = GpuQualificationStatus.GPU_UNQUALIFIED.value
    result["status"] = status
    result["qualification"] = qualification_projection(
      status,
      acceleration=float(qualification["speedup"]),
      minimum_sample_count=int(qualification["minimum_sample_count"]),
      peak_memory_fraction=float(qualification["peak_memory_fraction"]),
      evidence_sha256=str(qualification["evidence_sha256"]),
    )
    result["reason"] = "无法采集当前 GPU 可用显存"
    return result
  status = GpuQualificationStatus.GPU_AVAILABLE.value
  result["status"] = status
  result["qualification"] = qualification_projection(
    status,
    acceleration=float(qualification["speedup"]),
    minimum_sample_count=int(qualification["minimum_sample_count"]),
    peak_memory_fraction=float(qualification["peak_memory_fraction"]),
    gates_passed=True,
    evidence_sha256=str(qualification["evidence_sha256"]),
  )
  result["requirement_hash"] = requirement_hash
  return result


def _safe_output(path: str | Path) -> Path:
  candidate = Path(path)
  _reject_path_links(candidate)
  if candidate.exists() and _is_link_like(candidate):
    raise ValueError("GPU 资格输出路径不允许符号链接或联接点")
  candidate.parent.mkdir(parents=True, exist_ok=True)
  _reject_path_links(candidate.parent)
  return candidate


def _reject_path_links(path: Path) -> None:
  absolute = Path(os.path.abspath(path))
  current = Path(absolute.anchor)
  for component in absolute.parts[1:]:
    current /= component
    if _is_link_like(current):
      raise ValueError(f"GPU 资格路径不允许符号链接或联接点: {current.name}")


def _valid_public_build_evidence(payload: Mapping[str, Any]) -> bool:
  if payload.get("schema_version") == 2:
    return bool(
      set(payload) == _OFFICIAL_WHEEL_FIELDS
      and payload.get("source") == "pypi-official-wheel"
      and payload.get("lightgbm_version") == "4.6.0"
      and payload.get("wheel_sha256") == _OFFICIAL_GPU_WHEEL_SHA256
      and isinstance(payload.get("binary_sha256"), str)
      and _HASH_RE.fullmatch(payload["binary_sha256"])
      and payload.get("platform") == "Windows"
      and payload.get("use_gpu") is True
    )
  return bool(
    set(payload) == _PUBLIC_BUILD_EVIDENCE_FIELDS
    and payload.get("schema_version") == 1
    and payload.get("lightgbm_version") == "4.7.0"
    and payload.get("wheel_metadata_version") == "4.7.0"
    and payload.get("use_gpu") is True
  )


def _load_official_wheel_evidence(path: Path) -> tuple[str, dict[str, Any]]:
  """Verify the pinned upstream artifact and the binary actually loaded."""
  import zipfile

  from lightgbm.libpath import _find_lib_path

  _reject_path_links(path)
  if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != _OFFICIAL_GPU_WHEEL_SHA256:
    raise ValueError("官方 GPU wheel SHA-256 与锁定版本不匹配")
  binary = Path(_find_lib_path()[0])
  _reject_path_links(binary)
  with zipfile.ZipFile(path) as archive:
    wheel_binary = archive.read("lightgbm/bin/lib_lightgbm.dll")
  if getattr(lgb, "__version__", None) != "4.6.0" or binary.read_bytes() != wheel_binary:
    raise ValueError("已安装 LightGBM 二进制与官方 GPU wheel 不一致")
  public = {
    "schema_version": 2,
    "source": "pypi-official-wheel",
    "lightgbm_version": "4.6.0",
    "wheel_sha256": _OFFICIAL_GPU_WHEEL_SHA256,
    "binary_sha256": hashlib.sha256(wheel_binary).hexdigest(),
    "platform": "Windows",
    "use_gpu": True,
  }
  return stable_json_sha256(public), public


def _load_build_evidence(
  value: str | Path | Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]] | None:
  """Load strict schema-v1 wheel evidence without retaining host paths."""

  if value is None:
    return None
  if isinstance(value, Mapping):
    payload = dict(value)
  else:
    path = Path(value)
    if path.suffix.lower() == ".whl":
      return _load_official_wheel_evidence(path)
    _reject_path_links(path)
    if _is_link_like(path) or not path.is_file():
      raise ValueError("GPU build evidence 不是安全普通文件")
    try:
      payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda token: (_ for _ in ()).throw(
          ValueError(f"GPU build evidence 不允许非有限值: {token}")
        ),
      )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
      raise ValueError("GPU build evidence 无法读取") from exc
  if not isinstance(payload, dict) or set(payload) != _BUILD_EVIDENCE_FIELDS:
    raise ValueError("GPU build evidence 必须是完整 schema-v1 字段")
  if (
    payload.get("schema_version") != 1
    or payload.get("lightgbm_version") != "4.7.0"
    or payload.get("wheel_metadata_version") != "4.7.0"
    or payload.get("platform") != "Windows"
    or payload.get("use_gpu") is not True
  ):
    raise ValueError("GPU build evidence 必须对应 Windows LightGBM 4.7.0 GPU wheel")
  wheel = payload.get("wheel")
  if (
    not isinstance(wheel, str)
    or not wheel
    or Path(wheel).name != wheel
    or "\\" in wheel
  ):
    raise ValueError("GPU build evidence wheel 必须是单层文件名")
  wheel_hash = payload.get("wheel_sha256")
  if not isinstance(wheel_hash, str) or not _HASH_RE.fullmatch(wheel_hash):
    raise ValueError("GPU build evidence wheel_sha256 必须是小写 SHA-256")
  if not isinstance(value, Mapping):
    import zipfile

    from lightgbm.libpath import _find_lib_path

    wheel_path = Path(value).parent / wheel
    _reject_path_links(wheel_path)
    if (
      not wheel_path.is_file()
      or hashlib.sha256(wheel_path.read_bytes()).hexdigest() != wheel_hash
    ):
      raise ValueError("GPU wheel 文件与构建证据哈希不匹配")
    binary = Path(_find_lib_path()[0])
    with zipfile.ZipFile(wheel_path) as archive:
      entries = [
        name for name in archive.namelist() if name.endswith("/" + binary.name)
      ]
      if len(entries) != 1 or archive.read(entries[0]) != binary.read_bytes():
        raise ValueError("已安装 LightGBM 二进制与资格 wheel 不一致")
  definitions = payload.get("cmake_definitions")
  if not isinstance(definitions, list) or any(
    not isinstance(item, str) for item in definitions
  ):
    raise ValueError("GPU build evidence cmake_definitions 非法")
  if "--config-settings=cmake.define.USE_GPU=ON" not in definitions:
    raise ValueError("GPU build evidence 缺少官方 CMake USE_GPU=ON 配置")
  for key in (
    "cmake",
    "visual_studio_cl",
    "source_revision",
    "python",
    "python_abi",
    "built_at_utc",
  ):
    if not isinstance(payload.get(key), str):
      raise ValueError(f"GPU build evidence {key} 非法")
  for key in (
    "boost_root_configured",
    "boost_librarydir_configured",
    "opencl_include_dir_configured",
    "opencl_library_configured",
  ):
    if not isinstance(payload.get(key), bool):
      raise ValueError(f"GPU build evidence {key} 非法")
  evidence_hash = stable_json_sha256(payload)
  public = {
    "schema_version": 1,
    "lightgbm_version": "4.7.0",
    "wheel_sha256": wheel_hash,
    "wheel_metadata_version": "4.7.0",
    "use_gpu": True,
    "platform": "Windows",
    "cmake_gpu_enabled": True,
    "source_revision": payload["source_revision"][:128],
    "cmake": payload["cmake"][:128],
    "visual_studio_cl": payload["visual_studio_cl"][:128],
  }
  return evidence_hash, public


def _top20_overlap(left: Sequence[float], right: Sequence[float]) -> float:
  if len(left) == 0 or len(right) == 0:
    return 0.0
  top_count = min(20, len(left), len(right))
  if top_count <= 0:
    return 0.0
  left_order = set(np.argsort(np.asarray(left))[-top_count:])
  right_order = set(np.argsort(np.asarray(right))[-top_count:])
  return len(left_order & right_order) / float(top_count)


def _simple_ece(labels: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
  edges = np.linspace(0.0, 1.0, bins + 1)
  total = len(labels)
  if not total:
    return 0.0
  result = 0.0
  for index in range(bins):
    mask = (
      (probabilities >= edges[index])
      & (probabilities <= edges[index + 1] if index == bins - 1 else probabilities < edges[index + 1])
    )
    if not mask.any():
      continue
    result += float(mask.mean()) * abs(float(labels[mask].mean()) - float(probabilities[mask].mean()))
  return result


def _memory_fraction(*snapshots: Mapping[str, Any]) -> float | None:
  fractions: list[float] = []
  for snapshot in snapshots:
    try:
      total = float(snapshot.get("memory_total_mib"))
      free = float(snapshot.get("memory_free_mib"))
      if total <= 0 or not 0 <= free <= total:
        continue
      fraction = 1.0 - free / total
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
      continue
    if math.isfinite(fraction) and 0.0 <= fraction <= 1.0:
      fractions.append(fraction)
  return max(fractions) if fractions else None


def _monitor_host_gpu_memory() -> None:
  monitor_training_gpu_memory(lambda: _memory_fraction(_run_nvidia_smi()))


def _start_memory_sampler(
  *, interval_seconds: float = 0.15
) -> tuple[threading.Event, threading.Thread, list[float]]:
  """Sample GPU usage during the complete fit/predict/reload window."""
  _monitor_host_gpu_memory()

  stop = threading.Event()
  samples: list[float] = []

  def sample() -> None:
    while not stop.is_set():
      fraction = _memory_fraction(_run_nvidia_smi())
      if fraction is not None:
        samples.append(fraction)
      stop.wait(interval_seconds)

  thread = threading.Thread(target=sample, name="quantx-gpu-memory", daemon=True)
  thread.start()
  return stop, thread, samples


def _benchmark_gate_conclusion(labels: np.ndarray, probabilities: np.ndarray) -> str:
  if labels.size == 0 or probabilities.size != labels.size:
    return GateConclusion.BLOCKED.value
  baseline = float(labels.mean())
  brier = float(np.mean((probabilities - labels) ** 2))
  baseline_brier = float(np.mean((baseline - labels) ** 2))
  top_count = min(20, len(labels))
  top_indices = np.argsort(probabilities)[-top_count:]
  top_lift = float(labels[top_indices].mean() - baseline)
  if baseline_brier <= brier or _simple_ece(labels, probabilities) > 0.03 or top_lift <= 0:
    return GateConclusion.BLOCKED.value
  # The benchmark has no historical-universe membership evidence, therefore
  # its passing conclusion is intentionally shadow-only.
  return GateConclusion.SHADOW_ELIGIBLE.value


def _default_backend_trial(
  panel: pd.DataFrame,
  *,
  device_type: str,
  gpu_use_dp: bool = False,
) -> dict[str, Any]:
  """Small deterministic benchmark hook used by the qualification command."""

  if lgb is None:
    raise RuntimeError("LightGBM Python wheel unavailable")
  from quantx_domain.selection_factors import selection_feature_columns

  # Match real training features; numeric outcome columns contain future data.
  feature_columns = list(selection_feature_columns())
  if any(column not in panel.columns for column in feature_columns):
    raise ValueError("黄金面板缺少训练特征")
  if not feature_columns:
    raise ValueError("黄金面板没有数值特征")
  frame = panel.sort_values(["event_date", "stock_code"], kind="mergesort")
  split = max(1, int(len(frame) * 0.8))
  train, test = frame.iloc[:split], frame.iloc[split:]
  if test.empty:
    test = train
  x_train = train[feature_columns].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy()
  x_test = test[feature_columns].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy()
  y_train = train["label"].to_numpy(dtype=int)
  y_test = test["label"].to_numpy(dtype=float)
  sampler: tuple[threading.Event, threading.Thread, list[float]] | None = None
  if device_type == "gpu":
    sampler = _start_memory_sampler()
  started = time.perf_counter()
  params: dict[str, Any] = {
    "objective": "binary",
    "n_jobs": training_cpu_threads(),
    "n_estimators": 40,
    "num_leaves": 15,
    "learning_rate": 0.03,
    "max_bin": 63,
    "device_type": device_type,
    "verbosity": -1,
    "random_state": 20260901,
  }
  if device_type == "gpu":
    params["gpu_use_dp"] = bool(gpu_use_dp)
  try:
    model = lgb.LGBMClassifier(**params)
    model.fit(x_train, y_train)
    probabilities = np.asarray(model.predict_proba(x_test)[:, 1], dtype=float)
    model_loadable_on_cpu = False
    reload_difference: float | None = None
    if device_type == "gpu":
      try:
        with tempfile.TemporaryDirectory(prefix="quantx-lgb-gpu-") as directory:
          model_path = Path(directory) / "lightgbm.txt"
          model.booster_.save_model(str(model_path))
          cpu_model = lgb.Booster(model_file=str(model_path))
          cpu_probabilities = np.asarray(cpu_model.predict(x_test), dtype=float)
          model_loadable_on_cpu = bool(np.isfinite(cpu_probabilities).all())
          if model_loadable_on_cpu and cpu_probabilities.shape == probabilities.shape:
            reload_difference = float(np.max(np.abs(cpu_probabilities - probabilities)))
            model_loadable_on_cpu = reload_difference <= 1e-6
      except Exception:
        model_loadable_on_cpu = False
  finally:
    if sampler is not None:
      sampler[0].set()
      sampler[1].join(timeout=1.0)
      final_fraction = _memory_fraction(_run_nvidia_smi())
      if final_fraction is not None:
        sampler[2].append(final_fraction)
  memory_fraction = max(sampler[2]) if sampler and sampler[2] else None
  elapsed = max(time.perf_counter() - started, 1e-9)
  brier = float(np.mean((probabilities - y_test) ** 2))
  gate_conclusion = _benchmark_gate_conclusion(y_test, probabilities)
  return {
    "probabilities": probabilities.tolist(),
    "labels": y_test.tolist(),
    "brier": brier,
    "ece": _simple_ece(y_test, probabilities),
    "elapsed_seconds": elapsed,
    "top20": np.argsort(probabilities)[-min(20, len(probabilities)):].tolist(),
    "model_loadable_on_cpu": model_loadable_on_cpu if device_type == "gpu" else True,
    "cpu_reload_max_abs_difference": reload_difference,
    "peak_memory_fraction": memory_fraction,
    "memory_sampling_available": bool(sampler is None or sampler[2]),
    "gate_conclusion": gate_conclusion,
    "no_non_finite": bool(
      np.isfinite(probabilities).all() and np.isfinite(y_test).all()
    ),
  }


def qualify_lightgbm_gpu(
  dataset_dir: str | Path,
  output: str | Path | None = None,
  *,
  requirement_hash: str | None = None,
  trial_runner: Callable[..., dict[str, Any]] | None = None,
  repeat_count: int = 3,
  build_evidence: str | Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
  """Run CPU/FP32(/FP64) parity checks and write a qualification certificate.

  ``trial_runner`` is injectable so CI can exercise all gates without a GPU.
  The default runner performs the real LightGBM benchmark when an OpenCL
  wheel is installed.
  """

  from quantx_research.next_day_selection_dataset import load_certified_dataset_manifest

  _reject_path_links(Path(dataset_dir))
  manifest = load_certified_dataset_manifest(dataset_dir)
  loaded_build_evidence = _load_build_evidence(build_evidence)
  build_evidence_hash = loaded_build_evidence[0] if loaded_build_evidence else None
  build_evidence_public = loaded_build_evidence[1] if loaded_build_evidence else None
  root = Path(dataset_dir).resolve(strict=True)
  from quantx_domain.selection_factors import selection_feature_columns

  panel = pd.read_parquet(
    root / "training-panel.parquet",
    columns=["event_date", "stock_code", "label", *selection_feature_columns()],
  )
  if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count < 3:
    raise ValueError("GPU FP32/FP64 至少需要三次重复运行")
  runner = trial_runner or _default_backend_trial

  def run_trial(device_type: str, gpu_use_dp: bool) -> dict[str, Any]:
    try:
      value = runner(panel, device_type=device_type, gpu_use_dp=gpu_use_dp)
    except Exception as exc:
      return {
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:300],
        "no_non_finite": False,
      }
    if not isinstance(value, dict):
      return {"error_type": "InvalidTrialResult", "no_non_finite": False}
    return value

  cpu = run_trial("cpu", False)
  fp32 = [run_trial("gpu", False) for _ in range(repeat_count)]
  # FP64 is part of the qualification contract.  A runner that cannot execute
  # it yields explicit failed trials and therefore cannot certify the GPU.
  fp64 = [run_trial("gpu", True) for _ in range(repeat_count)]

  def finite_metric(item: Mapping[str, Any], key: str) -> float | None:
    try:
      value = float(item[key])
    except (KeyError, TypeError, ValueError):
      return None
    return value if math.isfinite(value) else None

  def vector(item: Mapping[str, Any], key: str) -> np.ndarray | None:
    try:
      values = np.asarray(item[key], dtype=float)
    except (KeyError, TypeError, ValueError):
      return None
    return values if values.ndim == 1 and values.size and np.isfinite(values).all() else None

  cpu_probabilities = vector(cpu, "probabilities")
  cpu_labels = vector(cpu, "labels")
  all_gpu = [*fp32, *fp64]
  fp32_probabilities = [vector(item, "probabilities") for item in fp32]
  fp64_probabilities = [vector(item, "probabilities") for item in fp64]

  def labels_match(item: Mapping[str, Any]) -> bool:
    labels = vector(item, "labels")
    return bool(
      cpu_labels is not None
      and labels is not None
      and labels.shape == cpu_labels.shape
      and np.array_equal(labels, cpu_labels)
    )

  labels_valid = bool(
    cpu_labels is not None
    and np.isin(cpu_labels, (0.0, 1.0)).all()
    and all(labels_match(item) for item in all_gpu)
  )

  def metric_values(items: Sequence[Mapping[str, Any]], key: str) -> list[float] | None:
    values = [finite_metric(item, key) for item in items]
    return [value for value in values if value is not None] if all(value is not None for value in values) else None

  cpu_brier = finite_metric(cpu, "brier")
  cpu_ece = finite_metric(cpu, "ece")
  fp32_brier = metric_values(fp32, "brier")
  fp64_brier = metric_values(fp64, "brier")
  fp32_ece = metric_values(fp32, "ece")
  fp64_ece = metric_values(fp64, "ece")

  def max_relative(values: Sequence[float] | None, baseline: float | None) -> float:
    if values is None or baseline is None:
      return math.inf
    return max(
      abs(value - baseline) / max(abs(baseline), 1e-12)
      for value in values
    )

  def max_absolute(values: Sequence[float] | None, baseline: float | None) -> float:
    if values is None or baseline is None:
      return math.inf
    return max(abs(value - baseline) for value in values)

  brier_relative = max_relative(fp32_brier, cpu_brier)
  ece_absolute = max_absolute(fp32_ece, cpu_ece)
  fp64_brier_relative = max_relative(fp64_brier, cpu_brier)
  fp64_ece_absolute = max_absolute(fp64_ece, cpu_ece)

  def matching_probability_arrays(values: Sequence[np.ndarray | None]) -> bool:
    return bool(
      cpu_probabilities is not None
      and all(
        item is not None
        and item.shape == cpu_probabilities.shape
        for item in values
      )
    )

  overlap = (
    min(_top20_overlap(cpu_probabilities, item) for item in fp32_probabilities if item is not None)
    if matching_probability_arrays(fp32_probabilities)
    else 0.0
  )
  fp64_overlap = (
    min(_top20_overlap(cpu_probabilities, item) for item in fp64_probabilities if item is not None)
    if matching_probability_arrays(fp64_probabilities)
    else 0.0
  )

  def repeat_consistent(values: Sequence[np.ndarray | None]) -> bool:
    if not values or any(item is None for item in values):
      return False
    first = values[0]
    assert first is not None
    return all(
      item is not None
      and item.shape == first.shape
      and np.max(np.abs(item - first)) <= 1e-6
      for item in values[1:]
    )

  repeat_consistent_value = repeat_consistent(fp32_probabilities) and repeat_consistent(fp64_probabilities)
  cpu_elapsed = finite_metric(cpu, "elapsed_seconds")
  fp32_elapsed = metric_values(fp32, "elapsed_seconds")
  speedups = (
    [1.0 - value / cpu_elapsed for value in fp32_elapsed]
    if cpu_elapsed is not None and cpu_elapsed > 0 and fp32_elapsed is not None
    and all(value > 0 for value in fp32_elapsed)
    else []
  )
  speedup = min(speedups) if speedups else 0.0

  memory_values: list[float] = []
  memory_complete = True
  memory_sampling_available = True
  for item in all_gpu:
    memory = finite_metric(item, "peak_memory_fraction")
    if memory is None or memory < 0.0 or memory > 1.0 or item.get("memory_sampling_available") is not True:
      memory_complete = False
    else:
      memory_values.append(memory)
    memory_sampling_available = memory_sampling_available and item.get("memory_sampling_available") is True
  peak_memory_fraction = max(memory_values) if memory_values else None

  reload_values = [finite_metric(item, "cpu_reload_max_abs_difference") for item in fp32]
  fp64_reload_values = [finite_metric(item, "cpu_reload_max_abs_difference") for item in fp64]
  model_cpu_loadable = bool(
    all(item.get("model_loadable_on_cpu") is True for item in all_gpu)
    and reload_values
    and fp64_reload_values
    and all(value is not None and value <= 1e-6 for value in reload_values)
    and all(value is not None and value <= 1e-6 for value in fp64_reload_values)
  )
  cpu_reload_max = max((value for value in reload_values if value is not None), default=None)
  fp64_cpu_reload_max = max((value for value in fp64_reload_values if value is not None), default=None)

  trial_conclusions = [cpu.get("gate_conclusion")] + [
    item.get("gate_conclusion") for item in all_gpu
  ]
  valid_conclusions = {item.value for item in GateConclusion}
  conclusion_not_flipped = bool(
    all(value in valid_conclusions for value in trial_conclusions)
    and len(set(trial_conclusions)) == 1
  )
  cpu_shape_valid = bool(
    cpu_labels is not None
    and cpu_probabilities is not None
    and cpu_probabilities.shape == cpu_labels.shape
  )
  no_non_finite = bool(
    labels_valid
    and cpu_shape_valid
    and cpu.get("no_non_finite") is True
    and all(
      item.get("no_non_finite") is True
      and value is not None
      and cpu_probabilities is not None
      and value.shape == cpu_probabilities.shape
      for item, value in zip(all_gpu, [*fp32_probabilities, *fp64_probabilities])
    )
  )

  # Keep evidence redacted to scalars and public trial facts.  Arrays are
  # deliberately represented by hashes/counts in the certificate.
  def public_trial(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
      key: value
      for key, value in item.items()
      if key not in {"probabilities", "labels"}
    }

  evidence = {
    "dataset_version": manifest["dataset_version"],
    "dataset_manifest_hash": manifest["manifest_sha256"],
    "panel_sha256": manifest["training_panel_sha256"],
    "cpu": public_trial(cpu),
    "fp32": [public_trial(item) for item in fp32],
    "fp64": [public_trial(item) for item in fp64],
    "repeat_consistent": repeat_consistent_value,
    "no_non_finite": no_non_finite,
    "conclusion_not_flipped": conclusion_not_flipped,
    "cpu_gate_conclusion": cpu.get("gate_conclusion"),
    "gpu_gate_conclusions": [item.get("gate_conclusion") for item in all_gpu],
    "labels_count": int(cpu_labels.size) if cpu_labels is not None else 0,
    "labels_match": labels_valid,
    "memory_complete": memory_complete,
    "memory_sampling_available": memory_sampling_available,
    "build_evidence_sha256": build_evidence_hash,
  }
  requirement = requirement_hash or gpu_requirement_hash()
  payload: dict[str, Any] = {
    "schema_version": 1,
    "qualification_version": GPU_QUALIFICATION_VERSION,
    "status": GpuQualificationStatus.GPU_UNQUALIFIED.value,
    "requirement_hash": requirement,
    "evidence": evidence,
    "evidence_sha256": stable_json_sha256(evidence),
    "brier_relative_difference": brier_relative,
    "ece_absolute_difference": ece_absolute,
    "fp64_brier_relative_difference": fp64_brier_relative,
    "fp64_ece_absolute_difference": fp64_ece_absolute,
    "top20_overlap": overlap,
    "fp64_top20_overlap": fp64_overlap,
    "speedup": speedup,
    "minimum_sample_count": _minimum_sample_count_from_manifest(manifest),
    "build_evidence_sha256": build_evidence_hash,
    "build_evidence": build_evidence_public,
    "peak_memory_fraction": peak_memory_fraction,
    "memory_sampling_available": memory_sampling_available,
    "model_cpu_loadable": model_cpu_loadable,
    "cpu_reload_max_abs_difference": cpu_reload_max,
    "fp64_cpu_reload_max_abs_difference": fp64_cpu_reload_max,
    "no_non_finite": no_non_finite,
    "conclusion_not_flipped": conclusion_not_flipped,
    "repeat_consistent": repeat_consistent_value,
    "repeat_count": repeat_count,
    "fp32_repeat_count": len(fp32),
    "fp64_runs": len(fp64),
    "environment": {
      **_environment_evidence(),
      "build_evidence_sha256": build_evidence_hash,
      "build": build_evidence_public,
    },
  }
  qualifies = bool(
    brier_relative <= GPU_BRIER_RELATIVE_TOLERANCE
    and ece_absolute <= GPU_ECE_ABSOLUTE_TOLERANCE
    and fp64_brier_relative <= GPU_BRIER_RELATIVE_TOLERANCE
    and fp64_ece_absolute <= GPU_ECE_ABSOLUTE_TOLERANCE
    and overlap >= GPU_TOP20_OVERLAP_MINIMUM
    and fp64_overlap >= GPU_TOP20_OVERLAP_MINIMUM
    and speedup >= GPU_MIN_SPEEDUP
    and memory_complete
    and memory_sampling_available
    and peak_memory_fraction is not None
    and peak_memory_fraction <= DEFAULT_GPU_MAX_MEMORY_FRACTION
    and model_cpu_loadable
    and no_non_finite
    and repeat_consistent_value
    and conclusion_not_flipped
    and build_evidence_hash is not None
    and build_evidence_public is not None
  )
  payload["status"] = (
    GpuQualificationStatus.GPU_AVAILABLE.value
    if qualifies
    else GpuQualificationStatus.GPU_UNQUALIFIED.value
  )
  payload["integrity"] = {
    "algorithm": "sha256",
    "kind": "UNSIGNED_DIGEST",
    "signed": False,
    "digest": _integrity_digest(payload),
  }
  output_path = _safe_output(output or _default_qualification_path())
  fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
  os.close(fd)
  try:
    write_json(Path(temporary), payload)
    _reject_path_links(Path(temporary))
    _reject_path_links(output_path)
    _reject_path_links(output_path.parent)
    os.replace(temporary, output_path)
  except BaseException:
    try:
      os.unlink(temporary)
    except OSError:
      pass
    raise
  return payload


__all__ = [
  "GPU_BRIER_RELATIVE_TOLERANCE",
  "GPU_ECE_ABSOLUTE_TOLERANCE",
  "GPU_MIN_SPEEDUP",
  "GPU_QUALIFICATION_VERSION",
  "GPU_TOP20_OVERLAP_MINIMUM",
  "gpu_requirement_hash",
  "probe_lightgbm_gpu",
  "qualify_lightgbm_gpu",
]
