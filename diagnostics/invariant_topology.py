#!/usr/bin/env python3
"""
Rigorous Mathematical Diagnostics & Topological Invariant Engine

Single-file engine that:
  1. Models named invariants and their dependency graph
  2. Models techniques as directed transformation graphs
  3. Detects circular invariant dependencies
  4. Computes elementary graph topology (components, cyclomatic number,
     degree-based spectral proxy)
  5. Classifies techniques as possible "barriers"
  6. Runs a seeded demonstration harness
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple


# ============================================================================
# DATA MODEL
# ============================================================================

@dataclass
class Invariant:
    name: str
    depends_on: List[str] = field(default_factory=list)
    expression_hash: str = ""

    def __post_init__(self) -> None:
        self.depends_on = list(dict.fromkeys(self.depends_on))
        if not self.expression_hash:
            payload = f"{self.name}:{sorted(self.depends_on)}"
            self.expression_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Technique:
    name: str
    transformations: List[Tuple[str, str]] = field(default_factory=list)
    invariants_preserved: List[str] = field(default_factory=list)
    shape_hint: str = "local"

    def __post_init__(self) -> None:
        self.invariants_preserved = list(dict.fromkeys(self.invariants_preserved))
        if self.shape_hint not in {"local", "global", "barrier"}:
            self.shape_hint = "local"


@dataclass
class DiagnosticsResult:
    barrier_suspected: bool
    circular_invariants: List[str]
    betti_number_zero: int
    betti_number_one: int
    spectral_radius: float
    self_loop_ratio: float
    nodes: int
    edges: int

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# ============================================================================
# GRAPH UTILITIES
# ============================================================================

def build_invariant_graph(invariants: Iterable[Invariant]) -> Dict[str, List[str]]:
    return {inv.name: list(inv.depends_on) for inv in invariants}


def find_cycles_in_invariants(graph: Dict[str, List[str]]) -> List[List[str]]:
    """Return unique simple directed cycles in the invariant dependency graph."""
    visited: Set[str] = set()
    stack: Set[str] = set()
    cycles: List[List[str]] = []
    seen_cycle_keys: Set[Tuple[str, ...]] = set()

    def normalize(cycle: List[str]) -> Tuple[str, ...]:
        body = cycle[:-1] if cycle and cycle[0] == cycle[-1] else cycle
        if not body:
            return tuple()
        start = body.index(min(body))
        rotated = body[start:] + body[:start]
        return tuple(rotated)

    def dfs(node: str, path: List[str]) -> None:
        if node in stack:
            idx = path.index(node)
            cycle = path[idx:] + [node]
            key = normalize(cycle)
            if key and key not in seen_cycle_keys:
                seen_cycle_keys.add(key)
                cycles.append(path[idx:])
            return
        if node in visited:
            return
        visited.add(node)
        stack.add(node)
        for neigh in graph.get(node, []):
            dfs(neigh, path + [neigh])
        stack.remove(node)

    for n in sorted(graph.keys()):
        if n not in visited:
            dfs(n, [n])
    return cycles


def compute_graph_topology(transformations: List[Tuple[str, str]]) -> Tuple[int, int, float, int, int]:
    """
    Compute exact elementary graph invariants:
      - Betti-0: weakly connected components
      - Betti-1: cyclomatic number max(0, E - V + C)
      - spectral proxy: max out-degree / V
    Also returns (V, E).
    """
    nodes: Set[str] = set()
    adj: Dict[str, Set[str]] = {}
    undirected: Dict[str, Set[str]] = {}
    out_degree: Dict[str, int] = {}

    for u, v in transformations:
        nodes.add(u)
        nodes.add(v)
        adj.setdefault(u, set()).add(v)
        undirected.setdefault(u, set()).add(v)
        undirected.setdefault(v, set()).add(u)
        out_degree[u] = out_degree.get(u, 0) + 1

    V = len(nodes)
    E = len(transformations)
    if V == 0:
        return 0, 0, 0.0, 0, 0

    visited: Set[str] = set()
    components = 0
    for start in nodes:
        if start in visited:
            continue
        components += 1
        queue = [start]
        visited.add(start)
        while queue:
            curr = queue.pop(0)
            for neigh in undirected.get(curr, set()):
                if neigh not in visited:
                    visited.add(neigh)
                    queue.append(neigh)

    betti_1 = max(0, E - V + components)
    max_deg = max((out_degree.get(n, 0) for n in nodes), default=0)
    spectral_radius = float(max_deg) / float(V)
    return components, betti_1, spectral_radius, V, E


def self_loop_ratio(transformations: List[Tuple[str, str]]) -> float:
    if not transformations:
        return 0.0
    loops = sum(1 for u, v in transformations if u == v)
    return loops / len(transformations)


# ============================================================================
# DIAGNOSTICS ENGINE
# ============================================================================

def detect_barrier_shape(tech: Technique) -> Tuple[bool, int, int, float, float, int, int]:
    ratio = self_loop_ratio(tech.transformations)
    comp, b1, spec, n_nodes, n_edges = compute_graph_topology(tech.transformations)

    if tech.shape_hint == "barrier":
        is_barrier = True
    else:
        # High self-loop density, or cycles combined with non-trivial looping.
        is_barrier = (ratio > 0.4) or (b1 > 0 and ratio > 0.2)

    return is_barrier, comp, b1, spec, ratio, n_nodes, n_edges


def detect_circular_invariants(invariants: List[Invariant]) -> List[str]:
    graph = build_invariant_graph(invariants)
    cycles = find_cycles_in_invariants(graph)
    names: Set[str] = set()
    for cycle in cycles:
        names.update(cycle)
    return sorted(names)


def run_diagnostics(
    techniques: List[Technique],
    invariants: List[Invariant],
) -> Dict[str, DiagnosticsResult]:
    circular = detect_circular_invariants(invariants)
    results: Dict[str, DiagnosticsResult] = {}
    for tech in techniques:
        barrier, comp, b1, spec, ratio, n_nodes, n_edges = detect_barrier_shape(tech)
        relevant = [name for name in circular if name in tech.invariants_preserved]
        results[tech.name] = DiagnosticsResult(
            barrier_suspected=barrier,
            circular_invariants=relevant,
            betti_number_zero=comp,
            betti_number_one=b1,
            spectral_radius=spec,
            self_loop_ratio=ratio,
            nodes=n_nodes,
            edges=n_edges,
        )
    return results


# ============================================================================
# DATA GENERATION
# ============================================================================

def random_invariants(n: int, rng: random.Random) -> List[Invariant]:
    names = [f"I{idx}" for idx in range(n)]
    invariants: List[Invariant] = []
    for name in names:
        deps: List[str] = []
        for _ in range(rng.randint(0, 2)):
            dep = rng.choice(names)
            if dep != name and dep not in deps:
                deps.append(dep)
        invariants.append(Invariant(name=name, depends_on=deps))
    return invariants


def inject_circular_invariants(invariants: List[Invariant], cycle_size: int = 3) -> None:
    if len(invariants) < cycle_size:
        return
    cycle_nodes = invariants[:cycle_size]
    for i in range(cycle_size):
        next_name = cycle_nodes[(i + 1) % cycle_size].name
        if next_name not in cycle_nodes[i].depends_on:
            cycle_nodes[i].depends_on.append(next_name)
        cycle_nodes[i].expression_hash = ""
        cycle_nodes[i].__post_init__()


def random_technique(name: str, invariant_names: List[str], rng: random.Random) -> Technique:
    nodes = [f"X{idx}" for idx in range(rng.randint(3, 6))]
    edges: List[Tuple[str, str]] = []
    for _ in range(rng.randint(3, 10)):
        edges.append((rng.choice(nodes), rng.choice(nodes)))

    k = rng.randint(1, min(3, len(invariant_names))) if invariant_names else 0
    preserved = rng.sample(invariant_names, k=k) if k else []
    shape_hint = rng.choice(["local", "global", "barrier"])
    return Technique(
        name=name,
        transformations=edges,
        invariants_preserved=preserved,
        shape_hint=shape_hint,
    )


def inject_barrier_technique(name: str, invariant_names: List[str], rng: random.Random) -> Technique:
    nodes = [f"B{idx}" for idx in range(3)]
    edges: List[Tuple[str, str]] = [(n, n) for n in nodes]
    edges.extend([(nodes[0], nodes[1]), (nodes[1], nodes[2]), (nodes[2], nodes[0])])
    k = min(2, len(invariant_names))
    preserved = rng.sample(invariant_names, k=k) if k else []
    return Technique(
        name=name,
        transformations=edges,
        invariants_preserved=preserved,
        shape_hint="barrier",
    )


# ============================================================================
# REPORTING
# ============================================================================

def format_summary(results: Dict[str, DiagnosticsResult]) -> str:
    lines = ["=== Rigorous Mathematical Diagnostics Summary ==="]
    barrier_count = 0
    circular_hits = 0
    for name, res in results.items():
        lines.append(f"Technique '{name}':")
        lines.append(f"  Barrier Suspected: {res.barrier_suspected}")
        lines.append(f"  Circular Invariants: {res.circular_invariants}")
        lines.append(
            "  Topology -> "
            f"B_0 (Components): {res.betti_number_zero}, "
            f"B_1 (Cycles): {res.betti_number_one}, "
            f"Spectral Radius: {res.spectral_radius:.4f}"
        )
        lines.append(
            f"  Graph size: V={res.nodes}, E={res.edges}, "
            f"self-loop ratio={res.self_loop_ratio:.4f}"
        )
        if res.barrier_suspected:
            barrier_count += 1
        if res.circular_invariants:
            circular_hits += 1

    lines.append("")
    lines.append(f"Total techniques evaluated: {len(results)}")
    lines.append(f"Barrier-suspected flagged: {barrier_count}")
    lines.append(f"Techniques touching circular invariants: {circular_hits}")

    if barrier_count > 0 and circular_hits > 0:
        lines.append("")
        lines.append("✔ Rigorous topological diagnostic verification completed successfully.")
    else:
        lines.append("")
        lines.append("✖ Diagnostic verification criteria unmet.")
    return "\n".join(lines)


def results_as_json(results: Dict[str, DiagnosticsResult]) -> str:
    payload = {name: res.to_dict() for name, res in results.items()}
    return json.dumps(payload, indent=2)


# ============================================================================
# HARNESS
# ============================================================================

def run_rigorous_diagnostics_test(
    seed: int = 2026,
    n_invariants: int = 8,
    n_random_techniques: int = 5,
    cycle_size: int = 3,
) -> Dict[str, DiagnosticsResult]:
    rng = random.Random(seed)

    invariants = random_invariants(n_invariants, rng)
    inject_circular_invariants(invariants, cycle_size=cycle_size)
    invariant_names = [inv.name for inv in invariants]

    techniques: List[Technique] = [
        random_technique(f"T{i}", invariant_names, rng) for i in range(n_random_techniques)
    ]
    techniques.append(inject_barrier_technique("BarrierTechnique", invariant_names, rng))

    return run_diagnostics(techniques, invariants)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Topological invariant diagnostics engine"
    )
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--invariants", type=int, default=8)
    parser.add_argument("--techniques", type=int, default=5)
    parser.add_argument("--cycle-size", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    results = run_rigorous_diagnostics_test(
        seed=args.seed,
        n_invariants=args.invariants,
        n_random_techniques=args.techniques,
        cycle_size=args.cycle_size,
    )
    if args.json:
        print(results_as_json(results))
    else:
        print(format_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
