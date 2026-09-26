"""P0-3 验收 — Deadline 贯穿 + wall-clock 真实终止

覆盖验收标准：
  5. remaining<=0 时 quota/slot/HTTP 各层均立即短路，不发出请求
  7. 【wall-clock】服务端持续分块返回、单次 read 未超时但总耗时超 case_deadline
     → 必须 deadline_exceeded，且底层连接被真实终止（非伪取消）
  3. remaining 传播：分项/HTTP 层均受 remaining 钳制
"""

import asyncio
import time

import httpx
import pytest

from app.exceptions import AIException, DeadlineExceeded
from app.services.ai_service import (
    _call_openai,
    _chat_http_attempt,
    _make_deadline,
    _request_timeout,
    _remaining,
)


class TestRemainingShortCircuit:
    """验收 #5：remaining<=0 各层立即短路，不发出请求"""

    def test_deadline_zero_raises_without_request(self, mock_settings, mocker):
        mock_settings("OPENAI_API_KEY", "test-key")
        posted = [0]

        async def _post(*a, **kw):
            posted[0] += 1
            return httpx.Response(200, json={}, request=httpx.Request("POST", "http://x"))

        mock_client = mocker.AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = _post
        mocker.patch("app.services.ai_service.httpx.AsyncClient", return_value=mock_client)

        with pytest.raises(DeadlineExceeded):
            _call_openai("prompt", "m", remaining=0.0)
        assert posted[0] == 0  # HTTP 请求未被发出

    def test_chat_http_attempt_remaining_zero_deadline(self, mocker):
        """直接调用 _chat_http_attempt(deadline<=now) → 立即 deadline_exceeded"""
        posted = [0]

        async def _post(*a, **kw):
            posted[0] += 1
            return httpx.Response(200, json={}, request=httpx.Request("POST", "http://x"))

        mock_client = mocker.AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = _post
        mocker.patch("app.services.ai_service.httpx.AsyncClient", return_value=mock_client)

        deadline = time.monotonic() - 0.1  # 已过期
        with pytest.raises(DeadlineExceeded):
            asyncio.run(_chat_http_attempt(
                model="m", messages=[], max_tokens=1, attempt=1, deadline=deadline,
            ))
        # 请求实际未被发出：wait_for(timeout=0) 在 post 体真正运行前即取消（真实终止）
        assert posted[0] == 0

    def test_request_timeout_short_circuits(self):
        """remaining<=0 → 分项超时全部归零，不跑默认值"""
        deadline = _make_deadline(0.0)
        t = _request_timeout(deadline)
        for field in ("connect", "write", "read", "pool"):
            assert getattr(t, field) <= 1e-6


class TestRemainingPropagation:
    """验收 #3：read timeout 受 remaining 钳制，不跑满 120s"""

    def test_request_timeout_read_bounded_by_small_remaining(self):
        deadline = _make_deadline(5.0)
        t = _request_timeout(deadline)
        assert t.read == pytest.approx(5.0, abs=0.2)
        assert t.read < 120.0

    def test_remaining_reflects_deadline(self):
        deadline = _make_deadline(3.0)
        assert 0 < _remaining(deadline) <= 3.0


class TestWallClockChunkedTermination:
    """验收 #7：持续分块返回、单次 read 未超时但总耗时超 deadline → 真实终止

    - chunked 真实服务器：服务端无限分块、单次 read 均 < read timeout；总耗时超
      case_deadline 必须得到 deadline_exceeded（而非 hung 或 read timeout）。这是
      '''只有分项 timeout、没有总 wall-clock deadline''' 做法的必抓场景。
    - 取消传播：证明 CancelledError 真正进入请求协程本体（区别于
      asyncio.wait_for(asyncio.to_thread(...)) 只取消等待方的伪取消）。
    """

    def test_chunked_stream_exceeds_deadline_produces_deadline_exceeded(
        self, mock_settings
    ):
        async def handler(reader, writer):
            try:
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Transfer-Encoding: chunked\r\n"
                    b"Connection: close\r\n\r\n"
                )
                await writer.drain()
                for _ in range(100_000):
                    body = b'{"choices":[{"message":{"content":"' + b"x" * 8 + b'"}}]}'
                    writer.write(b"%x\r\n" % len(body) + body + b"\r\n")
                    await writer.drain()
                    await asyncio.sleep(0.25)  # 每次 read 均远低于 read timeout(1s)
            except (BrokenPipeError, ConnectionResetError, OSError, asyncio.CancelledError):
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        async def scenario():
            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            mock_settings("OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")

            start = time.monotonic()
            raised = False
            try:
                await _chat_http_attempt(
                    model="m", messages=[], max_tokens=1, attempt=1,
                    deadline=_make_deadline(1.0),
                )
            except DeadlineExceeded:
                raised = True
            elapsed = time.monotonic() - start

            server.close()
            await server.wait_closed()
            return raised, elapsed

        raised, elapsed = asyncio.run(scenario())

        # 核心验收：无限分块 + 单次 read 未超时，累计超 deadline → 必须 deadline_exceeded
        assert raised is True, "持续分块累计超 deadline 必须抛 deadline_exceeded"
        # 证明受 case_deadline(=1s) 钳制，而非 read timeout(默认 120s) 或无限等待
        assert elapsed < 5.0, f"总耗时 {elapsed:.2f}s 应受 case_deadline 约束"

    def test_wallclock_cancellation_reaches_request_coroutine(self, mocker):
        """到期取消真正进入请求协程本体（区别于 to_thread 伪取消）"""
        cancelled = {"hit": False}

        async def _slow_post(*a, **kw):
            try:
                await asyncio.Future()  # 永不完成，模拟持续未完成的 HTTP 读取
            except asyncio.CancelledError:
                cancelled["hit"] = True
                raise

        mock_client = mocker.AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = _slow_post
        mocker.patch("app.services.ai_service.httpx.AsyncClient", return_value=mock_client)

        with pytest.raises(DeadlineExceeded):
            asyncio.run(_chat_http_attempt(
                model="m", messages=[], max_tokens=1, attempt=1,
                deadline=time.monotonic() + 0.2,
            ))
        # CancelledError 已进入请求协程本体的 except 分支 → 非伪取消
        assert cancelled["hit"] is True