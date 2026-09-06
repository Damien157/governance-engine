from imprint.dna import CLASH_MOTIF, DNACodec, motif_agrees_with_hard_V
from imprint.enumerator import enumerate_S_L, hard_valid, matching_key
from imprint.instance import generate_demo, generate_planted_no


def test_unique_codon_per_universe_element():
    inst = generate_demo()
    codec = DNACodec(inst)
    elems = inst.universe()
    codons = [codec.encode_element(e) for e in elems]
    assert len(set(codons)) == len(elems)
    assert all(len(c) == codec.codon_len for c in codons)
    assert all(set(c) <= set("ACG") for c in codons)
    assert "T" not in "".join(codons)


def test_matching_is_ordered_concat_of_triple_codons():
    inst = generate_demo()
    codec = DNACodec(inst)
    M = inst.planted
    seq = codec.encode_matching(M)
    assert CLASH_MOTIF not in seq
    expected = "".join(codec.encode_triple(t) for t in matching_key(M))
    assert seq == expected
    assert len(seq) == inst.k * 3 * codec.codon_len


def test_clash_motif_marks_invalids_at_sequence_level():
    inst = generate_demo()
    codec = DNACodec(inst)
    S, L = enumerate_S_L(inst)
    L_labels = {matching_key(M) for M in L}
    D = codec.encode_pool(S)
    kept = set(codec.sequence_filter_valid(D))
    assert kept == L_labels
    for M in S:
        assert motif_agrees_with_hard_V(codec, M, inst.k)
        seq = D[matching_key(M)]
        if hard_valid(M, inst.k):
            assert not codec.has_clash_motif(seq)
        else:
            assert codec.has_clash_motif(seq)
            assert CLASH_MOTIF in seq


def test_decode_recovers_M_valid_and_invalid():
    inst = generate_demo()
    codec = DNACodec(inst)
    S, _ = enumerate_S_L(inst)
    for M in S:
        seq = codec.encode_matching(M)
        rec = codec.decode_matching(seq)
        assert matching_key(rec) == matching_key(M)


def test_planted_no_all_sequences_have_clash():
    inst = generate_planted_no(n=5, n_triples=8, k=3, seed=3)
    codec = DNACodec(inst)
    S, L = enumerate_S_L(inst)
    assert L == []
    D = codec.encode_pool(S)
    assert codec.sequence_filter_valid(D) == []
    assert all(codec.has_clash_motif(seq) for seq in D.values())
