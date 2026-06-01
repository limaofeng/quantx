"""
策略日志广播处理器

使用 Python 标准 logging.Handler 机制，将策略日志转发到广播队列，
实现策略代码零侵入的日志订阅功能。
"""

import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from core.runtime_log_manager import RuntimeLogManager


class RuntimeLogBroadcastHandler(logging.Handler):
    """
    策略日志广播 Handler
    
    通过 Python 标准 logging 机制捕获策略日志，
    并将其转发到 StrategyRuntime 的广播队列。
    
    用法:
        handler = RuntimeLogBroadcastHandler(log_manager, run_id)
        strategy.logger.addHandler(handler)
    """
    
    # 日志级别映射: Python logging level -> LogLevel enum value
    LEVEL_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "ERROR",  # CRITICAL 也映射为 ERROR
    }

    def __init__(
        self,
        log_manager: "RuntimeLogManager",
        run_id: str,
        source: str = "strategy",
        level: int = logging.INFO,
        formatter: Optional[logging.Formatter] = None,
        filter_fn: Optional[Callable[[logging.LogRecord], bool]] = None,
        include_extra: bool = True,
    ):
        """
        初始化 Handler
        
        Args:
            log_manager: 策略日志管理器
            run_id: 策略运行实例ID
            source: 日志来源标识（默认为策略名称）
            level: 最低日志级别（默认 INFO）
            formatter: 日志格式器（默认仅输出 message）
            filter_fn: 可选过滤函数，返回 False 则丢弃
            include_extra: 是否附带扩展字段（run_id, strategy_name）
        """
        super().__init__()
        self.log_manager = log_manager
        self.run_id = run_id
        self.source = source
        self.filter_fn = filter_fn
        self.include_extra = include_extra
        self.setLevel(level)
        self.setFormatter(formatter or logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """
        处理日志记录
        
        Args:
            record: Python logging 的日志记录对象
        """
        try:
            if record.levelno < self.level:
                return
            if self.filter_fn is not None and not self.filter_fn(record):
                return

            # 获取日志级别
            level = self.LEVEL_MAP.get(record.levelno, "INFO")
            
            # 格式化消息
            message = self.format(record)
            
            # 检查是否为 SUCCESS 日志（通过前缀标记）
            if message.startswith("[SUCCESS]"):
                level = "SUCCESS"
                message = message[9:].strip()  # 移除前缀

            # 附带扩展字段（可选）
            if self.include_extra:
                extra = {"run_id": self.run_id}
                try:
                    message = f"{message} | extra={extra}"
                except Exception:
                    pass

            # 使用日志管理器广播
            self.log_manager.append(
                run_id=self.run_id,
                level=level,
                message=message,
                source=self.source,
            )
            
        except Exception:
            # 广播失败不应该影响策略运行
            self.handleError(record)


def attach_log_handler(
    runtime: Any,
    strategy: Any,
    *,
    level: int = logging.INFO,
    formatter: Optional[logging.Formatter] = None,
    filter_fn: Optional[Callable[[logging.LogRecord], bool]] = None,
    include_extra: bool = True,
) -> RuntimeLogBroadcastHandler:
    """
    为策略附加日志广播 Handler
    
    这是一个便捷函数，用于在策略初始化时附加日志 Handler。
    
    Args:
        runtime: 策略运行时对象
        strategy: 策略实例（需要有 logger 和 name 属性）
        
    Returns:
        附加的 Handler 实例，可用于后续移除
    """
    log_manager = getattr(runtime, "log_manager", None)
    if log_manager is None:
        raise ValueError("runtime.log_manager 未初始化，无法附加日志处理器")

    handler = log_manager.attach_handler(
        run_id=runtime.run_id,
        logger=strategy.logger,
        source=getattr(strategy, "name", "strategy"),
        level=level,
        formatter=formatter,
        filter_fn=filter_fn,
        include_extra=include_extra,
    )
    return handler


def detach_log_handler(strategy: Any, handler: RuntimeLogBroadcastHandler) -> None:
    """@deprecated 请使用 RuntimeLogManager.detach_handler"""
    try:
        strategy.logger.removeHandler(handler)
    except Exception:
        pass
