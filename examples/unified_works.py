#!/usr/bin/env python3
"""
unified_works.py  -  demo and cross-check, kept as an example (not a test, not the product).
Run from the repo root:
      python3 examples/unified_works.py
      GOV_REPO=. python3 examples/unified_works.py
          (also cross-checks the model in Part 3 against the real bio_policy.py)

HONEST SCOPE
  * Part 1 is a real, working CDCL SAT solver (Python port of solvers/cdcl.c).
  * Part 2 measures the random 3-SAT phase transition with that solver.
    It is an experiment on small random instances (25 samples per point), not a
    complexity-theory result.
  * Part 3 is a *mathematical model* of the governance invariants
    (tighten-only, voucher never loosens BLOCK, content binding) verified by
    exhaustive enumeration. It mirrors src/governed_stack/bio_policy.py and
    connectors.py; it is not those files. With GOV_REPO set it is compared to them.
  * The earlier Part 4 (re-adding the cost-to-recreate planning totals) was removed:
    those figures are unmeasured planning estimates and live only in
    docs/COST_TO_RECREATE.md.
  * Part 1b compiles solvers/cdcl.c (the repo's ORIGINAL file, not the hardened
    parser; see solvers/README.md) and checks it agrees with the Python port.
  * Mail note: plain GovernedMail (mail.py) runs no bio check on mail text. The
    sealed-mail path (#23, sealed_mail.py) does, via judge_bio_request (see
    docs/SEALED_MAIL.md and tests/test_sealed_mail.py). Part 3 models only the three-level ordering and the voucher
    rule. It does not cover the semantic overlay, the content-swap seal or the
    score snapshot (#24).
"""
from __future__ import annotations

import hashlib
import itertools
import math
import os
import random
import struct
import sys
from typing import Dict, List, Optional, Sequence, Tuple

Clause = Tuple[int, ...]


class CDCL:
    def __init__(self, n: int, clauses: Sequence[Sequence[int]]):
        self.n = n
        self.val: List[Optional[bool]] = [None] * (n + 1)
        self.level = [0] * (n + 1)
        self.reason: List[Optional[int]] = [None] * (n + 1)
        self.trail: List[int] = []
        self.trail_lim: List[int] = []
        self.qhead = 0
        self.clauses: List[List[int]] = []
        self.watches: List[List[int]] = [[] for _ in range(2 * (n + 1))]
        self.act = [0.0] * (n + 1)
        self.inc = 1.0
        self.phase = [False] * (n + 1)
        self.ok = True
        self.conflicts = 0
        for c in clauses:
            if not self._add(list(c)):
                self.ok = False
                break

    @staticmethod
    def _ix(l: int) -> int:
        return 2 * abs(l) + (l < 0)

    def _value(self, l: int) -> Optional[bool]:
        v = self.val[abs(l)]
        return None if v is None else (v if l > 0 else not v)

    def _add(self, lits: List[int]) -> bool:
        lits = list(dict.fromkeys(lits))
        if any(-l in lits for l in lits):
            return True
        if not lits:
            return False
        if len(lits) == 1:
            v = self._value(lits[0])
            if v is False:
                return False
            if v is None:
                self._enqueue(lits[0], None)
            return True
        ci = len(self.clauses)
        self.clauses.append(lits)
        self.watches[self._ix(lits[0])].append(ci)
        self.watches[self._ix(lits[1])].append(ci)
        return True

    def _enqueue(self, l: int, why: Optional[int]) -> None:
        v = abs(l)
        self.val[v] = l > 0
        self.level[v] = len(self.trail_lim)
        self.reason[v] = why
        self.trail.append(l)

    def _propagate(self) -> Optional[int]:
        while self.qhead < len(self.trail):
            p = self.trail[self.qhead]
            self.qhead += 1
            fl = -p
            ws = self.watches[self._ix(fl)]
            i = j = 0
            while i < len(ws):
                ci = ws[i]
                i += 1
                c = self.clauses[ci]
                if c[0] == fl:
                    c[0], c[1] = c[1], c[0]
                if self._value(c[0]) is True:
                    ws[j] = ci
                    j += 1
                    continue
                for k in range(2, len(c)):
                    if self._value(c[k]) is not False:
                        c[1], c[k] = c[k], c[1]
                        self.watches[self._ix(c[1])].append(ci)
                        break
                else:
                    ws[j] = ci
                    j += 1
                    if self._value(c[0]) is False:
                        while i < len(ws):
                            ws[j] = ws[i]
                            j += 1
                            i += 1
                        del ws[j:]
                        return ci
                    self._enqueue(c[0], ci)
            del ws[j:]
        return None

    def _bump(self, v: int) -> None:
        self.act[v] += self.inc
        if self.act[v] > 1e100:
            self.act = [a * 1e-100 for a in self.act]
            self.inc *= 1e-100

    def _analyze(self, confl: int) -> Tuple[List[int], int]:
        seen = set()
        learnt = [0]
        counter = 0
        p: Optional[int] = None
        idx = len(self.trail) - 1
        ci: Optional[int] = confl
        dl = len(self.trail_lim)
        while True:
            for q in self.clauses[ci]:
                if p is not None and q == p:
                    continue
                v = abs(q)
                if v not in seen and self.level[v] > 0:
                    seen.add(v)
                    self._bump(v)
                    if self.level[v] >= dl:
                        counter += 1
                    else:
                        learnt.append(q)
            while abs(self.trail[idx]) not in seen:
                idx -= 1
            p = self.trail[idx]
            idx -= 1
            ci = self.reason[abs(p)]
            seen.discard(abs(p))
            counter -= 1
            if counter == 0:
                break
        learnt[0] = -p
        if len(learnt) == 1:
            return learnt, 0
        best = max(range(1, len(learnt)), key=lambda k: self.level[abs(learnt[k])])
        learnt[1], learnt[best] = learnt[best], learnt[1]
        return learnt, self.level[abs(learnt[1])]

    def _cancel(self, lvl: int) -> None:
        if len(self.trail_lim) <= lvl:
            return
        lim = self.trail_lim[lvl]
        for l in self.trail[lim:]:
            v = abs(l)
            self.phase[v] = l > 0
            self.val[v] = None
            self.reason[v] = None
        del self.trail[lim:]
        del self.trail_lim[lvl:]
        self.qhead = len(self.trail)

    @staticmethod
    def luby(i: int) -> int:
        k = 1
        while (1 << k) - 1 < i:
            k += 1
        if (1 << k) - 1 == i:
            return 1 << (k - 1)
        return CDCL.luby(i - (1 << (k - 1)) + 1)

    def solve(self) -> Optional[Dict[int, bool]]:
        if not self.ok or self._propagate() is not None:
            return None
        restarts, budget = 1, 100 * self.luby(1)
        since = 0
        while True:
            confl = self._propagate()
            if confl is not None:
                self.conflicts += 1
                since += 1
                if not self.trail_lim:
                    return None
                learnt, bt = self._analyze(confl)
                self._cancel(bt)
                if len(learnt) == 1:
                    self._enqueue(learnt[0], None)
                else:
                    ci = len(self.clauses)
                    self.clauses.append(learnt)
                    self.watches[self._ix(learnt[0])].append(ci)
                    self.watches[self._ix(learnt[1])].append(ci)
                    self._enqueue(learnt[0], ci)
                self.inc /= 0.95
                continue
            if since >= budget:
                restarts += 1
                budget = 100 * self.luby(restarts)
                since = 0
                self._cancel(0)
                continue
            v = max((u for u in range(1, self.n + 1) if self.val[u] is None),
                    key=lambda u: self.act[u], default=0)
            if v == 0:
                return {u: bool(self.val[u]) for u in range(1, self.n + 1)}
            self.trail_lim.append(len(self.trail))
            self._enqueue(v if self.phase[v] else -v, None)


def sat_solve(n: int, clauses: Sequence[Sequence[int]]) -> Optional[Dict[int, bool]]:
    return CDCL(n, clauses).solve()


def satisfies(clauses: Sequence[Sequence[int]], m: Dict[int, bool]) -> bool:
    return all(any(m[abs(l)] == (l > 0) for l in c) for c in clauses)


def brute(n: int, clauses: Sequence[Sequence[int]]) -> bool:
    return any(satisfies(clauses, {v: bool(a >> (v - 1) & 1) for v in range(1, n + 1)})
               for a in range(1 << n))


def random_3sat(n: int, m: int, rng: random.Random) -> List[Clause]:
    return [tuple(v if rng.random() < .5 else -v for v in rng.sample(range(1, n + 1), 3))
            for _ in range(m)]


def check_solver() -> None:
    rng = random.Random(1)
    sat = unsat = 0
    for _ in range(300):
        n = rng.randint(3, 12)
        cl = random_3sat(n, int(n * rng.choice((2, 3, 4.3, 5, 6))), rng)
        m = sat_solve(n, cl)
        truth = brute(n, cl)
        assert (m is not None) == truth, ("solver disagrees with brute force", n, cl)
        if m is not None:
            assert satisfies(cl, m), "model does not satisfy formula"
        sat += truth
        unsat += not truth
    assert sat > 30 and unsat > 30
    holes = 4
    var = lambda p, h: p * holes + h + 1
    php = [tuple(var(p, h) for h in range(holes)) for p in range(holes + 1)]
    php += [(-var(p, h), -var(q, h)) for h in range(holes)
            for p in range(holes + 1) for q in range(p + 1, holes + 1)]
    assert sat_solve((holes + 1) * holes, php) is None
    print(f"[1] solver ok: 300 random vs brute force ({sat} SAT / {unsat} UNSAT), PHP(5,4) UNSAT")


def check_c_solver() -> None:
    import shutil, subprocess, tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "cdcl.c")
    if not os.path.exists(src):
        src = os.path.join(here, os.pardir, "solvers", "cdcl.c")
    if not (os.path.exists(src) and shutil.which("gcc")):
        print("[1b] skipped (needs gcc and solvers/cdcl.c)")
        return
    d = tempfile.mkdtemp()
    exe = os.path.join(d, "cdcl")
    subprocess.run(["gcc", "-O2", "-o", exe, src], check=True, capture_output=True)
    rng = random.Random(11)
    for i in range(120):
        n = rng.randint(3, 25)
        cl = random_3sat(n, int(n * rng.choice((3, 4.3, 5.5))), rng)
        path = os.path.join(d, "f.cnf")
        with open(path, "w") as fh:
            fh.write(f"p cnf {n} {len(cl)}\r\n" + "".join(" ".join(map(str, c)) + " 0\r\n" for c in cl))
        out = subprocess.run([exe, path], capture_output=True, text=True).stdout.split("\n")
        py = sat_solve(n, cl)
        assert (out[0] == "SAT") == (py is not None), ("C and Python disagree", n, cl)
        if out[0] == "SAT":
            m = {abs(int(t)): int(t) > 0 for t in out[1].split()[:-1]}
            assert satisfies(cl, m)
    print("[1b] C solver agrees with Python solver on 120 formulas (CRLF input)")


def phase_transition(n: int = 40, samples: int = 25) -> List[Tuple[float, float]]:
    rng = random.Random(7)
    out = []
    for alpha in (3.0, 3.6, 4.0, 4.27, 4.6, 5.0, 6.0):
        hits = sum(sat_solve(n, random_3sat(n, int(alpha * n), rng)) is not None
                   for _ in range(samples))
        out.append((alpha, hits / samples))
    return out


def check_phase() -> None:
    assert abs(math.log(2) / math.log(8 / 7) - 5.19) < 0.01
    res = phase_transition()
    fr = dict(res)
    assert fr[3.0] > 0.9 and fr[6.0] < 0.2, res
    assert all(a >= b - 0.25 for (_, a), (_, b) in zip(res, res[1:])), res
    print("[2] phase transition (n=40): " + ", ".join(f"a={a}: {f:.2f}" for a, f in res))


RANK = {"ALLOW": 0, "ALLOW_CANDIDATE": 0, "REVIEW": 1, "BLOCK": 2}
STACK = ("ALLOW", "REVIEW", "BLOCK")
POLICY = ("ALLOW_CANDIDATE", "REVIEW", "BLOCK")


def tighten(stack: str, policy: str) -> str:
    s = stack.upper() if stack in STACK else "BLOCK"
    top = max(RANK[s], RANK[policy])
    return ("ALLOW", "REVIEW", "BLOCK")[top]


def honor_voucher(voucher: bool, stack: str, policy: str, decision: str,
                  voucher_reason_present: bool) -> Tuple[str, bool]:
    if not voucher or stack != "ALLOW" or policy == "BLOCK" or decision == "BLOCK":
        return decision, False
    if not voucher_reason_present:
        return decision, False
    return "ALLOW", True


def content_commitment(fields: Sequence[str]) -> str:
    h = hashlib.sha256()
    for f in fields:
        b = f.encode("utf-8")
        h.update(struct.pack(">Q", len(b)) + b)
    return h.hexdigest()


def check_governance() -> None:
    for s, p in itertools.product(STACK, POLICY):
        out = tighten(s, p)
        assert RANK[out] >= RANK[s] and RANK[out] >= RANK[p], (s, p)
        if "BLOCK" in (s, p):
            assert out == "BLOCK"
        assert tighten(out, p) == out
        for p2 in POLICY:
            assert tighten(tighten(s, p), p2) == tighten(tighten(s, p2), p)
    for s, s2 in itertools.product(STACK, STACK):
        if RANK[s] <= RANK[s2]:
            for p in POLICY:
                assert RANK[tighten(s, p)] <= RANK[tighten(s2, p)]
    assert tighten("garbage", "ALLOW_CANDIDATE") != "ALLOW"
    n = 0
    for v, s, p, d, r in itertools.product((False, True), STACK, POLICY, STACK, (False, True)):
        out, honored = honor_voucher(v, s, p, d, r)
        n += 1
        if honored:
            assert v and r and s == "ALLOW" and p != "BLOCK" and d != "BLOCK" and out == "ALLOW"
        else:
            assert out == d
        if "BLOCK" in (s, p, d):
            assert not honored
    a = content_commitment(("a@b.co", "s", "hello"))
    assert a == content_commitment(("a@b.co", "s", "hello"))
    assert a != content_commitment(("a@b.co", "s", "hellO"))
    assert content_commitment(("ab", "c")) != content_commitment(("a", "bc"))
    rng = random.Random(3)
    seen = set()
    for _ in range(5000):
        body = "".join(rng.choice("abc é漢") for _ in range(rng.randint(0, 12)))
        seen.add((body, content_commitment(("t", "s", body))))
    assert len({c for _, c in seen}) == len({b for b, _ in seen}), "collision among samples"
    print(f"[3] governance model ok: tighten-only over 9 cases, voucher over {n} cases, binding ok")
    cross_check_repo()


def cross_check_repo() -> None:
    repo = os.environ.get("GOV_REPO")
    if not repo:
        print("    (set GOV_REPO to cross-check the model against the real bio_policy.py)")
        return
    for p in ("src", ".", "hais", "imprint/src", "haven2/src"):
        sys.path.insert(0, os.path.join(repo, p))
    from governed_stack import bio_policy as bp
    for s, p in itertools.product(STACK, POLICY):
        real = bp.tighten_decision(s, bp.BioPolicyResult(decision=p, reasons=["x"]))[0]
        assert real == tighten(s, p), ("model != repo tighten", s, p, real)
    for v, s, p, d, r in itertools.product((False, True), STACK, POLICY, STACK, (False, True)):
        pol = bp.BioPolicyResult(decision=p, reasons=["x"])
        env = ["human_review:approved_via_voucher:t"] if r else []
        real = bp.apply_bio_voucher_honor(
            approval_voucher="v" if v else None, stack_decision=s, policy=pol,
            env_reasons=env, decision=d, bio_reasons=[], bio_code="C")
        m_out, m_h = honor_voucher(v, s, p, d, r)
        assert (real[0], real[3]) == (m_out, m_h), ("model != repo voucher", v, s, p, d, r, real)
    print("    model matches the real bio_policy.py on every case")


if __name__ == "__main__":
    check_solver()
    check_c_solver()
    check_phase()
    check_governance()
    print("ALL CHECKS PASSED")
