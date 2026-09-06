"""Phase 0 imprint pipeline: 3-dimensional matching (3DM) simulation.

Hilbert space H is a dict/array over candidate labels, not a quantum device.
Empirical hit rates only — this does not resolve P vs NP.
"""

from .instance import Instance3DM, generate_demo, generate_planted_no, generate_planted_yes, generate_random
from .enumerator import enumerate_S_L, hard_valid, matching_key
from .dna import CLASH_MOTIF, DNACodec
from .verifier import SoftVerifier, handcrafted_score
from .sampler import SuperpositionSampler
from .baselines import greedy_matching, greedy_prior, uniform_sample
from .pipeline import run_pipeline

__version__ = "0.1.0"

__all__ = [
    "Instance3DM",
    "generate_demo",
    "generate_planted_no",
    "generate_planted_yes",
    "generate_random",
    "enumerate_S_L",
    "hard_valid",
    "matching_key",
    "CLASH_MOTIF",
    "DNACodec",
    "SoftVerifier",
    "handcrafted_score",
    "SuperpositionSampler",
    "greedy_matching",
    "greedy_prior",
    "uniform_sample",
    "run_pipeline",
]
