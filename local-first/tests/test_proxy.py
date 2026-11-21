"""The proxy end to end, against fake backends on localhost."""
import json
import os
import urllib.request

from _helpers import ProxyCase, chat, lf


class Forwarding(ProxyCase):
    def test_local_request(self):
        status, h, body = self.post(chat("Summarize this"))
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "local answer")
        self.assertEqual((h["X-Local-First-Backend"], h["X-Local-First-Where"], h["X-Local-First-Rule"]),
                         ("lmstudio", "local", "routine work"))
        sent = self.local.calls[-1]["body"]
        self.assertEqual(sent["model"], "qwen/qwen3.6-35b-a3b")          # resolved from model_hint, live
        self.assertEqual(sent["reasoning_effort"], "none")               # backend extra merged
        self.assertEqual(self.cloud.calls, [])
        row = self.ledger.rows()[-1]
        self.assertEqual((row["status"], row["backend"], row["local"], row["estimated"]), ("ok", "lmstudio", True, False))
        self.assertGreater(row["tokens_in"], 0)
        self.assertEqual(row["cost"], 0.0)

    def test_cloud_request_uses_key_from_env_and_never_logs_it(self):
        os.environ["LOCALFIRST_DEMO_KEY"] = "sk-test-not-real-0000"
        status, h, body = self.post(chat("Prove this lemma"))
        self.assertEqual((status, h["X-Local-First-Where"]), (200, "cloud"))
        self.assertEqual(self.cloud.calls[-1]["auth"], "Bearer sk-test-not-real-0000")
        self.assertEqual(self.cloud.calls[-1]["body"]["model"], "big-cloud-model")
        self.assertNotIn("sk-test", self.ledger.path.read_text())
        self.assertGreater(self.ledger.rows()[-1]["cost"], 0)

    def test_tags_field_not_forwarded(self):
        self.post(dict(chat("hello"), tags=["draft"]))
        self.assertNotIn("tags", self.local.calls[-1]["body"])

    def test_streaming_passthrough(self):
        self.local.reply = "one two three four"
        req = urllib.request.Request(self.url, data=json.dumps(chat("Summarize", stream=True)).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.headers["X-Local-First-Where"], "local")
            lines = [l for l in r.read().decode().splitlines() if l.startswith("data:")]
        text = "".join(json.loads(l[5:])["choices"][0]["delta"]["content"] for l in lines
                       if "[DONE]" not in l and json.loads(l[5:])["choices"])
        self.assertEqual(text, "one two three four")
        self.assertEqual(lines[-1], "data: [DONE]")
        row = self.ledger.rows()[-1]
        self.assertEqual((row["tokens_out"], row["estimated"]), (lf.estimate_tokens("one two three four") + 1, False))

    def test_usage_missing_is_estimated_and_flagged(self):
        self.local.usage = False
        self.post(chat("Summarize this please"))
        self.assertTrue(self.ledger.rows()[-1]["estimated"])

    def test_models_health_bill_endpoints(self):
        with urllib.request.urlopen(self.base + "/models") as r:
            ids = [m["id"] for m in json.loads(r.read())["data"]]
        self.assertEqual(ids, ["auto", "local", "lmstudio", "cloud"])
        with urllib.request.urlopen(self.base.rsplit("/v1", 1)[0] + "/health") as r:
            self.assertTrue(json.loads(r.read())["ok"])
        self.post(chat("Summarize"))
        os.environ["LOCALFIRST_NOW"] = self.ledger.rows()[-1]["at"]
        with urllib.request.urlopen(self.base.rsplit("/v1", 1)[0] + "/bill") as r:
            self.assertEqual(json.loads(r.read())["requests"], 1)

    def test_unknown_path_and_bad_json(self):
        status, _, body = lf.post(self.base + "/embeddings", {"input": "x"})
        self.assertEqual(status, 404)
        req = urllib.request.Request(self.url, data=b"{nope", method="POST")
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


class NoSilentFallback(ProxyCase):
    """The red line: when local is unavailable, fail loudly. Never the cloud, never another model."""

    def test_local_down_is_503_and_cloud_untouched(self):
        self.local.stop()
        status, h, body = self.post(chat("Summarize this"))
        self.assertEqual((status, body["error"]["type"]), (503, "local_unavailable"))
        self.assertIn("does NOT fall back to the cloud", body["error"]["message"])
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(self.ledger.rows()[-1]["status"], "local_unavailable")

    def test_model_missing_is_503_not_a_swap(self):
        cfg = self.make_cfg()
        cfg["backends"]["lmstudio"]["model"] = "qwen3-vl-32b-thinking"       # unloaded since
        self.restart(cfg)
        status, _, body = self.post(chat("Summarize"))
        self.assertEqual((status, body["error"]["type"]), (503, "model_not_loaded"))
        self.assertIn("qwen/qwen3.6-35b-a3b", body["error"]["message"])     # tells you what IS there
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.cloud.calls, [])

    def test_ambiguous_hint_refuses_to_guess(self):
        self.local.models.append("qwen/qwen3.6-27b")
        lf.Upstream._models_cache.clear()
        cfg = self.make_cfg()
        cfg["backends"]["lmstudio"]["model_hint"] = "qwen3.6"
        self.restart(cfg)
        status, _, body = self.post(chat("Summarize"))
        self.assertEqual((status, body["error"]["type"]), (503, "model_ambiguous"))
        self.assertEqual(self.local.calls, [])

    def test_no_hint_single_model_is_used(self):
        self.local.models[:] = ["only-chat-model", "nomic-embed-text"]
        lf.Upstream._models_cache.clear()
        cfg = self.make_cfg()
        cfg["backends"]["lmstudio"]["model_hint"] = ""
        self.restart(cfg)
        self.assertEqual(self.post(chat("Summarize"))[0], 200)
        self.assertEqual(self.local.calls[-1]["body"]["model"], "only-chat-model")

    def test_cloud_down_is_502_and_local_untouched(self):
        self.cloud.stop()
        status, _, body = self.post(chat("x", model="cloud"))
        self.assertEqual((status, body["error"]["type"]), (502, "cloud_unavailable"))
        self.assertEqual(self.local.calls, [])

    def test_cloud_without_model_is_config_error(self):
        cfg = self.make_cfg()
        cfg["backends"]["cloud"]["model"] = ""
        self.restart(cfg)
        status, _, body = self.post(chat("x", model="cloud"))
        self.assertEqual((status, body["error"]["type"]), (500, "config_error"))
        self.assertEqual(self.local.calls, [])

    def test_kept_local_is_403_and_logged(self):
        status, h, body = self.post(chat("token sk-proj-abcdefghijklmnopqrstuvwxyz0123", model="cloud"))
        self.assertEqual((status, body["error"]["type"]), (403, "kept_local"))
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(self.ledger.rows()[-1]["status"], "kept_local")
