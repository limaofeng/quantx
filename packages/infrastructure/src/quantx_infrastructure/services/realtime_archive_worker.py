"""Immutable external writes, exact content readback, then fenced publication."""

import asyncio

import pandas as pd
from quantx_contracts.realtime_archive import ArchiveProof, ArchiveRevision

from quantx_infrastructure.database.timeseries import get_timeseries_connection
from quantx_infrastructure.database.timeseries_connection import (
  NonRetryableWriteError,
  WriteError,
)
from quantx_infrastructure.database.timeseries_operations import (
  TimeSeriesOperations,
  single_write_attempt,
)

from .market_data_content_verification import verify_persisted_bar_content
from .market_data_persistence_verification import MarketDataPersistenceVerificationError
from .market_data_staging_cleanup import _joined_thread
from .realtime_archive_store import RealtimeArchiveStore


def frame(request):
  return pd.DataFrame(
    [
      {
        **request.bar.model_dump(),
        "stock_code": request.instrument,
        "period": "1m",
        "time": pd.Timestamp(request.minute),
      }
    ]
  )


def write(request, connection):
  values = frame(request)
  values["storage_version"] = request.storage_version()
  with single_write_attempt():
    TimeSeriesOperations(connection).write_dataframe(
      values,
      "kline_1m_versions",
      ["stock_code", "period", "storage_version"],
      batch_size=1,
    )


async def advance_realtime_archive(owner, *, connection=None):
  store = RealtimeArchiveStore(owner.engine)
  claim = await store.claim(owner)
  if claim is None:
    return False
  try:
    request = ArchiveRevision.model_validate(claim["request"])
    if request.identity() != claim["request_id"]:
      raise ValueError("archive persisted identity mismatch")
  except ValueError:
    await store.finish(owner, claim, phase="BLOCKED", reason="ARCHIVE_REQUEST_INVALID")
    return True
  connection = connection or get_timeseries_connection()
  proof = None
  phase = "READBACK" if claim["phase"] == "WRITE" else "VERIFIED"
  reason = None
  try:
    async with asyncio.timeout(30):
      if claim["phase"] == "WRITE":
        await _joined_thread(write, request, connection)
      else:

        async def expected():
          yield frame(request)

        proof = ArchiveProof(
          **(
            await verify_persisted_bar_content(
              expected(),
              connection=connection,
              storage_version=request.storage_version(),
            )
          ),
          storage_version=request.storage_version(),
        ).model_dump(mode="json")
  except NonRetryableWriteError:
    raise
  except (OSError, TimeoutError, WriteError, MarketDataPersistenceVerificationError):
    phase, reason = claim["phase"], "ARCHIVE_STORAGE_RETRY"
  except ValueError:
    phase, reason = "BLOCKED", "ARCHIVE_CONTENT_INVALID"
  await store.finish(owner, claim, phase=phase, reason=reason, proof=proof)
  return True
