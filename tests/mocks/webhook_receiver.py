import asyncio
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, Request
from uvicorn import Config, Server


class MockWebhookReceiver:
    def __init__(self):
        self.app = FastAPI()
        self.requests: List[Tuple[Dict[str, Any], bytes]] = []
        self.server = None

        @self.app.post("/")
        async def receive_webhook(request: Request):
            headers = dict(request.headers)
            body = await request.body()
            self.requests.append((headers, body))
            return {"status": "received"}

    async def start(self, host="127.0.0.1", port=8001):
        config = Config(app=self.app, host=host, port=port)
        self.server = Server(config)
        await self.server.serve()

    async def stop(self):
        if self.server:
            await self.server.shutdown()

    def clear(self):
        self.requests.clear()

    def get_requests(self) -> List[Tuple[Dict[str, Any], bytes]]:
        return self.requests