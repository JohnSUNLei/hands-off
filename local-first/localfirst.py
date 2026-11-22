#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""local-first — an OpenAI-compatible proxy that runs what it can on your own machine.

Point any OpenAI-compatible client at http://127.0.0.1:8484/v1. Each request is routed by
plain, readable rules (task type, prompt length, tags, images, tools) to a local backend
(LM Studio, Ollama, llama.cpp, vLLM…) or to a cloud backend — and every request lands in a
ledger, so `local-first bill` can give you an estimate of what this month cost and what it would
have cost if everything had gone to the cloud.

Two promises, both enforced in code and tests:
  * No silent fallback. If the local backend is down or its model isn't loaded, the request
    fails with a clear error. It is never quietly sent to the cloud, never swapped to another
    model, never answered from a cache.
  * Private stays private. Requests tagged `private` — or containing something that looks like
    a secret — are refused rather than sent to a cloud backend.

Routing is deterministic: regular expressions and numbers, no model decides where your prompt
goes. One file, Python standard library only.

    python3 localfirst.py demo        # offline demo with fake backends, nothing leaves the machine
    python3 localfirst.py init        # write local-first.json
    python3 localfirst.py check       # are the backends really up? (live probe)
    python3 localfirst.py serve       # run the proxy
    python3 localfirst.py bill        # this month's estimated bill
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import random
import re
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1.0"
CONFIG_FILE = "local-first.json"


class LocalFirstError(Exception):
    """A request we refuse to serve, with the HTTP status and a message meant for humans."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": self.message, "source": "local-first"}}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Configuration — the ONLY place that reads environment variables or config files
# ═══════════════════════════════════════════════════════════════════════════════
#   LOCALFIRST_CONFIG  path to local-first.json (overrides discovery)
#   LOCALFIRST_HOME    where the ledger lives (default ~/.local-first)
#   LOCALFIRST_NOW     ISO timestamp that pins "now" (tests, demos)
#   <backend.key_env>  the cloud API key, e.g. OPENAI_API_KEY — read at request time, never stored


def getenv(name: str, default: str | None = None) -> str | None:
    """The one function in this file that touches os.environ."""
    return os.environ.get(name, default)


DEFAULT_TASKS = {
    "reasoning": [r"\bprove\b", r"\bstep[- ]by[- ]step\b", r"\barchitecture\b", r"\btrade-?offs?\b",
                  r"\broot cause\b", r"证明", r"架构", r"权衡", r"根因"],
    "summarize": [r"\bsummari[sz]e", r"\btl;?dr\b", r"\bkey points\b", r"总结", r"摘要", r"概括"],
    "translate": [r"\btranslat", r"翻译", r"译成"],
    "extract": [r"\bextract", r"\bparse\b", r"\bpull out\b", r"提取", r"抽取"],
    "classify": [r"\bclassif", r"\bcategori[sz]e", r"\btag (?:this|these|each)\b", r"分类", r"打标"],
    "code": [r"```", r"\bdef \w+\(", r"\bfunction\b", r"\brefactor", r"\bunit tests?\b", r"写代码", r"重构"],
}

DEFAULT_CONFIG = {
    "listen": "127.0.0.1:8484",
    "backends": {
        "lmstudio": {"url": "http://127.0.0.1:1234/v1", "local": True, "model": "", "model_hint": "",
                     "extra": {"reasoning_effort": "none"}, "timeout": 600},
        "ollama": {"url": "http://127.0.0.1:11434/v1", "local": True, "model": "", "model_hint": "", "timeout": 600},
        "cloud": {"url": "https://api.openai.com/v1", "local": False, "model": "", "key_env": "OPENAI_API_KEY",
                  "price": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}, "timeout": 120},
    },
    "routes": [
        {"name": "private stays here", "when": {"tags": ["private"]}, "to": "lmstudio"},
        {"name": "asked for the cloud", "when": {"tags": ["cloud"]}, "to": "cloud"},
        {"name": "hard reasoning", "when": {"task": "reasoning"}, "to": "cloud"},
        {"name": "very long prompt", "when": {"min_tokens": 32000}, "to": "cloud"},
        {"name": "routine work", "when": {"task": ["summarize", "translate", "extract", "classify", "code"]},
         "to": "lmstudio"},
        {"name": "everything else", "to": "lmstudio"},
    ],
    "keep_local_tags": ["private"],
    "secrets_stay_local": True,
    "compare_price": None,            # what local tokens would have cost; default = first cloud backend's price
    "tasks": None,                    # override DEFAULT_TASKS
}
WHEN_KEYS = {"tags", "task", "min_tokens", "max_tokens", "has_images", "has_tools", "model", "match"}


def find_config(start: Path | None = None) -> Path | None:
    explicit = getenv("LOCALFIRST_CONFIG")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise LocalFirstError(500, "config_error", f"LOCALFIRST_CONFIG points to {p}, which does not exist")
        return p
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / CONFIG_FILE).is_file():
            return d / CONFIG_FILE
    p = Path.home() / ".config" / "local-first" / CONFIG_FILE
    return p if p.is_file() else None


def load_config(path: Path | None = None, start: Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = path or find_config(start)
    if p is not None:
        try:
            user = json.loads(Path(p).read_text("utf-8"))
        except (OSError, ValueError) as e:
            raise LocalFirstError(500, "config_error", f"cannot read {p}: {e}") from e
        unknown = sorted(k for k in user if k not in DEFAULT_CONFIG and not k.startswith("_"))
        if unknown:
            raise LocalFirstError(500, "config_error", f"{p}: unknown key(s) {', '.join(unknown)}")
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
        cfg["_file"] = str(p)
    validate(cfg)
    return cfg


def validate(cfg: dict):
    bad = lambda msg: LocalFirstError(500, "config_error", msg)  # noqa: E731
    backends = cfg.get("backends") or {}
    if not backends:
        raise bad("no backends configured")
    for name, b in backends.items():
        if not str(b.get("url", "")).startswith(("http://", "https://")):
            raise bad(f"backend {name!r}: url must start with http:// or https://")
        if "local" not in b:
            raise bad(f"backend {name!r}: say explicitly whether it is local (\"local\": true/false)")
        if b.get("api_key"):
            raise bad(f"backend {name!r}: don't put keys in the config — use \"key_env\": \"ENV_VAR_NAME\"")
    routes = cfg.get("routes") or []
    if not routes or routes[-1].get("when"):
        raise bad("the last route must have no \"when\" (it is the default)")
    for r in routes:
        if r.get("to") not in backends:
            raise bad(f"route {r.get('name')!r} goes to unknown backend {r.get('to')!r}")
        unknown = set(r.get("when") or {}) - WHEN_KEYS
        if unknown:
            raise bad(f"route {r.get('name')!r}: unknown condition(s) {', '.join(sorted(unknown))}")
    for name, pats in (cfg.get("tasks") or DEFAULT_TASKS).items():
        for p in pats:
            try:
                re.compile(p)
            except re.error as e:
                raise bad(f"task {name!r}: bad pattern {p!r}: {e}") from e


def home_dir() -> Path:
    return Path(getenv("LOCALFIRST_HOME") or Path.home() / ".local-first").expanduser()


def now() -> dt.datetime:
    pinned = getenv("LOCALFIRST_NOW")
    return dt.datetime.fromisoformat(pinned) if pinned else dt.datetime.now().astimezone()


def api_key(backend: dict) -> str:
    name = backend.get("key_env") or ""
    return (getenv(name) or "") if name else ""


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Data model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Request:
    """What the router needs to know about one chat request."""
    model: str = "auto"
    tags: list = field(default_factory=list)
    task: str = ""
    tokens: int = 0
    has_images: bool = False
    has_tools: bool = False
    text: str = ""                 # all message text, for secret scanning
    last_user: str = ""            # the last user message, for task detection / match


@dataclass
class Route:
    backend: str
    rule: str
    why: list                      # one line per rule considered: why it did / didn't match


SECRET_PATTERNS = [re.compile(p) for p in (
    r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}", r"\bAKIA[0-9A-Z]{16}\b", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    r"\bxox[abpr]-[A-Za-z0-9-]{10,}", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\bAIza[0-9A-Za-z_\-]{35}\b", r"(?i)\b(?:password|passwd|api[_-]?key|secret)\s*[:=]\s*\S{8,}",
)]


def estimate_tokens(text: str) -> int:
    """Rough and deterministic: CJK ≈ 1 token per character, everything else ≈ 4 characters per token."""
    cjk = sum(1 for c in text if "⺀" <= c <= "鿿" or "가" <= c <= "힯")
    return cjk + (len(text) - cjk + 3) // 4


def _content_text(content) -> tuple:
    """(text, has_image) from an OpenAI message content (string or list of parts)."""
    if isinstance(content, str):
        return content, False
    text, img = [], False
    for part in content or []:
        if isinstance(part, dict):
            if part.get("type") == "text":
                text.append(str(part.get("text", "")))
            elif part.get("type") in ("image_url", "input_image", "image"):
                img = True
    return "\n".join(text), img


def detect_task(text: str, tasks: dict) -> str:
    for name, pats in tasks.items():
        if any(re.search(p, text, re.I) for p in pats):
            return name
    return ""


def parse_request(body: dict, headers: dict, tasks: dict) -> Request:
    """Read tags from the X-Local-First-Tags header, a body field "tags", or "auto:tag1,tag2" as the model."""
    model = str(body.get("model") or "auto")
    tags = [t for t in re.split(r"[,\s]+", headers.get("x-local-first-tags", "")) if t]
    tags += [str(t) for t in (body.get("tags") or [])]
    if ":" in model and model.split(":", 1)[0] in ("auto", "local-first"):
        model, extra = model.split(":", 1)
        tags += [t for t in extra.split(",") if t]
    texts, imgs, last_user = [], False, ""
    for m in body.get("messages") or []:
        t, img = _content_text(m.get("content"))
        texts.append(t)
        imgs = imgs or img
        if m.get("role") == "user":
            last_user = t
    text = "\n".join(texts)
    task = headers.get("x-local-first-task", "") or detect_task(last_user, tasks)
    return Request(model=model, tags=sorted(set(t.lower() for t in tags)), task=task,
                   tokens=estimate_tokens(text), has_images=imgs, has_tools=bool(body.get("tools")),
                   text=text, last_user=last_user)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Router — pure functions, no I/O
# ═══════════════════════════════════════════════════════════════════════════════

def _check(cond: str, want, r: Request) -> tuple:
    if cond == "tags":
        want = [want] if isinstance(want, str) else want
        hit = [t for t in want if t in r.tags]
        return bool(hit), f"tags {want} {'∋' if hit else '∌'} request tags {r.tags or '[]'}"
    if cond == "task":
        want = [want] if isinstance(want, str) else want
        return r.task in want, f"task is {r.task or 'none'}"
    if cond == "min_tokens":
        return r.tokens >= want, f"~{r.tokens} tokens {'≥' if r.tokens >= want else '<'} {want}"
    if cond == "max_tokens":
        return r.tokens <= want, f"~{r.tokens} tokens {'≤' if r.tokens <= want else '>'} {want}"
    if cond == "has_images":
        return r.has_images == bool(want), f"images: {r.has_images}"
    if cond == "has_tools":
        return r.has_tools == bool(want), f"tools: {r.has_tools}"
    if cond == "model":
        hit = bool(re.search(want, r.model))
        return hit, f"model {r.model!r} {'matches' if hit else 'does not match'} /{want}/"
    if cond == "match":
        hit = bool(re.search(want, r.last_user, re.I))
        return hit, f"prompt {'matches' if hit else 'does not match'} /{want}/"
    raise LocalFirstError(500, "config_error", f"unknown condition {cond}")


def looks_secret(text: str) -> str:
    for p in SECRET_PATTERNS:
        m = p.search(text)
        if m:
            s = m.group(0)
            return s[:6] + "…" if len(s) > 6 else s
    return ""


def route(cfg: dict, r: Request) -> Route:
    """Pick a backend. Raises LocalFirstError when the request must not go where the rules point."""
    backends = cfg["backends"]
    why = []
    if r.model in backends:                       # the client named a backend explicitly
        chosen = Route(r.model, f"model={r.model}", [f"client asked for backend {r.model!r}"])
    elif r.model == "local":
        name = next((n for n, b in backends.items() if b["local"]), None)
        if name is None:
            raise LocalFirstError(400, "no_local_backend", "model 'local' requested, but no local backend is configured")
        chosen = Route(name, "model=local", [f"client asked for local → {name}"])
    else:
        chosen = None
        for rule in cfg["routes"]:
            conds = rule.get("when") or {}
            results = [_check(k, v, r) for k, v in conds.items()]
            ok = all(hit for hit, _ in results)
            detail = "; ".join(d for _, d in results) or "default"
            why.append(f"{'→' if ok else '·'} {rule.get('name', rule['to'])}: {detail}")
            if ok:
                chosen = Route(rule["to"], rule.get("name", rule["to"]), why)
                break
    b = backends[chosen.backend]
    if not b["local"]:
        private = [t for t in r.tags if t in (cfg.get("keep_local_tags") or [])]
        if private:
            raise LocalFirstError(403, "kept_local",
                                  f"Tagged {private[0]!r}: local-first never sends it to a cloud backend "
                                  f"({chosen.backend}). Route it to a local backend, or remove the tag.")
        secret = looks_secret(r.text) if cfg.get("secrets_stay_local") else ""
        if secret:
            raise LocalFirstError(403, "kept_local",
                                  f"The prompt contains something that looks like a secret ({secret}). "
                                  f"local-first won't send it to a cloud backend ({chosen.backend}).")
    return chosen


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Upstream — the wrapper layer: the only code that talks HTTP to model servers
# ═══════════════════════════════════════════════════════════════════════════════

class Upstream:
    """One OpenAI-compatible backend. Model names are resolved against the live /models list,
    never assumed: a model that isn't there is an error, not a reason to pick another one."""
    _models_cache: dict = {}
    _lock = threading.Lock()

    def __init__(self, name: str, spec: dict):
        self.name, self.spec = name, spec
        self.url = spec["url"].rstrip("/")
        self.local = bool(spec["local"])

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": f"local-first/{VERSION}"}
        key = api_key(self.spec)
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _unavailable(self, err) -> LocalFirstError:
        if self.local:
            return LocalFirstError(503, "local_unavailable",
                                   f"Local backend {self.name!r} at {self.url} is not answering ({err}). "
                                   f"local-first does NOT fall back to the cloud. Start it, or send this request "
                                   f"to the cloud on purpose (model \"cloud\" or tag \"cloud\").")
        return LocalFirstError(502, "cloud_unavailable",
                               f"Cloud backend {self.name!r} at {self.url} failed ({err}). Not retrying elsewhere.")

    def models(self, max_age: float = 30.0) -> list:
        key = self.url
        with self._lock:
            hit = self._models_cache.get(key)
            if hit and time.monotonic() - hit[0] < max_age:
                return hit[1]
        req = urllib.request.Request(self.url + "/models", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                ids = [m.get("id", "") for m in json.loads(resp.read()).get("data", [])]
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise self._unavailable(getattr(e, "reason", e)) from e
        with self._lock:
            self._models_cache[key] = (time.monotonic(), ids)
        return ids

    def resolve_model(self) -> str:
        want, hint = self.spec.get("model") or "", (self.spec.get("model_hint") or "").lower()
        if not self.local:
            if not want:
                raise LocalFirstError(500, "config_error", f"cloud backend {self.name!r} has no \"model\" set")
            return want
        ids = self.models()
        chat = [m for m in ids if "embed" not in m.lower()]
        if want:
            if want in ids:
                return want
            raise LocalFirstError(503, "model_not_loaded",
                                  f"{self.name}: model {want!r} is not available (it has: {', '.join(ids) or 'nothing'}). "
                                  f"Not switching to another model.")
        cands = [m for m in chat if hint in m.lower()] if hint else chat
        if len(cands) == 1:
            return cands[0]
        what = f"model_hint {hint!r}" if hint else "no model / model_hint"
        if not cands:
            raise LocalFirstError(503, "model_not_loaded",
                                  f"{self.name}: {what} matches no available model ({', '.join(ids) or 'none'}).")
        raise LocalFirstError(503, "model_ambiguous",
                              f"{self.name}: {what} matches {len(cands)} models ({', '.join(cands)}). "
                              f"Set \"model\" or a more specific \"model_hint\" — local-first won't guess.")

    def open(self, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + "/chat/completions", data=data, headers=self._headers(), method="POST")
        try:
            return urllib.request.urlopen(req, timeout=float(self.spec.get("timeout", 120)))
        except urllib.error.HTTPError as e:
            return e                                     # pass the upstream's own error through
        except (urllib.error.URLError, OSError) as e:
            raise self._unavailable(getattr(e, "reason", e)) from e

    def probe(self, deep: bool = False) -> tuple:
        """(ok, message). deep=True sends a real one-token completion — the only proof the chain works."""
        try:
            model = self.resolve_model()
            if self.local:
                self.models(max_age=0)
            if deep:
                resp = self.open({"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Say OK."}],
                                  **(self.spec.get("extra") or {})})
                payload = json.loads(resp.read() or b"{}")
                if getattr(resp, "status", 200) >= 400:
                    return False, f"{model}: HTTP {resp.status} {str(payload)[:120]}"
                msg = (payload.get("choices") or [{}])[0].get("message") or {}
                if not (msg.get("content") or msg.get("reasoning_content")):
                    return False, f"{model}: answered with an EMPTY message (reasoning model? set extra.reasoning_effort)"
                return True, f"{model}: answered {msg.get('content', '')[:30]!r}"
            return True, f"{model} available"
        except LocalFirstError as e:
            return False, e.message
        except (ValueError, OSError) as e:
            return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Ledger and bill
# ═══════════════════════════════════════════════════════════════════════════════

class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or home_dir() / "ledger.jsonl")
        self._lock = threading.Lock()

    def add(self, row: dict):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def rows(self, month: str | None = None) -> list:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                raise LocalFirstError(500, "ledger_corrupt", f"{self.path} line {n} is corrupt") from e
            if month is None or r.get("at", "").startswith(month):
                out.append(r)
        return out


def price_of(cfg: dict, backend: str) -> dict:
    b = cfg["backends"].get(backend) or {}
    return b.get("price") or {"input_per_mtok": 0.0, "output_per_mtok": 0.0}


def compare_price(cfg: dict) -> dict:
    if cfg.get("compare_price"):
        return cfg["compare_price"]
    return next((b["price"] for b in cfg["backends"].values() if not b["local"] and b.get("price")),
                {"input_per_mtok": 0.0, "output_per_mtok": 0.0})


def cost(price: dict, tin: int, tout: int) -> float:
    return tin / 1e6 * float(price.get("input_per_mtok", 0)) + tout / 1e6 * float(price.get("output_per_mtok", 0))


def bill(cfg: dict, rows: list) -> dict:
    by: dict = {}
    for r in rows:
        if r.get("status") != "ok":
            continue
        b = by.setdefault(r["backend"], {"backend": r["backend"], "local": r["local"], "requests": 0, "in": 0,
                                         "out": 0, "paid": 0.0, "estimated": 0})
        b["requests"] += 1
        b["in"] += r.get("tokens_in", 0)
        b["out"] += r.get("tokens_out", 0)
        b["paid"] += r.get("cost", 0.0)
        b["estimated"] += 1 if r.get("estimated") else 0
    cp = compare_price(cfg)
    paid = sum(b["paid"] for b in by.values())
    all_cloud = sum(cost(cp, b["in"], b["out"]) if b["local"] else b["paid"] for b in by.values())
    n = sum(b["requests"] for b in by.values())
    n_local = sum(b["requests"] for b in by.values() if b["local"])
    refused = [r for r in rows if r.get("status") != "ok"]
    return {"backends": sorted(by.values(), key=lambda b: (not b["local"], b["backend"])), "paid": paid,
            "all_cloud": all_cloud, "saved": all_cloud - paid, "requests": n, "local_share": n_local / n if n else 0.0,
            "compare_price": cp, "refused": len(refused),
            "kept_local": sum(1 for r in refused if r.get("status") == "kept_local"),
            "local_down": sum(1 for r in refused if r.get("status") in ("local_unavailable", "model_not_loaded",
                                                                        "model_ambiguous"))}


def _num(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.0f}k" if n >= 1e3 else str(n))


def format_bill(b: dict, month: str, color: bool = False) -> str:
    c = _Ansi(color)
    lines = [c.bold(f"local-first bill (estimate) · {month}"), "",
             f"  {'backend':<22}{'requests':>9}{'tokens in':>11}{'tokens out':>12}{'cost':>11}"]
    for x in b["backends"]:
        where = "local" if x["local"] else "cloud"
        est = "*" if x["estimated"] else " "
        label = x["backend"] if x["backend"] == where else f"{x['backend']} ({where})"
        lines.append(f"  {label:<22}{x['requests']:>9}{_num(x['in']):>11}"
                     f"{_num(x['out']):>12}{'$' + format(x['paid'], ',.2f'):>11}{est}")
    lines.append("  " + "─" * 65)
    money = lambda v: f"{'$' + format(v, ',.2f'):>11}"  # noqa: E731 — pad before colouring
    lines.append(f"  {'you paid (est.)':<54}{c.bold(money(b['paid']))}")
    cp = b["compare_price"]
    lines.append(f"  {'all-cloud would have cost (est.)':<54}{money(b['all_cloud'])}")
    pct = f" ({b['saved'] / b['all_cloud']:.0%})" if b["all_cloud"] else ""
    lines.append(f"  {'saved (est.)':<54}{c.green(money(b['saved']))}{pct}")
    lines.append("")
    lines.append(f"  {b['local_share']:.0%} of {b['requests']} requests ran on this machine.")
    if b["kept_local"]:
        lines.append(f"  {b['kept_local']} request(s) refused because they were private or held a secret.")
    if b["local_down"]:
        lines.append(f"  {b['local_down']} request(s) failed loudly because the local model was down — none were "
                     f"quietly sent to the cloud.")
    lines.append(c.dim("  estimate: prices come from your config; local electricity and compute are not counted."))
    lines.append(c.dim(f"  all-cloud priced at ${cp.get('input_per_mtok', 0):g} in / ${cp.get('output_per_mtok', 0):g} out per "
                       f"million tokens (set yours in local-first.json)."))
    if any(x["estimated"] for x in b["backends"]):
        lines.append(c.dim("  * some token counts estimated (backend sent no usage)."))
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


def color_enabled(stream) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and not getenv("NO_COLOR")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. The proxy
# ═══════════════════════════════════════════════════════════════════════════════

def _usage_from(payload: dict) -> tuple:
    u = payload.get("usage") or {}
    return u.get("prompt_tokens"), u.get("completion_tokens")


class Proxy:
    def __init__(self, cfg: dict, ledger: Ledger, log=None):
        self.cfg, self.ledger, self.log = cfg, ledger, log or (lambda s: None)
        self.tasks = cfg.get("tasks") or DEFAULT_TASKS

    def record(self, req: Request, rt: Route | None, status: str, model: str = "", tin=None, tout=None,
               started: float = 0.0, out_chars: int = 0):
        backend = rt.backend if rt else ""
        b = self.cfg["backends"].get(backend) or {"local": False}
        est = tin is None or tout is None
        tin = req.tokens if tin is None else int(tin)
        tout = (out_chars + 3) // 4 if tout is None else int(tout)
        row = {"at": now().isoformat(timespec="seconds"), "status": status, "backend": backend,
               "local": bool(b.get("local")), "model": model, "rule": rt.rule if rt else "", "task": req.task,
               "tags": req.tags, "tokens_in": tin if status == "ok" else 0, "tokens_out": tout if status == "ok" else 0,
               "estimated": est and status == "ok",
               "cost": cost(price_of(self.cfg, backend), tin, tout) if status == "ok" else 0.0,
               "ms": int((time.monotonic() - started) * 1000) if started else 0}
        self.ledger.add(row)
        self.log(f"{row['status']:<17} {backend or '-':<10} {row['rule'][:28]:<28} {model}")
        return row

    def handle(self, body: dict, headers: dict, write_head, write):
        """Route one chat request. write_head(status, headers) and write(bytes) talk to the client."""
        started = time.monotonic()
        req = parse_request(body, headers, self.tasks)
        rt = None
        try:
            rt = route(self.cfg, req)
            up = Upstream(rt.backend, self.cfg["backends"][rt.backend])
            model = up.resolve_model()
        except LocalFirstError as e:
            self.record(req, rt, e.kind, started=started)
            write_head(e.status, {"Content-Type": "application/json", "X-Local-First-Error": e.kind})
            write(json.dumps(e.body()).encode())
            return
        out = {k: v for k, v in body.items() if k != "tags"}
        out["model"] = model
        out.update(self.cfg["backends"][rt.backend].get("extra") or {})
        info = {"X-Local-First-Backend": rt.backend, "X-Local-First-Model": model,
                "X-Local-First-Rule": rt.rule.encode("ascii", "replace").decode(),
                "X-Local-First-Where": "local" if up.local else "cloud"}
        try:
            resp = up.open(out)
        except LocalFirstError as e:
            self.record(req, rt, e.kind, model, started=started)
            write_head(e.status, {"Content-Type": "application/json", "X-Local-First-Error": e.kind, **info})
            write(json.dumps(e.body()).encode())
            return
        status = getattr(resp, "status", None) or getattr(resp, "code", 200)
        ctype = resp.headers.get("Content-Type", "application/json")
        write_head(status, {"Content-Type": ctype, **info})
        if status >= 400:
            write(resp.read())
            self.record(req, rt, f"upstream_{status}", model, started=started)
            return
        if body.get("stream"):
            tin = tout = None
            chars = 0
            for line in resp:
                write(line)
                if line.startswith(b"data:") and b"[DONE]" not in line:
                    try:
                        chunk = json.loads(line[5:])
                    except ValueError:
                        continue
                    a, b = _usage_from(chunk)
                    tin, tout = (a, b) if a is not None else (tin, tout)
                    for ch in chunk.get("choices") or []:
                        chars += len(((ch.get("delta") or {}).get("content")) or "")
            self.record(req, rt, "ok", model, tin, tout, started, chars)
        else:
            raw = resp.read()
            write(raw)
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                payload = {}
            tin, tout = _usage_from(payload)
            text = "".join(((c.get("message") or {}).get("content") or "") for c in payload.get("choices") or [])
            self.record(req, rt, "ok", model, tin, tout, started, len(text))


def make_handler(proxy: Proxy):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"local-first/{VERSION}"

        def log_message(self, fmt, *args):      # quiet; the proxy logs its own one-liners
            pass

        def _send(self, status: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path in ("/v1/models", "/models"):
                ids = ["auto", "local"] + list(proxy.cfg["backends"])
                return self._send(200, {"object": "list", "data": [{"id": i, "object": "model",
                                                                     "owned_by": "local-first"} for i in ids]})
            if path == "/health":
                return self._send(200, {"ok": True, "version": VERSION})
            if path == "/bill":
                month = now().strftime("%Y-%m")
                return self._send(200, bill(proxy.cfg, proxy.ledger.rows(month)))
            return self._send(404, {"error": {"type": "not_found", "message": f"no route {path}"}})

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            if path not in ("/v1/chat/completions", "/chat/completions"):
                return self._send(404, {"error": {"type": "not_found",
                                                  "message": f"local-first only proxies /v1/chat/completions, not {path}"}})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": {"type": "bad_request", "message": "body is not JSON"}})
            headers = {k.lower(): v for k, v in self.headers.items()}

            def head(status, hdrs):
                self.send_response(status)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()

            def write(b):
                self.wfile.write(b)
                self.wfile.flush()
            try:
                proxy.handle(body, headers, head, write)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return Handler


def serve(cfg: dict, ledger: Ledger, host: str, port: int, log=print, ready=None) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(Proxy(cfg, ledger, log)))
    srv.daemon_threads = True
    if ready:
        ready(srv)
    return srv


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CLI
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_init(a) -> int:
    p = Path(a.dir) / CONFIG_FILE
    if p.exists() and not a.force:
        print(f"{p} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    starter = {k: v for k, v in DEFAULT_CONFIG.items() if v is not None}
    p.write_text(json.dumps(starter, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"wrote {p} — set backends.cloud.model, and model or model_hint for your local backends")
    return 0


def cmd_check(a) -> int:
    cfg = load_config()
    c = _Ansi(color_enabled(sys.stdout))
    bad = 0
    for name, spec in cfg["backends"].items():
        used = any(r["to"] == name for r in cfg["routes"])
        ok, msg = Upstream(name, spec).probe(deep=a.deep)
        mark = c.green("✓") if ok else (c.red("✗") if used else c.yellow("·"))
        where = "local" if spec["local"] else "cloud"
        note = "" if used else c.dim("  (no route uses it)")
        print(f"{mark} {name:<10} {where:<5} {spec['url']:<32} {msg}{note}")
        bad += (not ok) and used
    return 1 if bad else 0


def cmd_route(a) -> int:
    cfg = load_config()
    body = {"model": a.model, "messages": [{"role": "user", "content": a.prompt}]}
    req = parse_request(body, {"x-local-first-tags": ",".join(a.tag or [])}, cfg.get("tasks") or DEFAULT_TASKS)
    print(f"task: {req.task or 'none'} · ~{req.tokens} tokens · tags: {req.tags or '[]'}")
    try:
        rt = route(cfg, req)
    except LocalFirstError as e:
        print(f"REFUSED ({e.kind}): {e.message}")
        return 3
    for w in rt.why:
        print("  " + w)
    where = "local" if cfg["backends"][rt.backend]["local"] else "cloud"
    print(f"→ {rt.backend} ({where})")
    return 0


def cmd_bill(a) -> int:
    cfg = load_config()
    month = a.month or now().strftime("%Y-%m")
    b = bill(cfg, Ledger(Path(a.ledger) if a.ledger else None).rows(month))
    print(json.dumps(b, indent=1) if a.json else format_bill(b, month, color_enabled(sys.stdout)))
    return 0


def cmd_serve(a) -> int:
    cfg = load_config()
    host, _, port = (a.listen or cfg["listen"]).rpartition(":")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: listening on {host} — anyone on that network can use your models and your cloud key",
              file=sys.stderr)
    ledger = Ledger(Path(a.ledger) if a.ledger else None)
    srv = serve(cfg, ledger, host, int(port))
    print(f"local-first {VERSION} listening on http://{host}:{srv.server_address[1]}/v1  ·  ledger {ledger.path}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Demo — fake backends on localhost; nothing leaves the machine, no key needed
# ═══════════════════════════════════════════════════════════════════════════════

class FakeBackend:
    """A tiny OpenAI-compatible server that answers deterministically. For demos and tests."""

    def __init__(self, models: list, reply: str = "OK", usage: bool = True):
        self.models, self.reply, self.usage, self.calls = models, reply, usage, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = json.dumps({"data": [{"id": m} for m in fake.models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                fake.calls.append({"body": body, "auth": self.headers.get("Authorization", "")})
                text = fake.reply
                tin = estimate_tokens(" ".join(str(m.get("content")) for m in body.get("messages", [])))
                usage = {"prompt_tokens": tin, "completion_tokens": estimate_tokens(text) + 1}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for i, piece in enumerate(re.findall(r"\S+\s*", text)):
                        chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    if fake.usage:
                        self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                payload = {"id": "fake", "object": "chat.completion", "model": body.get("model"),
                           "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                        "finish_reason": "stop"}]}
                if fake.usage:
                    payload["usage"] = usage
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def post(url: str, body: dict, headers: dict | None = None, timeout: float = 30) -> tuple:
    """Tiny client for the demo and tests: (status, headers, json)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


DEMO_PROMPTS = [
    ("Summarize this meeting transcript in five bullet points: …", []),
    ("Translate this product description into French: …", []),
    ("Extract the invoice number, date and total from this email: …", []),
    ("Classify these 40 support tickets as billing, bug or feature request: …", []),
    ("My blood test results are below. Explain them in plain words: …", ["private"]),
    ("Think step by step: what are the architecture trade-offs of moving our queue to Kafka?", []),
    ("Here is our config, find the bug: OPENAI_API_KEY=sk-proj-4f9a8c7b6d5e4f3a2b1c0d9e8f7a", ["cloud"]),
]


def demo_config(local_url: str, cloud_url: str) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["backends"] = {
        "lmstudio": {"url": local_url, "local": True, "model_hint": "qwen3.6", "extra": {"reasoning_effort": "none"}},
        "cloud": {"url": cloud_url, "local": False, "model": "big-cloud-model", "key_env": "LOCALFIRST_DEMO_KEY",
                  "price": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}},
    }
    validate(cfg)
    return cfg


def simulate_month(cfg: dict, ledger: Ledger, n: int = 1500, seed: int = 7, month: str = "2026-11"):
    """Replay n requests through the REAL router with synthetic token counts (fixed seed → same bill)."""
    rnd = random.Random(seed)
    mix = [pt for pt, w in zip(DEMO_PROMPTS[:6], (30, 18, 18, 14, 8, 12)) for _ in range(w)]
    proxy = Proxy(cfg, ledger)
    for i in range(n):
        text, tags = rnd.choice(mix)
        req = parse_request({"messages": [{"role": "user", "content": text}]},
                            {"x-local-first-tags": ",".join(tags)}, proxy.tasks)
        rt = route(cfg, req)
        req.tokens = rnd.randint(2000, 24000)          # agent calls carry a lot of context
        tout = rnd.randint(150, 1500)
        day = 1 + i * 20 // n
        b = cfg["backends"][rt.backend]
        ledger.add({"at": f"{month}-{day:02d}T{9 + i % 9:02d}:00:00", "status": "ok", "backend": rt.backend,
                    "local": b["local"], "model": "", "rule": rt.rule, "task": req.task, "tags": req.tags,
                    "tokens_in": req.tokens, "tokens_out": tout, "estimated": False,
                    "cost": cost(price_of(cfg, rt.backend), req.tokens, tout), "ms": 0})


def cmd_demo(a) -> int:
    import tempfile
    c = _Ansi(a.color == "always" or (a.color == "auto" and color_enabled(sys.stdout)))
    pause = 0.0 if a.fast else 0.5
    say = lambda s="", p=0.0: (print(s, flush=True), time.sleep(p))  # noqa: E731
    local = FakeBackend(["qwen/qwen3.6-35b-a3b", "text-embedding-nomic"], "Done — handled on this machine.")
    cloud = FakeBackend(["big-cloud-model"], "Done — handled in the cloud.")
    tmp = Path(tempfile.mkdtemp(prefix="local-first-demo-"))
    cfg = demo_config(local.url, cloud.url)
    ledger = Ledger(tmp / "ledger.jsonl")
    srv = serve(cfg, ledger, "127.0.0.1", 0, log=lambda s: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
    try:
        say(c.bold("local-first demo") + c.dim("  — fake LM Studio + fake cloud on localhost. Nothing leaves this machine."), pause)
        say(c.dim(f"proxy: {url.rsplit('/chat', 1)[0]}   (point any OpenAI client here, model \"auto\")"), pause)
        say()
        for prompt, tags in DEMO_PROMPTS:
            hdr = {"X-Local-First-Tags": ",".join(tags)} if tags else {}
            status, h, _ = post(url, {"model": "auto", "messages": [{"role": "user", "content": prompt}]}, hdr)
            short = prompt if len(prompt) <= 50 else prompt[:49] + "…"
            tagtxt = c.dim(f" [{','.join(tags)}]") if tags else ""
            if status == 200:
                where = h.get("X-Local-First-Where")
                label = c.green("LOCAL ") if where == "local" else c.yellow("CLOUD ")
                say(f"  {label} {short}{tagtxt}  {c.dim('← ' + h.get('X-Local-First-Rule', ''))}", pause / 2)
            else:
                say(f"  {c.red('KEPT  ')} {short}{tagtxt}  {c.dim('← looks like a secret; never sent to the cloud')}",
                    pause / 2)
        say()
        say(c.bold("LM Studio crashes. Next request:"), pause)
        local.stop()
        before = len(cloud.calls)
        status, h, body = post(url, {"model": "auto", "messages": [{"role": "user", "content": "Summarize this: …"}]})
        msg = textwrap.wrap(body["error"]["message"], 84)
        say(f"  {c.red(f'HTTP {status}')}  " + "\n            ".join(msg), pause)
        say(f"  cloud requests during the outage: {len(cloud.calls) - before} "
            + c.dim("(no silent fallback, no model swap)"), pause)
        say()
        sim = Ledger(tmp / "month.jsonl")
        simulate_month(cfg, sim)
        say(c.dim("A month like this — 1,500 requests replayed through the same rules (synthetic token counts):"))
        say(format_bill(bill(cfg, sim.rows("2026-11")), "November 2026 (simulated)", c.on))
        say()
        say(c.dim("Install: curl -fsSL https://raw.githubusercontent.com/JohnSUNLei/hands-off/main/local-first/localfirst.py -o localfirst.py"))
    finally:
        srv.shutdown()
        srv.server_close()
        cloud.stop()
    return 0 if status == 503 and len(cloud.calls) == before else 1


# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="local-first", description="Run what you can on your own machine. Know where every request went and what it cost (estimated).")
    ap.add_argument("-V", "--version", action="version", version=f"local-first {VERSION}")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="run the OpenAI-compatible proxy")
    p.add_argument("--listen", help="host:port (default from config, 127.0.0.1:8484)")
    p.add_argument("--ledger", help="ledger file (default ~/.local-first/ledger.jsonl)")
    p = sub.add_parser("init", help="write a starter local-first.json")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("check", help="probe every backend live (are they really up?)")
    p.add_argument("--deep", action="store_true", help="also send a real one-token completion")
    p = sub.add_parser("route", help="dry run: where would this prompt go, and why?")
    p.add_argument("prompt")
    p.add_argument("--tag", action="append")
    p.add_argument("--model", default="auto")
    p = sub.add_parser("bill", help="this month's estimated bill")
    p.add_argument("--month", help="YYYY-MM")
    p.add_argument("--ledger")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("demo", help="offline demo with fake backends")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    handlers = {"serve": cmd_serve, "init": cmd_init, "check": cmd_check, "route": cmd_route, "bill": cmd_bill,
                "demo": cmd_demo}
    if a.cmd not in handlers:
        build_parser().print_help()
        return 64
    try:
        return handlers[a.cmd](a)
    except LocalFirstError as e:
        print(f"local-first: {e.message}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    sys.exit(main())
