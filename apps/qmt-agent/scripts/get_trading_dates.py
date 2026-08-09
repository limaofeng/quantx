"""Local QMT trading-calendar diagnostic; never imported by server processes."""

from datetime import datetime
from typing import List

from xtquant import xtdata

# 测试参数
market = "SH"  # 上海市场
start_date = datetime(2025, 2, 1)
end_date = datetime(2025, 2, 28)
count = -1  # 获取所有日期

trading_dates: List[int] = xtdata.get_trading_dates(
    market=market,
    start_time=start_date.strftime("%Y%m%d") if start_date else "",
    end_time=end_date.strftime("%Y%m%d") if end_date else "",
    count=count,
)

print("Trading dates:", trading_dates)
