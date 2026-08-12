"""Command-line interface for omni-agent-bridge.

Config resolution order for --url/--token/--channel: CLI flag, then
OMNI_URL / OMNI_TOKEN / OMNI_CHANNEL env vars. Once those env vars are set,
every command below can drop the flags entirely.

    export OMNI_TOKEN=omni_agt_...
    export OMNI_CHANNEL=agents          # name or numeric id
    omni-agent-bridge channels
    omni-agent-bridge history
    omni-agent-bridge send "hello"
    omni-agent-bridge listen            # prints one JSON line per incoming message
    omni-agent-bridge relay             # listen + send stdin lines, both at once
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading

from .client import DEFAULT_URL, OmniAgentBridge, OmniAgentBridgeError


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", default=os.environ.get("OMNI_URL", DEFAULT_URL))
    p.add_argument("--token", default=os.environ.get("OMNI_TOKEN"))
    p.add_argument("--channel", default=os.environ.get("OMNI_CHANNEL"))


def _require_token(args: argparse.Namespace) -> str:
    if not args.token:
        print("error: no token — pass --token or set OMNI_TOKEN (create one from the chat UI's 'Manage agents' panel)", file=sys.stderr)
        sys.exit(2)
    return args.token


def _msg_to_json(m) -> str:
    return json.dumps(
        {
            "id": m.id,
            "channelId": m.channel_id,
            "senderType": m.sender_type,
            "senderName": m.sender_name,
            "content": m.content,
            "createdAt": m.created_at,
        }
    )


def cmd_channels(args: argparse.Namespace) -> None:
    bridge = OmniAgentBridge(token=_require_token(args), url=args.url)
    for c in bridge.channels():
        print(f"{c.id}\t#{c.name}\t{c.topic or ''}")


def cmd_history(args: argparse.Namespace) -> None:
    bridge = OmniAgentBridge(token=_require_token(args), channel=args.channel, url=args.url)
    for m in bridge.history(limit=args.limit):
        print(_msg_to_json(m))


def cmd_send(args: argparse.Namespace) -> None:
    bridge = OmniAgentBridge(token=_require_token(args), channel=args.channel, url=args.url)
    m = bridge.send(args.content)
    print(_msg_to_json(m))


def cmd_listen(args: argparse.Namespace) -> None:
    bridge = OmniAgentBridge(token=_require_token(args), channel=args.channel, url=args.url)
    for m in bridge.listen(include_own=args.include_own):
        print(_msg_to_json(m))
        sys.stdout.flush()


def cmd_relay(args: argparse.Namespace) -> None:
    """Two-way bridge: prints incoming messages as JSON lines to stdout,
    and sends each line read from stdin as a message. Wire an agent process
    up to this via a pipe to give it a live chat connection without it
    needing to speak the OmniTools protocol itself."""
    bridge = OmniAgentBridge(token=_require_token(args), channel=args.channel, url=args.url)

    def reader():
        for m in bridge.listen(include_own=args.include_own):
            print(_msg_to_json(m))
            sys.stdout.flush()

    threading.Thread(target=reader, daemon=True).start()

    for line in sys.stdin:
        line = line.strip()
        if line:
            bridge.send(line)


def main() -> None:
    parser = argparse.ArgumentParser(prog="omni-agent-bridge", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("channels", help="list channels")
    _add_common_args(p)
    p.set_defaults(func=cmd_channels)

    p = sub.add_parser("history", help="print recent messages in a channel")
    _add_common_args(p)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("send", help="send one message")
    _add_common_args(p)
    p.add_argument("content")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("listen", help="stream incoming messages as JSON lines")
    _add_common_args(p)
    p.add_argument("--include-own", action="store_true", help="also print messages this token itself sent")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("relay", help="listen (stdout) + send stdin lines, simultaneously")
    _add_common_args(p)
    p.add_argument("--include-own", action="store_true")
    p.set_defaults(func=cmd_relay)

    args = parser.parse_args()
    try:
        args.func(args)
    except OmniAgentBridgeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
