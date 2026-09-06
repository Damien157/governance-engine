from math import comb

from imprint.enumerator import enumerate_S_L
from imprint.instance import generate_demo, generate_planted_no, generate_planted_yes, generate_random


def test_demo_shape():
    inst = generate_demo()
    inst.validate_shape()
    assert inst.k == 3
    assert len(inst.T) == 9
    assert len(set(inst.X) & set(inst.Y)) == 0
    assert len(inst.universe()) == 12  # disjoint n=4


def test_planted_yes_has_language():
    inst = generate_planted_yes(n=5, n_extra=5, k=3, seed=0)
    S, L = enumerate_S_L(inst)
    assert len(S) == comb(len(inst.T), inst.k)
    assert len(L) >= 1
    assert set(inst.planted) in [set(M) for M in L]


def test_planted_no_empty_language():
    inst = generate_planted_no(n=5, n_triples=10, k=3, seed=1)
    S, L = enumerate_S_L(inst)
    assert len(S) == comb(len(inst.T), inst.k)
    assert L == []


def test_random_enumerable():
    inst = generate_random(n=4, n_triples=8, k=3, seed=2)
    S, L = enumerate_S_L(inst)
    assert len(S) == comb(8, 3)
    assert 0 <= len(L) <= len(S)
