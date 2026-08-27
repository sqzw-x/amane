"""Test WebSocket endpoints and EventBus"""

import threading
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from amane.api.routes.ws import router
from amane.events import Event, EventBus, EventType


class TestEventBus:
    """EventBus unit tests"""

    def test_initial_state(self):
        bus = EventBus()
        assert bus.connection_count == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_broadcast_no_connections(self):
        """Broadcasting with no connections should not raise"""
        bus = EventBus()
        event = Event(type=EventType.LOG, data={"message": "hello"})
        await bus.broadcast(event)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_emit_creates_event(self):
        """emit() is a convenience wrapper around broadcast()"""
        bus = EventBus()
        await bus.emit(EventType.TASK_STARTED, {"task_id": 1})

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        "bad_data",
        [
            pytest.param({"payload": {"use_cache": {"metadata", "trans"}}}, id="nested-set"),
            pytest.param({"value": {1, 2, 3}}, id="top-level-set"),
            pytest.param({"when": object()}, id="non-serializable-object"),
        ],
    )
    async def test_broadcast_non_serializable_keeps_connections(self, bad_data):
        """
        回归: 含非 JSON 类型的 event 不能被误判为"连接已死".

        旧 bug: send_json 序列化失败 → except 把连接当死连接移除 → 客户端 TCP 未断
        (无 onclose) 却再也收不到事件, 表现为前端"绿灯长亮但日志不更新". 修复后坏 event
        被丢弃, 连接存活, 后续正常 event 仍送达.
        """

        class _FakeWS:
            def __init__(self) -> None:
                self.sent: list[dict] = []

            async def send_json(self, data: dict) -> None:
                self.sent.append(data)

        bus = EventBus()
        ws = _FakeWS()
        bus._connections.append(ws)  # type: ignore[arg-type]

        await bus.broadcast(Event(type=EventType.LOG, data=bad_data))
        assert bus.connection_count == 1, "坏 event 不应移除连接"
        assert ws.sent == [], "坏 event 不应送达"

        await bus.broadcast(Event(type=EventType.LOG, data={"message": "ok"}))
        assert bus.connection_count == 1
        assert len(ws.sent) == 1, "坏 event 后正常 event 仍应送达"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_close_all_closes_and_clears(self) -> None:
        class _WS:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        bus = EventBus()
        ws = _WS()
        bus._connections.append(ws)  # type: ignore[arg-type]
        await bus.close_all()
        assert ws.closed
        assert bus.connection_count == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_close_all_swallows_close_errors(self) -> None:
        class _WS:
            async def close(self) -> None:
                raise RuntimeError("already gone")

        bus = EventBus()
        bus._connections.append(_WS())  # type: ignore[arg-type]
        await bus.close_all()
        assert bus.connection_count == 0


class TestWebSocketEndpoint:
    """/ws endpoint integration tests"""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def app(self, event_bus):
        """Create a minimal FastAPI app with ws router and runtime state"""
        app = FastAPI()
        app.include_router(router)
        app.state.runtime = type("Runtime", (), {"event_bus": event_bus, "api_token": None})()
        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_websocket_connect_registers(self, client, event_bus):
        """连接建立后 EventBus 注册该连接, 并能收到广播事件"""
        with client.websocket_connect("/ws"):
            # TestClient 同步上下文内连接已 accept; broadcast 经独立 emit 验证
            assert event_bus.connection_count == 1

    def test_websocket_disconnect(self, client, event_bus):
        """Test disconnect is handled correctly"""
        initial_count = event_bus.connection_count
        with client.websocket_connect("/ws"):
            assert event_bus.connection_count == initial_count + 1
        assert event_bus.connection_count == initial_count

    def test_event_type_values(self):
        """Verify event type string values"""
        assert EventType.TASK_STARTED == "task.started"
        assert EventType.TASK_PROGRESS == "task.progress"
        assert EventType.TASK_COMPLETED == "task.completed"
        assert EventType.TASK_FAILED == "task.failed"
        assert EventType.FILE_DISCOVERED == "file.discovered"
        assert EventType.FILE_REMOVED == "file.removed"
        assert EventType.LOG == "log"

    def test_event_dataclass(self):
        """Test Event dataclass creation"""
        event = Event(type=EventType.LOG, data={"msg": "test"})
        assert event.type == "log"
        assert event.data == {"msg": "test"}
        assert event.timestamp


class TestWorkerEventBroadcast:
    """验证 worker 执行任务时通过 lifespan EventBus 广播事件到 WebSocket 客户端"""

    def test_worker_events_reach_websocket(self, app, safe_path):
        """提交扫描任务, worker 处理后 WS 客户端收到 task.started / task.completed 事件"""
        scan_dir = safe_path / "videos"
        scan_dir.mkdir()

        with TestClient(app) as tc:
            # 先创建归属 library (FK + library-scoped scan)
            lib_resp = tc.post("/api/libraries", json={"path": str(scan_dir)})
            assert lib_resp.status_code == 201
            lib_id = lib_resp.json()["id"]
            # lifespan 自动进入, worker 已启动, EventBus 就绪
            with tc.websocket_connect("/api/ws") as ws:
                # 后台线程收集 WS 消息 (receive_json 阻塞)
                events: list[dict] = []
                stop = threading.Event()

                def collect():
                    try:
                        while not stop.is_set():
                            events.append(ws.receive_json())
                    except Exception:
                        pass

                t = threading.Thread(target=collect, daemon=True)
                t.start()

                # 提交 scan 任务
                resp = tc.post("/api/tasks", json={"type": "refresh", "library_id": lib_id})
                assert resp.status_code == 202

                # 等待 worker 处理并广播事件
                deadline = time.monotonic() + 10.0
                started = completed = False
                while time.monotonic() < deadline:
                    for e in events:
                        if e.get("type") == "task.started":
                            started = True
                        if e.get("type") == "task.completed":
                            completed = True
                    if started and completed:
                        break
                    time.sleep(0.05)

                stop.set()
                t.join(timeout=1.0)

                assert started, f"未收到 task.started 事件, 收到: {events}"
                assert completed, f"未收到 task.completed 事件, 收到: {events}"
