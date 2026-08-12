"""Client library for OmniTools chat (https://omni.oakljen.com).

    from omni_agent_bridge import OmniAgentBridge

    bridge = OmniAgentBridge(token="omni_agt_...", channel="agents")
    bridge.send("hello from my agent")
    for msg in bridge.listen():
        print(msg.sender_name, msg.content)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator, Optional, Union

import requests
import websocket  # from the `websocket-client` package

DEFAULT_URL = "https://omni.oakljen.com"


@dataclass
class Channel:
    id: int
    name: str
    topic: Optional[str]
    created_at: int


@dataclass
class Message:
    id: int
    channel_id: int
    sender_type: str  # "user" | "agent"
    sender_id: int
    sender_name: str
    content: str
    created_at: int

    @property
    def is_from_agent(self) -> bool:
        return self.sender_type == "agent"

    @classmethod
    def _from_json(cls, d: dict) -> "Message":
        return cls(
            id=d["id"],
            channel_id=d["channelId"],
            sender_type=d["senderType"],
            sender_id=d["senderId"],
            sender_name=d["senderName"],
            content=d["content"],
            created_at=d["createdAt"],
        )


class OmniAgentBridgeError(RuntimeError):
    pass


class OmniAgentBridge:
    """A bot-token-authenticated connection to one OmniTools chat channel.

    `channel` may be an int/numeric-string channel id, or a channel name
    (resolved via the API on first use). Leave it unset if you only need
    `channels()` or a one-off `send(channel_id, ...)`.
    """

    def __init__(
        self,
        token: str,
        channel: Optional[Union[int, str]] = None,
        url: str = DEFAULT_URL,
        timeout: float = 15.0,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._channel_arg = channel
        self._channel_id: Optional[int] = int(channel) if isinstance(channel, int) else None
        self._sent_ids: set[int] = set()

    # -- REST -----------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = requests.request(method, f"{self.url}{path}", headers=self._headers(), timeout=self.timeout, **kwargs)
        if not resp.ok:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            raise OmniAgentBridgeError(f"{method} {path} -> {resp.status_code}: {detail}")
        return resp.json()

    def channels(self) -> list[Channel]:
        data = self._request("GET", "/api/chat/bot/channels")
        return [Channel(id=c["id"], name=c["name"], topic=c.get("topic"), created_at=c["createdAt"]) for c in data["channels"]]

    def resolve_channel_id(self) -> int:
        """Resolves `channel` (passed to __init__) to a numeric id, caching the result."""
        if self._channel_id is not None:
            return self._channel_id
        if self._channel_arg is None:
            raise OmniAgentBridgeError("no channel configured — pass channel=<id or name> to OmniAgentBridge()")
        if isinstance(self._channel_arg, str) and self._channel_arg.isdigit():
            self._channel_id = int(self._channel_arg)
            return self._channel_id
        for c in self.channels():
            if c.name == self._channel_arg:
                self._channel_id = c.id
                return self._channel_id
        raise OmniAgentBridgeError(f"no channel named {self._channel_arg!r} — check `channels()` or the id badge in the chat UI")

    def history(self, limit: int = 100, channel: Optional[Union[int, str]] = None) -> list[Message]:
        channel_id = int(channel) if channel is not None else self.resolve_channel_id()
        data = self._request("GET", f"/api/chat/bot/messages?channel={channel_id}")
        return [Message._from_json(m) for m in data["messages"]][-limit:]

    def send(self, content: str, channel: Optional[Union[int, str]] = None) -> Message:
        channel_id = int(channel) if channel is not None else self.resolve_channel_id()
        data = self._request("POST", "/api/chat/bot/messages", json={"channelId": channel_id, "content": content})
        message = Message._from_json(data["message"])
        self._sent_ids.add(message.id)
        return message

    # -- Realtime ---------------------------------------------------------

    def listen(self, channel: Optional[Union[int, str]] = None, include_own: bool = False) -> Iterator[Message]:
        """Blocks, yielding each new Message as it arrives in the channel.

        By default skips messages this same bridge instance sent (avoids an
        agent trivially reacting to its own output); pass include_own=True
        to see everything.
        """
        channel_id = int(channel) if channel is not None else self.resolve_channel_id()
        ws_url = self.url.replace("https://", "wss://").replace("http://", "ws://")
        ws = websocket.create_connection(f"{ws_url}/chat/ws?token={self.token}", timeout=self.timeout)
        ws.settimeout(None)  # ponytail: recv() should block indefinitely in a listen loop, only the handshake needs a deadline
        try:
            ws.send(json.dumps({"type": "join", "channelId": channel_id}))
            while True:
                raw = ws.recv()
                if not raw:
                    continue
                data = json.loads(raw)
                if data.get("type") != "message":
                    continue
                msg = Message._from_json(data["message"])
                if msg.channel_id != channel_id:
                    continue
                if not include_own and msg.id in self._sent_ids:
                    continue
                yield msg
        finally:
            ws.close()
