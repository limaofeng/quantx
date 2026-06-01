"""
策略订阅相关的 GraphQL 类型定义

用于策略运行实例的实时数据订阅：
- 市场数据订阅（Tick/K线）
- 日志订阅
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

import strawberry


@strawberry.enum(description="策略市场数据类型")
class StrategyDataType(Enum):
    TICK = "TICK"
    KLINE = "KLINE"


@strawberry.enum(description="日志级别")
class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@strawberry.type(description="策略 Tick 数据")
class StrategyTickData:
    stock_code: str = strawberry.field(description="股票代码")
    last_price: float = strawberry.field(description="最新价")
    volume: int = strawberry.field(description="成交量")
    amount: float = strawberry.field(description="成交额")
    bid_price: Optional[float] = strawberry.field(description="买一价", default=None)
    ask_price: Optional[float] = strawberry.field(description="卖一价", default=None)
    bid_volume: Optional[int] = strawberry.field(description="买一量", default=None)
    ask_volume: Optional[int] = strawberry.field(description="卖一量", default=None)
    open_price: Optional[float] = strawberry.field(description="开盘价", default=None)
    high_price: Optional[float] = strawberry.field(description="最高价", default=None)
    low_price: Optional[float] = strawberry.field(description="最低价", default=None)
    pre_close: Optional[float] = strawberry.field(description="昨收价", default=None)
    time: datetime = strawberry.field(description="时间戳")


@strawberry.type(description="策略 K线数据")
class StrategyKLineData:
    stock_code: str = strawberry.field(description="股票代码")
    period: str = strawberry.field(description="周期，如 1m, 5m, 1d")
    open: float = strawberry.field(description="开盘价")
    close: float = strawberry.field(description="收盘价")
    high: float = strawberry.field(description="最高价")
    low: float = strawberry.field(description="最低价")
    volume: int = strawberry.field(description="成交量")
    amount: float = strawberry.field(description="成交额")
    time: datetime = strawberry.field(description="时间戳")


@strawberry.type(description="策略市场数据事件")
class StrategyMarketDataEvent:
    run_id: str = strawberry.field(description="策略运行实例ID")
    data_type: StrategyDataType = strawberry.field(description="数据类型")
    timestamp: datetime = strawberry.field(description="事件时间戳")
    tick: Optional[StrategyTickData] = strawberry.field(
        description="Tick数据（当 dataType=TICK 时）", default=None
    )
    kline: Optional[StrategyKLineData] = strawberry.field(
        description="K线数据（当 dataType=KLINE 时）", default=None
    )

    @classmethod
    def from_tick(cls, run_id: str, tick) -> "StrategyMarketDataEvent":
        """从 Tick 对象创建事件"""
        from core.utils import time_utils

        return cls(
            run_id=run_id,
            data_type=StrategyDataType.TICK,
            timestamp=time_utils.now(),
            tick=StrategyTickData(
                stock_code=tick.stock_code,
                last_price=tick.last_price,
                volume=tick.volume,
                amount=getattr(tick, "amount", 0.0),
                bid_price=getattr(tick, "bid_price", None),
                ask_price=getattr(tick, "ask_price", None),
                bid_volume=getattr(tick, "bid_volume", None),
                ask_volume=getattr(tick, "ask_volume", None),
                open_price=getattr(tick, "open_price", None),
                high_price=getattr(tick, "high_price", None),
                low_price=getattr(tick, "low_price", None),
                pre_close=getattr(tick, "pre_close", None),
                time=tick.time,
            ),
            kline=None,
        )

    @classmethod
    def from_kline(cls, run_id: str, kline) -> "StrategyMarketDataEvent":
        """从 KLine 对象创建事件"""
        from core.utils import time_utils

        return cls(
            run_id=run_id,
            data_type=StrategyDataType.KLINE,
            timestamp=time_utils.now(),
            tick=None,
            kline=StrategyKLineData(
                stock_code=kline.stock_code,
                period=getattr(kline, "period", "1m"),
                open=kline.open,
                close=kline.close,
                high=kline.high,
                low=kline.low,
                volume=kline.volume,
                amount=getattr(kline, "amount", 0.0),
                time=kline.time,
            ),
        )


@strawberry.type(description="策略日志条目")
class StrategyLogEntry:
    run_id: str = strawberry.field(description="策略运行实例ID")
    timestamp: datetime = strawberry.field(description="日志时间戳")
    level: LogLevel = strawberry.field(description="日志级别")
    message: str = strawberry.field(description="日志消息")
    source: str = strawberry.field(description="日志来源（策略名称/模块）")

    @classmethod
    def create(
        cls,
        run_id: str,
        level: LogLevel,
        message: str,
        source: str = "strategy",
    ) -> "StrategyLogEntry":
        """创建日志条目"""
        from core.utils import time_utils

        return cls(
            run_id=run_id,
            timestamp=time_utils.now(),
            level=level,
            message=message,
            source=source,
        )

    @classmethod
    def from_record(cls, run_id: str, record: dict) -> "StrategyLogEntry":
        """从 JSONL 文件记录创建日志条目。"""
        raw_level = str(record.get("level") or "INFO").upper()
        try:
            level = LogLevel[raw_level]
        except KeyError:
            level = LogLevel.INFO

        raw_timestamp = (
            record.get("timestamp")
            or record.get("_timestamp")
            or datetime.now().isoformat()
        )
        if isinstance(raw_timestamp, datetime):
            timestamp = raw_timestamp
        else:
            try:
                timestamp = datetime.fromisoformat(
                    str(raw_timestamp).replace("Z", "+00:00")
                )
            except ValueError:
                timestamp = datetime.now()

        return cls(
            run_id=str(record.get("run_id") or run_id),
            timestamp=timestamp,
            level=level,
            message=str(record.get("message") or ""),
            source=str(record.get("source") or "strategy"),
        )


@strawberry.type(description="策略执行日志分页结果")
class StrategyLogPage:
    run_id: str = strawberry.field(description="策略运行实例ID")
    mode: Optional[str] = strawberry.field(default=None, description="运行模式")
    backtest_id: Optional[str] = strawberry.field(default=None, description="回测ID")
    backtest_version: Optional[int] = strawberry.field(default=None, description="回测版本")
    source_path: Optional[str] = strawberry.field(default=None, description="日志文件相对路径")
    start_cursor: int = strawberry.field(description="本页第一行游标")
    end_cursor: int = strawberry.field(description="本页后一行游标")
    has_previous_page: bool = strawberry.field(description="是否还有更早日志")
    has_next_page: bool = strawberry.field(description="是否还有更新日志")
    total_lines: int = strawberry.field(description="日志文件总行数")
    file_size_bytes: int = strawberry.field(description="日志文件大小")
    entries: List[StrategyLogEntry] = strawberry.field(default_factory=list, description="日志条目")


__all__ = [
    "StrategyDataType",
    "LogLevel",
    "StrategyTickData",
    "StrategyKLineData",
    "StrategyMarketDataEvent",
    "StrategyLogEntry",
    "StrategyLogPage",
]
