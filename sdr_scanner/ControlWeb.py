"""
FastAPI based Websocket for Scanner remote control. Bridges to the Scanner control queues.

- Exposes:
    WS   /control_ws    -> bidirectional message bus (UI->Scanner commands, Scanner->UI updates)
"""

import asyncio
from contextlib import asynccontextmanager
import json
import queue
import threading
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse

from .Scanner import Scanner

# Control Message Types we won't send via Websocket
SCANNER_IGNORE_MESSAGE_TYPES = [
    "ScanWindowStart",
    "ScanWindowDone",
]


def ws_json(obj: Any) -> str:
    # Convert into JSON-safe primitives
    return json.dumps(jsonable_encoder(obj))


class ScannerWeb:
    """
    Bridges Scanner queue-based message API to asyncio/WebSockets.
    """

    def __init__(self, scanner: Scanner, stopEvent: threading.Event):
        self.scanner = scanner

        self.scanner_to_ui: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.ui_to_scanner: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        # Scanner expects normal queue.Queue input/output queues
        self.scanner.addInputQueue(self.ui_to_scanner)
        self.scanner.addOutputQueue(self.scanner_to_ui)

        self._ws_clients: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._broadcast_q: Optional["asyncio.Queue[Dict[str, Any]]"] = None

        self._scanner_thread: Optional[threading.Thread] = None
        self._drain_thread: Optional[threading.Thread] = None
        self._stopEvent = stopEvent

    def start(self):
        # Start a drain thread to pull scanner_to_ui and push into asyncio queue
        self._drain_thread = threading.Thread(
            target=self._drain_runner,
            name="scanner-to-web-drain",
            daemon=True,
        )
        self._drain_thread.start()

    def attach_asyncio(self, loop: asyncio.AbstractEventLoop, broadcast_q: "asyncio.Queue[Dict[str, Any]]"):
        self._loop = loop
        self._broadcast_q = broadcast_q

    def _emit_to_asyncio(self, msg: Dict[str, Any]):
        if self._loop and self._broadcast_q:
            self._loop.call_soon_threadsafe(self._broadcast_q.put_nowait, msg)

    def _drain_runner(self):
        while not self._stopEvent.is_set():
            try:
                msg = self.scanner_to_ui.get(timeout=0.01)
            except queue.Empty:
                continue

            msgType = msg.get("type")
            if msgType in SCANNER_IGNORE_MESSAGE_TYPES:
                continue

            # Broadcast to clients
            self._emit_to_asyncio(msg)


def create_app(
        bridge: ScannerWeb,
        ) -> FastAPI:

    @asynccontextmanager
    async def websocketLifespan(app: FastAPI):

        ##########
        # Startup

        loop = asyncio.get_running_loop()
        broadcast_q: asyncio.Queue = asyncio.Queue()
        bridge.attach_asyncio(loop, broadcast_q)
        bridge.start()

        async def broadcaster():
            while True:
                msg = await broadcast_q.get()
                if not bridge._ws_clients:
                    continue
                payload = ws_json(msg)
                dead: List[WebSocket] = []
                for c in list(bridge._ws_clients):
                    try:
                        await c.send_text(payload)
                    except Exception:
                        dead.append(c)
                for c in dead:
                    bridge._ws_clients.discard(c)

        asyncio.create_task(broadcaster())
        print("ControlWebsocket Startup Complete")

        yield

        ###########
        # Shutdown

        print("Control WebSocket Shutdown")

    app = FastAPI(title="sdr-scanner Web", lifespan=websocketLifespan)

    @app.websocket("/control_ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        bridge._ws_clients.add(ws)
        print(f"WS client connected: {getattr(ws, 'client', None)}")

        try:

            # Send config on connect
            await ws.send_text(ws_json(bridge.scanner.getJsonConfigMsg()))

            while True:
                raw = await ws.receive_text()
                print(f"WS RAW: {raw}")

                try:
                    msg = json.loads(raw)
                except Exception:
                    await ws.send_text(ws_json({"type": "Error", "data": {"error": "invalid json"}}))
                    continue

                # Forward directly into Scanner input queue (same format as wx UI)
                if isinstance(msg, dict) and "type" in msg:
                    mtype = str(msg.get("type") or "")

                    print(f"WEB->SCANNER: {msg}")
                    bridge.ui_to_scanner.put(msg)

        except WebSocketDisconnect:
            print(f"WS client disconnected: {getattr(ws, 'client', None)}")
        except Exception as e:
            print(f"WS handler error: {e}")
            try:
                await ws.send_text(ws_json({"type": "Error", "data": {"error": str(e)}}))
            except Exception:
                pass
        finally:
            bridge._ws_clients.discard(ws)

    return app


def controlWebsocketRun(scanner: Scanner, host: str, port: int, stopEvent: threading.Event):

    scannerWeb = ScannerWeb(scanner, stopEvent)
    app = create_app(scannerWeb)

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        reload=False
    )
    server = uvicorn.Server(config)
    server.should_exit = stopEvent
    server.run()
