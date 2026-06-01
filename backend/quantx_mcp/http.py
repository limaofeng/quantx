"""
MCP HTTP integration for QuantX.

Provides ASGI apps for MCP over HTTP (streamable-http or SSE) to be mounted
under the main FastAPI application.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Literal

from config.mcp_config import mcp_settings

from .server import create_mcp_server

HTTPMode = Literal["sse", "streamable-http", "http"]


def _normalize_mode(mode: str) -> HTTPMode:
    value = mode.lower().strip()
    if value in ("sse", "streamable-http", "http"):
        return value  # type: ignore[return-value]
    raise ValueError(f"Unsupported MCP HTTP mode: {mode}")


def create_mcp_http_app(
    *,
    mode: str,
    sse_path: str = "/sse",
    message_path: str = "/messages/",
) -> object:
    """
    Create MCP HTTP ASGI app.

    Args:
        mode: "sse" or "streamable-http" (alias: "http")
        sse_path: SSE endpoint path when mode="sse"
        message_path: message POST endpoint when mode="sse"
    """
    normalized_mode = _normalize_mode(mode)
    server = create_mcp_server()

    if normalized_mode == "sse":
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.routing import Mount, Route

        from mcp.server.sse import SseServerTransport

        sse_transport = SseServerTransport(message_path)

        async def sse_endpoint(request: Request) -> Response:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send  # type: ignore[reportPrivateUsage]
            ) as streams:
                await server.app.run(
                    streams[0],
                    streams[1],
                    server.app.create_initialization_options(),
                )
            return Response()

        routes = [
            Route(sse_path, endpoint=sse_endpoint, methods=["GET"]),
            Mount(message_path, app=sse_transport.handle_post_message),
        ]

        async def sse_lifespan() -> AsyncIterator[None]:
            yield

        return Starlette(routes=routes), asynccontextmanager(sse_lifespan)

    import asyncio
    import logging

    from mcp.server.fastmcp.server import StreamableHTTPASGIApp
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette

    logger = logging.getLogger(__name__)

    session_manager = StreamableHTTPSessionManager(
        app=server.app,
        json_response=False,
        stateless=False,
        security_settings=None,
        retry_interval=None,
    )

    # 创建底层 MCP 应用
    mcp_asgi = StreamableHTTPASGIApp(session_manager)

    # Session manager 管理器 - 将在外部 lifespan 中管理
    class MCPHTTPApp:
        def __init__(self, session_manager, mcp_asgi):
            self.session_manager = session_manager
            self.mcp_asgi = mcp_asgi
            self._session_started = False
            self._starting = False
            self._stopping = False
            self._stop_event = None
            self._task = None
            self._active_tasks: set[asyncio.Task] = set()

        async def _terminate_active_sessions(self):
            instances = getattr(self.session_manager, "_server_instances", None)
            if not instances:
                return
            for session_id, transport in list(instances.items()):
                try:
                    await transport.terminate()
                except Exception as exc:
                    logger.warning(f"Failed to terminate MCP session {session_id}: {exc}")
            instances.clear()

        async def start(self):
            """启动 session manager"""
            if self._session_started or self._starting:
                logger.info("MCP HTTP session manager already running")
                return
            logger.info("Starting MCP HTTP session manager")
            self._starting = True
            self._stopping = False
            self._stop_event = asyncio.Event()

            async def run_session_manager():
                try:
                    async with self.session_manager.run():
                        self._session_started = True
                        logger.info("MCP session manager started")
                        try:
                            await self._stop_event.wait()
                        except asyncio.CancelledError:
                            logger.info("Session manager received cancellation")
                            raise
                except asyncio.CancelledError:
                    logger.info("Session manager cancelled")
                    raise
                finally:
                    self._session_started = False
                    self._starting = False
                    self._stopping = False
                    logger.info("MCP session manager stopped")

            self._task = asyncio.create_task(run_session_manager())

            # 等待启动完成
            for _ in range(50):
                await asyncio.sleep(0.1)
                if self._session_started:
                    break
                if self._task.done():
                    break

            if not self._session_started:
                if self._task and self._task.done():
                    try:
                        exc = self._task.exception()
                    except asyncio.CancelledError:
                        exc = None
                    if exc:
                        logger.error(f"MCP session manager failed to start: {exc}")
                self._starting = False
                raise RuntimeError("Failed to start MCP session manager")
            self._starting = False

        async def stop(self):
            """停止 session manager"""
            if self._stopping:
                logger.info("MCP HTTP session manager is stopping")
                return
            if not self._task:
                self._session_started = False
                return
            logger.info("Stopping MCP HTTP session manager")
            self._stopping = True

            # 1. 先设置停止事件,让 run_session_manager 退出等待
            if self._stop_event:
                self._stop_event.set()

            # 2. 终止活动会话(带超时)
            try:
                await asyncio.wait_for(self._terminate_active_sessions(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("终止活动会话超时")

            # 3. 取消所有活跃的 ASGI 请求任务（不等待，避免 gather 本身被取消导致死锁）
            if self._active_tasks:
                active_tasks = list(self._active_tasks)
                logger.info(f"Cancelling {len(active_tasks)} active MCP HTTP requests")
                for t in self._active_tasks:
                    t.cancel()
                await asyncio.gather(*active_tasks, return_exceptions=True)
                self._active_tasks.clear()

            # 4. 等待任务结束(带超时)
            if self._task:
                try:
                    await asyncio.wait_for(self._task, timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning("Session manager stop timed out, cancelling task")
                    self._task.cancel()
                    try:
                        await asyncio.wait_for(self._task, timeout=1.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        logger.warning("Session manager force cancelled")
                except asyncio.CancelledError:
                    logger.warning("Session manager stop cancelled")

            self._session_started = False
            self._stopping = False
            self._starting = False
            self._stop_event = None
            self._task = None

        @property
        def is_running(self) -> bool:
            return self._session_started

        # ASGI 接口
        async def __call__(self, scope, receive, send):
            """ASGI 入口"""
            # 等待 session manager 启动（短暂等待，避免关停时阻塞）
            if not self._session_started and self._starting and not self._stopping:
                for _ in range(20):
                    if self._session_started or self._stopping:
                        break
                    await asyncio.sleep(0.1)

            if not self._session_started or self._stopping:
                # Session manager 未启动
                if scope['type'] == 'http':
                    response = {
                        'type': 'http.response.start',
                        'status': 503,
                        'headers': [[b'content-type', b'text/plain']],
                    }
                    await send(response)
                    body = b'MCP session manager not initialized'
                    await send({
                        'type': 'http.response.body',
                        'body': body,
                        'more_body': False,
                    })
                return

            # 转发到实际的 MCP 应用，并追踪任务以便关闭时取消
            current_task = asyncio.current_task()
            if current_task:
                self._active_tasks.add(current_task)
            try:
                await self.mcp_asgi(scope, receive, send)
            except asyncio.CancelledError:
                if self._stopping:
                    logger.info("MCP HTTP request cancelled during shutdown")
                    return
                raise
            except BaseException as exc:
                if self._stopping:
                    logger.warning(
                        "MCP HTTP request failed during shutdown: %s",
                        repr(exc),
                    )
                    return
                raise
            finally:
                self._active_tasks.discard(current_task)

    mcp_app = MCPHTTPApp(session_manager, mcp_asgi)

    # 创建简单的 Starlette 应用来处理路由
    starlette_app = Starlette()

    starlette_app.add_route("/", mcp_app, methods=["GET", "POST", "OPTIONS"])
    starlette_app.add_route("/{path:path}", mcp_app, methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"])

    # 将 session manager 管理器附加到 app 上，以便外部访问
    starlette_app.mcp_manager = mcp_app

    return starlette_app


def is_http_mode_enabled() -> bool:
    """Check if MCP is enabled and configured for HTTP mode."""
    if not mcp_settings.enabled:
        return False
    return mcp_settings.mode.lower().strip() in ("sse", "streamable-http", "http")
