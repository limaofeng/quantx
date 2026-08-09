"""
策略日志管理器

负责：
- 生成策略 logger 并绑定广播 handler
- 维护每个策略的最近日志缓存（环形）
- 管理订阅者并广播实时日志
"""

import asyncio
import json
import logging
import os
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Set

from .runtime_log_broadcast_handler import RuntimeLogBroadcastHandler
from .runtime_log_types import RuntimeLogEntry, RuntimeLogLevel


class RuntimeLogManager:
    """运行时日志管理器"""

    def __init__(self, history_max: int = 50) -> None:
        self.history_max = max(1, int(history_max))
        self._history: Dict[str, Deque[RuntimeLogEntry]] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._handlers: Dict[str, RuntimeLogBroadcastHandler] = {}
        self._log_files: Dict[str, str] = {}
        self._file_locks: Dict[str, asyncio.Lock] = {}
        self._write_tasks: Dict[str, Set[asyncio.Task]] = {}

    def configure_file(self, *, run_id: str, file_path: Optional[str]) -> None:
        """绑定运行实例的日志文件。"""
        if not file_path:
            return

        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._log_files[run_id] = file_path

    def get_log_file_path(self, run_id: str) -> Optional[str]:
        """获取运行实例绑定的日志文件路径。"""
        return self._log_files.get(run_id)

    def get_logger(
        self,
        *,
        run_id: str,
        strategy_name: str,
        level: int = logging.INFO,
        formatter: Optional[logging.Formatter] = None,
        filter_fn: Optional[Callable[[logging.LogRecord], bool]] = None,
        include_extra: bool = True,
    ) -> logging.Logger:
        """获取策略 logger，并自动绑定广播 handler"""
        logger = logging.getLogger(f"Strategy-{run_id}")
        logger.setLevel(level)

        if run_id not in self._handlers:
            handler = RuntimeLogBroadcastHandler(
                log_manager=self,
                run_id=run_id,
                source=strategy_name,
                level=level,
                formatter=formatter,
                filter_fn=filter_fn,
                include_extra=include_extra,
            )
            logger.addHandler(handler)
            self._handlers[run_id] = handler

        return logger

    def attach_handler(
        self,
        *,
        run_id: str,
        logger: logging.Logger,
        source: str,
        level: int = logging.INFO,
        formatter: Optional[logging.Formatter] = None,
        filter_fn: Optional[Callable[[logging.LogRecord], bool]] = None,
        include_extra: bool = True,
    ) -> RuntimeLogBroadcastHandler:
        """为已有 logger 绑定广播 handler"""
        if run_id in self._handlers:
            return self._handlers[run_id]

        handler = RuntimeLogBroadcastHandler(
            log_manager=self,
            run_id=run_id,
            source=source,
            level=level,
            formatter=formatter,
            filter_fn=filter_fn,
            include_extra=include_extra,
        )
        logger.addHandler(handler)
        self._handlers[run_id] = handler
        return handler

    def detach_handler(self, run_id: str, logger: logging.Logger) -> None:
        """移除广播 handler"""
        handler = self._handlers.pop(run_id, None)
        if handler is None:
            return
        try:
            logger.removeHandler(handler)
        except Exception:
            pass

    def append(
        self,
        *,
        run_id: str,
        level: str,
        message: str,
        source: str = "strategy",
    ) -> RuntimeLogEntry:
        """追加日志并广播"""
        try:
            log_level = RuntimeLogLevel[level.upper()]
        except KeyError:
            log_level = RuntimeLogLevel.INFO

        log_entry = RuntimeLogEntry.create(
            run_id=run_id,
            level=log_level,
            message=message,
            source=source,
        )

        history = self._history.setdefault(
            run_id, deque(maxlen=self.history_max)
        )
        history.append(log_entry)
        self._append_file_record(log_entry)

        subscribers = self._subscribers.get(run_id, [])
        for queue in list(subscribers):
            try:
                queue.put_nowait(log_entry)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(log_entry)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

        return log_entry

    def _append_file_record(self, log_entry: RuntimeLogEntry) -> None:
        file_path = self._log_files.get(log_entry.run_id)
        if not file_path:
            return

        level_value = getattr(log_entry.level, "value", str(log_entry.level))
        record = {
            "run_id": log_entry.run_id,
            "timestamp": log_entry.timestamp.isoformat(),
            "level": level_value,
            "message": log_entry.message,
            "source": log_entry.source,
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write_line_sync(file_path, line)
            return

        task = loop.create_task(self._write_line_async(file_path, line))
        self._write_tasks.setdefault(log_entry.run_id, set()).add(task)
        task.add_done_callback(
            lambda done, run_id=log_entry.run_id: self._handle_write_task_result(
                run_id, done
            )
        )

    async def _write_line_async(self, file_path: str, line: str) -> None:
        lock = self._file_locks.setdefault(file_path, asyncio.Lock())
        async with lock:
            await asyncio.to_thread(self._write_line_sync, file_path, line)

    def _handle_write_task_result(self, run_id: str, task: asyncio.Task) -> None:
        tasks = self._write_tasks.get(run_id)
        if tasks is not None:
            tasks.discard(task)
        try:
            task.result()
        except Exception as exc:
            logging.getLogger(__name__).warning("写入策略日志文件失败: %s", exc)

    async def flush(self, run_id: Optional[str] = None) -> None:
        """等待已排队的文件日志写入完成。"""
        if run_id is None:
            tasks = [
                task
                for task_set in self._write_tasks.values()
                for task in task_set
                if not task.done()
            ]
        else:
            tasks = [
                task for task in self._write_tasks.get(run_id, set()) if not task.done()
            ]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _write_line_sync(file_path: str, line: str) -> None:
        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(file_path, mode="a", encoding="utf-8") as fp:
            fp.write(line)

    def get_recent(self, run_id: str, limit: Optional[int] = None) -> List[RuntimeLogEntry]:
        """获取最近日志"""
        history = self._history.get(run_id)
        if not history:
            return []
        if limit is None:
            return list(history)
        return list(history)[-max(0, int(limit)):]

    def subscribe(
        self,
        *,
        run_id: str,
        maxsize: int = 500,
        include_history: bool = True,
    ) -> asyncio.Queue:
        """订阅策略日志，返回专属队列"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.setdefault(run_id, []).append(queue)

        if include_history:
            for entry in self.get_recent(run_id):
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    break

        return queue

    def unsubscribe(self, *, run_id: str, queue: asyncio.Queue) -> None:
        """取消订阅"""
        subscribers = self._subscribers.get(run_id, [])
        self._subscribers[run_id] = [q for q in subscribers if q is not queue]

    def clear(self, run_id: str) -> None:
        """清空历史日志"""
        if run_id in self._history:
            self._history[run_id].clear()
