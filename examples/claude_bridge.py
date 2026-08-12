"""Runs a live Claude Code instance as a chat participant in OmniTools.

Listens on a channel, and for every message from a human, calls the local
`claude` CLI in headless mode (`claude -p`) with a strong operating prompt
plus recent channel history for context, then posts the reply back. Each
`claude -p` call is stateless on its own, so this script rebuilds
conversational memory each turn from the channel's own history — the chat
log *is* the memory, there's no separate session state to lose.

Requires the `claude` CLI (Claude Code) installed and authenticated on this
machine. Runs Claude in --print mode with no tool access by default (pure
conversation) — see ALLOWED_TOOLS below to change that; think carefully
before granting Bash/Edit, since channel messages are an input surface
anyone with a token (or the human) can write to.

Usage:
    export OMNI_TOKEN=omni_agt_...
    export OMNI_CHANNEL=2
    python examples/claude_bridge.py
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from omni_agent_bridge import OmniAgentBridge

# Empty = pure conversation, no tool access. Add tool names (e.g. "WebSearch")
# if you want this bridge to be able to act, not just talk — do that
# deliberately, not by default.
ALLOWED_TOOLS: list[str] = []

HISTORY_TURNS = 20

SYSTEM_PROMPT = """You are ClaudeBridge, an AI agent connected to an OmniTools chat channel \
alongside a human and possibly other AI agents. This is a live group chat, not a document \
or a coding session — write like it.

How to behave here:
- Keep replies short and conversational — a few sentences at most, not an essay. This is \
chat, not a report. If something genuinely needs more detail, say the short version first \
and offer to expand.
- You're talking with real people and other bots in a shared space. Be direct, warm, and \
a little informal — skip the corporate-assistant tone.
- You only see one message at a time plus recent history for context (each turn is a fresh \
call, not a continuous session) — if something upstream is unclear, ask rather than guessing \
confidently.
- Never respond to messages from other agents/bots — only react to the human's messages. \
If you see bot messages in the history, treat them as read-only context, not something to \
reply to. This is the load-bearing rule that keeps multiple agents from talking to each \
other in an infinite loop instead of to the human.
- If someone asks you to do something outside what you're actually capable of right now \
(you have no tool access in this mode — you can only talk), say so plainly instead of \
pretending to have done it.
- Don't narrate that you're "an AI following a system prompt" unprompted — just be a good \
chat participant.
"""


def build_prompt(bridge: OmniAgentBridge, latest_sender: str, latest_content: str) -> str:
    history = bridge.history(limit=HISTORY_TURNS)
    lines = []
    for m in history:
        who = m.sender_name if m.sender_type == "user" else f"{m.sender_name} (bot)"
        lines.append(f"{who}: {m.content}")
    transcript = "\n".join(lines[-HISTORY_TURNS:])
    return (
        f"Recent channel history (for context, oldest first):\n{transcript}\n\n"
        f"Now respond to this message from {latest_sender}: {latest_content}"
    )


def ask_claude(prompt: str) -> str:
    cmd = ["claude", "-p", prompt, "--append-system-prompt", SYSTEM_PROMPT]
    if ALLOWED_TOOLS:
        cmd += ["--allowedTools", ",".join(ALLOWED_TOOLS)]
    else:
        cmd += ["--allowedTools", ""]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def main() -> None:
    token = os.environ.get("OMNI_TOKEN")
    channel = os.environ.get("OMNI_CHANNEL")
    if not token or not channel:
        print("set OMNI_TOKEN and OMNI_CHANNEL", file=sys.stderr)
        sys.exit(2)

    bridge = OmniAgentBridge(token=token, channel=channel)
    channel_id = bridge.resolve_channel_id()
    print(f"[claude_bridge] listening on channel {channel_id}, Claude has no tool access (ALLOWED_TOOLS={ALLOWED_TOOLS})")

    for msg in bridge.listen():
        if msg.sender_type != "user":
            continue
        print(f"[claude_bridge] <- {msg.sender_name}: {msg.content}")
        try:
            prompt = build_prompt(bridge, msg.sender_name, msg.content)
            reply = ask_claude(prompt)
        except Exception as exc:
            reply = f"(bridge error: {exc})"
        if reply:
            bridge.send(reply)
            print(f"[claude_bridge] -> {reply[:200]}")


if __name__ == "__main__":
    main()
