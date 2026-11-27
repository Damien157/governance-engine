"""Advanced test suite for ``solvers/cdcl.c`` (the CDCL SAT solver).

Runs under ``python -m unittest`` and under ``pytest`` (plain ``unittest.TestCase``
classes, no third-party dependencies).

What is checked, and *why each check is independent of the solver's own logic*:

* **Model validation** - every SAT answer is re-checked clause by clause against
  the original CNF. The solver is never trusted about its own SAT claims.
* **Two oracles** - brute force (n <= 16) and a separate Python DPLL (n <= ~32).
  The DPLL oracle is itself differential-tested against brute force first
  (``TestOracleSelfCheck``), so the tester is tested.
* **Metamorphic relations** - verdicts must be invariant under variable
  renaming, polarity flips, clause/literal reordering, duplicated clauses,
  tautologies and subsumed clauses. SAT models are mapped back and re-validated.
  This is the only way to cross-check UNSAT verdicts beyond oracle range.
* **Structured families with known answers** - pigeonhole (UNSAT), odd/even
  cycle 2-colouring, N-queens *solution counts*, and graph 3-colouring counts
  from the chromatic polynomial, obtained by enumerating every model with
  blocking clauses. A solver that is wrong about any single model changes the
  count.
* **Backbone / enumeration** - exact model counts vs brute force; adding the
  negation of a backbone literal must flip SAT to UNSAT.
* **Robustness** - malformed / unusual DIMACS, CLI errors, determinism.
* **Sanitizers** - the same corpus through an ASan+UBSan build (fatal on UB).

Known parser defects are in ``TestParserDefects`` as ``expectedFailure`` (the repo
convention). Set ``CDCL_STRICT=1`` to make them fail loudly, and when one is
fixed the suite will report an *unexpected success* so the marker gets removed.

Environment knobs:
    CDCL_BIN          use this prebuilt binary instead of compiling cdcl.c
    CDCL_FUZZ_SEED    base RNG seed (default fixed => reproducible)
    CDCL_FUZZ_SCALE   multiply fuzz iteration counts (e.g. 5 for a soak run)
    CDCL_TIMEOUT      per-solver-invocation timeout, seconds (default 30)
    CDCL_STRICT       1 => known-defect tests fail instead of expectedFailure
    CDCL_KEEP         1 => keep temp dir (failing CNFs are always kept)
"""

from __future__ import annotations

import atexit
import itertools
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]  # solvers/tests/ -> repo root
SRC = ROOT / "solvers" / "cdcl.c"

SCALE = float(os.environ.get("CDCL_FUZZ_SCALE", "1"))
BASE_SEED = int(os.environ.get("CDCL_FUZZ_SEED", "20261021"))
STRICT = bool(os.environ.get("CDCL_STRICT"))
TIMEOUT = float(os.environ.get("CDCL_TIMEOUT", "30"))

Clause = Tuple[int, ...]
Cnf = List[Clause]


def scaled(k: int) -> int:
    return max(1, int(k * SCALE))


known_defect = (lambda f: f) if STRICT else unittest.expectedFailure


# --------------------------------------------------------------------------- #
# Build + run harness
# --------------------------------------------------------------------------- #
class _Bins:
    release: Optional[str] = None
    sanitized: Optional[str] = None
    sanitized_reason: str = ""
    tmp: Optional[str] = None
    failed: bool = False
    counter = itertools.count()


_B = _Bins()


def _cleanup() -> None:
    if _B.tmp and not _B.failed and not os.environ.get("CDCL_KEEP"):
        shutil.rmtree(_B.tmp, ignore_errors=True)
    elif _B.tmp:
        print(f"\n[cdcl tests] temp dir kept (failing CNFs inside): {_B.tmp}")


atexit.register(_cleanup)


def _ensure_tmp() -> str:
    if _B.tmp is None:
        _B.tmp = tempfile.mkdtemp(prefix="cdcl_adv_")
    return _B.tmp


def _compile(flags: Sequence[str], out: str) -> Tuple[bool, str]:
    cmd = ["gcc", *flags, "-o", out, str(SRC)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:  # no gcc, etc.
        return False, f"{type(exc).__name__}: {exc}"
    return r.returncode == 0, (r.stderr or "")[-2000:]


def release_bin() -> str:
    if _B.release is None:
        env_bin = os.environ.get("CDCL_BIN")
        if env_bin:
            _B.release = env_bin
        else:
            out = os.path.join(_ensure_tmp(), "cdcl_release")
            ok, err = _compile(["-O2", "-Wall"], out)
            if not ok:
                raise unittest.SkipTest(f"cannot compile {SRC}: {err}")
            _B.release = out
    return _B.release


def sanitized_bin() -> Optional[str]:
    if _B.sanitized is None and not _B.sanitized_reason:
        out = os.path.join(_ensure_tmp(), "cdcl_asan")
        ok, err = _compile(
            ["-O1", "-g", "-fno-omit-frame-pointer",
             "-fsanitize=address,undefined", "-fno-sanitize-recover=all"],
            out,
        )
        if ok:
            _B.sanitized = out
        else:
            _B.sanitized_reason = err or "sanitizer build failed"
    return _B.sanitized


class Result(NamedTuple):
    verdict: str          # "SAT" | "UNSAT" | "" (no verdict)
    model: Optional[Dict[int, bool]]
    rc: int
    stdout: str
    stderr: str
    path: str


_ENV = {
    **os.environ,
    "ASAN_OPTIONS": "detect_leaks=0:abort_on_error=0",   # solver never frees; by design
    "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
}


def to_dimacs(n: int, clauses: Iterable[Sequence[int]]) -> str:
    cl = list(clauses)
    return f"p cnf {n} {len(cl)}\n" + "".join(" ".join(map(str, c)) + " 0\n" for c in cl)


def run_text(text: str, binary: Optional[str] = None, timeout: float = TIMEOUT) -> Result:
    binary = binary or release_bin()
    path = os.path.join(_ensure_tmp(), f"case_{next(_B.counter)}.cnf")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        p = subprocess.run([binary, path], capture_output=True, text=True,
                           timeout=timeout, env=_ENV)
    except subprocess.TimeoutExpired as exc:
        _B.failed = True
        raise AssertionError(f"solver timed out after {timeout}s on {path}") from exc
    lines = p.stdout.strip().split("\n") if p.stdout.strip() else []
    verdict = lines[0].strip() if lines else ""
    model = None
    if verdict == "SAT":
        toks = " ".join(lines[1:]).split()
        try:
            vals = [int(t) for t in toks]
        except ValueError:
            vals = []
        if vals and vals[-1] == 0:
            model = {abs(v): v > 0 for v in vals[:-1]}
    return Result(verdict, model, p.returncode, p.stdout, p.stderr, path)


def run_cnf(n: int, clauses: Cnf, binary: Optional[str] = None,
            timeout: float = TIMEOUT) -> Result:
    return run_text(to_dimacs(n, clauses), binary, timeout)


# --------------------------------------------------------------------------- #
# Independent oracles
# --------------------------------------------------------------------------- #
def satisfied_by(clauses: Iterable[Sequence[int]], model: Dict[int, bool]) -> bool:
    return all(any(model.get(abs(l), False) == (l > 0) for l in c) for c in clauses)


def brute_force_models(n: int, clauses: Cnf, limit: Optional[int] = None) -> List[int]:
    """Return satisfying assignments as bitmasks (bit v-1 = value of var v)."""
    masks = []
    for c in clauses:
        pos = neg = 0
        for l in c:
            if l > 0:
                pos |= 1 << (l - 1)
            else:
                neg |= 1 << (-l - 1)
        masks.append((pos, neg))
    full = (1 << n) - 1
    out: List[int] = []
    for a in range(1 << n):
        na = ~a & full
        if all((a & p) or (na & q) for p, q in masks):
            out.append(a)
            if limit is not None and len(out) >= limit:
                break
    return out


def mask_to_model(n: int, mask: int) -> Dict[int, bool]:
    return {v: bool(mask >> (v - 1) & 1) for v in range(1, n + 1)}


def dpll(clauses: Cnf) -> Optional[Dict[int, bool]]:
    """Deliberately simple DPLL (unit propagation + most-frequent-variable branch)."""
    norm: List[Clause] = []
    for c in clauses:
        s = set(c)
        if any(-l in s for l in s):
            continue            # tautology
        if not s:
            return None
        norm.append(tuple(sorted(s)))

    def simplify(cls: List[Clause], lit: int) -> Optional[List[Clause]]:
        out = []
        for c in cls:
            if lit in c:
                continue
            if -lit in c:
                nc = tuple(x for x in c if x != -lit)
                if not nc:
                    return None
                out.append(nc)
            else:
                out.append(c)
        return out

    def solve(cls: List[Clause], assign: Dict[int, bool]) -> Optional[Dict[int, bool]]:
        while True:
            unit = next((c[0] for c in cls if len(c) == 1), None)
            if unit is None:
                break
            assign = dict(assign)
            assign[abs(unit)] = unit > 0
            nxt = simplify(cls, unit)
            if nxt is None:
                return None
            cls = nxt
        if not cls:
            return assign
        v = Counter(abs(l) for c in cls for l in c).most_common(1)[0][0]
        for lit in (v, -v):
            nxt = simplify(cls, lit)
            if nxt is None:
                continue
            a2 = dict(assign)
            a2[v] = lit > 0
            r = solve(nxt, a2)
            if r is not None:
                return r
        return None

    return solve(norm, {})


# --------------------------------------------------------------------------- #
# Instance generators
# --------------------------------------------------------------------------- #
def random_ksat(n: int, m: int, k: int, rng: random.Random) -> Cnf:
    return [tuple(v if rng.random() < 0.5 else -v for v in rng.sample(range(1, n + 1), k))
            for _ in range(m)]


def wild_cnf(n: int, m: int, rng: random.Random) -> Cnf:
    """Clauses of size 1..4 *with replacement*: duplicate literals, tautologies and
    unit clauses all occur naturally. Stress for the watched-literal scheme."""
    out: Cnf = []
    for _ in range(m):
        k = rng.randint(1, 4)
        out.append(tuple(rng.choice((-1, 1)) * rng.randint(1, n) for _ in range(k)))
    return out


def planted_3sat(n: int, m: int, rng: random.Random) -> Tuple[Cnf, Dict[int, bool]]:
    hidden = {v: rng.random() < 0.5 for v in range(1, n + 1)}
    out: Cnf = []
    while len(out) < m:
        vs = rng.sample(range(1, n + 1), 3)
        c = tuple(v if rng.random() < 0.5 else -v for v in vs)
        if any(hidden[abs(l)] == (l > 0) for l in c):
            out.append(c)
    return out, hidden


def pigeonhole(holes: int) -> Tuple[int, Cnf]:
    """PHP(holes+1, holes): UNSAT, exponentially hard for resolution."""
    pigeons = holes + 1
    var = lambda p, h: p * holes + h + 1  # noqa: E731
    cl: Cnf = [tuple(var(p, h) for h in range(holes)) for p in range(pigeons)]
    for h in range(holes):
        for p1 in range(pigeons):
            for p2 in range(p1 + 1, pigeons):
                cl.append((-var(p1, h), -var(p2, h)))
    return pigeons * holes, cl


def queens(n: int) -> Tuple[int, Cnf]:
    var = lambda r, c: r * n + c + 1  # noqa: E731
    cl: Cnf = [tuple(var(r, c) for c in range(n)) for r in range(n)]
    cells = [(r, c) for r in range(n) for c in range(n)]
    for (r1, c1), (r2, c2) in itertools.combinations(cells, 2):
        if r1 == r2 or c1 == c2 or abs(r1 - r2) == abs(c1 - c2):
            cl.append((-var(r1, c1), -var(r2, c2)))
    return n * n, cl


def cycle_coloring(length: int, colors: int) -> Tuple[int, Cnf]:
    """Proper ``colors``-colourings of the cycle C_length with exactly-one per vertex."""
    var = lambda v, c: v * colors + c + 1  # noqa: E731
    cl: Cnf = []
    for v in range(length):
        cl.append(tuple(var(v, c) for c in range(colors)))
        for c1, c2 in itertools.combinations(range(colors), 2):
            cl.append((-var(v, c1), -var(v, c2)))
    for v in range(length):
        w = (v + 1) % length
        for c in range(colors):
            cl.append((-var(v, c), -var(w, c)))
    return length * colors, cl


def chromatic_cycle(length: int, k: int) -> int:
    return (k - 1) ** length + (-1) ** length * (k - 1)


# --------------------------------------------------------------------------- #
# Metamorphic transforms. Each returns (new_n, new_clauses, map_back(model)->model)
# --------------------------------------------------------------------------- #
def t_rename(n, cl, rng):
    perm = list(range(1, n + 1))
    rng.shuffle(perm)                      # old v -> perm[v-1]
    new = [tuple((perm[abs(l) - 1]) * (1 if l > 0 else -1) for l in c) for c in cl]
    return n, new, lambda m: {v: m[perm[v - 1]] for v in range(1, n + 1)}


def t_flip(n, cl, rng):
    flip = {v: rng.random() < 0.5 for v in range(1, n + 1)}
    new = [tuple(-l if flip[abs(l)] else l for l in c) for c in cl]
    return n, new, lambda m: {v: (not m[v]) if flip[v] else m[v] for v in range(1, n + 1)}


def t_shuffle(n, cl, rng):
    new = [tuple(rng.sample(c, len(c))) for c in cl]
    rng.shuffle(new)
    return n, new, lambda m: dict(m)


def t_duplicate(n, cl, rng):
    new = list(cl) + [c for c in cl if rng.random() < 0.5]
    rng.shuffle(new)
    return n, new, lambda m: dict(m)


def t_tautology(n, cl, rng):
    new = list(cl)
    for _ in range(max(1, len(cl) // 4)):
        v, w = rng.randint(1, n), rng.randint(1, n)
        new.append((v, -v, w if rng.random() < 0.5 else -w))
    rng.shuffle(new)
    return n, new, lambda m: dict(m)


def t_subsumed(n, cl, rng):
    new = list(cl)
    for c in cl:
        if rng.random() < 0.3:
            new.append(tuple(c) + (rng.choice((-1, 1)) * rng.randint(1, n),))
    rng.shuffle(new)
    return n, new, lambda m: dict(m)


def t_dup_literals(n, cl, rng):
    new = [tuple(c) + ((c[0],) if rng.random() < 0.3 else ()) for c in cl]
    return n, new, lambda m: dict(m)


TRANSFORMS = [t_rename, t_flip, t_shuffle, t_duplicate, t_tautology, t_subsumed, t_dup_literals]


# --------------------------------------------------------------------------- #
# Shared assertions
# --------------------------------------------------------------------------- #
class CdclCase(unittest.TestCase):
    maxDiff = None

    def _fail(self, res: Result, msg: str):
        _B.failed = True
        self.fail(f"{msg}\n  cnf: {res.path}\n  rc={res.rc} verdict={res.verdict!r}\n"
                  f"  stderr tail: {res.stderr[-400:]!r}")

    def check(self, n: int, clauses: Cnf, res: Result,
              expect: Optional[str] = None, *, require_full_model: bool = True) -> Result:
        if res.rc != 0:
            self._fail(res, "solver exited non-zero (crash / sanitizer trip?)")
        if "ERROR: AddressSanitizer" in res.stderr or "runtime error" in res.stderr:
            self._fail(res, "sanitizer diagnostic in stderr")
        if res.verdict not in ("SAT", "UNSAT"):
            self._fail(res, "no SAT/UNSAT verdict")
        if expect is not None and res.verdict != expect:
            self._fail(res, f"wrong verdict: expected {expect}")
        if res.verdict == "SAT":
            if res.model is None:
                self._fail(res, "SAT without a well-formed 0-terminated model line")
            if require_full_model:
                missing = set(range(1, n + 1)) - set(res.model)
                if missing:
                    self._fail(res, f"model omits variables {sorted(missing)[:8]}")
            if not satisfied_by(clauses, res.model):
                self._fail(res, "SAT model does NOT satisfy the formula (unsound)")
        return res

    def solve(self, n, clauses, expect=None, binary=None, **kw) -> Result:
        return self.check(n, clauses, run_cnf(n, clauses, binary), expect, **kw)


# --------------------------------------------------------------------------- #
# 0. Test the tester
# --------------------------------------------------------------------------- #
class TestOracleSelfCheck(unittest.TestCase):
    def test_dpll_oracle_agrees_with_brute_force(self):
        rng = random.Random(BASE_SEED)
        for i in range(scaled(250)):
            n = rng.randint(1, 11)
            cl = wild_cnf(n, rng.randint(0, 5 * n), rng) if i % 2 else \
                 random_ksat(n, rng.randint(0, 6 * n), min(3, n), rng) if n >= 3 else []
            bf = brute_force_models(n, cl, limit=1)
            dp = dpll(cl)
            self.assertEqual(bool(bf), dp is not None, f"seed={BASE_SEED} i={i} cl={cl}")
            if dp is not None:
                full = {v: dp.get(v, False) for v in range(1, n + 1)}
                self.assertTrue(satisfied_by(cl, full), f"dpll model invalid i={i}")


# --------------------------------------------------------------------------- #
# 1. Differential testing
# --------------------------------------------------------------------------- #
class TestDifferential(CdclCase):
    def test_brute_force_3sat_ratio_sweep(self):
        rng = random.Random(BASE_SEED + 1)
        sat = unsat = 0
        for i in range(scaled(220)):
            n = rng.randint(4, 14)
            m = int(n * rng.choice((1.5, 2.5, 3.5, 4.0, 4.3, 4.8, 5.5, 7.0)))
            cl = random_ksat(n, m, 3, rng)
            expect = "SAT" if brute_force_models(n, cl, 1) else "UNSAT"
            self.solve(n, cl, expect)
            sat += expect == "SAT"
            unsat += expect == "UNSAT"
        self.assertGreater(sat, 15, "generator produced too few SAT cases to be meaningful")
        self.assertGreater(unsat, 15, "generator produced too few UNSAT cases to be meaningful")

    def test_brute_force_wild_clause_shapes(self):
        """Mixed clause sizes with duplicate literals, tautologies, unit clauses."""
        rng = random.Random(BASE_SEED + 2)
        sat = unsat = 0
        for i in range(scaled(300)):
            n = rng.randint(1, 12)
            cl = wild_cnf(n, rng.randint(1, 6 * n), rng)
            expect = "SAT" if brute_force_models(n, cl, 1) else "UNSAT"
            self.solve(n, cl, expect)
            sat += expect == "SAT"
            unsat += expect == "UNSAT"
        self.assertGreater(sat, 20)
        self.assertGreater(unsat, 20)

    def test_dpll_oracle_medium_instances_near_threshold(self):
        rng = random.Random(BASE_SEED + 3)
        sat = unsat = 0
        for i in range(scaled(40)):
            n = rng.randint(20, 30)
            m = int(n * rng.choice((3.8, 4.2, 4.27, 4.5, 5.0)))
            cl = random_ksat(n, m, 3, rng)
            expect = "SAT" if dpll(cl) is not None else "UNSAT"
            self.solve(n, cl, expect)
            sat += expect == "SAT"
            unsat += expect == "UNSAT"
        self.assertGreater(sat, 3)
        self.assertGreater(unsat, 3)

    def test_unused_declared_variables_get_a_value(self):
        """p-line declares more variables than any clause mentions."""
        res = run_text("p cnf 9 2\n1 -2 0\n2 3 0\n")
        self.check(9, [(1, -2), (2, 3)], res, "SAT")


# --------------------------------------------------------------------------- #
# 2. Planted-solution (SAT guaranteed by construction) at sizes brute force can't reach
# --------------------------------------------------------------------------- #
class TestPlanted(CdclCase):
    def test_planted_solutions_are_found(self):
        rng = random.Random(BASE_SEED + 4)
        for i in range(scaled(40)):
            n = rng.choice((30, 60, 100, 150, 200))
            ratio = rng.choice((2.0, 3.0, 3.8, 4.2))
            cl, _hidden = planted_3sat(n, int(n * ratio), rng)
            self.solve(n, cl, "SAT")

    def test_planted_plus_negated_unit_on_backbone_is_unsat_when_forced(self):
        """Fix every variable to the planted value via units: still SAT, and the
        returned model must equal the planted assignment exactly."""
        rng = random.Random(BASE_SEED + 5)
        for _ in range(scaled(10)):
            n = rng.choice((20, 40, 80))
            cl, hidden = planted_3sat(n, int(3.5 * n), rng)
            units = [(v if hidden[v] else -v,) for v in range(1, n + 1)]
            res = self.solve(n, cl + units, "SAT")
            self.assertEqual(res.model, hidden)
            # and flipping any single unit must make it UNSAT
            v = rng.randint(1, n)
            flipped = [u for u in units if abs(u[0]) != v] + [(-units[v - 1][0],)]
            res2 = run_cnf(n, cl + units + [(-units[v - 1][0],)])
            self.check(n, cl + units + [(-units[v - 1][0],)], res2, "UNSAT")
            self.assertTrue(flipped)


# --------------------------------------------------------------------------- #
# 3. Metamorphic relations
# --------------------------------------------------------------------------- #
class TestMetamorphic(CdclCase):
    def _relation(self, n, cl, rng, tag):
        base = self.solve(n, cl)
        for t in TRANSFORMS:
            n2, cl2, back = t(n, cl, rng)
            res = self.check(n2, cl2, run_cnf(n2, cl2), expect=base.verdict)
            if res.verdict == "SAT":
                original = back(res.model)
                self.assertTrue(
                    satisfied_by(cl, original),
                    f"{tag}/{t.__name__}: model mapped back does not satisfy original "
                    f"(seed={BASE_SEED})",
                )

    def test_small_random_instances(self):
        rng = random.Random(BASE_SEED + 6)
        for i in range(scaled(60)):
            n = rng.randint(5, 14)
            cl = random_ksat(n, int(n * rng.choice((3.0, 4.3, 5.0))), 3, rng)
            self._relation(n, cl, rng, f"small{i}")

    def test_near_threshold_beyond_oracle_range(self):
        rng = random.Random(BASE_SEED + 7)
        for i in range(scaled(12)):
            n = rng.choice((60, 80, 100))
            cl = random_ksat(n, int(n * 4.26), 3, rng)
            self._relation(n, cl, rng, f"thr{i}")

    def test_wild_shapes(self):
        rng = random.Random(BASE_SEED + 8)
        for i in range(scaled(60)):
            n = rng.randint(3, 12)
            cl = wild_cnf(n, rng.randint(2, 5 * n), rng)
            self._relation(n, cl, rng, f"wild{i}")


# --------------------------------------------------------------------------- #
# 4. Structured families with known answers
# --------------------------------------------------------------------------- #
class TestStructured(CdclCase):
    def test_pigeonhole_is_unsat(self):
        for holes in range(1, 6):
            n, cl = pigeonhole(holes)
            self.solve(n, cl, "UNSAT")

    def test_pigeonhole_minus_one_pigeon_is_sat(self):
        for holes in range(2, 7):
            n, cl = pigeonhole(holes)
            pigeons = holes + 1
            drop = pigeons - 1  # remove the last pigeon's "must be placed" clause
            cl2 = [c for i, c in enumerate(cl) if i != drop]
            self.solve(n, cl2, "SAT")

    def test_two_coloring_of_cycles(self):
        for length in range(3, 15):
            n, cl = cycle_coloring(length, 2)
            self.solve(n, cl, "SAT" if length % 2 == 0 else "UNSAT")

    def test_unsat_core_with_padding(self):
        """A tiny UNSAT core hidden in a large satisfiable haystack."""
        rng = random.Random(BASE_SEED + 9)
        n = 120
        cl, _ = planted_3sat(n, 300, rng)
        core = [(1, 2), (1, -2), (-1, 2), (-1, -2)]
        rng.shuffle(cl)
        self.solve(n, cl + core, "UNSAT")

    def test_xor_chain_parity_contradiction(self):
        """x1^x2^...^xk = 0 and = 1 simultaneously via Tseitin chains: UNSAT."""
        k = 7

        def xor_chain(first_aux: int, rhs: int) -> Cnf:
            cl: Cnf = []
            prev = 1
            aux = first_aux
            for i in range(2, k + 1):
                a, b, c = prev, i, aux  # c <-> a xor b
                cl += [(-a, -b, -c), (a, b, -c), (a, -b, c), (-a, b, c)]
                prev, aux = c, aux + 1
            cl.append((prev,) if rhs else (-prev,))
            return cl

        cl = xor_chain(k + 1, 0) + xor_chain(2 * k + 1, 1)
        self.solve(3 * k, cl, "UNSAT")


# --------------------------------------------------------------------------- #
# 5. Exact model counting via enumeration (strongest end-to-end soundness check)
# --------------------------------------------------------------------------- #
def enumerate_models(self: CdclCase, n: int, clauses: Cnf, cap: int) -> List[Dict[int, bool]]:
    found: List[Dict[int, bool]] = []
    work = list(clauses)
    while len(found) <= cap:
        res = self.check(n, work, run_cnf(n, work))
        if res.verdict == "UNSAT":
            return found
        found.append(res.model)
        work.append(tuple(-v if res.model[v] else v for v in range(1, n + 1)))
    self.fail(f"more than {cap} models; raise the cap or shrink the instance")
    return found


class TestEnumeration(CdclCase):
    def test_nqueens_solution_counts(self):
        known = {1: 1, 2: 0, 3: 0, 4: 2, 5: 10, 6: 4, 7: 40}
        for size, count in known.items():
            n, cl = queens(size)
            models = enumerate_models(self, n, cl, cap=count + 1)
            self.assertEqual(len(models), count, f"{size}-queens solution count")
            self.assertEqual(len({tuple(sorted(k for k, v in m.items() if v)) for m in models}),
                             count, "enumeration returned duplicate models")
            for m in models:
                self.assertEqual(sum(m.values()), size, "each queens model has n queens")

    def test_three_coloring_counts_match_chromatic_polynomial(self):
        for length in (3, 4, 5, 6):
            n, cl = cycle_coloring(length, 3)
            expected = chromatic_cycle(length, 3)       # 6, 18, 30, 66
            models = enumerate_models(self, n, cl, cap=expected + 1)
            self.assertEqual(len(models), expected, f"3-colourings of C{length}")

    def test_model_count_matches_brute_force(self):
        rng = random.Random(BASE_SEED + 10)
        checked = 0
        for _ in range(scaled(40)):
            n = rng.randint(3, 9)
            cl = random_ksat(n, rng.randint(n, 3 * n), 3 if n >= 3 else n, rng)
            truth = brute_force_models(n, cl)
            if len(truth) > 48:
                continue
            models = enumerate_models(self, n, cl, cap=len(truth) + 1)
            got = {sum(1 << (v - 1) for v, val in m.items() if val) for m in models}
            self.assertEqual(got, set(truth), f"model sets differ (seed={BASE_SEED})")
            checked += 1
        self.assertGreater(checked, 5)

    def test_backbone_literals(self):
        rng = random.Random(BASE_SEED + 11)
        checked = 0
        for _ in range(scaled(40)):
            n = rng.randint(6, 12)
            cl = random_ksat(n, int(n * 3.2), 3, rng)
            truth = brute_force_models(n, cl)
            if not truth:
                continue
            for v in range(1, n + 1):
                vals = {bool(a >> (v - 1) & 1) for a in truth}
                if len(vals) == 1:      # backbone variable: forced value
                    forced = vals.pop()
                    self.solve(n, cl + [(-v,) if forced else (v,)], "UNSAT")
                    self.solve(n, cl + [(v,) if forced else (-v,)], "SAT")
                    checked += 1
        self.assertGreater(checked, 5, "no backbone variables found; generator too loose")


# --------------------------------------------------------------------------- #
# 6. Robustness: input handling, CLI, determinism
# --------------------------------------------------------------------------- #
class TestRobustness(CdclCase):
    def test_zero_clauses(self):
        res = run_text("p cnf 5 0\n")
        self.check(5, [], res, "SAT")

    def test_empty_file_is_sat(self):
        res = run_text("")
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.verdict, "SAT")

    def test_empty_clause_is_unsat(self):
        self.check(1, [], run_text("p cnf 1 1\n0\n"), "UNSAT")

    def test_contradictory_units(self):
        self.solve(1, [(1,), (-1,)], "UNSAT")

    def test_unit_only_formula_model_is_forced(self):
        cl = [(1,), (-2,), (3,), (-4,)]
        res = self.solve(4, cl, "SAT")
        self.assertEqual(res.model, {1: True, 2: False, 3: True, 4: False})

    def test_clause_spanning_lines_and_many_clauses_per_line(self):
        res = run_text("p cnf 3 3\n1\n-2\n3 0 -1 2 0\n-3 0\n")
        self.check(3, [(1, -2, 3), (-1, 2), (-3,)], res, "SAT")

    def test_comments_blank_lines_and_crlf_line_endings(self):
        text = "c hello\r\np cnf 2 2\r\nc inline comment line\r\n1 2 0\r\n-1 0\r\n"
        self.check(2, [(1, 2), (-1,)], run_text(text), "SAT")

    def test_comments_and_blank_lines_lf(self):
        text = "c hello\n\n   \np cnf 2 2\nc x\n1 2 0\n-1 0\n"
        self.check(2, [(1, 2), (-1,)], run_text(text), "SAT")

    def test_variable_numbers_exceed_p_line(self):
        res = run_text("p cnf 2 1\n1 7 0\n")
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.verdict, "SAT")
        self.assertTrue(satisfied_by([(1, 7)], res.model))

    def test_duplicate_literals_and_tautologies_do_not_break_propagation(self):
        for cl, expect in [
            ([(1, 1), (-1, -1)], "UNSAT"),
            ([(1, 1, 2), (-1, -1), (-2,)], "UNSAT"),
            ([(1, -1), (2, 2, 2)], "SAT"),
            ([(3, 3, -3), (-1,), (1, 1)], "UNSAT"),
        ]:
            self.solve(3, cl, expect)

    def test_determinism(self):
        rng = random.Random(BASE_SEED + 12)
        for _ in range(scaled(8)):
            n = rng.choice((30, 60))
            cl = random_ksat(n, int(n * 4.2), 3, rng)
            a, b = run_cnf(n, cl), run_cnf(n, cl)
            self.assertEqual(a.stdout, b.stdout, "same input must give byte-identical output")

    def test_cli_missing_file_and_no_args(self):
        p = subprocess.run([release_bin(), "/definitely/not/here.cnf"],
                           capture_output=True, text=True, env=_ENV, timeout=20)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("SAT", p.stdout.split()[:1])
        p = subprocess.run([release_bin()], capture_output=True, text=True, env=_ENV, timeout=20)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Usage", p.stderr)

    def test_long_but_legal_clause_within_limits(self):
        n = 5000
        cl = [tuple(range(1, n + 1)), (-1,)]
        self.solve(n, cl, "SAT")

    def test_many_variables_sparse(self):
        n = 20000
        cl = [(i, i + 1) for i in range(1, n, 2)]
        self.solve(n, cl, "SAT")


# --------------------------------------------------------------------------- #
# 7. Known parser / input-handling defects (expectedFailure until fixed)
# --------------------------------------------------------------------------- #
class TestParserDefects(CdclCase):
    # All six were real defects in the original parser; fixed in the hardened
    # parse_dimacs. They now run as ordinary regression tests.
    """Each test asserts the *correct* behaviour. Discovered by reading the parser:
    ``atoi`` returns 0 for non-numeric text, and a 0 token with an empty buffer is
    treated as the empty clause (=> trivially UNSAT)."""

    def test_satlib_trailer_does_not_poison_a_satisfiable_formula(self):
        """SATLIB uf*.cnf files end with a ``%`` line and a lone ``0``."""
        text = "c satlib style\np cnf 3 2\n1 2 0\n-1 3 0\n%\n0\n\n"
        self.check(3, [(1, 2), (-1, 3)], run_text(text), "SAT")

    def test_blank_crlf_line_is_not_an_empty_clause(self):
        """Found by this suite: strtok delimiters omit '\\r', so a blank CRLF line yields
        the token "\\r"; atoi -> 0; parser treats it as the empty clause => bogus UNSAT."""
        text = "p cnf 2 2\r\n\r\n1 2 0\r\n-1 0\r\n"
        self.check(2, [(1, 2), (-1,)], run_text(text), "SAT")

    def test_non_numeric_token_is_rejected_not_read_as_empty_clause(self):
        res = run_text("p cnf 2 1\n1 2 0\nfoo\n")
        ok_sat = res.verdict == "SAT" and res.rc == 0
        rejected = res.rc != 0
        self.assertTrue(ok_sat or rejected,
                        "garbage token must be ignored or rejected, never become UNSAT")

    def test_unterminated_final_clause_is_not_silently_dropped(self):
        """'1 0' then an unterminated '-1' must not be answered SAT."""
        res = run_text("p cnf 1 2\n1 0\n-1")
        self.assertTrue(res.rc != 0 or res.verdict == "UNSAT",
                        f"unterminated clause silently dropped (verdict={res.verdict!r})")

    def test_comment_line_longer_than_fgets_buffer(self):
        text = "c " + "x" * 70000 + "\np cnf 2 1\n1 2 0\n"
        self.check(2, [(1, 2)], run_text(text), "SAT")

    def test_clause_with_more_than_10000_literals(self):
        """parse_dimacs keeps clauses in ``Lit buf[10000]`` without a bounds check."""
        n = 20000
        lines = ["p cnf %d 2" % n]
        for start in range(1, n + 1, 1000):
            lines.append(" ".join(str(v) for v in range(start, start + 1000)))
        lines.append("0")
        lines.append("-1 0")
        res = run_text("\n".join(lines) + "\n", timeout=60)
        self.assertEqual(res.rc, 0, "crash / overflow on a 20000-literal clause")
        self.assertEqual(res.verdict, "SAT")


# --------------------------------------------------------------------------- #
# 8. Performance envelope + stability near the phase transition
# --------------------------------------------------------------------------- #
class TestStress(CdclCase):
    def test_threshold_instances_are_stable_and_sound(self):
        rng = random.Random(BASE_SEED + 13)
        for i in range(scaled(12)):
            n = rng.choice((70, 90, 110))
            cl = random_ksat(n, int(n * 4.26), 3, rng)
            res = self.solve(n, cl)                       # SAT => model verified
            # verdict must agree on an equivalent, differently-ordered presentation
            n2, cl2, _ = t_rename(n, cl, rng)
            n2, cl2, _ = t_shuffle(n2, cl2, rng)
            self.check(n2, cl2, run_cnf(n2, cl2), expect=res.verdict)

    def test_over_constrained_large_instances_are_unsat_fast(self):
        rng = random.Random(BASE_SEED + 14)
        for _ in range(scaled(6)):
            n = 200
            cl = random_ksat(n, int(n * 8.0), 3, rng)
            self.solve(n, cl, "UNSAT")

    def test_under_constrained_large_instances_are_sat_fast(self):
        rng = random.Random(BASE_SEED + 15)
        for _ in range(scaled(6)):
            n = 2000
            cl = random_ksat(n, int(n * 2.0), 3, rng)
            self.solve(n, cl, "SAT")


# --------------------------------------------------------------------------- #
# 9. Sanitizer pass (ASan + UBSan, fatal on any report)
# --------------------------------------------------------------------------- #
class TestSanitized(CdclCase):
    def setUp(self):
        self.bin = sanitized_bin()
        if not self.bin:
            self.skipTest(f"sanitizer build unavailable: {_B.sanitized_reason[:120]}")

    def test_corpus_under_asan_ubsan(self):
        rng = random.Random(BASE_SEED + 16)
        corpus: List[Tuple[int, Cnf, Optional[str]]] = []
        for _ in range(scaled(60)):
            n = rng.randint(2, 12)
            cl = wild_cnf(n, rng.randint(1, 5 * n), rng)
            corpus.append((n, cl, "SAT" if brute_force_models(n, cl, 1) else "UNSAT"))
        for _ in range(scaled(20)):
            n = rng.randint(14, 40)
            corpus.append((n, random_ksat(n, int(n * 4.3), 3, rng), None))
        for holes in (2, 3, 4):
            n, cl = pigeonhole(holes)
            corpus.append((n, cl, "UNSAT"))
        n, cl = queens(6)
        corpus.append((n, cl, "SAT"))
        for n, cl, expect in corpus:
            self.solve(n, cl, expect, binary=self.bin)

    def test_edge_inputs_under_asan_ubsan(self):
        for text in ("", "p cnf 0 0\n", "p cnf 5 0\n", "p cnf 1 1\n0\n", "c only a comment\n",
                     "p cnf 2 1\n1 7 0\n"):
            res = run_text(text, binary=self.bin)
            self.assertEqual(res.rc, 0, f"sanitizer trip on {text!r}: {res.stderr[-300:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)