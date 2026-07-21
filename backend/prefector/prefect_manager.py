"""
Prefect Worker 管理器

负责在 FastAPI 应用中管理 Prefect Worker，连接到外部 Prefect 服务器
使用外部服务模式：Prefect Server 独立运行，此管理器只启动和管理 Worker
"""

import asyncio
import inspect
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from prefect import get_client
from config.settings import settings

logger = logging.getLogger(__name__)


class PrefectServiceManager:
  """Prefect 服务管理器 - 外部服务模式，只管理 worker"""

  def __init__(self):
    self.enabled = getattr(settings, "prefect_enabled", True)
    self.worker_process: Optional[subprocess.Popen] = None
    self.is_running = False
    self.worker_pool_name = getattr(settings, "prefect_worker_pool", "my-docker-pool")
    self._output_reader_task: Optional[asyncio.Task] = None
    self.worker_state_file = self._resolve_worker_state_file()

    # 确保日志目录存在
    reports_dir = Path(getattr(settings, "sync_reports_dir", "logs/sync_reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

  def _resolve_worker_state_file(self) -> Optional[Path]:
    path_text = os.environ.get("QUANTX_PREFECT_WORKER_STATE_FILE", "").strip()
    if not path_text:
      return None
    return Path(path_text)

  def _write_worker_state(self, cmd: list[str], cwd: Path) -> None:
    if not self.worker_state_file or not self.worker_process:
      return

    state = {
      "kind": "prefect-worker",
      "pid": self.worker_process.pid,
      "parent_pid": os.getpid(),
      "worker_pool": self.worker_pool_name,
      "cwd": str(cwd),
      "command": cmd,
      "started_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
      self.worker_state_file.parent.mkdir(parents=True, exist_ok=True)
      temp_path = self.worker_state_file.with_suffix(
        f"{self.worker_state_file.suffix}.tmp"
      )
      temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
      )
      temp_path.replace(self.worker_state_file)
    except Exception as e:
      logger.warning(f"写入 Prefect Worker 状态文件失败: {e}")

  def _remove_worker_state(self) -> None:
    if not self.worker_state_file:
      return

    try:
      self.worker_state_file.unlink(missing_ok=True)
    except Exception as e:
      logger.warning(f"删除 Prefect Worker 状态文件失败: {e}")

  async def start(self):
    """启动 Prefect Worker"""
    if not self.enabled:
      logger.info("Prefect 服务已禁用")
      return

    # 如果已有初始化任务在运行，则等待其完成
    if hasattr(self, "_init_task") and not self._init_task.done():
      logger.info("Prefect Worker 初始化任务已在运行，等待其完成...")
      try:
        await self._init_task
      except Exception as e:
        logger.error(f"等待已有 Prefect Worker 初始化任务失败: {e}")
      return

    # 启动后台任务来启动 Worker
    self.is_running = True
    self._init_task = asyncio.create_task(self._start_worker())

    # 同步回调，用于记录结果并清理状态
    def _on_init_done(task: asyncio.Task):
      try:
        exc = task.exception()
        if exc:
          logger.error(f"Prefect Worker 启动任务异常: {exc}")
          self.is_running = False
        else:
          logger.info("Prefect Worker 启动任务已完成")
      except asyncio.CancelledError:
        logger.info("Prefect Worker 启动任务被取消")
        self.is_running = False

    self._init_task.add_done_callback(_on_init_done)

  async def stop(self):
    """停止 Prefect Worker"""
    if not self.enabled:
      return
    init_task = getattr(self, "_init_task", None)
    if (
      not self.is_running
      and not self.worker_process
      and not (init_task and not init_task.done())
      and not (self._output_reader_task and not self._output_reader_task.done())
    ):
      return

    try:
      logger.info("正在停止 Prefect Worker...")

      # 取消初始化任务
      if hasattr(self, "_init_task") and not self._init_task.done():
        self._init_task.cancel()
        try:
          await self._init_task
        except asyncio.CancelledError:
          logger.info("Prefect Worker 初始化任务已取消")

      # 停止工作进程
      if self.worker_process:
        logger.info(f"正在终止 Worker 进程 (PID: {self.worker_process.pid})")

        # Windows 特殊处理: 使用 CTRL_BREAK_EVENT
        if sys.platform == "win32":
          try:
            # 向进程组发送 CTRL_BREAK 信号
            import signal

            self.worker_process.send_signal(signal.CTRL_BREAK_EVENT)
            logger.debug("已发送 CTRL_BREAK_EVENT 信号")
          except Exception as e:
            logger.warning(f"发送 CTRL_BREAK_EVENT 失败: {e}, 使用 terminate")
            self.worker_process.terminate()
        else:
          self.worker_process.terminate()

        try:
          self.worker_process.wait(timeout=10)
          logger.info("Worker 进程已正常终止")
        except subprocess.TimeoutExpired:
          logger.warning("Worker 进程未能在 10 秒内正常退出，强制终止")
          self.worker_process.kill()
          self.worker_process.wait()  # 等待进程真正结束
        finally:
          # 清理 stdout 管道,防止资源泄漏
          if self.worker_process.stdout:
            self.worker_process.stdout.close()

        self.worker_process = None

      # 等待输出读取任务在进程退出/管道关闭后自然结束。这个任务内部使用
      # 默认线程池执行阻塞 readline；先取消它会留下一个无法被取消的 executor
      # future，进而导致 uvicorn 退出时等待默认线程池卡住。
      if self._output_reader_task and not self._output_reader_task.done():
        try:
          await asyncio.wait_for(self._output_reader_task, timeout=2)
        except asyncio.TimeoutError:
          self._output_reader_task.cancel()
          try:
            await self._output_reader_task
          except asyncio.CancelledError:
            logger.info("Worker 输出读取任务已取消")
        except asyncio.CancelledError:
          logger.info("Worker 输出读取任务已取消")
      self._output_reader_task = None

      self.is_running = False
      logger.info("Prefect Worker 已停止")
      self._remove_worker_state()

    except Exception as e:
      logger.error(f"停止 Prefect Worker 失败: {e}")

  async def _start_worker(self):
    """启动 Prefect Worker"""
    try:
      logger.info(f"启动 Prefect Worker，连接到工作池: {self.worker_pool_name}")

      # 确保工作池存在（不存在则尝试创建）
      if not await self._ensure_work_pool(self.worker_pool_name):
        logger.warning(
          f"工作池 {self.worker_pool_name} 不可用，将继续启动 Worker（可能导致连接失败）"
        )

      # 获取可选 conda 可执行文件路径；未配置环境名时使用当前 Python
      conda_exe = os.environ.get("CONDA_EXE")
      conda_env = (getattr(settings, "conda_env_name", "") or "").strip()

      if conda_exe and conda_env:
        logger.debug(f"使用 conda 环境: {conda_env}")
        logger.debug(f"使用 conda 路径: {conda_exe}")
        cmd = [
          conda_exe,
          "run",
          "-n",
          conda_env,
          "python",
          "-m",
          "prefect",
          "worker",
          "start",
          "--pool",
          self.worker_pool_name,
        ]
      else:
        # 回退到使用 sys.executable
        logger.debug("未配置 conda 环境，使用当前 Python 解释器启动 Worker")
        cmd = [
          sys.executable,
          "-m",
          "prefect",
          "worker",
          "start",
          "--pool",
          self.worker_pool_name,
        ]

      logger.debug(f"Worker 启动命令: {' '.join(cmd)}")

      # 启动 Worker 进程
      env = os.environ.copy()
      worker_cwd = Path(__file__).parent.parent

      # Windows 特殊处理: 创建新的进程组以便正确处理信号
      creation_flags = 0
      if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

      self.worker_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=worker_cwd,
        env=env,
        creationflags=creation_flags,
      )

      # 等待一小段时间确保进程启动
      await asyncio.sleep(3)

      # 检查进程是否正常运行
      if self.worker_process.poll() is None:
        logger.info(f"Prefect Worker 启动成功，PID: {self.worker_process.pid}")
        self._write_worker_state(cmd, worker_cwd)
        # 启动后台任务读取输出,防止管道阻塞
        self._output_reader_task = asyncio.create_task(self._read_worker_output())
      else:
        logger.error("Prefect Worker 启动失败")
        self._remove_worker_state()
        # 输出启动日志用于调试
        if self.worker_process and self.worker_process.stdout:
          try:
            output = self.worker_process.stdout.read(1000).decode(
              "utf-8", errors="ignore"
            )
            if output:
              logger.error(f"Worker 启动输出: {output}")
          except Exception:
            pass
        raise Exception("Worker 进程启动失败")

      # 可选：部署 flows（如果需要的话）
      deploy_flows = getattr(settings, "prefect_auto_deploy_flows", True)
      if deploy_flows:
        if self.worker_process and self.worker_process.poll() is None:
          await self._deploy_flows()
        else:
          logger.warning("Worker 未正常运行，跳过部署 flows")

    except Exception as e:
      logger.error(f"启动 Prefect Worker 失败: {e}")
      raise

  async def _read_worker_output(self):
    """持续读取 Worker 进程输出,防止管道阻塞"""
    try:
      if not self.worker_process or not self.worker_process.stdout:
        return

      loop = asyncio.get_running_loop()
      # 使用线程池执行阻塞的 readline
      while self.worker_process.poll() is None:
        try:
          line = await loop.run_in_executor(None, self.worker_process.stdout.readline)
          if not line:
            break
          # 可选：记录 Worker 输出（仅在调试模式下）
          if settings.debug:
            decoded = line.decode("utf-8", errors="ignore").strip()
            if decoded:
              logger.debug(f"[Prefect Worker] {decoded}")
        except Exception as e:
          logger.debug(f"读取 Worker 输出时出错: {e}")
          break
    except asyncio.CancelledError:
      logger.debug("Worker 输出读取任务被取消")
    except Exception as e:
      logger.error(f"读取 Worker 输出失败: {e}")
    finally:
      if self.worker_process and self.worker_process.poll() is not None:
        self._remove_worker_state()

  async def _deploy_flows(self):
    """部署所有可用的 flows"""
    try:
      logger.info("开始部署 Prefect flows...")

      # 导入部署管理器注册表
      from .flow_deployment_manager import flow_deployment_registry

      # 获取单例实例
      deployment_manager = flow_deployment_registry.get_manager()

      # 部署所有 flows
      results = await deployment_manager.deploy_all_flows()

      logger.info(
        f"Flow 部署完成: 成功 {len(results['success'])}, 失败 {len(results['failed'])}, 跳过 {len(results['skipped'])}"
      )

      # 记录详细结果
      if results["failed"]:
        logger.warning("部署失败的 flows:")
        for failed in results["failed"]:
          logger.warning(
            f"  - {failed['name']}: {failed.get('error', 'Unknown error')}"
          )

    except Exception as e:
      import traceback

      traceback.print_exc()
      logger.error(f"Flow 部署失败: {e}")

  async def _ensure_work_pool(self, work_pool_name: str) -> bool:
    """确保工作池存在；若不存在则尝试创建"""
    try:
      async with get_client() as client:
        if await self._read_work_pool(client, work_pool_name):
          return True

        created = await self._create_work_pool(client, work_pool_name)
        if not created:
          logger.error(f"创建工作池失败: {work_pool_name}")
          return False

        # 再次验证
        if await self._read_work_pool(client, work_pool_name):
          logger.info(f"已创建工作池: {work_pool_name}")
          return True

        logger.warning(f"工作池创建后仍无法读取: {work_pool_name}")
        return True
    except Exception as e:
      logger.error(f"确保工作池存在时出错: {e}", exc_info=True)
      return False

  def _is_work_pool_not_found(self, error: Exception) -> bool:
    message = str(error).lower()
    return "not found" in message or "404" in message or "work pool" in message

  async def _read_work_pool(self, client, work_pool_name: str) -> bool:
    try:
      await client.read_work_pool(work_pool_name)
      logger.debug(f"工作池已存在: {work_pool_name}")
      return True
    except Exception as e:
      if not self._is_work_pool_not_found(e):
        logger.error(f"检查工作池失败: {e}", exc_info=True)
      return False

  async def _create_work_pool(self, client, work_pool_name: str) -> bool:
    method = getattr(client, "create_work_pool", None)
    if not method:
      logger.error("Prefect client 不支持 create_work_pool")
      return False

    # 优先尝试 WorkPoolCreate
    try:
      from prefect.client.schemas.actions import WorkPoolCreate

      payload = WorkPoolCreate(name=work_pool_name, type="process")
      try:
        sig = inspect.signature(method)
        if "work_pool" in sig.parameters:
          await method(work_pool=payload)
          return True
        if len(sig.parameters) == 1:
          await method(payload)
          return True
      except TypeError:
        # 某些版本可能不接受上述参数签名
        pass
      except Exception as e:
        logger.error(f"创建工作池出错: {e}", exc_info=True)
        return False
    except Exception:
      # WorkPoolCreate 不可用时继续回退
      pass

    # 回退到常见签名
    for kwargs in (
      {"name": work_pool_name, "type": "process"},
      {"name": work_pool_name, "type": "process", "description": "auto-created"},
    ):
      try:
        sig = inspect.signature(method)
        if "name" in sig.parameters:
          await method(**kwargs)
          return True
        if len(sig.parameters) == 1:
          await method(kwargs)
          return True
        await method(**kwargs)
        return True
      except TypeError:
        continue
      except Exception as e:
        logger.error(f"创建工作池出错: {e}", exc_info=True)
        return False

    return False

  def get_status(self) -> dict:
    """获取 Prefect 服务状态"""
    return {
      "enabled": self.enabled,
      "running": self.is_running,
      "worker_running": self.worker_process is not None
      and self.worker_process.poll() is None,
      "worker_pool": self.worker_pool_name,
      "settings": {
        "worker_pool_name": self.worker_pool_name,
        "conda_env_name": getattr(settings, "conda_env_name", ""),
        "auto_deploy_flows": getattr(settings, "prefect_auto_deploy_flows", True),
        "timezone": getattr(settings, "timezone", "N/A"),
      },
    }


# 全局实例
prefect_manager = PrefectServiceManager()
