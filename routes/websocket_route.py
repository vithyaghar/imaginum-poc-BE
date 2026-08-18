from fastapi import APIRouter, WebSocket
from controllers.websocket import handle_websocket

router = APIRouter(prefix="/ws", tags=["Websocket"])


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    print(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
    await handle_websocket(websocket)
