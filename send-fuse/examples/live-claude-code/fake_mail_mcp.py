#!/usr/bin/env python3
"""A fake MCP mail server (stdio, JSON-RPC). One tool: send_email. It never touches the network —
it only appends the message to fake_outbox.jsonl next to this file."""
import json, sys, datetime
from pathlib import Path
OUTBOX = Path(__file__).resolve().parent / "fake_outbox.jsonl"
TOOL = {"name": "send_email", "description": "Send an email (or a reply when in_reply_to is given).",
        "inputSchema": {"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
            "in_reply_to": {"type": "string", "description": "Message-ID being answered"}},
            "required": ["to", "subject", "body"]}}
def reply(i, result): print(json.dumps({"jsonrpc": "2.0", "id": i, "result": result}), flush=True)
for line in sys.stdin:
    if not line.strip(): continue
    msg = json.loads(line); m = msg.get("method"); i = msg.get("id")
    if m == "initialize":
        reply(i, {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "fakemail", "version": "0.0.1"}})
    elif m == "tools/list":
        reply(i, {"tools": [TOOL]})
    elif m == "tools/call":
        args = msg["params"].get("arguments") or {}
        with open(OUTBOX, "a") as f:
            f.write(json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"), **args}) + "\n")
        reply(i, {"content": [{"type": "text", "text": f"Sent (fake) to {args.get('to')}."}]})
    elif i is not None:
        print(json.dumps({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": f"no method {m}"}}), flush=True)
