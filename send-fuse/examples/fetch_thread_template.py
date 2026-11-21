#!/usr/bin/env python3
"""Template for a `command` thread source.

policy.json:
    "thread_source": {"type": "command", "cmd": ["python3", "examples/fetch_thread_template.py"]}

send-fuse runs this script before every send and passes the outgoing message on stdin:
    {"to": ["sam@acme.com"], "subject": "Re: Report", "body": "...", "thread_id": "", "in_reply_to": "<abc@acme.com>", "via": "mcp__gmail__reply"}

Print the conversation it belongs to (or `null` if there is none) on stdout:
    {"id": "...", "subject": "...", "messages": [
        {"id": "<abc@acme.com>", "from": "Sam <sam@acme.com>", "to": ["you@example.com"],
         "date": "2026-11-17T09:12:00-05:00", "subject": "...", "body": "...", "attachments": ["invite.ics"]}]}

Exit non-zero if you can't tell — send-fuse then blocks the send ("not sure → not sending").
Replace `lookup()` with a call to your provider (Gmail API, Slack conversations.replies, a CRM...).
Credentials belong in environment variables read here, never in policy.json.
"""
import json
import sys


def lookup(outgoing: dict):
    """Return the thread dict, or None. This template knows no provider, so it says so."""
    raise SystemExit("fetch_thread_template.py: replace lookup() with your provider")


if __name__ == "__main__":
    print(json.dumps(lookup(json.load(sys.stdin))))
