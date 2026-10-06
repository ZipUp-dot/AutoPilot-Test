"""P0-3B 验收 — Windows Proactor 下 deadline 硬看门狗真实终止（防挂起反回归）

背景：ai_service._chat_http_attempt 原用 asyncio.wait_for(client.post(...))。
在 Windows ProactorEventLoop 下，wait_for 超时调 task.cancel() 无法中止已发出的
overlapped socket recv，协程卡在 await recv_future 上永久挂起 → worker 线程占满
ThreadPoolExecutor(2) → 批量生成饿死（用户侧「生成中途中断」）。

修复：改 asyncio.wait(FIRST_COMPLETED) + deadline_task(纯定时器) + 到点主动
client.aclose() 关闭底层连接真正中止 I/O，且从不 await 被取消的 post_task。

本文件用 mock 探针锁定「取消免疫的 post」在 deadline 到点必须抛 DeadlineExceeded
且不挂起（墙钟 <5s），防止任何回归到 wait_for 伪取消的写法。
"""

import asyncio
import time

import httpx
import pytest

from app.exceptions import DeadlineExceeded
from app.services.ai_service import _chat_http_attempt, _make_deadline


class _MockClient:
    """手动 AsyncClient mock：新实现不走 __aenter__，须可 await aclose()"""

    def __init__(self, post_fn=None):
        self.post_fn = post_fn or (lambda *a, **kw: _never())
        self.closed = False

    async def post(self, *a, **kw):
        return await self.post_fn(*a, **kw)

    async def aclose(self):
        self.closed = True


def _never(*a, **kw):
    """永不完成任务（模拟挂起的 overlapped recv）"""
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    return fut


async def _cancel_immune_post(*a, **kw):
    """取消免疫 post：收到 CancelledError 后吞掉并继续 await 永不完成的 Future。

    这是 Windows Proactor 下 recv 挂起被 cancel 后实际行为的最坏情形模拟——
    如果实现依赖 post_task 被取消就能结束，就会在此挂死。
    """
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass  # 吞掉取消，执意继续等待 → 若实现 await 该任务将永不返回
    await asyncio.Future()


def _run(*, deadline, post_fn=None):
    client = _MockClient(post_fn)
    async def scenario():
        await _chat_http_attempt(
            model="m", messages=[], max_tokens=1, attempt=1,
            deadline=deadline, case_id=1,
        )
    # 重新补丁：_chat_http_attempt 内部用 httpx.AsyncClient，mock 需替换该符号
    import app.services.ai_service as mod
    orig = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = lambda *a, **kw: client
    try:
        start = time.monotonic()
        try:
            asyncio.run(scenario())
        except DeadlineExceeded:
            asyncio.run(asyncio.sleep(0))
            return None, time.monotonic() - start, client.closed
        raise AssertionError("应当抛 DeadlineExceeded")
    finally:
        mod.httpx.AsyncClient = orig


class TestWatchdogHardTermination:
    """核心：deadline 到点必须真实终止并抛 DeadlineExceeded，绝不挂起"""

    def test_cancel_immune_post_does_not_hang(self):
        _, elapsed, closed = _run(
            deadline=_make_deadline(0.4), post_fn=_cancel_immune_post)
        assert closed is True, "deadline 到点必须主动关闭 client 中止底层 I/O"
        assert elapsed < 5.0, f"取消免疫 post 不得挂起，实测 {elapsed:.2f}s"

    def test_never_completing_post_does_not_hang(self):
        _, elapsed, closed = _run(
            deadline=_make_deadline(0.4), post_fn=_never)
        assert closed is True
        assert elapsed < 5.0, f"永不完成 post 不得挂起，实测 {elapsed:.2f}s"

    def test_immediate_deadline_short_circuits(self):
        """remaining<=0：早期短路，不发请求（client 也无需创建到 socket）"""
        _, elapsed, closed = _run(deadline=_make_deadline(-0.1), post_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应发送")))
        assert elapsed < 5.0


class TestResponsesAfterDeadline:
    """200 响应虽返回但已超 deadline → 一律 deadline_exceeded"""

    def test_slow_ok_response_marked_deadline_exceeded(self):
        """响应在 deadline 之后才回来（早于 read timeout）→ deadline_exceeded"""
        async def _slow_ok(*a, **kw):
            # 先睡 0.3s 制造「已过期」的墙钟，再返回 200
            await asyncio.sleep(0.5)
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]},
                                  request=httpx.Request("POST", "http://x"))
        client = _MockClient(_slow_ok)
        import app.services.ai_service as mod
        orig = mod.httpx.AsyncClient
        mod.httpx.AsyncClient = lambda *a, **kw: client
        start = time.monotonic()
        error_type = None
        try:
            async def scenario():
                await _chat_http_attempt(
                    model="m", messages=[], max_tokens=1, attempt=1,
                    deadline=_make_deadline(0.2), case_id=1,
                )
            asyncio.run(scenario())
        except DeadlineExceeded:
            error_type = "deadline_exceeded"
        finally:
            mod.httpx.AsyncClient = orig
        elapsed = time.monotonic() - start
        # 已在 deadline(0.2) 之前被看门狗终止（而非等 read timeout），墙钟受钳制
        assert error_type == "deadline_exceeded"
        assert elapsed < 5.0