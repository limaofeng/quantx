import json

import pytest
from quantx_infrastructure.training_bundle_store import publication_lock
from quantx_trainer.service_exit import record_group_exit
from quantx_trainer.service_status import ServiceReporter, service_status


def evidence(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("private-config")
  root = tmp_path / "service"
  root.mkdir()
  reporter = ServiceReporter(root, config)
  reporter.write()
  instance = reporter.identity["instance_id"]
  record_group_exit(tmp_path, config, instance, forced=True)
  return root, config, instance


def test_receipt_survives_stop_caller_and_does_not_prove_database_state(tmp_path):
  root, config, instance = evidence(tmp_path)
  assert service_status(tmp_path, config) == {
    "service": "OFFLINE",
    "execution_state": "GROUP_EXITED",
    "database_state": "NOT_RECONCILED",
  }
  receipt = root / f"group-exit-{instance}.json"
  original = receipt.read_bytes()
  assert b"private-config" not in original
  with pytest.raises(ValueError, match="ALREADY_RECORDED"):
    record_group_exit(tmp_path, config, instance, forced=False)
  assert receipt.read_bytes() == original
  # Even a valid old receipt cannot override a currently held service lease.
  with publication_lock(root):
    assert service_status(tmp_path, config)["execution_state"] == "NOT_INSPECTED"


@pytest.mark.parametrize(
  "fault",
  [
    "new_instance",
    "config",
    "host",
    "active",
    "bool_count",
    "future",
    "corrupt",
    "link",
  ],
)
def test_inapplicable_or_corrupt_receipt_cannot_prove_group_exit(tmp_path, fault):
  root, config, instance = evidence(tmp_path)
  receipt = root / f"group-exit-{instance}.json"
  value = json.loads(receipt.read_text())
  if fault == "new_instance":
    ServiceReporter(root, config).write()
  elif fault == "config":
    config.write_text("changed")
  elif fault == "host":
    value["host"] = "other-host"
  elif fault == "active":
    value["active_processes"] = 1
  elif fault == "bool_count":
    value["active_processes"] = False
  elif fault == "future":
    value["observed_at"] += 60
  receipt.write_text(json.dumps(value))
  if fault == "corrupt":
    receipt.write_text("{")
  if fault == "link":
    receipt.unlink()
    receipt.symlink_to(config)
  assert service_status(tmp_path, config) == {
    "service": "OFFLINE",
    "execution_state": "NOT_INSPECTED",
  }
