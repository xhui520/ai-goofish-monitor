"""
WebSocket 路由
提供实时通信功能
"""
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Set


router = APIRouter()

# 全局 WebSocket 连接管理
active_connections: Set[WebSocket] = set()

# 单次广播发送超时，避免半开连接挂起广播进而阻塞任务生命周期钩子
_BROADCAST_TIMEOUT_SECONDS = 5


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    """WebSocket 端点"""
    # 接受连接
    await websocket.accept()
    active_connections.add(websocket)

    try:
        # 保持连接并接收消息
        while True:
            # 接收客户端消息（如果有的话）
            data = await websocket.receive_text()
            # 这里可以处理客户端发送的消息
            # 目前我们主要用于服务端推送，所以暂时不处理
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception as e:
        print(f"WebSocket 错误: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


async def broadcast_message(message_type: str, data: dict):
    """向所有连接的客户端广播消息"""
    message = {
        "type": message_type,
        "data": data
    }

    # 快照当前连接：发送期间会有新连接加入/旧连接断开，
    # 直接迭代 set 会触发 "Set changed size during iteration"
    snapshot = list(active_connections)

    async def _send(connection: WebSocket) -> None:
        try:
            await asyncio.wait_for(
                connection.send_json(message), timeout=_BROADCAST_TIMEOUT_SECONDS
            )
        except Exception:
            active_connections.discard(connection)

    # 并发发送，僵死连接的超时等待不会串行拖长总耗时
    if snapshot:
        await asyncio.gather(*(_send(conn) for conn in snapshot))
