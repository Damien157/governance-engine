/* ============================================================================
 * cdcl.c — A CDCL SAT solver, unified with the mathematics behind it.
 * ============================================================================
 *
 * This file is a single self-contained artifact: every algorithmic piece
 * below is preceded by the math that justifies it. Nothing here is a proof
 * of P vs NP (see the note at the very end on why that's a different kind
 * of problem) — this is an exact decision procedure for one NP-complete
 * problem, 3-SAT, that is fast in practice despite having no known
 * polynomial worst-case bound.
 *
 * ----------------------------------------------------------------------
 * 1. THE PROBLEM
 * ----------------------------------------------------------------------
 * A propositional variable x_i takes a value in {0,1} (false/true).
 * A literal is x_i or its negation ¬x_i.
 * A clause is a disjunction of literals: (l_1 ∨ l_2 ∨ ... ∨ l_k).
 * A CNF formula is a conjunction of clauses: C_1 ∧ C_2 ∧ ... ∧ C_m.
 * 3-SAT restricts every clause to exactly 3 literals.
 *
 * SAT decision problem: given a CNF formula φ over n variables, does there
 * exist an assignment α: {x_1,...,x_n} → {0,1} such that every clause
 * evaluates to true under α?
 *
 * Cook–Levin theorem (1971): SAT is NP-complete. Every problem in NP
 * reduces to it in polynomial time. 3-SAT is NP-complete too (SAT reduces
 * to 3-SAT by splitting long clauses via fresh auxiliary variables).
 * This is why no polynomial-time worst-case algorithm is known: finding
 * one would prove P = NP. Nothing below changes that; this solver has
 * exponential worst-case behavior on adversarial instances by
 * construction of the problem itself, and is exact and correct on every
 * instance it terminates on.
 *
 * ----------------------------------------------------------------------
 * 2. RESOLUTION — the proof system underlying every technique below
 * ----------------------------------------------------------------------
 * Resolution rule: from clauses (A ∨ x) and (B ∨ ¬x), derive (A ∨ B).
 *   (A ∨ x) ∧ (B ∨ ¬x) ⊨ (A ∨ B)
 * This is sound (the derived clause is implied by the formula) and,
 * for propositional logic, refutation-complete: φ is UNSAT if and only
 * if the empty clause (⊥) can be derived from φ by repeated resolution.
 * Both unit propagation and clause learning below are just resolution,
 * specialized for speed.
 *
 * ----------------------------------------------------------------------
 * 3. UNIT PROPAGATION — resolution restricted to unit clauses
 * ----------------------------------------------------------------------
 * If a clause reduces to a single unassigned literal l under the current
 * partial assignment (every other literal in it is false), l must be
 * true in any satisfying extension — this is resolution against every
 * one of that clause's falsified literals, folded into one inference:
 *     (l ∨ f_1 ∨ ... ∨ f_k), f_1=0,...,f_k=0   ⟹   l = 1
 * Applying this exhaustively is called Boolean Constraint Propagation.
 *
 * Two-watched-literal invariant (Moskewicz et al., 2001, "Chaff"):
 * for each clause, track two literals not yet known false. The clause
 * only needs inspection when one of ITS TWO watched literals is falsified
 * — not on every assignment — which is what makes propagation fast
 * (amortized near-constant work per assignment instead of rescanning
 * every clause containing the assigned variable).
 *
 * A conflict is detected exactly when a clause's watched literals are
 * both false and no unwatched literal is available to replace them —
 * i.e., resolution has derived a clause every one of whose literals is
 * currently false.
 *
 * ----------------------------------------------------------------------
 * 4. CONFLICT-DRIVEN CLAUSE LEARNING — first-UIP resolution
 * ----------------------------------------------------------------------
 * On conflict, walk backward through the implication graph, resolving
 * the conflicting clause against each variable's reason clause, until
 * exactly one literal from the CURRENT decision level remains — the
 * first Unique Implication Point (1-UIP). Formally, maintain:
 *     counter = |{ literals in the working clause at current level }|
 * and resolve on the most recently assigned such literal until
 * counter = 1. The resulting clause is a valid resolvent of the
 * original formula (by repeated soundness of the resolution rule) and
 * is asserting: it becomes unit as soon as you backtrack to
 *     backtrack_level = max{ level(l) : l ∈ learnt clause, l ≠ UIP }
 * which is exactly the non-chronological jump the solver takes.
 *
 * ----------------------------------------------------------------------
 * 5. VSIDS — Variable State Independent Decaying Sum
 * ----------------------------------------------------------------------
 * Each variable v has an activity score a(v), initialized to 0.
 * On each conflict, for every variable touched during resolution:
 *     a(v) ← a(v) + inc
 * After each conflict, decay all activity uniformly:
 *     inc ← inc / decay        (decay = 0.95 here, so inc grows over time)
 * which is algebraically equivalent to multiplying every a(v) by `decay`
 * every step, without touching all n entries — an O(1) decay
 * implemented by inflating future increments instead. The branching
 * heuristic picks the unassigned variable with maximum a(v): variables
 * that recur in recent conflicts are judged most constrained, so the
 * search focuses where it is currently failing.
 *
 * ----------------------------------------------------------------------
 * 6. RESTARTS — the Luby sequence
 * ----------------------------------------------------------------------
 * CDCL search trees have heavy-tailed runtime: most runs finish fast,
 * a few take catastrophically long. Restarting the search (keeping all
 * learnt clauses, forgetting the decision trail) bounds the expected
 * cost. The Luby sequence
 *     1,1,2,1,1,2,4,1,1,2,1,1,2,4,8,...
 * defined by
 *     luby(i) = 2^(k-1)          if i = 2^k − 1
 *             = luby(i − 2^(k-1) + 1)   otherwise, where 2^(k-1) ≤ i < 2^k − 1
 * gives restart cutoffs that are within a log factor of the optimal
 * fixed strategy for an unknown heavy-tailed distribution (Luby, Sinclair,
 * Zuckerman, 1993) — this is the only piece of the solver with its own
 * optimality theorem attached.
 *
 * ----------------------------------------------------------------------
 * 7. WHAT THIS DOES NOT DO
 * ----------------------------------------------------------------------
 * This solver decides individual instances; it is not a proof that
 * 3-SAT ∈ P. Its worst-case runtime is exponential (no known SAT solver
 * avoids this), and nothing in sections 2–6 evades that — they make the
 * typical case fast, which is a different and much more tractable goal
 * than the open mathematical question of P vs NP.
 *
 * ----------------------------------------------------------------------
 * Build: gcc -O2 -o cdcl cdcl.c
 * Run:   ./cdcl instance.cnf
 * ============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

/* ---------- Basic types ---------- */

typedef int Lit;               /* literal: variable v (1-indexed) -> +v or -v */
typedef enum { UNASSIGNED = 0, TRUE_VAL = 1, FALSE_VAL = 2 } Value;

static inline int var_of(Lit l) { return l < 0 ? -l : l; }
static inline int sign_of(Lit l) { return l < 0 ? 1 : 0; } /* 1 = negative */

/* A clause is a dynamic array of literals. */
typedef struct {
    int size;
    int capacity;
    Lit *lits;
    bool learnt;
    double activity;
} Clause;

/* Watch list entry: which clause, and index of the watched literal within it. */
typedef struct WatchNode {
    int clause_idx;
    struct WatchNode *next;
} WatchNode;

/* ---------- Solver state ---------- */

#define MAX_VARS_DEFAULT 4096

typedef struct {
    int num_vars;
    int num_clauses_orig;

    Clause *clauses;      /* all clauses, original + learnt */
    int clauses_size;
    int clauses_cap;

    WatchNode **watches;  /* watches[2*var + sign] -> list of clause indices watching that literal being FALSE */
    int watches_len;

    Value *assign;        /* assign[var] */
    int *level;           /* decision level at which var was assigned */
    int *reason;          /* clause index that forced this assignment, or -1 if decision */
    Lit *trail;           /* stack of assigned literals in order */
    int trail_size;
    int *trail_lim;       /* trail_lim[d] = index into trail where decision level d begins */
    int trail_lim_size;
    int decision_level;

    double *activity;     /* VSIDS activity per variable */
    double var_inc;
    double var_decay;

    int *order_heap;      /* simple array-based priority structure (rebuilt each pick, ok for teaching solver) */

    long conflicts;
    long decisions;
    long propagations;
    long restarts;
} Solver;

static Solver S;
static bool g_trivial_unsat = false;

/* ---------- Utility: growable clause storage ---------- */

static int add_clause_storage(Lit *lits, int size, bool learnt) {
    if (S.clauses_size == S.clauses_cap) {
        S.clauses_cap = S.clauses_cap ? S.clauses_cap * 2 : 256;
        S.clauses = realloc(S.clauses, sizeof(Clause) * S.clauses_cap);
    }
    Clause *c = &S.clauses[S.clauses_size];
    c->size = size;
    c->capacity = size;
    c->lits = malloc(sizeof(Lit) * size);
    memcpy(c->lits, lits, sizeof(Lit) * size);
    c->learnt = learnt;
    c->activity = 0.0;
    return S.clauses_size++;
}

/* Watch list index for literal l: we watch on the literal being FALSE. */
static inline int watch_index(Lit l) {
    int v = var_of(l) - 1;
    return 2 * v + sign_of(l);
}

static void watch_add(Lit l, int clause_idx) {
    int idx = watch_index(l);
    WatchNode *n = malloc(sizeof(WatchNode));
    n->clause_idx = clause_idx;
    n->next = S.watches[idx];
    S.watches[idx] = n;
}

/* Attach a newly added clause: watch its first two literals. */
static void attach_clause(int clause_idx) {
    Clause *c = &S.clauses[clause_idx];
    if (c->size == 1) {
        /* unit clause: watch its single literal; fires when that literal becomes false */
        watch_add(c->lits[0], clause_idx);
        return;
    }
    watch_add(c->lits[0], clause_idx);
    watch_add(c->lits[1], clause_idx);
}

/* ---------- Value helpers ---------- */

static inline Value lit_value(Lit l) {
    Value v = S.assign[var_of(l)];
    if (v == UNASSIGNED) return UNASSIGNED;
    if (sign_of(l) == 0) return v;                 /* positive literal */
    return v == TRUE_VAL ? FALSE_VAL : TRUE_VAL;    /* negative literal: flip */
}

/* ---------- Trail / assignment ---------- */

static void new_decision_level(void) {
    if (S.trail_lim_size == 0 || 1) {
        S.trail_lim = realloc(S.trail_lim, sizeof(int) * (S.trail_lim_size + 1));
        S.trail_lim[S.trail_lim_size++] = S.trail_size;
        S.decision_level++;
    }
}

static void enqueue(Lit l, int reason_clause) {
    int v = var_of(l);
    S.assign[v] = sign_of(l) == 0 ? TRUE_VAL : FALSE_VAL;
    S.level[v] = S.decision_level;
    S.reason[v] = reason_clause;
    S.trail[S.trail_size++] = l;
}

/* ---------- VSIDS ---------- */

static void var_bump(int v) {
    S.activity[v] += S.var_inc;
    if (S.activity[v] > 1e100) {
        for (int i = 1; i <= S.num_vars; i++) S.activity[i] *= 1e-100;
        S.var_inc *= 1e-100;
    }
}

static void var_decay_activity(void) {
    /* See header §5: inflating inc is equivalent to decaying every a(v). */
    S.var_inc /= S.var_decay;
}

/* Pick unassigned variable with highest activity (linear scan; fine for a teaching solver). */
static int pick_branch_var(void) {
    int best = -1;
    double best_act = -1.0;
    for (int v = 1; v <= S.num_vars; v++) {
        if (S.assign[v] == UNASSIGNED && S.activity[v] > best_act) {
            best_act = S.activity[v];
            best = v;
        }
    }
    return best;
}

/* ---------- Unit propagation ---------- */

/* ---------- Conflict analysis (First-UIP) ---------- */

static bool *seen;

/* Returns learnt clause literals in out_lits (caller-provided buffer), size in *out_size,
   and backtrack level in *out_level. */
static void analyze(int confl_clause, Lit **out_lits, int *out_size, int *out_level) {
    static Lit *buf = NULL;
    static int buf_cap = 0;
    int buf_size = 0;

    if (!seen) seen = calloc(S.num_vars + 1, sizeof(bool));
    memset(seen, 0, sizeof(bool) * (S.num_vars + 1));

    int counter = 0;
    Lit p = 0;          /* literal currently being resolved (0 = none yet) */
    int idx = S.trail_size - 1;
    int conflict_idx = confl_clause;

    if (buf_cap < S.num_vars + 2) {
        buf_cap = S.num_vars + 2;
        buf = realloc(buf, sizeof(Lit) * buf_cap);
    }
    /* reserve slot 0 for the asserting (UIP) literal, fill later */
    buf_size = 1;

    do {
        Clause *c = &S.clauses[conflict_idx];
        /* bump activity of clause's variables (VSIDS) */
        for (int k = 0; k < c->size; k++) {
            Lit q = c->lits[k];
            int v = var_of(q);
            if (p != 0 && v == var_of(p)) continue; /* skip the literal we're resolving on */
            if (!seen[v] && S.level[v] > 0) {
                seen[v] = true;
                var_bump(v);
                if (S.level[v] >= S.decision_level) {
                    counter++;
                } else {
                    buf[buf_size++] = q;
                    if (buf_size >= buf_cap) {
                        buf_cap *= 2;
                        buf = realloc(buf, sizeof(Lit) * buf_cap);
                    }
                }
            }
        }
        /* find next literal on trail to resolve on (one marked 'seen' at current decision level) */
        while (!seen[var_of(S.trail[idx])]) idx--;
        p = S.trail[idx];
        int pv = var_of(p);
        conflict_idx = S.reason[pv];
        seen[pv] = false;
        counter--;
        idx--;
    } while (counter > 0);

    buf[0] = -p; /* asserting literal (negation of the UIP variable's current value) */
    buf_size = buf_size; /* already tracked */

    /* determine backtrack level = second-highest level among buf[1..] */
    int btlevel = 0;
    for (int i = 1; i < buf_size; i++) {
        int lv = S.level[var_of(buf[i])];
        if (lv > btlevel) btlevel = lv;
    }

    *out_lits = buf;
    *out_size = buf_size;
    *out_level = btlevel;
}

/* ---------- Backtrack ---------- */

extern int g_qhead; /* forward decl, defined below, used to reset propagation queue */

static void cancel_until(int level) {
    if (S.decision_level <= level) return;
    int lim = S.trail_lim[level];
    for (int i = S.trail_size - 1; i >= lim; i--) {
        int v = var_of(S.trail[i]);
        S.assign[v] = UNASSIGNED;
        S.reason[v] = -1;
        S.level[v] = -1;
    }
    S.trail_size = lim;
    S.trail_lim_size = level;
    S.decision_level = level;
    g_qhead = S.trail_size;
}

/* ---------- Main solve loop ---------- */

int g_qhead = 0; /* propagation queue head, global so cancel_until can reset it */

/* redefine propagate to use g_qhead instead of a function-local static */
static int propagate2(void) {
    while (g_qhead < S.trail_size) {
        Lit p = S.trail[g_qhead++];
        int idx = watch_index(-p);
        WatchNode **prev_next = &S.watches[idx];
        WatchNode *w = S.watches[idx];
        while (w != NULL) {
            int ci = w->clause_idx;
            Clause *c = &S.clauses[ci];

            if (c->size == 1) {
                if (lit_value(c->lits[0]) == FALSE_VAL) {
                    return ci;
                }
                prev_next = &w->next;
                w = w->next;
                continue;
            }

            if (c->lits[0] != -p) {
                Lit tmp = c->lits[0]; c->lits[0] = c->lits[1]; c->lits[1] = tmp;
            }
            if (lit_value(c->lits[1]) == TRUE_VAL) {
                prev_next = &w->next;
                w = w->next;
                continue;
            }
            bool found_new = false;
            for (int k = 2; k < c->size; k++) {
                if (lit_value(c->lits[k]) != FALSE_VAL) {
                    Lit tmp = c->lits[0]; c->lits[0] = c->lits[k]; c->lits[k] = tmp;
                    *prev_next = w->next;
                    WatchNode *old = w;
                    w = w->next;
                    watch_add(c->lits[0], ci);
                    free(old);
                    found_new = true;
                    break;
                }
            }
            if (found_new) continue;

            if (lit_value(c->lits[1]) == FALSE_VAL) {
                return ci;
            } else {
                enqueue(c->lits[1], ci);
                prev_next = &w->next;
                w = w->next;
            }
        }
    }
    return -1;
}

/* Luby sequence for restarts — see header §6 for the closed-form recurrence. */
static int luby(int i) {
    int k = 1;
    while ((1 << k) - 1 < i + 1) k++;
    if ((1 << (k - 1)) - 1 == i) return 1 << (k - 2 >= 0 ? k - 2 : 0);
    return luby(i - (1 << (k - 1)) + 1);
}

typedef enum { SAT, UNSAT } Result;

static Result solve(void) {
    int restart_base = 100;
    long conflicts_since_restart = 0;
    int restart_count = 0;

    for (;;) {
        int confl = propagate2();
        if (confl != -1) {
            S.conflicts++;
            conflicts_since_restart++;
            if (S.decision_level == 0) return UNSAT;

            Lit *learnt; int lsize; int btlevel;
            analyze(confl, &learnt, &lsize, &btlevel);
            cancel_until(btlevel);

            int ci = add_clause_storage(learnt, lsize, true);
            attach_clause(ci);
            enqueue(learnt[0], ci);

            var_decay_activity();
        } else {
            /* No conflict: all clauses satisfied so far. Check restart. */
            long limit = (long)restart_base * luby(restart_count);
            if (conflicts_since_restart >= limit) {
                restart_count++;
                conflicts_since_restart = 0;
                S.restarts++;
                cancel_until(0);
                continue;
            }

            int v = pick_branch_var();
            if (v == -1) return SAT; /* all variables assigned, no conflict -> satisfying assignment */

            S.decisions++;
            new_decision_level();
            /* default polarity: try TRUE (i.e., positive literal) first */
            enqueue(v, -1);
        }
    }
}

/* ---------- DIMACS parsing ---------- */

static void ensure_var_capacity(int v) {
    if (v <= S.num_vars) return;
    int old = S.num_vars;
    S.num_vars = v;
    S.assign = realloc(S.assign, sizeof(Value) * (v + 1));
    S.level = realloc(S.level, sizeof(int) * (v + 1));
    S.reason = realloc(S.reason, sizeof(int) * (v + 1));
    S.activity = realloc(S.activity, sizeof(double) * (v + 1));
    for (int i = old + 1; i <= v; i++) {
        S.assign[i] = UNASSIGNED;
        S.level[i] = -1;
        S.reason[i] = -1;
        S.activity[i] = 0.0;
    }

    /* Grow watch-list array to cover the new variables too. */
    int new_len = 2 * (v + 1);
    if (new_len > S.watches_len) {
        int old_len = S.watches_len;
        S.watches = realloc(S.watches, sizeof(WatchNode *) * new_len);
        for (int i = old_len; i < new_len; i++) S.watches[i] = NULL;
        S.watches_len = new_len;
    }
}

static void init_watches(void) {
    /* Start with a small watch array; ensure_var_capacity grows it as variables are seen. */
    S.watches_len = 2;
    S.watches = calloc(S.watches_len, sizeof(WatchNode *));
}

static bool parse_dimacs(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "Cannot open %s\n", path); return false; }

    char line[65536];
    int expected_vars = 0, expected_clauses = 0;
    Lit buf[10000];
    int buf_n = 0;

    while (fgets(line, sizeof(line), f)) {
        if (line[0] == 'c') continue;
        if (line[0] == 'p') {
            sscanf(line, "p cnf %d %d", &expected_vars, &expected_clauses);
            ensure_var_capacity(expected_vars);
            continue;
        }
        char *tok = strtok(line, " \t\n");
        while (tok) {
            int val = atoi(tok);
            if (val == 0) {
                if (buf_n > 0) {
                    for (int i = 0; i < buf_n; i++) ensure_var_capacity(var_of(buf[i]));
                    int ci = add_clause_storage(buf, buf_n, false);
                    attach_clause(ci);
                    buf_n = 0;
                } else {
                    /* Empty clause: formula is trivially unsatisfiable. */
                    g_trivial_unsat = true;
                }
            } else {
                buf[buf_n++] = val;
            }
            tok = strtok(NULL, " \t\n");
        }
    }
    fclose(f);
    return true;
}

/* ---------- Entry point ---------- */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file.cnf>\n", argv[0]);
        return 1;
    }

    S.num_vars = 0;
    S.clauses = NULL; S.clauses_size = 0; S.clauses_cap = 0;
    S.assign = malloc(sizeof(Value)); /* placeholder, resized in ensure_var_capacity */
    S.level = malloc(sizeof(int));
    S.reason = malloc(sizeof(int));
    S.activity = malloc(sizeof(double));
    S.trail = NULL;
    S.trail_size = 0;
    S.trail_lim = NULL;
    S.trail_lim_size = 0;
    S.decision_level = 0;
    S.var_inc = 1.0;
    S.var_decay = 0.95;
    S.conflicts = S.decisions = S.propagations = S.restarts = 0;

    init_watches();
    if (!parse_dimacs(argv[1])) return 1;

    S.trail = malloc(sizeof(Lit) * (S.num_vars + 1));

    Result r = g_trivial_unsat ? UNSAT : solve();

    if (r == SAT) {
        printf("SAT\n");
        for (int v = 1; v <= S.num_vars; v++) {
            printf("%d ", S.assign[v] == TRUE_VAL ? v : -v);
        }
        printf("0\n");
    } else {
        printf("UNSAT\n");
    }
    fprintf(stderr, "conflicts=%ld decisions=%ld restarts=%ld\n", S.conflicts, S.decisions, S.restarts);
    return 0;
}