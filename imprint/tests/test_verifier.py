from imprint.enumerator import enumerate_S_L, hard_valid
from imprint.instance import generate_demo
from imprint.verifier import SoftVerifier, admit_to_L, handcrafted_score


def test_handcrafted_is_one_iff_valid():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    for M in S:
        s = handcrafted_score(M, inst.k)
        if hard_valid(M, inst.k):
            assert abs(s - 1.0) < 1e-12
        else:
            assert s < 1.0


def test_hard_V_gates_L_soft_cannot_admit_invalid():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    soft = SoftVerifier()
    soft.train_on_instance(inst, S)
    leaked = 0
    for M in S:
        p = soft.soft_score(M, inst.k)
        admitted = admit_to_L(M, inst.k, soft=p)
        assert admitted == hard_valid(M, inst.k)
        if admitted and not hard_valid(M, inst.k):
            leaked += 1
    assert leaked == 0
    assert len(L) == sum(1 for M in S if admit_to_L(M, inst.k, soft=1.0))
