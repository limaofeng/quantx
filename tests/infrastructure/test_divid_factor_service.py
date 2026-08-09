from decimal import Decimal

import pandas as pd
from quantx_infrastructure.services.divid_factor_service import (
  DividFactorService,
)


def test_normalize_factors_quantizes_decimal_half_up_at_persistence_boundary():
  frame = pd.DataFrame(
    [
      {
        "time": 1_758_038_400_000,
        "interest": 0.34835,
        "stockBonus": 1.23445,
        "stockGift": 2.34555,
        "allotNum": 3.45665,
        "allotPrice": 4.56775,
        "gugai": 5.67885,
        "dr": 1.1234565,
      }
    ],
    index=["20250917"],
  )

  factors = DividFactorService()._normalize_factors("000739.SZ", frame)

  assert len(factors) == 1
  factor = factors[0]
  assert factor.interest == Decimal("0.3484")
  assert factor.stock_bonus == Decimal("1.2345")
  assert factor.stock_gift == Decimal("2.3456")
  assert factor.allot_num == Decimal("3.4567")
  assert factor.allot_price == Decimal("4.5678")
  assert factor.gugai == Decimal("5.6789")
  assert factor.dr == Decimal("1.123457")
