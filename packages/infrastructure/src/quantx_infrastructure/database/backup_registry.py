"""Record completed backup age in the operational database projection."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from quantx_domain.clock import utcnow
from sqlalchemy import update

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import AccountTradingRollout


async def record_backup(manifest_path: str) -> None:
  path = Path(manifest_path).resolve()
  if not path.is_file():
    raise ValueError("backup manifest does not exist")
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(AccountTradingRollout).values(last_backup_at=utcnow())
    )
    await db.commit()
  print(json.dumps({"recorded": True, "manifest": str(path)}))


if __name__ == "__main__":
  if len(sys.argv) != 2:
    raise SystemExit("usage: python -m ...backup_registry MANIFEST")
  asyncio.run(record_backup(sys.argv[1]))
