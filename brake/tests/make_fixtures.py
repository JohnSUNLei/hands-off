"""Writes the fixture transcripts (run once; the output is committed). Nothing here costs money: it's made-up usage.

runaway.jsonl — an agent stuck re-running a failing test suite on Claude Fable 5.2, one model call every 20 s,
each re-reading a ~450K-token context. Shapes follow what Claude Code writes today: one line per content block,
so one API response can appear on several lines with the same message.id (brake must count it once).
"""
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent / "fixtures"
START = dt.datetime(2026, 11, 21, 3, 0, 0, tzinfo=dt.timezone.utc)      # 22:00 in Toronto
USAGE = {"input_tokens": 5, "cache_creation_input_tokens": 30000, "cache_read_input_tokens": 450000,
         "output_tokens": 2500, "cache_creation": {"ephemeral_5m_input_tokens": 30000, "ephemeral_1h_input_tokens": 0},
         "service_tier": "standard", "speed": "standard"}


def ts(i):
    return (START + dt.timedelta(seconds=20 * i)).isoformat().replace("+00:00", "Z")


def main():
    lines = [{"type": "user", "timestamp": ts(0), "message": {"role": "user", "content": "Make the test suite pass."},
              "sessionId": "runaway-1", "uuid": "u0"}]
    for i in range(40):
        mid = f"msg_{i:04d}"
        for j, block in enumerate(("thinking", "text", "tool_use")):   # three lines, one response
            lines.append({"type": "assistant", "timestamp": ts(i), "requestId": f"req_{i:04d}", "uuid": f"a{i}-{j}",
                          "sessionId": "runaway-1",
                          "message": {"id": mid, "model": "claude-fable-5-2", "role": "assistant",
                                      "content": [{"type": block}], "usage": USAGE}})
        lines.append({"type": "user", "timestamp": ts(i), "uuid": f"r{i}", "sessionId": "runaway-1",
                      "message": {"role": "user", "content": [{"type": "tool_result", "content": "1 failed"}]}})
    lines.append({"type": "summary", "summary": "test loop", "leafUuid": "a39-2"})
    (HERE / "runaway.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    sub = [{"type": "assistant", "timestamp": ts(5), "requestId": "req_sub", "uuid": "s1", "isSidechain": True,
            "message": {"id": "msg_sub_1", "model": "claude-haiku-5-5", "role": "assistant", "content": [],
                        "usage": {"input_tokens": 20000, "output_tokens": 1000}}}]
    (HERE / "runaway" / "subagents" / "agent-a1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in sub))


if __name__ == "__main__":
    main()
