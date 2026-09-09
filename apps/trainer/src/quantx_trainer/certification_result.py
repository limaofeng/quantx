"""Register and publish verified certification output without repeating compute."""

import threading

from quantx_contracts.research_preparation import CertificationInputReference
from quantx_infrastructure.training_dataset_store import certification_values
from quantx_infrastructure.training_process_evidence import (
  inspect_execution,
  local_exit_recorded,
)
from quantx_infrastructure.training_result import safe_public_details

from quantx_trainer.dataset_transfer import publish_dataset
from quantx_trainer.preparation_flow import attempt_directory
from quantx_trainer.publication import _supervised_io, read_object


async def finalize_certification(config, preparation, datasets, job):
  """Caller holds the attempt lock; failures leave RUNNING for registration retry."""
  directory = attempt_directory(config, job)
  identity = dict(
    run_id=job.job_id, owner=job.flow_run_id, request=directory / "request.json"
  )
  evidence = directory / "process.json"
  if inspect_execution(evidence, **identity) != "EXITED" and not local_exit_recorded(
    evidence, **identity
  ):
    raise ValueError("CERTIFICATION_EXIT_UNCONFIRMED")
  process = read_object(evidence)
  if (
    process.get("state") != "EXITED"
    or type(process.get("returncode")) is not int
    or process["returncode"] != 0
  ):
    raise ValueError("CERTIFICATION_EXIT_UNCONFIRMED")
  reference = CertificationInputReference.model_validate(
    job.request["certification_input"]
  )
  version = job.request["dataset_version"]
  executed = read_object(directory / "request.json")
  executed_reference = CertificationInputReference.model_validate(
    executed.get("certification_input")
  )
  if (
    executed.get("kind") != "CERTIFY_FROZEN"
    or executed.get("dataset_version") != version
    or executed_reference.bundle.bundle_id != reference.bundle.bundle_id
    or executed_reference.manifest_sha256 != reference.manifest_sha256
  ):
    raise ValueError("CERTIFICATION_REQUEST_IDENTITY_MISMATCH")
  result = read_object(directory / "result.json")
  if (
    reference.bundle.source_id != version
    or result.get("ready") is not True
    or result.get("dataset_version") != version
    or result.get("input_manifest_sha256") != reference.manifest_sha256
  ):
    raise ValueError("CERTIFICATION_RESULT_IDENTITY_MISMATCH")

  async def check():
    await preparation.progress(job.job_id, expected_flow_run_id=job.flow_run_id)

  cancel = threading.Event()

  def validate():
    values = certification_values(
      dataset_version=version,
      manifest_sha256=result["manifest_sha256"],
      root=config.state_root / "datasets",
      cancel=cancel,
    )
    provenance = (
      values["quality_summary"]
      .get("coverage", {})
      .get("source", {})
      .get("source_provenance", {})
    )
    if (
      values["status"] != "CERTIFIED"
      or provenance.get("input_manifest_sha256") != reference.manifest_sha256
    ):
      raise ValueError("CERTIFICATION_OUTPUT_INPUT_MISMATCH")
    return values

  values = await _supervised_io(
    validate, preparation, job.job_id, job.flow_run_id, cancel, check=check
  )
  await check()
  await datasets.certify_dataset(values)
  await publish_dataset(config, datasets, dataset_version=version, check=check)
  await preparation.progress(
    job.job_id,
    expected_flow_run_id=job.flow_run_id,
    status="SUCCEEDED",
    phase="认证数据集已发布",
    result=safe_public_details(result),
    error=None,
  )
  return {"job_id": job.job_id, "status": "SUCCEEDED"}
