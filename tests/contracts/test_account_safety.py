from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from quantx_contracts import (
  ACCOUNT_EXECUTION_SAFETY_CHECK_CODES,
  AccountSafetyCheckObservation,
  AccountSafetyCheckStatus,
  AccountSafetyObservationSnapshot,
)


def _checks() -> list[AccountSafetyCheckObservation]:
  return [
    AccountSafetyCheckObservation(
      code=code,
      status=AccountSafetyCheckStatus.PASSED,
      scope="INCREASE_RISK",
    )
    for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES
  ]


def test_ready_observation_requires_every_check_in_canonical_order():
  with pytest.raises(ValidationError, match="every check in order"):
    AccountSafetyObservationSnapshot(
      status="ready",
      observed_at=datetime.now(timezone.utc),
      checks=_checks()[:-1],
    )


def test_standby_requires_a_public_reason():
  with pytest.raises(ValidationError, match="require a public reason"):
    AccountSafetyCheckObservation(
      code="MARKET_STREAM_READY",
      status=AccountSafetyCheckStatus.STANDBY,
      scope="INCREASE_RISK",
    )
