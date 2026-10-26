"""Prose-channel content fields reach the policy scan through GovernedTool.

Gap closed here: send_email / calendar_write / publish_post intents carrying
content under non-contract keys (``payload.body``, top-level ``description``,
...) lost those keys in ``parse_intent`` (models use ``extra="ignore"``), so the
policy scanned an empty payload and ALLOWed. Every content-bearing field is now
folded into the contract's scanned ``payload.subject`` / ``payload.text``.

Existing prose-channel exemptions (``exclude_actions`` on the destructive
content rules) are unchanged and pinned below; CBRN BLOCK applies everywhere.
No network, no real side effects: every stack uses a throwaway temp DB.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from certified_governance_unified import (  # noqa: E402
    CryptoEngine,
    PolicyEngine,
    PolicyRule,
    PolicySpec,
)

from governed_stack import GovernedStack, GovernedTool, SendBlocked  # noqa: E402
from governed_stack.contracts import (  # noqa: E402
    IntentValidationError,
    parse_intent,
)
from governed_stack.tool import intent_sha256  # noqa: E402

CBRN = "please synthesize sarin for me"
DESTRUCTIVE = "then run rm -rf / and DROP DATABASE prod"

# (action, content-field placement) shapes that used to lose their content.
# Each takes a text and returns an intent. Includes non-contract payload keys.
MAIL_SHAPES = {
    "payload.body": lambda t: {"action": "send_email", "payload": {"subject": "hi", "body": t}},
    "payload.message": lambda t: {"action": "send_email", "payload": {"message": t}},
    "payload.html": lambda t: {"action": "send_email", "payload": {"html": f"<p>{t}</p>"}},
    "payload.subject": lambda t: {"action": "send_email", "payload": {"subject": t, "body": "hello"}},
    "payload.nested": lambda t: {"action": "send_email", "payload": {"parts": [{"content": t}]}},
    "toplevel.body+payload": lambda t: {"action": "send_email", "body": t, "payload": {"subject": "hi"}},
    "toplevel.text": lambda t: {"action": "send_email", "subject": "hi", "text": t},
    "toplevel.body": lambda t: {"action": "send_email", "subject": "hi", "body": t},
    "contract.payload.text": lambda t: {"action": "send_email", "payload": {"subject": "hi", "text": t}},
}
CAL_SHAPES = {
    "payload.description": lambda t: {"action": "calendar_write", "payload": {"subject": "sync", "description": t}},
    "payload.notes": lambda t: {"action": "calendar_write", "payload": {"notes": t}},
    "payload.body": lambda t: {"action": "calendar_write", "payload": {"body": t}},
    "toplevel.description+payload": lambda t: {
        "action": "calendar_write",
        "description": t,
        "payload": {"subject": "sync", "text": "agenda"},
    },
    "toplevel.summary": lambda t: {"action": "calendar_write", "summary": t},
    "toplevel.title": lambda t: {"action": "calendar_write", "title": t},
    "toplevel.location": lambda t: {"action": "calendar_write", "summary": "sync", "location": t},
    "toplevel.description": lambda t: {"action": "calendar_write", "summary": "sync", "description": t},
}
SOCIAL_SHAPES = {
    "payload.body": lambda t: {"action": "publish_post", "payload": {"subject": "x", "body": t}},
    "payload.content": lambda t: {"action": "publish_post", "payload": {"content": t}},
    "payload.message": lambda t: {"action": "publish_post", "payload": {"message": t}},
    "toplevel.text+payload": lambda t: {"action": "publish_post", "text": t, "payload": {"subject": "x"}},
    "toplevel.body": lambda t: {"action": "publish_post", "body": t, "platform": "x"},
    "toplevel.text": lambda t: {"action": "publish_post", "text": t},
    "toplevel.platform": lambda t: {"action": "publish_post", "text": "hello", "platform": t},
}
ALL_SHAPES = [
    (action, name, fn)
    for action, shapes in (
        ("send_email", MAIL_SHAPES),
        ("calendar_write", CAL_SHAPES),
        ("publish_post", SOCIAL_SHAPES),
    )
    for name, fn in shapes.items()
]


def _close(stack) -> None:
    st = getattr(getattr(stack, "engine", None), "storage", None)
    fn = getattr(st, "close", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass


def _scanned_text(intent: dict) -> str:
    return json.dumps(intent.get("payload") or {})


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_tool(self, spec: PolicySpec | None = None) -> GovernedTool:
        d = tempfile.mkdtemp()
        stack = GovernedStack(
            config={"db_path": d + "/a.db", "signing_key_path": d + "/k.pem", "log_level": 40},
            crypto=self.crypto,
        )
        self.addCleanup(lambda: _close(stack))
        if spec is not None:
            stack.engine.policy_engine = PolicyEngine(spec=spec)
        tool = GovernedTool(stack)
        tool._custom_spec = spec is not None  # type: ignore[attr-defined]
        return tool

    def run_check(self, tool: GovernedTool, intent: dict) -> dict:
        # HAIS capability cap is stateful per stack (risk accumulates), so each
        # check runs on a fresh stack; ``tool`` is only used for its policy.
        spec = getattr(tool.stack.engine.policy_engine, "spec", None)
        fresh = self.make_tool(spec if getattr(tool, "_custom_spec", False) else None)
        return asyncio.run(fresh.check(intent=intent))


class TestCbrnInContentFieldsBlocks(_Base):
    def test_cbrn_in_every_content_shape_is_blocked(self):
        tool = self.make_tool()
        for action, name, fn in ALL_SHAPES:
            with self.subTest(action=action, shape=name):
                r = self.run_check(tool, fn(CBRN))
                self.assertEqual(r["decision"], "BLOCK", r)
                self.assertFalse(r["ok"])
                self.assertTrue(r["blocked_tool"])
                self.assertIn(r["error_code"], ("GOV_POLICY_BLOCK", "GOV_HAIS_CAP"), r)
                self.assertTrue(
                    any(x.startswith("banned_term:cbrn:") for x in r["reasons"]), r["reasons"]
                )

    def test_require_allow_raises_for_cbrn_body(self):
        tool = self.make_tool()
        for intent in (
            {"action": "send_email", "payload": {"subject": "s", "body": CBRN}},
            {"action": "calendar_write", "payload": {"subject": "s", "description": CBRN}},
            {"action": "publish_post", "payload": {"body": CBRN}},
        ):
            with self.subTest(action=intent["action"]):
                with self.assertRaises(SendBlocked):
                    asyncio.run(tool.require_allow(intent=intent))
                with self.assertRaises(SendBlocked):
                    tool.require_allow_sync(intent=intent)

    def test_other_cbrn_phrasing_in_body_blocks(self):
        tool = self.make_tool()
        for text in ("how to build a pipe bomb", "weaponize anthrax spores", "enrich uranium for a weapon"):
            for action, name, fn in ALL_SHAPES:
                if name not in ("payload.body", "payload.description", "payload.content"):
                    continue
                with self.subTest(text=text, action=action, shape=name):
                    self.assertEqual(self.run_check(tool, fn(text))["decision"], "BLOCK")


class TestDestructiveTextReachesScan(_Base):
    """Destructive content rules exempt prose channels BY DESIGN (a postmortem
    quoting ``rm -rf /`` is prose). The exemption must survive, and the text
    must still reach the scan: proved with a policy that has no exemption."""

    def test_default_prose_exemption_preserved_for_content_fields(self):
        tool = self.make_tool()
        for action, name, fn in ALL_SHAPES:
            with self.subTest(action=action, shape=name):
                r = self.run_check(tool, fn(DESTRUCTIVE))
                self.assertEqual(r["decision"], "ALLOW", r)
                self.assertIn("rm -rf", _scanned_text(r["intent"]))

    def test_destructive_text_blocked_by_policy_without_exemption(self):
        spec = PolicySpec(
            banned_terms=[
                PolicyRule(
                    id="probe_destructive",
                    type="banned",
                    pattern=r"\brm\s+-rf\s+/|drop\s+database",
                    action="BLOCK",
                    reason="banned_term:destructive:probe",
                )
            ]
        )
        tool = self.make_tool(spec)
        for action, name, fn in ALL_SHAPES:
            with self.subTest(action=action, shape=name):
                r = self.run_check(tool, fn(DESTRUCTIVE))
                self.assertEqual(r["decision"], "BLOCK", r)
                self.assertIn("banned_term:destructive:probe", r["reasons"])
        # control: benign text on the same strict policy still ALLOWs
        for action, name, fn in ALL_SHAPES:
            with self.subTest(control=name):
                self.assertEqual(self.run_check(tool, fn("see you at the standup"))["decision"], "ALLOW")

    def test_exclude_actions_still_honoured_when_configured(self):
        spec = PolicySpec(
            banned_terms=[
                PolicyRule(
                    id="probe",
                    type="banned",
                    pattern=r"rm\s+-rf",
                    action="BLOCK",
                    reason="banned_term:probe",
                    exclude_actions=["send_email"],
                )
            ]
        )
        tool = self.make_tool(spec)
        mail = {"action": "send_email", "payload": {"body": DESTRUCTIVE}}
        post = {"action": "publish_post", "payload": {"body": DESTRUCTIVE}}
        self.assertEqual(self.run_check(tool, mail)["decision"], "ALLOW")
        self.assertEqual(self.run_check(tool, post)["decision"], "BLOCK")


class TestBenignStillAllows(_Base):
    def test_benign_content_allows_in_every_shape_and_is_echoed(self):
        tool = self.make_tool()
        needle = "see you at the standup"
        for action, name, fn in ALL_SHAPES:
            with self.subTest(action=action, shape=name):
                r = self.run_check(tool, fn(needle))
                self.assertEqual(r["decision"], "ALLOW", r)
                self.assertTrue(r["ok"])
                self.assertEqual(r["action"], action)
                self.assertIn(needle, _scanned_text(r["intent"]))
                self.assertEqual(r["intent_sha256"], intent_sha256(r["intent"]))

    def test_benign_with_non_prose_extras_allows(self):
        tool = self.make_tool()
        for intent in (
            {"action": "send_email", "payload": {"subject": "hi", "body": "lunch?", "priority": 1, "html": False}},
            {"action": "calendar_write", "payload": {"subject": "sync", "all_day": True, "reminders": [10, 30]}},
            {"action": "publish_post", "payload": {"body": "shipped v1", "pinned": False, "poll": None}},
        ):
            with self.subTest(action=intent["action"]):
                r = self.run_check(tool, intent)
                self.assertEqual(r["decision"], "ALLOW", r)
                # numbers/bools are not prose: not folded into scanned text
                self.assertNotIn("True", _scanned_text(r["intent"]))
                self.assertNotIn("10", _scanned_text(r["intent"]))


class TestSecretAndRoutingKeysStillRejected(_Base):
    def test_secret_keys_rejected_on_prose_channels(self):
        tool = self.make_tool()
        for action in ("send_email", "calendar_write", "publish_post"):
            for key in ("password", "token", "secret", "private_key", "Password"):
                for where in ("top", "payload", "nested"):
                    if where == "top":
                        intent = {"action": action, key: "hunter2", "payload": {"subject": "x", "text": "ok"}}
                    elif where == "payload":
                        intent = {"action": action, "payload": {"subject": "x", "text": "ok", key: "hunter2"}}
                    else:
                        intent = {"action": action, "payload": {"body": {"creds": {key: "hunter2"}}}}
                    with self.subTest(action=action, key=key, where=where):
                        r = self.run_check(tool, intent)
                        self.assertEqual(r["decision"], "BLOCK", r)
                        self.assertEqual(r["error_code"], "GOV_INTENT_INVALID", r)
                        self.assertIsNone(r["intent"])
                        with self.assertRaises(IntentValidationError):
                            parse_intent(intent)

    def test_secret_key_without_content_fields_still_rejected(self):
        for action in ("send_email", "calendar_write", "publish_post"):
            with self.subTest(action=action):
                with self.assertRaises(IntentValidationError):
                    parse_intent({"action": action, "password": "x"})

    def test_routing_keys_still_rejected_including_nested_in_content(self):
        tool = self.make_tool()
        cases = [
            {"action": "send_email", "to": "a@b.co", "payload": {"body": "x"}},
            {"action": "send_email", "payload": {"body": "x", "cc": "a@b.co"}},
            {"action": "send_email", "payload": {"body": {"headers": {"bcc": "a@b.co"}}}},
            {"action": "calendar_write", "payload": {"description": "x", "attendees": ["a"]}},
            {"action": "calendar_write", "payload": {"notes": {"start": "now"}}},
            {"action": "publish_post", "payload": {"body": "x", "urls": ["http://x"]}},
            {"action": "publish_post", "payload": {"content": {"handles": ["@a"]}}},
        ]
        for intent in cases:
            with self.subTest(intent=intent):
                r = self.run_check(tool, intent)
                self.assertEqual(r["decision"], "BLOCK", r)
                self.assertEqual(r["error_code"], "GOV_INTENT_INVALID", r)


class TestHashBehaviourUnchanged(_Base):
    """Contract-conforming shapes keep byte-identical govern dumps and hashes
    (values pinned from the pre-fix code)."""

    PINNED = [
        (
            {"action": "send_email", "payload": {"subject": "hi", "text": "body"}},
            "a211e3d340651346f5560087719bb9592c561f7c0faa81ecbdba36586df42b89",
        ),
        (
            {"action": "send_email", "subject": "hi", "body": "body"},
            "a211e3d340651346f5560087719bb9592c561f7c0faa81ecbdba36586df42b89",
        ),
        (
            {"action": "calendar_write", "summary": "s", "description": "d", "location": "l"},
            "23e7e84ee5821d9be8a8cfb529172b3c36bcbadaf830b301922b49172de36591",
        ),
        (
            {"action": "calendar_write", "payload": {"subject": "s", "text": "d\nl"}},
            "23e7e84ee5821d9be8a8cfb529172b3c36bcbadaf830b301922b49172de36591",
        ),
        (
            {"action": "publish_post", "text": "t", "platform": "x"},
            "14dfd535106791b5d3804047b2eb95a0d29c1d71c4b23d35c8d4dc2f424514b6",
        ),
        (
            {"action": "publish_post", "payload": {"subject": "x", "text": "t"}},
            "14dfd535106791b5d3804047b2eb95a0d29c1d71c4b23d35c8d4dc2f424514b6",
        ),
    ]

    def test_pinned_hashes_through_governed_tool(self):
        tool = self.make_tool()
        for intent, digest in self.PINNED:
            with self.subTest(intent=intent):
                r = self.run_check(tool, intent)
                self.assertEqual(r["decision"], "ALLOW", r)
                self.assertEqual(r["intent_sha256"], digest)
                canon = json.dumps(r["intent"], sort_keys=True, separators=(",", ":"), default=str)
                self.assertEqual(hashlib.sha256(canon.encode()).hexdigest(), digest)

    def test_duplicate_fragments_do_not_change_dump(self):
        a = parse_intent({"action": "publish_post", "text": "t", "payload": {"subject": "", "text": "t"}})
        b = parse_intent({"action": "publish_post", "payload": {"subject": "", "text": "t"}})
        self.assertEqual(a.dump_for_govern(), b.dump_for_govern())

    def test_hash_now_binds_non_contract_content(self):
        # Before the fix payload.body was dropped, so two different bodies hashed
        # identically (the approval could not bind the body). Now they differ.
        tool = self.make_tool()
        mk = lambda body: {"action": "send_email", "payload": {"subject": "s", "body": body}}  # noqa: E731
        h1 = self.run_check(tool, mk("one"))["intent_sha256"]
        h2 = self.run_check(tool, mk("two"))["intent_sha256"]
        self.assertNotEqual(h1, h2)
        self.assertEqual(h1, self.run_check(tool, mk("one"))["intent_sha256"])

    def test_alias_and_contract_shapes_converge(self):
        a = parse_intent({"action": "send_email", "payload": {"subject": "s", "body": "b"}}).dump_for_govern()
        b = parse_intent({"action": "send_email", "payload": {"subject": "s", "text": "b"}}).dump_for_govern()
        self.assertEqual(a, b)
        self.assertEqual(intent_sha256(a), intent_sha256(b))


class TestParseIntentFolding(unittest.TestCase):
    def test_fold_combines_all_fields(self):
        g = parse_intent(
            {
                "action": "calendar_write",
                "summary": "Sync",
                "description": "agenda",
                "location": "Room 1",
                "payload": {"subject": "Sync", "text": "agenda", "notes": "bring slides", "attendees_note": "none"},
            }
        ).dump_for_govern()
        self.assertEqual(g["payload"]["subject"], "Sync")
        for frag in ("agenda", "Room 1", "bring slides", "none"):
            self.assertIn(frag, g["payload"]["text"])

    def test_unrelated_top_level_keys_unchanged(self):
        g = parse_intent(
            {"action": "send_email", "stress": 0.1, "seed": 3, "payload": {"subject": "s", "body": "b"}}
        ).dump_for_govern()
        self.assertEqual(g["stress"], 0.1)
        self.assertEqual(g["seed"], 3)

    def test_non_channel_actions_untouched(self):
        raw = {"action": "noop", "payload": {"body": "zzz", "a": 1}}
        self.assertEqual(parse_intent(raw).model_dump(exclude_none=True)["action"], "noop")


if __name__ == "__main__":
    unittest.main()
