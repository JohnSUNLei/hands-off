"""Routing decisions — pure functions, no network."""
import copy
import unittest

from _helpers import lf

CFG = lf.demo_config("http://127.0.0.1:1/v1", "http://127.0.0.1:2/v1")


def req(text, tags=(), model="auto", headers=None, **body):
    h = {"x-local-first-tags": ",".join(tags), **(headers or {})}
    return lf.parse_request({"model": model, "messages": [{"role": "user", "content": text}], **body}, h, lf.DEFAULT_TASKS)


class Parsing(unittest.TestCase):
    def test_tasks(self):
        cases = {"Summarize this thread": "summarize", "TL;DR please": "summarize", "请帮我总结一下": "summarize",
                 "Translate to German": "translate", "翻译成英文": "translate", "Extract the dates": "extract",
                 "Classify these tickets": "classify", "Refactor this function": "code",
                 "Think step by step about the trade-offs": "reasoning", "架构上怎么权衡": "reasoning",
                 "Hello there": ""}
        for text, task in cases.items():
            self.assertEqual(req(text).task, task, text)

    def test_tags_from_header_body_and_model(self):
        self.assertEqual(req("x", tags=["Private"]).tags, ["private"])
        self.assertEqual(lf.parse_request({"model": "auto:private,draft", "messages": []}, {}, {}).tags,
                         ["draft", "private"])
        r = lf.parse_request({"model": "auto:cloud", "tags": ["x"], "messages": []}, {}, {})
        self.assertEqual((r.model, r.tags), ("auto", ["cloud", "x"]))

    def test_images_tools_tokens(self):
        r = lf.parse_request({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "data:"}}]}],
            "tools": [{"type": "function"}]}, {}, {})
        self.assertTrue(r.has_images and r.has_tools)
        self.assertEqual(lf.estimate_tokens("abcd" * 100), 100)
        self.assertEqual(lf.estimate_tokens("你好世界"), 4)

    def test_task_header_overrides(self):
        self.assertEqual(req("hello", headers={"x-local-first-task": "reasoning"}).task, "reasoning")


class Routing(unittest.TestCase):
    def test_default_rules(self):
        self.assertEqual(lf.route(CFG, req("Summarize this")).backend, "lmstudio")
        self.assertEqual(lf.route(CFG, req("Summarize this")).rule, "routine work")
        self.assertEqual(lf.route(CFG, req("hello")).rule, "everything else")
        self.assertEqual(lf.route(CFG, req("Prove that this is correct")).backend, "cloud")
        self.assertEqual(lf.route(CFG, req("x", tags=["cloud"])).backend, "cloud")
        self.assertEqual(lf.route(CFG, req("word " * 40000)).rule, "very long prompt")

    def test_explicit_model_choice(self):
        self.assertEqual(lf.route(CFG, req("x", model="cloud")).backend, "cloud")
        self.assertEqual(lf.route(CFG, req("Prove it", model="local")).backend, "lmstudio")
        self.assertEqual(lf.route(CFG, req("x", model="gpt-whatever")).rule, "everything else")

    def test_why_explains_every_rule_tried(self):
        rt = lf.route(CFG, req("Summarize this"))
        self.assertTrue(rt.why[-1].startswith("→ routine work"))
        self.assertTrue(all(w.startswith("·") for w in rt.why[:-1]))

    def test_private_never_goes_to_cloud(self):
        self.assertEqual(lf.route(CFG, req("my diagnosis", tags=["private"])).backend, "lmstudio")
        self.assertEqual(lf.route(CFG, req("my diagnosis", tags=["private", "cloud"])).backend, "lmstudio")
        with self.assertRaises(lf.LocalFirstError) as cm:                 # even when asked by name
            lf.route(CFG, req("my diagnosis", tags=["private"], model="cloud"))
        self.assertEqual((cm.exception.status, cm.exception.kind), (403, "kept_local"))
        cfg = copy.deepcopy(CFG)                                          # even if the rules are badly ordered
        cfg["routes"] = [{"name": "all to cloud", "to": "cloud"}]
        with self.assertRaises(lf.LocalFirstError):
            lf.route(cfg, req("my diagnosis", tags=["private"]))

    def test_secrets_stay_local(self):
        for secret in ("sk-proj-abcdefghijklmnopqrstuvwxyz123456", "AKIAABCDEFGHIJKLMNOP",
                       "ghp_" + "a" * 36, "-----BEGIN RSA PRIVATE KEY-----", "password = hunter2hunter2"):
            with self.assertRaises(lf.LocalFirstError, msg=secret):
                lf.route(CFG, req(f"debug this: {secret}", tags=["cloud"]))
            self.assertEqual(lf.route(CFG, req(f"debug this: {secret}")).backend, "lmstudio")
        cfg = dict(CFG, secrets_stay_local=False)
        self.assertEqual(lf.route(cfg, req("sk-proj-abcdefghijklmnopqrstuvwxyz", tags=["cloud"])).backend, "cloud")
        self.assertEqual(lf.looks_secret("the word sky-high is fine"), "")

    def test_conditions(self):
        cfg = copy.deepcopy(CFG)
        cfg["routes"] = [{"name": "vision", "when": {"has_images": True}, "to": "cloud"},
                         {"name": "tools", "when": {"has_tools": True}, "to": "cloud"},
                         {"name": "short", "when": {"max_tokens": 10, "match": "^hi"}, "to": "lmstudio"},
                         {"name": "model", "when": {"model": "^gpt-"}, "to": "cloud"},
                         {"name": "rest", "to": "lmstudio"}]
        lf.validate(cfg)
        img = lf.parse_request({"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}, {}, {})
        self.assertEqual(lf.route(cfg, img).rule, "vision")
        self.assertEqual(lf.route(cfg, req("x", tools=[{}])).rule, "tools")
        self.assertEqual(lf.route(cfg, req("hi there")).rule, "short")
        self.assertEqual(lf.route(cfg, req("x", model="gpt-4o")).rule, "model")
        self.assertEqual(lf.route(cfg, req("hello " * 50)).rule, "rest")


class ConfigValidation(unittest.TestCase):
    def bad(self, mutate):
        cfg = copy.deepcopy(CFG)
        mutate(cfg)
        with self.assertRaises(lf.LocalFirstError):
            lf.validate(cfg)

    def test_rejects(self):
        self.bad(lambda c: c["routes"].append({"name": "x", "when": {"tags": ["a"]}, "to": "lmstudio"}))
        self.bad(lambda c: c["routes"].insert(0, {"name": "x", "to": "nowhere"}))
        self.bad(lambda c: c["routes"][0].update(when={"colour": "red"}))
        self.bad(lambda c: c["backends"]["cloud"].update(api_key="sk-oops"))
        self.bad(lambda c: c["backends"]["cloud"].pop("local"))
        self.bad(lambda c: c["backends"]["cloud"].update(url="ftp://x"))
        self.bad(lambda c: c.update(tasks={"x": ["(unclosed"]}))
        self.bad(lambda c: c.update(backends={}))


class Bill(unittest.TestCase):
    def test_math(self):
        rows = [{"status": "ok", "backend": "lmstudio", "local": True, "tokens_in": 1_000_000, "tokens_out": 100_000,
                 "cost": 0.0},
                {"status": "ok", "backend": "cloud", "local": False, "tokens_in": 500_000, "tokens_out": 50_000,
                 "cost": lf.cost({"input_per_mtok": 3, "output_per_mtok": 15}, 500_000, 50_000)},
                {"status": "kept_local", "backend": "cloud", "local": False},
                {"status": "local_unavailable", "backend": "lmstudio", "local": True}]
        b = lf.bill(CFG, rows)
        self.assertAlmostEqual(b["paid"], 2.25)
        self.assertAlmostEqual(b["all_cloud"], 3 + 1.5 + 2.25)
        self.assertAlmostEqual(b["saved"], 4.5)
        self.assertEqual((b["requests"], b["kept_local"], b["local_down"]), (2, 1, 1))
        text = lf.format_bill(b, "2026-11")
        for s in ("$2.25", "$6.75", "$4.50 (67%)", "50% of 2 requests", "none were quietly sent"):
            self.assertIn(s, text)

    def test_empty(self):
        b = lf.bill(CFG, [])
        self.assertEqual((b["paid"], b["saved"], b["local_share"]), (0, 0, 0))
        lf.format_bill(b, "2026-11")

    def test_simulated_month_is_deterministic(self):
        import tempfile
        from pathlib import Path
        out = []
        for _ in range(2):
            led = lf.Ledger(Path(tempfile.mkdtemp()) / "m.jsonl")
            lf.simulate_month(CFG, led)
            out.append(lf.format_bill(lf.bill(CFG, led.rows("2026-11")), "x"))
        self.assertEqual(out[0], out[1])
