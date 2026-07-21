"""Subprocess worker for financial batch sync.

Keeping xtdata financial reads in a child process prevents one wedged batch from
pinning the Prefect engine process forever.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from miniqmt import XTDataManagerRegistry
from services.financial_service import FinancialService

logger = logging.getLogger(__name__)


async def sync_financial_batch(
  stock_codes: List[str],
  request_id: Optional[str] = None,
) -> Dict[str, Any]:
  result: Dict[str, Any] = {
    "total": len(stock_codes),
    "success": 0,
    "failed": 0,
    "saved_count": 0,
    "status": "success",
    "request_id": request_id,
    "stock_codes": stock_codes,
  }

  if not stock_codes:
    return result

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()
  financial_data_map = data_manager.get_financial_data_list(stock_codes)

  if not financial_data_map:
    result["failed"] = len(stock_codes)
    result["status"] = "failed"
    result["error"] = "未获取到任何财务数据"
    return result

  service = FinancialService()
  total_saved = await service.save_batch_financial_data(financial_data_map)

  result["saved_count"] = total_saved
  result["success"] = len(financial_data_map)
  result["failed"] = result["total"] - result["success"]
  if result["failed"]:
    result["status"] = "partial"
  return result


def _read_input(path: Path) -> Dict[str, Any]:
  return json.loads(path.read_text(encoding="utf-8"))


def _write_output(path: Path, payload: Dict[str, Any]) -> None:
  path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--input", required=True)
  parser.add_argument("--output", required=True)
  args = parser.parse_args()

  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
  )

  input_path = Path(args.input)
  output_path = Path(args.output)
  stock_codes: List[str] = []

  try:
    payload = _read_input(input_path)
    stock_codes = payload.get("stock_codes") or []
    request_id = payload.get("request_id")
    result = asyncio.run(
      sync_financial_batch(stock_codes, request_id=request_id)
    )
    _write_output(output_path, result)
    return 0
  except Exception as exc:
    logger.exception("财务批次 worker 失败")
    _write_output(
      output_path,
      {
        "status": "failed",
        "total": len(stock_codes),
        "success": 0,
        "failed": len(stock_codes),
        "saved_count": 0,
        "request_id": payload.get("request_id") if "payload" in locals() else None,
        "stock_codes": stock_codes,
        "error": str(exc),
      },
    )
    return 1


if __name__ == "__main__":
  sys.exit(main())
