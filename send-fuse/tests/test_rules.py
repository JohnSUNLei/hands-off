"""The rules, one by one, against fake conversations."""
import copy
import datetime as dt

from _helpers import INCIDENT, JORDAN, ME, WED, FuseCase, sf

T = sf.parse_time


class Signals(FuseCase):
    def test_confirmations(self):
        yes = ["Confirmed — see you Thursday at 2pm.", "Great, see you then!", "That time works.",
               "Thursday works for me.", "I've sent a calendar invite.", "The interview is now scheduled.",
               "Looking forward to speaking with you.", "好的，周四下午两点，已确认。", "那就这么定，到时见！"]
        no = ["Could you confirm a time?", "Please confirm your availability.", "This is not confirmed yet.",
              "The slot is unconfirmed.", "Can you share a few times?", "Thanks for applying."]
        for text in yes:
            self.assertTrue(sf.confirmation_in(sf.Message(body=text)), text)
        for text in no:
            self.assertFalse(sf.confirmation_in(sf.Message(body=text)), text)

    def test_calendar_markers(self):
        self.assertTrue(sf.confirmation_in(sf.Message(body="See attached.", attachments=["invite.ics"])))
        self.assertTrue(sf.confirmation_in(sf.Message(body="", attachments=["text/calendar"])))
        self.assertTrue(sf.confirmation_in(sf.Message(subject="Invitation: Interview @ Thu 2pm", body="")))
        self.assertTrue(sf.confirmation_in(sf.Message(subject="Accepted: Intro call", body="")))
        self.assertFalse(sf.confirmation_in(sf.Message(subject="Re: Interview", body="Any update?")))

    def test_scheduling(self):
        yes = ["Here are three times that work for me", "Are you available on Monday?",
               "Would any of these work?", "Let me know what time works", "Mon 10am or Tue 2:30 pm",
               "以下几个时间您看哪个方便", "我们约个时间聊聊"]
        no = ["Thanks, see you Thursday!", "Attached is my portfolio.", "The meeting is at 2pm."]
        for text in yes:
            self.assertTrue(sf.is_scheduling(text), text)
        for text in no:
            self.assertFalse(sf.is_scheduling(text), text)

    def test_subject_and_addresses(self):
        self.assertEqual(sf.norm_subject("RE: Fwd: re:  Hello  World"), "hello world")
        self.assertEqual(sf.norm_subject("回复：面试安排"), "面试安排")
        self.assertEqual(sf.addr_of("Jordan (Recruiting) <Jordan@X.example>"), "jordan@x.example")
        self.assertEqual(sf.addr_of("#team"), "#team")
        self.assertEqual(sf.name_of("Jordan (Northwind Recruiting) <j@x.example>"), "Jordan")


class TheIncident(FuseCase):
    """Recruiter confirmed; two days later the agent answers the first email with new times."""

    def setUp(self):
        super().setUp()
        self.write_thread(INCIDENT)

    def test_blocked_with_reasons(self):
        v = self.fuse().evaluate(self.reply(), at=T(WED))
        self.assertEqual(v.decision, "block")
        self.assertEqual(self.rules(v), ["already_settled", "not_latest"])
        self.assertIn("Jordan already confirmed", v.reasons[0].message)
        self.assertIn("2 newer message(s)", v.reasons[1].message)
        self.assertEqual(v.outgoing.to, [JORDAN])          # recipient derived from the thread

    def test_thank_you_on_latest_message_is_fine(self):
        v = self.fuse().evaluate(self.reply(body="Thanks Jordan, see you Thursday!",
                                            in_reply_to="<m3@northwind.example>"), at=T(WED))
        self.assertEqual(v.decision, "allow", v.explain())

    def test_scheduling_blocked_even_when_replying_to_latest(self):
        v = self.fuse().evaluate(self.reply(in_reply_to="<m3@northwind.example>"), at=T(WED))
        self.assertEqual(self.rules(v), ["already_settled"])

    def test_message_ids_without_brackets(self):
        v = self.fuse().evaluate(self.reply(in_reply_to="m1@northwind.example"), at=T(WED))
        self.assertIn("not_latest", self.rules(v))

    def test_found_by_subject_and_recipient(self):
        v = self.fuse().evaluate(sf.Outgoing(to=[JORDAN], subject="Re: Interview — Data Engineer, Northwind",
                                             body="Are you available Monday instead?"), at=T(WED))
        self.assertEqual(self.rules(v), ["already_settled"])

    def test_rule_can_be_switched_off(self):
        v = self.fuse(rules_off=["already_settled", "not_latest"]).evaluate(self.reply(), at=T(WED))
        self.assertEqual(v.decision, "allow")


class BallInTheirCourt(FuseCase):
    def setUp(self):
        super().setUp()
        t = copy.deepcopy(INCIDENT)
        t["messages"] = t["messages"][:2]           # we answered; they haven't yet
        self.write_thread(t)

    def test_recent_own_reply_blocks(self):
        v = self.fuse().evaluate(self.reply(body="Just checking in!", in_reply_to="<m2@example.com>"),
                                 at=T("2026-11-18T10:00:00-05:00"))
        self.assertEqual(self.rules(v), ["already_replied"])

    def test_follow_up_after_n_days(self):
        v = self.fuse().evaluate(self.reply(body="Just checking in!", in_reply_to="<m2@example.com>"),
                                 at=T("2026-11-21T10:00:00-05:00"))
        self.assertEqual(v.decision, "allow", v.explain())


class MissingThread(FuseCase):
    def test_reply_without_visible_thread_blocks(self):
        v = self.fuse().evaluate(self.reply(in_reply_to="<nope@x>"), at=T(WED))
        self.assertEqual(self.rules(v), ["thread_missing"])

    def test_no_source_configured_says_so(self):
        v = self.fuse(thread_source=None).evaluate(self.reply(), at=T(WED))
        self.assertIn("No thread_source is configured", v.reasons[0].message)

    def test_relaxed_policy(self):
        v = self.fuse(thread_source=None, require_thread_for_replies=False).evaluate(self.reply(), at=T(WED))
        self.assertEqual(v.decision, "allow")

    def test_new_message_needs_no_thread(self):
        v = self.fuse(thread_source=None).evaluate(sf.Outgoing(to=["a@b.example"], subject="Hello", body="Hi"),
                                                   at=T(WED))
        self.assertEqual(v.decision, "allow")

    def test_unreadable_source_blocks(self):
        v = self.fuse(thread_source={"type": "json", "path": "does/not/exist"}).evaluate(
            sf.Outgoing(to=["a@b.example"], subject="Hello", body="Hi"), at=T(WED))
        self.assertEqual(self.rules(v), ["thread_unreadable"])

    def test_malformed_thread_file_blocks(self):
        (self.threads / "bad.json").write_text('{"messages": [{"date": "not a date"}]}', "utf-8")
        v = self.fuse().evaluate(sf.Outgoing(to=["a@b.example"], subject="Hello", body="Hi"), at=T(WED))
        self.assertEqual(self.rules(v), ["thread_unreadable"])


class LedgerRules(FuseCase):
    def out(self, body="Report attached.", to="sam@acme.example", subject="Quarterly report"):
        return sf.Outgoing(to=[to], subject=subject, body=body, via="agent A")

    def test_one_send_per_thread_per_window(self):
        f = self.fuse(thread_source=None)
        self.assertEqual(f.check(self.out(), at=T(WED), record=True, ref="r1").decision, "allow")
        v = f.check(self.out(body="Different words"), at=T("2026-11-19T09:45:00-05:00"))
        self.assertEqual(self.rules(v), ["same_thread"])        # in flight, not yet confirmed
        self.assertIn("started sending", v.reasons[0].message)
        f.state.settle("r1", "sent")                              # PostToolUse confirmed it
        v = f.check(self.out(body="Different words"), at=T("2026-11-19T12:00:00-05:00"), record=True)
        self.assertEqual(self.rules(v), ["same_thread"])
        v = f.check(self.out(body="Different words"), at=T("2026-11-20T10:00:00-05:00"), record=True)
        self.assertEqual(v.decision, "allow", v.explain())

    def test_exact_duplicate_blocked_for_days(self):
        f = self.fuse(thread_source=None, same_thread_hours=1, require_thread_for_replies=False)
        f.check(self.out(), at=T(WED), record=True, ref="r1")
        f.state.settle("r1", "sent")
        v = f.check(self.out(subject="Re: Quarterly report"), at=T("2026-11-22T10:00:00-05:00"))
        self.assertEqual(self.rules(v), ["duplicate"])

    def test_unconfirmed_reservation_expires(self):
        f = self.fuse(thread_source=None)
        f.check(self.out(), at=T(WED), record=True, ref="toolu_1")     # PostToolUse never came
        v = f.evaluate(self.out(body="x"), at=T("2026-11-19T09:55:00-05:00"))
        self.assertEqual(v.decision, "allow", v.explain())

    def test_settled_reservation_counts(self):
        f = self.fuse(thread_source=None)
        f.check(self.out(), at=T(WED), record=True, ref="toolu_1")
        self.assertTrue(f.state.settle("toolu_1", "sent"))
        v = f.evaluate(self.out(body="x"), at=T("2026-11-19T09:55:00-05:00"))
        self.assertEqual(self.rules(v), ["same_thread"])

    def test_daily_cap_holds(self):
        f = self.fuse(thread_source=None, daily_cap=2)
        for i in range(2):
            self.assertTrue(f.check(self.out(to=f"p{i}@x.example"), at=T(WED), record=True).allowed)
        v = f.check(self.out(to="p9@x.example"), at=T(WED), record=True)
        self.assertEqual((v.decision, self.rules(v)), ("hold", ["daily_cap"]))

    def test_corrupt_ledger_refuses(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "ledger.jsonl").write_text("{not json\n", "utf-8")
        with self.assertRaises(sf.FuseError):
            self.fuse(thread_source=None).evaluate(self.out(), at=T(WED))


class QuietHoursAndReview(FuseCase):
    def test_quiet_hours_cross_midnight(self):
        q = {"start": "21:00", "end": "08:00", "tz": "America/Toronto"}
        self.assertTrue(sf.quiet_now(q, T("2026-11-19T23:40:00-05:00")))
        self.assertTrue(sf.quiet_now(q, T("2026-11-20T07:59:00-05:00")))
        self.assertFalse(sf.quiet_now(q, T("2026-11-20T08:00:00-05:00")))
        self.assertTrue(sf.quiet_now(q, T("2026-11-20T03:00:00+00:00")))       # 22:00 in Toronto
        self.assertTrue(sf.quiet_now({**q, "weekends": True}, T("2026-11-21T12:00:00-05:00")))
        self.assertFalse(sf.quiet_now(None, T("2026-11-19T23:40:00-05:00")))

    def test_same_day_window_and_bad_tz(self):
        self.assertTrue(sf.quiet_now({"start": "12:00", "end": "13:00", "tz": "UTC"}, T("2026-11-19T12:30:00Z")))
        with self.assertRaises(sf.FuseError):
            sf.quiet_now({"start": "21:00", "end": "08:00", "tz": "Mars/Olympus"}, T(WED))

    def test_quiet_hours_hold_and_queue(self):
        f = self.fuse(thread_source=None)
        v = f.check(sf.Outgoing(to=["#team"], subject="Status", body="Deploy done"),
                    at=T("2026-11-19T23:40:00-05:00"), record=True)
        self.assertEqual((v.decision, self.rules(v)), ("hold", ["quiet_hours"]))
        self.assertEqual(f.state.queue()[0]["status"], "held")

    def test_review_modes(self):
        o = sf.Outgoing(to=["new@x.example"], subject="Hi", body="Hello")
        self.assertEqual(self.fuse(thread_source=None, review="always").evaluate(o, at=T(WED)).decision, "ask")
        self.assertEqual(self.fuse(thread_source=None, review="new_recipient").evaluate(o, at=T(WED)).decision, "ask")
        self.assertEqual(self.fuse(thread_source=None).evaluate(o, at=T(WED)).decision, "allow")

    def test_block_beats_hold(self):
        self.write_thread(INCIDENT)
        v = self.fuse().evaluate(self.reply(), at=T("2026-11-19T23:40:00-05:00"))
        self.assertEqual(v.decision, "block")
        self.assertIn("quiet_hours", self.rules(v))


class HumanApproval(FuseCase):
    def test_approval_is_single_use(self):
        self.write_thread(INCIDENT)
        f = self.fuse()
        v = f.check(self.reply(), at=T(WED), record=True)
        self.assertEqual(v.decision, "block")
        f.state.set_status(v.queue_id, "approved", T(WED))
        v2 = f.check(self.reply(), at=T(WED), record=True)
        self.assertTrue(v2.allowed and v2.approved)
        v3 = f.check(self.reply(), at=T(WED), record=True)
        self.assertEqual(v3.decision, "block")

    def test_approval_expires(self):
        self.write_thread(INCIDENT)
        f = self.fuse()
        v = f.check(self.reply(), at=T(WED))
        f.state.set_status(v.queue_id, "approved", T(WED))
        self.assertFalse(f.check(self.reply(), at=T("2026-11-21T09:40:00-05:00")).allowed)

    def test_retry_does_not_pile_up(self):
        self.write_thread(INCIDENT)
        f = self.fuse()
        a = f.check(self.reply(), at=T(WED))
        b = f.check(self.reply(), at=T(WED))
        self.assertEqual(a.queue_id, b.queue_id)
        self.assertEqual(len(f.state.queue()), 1)


class LibraryApi(FuseCase):
    def test_sending_context_records_and_blocks(self):
        f = self.fuse(thread_source=None)
        sent = []
        with f.sending(sf.Outgoing(to=["a@x.example"], subject="Hi", body="Hello"), at=T(WED)):
            sent.append(1)
        self.assertEqual(sent, [1])
        self.assertEqual(f.state.ledger()[-1]["status"], "sent")
        with self.assertRaises(sf.Blocked) as cm:
            with f.sending(sf.Outgoing(to=["a@x.example"], subject="Hi", body="Hello"), at=T(WED)):
                sent.append(2)
        self.assertEqual(sent, [1])
        self.assertEqual(cm.exception.verdict.decision, "block")

    def test_failed_send_releases(self):
        f = self.fuse(thread_source=None)
        with self.assertRaises(RuntimeError):
            with f.sending(sf.Outgoing(to=["a@x.example"], subject="Hi", body="Hello"), at=T(WED)):
                raise RuntimeError("smtp down")
        self.assertEqual(f.state.ledger()[-1]["status"], "released")
        self.assertTrue(f.evaluate(sf.Outgoing(to=["a@x.example"], subject="Hi", body="Hello"), at=T(WED)).allowed)

    def test_me_is_case_insensitive(self):
        f = self.fuse(me=["You@Example.com"])
        self.assertEqual(f.me, {ME})

    def test_explain_wraps(self):
        self.write_thread(INCIDENT)
        v = self.fuse().evaluate(self.reply(), at=T(WED))
        self.assertTrue(all(len(line) <= 80 for line in v.explain(width=80).splitlines()[1:]))
        self.assertNotIn("\033[", v.explain(color=False))
        self.assertIn("\033[", v.explain(color=True))


if __name__ == "__main__":
    import unittest
    unittest.main()
