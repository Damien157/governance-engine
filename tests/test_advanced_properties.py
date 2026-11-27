"""Advanced property / invariant tests for the governance gates.

Dependency-free (unittest + seeded random; no hypothesis, no pytest needed).
Each test states the *invariant* it protects, so a failure names the safety
property that broke rather than just a value mismatch.

Layers
  A. bio policy  - exhaustive grid + seeded fuzz of ``classify_bio``,
                   ``tighten_decision``, ``apply_bio_voucher_honor``
  B. connectors  - ``assert_bound_content`` over every channel/key/value shape
  C. bus (end-to-end) - envelope binding, content-swap refusal, side-effect
                   only on ALLOW, concurrency, audit growth

Env: GOV_FUZZ_SEED (default fixed), GOV_FUZZ_SCALE (multiplier).
"""

from __future__ import annotations

import asyncio
import itertools
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in reversed((ROOT / "src", ROOT, ROOT / "hais", ROOT / "imprint" / "src",
                    ROOT / "haven2" / "src")):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from governed_stack import bio_policy as bp  # noqa: E402
from governed_stack import connectors as cn  # noqa: E402

SEED = int(os.environ.get("GOV_FUZZ_SEED", "20261021"))
SCALE = float(os.environ.get("GOV_FUZZ_SCALE", "1"))


def scaled(k: int) -> int:
    return max(1, int(k * SCALE))


DECISIONS = ("ALLOW", "REVIEW", "BLOCK")
POLICY_DECISIONS = ("ALLOW_CANDIDATE", "REVIEW", "BLOCK")
# strictness order: higher = tighter
RANK = {"ALLOW": 0, "ALLOW_CANDIDATE": 0, "REVIEW": 1, "BLOCK": 2}

DOMAINS = ("", "aging", "disease", "computational_biology", "research_ops",
           "clinical_support", "other", "Weird Domain")
CLASSES = ("", "observational", "computational", "literature", "other", "unknown_x",
           *sorted(bp.HARD_BLOCK_CLASSES), *sorted(bp.REVIEW_CLASSES))


def policy(decision: str, code=None) -> bp.BioPolicyResult:
    return bp.BioPolicyResult(decision=decision, reasons=["t"], error_code=code)


# --------------------------------------------------------------------------- #
# A. bio policy
# --------------------------------------------------------------------------- #
class TestBioClassifier(unittest.TestCase):
    def test_class_sets_are_disjoint(self):
        self.assertFalse(bp.HARD_BLOCK_CLASSES & bp.ALLOW_CLASSES)
        self.assertFalse(bp.HARD_BLOCK_CLASSES & bp.REVIEW_CLASSES)
        self.assertFalse(bp.REVIEW_CLASSES & bp.ALLOW_CLASSES)

    def test_every_hard_block_class_blocks_in_every_domain_and_flag_combo(self):
        """Invariant: no domain/flag/authority combination can soften a hard-block class."""
        for ic, dom, irr, hs, du, role in itertools.product(
                sorted(bp.HARD_BLOCK_CLASSES), DOMAINS, (False, True), (False, True),
                (False, True), ("", "pi", "admin", "ceo")):
            r = bp.classify_bio(purpose="routine work", domain=dom, intervention_class=ic,
                                irreversible=irr, human_subjects=hs, dual_use_flag=du,
                                authority_role=role)
            self.assertEqual(r.decision, "BLOCK", (ic, dom, irr, hs, du, role))
            self.assertTrue(r.error_code)

    def test_every_block_phrase_blocks_regardless_of_case_field_or_class(self):
        pinned = ("gain of function", "gain-of-function", "bioweapon", "biological weapon",
                  "weaponize", "weaponise", "select agent production",
                  "reverse genetics protocol", "enhance virulence",
                  "increase transmissibility", "gene drive construct", "release gene drive")
        self.assertTrue(set(pinned) <= set(bp._BLOCK_PHRASES), "a pinned block phrase was removed")
        for phrase in pinned:
            for variant in (phrase, phrase.upper(), phrase.title(), f"  {phrase}  ",
                            f"please {phrase} now"):
                for field in ("purpose", "summary", "risk_notes"):
                    kw = dict(purpose="x", summary="", risk_notes="")
                    kw[field] = variant
                    for ic in ("literature", "computational", "observational", "other"):
                        r = bp.classify_bio(domain="research_ops", intervention_class=ic, **kw)
                        self.assertEqual(r.decision, "BLOCK", (phrase, variant, field, ic))

    def test_dual_use_flag_always_blocks(self):
        for dom, ic in itertools.product(DOMAINS, CLASSES):
            r = bp.classify_bio(purpose="p", domain=dom, intervention_class=ic,
                                dual_use_flag=True)
            self.assertEqual(r.decision, "BLOCK", (dom, ic))

    def test_block_phrase_inside_unrelated_words_does_not_false_negative(self):
        # phrase is found as substring: surrounding noise must not hide it
        r = bp.classify_bio(purpose="we will xxbioweaponxx",
                            domain="other", intervention_class="literature")
        self.assertEqual(r.decision, "BLOCK")

    def test_human_subjects_and_irreversible_never_allow(self):
        for dom, ic in itertools.product(DOMAINS, CLASSES):
            if ic in bp.HARD_BLOCK_CLASSES:
                continue
            for hs, irr in ((True, False), (False, True), (True, True)):
                r = bp.classify_bio(purpose="p", domain=dom, intervention_class=ic,
                                    human_subjects=hs, irreversible=irr)
                self.assertEqual(r.decision, "REVIEW", (dom, ic, hs, irr))
                self.assertEqual(r.error_code, "GOV_BIO_REVIEW")

    def test_authority_role_never_upgrades_a_review(self):
        for role in ("", "pi", "admin", "root", "ceo", "SYSTEM"):
            r = bp.classify_bio(purpose="p", domain="clinical_support",
                                intervention_class="therapeutic" if "therapeutic" in
                                bp.REVIEW_CLASSES else sorted(bp.REVIEW_CLASSES)[0],
                                authority_role=role)
            self.assertEqual(r.decision, "REVIEW", role)

    def test_known_low_risk_inputs_are_allow_candidates(self):
        for ic in sorted(bp.ALLOW_CLASSES):
            r = bp.classify_bio(purpose="summarise papers", domain="research_ops",
                                intervention_class=ic)
            self.assertEqual(r.decision, "ALLOW_CANDIDATE", ic)
            self.assertIsNone(r.error_code)

    def test_unknown_class_fails_closed_to_review(self):
        r = bp.classify_bio(purpose="p", domain="clinical_support",
                            intervention_class="totally_new_class")
        self.assertEqual(r.decision, "REVIEW")

    def test_classifier_is_total_and_deterministic_under_fuzz(self):
        """Never raises, always returns a valid decision, same input => same output."""
        rng = random.Random(SEED)
        words = ["gene", "drive", "enhance", "virulence", "pathogen", "weapon", "paper",
                 "analysis", "", "  ", "ÆØÅ", "\n", "\x00", "gain", "of", "function", "x" * 500]
        for _ in range(scaled(800)):
            kw = dict(
                purpose=" ".join(rng.sample(words, rng.randint(0, 5))),
                summary=" ".join(rng.sample(words, rng.randint(0, 5))),
                risk_notes=" ".join(rng.sample(words, rng.randint(0, 5))),
                domain=rng.choice(DOMAINS + (None,)),
                intervention_class=rng.choice(CLASSES + (None,)),
                authority_role=rng.choice(("", "pi", None)),
                irreversible=rng.random() < 0.3,
                human_subjects=rng.random() < 0.3,
                dual_use_flag=rng.random() < 0.1,
            )
            r1, r2 = bp.classify_bio(**kw), bp.classify_bio(**kw)
            self.assertIn(r1.decision, POLICY_DECISIONS)
            self.assertEqual(r1, r2)
            if r1.decision != "ALLOW_CANDIDATE":
                self.assertTrue(r1.error_code, kw)
            self.assertTrue(r1.reasons)

    def test_adding_a_risk_flag_never_loosens(self):
        """Monotonicity: turning on irreversible/human_subjects/dual_use can only raise rank."""
        rng = random.Random(SEED + 1)
        for _ in range(scaled(600)):
            base = dict(purpose="p", summary=rng.choice(("", "lit review", "gene drive construct")),
                        domain=rng.choice(DOMAINS), intervention_class=rng.choice(CLASSES))
            flags = {k: rng.random() < 0.3 for k in ("irreversible", "human_subjects", "dual_use_flag")}
            before = bp.classify_bio(**base, **flags)
            for k in flags:
                if not flags[k]:
                    after = bp.classify_bio(**base, **{**flags, k: True})
                    self.assertGreaterEqual(RANK[after.decision], RANK[before.decision], (base, flags, k))

    def test_appending_text_never_loosens(self):
        """Monotonicity in text: appending words to the summary cannot lower strictness."""
        rng = random.Random(SEED + 2)
        for _ in range(scaled(400)):
            base = dict(purpose="p", domain=rng.choice(DOMAINS), intervention_class=rng.choice(CLASSES))
            a = bp.classify_bio(summary="alpha", **base)
            b = bp.classify_bio(summary="alpha " + rng.choice(("beta", "release gene drive", "")), **base)
            self.assertGreaterEqual(RANK[b.decision], RANK[a.decision])


class TestTightenOnly(unittest.TestCase):
    def test_exhaustive_never_loosens_and_block_is_absorbing(self):
        for sd, pd in itertools.product(DECISIONS, POLICY_DECISIONS):
            out, reasons, code = bp.tighten_decision(sd, policy(pd, "GOV_X"))
            self.assertGreaterEqual(RANK[out], RANK[sd], (sd, pd))
            self.assertGreaterEqual(RANK[out], RANK[pd], (sd, pd))
            if sd == "BLOCK" or pd == "BLOCK":
                self.assertEqual(out, "BLOCK", (sd, pd))
            self.assertEqual(reasons, ["t"])

    def test_allow_candidate_never_changes_stack_decision(self):
        for sd in DECISIONS:
            self.assertEqual(bp.tighten_decision(sd, policy("ALLOW_CANDIDATE"))[0], sd)

    def test_review_overlay_only_tightens_allow(self):
        self.assertEqual(bp.tighten_decision("ALLOW", policy("REVIEW"))[0], "REVIEW")
        self.assertEqual(bp.tighten_decision("REVIEW", policy("REVIEW"))[0], "REVIEW")
        self.assertEqual(bp.tighten_decision("BLOCK", policy("REVIEW"))[0], "BLOCK")

    def test_missing_or_garbage_stack_decision_fails_closed(self):
        for sd in (None, "", "block", "weird"):
            out, _, _ = bp.tighten_decision(sd, policy("ALLOW_CANDIDATE"))
            self.assertNotEqual(out, "ALLOW", sd)

    def test_policy_block_without_code_still_gets_a_code(self):
        _, _, code = bp.tighten_decision("ALLOW", policy("BLOCK", None))
        self.assertTrue(code)


class TestVoucherHonor(unittest.TestCase):
    OK_REASONS = ["human_review:approved_via_voucher:abc123"]

    def call(self, **over):
        kw = dict(approval_voucher="v", stack_decision="ALLOW", policy=policy("REVIEW"),
                  env_reasons=self.OK_REASONS, decision="REVIEW",
                  bio_reasons=["bio_policy:x"], bio_code="GOV_BIO_REVIEW")
        kw.update(over)
        return bp.apply_bio_voucher_honor(**kw)

    def test_happy_path_honors(self):
        d, reasons, code, honored = self.call()
        self.assertEqual((d, code, honored), ("ALLOW", None, True))
        self.assertIn("bio_policy:human_review_voucher_honored", reasons)

    def test_exhaustive_voucher_never_loosens_block(self):
        """Invariant: for every combination, a BLOCK in (policy, decision, stack) stays BLOCK/non-ALLOW."""
        for voucher, sd, pd, dec, reasons in itertools.product(
                (None, "", "v"), DECISIONS, POLICY_DECISIONS, DECISIONS,
                (None, [], self.OK_REASONS, ["human_review:other"])):
            d, _, code, honored = bp.apply_bio_voucher_honor(
                approval_voucher=voucher, stack_decision=sd, policy=policy(pd, "GOV_X"),
                env_reasons=reasons, decision=dec, bio_reasons=["r"], bio_code="GOV_BIO_REVIEW")
            ctx = (voucher, sd, pd, dec, reasons)
            if honored:
                self.assertTrue(voucher, ctx)
                self.assertEqual(sd, "ALLOW", ctx)
                self.assertNotEqual(pd, "BLOCK", ctx)
                self.assertNotEqual(dec, "BLOCK", ctx)
                self.assertEqual(d, "ALLOW", ctx)
                self.assertIsNone(code, ctx)
            else:
                self.assertEqual(d, dec, ctx)            # untouched
                self.assertEqual(code, "GOV_BIO_REVIEW", ctx)
            if "BLOCK" in (pd, dec) or sd != "ALLOW":
                self.assertFalse(honored, ctx)

    def test_requires_voucher_prefix_exactly(self):
        for bad in (["approved_via_voucher:x"], ["HUMAN_REVIEW:approved_via_voucher:x"],
                    [" human_review:approved_via_voucher:x"], ["xhuman_review:approved_via_voucher:"]):
            self.assertFalse(self.call(env_reasons=bad)[3], bad)

    def test_inputs_are_not_mutated(self):
        reasons = ["a"]
        env = list(self.OK_REASONS)
        self.call(bio_reasons=reasons, env_reasons=env)
        self.assertEqual(reasons, ["a"])
        self.assertEqual(env, self.OK_REASONS)


# --------------------------------------------------------------------------- #
# B. connectors: assert_bound_content
# --------------------------------------------------------------------------- #
class TestBoundContent(unittest.TestCase):
    def full(self, ch):
        return {k: "x" for k in cn._BOUND_CONTENT_KEYS[ch]}

    def test_channels_and_keys_are_what_the_pilot_brief_claims(self):
        self.assertEqual(set(cn._BOUND_CONTENT_KEYS["mail"]), {"body", "subject", "to"})
        self.assertEqual(set(cn._BOUND_CONTENT_KEYS["social"]), {"text"})
        self.assertEqual(set(cn._BOUND_CONTENT_KEYS["calendar"]),
                         {"summary", "description", "location"})

    def test_complete_envelope_passes(self):
        for ch in cn._BOUND_CONTENT_KEYS:
            cn.assert_bound_content(self.full(ch), ch)

    def test_empty_string_is_allowed_but_none_and_missing_are_not(self):
        for ch, keys in cn._BOUND_CONTENT_KEYS.items():
            for k in keys:
                ok = self.full(ch)
                ok[k] = ""
                cn.assert_bound_content(ok, ch)
                for bad in (None,):
                    env = self.full(ch)
                    env[k] = bad
                    with self.assertRaises(cn.ContentBindingError, msg=(ch, k, "None")):
                        cn.assert_bound_content(env, ch)
                env = self.full(ch)
                del env[k]
                with self.assertRaises(cn.ContentBindingError, msg=(ch, k, "missing")):
                    cn.assert_bound_content(env, ch)

    def test_unknown_or_hostile_channel_names_are_refused(self):
        for ch in ("", "MAIL", "mail ", "sms", "tool", None, "mail\x00", "../mail"):
            with self.assertRaises((cn.ContentBindingError, TypeError, KeyError), msg=repr(ch)):
                cn.assert_bound_content(self.full("mail"), ch)

    def test_extra_keys_do_not_satisfy_missing_ones(self):
        env = {"body": "b", "subject": "s", "extra_to": "x"}
        with self.assertRaises(cn.ContentBindingError):
            cn.assert_bound_content(env, "mail")

    def test_error_type_is_value_error(self):
        self.assertTrue(issubclass(cn.ContentBindingError, ValueError))

    def test_mock_sinks_start_empty_and_are_independent(self):
        a, b = cn.MockMailSender(), cn.MockMailSender()
        a.sent.append({"x": 1})
        self.assertEqual(b.sent, [])


# --------------------------------------------------------------------------- #
# C. end-to-end through the bus
# --------------------------------------------------------------------------- #
try:
    from certified_governance_unified import CryptoEngine  # noqa: E402
    from governed_stack import GovernedActionBus, GovernedStack  # noqa: E402
    _E2E_ERR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    _E2E_ERR = f"{type(exc).__name__}: {exc}"


@unittest.skipIf(_E2E_ERR, f"governed stack unavailable: {_E2E_ERR}")
class TestBusProperties(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_bus(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        key = db + ".pem"
        self.addCleanup(lambda: os.path.exists(db) and os.remove(db))
        self.addCleanup(lambda: os.path.exists(key) and os.remove(key))
        stack = GovernedStack(config={"db_path": db, "signing_key_path": key, "log_level": 40},
                              crypto=self.crypto)

        def close():
            st = getattr(getattr(stack, "engine", None), "storage", None)
            if st is not None and hasattr(st, "close"):
                try:
                    st.close()
                except Exception:
                    pass
        self.addCleanup(close)
        return GovernedActionBus(stack=stack)

    async def test_allowed_mail_is_delivered_exactly_as_approved(self):
        bus = self.make_bus()
        sender = cn.MockMailSender()
        rng = random.Random(SEED + 3)
        for i in range(scaled(12)):
            body = "Quarterly note %d %s" % (i, "é漢字🙂 \n\t " * rng.randint(0, 3))
            subject = "Subject %d" % i
            out = await bus.execute("mail", to=f"user{i}@example.com", subject=subject,
                                    body=body, side_effect=cn.bus_mail_side_effect(sender))
            self.assertEqual(out["decision"], "ALLOW", out)
            self.assertTrue(out.get("executed"))
            self.assertEqual(sender.sent[-1]["body"], body, "delivered body != approved body")
            self.assertEqual(sender.sent[-1]["subject"], subject)
            self.assertEqual(sender.sent[-1]["to"], [f"user{i}@example.com"])
        self.assertEqual(len(sender.sent), scaled(12))

    async def test_non_allow_mail_never_executes(self):
        """Invariant: whenever the decision is not ALLOW, the sink stays empty."""
        bus = self.make_bus()
        sender = cn.MockMailSender()
        for kw in (dict(to="", subject="", body=""),
                   dict(to="not-an-address", subject="s", body="b")):
            try:
                out = await bus.execute("mail", side_effect=cn.bus_mail_side_effect(sender), **kw)
            except Exception:
                continue                       # raising is also fail-closed
            if out.get("decision") != "ALLOW":
                self.assertFalse(out.get("executed"), (kw, out))
        # every delivered item must carry the full bound content
        for entry in sender.sent:
            for k in ("to", "subject", "body"):
                self.assertIn(k, entry)

    @unittest.expectedFailure
    async def test_bio_phrases_in_mail_body_are_gated(self):
        """KNOWN GAP (found by this suite): the pilot brief says bio-shaped content hits the
        bio gate, but mail.py has no bio logic - the bio classifier only runs on the separate
        ``bio`` channel. A mail body containing a hard-block phrase is ALLOWed and delivered.
        Either wire classify_bio into the mail gate or reword the brief. Remove this marker
        once fixed (the suite will report an unexpected success)."""
        bus = self.make_bus()
        sender = cn.MockMailSender()
        try:
            out = await bus.execute("mail", to="a@b.co", subject="x",
                                    body="bioweapon synthesis steps, enhance virulence",
                                    side_effect=cn.bus_mail_side_effect(sender))
            decision = out.get("decision")
        except Exception:
            decision = "BLOCK"
        self.assertNotEqual(decision, "ALLOW")
        self.assertEqual(sender.sent, [])

    async def test_bio_channel_blocks_hard_intents_and_runs_no_side_effect(self):
        bus = self.make_bus()
        logger = cn.MockBioTicketLogger()
        fx = cn.bus_bio_side_effect(logger)
        bad = [
            dict(purpose="routine", domain="research_ops", intervention_class="gain_of_function"),
            dict(purpose="routine", domain="other", intervention_class="pathogen_work"),
            dict(purpose="we will weaponize it", domain="other", intervention_class="literature"),
            dict(purpose="routine", domain="other", intervention_class="literature",
                 dual_use_flag=True),
        ]
        for kw in bad:
            blocked = False
            try:
                out = await bus.execute("bio", side_effect=fx, **kw)
                blocked = out.get("decision") != "ALLOW"
                self.assertFalse(out.get("executed"), (kw, out))
            except Exception:
                blocked = True
            self.assertTrue(blocked, f"bio intent was ALLOWed: {kw}")
        self.assertEqual(logger.tickets, [], "ticket logged for a blocked intent")

    async def test_bio_channel_allows_low_risk_literature_work(self):
        bus = self.make_bus()
        logger = cn.MockBioTicketLogger()
        out = await bus.execute("bio", purpose="summarise published papers",
                                domain="research_ops", intervention_class="literature",
                                side_effect=cn.bus_bio_side_effect(logger))
        self.assertEqual(out["decision"], "ALLOW", out)

    async def test_check_is_pure_no_delivery(self):
        bus = self.make_bus()
        sender = cn.MockMailSender()
        r = await bus.mail.check(to="a@b.co", subject="s", body="hello")
        self.assertEqual(r["decision"], "ALLOW")
        cn.assert_bound_content(r, "mail")
        self.assertEqual(sender.sent, [])

    async def test_envelope_fields_survive_unicode_and_large_bodies(self):
        bus = self.make_bus()
        for body in ("", "x" * 50_000, "é漢字🙂", "line1\r\nline2\n", "‮ rtl override"):
            r = await bus.mail.check(to="a@b.co", subject="s", body=body)
            if r["decision"] == "ALLOW":
                self.assertEqual(r["body"], body)
                cn.assert_bound_content(r, "mail")

    async def test_tampered_envelope_is_refused_by_side_effect(self):
        """A side effect handed a result whose bound content was stripped must raise."""
        sender = cn.MockMailSender()
        fx = cn.bus_mail_side_effect(sender)
        bad_envelopes = [
            {"decision": "ALLOW"},
            {"decision": "ALLOW", "subject": "s", "to": ["a@b.co"]},
            {"decision": "ALLOW", "body": "b", "to": ["a@b.co"]},
        ]
        for env in bad_envelopes:
            with self.assertRaises(Exception, msg=env):
                r = fx(env)
                if asyncio.iscoroutine(r):
                    await r
        self.assertEqual(sender.sent, [])

    async def test_concurrent_executes_deliver_only_what_was_allowed_without_crosstalk(self):
        """Invariant under concurrency: every delivered mail was ALLOWed and carries its own
        payload exactly once. The HAIS capability cap may legitimately throttle (BLOCK) some
        of a burst - that is a safety feature, so throttled requests must simply deliver nothing."""
        bus = self.make_bus()
        sender = cn.MockMailSender()
        n = scaled(20)
        outs = await asyncio.gather(*[
            bus.execute("mail", to=f"u{i}@example.com", subject=f"s{i}", body=f"body-{i}",
                        side_effect=cn.bus_mail_side_effect(sender))
            for i in range(n)
        ], return_exceptions=True)
        allowed = {i for i, o in enumerate(outs)
                   if not isinstance(o, Exception) and o.get("decision") == "ALLOW"}
        refused = set(range(n)) - allowed
        for i in refused:
            o = outs[i]
            if isinstance(o, Exception):
                self.assertIn("SendBlocked", type(o).__name__, repr(o))
        self.assertGreater(len(allowed), 0, "entire burst refused")
        got = [(s["to"][0], s["subject"], s["body"]) for s in sender.sent]
        self.assertEqual(len(got), len(set(got)), "a mail was delivered twice")
        self.assertEqual(set(got), {(f"u{i}@example.com", f"s{i}", f"body-{i}") for i in allowed},
                         "delivered set != allowed set, or payloads crossed between requests")

    async def test_calendar_and_social_bind_their_content(self):
        bus = self.make_bus()
        cal, soc = cn.MockCalendarWriter(), cn.MockSocialPublisher()
        r = await bus.calendar.check(summary="Sync", description="d", location="Room")
        if r["decision"] == "ALLOW":
            cn.assert_bound_content(r, "calendar")
        out = await bus.execute("calendar", summary="Sync", description="d", location="Room",
                                side_effect=cn.bus_calendar_side_effect(cal))
        if out["decision"] == "ALLOW":
            self.assertEqual(cal.created[-1]["summary"], "Sync")
        out = await bus.execute("social", text="hello world",
                                side_effect=cn.bus_social_side_effect(soc))
        if out["decision"] == "ALLOW":
            self.assertEqual(soc.published[-1]["text"], "hello world")


if __name__ == "__main__":
    unittest.main(verbosity=2)
