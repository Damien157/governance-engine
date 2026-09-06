"""DNA-like encoder/decoder for 3DM candidates (Damien codon + clash-motif spec).

Map
----
* Each element of X ∪ Y ∪ Z gets a unique fixed-length codon over {A,C,G}
  (T is reserved so a T-run cannot arise from data).
* A triple is codon_x + codon_y + codon_z.
* A candidate M is the **ordered** (sorted) concatenation of its k triples.
* Forbidden clash motif ``CLASH_MOTIF = "TTTT"`` is inserted immediately
  before the first triple that reuses an x, y, or z already seen. Valid
  matchings therefore never contain T, so non-disjoint candidates can be
  rejected by a sequence-level motif scan (no decode required).
* Pool D = {E_DNA(M) : M in S}.
* Decoder strips clash motifs and recovers M from the codon body.
* Hard V still gates admission to L; the motif is a sequence-level filter
  only, not a substitute for V.

GC: data codons are chosen from {A,C,G}^L preferring ~1/3–2/3 G/C.
"""

from __future__ import annotations

from itertools import product
from typing import Iterable

from .enumerator import hard_valid, matching_key
from .instance import Instance3DM, Triple

# Reserved T-run. Data codons use only {A,C,G}, so this cannot appear in a
# valid body or as a codon-boundary artefact.
CLASH_MOTIF = "TTTT"
DATA_BASES = "ACG"
ALL_BASES = "ACGT"


def _codon_length(n_elements: int) -> int:
    L = 1
    while 3**L < n_elements:
        L += 1
    return max(L, 2)


def _gc_fraction(word: str) -> float:
    if not word:
        return 0.0
    return sum(ch in "GC" for ch in word) / len(word)


def _candidate_codons(L: int) -> list[str]:
    """Length-L words over {A,C,G}, no 3-run homopolymer, mid GC preferred."""
    words = ["".join(p) for p in product(DATA_BASES, repeat=L)]
    filtered = [w for w in words if "AAA" not in w and "CCC" not in w and "GGG" not in w]
    if len(filtered) < 2:
        filtered = words
    filtered.sort(key=lambda w: (abs(_gc_fraction(w) - 0.5), w))
    return filtered


class DNACodec:
    """Unique codon per element of X ∪ Y ∪ Z; clash motif on coordinate reuse."""

    def __init__(self, inst: Instance3DM):
        elems = inst.universe()
        if not elems:
            raise ValueError("empty X ∪ Y ∪ Z")
        self.elements = elems
        self.codon_len = _codon_length(len(elems))
        pool = _candidate_codons(self.codon_len)
        if len(pool) < len(elems):
            raise ValueError(
                f"not enough safe {self.codon_len}-mers for {len(elems)} elements"
            )
        self.codon_of: dict[int, str] = {e: pool[i] for i, e in enumerate(elems)}
        self.elem_of: dict[str, int] = {c: e for e, c in self.codon_of.items()}
        self.triple_len = 3 * self.codon_len
        self.clash_motif = CLASH_MOTIF

    def document_map(self) -> dict:
        """Human-readable description of the codon table and motifs."""
        return {
            "codon_len": self.codon_len,
            "data_alphabet": DATA_BASES,
            "reserved_T": "T reserved for clash motif; never used in data codons",
            "clash_motif": CLASH_MOTIF,
            "triple": "codon_x + codon_y + codon_z",
            "matching": "ordered (sorted) concatenation of k triples; "
            "CLASH_MOTIF inserted before the first reuse of an x, y, or z",
            "gc_policy": "codons sorted toward 50% GC over {A,C,G}",
            "codons": {str(e): self.codon_of[e] for e in self.elements},
        }

    def encode_element(self, elem: int) -> str:
        return self.codon_of[elem]

    def encode_triple(self, t: Triple) -> str:
        x, y, z = t
        return self.codon_of[x] + self.codon_of[y] + self.codon_of[z]

    def encode_matching(self, M: Iterable[Triple]) -> str:
        """E_DNA(M): ordered concat of k triples, with clash motif on reuse."""
        triples = matching_key(M)
        parts: list[str] = []
        seen_x: set[int] = set()
        seen_y: set[int] = set()
        seen_z: set[int] = set()
        for t in triples:
            x, y, z = t
            if x in seen_x or y in seen_y or z in seen_z:
                parts.append(CLASH_MOTIF)
            parts.append(self.encode_triple(t))
            seen_x.add(x)
            seen_y.add(y)
            seen_z.add(z)
        return "".join(parts)

    def has_clash_motif(self, seq: str) -> bool:
        """Sequence-level invalidity test (no decode)."""
        return CLASH_MOTIF in seq

    def decode_matching(self, seq: str) -> tuple[Triple, ...]:
        """Recover the ordered triple tuple. Clash motifs are stripped."""
        if CLASH_MOTIF in seq:
            seq = seq.replace(CLASH_MOTIF, "")
        if len(seq) % self.triple_len != 0:
            raise ValueError(
                f"sequence length {len(seq)} not a multiple of triple length "
                f"{self.triple_len}"
            )
        triples: list[Triple] = []
        for i in range(0, len(seq), self.triple_len):
            block = seq[i : i + self.triple_len]
            triples.append(self._decode_triple(block))
        return tuple(triples)

    def _decode_triple(self, block: str) -> Triple:
        L = self.codon_len
        cx, cy, cz = block[:L], block[L : 2 * L], block[2 * L : 3 * L]
        try:
            return (self.elem_of[cx], self.elem_of[cy], self.elem_of[cz])
        except KeyError as exc:
            raise ValueError(f"unknown codon in triple block {block!r}") from exc

    def encode_pool(self, S: Iterable[Iterable[Triple]]) -> dict[tuple[Triple, ...], str]:
        """Pool D = {E_DNA(M) : M in S}, keyed by canonical matching labels."""
        return {matching_key(M): self.encode_matching(M) for M in S}

    def sequence_filter_valid(self, D: dict[tuple[Triple, ...], str]) -> list[tuple[Triple, ...]]:
        """Eliminate non-disjoint encodings by motif scan only."""
        return [key for key, seq in D.items() if not self.has_clash_motif(seq)]

    def mutate(self, seq: str, n_subs: int, rng) -> str:
        """Random nucleotide substitutions (robustness probe)."""
        if n_subs <= 0 or not seq:
            return seq
        chars = list(seq)
        n = min(n_subs, len(chars))
        idxs = rng.sample(range(len(chars)), n)
        for i in idxs:
            others = [b for b in ALL_BASES if b != chars[i]]
            chars[i] = rng.choice(others)
        return "".join(chars)


def motif_agrees_with_hard_V(codec: DNACodec, M: Iterable[Triple], k: int) -> bool:
    """Clash motif present iff hard V is 0 (for uncorrupted encodings)."""
    seq = codec.encode_matching(M)
    return codec.has_clash_motif(seq) == (not hard_valid(M, k))
