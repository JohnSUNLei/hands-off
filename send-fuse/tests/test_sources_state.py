"""Thread sources (Maildir / mbox / command) and the on-disk state (lock, ledger, synced folders)."""
import email.message
import json
import mailbox
import subprocess
import sys
import textwrap
import time

from _helpers import JORDAN, ME, REPLY_WITH_TIMES, WED, FuseCase, sf

T = sf.parse_time


def mail(mid, frm, to, date, subject, body, refs="", ics=False):
    m = email.message.EmailMessage()
    m["Message-ID"], m["From"], m["To"], m["Date"], m["Subject"] = mid, frm, to, date, subject
    if refs:
        m["References"] = refs
        m["In-Reply-To"] = refs.split()[-1]
    m.set_content(body)
    if ics:
        m.add_attachment(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n", maintype="text", subtype="calendar",
                         filename="invite.ics")
    return m


INCIDENT_MAILS = [
    ("<m1@acme.example>", f"Jordan <{JORDAN}>", ME, "Mon, 17 Nov 2026 09:12:00 -0500",
     "Project kickoff — Acme", "Could you share a few times for a call?", ""),
    ("<m2@example.com>", f"You <{ME}>", JORDAN, "Mon, 17 Nov 2026 10:30:00 -0500",
     "Re: Project kickoff — Acme", "Thursday at 2pm would be great.", "<m1@acme.example>"),
    ("<m3@acme.example>", f"Jordan <{JORDAN}>", ME, "Mon, 17 Nov 2026 11:05:00 -0500",
     "Re: Project kickoff — Acme", "Confirmed - see you Thursday at 2:00 PM.",
     "<m1@acme.example> <m2@example.com>"),
    ("<x1@other.example>", "Sam <sam@other.example>", ME, "Mon, 17 Nov 2026 12:00:00 -0500",
     "Unrelated", "Lunch?", ""),
]


class MailboxSources(FuseCase):
    def fill(self, box):
        for i, row in enumerate(INCIDENT_MAILS):
            box.add(mail(*row, ics=(i == 2)))
        box.flush()

    def check_incident(self, source):
        f = sf.Fuse(self.policy(thread_source=source), sf.State(self.state_dir))
        threads = f._source.threads()
        self.assertEqual(sorted(len(t.messages) for t in threads), [1, 3])
        v = f.evaluate(sf.Outgoing(subject="Re: Project kickoff — Acme", body=REPLY_WITH_TIMES,
                                   in_reply_to="<m1@acme.example>"), at=T(WED))
        self.assertEqual([r.rule for r in v.reasons], ["already_settled", "not_latest"])
        self.assertEqual(v.outgoing.to, [JORDAN])

    def test_maildir(self):
        path = self.tmp / "Maildir"
        self.fill(mailbox.Maildir(str(path), create=True))
        self.check_incident({"type": "maildir", "path": str(path)})

    def test_mbox(self):
        path = self.tmp / "inbox.mbox"
        self.fill(mailbox.mbox(str(path), create=True))
        self.check_incident({"type": "mbox", "path": "inbox.mbox"})       # relative to the project

    def test_calendar_part_seen_as_attachment(self):
        path = self.tmp / "inbox.mbox"
        self.fill(mailbox.mbox(str(path), create=True))
        src = sf.open_source(self.policy(thread_source={"type": "mbox", "path": str(path)}))
        m3 = next(m for t in src.threads() for m in t.messages if m.id == "<m3@acme.example>")
        self.assertIn("invite.ics", m3.attachments)


class CommandSourceTests(FuseCase):
    def script(self, body: str):
        p = self.tmp / "fetch.py"
        p.write_text(textwrap.dedent(body), "utf-8")
        return {"type": "command", "cmd": [sys.executable, str(p)]}

    def test_command_gets_outgoing_and_returns_thread(self):
        src = self.script(f"""
            import json, sys
            out = json.load(sys.stdin)
            assert out["in_reply_to"] == "<m1@acme.example>"
            print(json.dumps({json.dumps(sf.DEMO_THREAD)}))
        """)
        v = self.fuse(thread_source=src).evaluate(
            sf.Outgoing(subject="Re: x", body=REPLY_WITH_TIMES, in_reply_to="<m1@acme.example>"), at=T(WED))
        self.assertEqual([r.rule for r in v.reasons], ["already_settled", "not_latest"])

    def test_command_null_means_no_thread(self):
        src = self.script("print('null')")
        v = self.fuse(thread_source=src).evaluate(sf.Outgoing(to=["a@x.example"], subject="Hi", body="Hello"),
                                                  at=T(WED))
        self.assertEqual(v.decision, "allow")

    def test_command_failure_blocks(self):
        for body in ("import sys; sys.exit(3)", "print('{oops')", "import time; time.sleep(5)"):
            src = self.script(body)
            src["timeout"] = 1
            v = self.fuse(thread_source=src).evaluate(sf.Outgoing(to=["a@x.example"], subject="Hi", body="x"),
                                                      at=T(WED))
            self.assertEqual([r.rule for r in v.reasons], ["thread_unreadable"], body)

    def test_bad_specs(self):
        with self.assertRaises(sf.FuseError):
            sf.open_source(self.policy(thread_source={"type": "command", "cmd": "python fetch.py"}))
        with self.assertRaises(sf.FuseError):
            sf.open_source(self.policy(thread_source={"type": "imap"}))


class StateTests(FuseCase):
    def test_synced_folders_refused(self):
        for p in ("~/Dropbox/x", "~/Library/Mobile Documents/com~apple~CloudDocs/x",
                  "~/Library/CloudStorage/GoogleDrive-a/x", "~/OneDrive/x", "~/同步空间/x"):
            with self.assertRaises(sf.FuseError, msg=p):
                sf.State(sf.Path(p).expanduser())
        sf.State(self.tmp / "fine")

    def test_lock_is_exclusive_across_processes(self):
        st = sf.State(self.state_dir)
        holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
            import sys, time; sys.path.insert(0, {str(sf.Path(sf.__file__).parent)!r})
            import sendfuse as sf
            with sf.State(sf.Path({str(self.state_dir)!r})).lock():
                print("locked", flush=True); time.sleep(3)
        """)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            t0 = time.monotonic()
            with self.assertRaises(sf.LockBusy):
                with st.lock(wait=0.3):
                    pass
            self.assertLess(time.monotonic() - t0, 2)
        finally:
            holder.wait(10)
        with st.lock(wait=0.3):         # free again once the holder is gone
            pass

    def test_two_parallel_agents_only_one_gets_through(self):
        out = {"to": ["sam@acme.example"], "subject": "Quarterly report", "body": "Report attached."}
        code = textwrap.dedent(f"""
            import sys, json; sys.path.insert(0, {str(sf.Path(sf.__file__).parent)!r})
            import sendfuse as sf
            pol = dict(sf.DEFAULT_POLICY, quiet_hours=None, _base=".")
            f = sf.Fuse(pol, sf.State(sf.Path({str(self.state_dir)!r})))
            v = f.check(sf.Outgoing(**json.loads(sys.argv[1]), via=sys.argv[2]),
                        at=sf.parse_time("{WED}"), record=True)
            print(v.decision)
        """)
        procs = [subprocess.Popen([sys.executable, "-c", code, json.dumps(out), f"agent {i}"],
                                  stdout=subprocess.PIPE, text=True) for i in range(6)]
        results = sorted(p.communicate(timeout=30)[0].strip() for p in procs)
        self.assertEqual(results, ["allow"] + ["block"] * 5)

    def test_settle_unknown_ref(self):
        self.assertFalse(sf.State(self.state_dir).settle("nope", "sent"))

    def test_corrupt_queue_refuses(self):
        st = sf.State(self.state_dir)
        st.queue_path.write_text("[oops", "utf-8")
        with self.assertRaises(sf.FuseError):
            st.queue()


class PolicyFiles(FuseCase):
    def test_discovery_and_validation(self):
        proj = self.tmp / "proj"
        (proj / ".send-fuse").mkdir(parents=True)
        (proj / "sub" / "deeper").mkdir(parents=True)
        (proj / ".send-fuse" / "policy.json").write_text(json.dumps(
            {"me": [ME], "thread_source": {"type": "json", "path": "mail.json"}}), "utf-8")
        pol = sf.load_policy(start=proj / "sub" / "deeper")
        self.assertEqual(pol["me"], [ME])
        self.assertEqual(sf.Path(pol["_base"]), proj.resolve())
        self.assertEqual(pol["daily_cap"], sf.DEFAULT_POLICY["daily_cap"])
        for bad in ({"me": "x"}, {"typo_key": 1}, {"review": "sometimes"}, {"bash": "maybe"}):
            (proj / ".send-fuse" / "policy.json").write_text(json.dumps(bad), "utf-8")
            with self.assertRaises(sf.FuseError, msg=bad):
                sf.load_policy(start=proj)

    def test_env_override(self):
        p = self.tmp / "p.json"
        p.write_text(json.dumps({"daily_cap": 3}), "utf-8")
        import os
        os.environ["SENDFUSE_POLICY"] = str(p)
        self.assertEqual(sf.load_policy()["daily_cap"], 3)
        os.environ["SENDFUSE_POLICY"] = str(self.tmp / "missing.json")
        with self.assertRaises(sf.FuseError):
            sf.load_policy()
