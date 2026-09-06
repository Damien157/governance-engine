from itertools import combinations

from imprint.enumerator import enumerate_S_L, hard_valid, matching_key
from imprint.instance import generate_demo


def test_hard_valid_diagonal():
    inst = generate_demo()
    assert hard_valid(inst.planted, inst.k)
    # two triples sharing x
    bad = (inst.T[0], inst.T[0], inst.T[1]) if False else None
    overlapping = [t for t in inst.T if t[0] == inst.planted[0][0] and t != inst.planted[0]]
    if overlapping:
        M = (inst.planted[0], overlapping[0], inst.planted[1])
        # may or may not be size 3 distinct; just check function on a forced clash
        clash = (inst.planted[0], (inst.planted[0][0], inst.Y[-1], inst.Z[-1]), inst.planted[1])
        assert not hard_valid(clash, 3)


def test_S_is_all_k_subsets():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    expected = {matching_key(c) for c in combinations(inst.T, inst.k)}
    assert {matching_key(M) for M in S} == expected
    assert all(hard_valid(M, inst.k) for M in L)
    assert all(not hard_valid(M, inst.k) for M in S if matching_key(M) not in {matching_key(x) for x in L})
