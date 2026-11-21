#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send-fuse — a fuse between your AI agent and the Send button.

Before an agent sends an email / message / post, send-fuse looks at the conversation
as it is *now* and blocks the send when a careful human would not hit Send:

  * the other side already confirmed (an invite is out, "see you Thursday")
    and the agent is about to propose times again;
  * the agent is answering an old message — newer ones exist in the thread;
  * we already replied and the ball is in their court;
  * an automatic send already went out on this conversation in the last 24 h,
    or the very same message went to the same person recently;
  * it is quiet hours, or the daily cap is reached (these are *held*, not dropped);
  * another process is sending right now (one exclusive lock on local disk).

Everything is deterministic: regular expressions, timestamps and a ledger. No model is
called, nothing goes over the network, and send-fuse itself can never send anything.

One file, standard library only. Use it three ways:
  * Claude Code hook:  `python3 sendfuse.py init --claude`   (PreToolUse + PostToolUse)
  * CLI wrapper:       `send-fuse run --to a@b.c --subject S --body-file m.txt -- msmtp a@b.c`
  * Python library:    `with Fuse().sending(Outgoing(...)): smtp.send_message(...)`

Try it in 10 seconds:  `python3 sendfuse.py demo`
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import email
import email.policy
import email.utils
import hashlib
import json
import mailbox
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:                                   # Unix
    import fcntl as _fcntl
    _msvcrt = None
except ImportError:                    # Windows
    _fcntl = None
    import msvcrt as _msvcrt           # type: ignore

VERSION = "0.1.0"
PROJECT_DIR = ".send-fuse"
POLICY_FILE = "policy.json"


class FuseError(Exception):
    """Something is wrong with send-fuse's own setup (policy, state dir, thread source)."""


class LockBusy(FuseError):
    """Another process holds the send lock."""


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Configuration — the ONLY place that reads environment variables or policy files
# ═══════════════════════════════════════════════════════════════════════════════

ENV_PREFIX = "SENDFUSE_"
#   SENDFUSE_POLICY     path to a policy.json (overrides discovery)
#   SENDFUSE_STATE_DIR  where the ledger / queue / lock live (must be local disk)
#   SENDFUSE_NOW        ISO timestamp that pins "now" (tests, demos, replays)
#   NO_COLOR            standard: disables ANSI colour


RAW_ENV = ("NO_COLOR", "LOCALAPPDATA", "XDG_CACHE_HOME")   # standard variables we also honour


def getenv(name: str, default: str | None = None) -> str | None:
    """The one function in this file that touches os.environ."""
    key = name if name in RAW_ENV else ENV_PREFIX + name
    return os.environ.get(key, default)


DEFAULT_POLICY: dict = {
    "me": [],                           # your own addresses / handles: messages from these are "ours"
    "thread_source": None,              # where to read the live conversation (see ThreadSource below)
    "require_thread_for_replies": True,  # a reply whose conversation can't be read is blocked
    "same_thread_hours": 24,            # one automatic send per conversation per N hours
    "duplicate_days": 7,                # the same message to the same person: once per N days
    "followup_days": 3,                 # after our own last message, wait N days before a follow-up
    "daily_cap": 20,                    # automatic sends per local day, then hold
    "quiet_hours": {"start": "21:00", "end": "08:00", "tz": "local", "weekends": False},
    "review": "off",                    # "off" | "always" | "new_recipient": ask a human first
    "bash": "ask",                      # shell commands that look like sending: "ask" | "block" | "off"
    "reserve_minutes": 10,              # a pre-send reservation that is never confirmed expires
    "approval_hours": 24,               # a human approval is valid this long, once
    "rules_off": [],                    # rule names to disable
    "guard_tools": [],                  # extra regexes: tool names that always count as sending
    "ignore_tools": [],                 # regexes: tool names that never count as sending
    "state_dir": None,                  # override the state directory
}
POLICY_TYPES = {"me": list, "require_thread_for_replies": bool, "same_thread_hours": (int, float),
                "duplicate_days": (int, float), "followup_days": (int, float), "daily_cap": int,
                "review": str, "bash": str, "reserve_minutes": (int, float), "approval_hours": (int, float),
                "rules_off": list, "guard_tools": list, "ignore_tools": list}


def find_policy_file(start: Path | None = None) -> Path | None:
    explicit = getenv("POLICY")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise FuseError(f"SENDFUSE_POLICY points to {p}, which does not exist")
        return p
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        p = d / PROJECT_DIR / POLICY_FILE
        if p.is_file():
            return p
    p = Path.home() / ".config" / "send-fuse" / POLICY_FILE
    return p if p.is_file() else None


def load_policy(path: Path | None = None, start: Path | None = None) -> dict:
    """Defaults, overlaid with the policy file. Relative paths in thread_source resolve
    against the directory that holds `.send-fuse/`."""
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    p = path or find_policy_file(start)
    if p is None:
        pol["_base"] = str((start or Path.cwd()).resolve())
        return pol
    try:
        user = json.loads(Path(p).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise FuseError(f"cannot read policy {p}: {e}") from e
    if not isinstance(user, dict):
        raise FuseError(f"policy {p} must be a JSON object")
    unknown = sorted(k for k in user if k not in DEFAULT_POLICY and not k.startswith("_"))
    if unknown:
        raise FuseError(f"policy {p}: unknown key(s) {', '.join(unknown)}")
    for k, typ in POLICY_TYPES.items():
        if k in user and not isinstance(user[k], typ):
            raise FuseError(f"policy {p}: '{k}' has the wrong type")
    if user.get("review", "off") not in ("off", "always", "new_recipient"):
        raise FuseError(f"policy {p}: review must be off / always / new_recipient")
    if user.get("bash", "ask") not in ("ask", "block", "off"):
        raise FuseError(f"policy {p}: bash must be ask / block / off")
    pol.update({k: v for k, v in user.items() if not k.startswith("_")})
    base = Path(p).resolve().parent
    pol["_base"] = str(base.parent if base.name == PROJECT_DIR else base)
    pol["_file"] = str(p)
    return pol


def now() -> dt.datetime:
    pinned = getenv("NOW")
    if pinned:
        return parse_time(pinned)
    return dt.datetime.now().astimezone()


def default_state_dir() -> Path:
    override = getenv("STATE_DIR")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "send-fuse"
    if os.name == "nt":
        return Path(getenv("LOCALAPPDATA") or home) / "send-fuse"
    return Path(getenv("XDG_CACHE_HOME") or home / ".cache") / "send-fuse"


def color_enabled(stream) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and not getenv("NO_COLOR")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Data model — every concept is defined exactly once, here
# ═══════════════════════════════════════════════════════════════════════════════

def parse_time(value) -> dt.datetime:
    """ISO 8601 or RFC 2822 → aware datetime (naive input is taken as local time)."""
    if isinstance(value, dt.datetime):
        t = value
    else:
        s = str(value).strip()
        try:
            t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                t = email.utils.parsedate_to_datetime(s)
            except (TypeError, ValueError) as e:
                raise ValueError(f"unreadable time: {s!r}") from e
    return t if t.tzinfo else t.astimezone()


def addr_of(raw: str) -> str:
    """'Jordan <jordan@x.com>' → 'jordan@x.com'; handles like '#general' or '@sam' pass through."""
    raw = (raw or "").strip()
    name, addr = email.utils.parseaddr(raw)
    return (addr or raw).strip().lower()


def name_of(raw: str) -> str:
    """'Jordan (Northwind Recruiting) <j@x.com>' → 'Jordan'."""
    name, addr = email.utils.parseaddr(raw or "")
    name = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    return name or (addr.split("@")[0] if addr else raw)


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0].rstrip(".,;:—-") + "…"


def norm_subject(s: str) -> str:
    s = re.sub(r"^\s*((re|fw|fwd|aw|sv|答复|回复|转发)\s*[:：]\s*)+", "", s or "", flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_body(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


@dataclass
class Message:
    """One message in a conversation, as the thread source saw it."""
    id: str = ""
    sender: str = ""
    to: list = field(default_factory=list)
    date: dt.datetime | None = None
    subject: str = ""
    body: str = ""
    attachments: list = field(default_factory=list)   # filenames and/or content types

    @property
    def addr(self) -> str:
        return addr_of(self.sender)

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        to = d.get("to") or []
        return cls(id=str(d.get("id") or d.get("message_id") or ""),
                   sender=str(d.get("from") or d.get("sender") or ""),
                   to=[to] if isinstance(to, str) else list(to),
                   date=parse_time(d["date"]) if d.get("date") else None,
                   subject=str(d.get("subject") or ""),
                   body=str(d.get("body") or d.get("text") or d.get("snippet") or ""),
                   attachments=list(d.get("attachments") or []))


@dataclass
class Thread:
    """A whole conversation, oldest message first."""
    id: str
    subject: str = ""
    messages: list = field(default_factory=list)

    def __post_init__(self):
        epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        self.messages.sort(key=lambda m: m.date or epoch)

    @classmethod
    def from_dict(cls, d: dict) -> "Thread":
        msgs = [Message.from_dict(m) for m in d.get("messages") or []]
        subj = d.get("subject") or (msgs[0].subject if msgs else "")
        return cls(id=str(d.get("id") or (msgs[0].id if msgs else "")), subject=subj, messages=msgs)

    def find(self, message_id: str) -> Message | None:
        mid = (message_id or "").strip("<> ")
        return next((m for m in self.messages if mid and m.id.strip("<> ") == mid), None)


@dataclass
class Outgoing:
    """The message an agent wants to send."""
    to: list = field(default_factory=list)
    subject: str = ""
    body: str = ""
    thread_id: str = ""
    in_reply_to: str = ""
    via: str = ""                  # who is sending: a tool name, a script name

    def __post_init__(self):
        if isinstance(self.to, str):
            self.to = [self.to]
        self.to = [addr_of(t) for t in self.to if str(t).strip()]

    @property
    def is_reply(self) -> bool:
        return bool(self.thread_id or self.in_reply_to or re.match(r"^\s*(re|答复|回复)\s*[:：]", self.subject, re.I))

    def fingerprint(self) -> str:
        raw = "\n".join([",".join(sorted(self.to)), norm_subject(self.subject), norm_body(self.body)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, d: dict) -> "Outgoing":
        return cls(**{k: d[k] for k in ("to", "subject", "body", "thread_id", "in_reply_to", "via") if k in d})


@dataclass
class Reason:
    rule: str
    effect: str          # "block" | "hold" | "ask"
    message: str


EFFECT_RANK = {"block": 3, "hold": 2, "ask": 1}


@dataclass
class Verdict:
    decision: str        # "allow" | "block" | "hold" | "ask"
    reasons: list
    outgoing: Outgoing
    thread_key: str = ""
    queue_id: int = 0
    approved: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reasons": [asdict(r) for r in self.reasons],
                "to": self.outgoing.to, "subject": self.outgoing.subject, "thread": self.thread_key,
                "queue_id": self.queue_id, "approved": self.approved}

    def explain(self, color: bool = False, width: int = 0) -> str:
        """Human-readable verdict. width > 0 wraps long reasons (terminals); 0 = one line each."""
        c = _Ansi(color)
        head = {"allow": c.green("ALLOWED"), "block": c.red("BLOCKED"), "hold": c.yellow("HELD"),
                "ask": c.yellow("NEEDS A HUMAN")}[self.decision]
        to = ", ".join(self.outgoing.to) or "(no recipient)"
        lines = [f"send-fuse: {head}  → {to}  “{self.outgoing.subject or '(no subject)'}”"]
        if self.approved:
            lines.append("  " + c.green("✓ approved by a human (send-fuse approve)"))
        for r in self.reasons:
            mark = c.red("✗") if r.effect == "block" else c.yellow("…")
            body = [r.message]
            if width > 40:
                body = textwrap.wrap(r.message, width - 21) or [""]
            lines.append(f"  {mark} {c.bold(f'{r.rule:<16}')} {body[0]}")
            lines.extend(" " * 21 + b for b in body[1:])
        if self.decision in ("block", "hold") and self.queue_id:
            lines.append(c.dim(f"  queued as #{self.queue_id} · review: send-fuse queue · "
                               f"override: send-fuse approve {self.queue_id}"))
        return "\n".join(lines)


class _Ansi:
    def __init__(self, on: bool):
        self.on = on

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def red(self, s): return self._w("1;31", s)
    def green(self, s): return self._w("1;32", s)
    def yellow(self, s): return self._w("1;33", s)
    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Signals — what a message *says*, decided by regular expressions only
# ═══════════════════════════════════════════════════════════════════════════════

CONFIRM_PATTERNS = [re.compile(p, re.I) for p in (
    r"(?<!not )(?<!n't )(?<!un)\bconfirmed\b",
    r"\bi(?:'m| am) confirming\b",
    r"\bsee you (?:then|there|on|at|next|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\blooking forward to (?:speaking|meeting|talking|chatting|seeing)\b",
    r"\b(?:that|this|the) (?:time|slot|date) works\b",
    r"\bworks (?:great |perfectly |well )?for me\b",
    r"\b(?:i'?ve|i have|we'?ve|we have) (?:sent|scheduled|booked)\b",
    r"\bcalendar invit(?:e|ation)\b",
    r"\b(?:is|are|has been|have been) (?:now )?(?:scheduled|booked|locked in)\b",
    r"已确认", r"确认了", r"到时见", r"就这么定", r"约好了", r"日历邀请", r"时间没问题",
)]
CALENDAR_SUBJECT = re.compile(r"^\s*(invitation|updated invitation|accepted|已接受|邀请)\s*[:：]", re.I)
SCHEDULING_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bare you (?:available|free)\b",
    r"\bwhat (?:time|day|date)s? (?:works|would work|suits|is good|are good)\b",
    r"\b(?:would|do|does|could) any of (?:these|those|the following)\b",
    r"\bhere are (?:a few|some|several|two|three|four|\d+) (?:times|slots|options|windows)\b",
    r"\b(?:my|our) availability\b",
    r"\blet me know (?:what|which|a|your) (?:time|day|date|slot|availability)",
    r"\b(?:schedule|set up|arrange) (?:a|an|the) (?:call|chat|meeting|interview)\b",
    r"\b(?:find|pick|book|propose) a time\b",
    r"\btime ?slots?\b",
    r"什么时间方便", r"哪个时间", r"以下(?:几个)?时间", r"约个时间", r"方便的时间", r"时间段",
)]
TIME_OF_DAY = re.compile(r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s?(?:am|pm)\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b", re.I)


def _snippet(text: str, m: re.Match, width: int = 90) -> str:
    """The sentence around a match, trimmed for display."""
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start()), text.rfind("。", 0, m.start())) + 1
    ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end()), text.find("。", m.end())) if i != -1]
    end = min(ends) + 1 if ends else len(text)
    s = re.sub(r"\s+", " ", text[start:end]).strip()
    return s if len(s) <= width else s[:width - 1] + "…"


def confirmation_in(msg: Message) -> str:
    """Return a short quote if this message confirms / books something, else ''."""
    for a in msg.attachments:
        a = str(a).lower()
        if a.endswith(".ics") or "text/calendar" in a:
            body_hit = next((p.search(msg.body) for p in CONFIRM_PATTERNS if p.search(msg.body)), None)
            return _snippet(msg.body, body_hit) if body_hit else f"calendar invite attached ({a})"
    if CALENDAR_SUBJECT.search(msg.subject):
        return msg.subject.strip()
    for p in CONFIRM_PATTERNS:
        m = p.search(msg.body)
        if m:
            return _snippet(msg.body, m)
    return ""


def is_scheduling(text: str) -> bool:
    """Does this outgoing text propose times / ask for availability?"""
    if any(p.search(text or "") for p in SCHEDULING_PATTERNS):
        return True
    return len(TIME_OF_DAY.findall(text or "")) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Thread sources — the wrapper layer: the only code that knows about mail formats
# ═══════════════════════════════════════════════════════════════════════════════

class ThreadSource:
    """Find the live conversation an outgoing message belongs to.
    Policy: {"thread_source": {"type": "json" | "maildir" | "mbox" | "command", ...}}"""

    def threads(self) -> list:
        raise NotImplementedError

    def find(self, out: Outgoing) -> Thread | None:
        ts = self.threads()
        if out.thread_id:
            hit = next((t for t in ts if t.id == out.thread_id), None)
            if hit:
                return hit
        if out.in_reply_to:
            hit = next((t for t in ts if t.find(out.in_reply_to)), None)
            if hit:
                return hit
        subj = norm_subject(out.subject)
        if subj:
            for t in ts:
                people = {m.addr for m in t.messages} | {addr_of(x) for m in t.messages for x in m.to}
                if norm_subject(t.subject) == subj and (not out.to or set(out.to) & people):
                    return t
        return None


class JsonSource(ThreadSource):
    """A JSON file ({"threads": [...]}, or one thread) or a directory of such files."""

    def __init__(self, path: Path):
        self.path = path

    def threads(self) -> list:
        if not self.path.exists():
            raise FuseError(f"thread source {self.path} does not exist")
        files = sorted(self.path.glob("*.json")) if self.path.is_dir() else [self.path]
        out = []
        for f in files:
            try:
                doc = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError) as e:
                raise FuseError(f"cannot read thread file {f}: {e}") from e
            items = doc.get("threads", [doc]) if isinstance(doc, dict) else doc
            try:
                out.extend(Thread.from_dict(t) for t in items if isinstance(t, dict))
            except (KeyError, TypeError, ValueError) as e:
                raise FuseError(f"thread file {f} has a malformed message: {e}") from e
        return out


class MailboxSource(ThreadSource):
    """A local Maildir (mbsync / offlineimap / Thunderbird export) or an mbox file."""

    def __init__(self, path: Path, kind: str):
        self.path, self.kind = path, kind

    def _box(self):
        if not self.path.exists():
            raise FuseError(f"thread source {self.path} does not exist")
        return mailbox.Maildir(str(self.path), factory=None, create=False) if self.kind == "maildir" \
            else mailbox.mbox(str(self.path), factory=None, create=False)

    @staticmethod
    def _parse(raw) -> tuple:
        msg = email.message_from_bytes(raw.as_bytes(), policy=email.policy.default)
        refs = re.findall(r"<[^>]+>", (msg.get("References") or "") + " " + (msg.get("In-Reply-To") or ""))
        body, atts = "", []
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype, fname = part.get_content_type(), part.get_filename()
            if fname or ctype == "text/calendar":
                atts.append(fname or ctype)
            elif ctype == "text/plain" and not body:
                try:
                    body = part.get_content()
                except (LookupError, ValueError):
                    body = part.get_payload(decode=True).decode("utf-8", "replace")
        date = None
        if msg.get("Date"):
            with contextlib.suppress(TypeError, ValueError):
                date = parse_time(msg["Date"])
        to = [a for _, a in email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))]
        m = Message(id=(msg.get("Message-ID") or "").strip(), sender=str(msg.get("From") or ""), to=to,
                    date=date, subject=str(msg.get("Subject") or ""), body=body, attachments=atts)
        return m, refs, (msg.get("X-GM-THRID") or "").strip()

    def threads(self) -> list:
        parsed = [self._parse(raw) for raw in self._box()]
        root: dict = {}

        def find(x):
            while root.get(x, x) != x:
                x = root[x]
            return x

        for m, refs, gm in parsed:
            keys = [k for k in [m.id, *refs, ("gm:" + gm) if gm else ""] if k]
            for k in keys:
                root.setdefault(k, k)
            for k in keys[1:]:
                root[find(k)] = find(keys[0])
        groups: dict = {}
        for n, (m, refs, gm) in enumerate(parsed):
            keys = [k for k in [m.id, *refs, ("gm:" + gm) if gm else ""] if k]
            groups.setdefault(find(keys[0]) if keys else f"solo:{n}", []).append(m)
        return [Thread(id=k.removeprefix("gm:"), subject=ms[0].subject, messages=ms) for k, ms in groups.items()]


class CommandSource(ThreadSource):
    """Any provider, via your own script: it gets the outgoing message as JSON on stdin and
    prints the thread as JSON (or `null` if there is none). Gmail API, Slack, Teams, ..."""

    def __init__(self, cmd: list, cwd: Path, timeout: float = 20):
        self.cmd, self.cwd, self.timeout = cmd, cwd, timeout

    def threads(self) -> list:
        return []

    def find(self, out: Outgoing) -> Thread | None:
        try:
            p = subprocess.run(self.cmd, input=json.dumps(asdict(out)), capture_output=True, text=True,
                               cwd=str(self.cwd), timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise FuseError(f"thread command failed: {e}") from e
        if p.returncode != 0:
            raise FuseError(f"thread command exited {p.returncode}: {(p.stderr or '').strip()[-200:]}")
        try:
            doc = json.loads(p.stdout or "null")
        except ValueError as e:
            raise FuseError(f"thread command printed invalid JSON: {e}") from e
        try:
            return Thread.from_dict(doc) if isinstance(doc, dict) else None
        except (KeyError, TypeError, ValueError) as e:
            raise FuseError(f"thread command printed a malformed thread: {e}") from e


def open_source(policy: dict) -> ThreadSource | None:
    spec = policy.get("thread_source")
    if not spec:
        return None
    base = Path(policy.get("_base") or ".")
    kind = spec.get("type")
    if kind in ("json", "maildir", "mbox"):
        path = Path(os.path.expanduser(spec.get("path", "")))
        path = path if path.is_absolute() else base / path
        return JsonSource(path) if kind == "json" else MailboxSource(path, kind)
    if kind == "command":
        cmd = spec.get("cmd")
        if isinstance(cmd, str) or not cmd:
            raise FuseError("thread_source.cmd must be a list, e.g. [\"python3\", \"fetch_thread.py\"]")
        return CommandSource([str(c) for c in cmd], base, float(spec.get("timeout", 20)))
    raise FuseError(f"unknown thread_source type {kind!r} (json / maildir / mbox / command)")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. State — ledger, queue and the exclusive send lock, all on LOCAL disk
# ═══════════════════════════════════════════════════════════════════════════════

# File locks are not reliable inside folders that a sync client rewrites behind your back.
# That is exactly how two runs overlapped and sent the same email twice — so we refuse.
SYNCED_MARKERS = ("dropbox", "icloud", "mobile documents", "clouddocs", "cloudstorage", "onedrive",
                  "google drive", "googledrive", "box sync", "syncthing", "nextcloud", "坚果云", "同步")


def synced_folder(path: Path) -> str:
    low = str(path).lower()
    return next((m for m in SYNCED_MARKERS if m in low), "")


class State:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory or default_state_dir()).expanduser()
        hit = synced_folder(self.dir.resolve() if self.dir.exists() else self.dir)
        if hit:
            raise FuseError(f"state dir {self.dir} looks like a synced folder ({hit!r}); file locks are "
                            f"unreliable there. Set SENDFUSE_STATE_DIR to a local, unsynced directory.")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.dir / "ledger.jsonl"
        self.queue_path = self.dir / "queue.json"

    @contextlib.contextmanager
    def lock(self, wait: float = 5.0):
        """One sender at a time, across processes and agents."""
        fh = open(self.dir / "send.lock", "a+")
        deadline = time.monotonic() + wait
        try:
            while True:
                try:
                    if _fcntl:
                        _fcntl.flock(fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    else:
                        fh.seek(0)
                        _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LockBusy("another process is sending right now (send lock is held)")
                    time.sleep(0.05)
            yield
        finally:
            with contextlib.suppress(OSError):
                if _fcntl:
                    _fcntl.flock(fh, _fcntl.LOCK_UN)
                else:
                    fh.seek(0)
                    _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
            fh.close()

    # -- ledger: one JSON object per line ------------------------------------------------
    def ledger(self) -> list:
        """Rows, oldest first. A corrupt ledger raises: we would rather not send than guess."""
        if not self.ledger_path.exists():
            return []
        rows = []
        for n, line in enumerate(self.ledger_path.read_text("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise FuseError(f"ledger {self.ledger_path} line {n} is corrupt — fix or remove it") from e
        return rows

    def _write_ledger(self, rows: list):
        tmp = self.ledger_path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[-2000:]), "utf-8")
        os.replace(tmp, self.ledger_path)

    def reserve(self, v: Verdict, at: dt.datetime, ref: str = "") -> dict:
        row = {"at": at.isoformat(timespec="seconds"), "status": "reserved", "thread": v.thread_key,
               "to": v.outgoing.to, "subject": v.outgoing.subject, "fp": v.outgoing.fingerprint(),
               "via": v.outgoing.via, "ref": ref}
        rows = self.ledger() + [row]
        self._write_ledger(rows)
        return row

    def settle(self, ref: str, status: str) -> bool:
        """Mark a reservation as 'sent' or 'released'. Returns False if it wasn't found."""
        rows, hit = self.ledger(), False
        for r in reversed(rows):
            if r.get("ref") == ref and r.get("status") == "reserved":
                r["status"], hit = status, True
                break
        if hit:
            self._write_ledger(rows)
        return hit

    # -- queue: held / blocked / review items, for a human --------------------------------
    def queue(self) -> list:
        if not self.queue_path.exists():
            return []
        try:
            return json.loads(self.queue_path.read_text("utf-8"))
        except ValueError as e:
            raise FuseError(f"queue {self.queue_path} is corrupt — fix or remove it") from e

    def _write_queue(self, items: list):
        tmp = self.queue_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.queue_path)

    def enqueue(self, v: Verdict, at: dt.datetime) -> int:
        items = self.queue()
        fp = v.outgoing.fingerprint()
        for it in items:          # the same message retried: don't pile up duplicates
            if it["fp"] == fp and it["status"] in ("blocked", "held", "review"):
                it.update(at=at.isoformat(timespec="seconds"), reasons=[asdict(r) for r in v.reasons])
                self._write_queue(items)
                return it["id"]
        qid = max([it["id"] for it in items] or [0]) + 1
        items.append({"id": qid, "at": at.isoformat(timespec="seconds"), "status": {"ask": "review"}.get(
            v.decision, "blocked" if v.decision == "block" else "held"), "fp": fp,
            "outgoing": asdict(v.outgoing), "reasons": [asdict(r) for r in v.reasons]})
        self._write_queue(items)
        return qid

    def set_status(self, qid: int, status: str, at: dt.datetime) -> dict:
        items = self.queue()
        it = next((i for i in items if i["id"] == qid), None)
        if it is None:
            raise FuseError(f"no queue item #{qid}")
        it["status"], it["decided_at"] = status, at.isoformat(timespec="seconds")
        self._write_queue(items)
        return it

    def take_approval(self, fp: str, at: dt.datetime, hours: float) -> bool:
        """A human approval is single-use and expires."""
        items = self.queue()
        for it in items:
            if it["fp"] == fp and it["status"] == "approved":
                if at - parse_time(it.get("decided_at") or it["at"]) <= dt.timedelta(hours=hours):
                    it["status"], it["used_at"] = "used", at.isoformat(timespec="seconds")
                    self._write_queue(items)
                    return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Rules — each one is a small pure function: (context) → Reason | None
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Context:
    out: Outgoing
    thread: Thread | None
    source_error: str
    has_source: bool
    policy: dict
    rows: list               # ledger rows that still count
    now: dt.datetime
    me: set
    key: str


def _when(t: dt.datetime | None) -> str:
    return f"{t:%b} {t.day} {t:%H:%M}" if t else "?"


def _ago(delta: dt.timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 60:
        return f"{max(s, 0)} s ago"
    if s < 3600:
        return f"{max(s // 60, 1)} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def rule_thread_unreadable(c: Context):
    if c.source_error:
        return Reason("thread_unreadable", "block",
                      f"Couldn't read the conversation ({c.source_error}). Not sure → not sending.")


def rule_thread_missing(c: Context):
    if c.thread is None and not c.source_error and c.out.is_reply and c.policy["require_thread_for_replies"]:
        hint = "" if c.has_source else " No thread_source is configured in .send-fuse/policy.json."
        return Reason("thread_missing", "block",
                      "This is a reply, but the conversation it belongs to can't be seen. "
                      "Read the whole thread before replying." + hint)


def rule_not_latest(c: Context):
    if not (c.thread and c.out.in_reply_to):
        return None
    target = c.thread.find(c.out.in_reply_to)
    if target is None or target.date is None:
        return None
    newer = [m for m in c.thread.messages if m.date and m.date > target.date]
    if newer:
        last = newer[-1]
        return Reason("not_latest", "block",
                      f"You're answering the message of {_when(target.date)}, but {len(newer)} newer "
                      f"message(s) exist (latest: {name_of(last.sender)}, {_when(last.date)}).")


def rule_already_settled(c: Context):
    if not (c.thread and is_scheduling(c.out.subject + "\n" + c.out.body)):
        return None
    for m in reversed(c.thread.messages):
        quote = confirmation_in(m)
        if quote:
            who = "You" if m.addr in c.me else name_of(m.sender)
            return Reason("already_settled", "block",
                          f"{who} already confirmed this ({_when(m.date)}): “{quote.rstrip('.。')}” — "
                          f"proposing times again would undo it.")


def rule_already_replied(c: Context):
    if not (c.thread and c.thread.messages and c.me):
        return None
    last = c.thread.messages[-1]
    if last.addr in c.me and last.date:
        age = c.now - last.date
        if age < dt.timedelta(days=c.policy["followup_days"]):
            return Reason("already_replied", "block",
                          f"The last message in this conversation is yours ({_ago(age)}). The ball is in "
                          f"their court — a follow-up is fine after {c.policy['followup_days']} days.")


def rule_same_thread(c: Context):
    cut = c.now - dt.timedelta(hours=c.policy["same_thread_hours"])
    subj, to = norm_subject(c.out.subject), set(c.out.to)
    for r in reversed(c.rows):
        t = parse_time(r["at"])
        same = r.get("thread") == c.key or (subj and norm_subject(r.get("subject", "")) == subj
                                             and to & set(r.get("to") or []))
        if same and t >= cut:
            word = "started sending" if r["status"] == "reserved" else "already sent"
            who = ", ".join(r.get("to") or []) or "them"
            return Reason("same_thread", "block",
                          f"{r.get('via') or 'An agent'} {word} to {who} in this email thread {_ago(c.now - t)} "
                          f"(possibly from another session). One automatic send per thread per "
                          f"{c.policy['same_thread_hours']} h.")


def rule_duplicate(c: Context):
    fp, cut = c.out.fingerprint(), c.now - dt.timedelta(days=c.policy["duplicate_days"])
    for r in reversed(c.rows):
        if r.get("fp") == fp and parse_time(r["at"]) >= cut:
            return Reason("duplicate", "block",
                          f"This exact message already went to {', '.join(c.out.to) or 'them'} "
                          f"{_ago(c.now - parse_time(r['at']))}.")


def rule_daily_cap(c: Context):
    cap = c.policy["daily_cap"]
    today = c.now.date()
    n = sum(1 for r in c.rows if parse_time(r["at"]).astimezone(c.now.tzinfo).date() == today)
    if cap and n >= cap:
        return Reason("daily_cap", "hold", f"{n} automatic sends today already (cap {cap}). Held for a human.")


def quiet_now(q: dict | None, t: dt.datetime) -> str:
    """'' if not quiet, else a human description of why."""
    if not q:
        return ""
    tz = q.get("tz", "local")
    if tz and tz != "local":
        try:
            import zoneinfo
            t = t.astimezone(zoneinfo.ZoneInfo(tz))
        except Exception as e:  # noqa: BLE001 — a bad tz name must stop us, not be ignored
            raise FuseError(f"quiet_hours.tz {tz!r} is not a known time zone: {e}") from e
    if q.get("weekends") and t.weekday() >= 5:
        return f"it's the weekend ({t.strftime('%a %H:%M')})"
    start, end = (dt.time.fromisoformat(q[k]) for k in ("start", "end"))
    cur = t.time()
    inside = start <= cur < end if start < end else (cur >= start or cur < end)
    return f"it's {t.strftime('%H:%M')}, quiet hours are {q['start']}–{q['end']}" if inside else ""


def rule_quiet_hours(c: Context):
    why = quiet_now(c.policy.get("quiet_hours"), c.now)
    if why:
        return Reason("quiet_hours", "hold", f"Not now: {why}. Held, not dropped.")


def rule_review(c: Context):
    mode = c.policy["review"]
    if mode == "always":
        return Reason("review", "ask", "Policy says a human previews every send.")
    if mode == "new_recipient":
        known = {m.addr for m in (c.thread.messages if c.thread else [])}
        known |= {a for r in c.rows if r.get("status") == "sent" for a in r.get("to") or []}
        new = [a for a in c.out.to if a not in known]
        if new:
            return Reason("review", "ask", f"First message to {', '.join(new)} — a human previews it.")


RULES = (rule_thread_unreadable, rule_thread_missing, rule_already_settled, rule_not_latest,
         rule_already_replied, rule_same_thread, rule_duplicate, rule_daily_cap, rule_quiet_hours, rule_review)
RULE_NAMES = [f.__name__[5:] for f in RULES]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. The fuse — library API
# ═══════════════════════════════════════════════════════════════════════════════

class Blocked(Exception):
    def __init__(self, verdict: Verdict):
        super().__init__(verdict.explain())
        self.verdict = verdict


class Fuse:
    """
    >>> fuse = Fuse()
    >>> with fuse.sending(Outgoing(to="sam@x.com", subject="Re: Lunch", body="...", in_reply_to=mid)):
    ...     smtp.send_message(msg)          # only runs if the fuse lets it through
    """

    def __init__(self, policy: dict | None = None, state: State | None = None, source: ThreadSource | None = None):
        self.policy = policy if policy is not None else load_policy()
        self.state = state or State(self.policy.get("state_dir") and Path(self.policy["state_dir"]))
        self._source = source if source is not None else open_source(self.policy)
        self.me = {addr_of(a) for a in self.policy.get("me") or []}

    def _thread(self, out: Outgoing):
        if self._source is None:
            return None, ""
        try:
            return self._source.find(out), ""
        except FuseError as e:
            return None, str(e)

    def evaluate(self, out: Outgoing, at: dt.datetime | None = None) -> Verdict:
        """Pure check: reads the thread and the ledger, writes nothing."""
        at = at or now()
        thread, err = self._thread(out)
        if thread and not out.to:      # a bare "reply" tool: the recipient is whoever wrote last
            others = [m for m in thread.messages if m.addr not in self.me]
            out.to = [others[-1].addr] if others else []
        key = f"thread:{thread.id}" if thread else (f"thread:{out.thread_id}" if out.thread_id else
                                                   f"to:{','.join(sorted(out.to))}|{norm_subject(out.subject)}")
        reserve_cut = at - dt.timedelta(minutes=self.policy["reserve_minutes"])
        rows = [r for r in self.state.ledger()
                if r.get("status") == "sent" or (r.get("status") == "reserved" and parse_time(r["at"]) >= reserve_cut)]
        ctx = Context(out, thread, err, self._source is not None, self.policy, rows, at, self.me, key)
        off = set(self.policy.get("rules_off") or [])
        reasons = [r for f in RULES if f.__name__[5:] not in off for r in [f(ctx)] if r]
        decision = max((r.effect for r in reasons), key=EFFECT_RANK.get, default="allow")
        return Verdict(decision, reasons, out, key)

    def check(self, out: Outgoing, at: dt.datetime | None = None, record: bool = False, ref: str = "",
              queue: bool = True) -> Verdict:
        """Evaluate under the lock. With record=True an allowed send is reserved in the ledger,
        so a parallel agent sees it immediately. Non-allowed sends go to the review queue."""
        at = at or now()
        with self.state.lock():
            v = self.evaluate(out, at)
            if not v.allowed and self.state.take_approval(out.fingerprint(), at, self.policy["approval_hours"]):
                v.decision, v.approved = "allow", True
            if v.allowed and record:
                self.state.reserve(v, at, ref)
            elif not v.allowed and queue:
                v.queue_id = self.state.enqueue(v, at)
        return v

    @contextlib.contextmanager
    def sending(self, out: Outgoing, at: dt.datetime | None = None):
        """Hold the lock for the whole send. Raises Blocked if the fuse says no."""
        at = at or now()
        ref = f"lib:{os.getpid()}:{time.time_ns()}"
        with self.state.lock():
            v = self.evaluate(out, at)
            if not v.allowed and self.state.take_approval(out.fingerprint(), at, self.policy["approval_hours"]):
                v.decision, v.approved = "allow", True
            if not v.allowed:
                v.queue_id = self.state.enqueue(v, at)
                raise Blocked(v)
            self.state.reserve(v, at, ref)      # record first, then send (a crash can't cause a resend)
            try:
                yield v
            except BaseException:
                self.state.settle(ref, "released")
                raise
            self.state.settle(ref, "sent")


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Claude Code hook adapter (PreToolUse / PostToolUse)
# ═══════════════════════════════════════════════════════════════════════════════

SEND_VERBS = {"send", "reply", "respond", "post", "forward", "fwd", "publish", "tweet", "retweet", "toot",
              "comment", "broadcast", "notify"}
LEAD_VERBS = {"message", "email", "mail", "text", "dm", "sms"}      # send verbs only when they come first
READ_VERBS = {"get", "list", "search", "read", "fetch", "find", "query", "view", "show", "count", "describe",
              "download", "check", "lookup", "watch"}
BASH_SEND = re.compile(
    r"\b(sendmail|mailx|msmtp|swaks|mutt\s+-s|mail\s+-s|smtplib|gws\s+gmail\s+\S*send|gh\s+(?:issue|pr)\s+comment)\b"
    r"|curl\b[^|;&]*(?:api\.sendgrid\.com|api\.mailgun\.net|api\.postmarkapp\.com|api\.resend\.com|"
    r"slack\.com/api/chat\.postMessage|hooks\.slack\.com|api\.telegram\.org/\S*/send|discord(?:app)?\.com/api/webhooks|"
    r"api\.twitter\.com|api\.x\.com|graph\.facebook\.com|api\.linkedin\.com|gmail\.googleapis\.com\S*send)", re.I)
HOOK_MATCHER = ("[Ss]end|[Rr]eply|[Rr]espond|[Pp]ost|[Ff]orward|[Pp]ublish|[Tt]weet|[Cc]omment|[Bb]roadcast|"
                "[Nn]otify|[Mm]ail|[Mm]essage|[Ss]ms|^Bash$")

TO_KEYS = ("to", "recipient", "recipients", "to_email", "to_address", "email", "address", "cc", "bcc",
           "channel", "channel_id", "chat_id", "user", "user_id", "username", "handle", "phone")
SUBJECT_KEYS = ("subject", "title")
BODY_KEYS = ("body", "text", "message", "content", "html", "html_body", "plain", "markdown", "comment", "caption")
THREAD_KEYS = ("thread_id", "threadId", "thread_ts", "thread", "conversation_id", "conversationId")
REPLY_KEYS = ("in_reply_to", "inReplyTo", "reply_to_message_id", "replyToMessageId", "message_id", "messageId",
              "parent_id", "parentId")


def _tokens(name: str) -> list:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t]


def is_send_tool(tool: str, policy: dict) -> bool:
    if any(re.search(p, tool) for p in policy.get("ignore_tools") or []):
        return False
    if any(re.search(p, tool) for p in policy.get("guard_tools") or []):
        return True
    if tool in ("Bash", "PowerShell"):
        return False             # handled by bash_is_send
    part = tool.split("__")[-1] if tool.startswith("mcp__") else tool
    toks = _tokens(part)
    if not toks or READ_VERBS & set(toks):
        return False
    return bool(SEND_VERBS & set(toks)) or toks[0] in LEAD_VERBS


def _flat(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [x for i in v for x in _flat(i)]
    if isinstance(v, dict):
        return _flat(v.get("email") or v.get("address") or v.get("id") or v.get("name"))
    return [str(v)]


def _first(ti: dict, keys) -> str:
    for k in keys:
        vals = _flat(ti.get(k))
        if vals:
            return "\n".join(vals)
    return ""


def outgoing_from_tool(tool: str, ti: dict) -> Outgoing:
    to = [x for k in TO_KEYS for x in _flat(ti.get(k))]
    return Outgoing(to=to, subject=_first(ti, SUBJECT_KEYS), body=_first(ti, BODY_KEYS),
                    thread_id=_first(ti, THREAD_KEYS), in_reply_to=_first(ti, REPLY_KEYS), via=tool)


def _decision(kind: str, reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                              "permissionDecisionReason": reason}}, ensure_ascii=False)


def _preview(out: Outgoing, width: int = 400) -> str:
    body = out.body if len(out.body) <= width else out.body[:width] + "…"
    return f"To: {', '.join(out.to) or '?'}\nSubject: {out.subject or '(none)'}\n\n{body}"


def hook(data: dict, policy: dict | None = None, state: State | None = None) -> tuple:
    """Returns (exit code, stdout, stderr). Split out so tests can call it directly."""
    event = data.get("hook_event_name", "")
    tool, ti = data.get("tool_name", ""), data.get("tool_input") or {}
    ref = data.get("tool_use_id") or ""
    cwd = Path(data.get("cwd") or os.getcwd())
    bash_send = tool in ("Bash", "PowerShell") and bool(BASH_SEND.search(ti.get("command") or ""))
    try:
        policy = policy or load_policy(start=cwd)
    except FuseError as e:      # broken policy: still let non-sends through, but stop every send
        if bash_send or is_send_tool(tool, DEFAULT_POLICY):
            if event == "PreToolUse":
                return 0, _decision("deny", f"send-fuse error ({e}). Failing closed: the message was not sent."), ""
            return 1, "", f"send-fuse error: {e}"
        return 0, "", ""
    try:
        if not (is_send_tool(tool, policy) or bash_send):
            return 0, "", ""
        fuse = Fuse(policy, state)
        if event == "PostToolUse":
            fuse.state.settle(ref, "sent")
            return 0, "", ""
        if event != "PreToolUse":
            return 0, "", ""
        if bash_send:
            mode = policy["bash"]
            if mode == "off":
                return 0, "", ""
            why = quiet_now(policy.get("quiet_hours"), now())
            cmd = (ti.get("command") or "")[:300]
            if mode == "block" or why:
                return 0, _decision("deny", "send-fuse: this shell command sends a message"
                                    + (f", and {why}" if why else "") + f". Not running it:\n{cmd}"), ""
            return 0, _decision("ask", f"send-fuse: this shell command sends a message — preview it:\n{cmd}"), ""
        out = outgoing_from_tool(tool, ti)
        v = fuse.check(out, record=True, ref=ref, queue=True)
        if v.allowed:
            return 0, "", ""
        text = v.explain(color=False)
        if v.decision == "ask":
            return 0, _decision("ask", text + "\n\n" + _preview(v.outgoing)), ""
        tail = ("\nDo not retry or rephrase to get around this. Tell the user what send-fuse found."
                if v.decision == "block" else "\nIt is held, not lost. Tell the user it is waiting in the queue.")
        return 0, _decision("deny", text + tail), ""
    except Exception as e:  # noqa: BLE001 — a broken fuse must fail CLOSED for sends, loudly
        msg = f"send-fuse error ({type(e).__name__}: {e}). Failing closed: the message was not sent."
        if event == "PreToolUse":
            return 0, _decision("deny", msg), ""
        return 1, "", msg


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CLI
# ═══════════════════════════════════════════════════════════════════════════════

EXIT = {"allow": 0, "block": 2, "hold": 3, "ask": 4}


def _outgoing_from_args(a) -> Outgoing:
    body = a.body or ""
    if a.body_file:
        body = sys.stdin.read() if a.body_file == "-" else Path(a.body_file).read_text("utf-8")
    return Outgoing(to=a.to or [], subject=a.subject or "", body=body, thread_id=a.thread or "",
                    in_reply_to=a.in_reply_to or "", via=a.via or "cli")


def _cols() -> int:
    return min(shutil.get_terminal_size((100, 30)).columns, 120)


def cmd_check(a) -> int:
    fuse = Fuse()
    v = fuse.check(_outgoing_from_args(a), record=False, queue=not a.dry)
    print(json.dumps(v.to_dict(), ensure_ascii=False, indent=1) if a.json
          else v.explain(color_enabled(sys.stdout), _cols()))
    return EXIT[v.decision]


def cmd_run(a) -> int:
    """Run the real send command only if the fuse allows it; the lock is held throughout."""
    if not a.command:
        print("send-fuse run: give the send command after --", file=sys.stderr)
        return 64
    fuse = Fuse()
    try:
        with fuse.sending(_outgoing_from_args(a)) as v:
            print(v.explain(color_enabled(sys.stderr), _cols()), file=sys.stderr)
            rc = subprocess.run(a.command).returncode
            if rc != 0:
                raise subprocess.CalledProcessError(rc, a.command)
    except Blocked as b:
        print(b.verdict.explain(color_enabled(sys.stderr), _cols()), file=sys.stderr)
        return EXIT[b.verdict.decision]
    except subprocess.CalledProcessError as e:
        print(f"send-fuse: send command failed (exit {e.returncode}); reservation released", file=sys.stderr)
        return e.returncode
    return 0


def cmd_hook(a) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError as e:
        print(_decision("deny", f"send-fuse error: unreadable hook input ({e}). Failing closed."))
        return 0
    code, out, err = hook(data)
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


def cmd_queue(a) -> int:
    st = State(load_policy().get("state_dir"))
    items = [i for i in st.queue() if a.all or i["status"] in ("blocked", "held", "review")]
    if not items:
        print("send-fuse queue: empty")
        return 0
    c = _Ansi(color_enabled(sys.stdout))
    for it in items:
        o = Outgoing.from_dict(it["outgoing"])
        print(c.bold(f"#{it['id']}  [{it['status']}]  {it['at']}  via {o.via or '?'}"))
        print("    " + _preview(o, 240).replace("\n", "\n    "))
        for r in it["reasons"]:
            print(f"    {'✗' if r['effect'] == 'block' else '…'} {r['rule']}: {r['message']}")
        print()
    print(c.dim("approve: send-fuse approve ID   ·   drop: send-fuse drop ID"))
    return 0


def cmd_decide(a, status: str) -> int:
    st = State(load_policy().get("state_dir"))
    it = st.set_status(a.id, status, now())
    o = Outgoing.from_dict(it["outgoing"])
    if status == "approved":
        print(f"#{a.id} approved for one send to {', '.join(o.to)} (valid "
              f"{load_policy()['approval_hours']} h). Ask the agent to retry.")
    else:
        print(f"#{a.id} dropped.")
    return 0


def cmd_log(a) -> int:
    st = State(load_policy().get("state_dir"))
    for r in st.ledger()[-a.n:]:
        print(f"{r['at']}  {r['status']:<9} {', '.join(r.get('to') or []):<32} {r.get('subject', '')[:50]}  "
              f"via {r.get('via') or '?'}")
    return 0


def settings_entry(command: str) -> dict:
    return {"matcher": HOOK_MATCHER, "hooks": [{"type": "command", "command": command, "timeout": 30}]}


def cmd_init(a) -> int:
    root = Path(a.dir).resolve()
    d = root / PROJECT_DIR
    d.mkdir(parents=True, exist_ok=True)
    pol = d / POLICY_FILE
    if not pol.exists():
        starter = {k: DEFAULT_POLICY[k] for k in ("me", "thread_source", "same_thread_hours", "followup_days",
                                                  "daily_cap", "quiet_hours", "review")}
        starter["me"] = a.me or ["you@example.com"]
        pol.write_text(json.dumps(starter, indent=2, ensure_ascii=False) + "\n", "utf-8")
        print(f"wrote {pol.relative_to(root)}  ← put your own addresses in \"me\", and a thread_source")
    shutil.copyfile(Path(__file__).resolve(), d / "sendfuse.py")
    print(f"copied engine → {(d / 'sendfuse.py').relative_to(root)}")
    if a.claude:
        sp = root / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
        command = 'python3 "$CLAUDE_PROJECT_DIR"/.send-fuse/sendfuse.py hook'
        hooks = doc.setdefault("hooks", {})
        for ev in ("PreToolUse", "PostToolUse"):
            lst = hooks.setdefault(ev, [])
            if not any("sendfuse.py hook" in h.get("command", "") for e in lst for h in e.get("hooks", [])):
                lst.append(settings_entry(command))
        sp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "utf-8")
        print(f"hooked into {sp.relative_to(root)} (PreToolUse + PostToolUse)")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Demo — the real incident, replayed with fake data. Never sends anything.
# ═══════════════════════════════════════════════════════════════════════════════

DEMO_THREAD = {
    "id": "thread-7731", "subject": "Interview — Data Engineer, Northwind",
    "messages": [
        {"id": "<m1@northwind.example>", "from": "Jordan (Northwind Recruiting) <jordan@northwind.example>",
         "to": ["you@example.com"], "date": "2026-11-17T09:12:00-05:00",
         "subject": "Interview — Data Engineer, Northwind",
         "body": "Hi! Thanks for applying. Could you share a few times for a 45-minute call this week?"},
        {"id": "<m2@example.com>", "from": "You <you@example.com>", "to": ["jordan@northwind.example"],
         "date": "2026-11-17T10:30:00-05:00", "subject": "Re: Interview — Data Engineer, Northwind",
         "body": "Thanks Jordan. Thursday at 2pm would be great."},
        {"id": "<m3@northwind.example>", "from": "Jordan (Northwind Recruiting) <jordan@northwind.example>",
         "to": ["you@example.com"], "date": "2026-11-17T11:05:00-05:00",
         "subject": "Re: Interview — Data Engineer, Northwind",
         "body": "Confirmed — see you Thursday, Nov 20 at 2:00 PM. Calendar invite attached.",
         "attachments": ["invite.ics"]},
    ]}
DEMO_REPLY = ("Hi Jordan, thanks for reaching out! Here are three times that work for me: "
              "Mon 10am, Tue 2pm or Wed 4pm. Let me know which one suits you.")


def _say(line: str = "", pause: float = 0.0):
    print(line, flush=True)
    if pause:
        time.sleep(pause)


def cmd_demo(a) -> int:
    c = _Ansi(a.color == "always" or (a.color == "auto" and color_enabled(sys.stdout)))
    p = 0.0 if a.fast else 0.6
    w = min(shutil.get_terminal_size((100, 30)).columns, 110)
    with tempfile.TemporaryDirectory(prefix="send-fuse-demo-") as tmp:
        tmp = Path(tmp)
        (tmp / "thread.json").write_text(json.dumps(DEMO_THREAD), "utf-8")
        policy = json.loads(json.dumps(DEFAULT_POLICY))      # the demo ignores your own policy on purpose
        policy.update(_base=str(tmp), me=["you@example.com"], thread_source={"type": "json", "path": str(tmp / "thread.json")},
                      quiet_hours={"start": "21:00", "end": "08:00", "tz": "America/Toronto", "weekends": False})
        fuse = Fuse(policy, State(tmp / "state"))
        t = Thread.from_dict(DEMO_THREAD)
        _say(c.bold("send-fuse demo") + c.dim("  — a real incident, replayed with fake data. Nothing is sent."), p)
        _say()
        _say(c.bold("Inbox · ") + t.subject)
        for m in t.messages:
            att = c.dim(f"  [{m.attachments[0]}]") if m.attachments else ""
            _say(f"  {_when(m.date):<13} {name_of(m.sender):<7} {clip(m.body, 58)}{att}", p / 2)
        _say()
        _say(c.dim("Two days later the agent re-reads Jordan's first email as if it were new, and drafts:"), p)
        _say(f"  {c.yellow('agent →')} reply to Jordan's 1st email: “{clip(DEMO_REPLY, 60)}”", p)
        _say()
        _say(f"{c.red('✗ without send-fuse')}  sent. Jordan gets a 2nd scheduling email for an interview")
        _say("                      that is already booked. You write “please ignore” by hand.", p)
        _say()
        out = Outgoing(to=[], subject="Re: " + t.subject, body=DEMO_REPLY, in_reply_to="<m1@northwind.example>",
                       via="agent")
        v = fuse.check(out, at=parse_time("2026-11-19T09:40:00-05:00"), record=True)
        _say(f"{c.green('✓ with send-fuse')}")
        _say("  " + v.explain(color=c.on, width=w - 2).replace("\n", "\n  "), p)
        _say()
        _say(c.bold("Two agents, same thread, same minute") + c.dim("  (the other half of the incident)"))
        fuse2 = Fuse(dict(policy, thread_source=None), State(tmp / "state2"))
        o2 = Outgoing(to=["sam@acme.example"], subject="Quarterly report", body="Report attached.", via="agent A")
        v1 = fuse2.check(o2, at=parse_time("2026-11-19T10:00:00-05:00"), record=True)
        o3 = Outgoing(to=["sam@acme.example"], subject="Quarterly report", body="Here is the report!", via="agent B")
        v2 = fuse2.check(o3, at=parse_time("2026-11-19T10:00:30-05:00"), record=True)
        _say(f"  agent A → {c.green('ALLOWED')}  (reserved in the ledger under the lock)")
        _say(f"  agent B → {c.red('BLOCKED')}  " + "\n".join(textwrap.wrap(
            v2.reasons[0].message, w, initial_indent=" " * 22, subsequent_indent=" " * 22)).lstrip(), p)
        _say()
        _say(c.bold("23:40, an agent wants to post a status update"))
        o4 = Outgoing(to=["#team"], subject="Status", body="Deploy finished.", via="agent")
        v4 = fuse2.check(o4, at=parse_time("2026-11-19T23:40:00-05:00"), record=True)
        _say(f"  → {c.yellow('HELD')}  {v4.reasons[0].message}", p)
        _say()
        _say(c.dim("Install: curl -fsSL https://raw.githubusercontent.com/JohnSUNLei/hands-off/main/send-fuse/sendfuse.py -o sendfuse.py"))
        _say(c.dim("Hook it into Claude Code: python3 sendfuse.py init --claude"))
    return 0 if (v.decision == "block" and v2.decision == "block" and v4.decision == "hold") else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="send-fuse", description="A fuse between your AI agent and the Send button.")
    ap.add_argument("-V", "--version", action="version", version=f"send-fuse {VERSION}")
    sub = ap.add_subparsers(dest="cmd")

    def msg_args(p):
        p.add_argument("--to", action="append", help="recipient (repeatable)")
        p.add_argument("--subject", default="")
        p.add_argument("--body", default="")
        p.add_argument("--body-file", help="read the body from a file ('-' = stdin)")
        p.add_argument("--thread", help="thread / conversation id")
        p.add_argument("--in-reply-to", help="id of the message being answered")
        p.add_argument("--via", help="who is sending (shown in the ledger)")

    p = sub.add_parser("check", help="would this message be allowed? (exit 0 allow, 2 block, 3 hold, 4 ask)")
    msg_args(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry", action="store_true", help="don't add non-allowed messages to the queue")
    p = sub.add_parser("run", help="run a send command only if the fuse allows it:  run --to X ... -- CMD ARGS")
    msg_args(p)
    p.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("hook", help="Claude Code hook entry point (reads the event JSON on stdin)")
    p = sub.add_parser("queue", help="messages waiting for a human")
    p.add_argument("--all", action="store_true")
    for name in ("approve", "drop"):
        p = sub.add_parser(name, help=f"{name} a queued message")
        p.add_argument("id", type=int)
    p = sub.add_parser("log", help="recent sends seen by the fuse")
    p.add_argument("-n", type=int, default=20)
    p = sub.add_parser("init", help="set up .send-fuse/ in a project (and --claude to add the hook)")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--claude", action="store_true", help="add PreToolUse/PostToolUse hooks to .claude/settings.json")
    p.add_argument("--me", action="append", help="your own address (repeatable)")
    p = sub.add_parser("demo", help="replay the incident with fake data (sends nothing)")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "run" and a.command and a.command[0] == "--":
        a.command = a.command[1:]
    handlers = {"check": cmd_check, "run": cmd_run, "hook": cmd_hook, "queue": cmd_queue, "log": cmd_log,
                "init": cmd_init, "demo": cmd_demo,
                "approve": lambda x: cmd_decide(x, "approved"), "drop": lambda x: cmd_decide(x, "dropped")}
    if a.cmd not in handlers:
        build_parser().print_help()
        return 64
    try:
        return handlers[a.cmd](a)
    except FuseError as e:
        print(f"send-fuse: {e}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    sys.exit(main())
