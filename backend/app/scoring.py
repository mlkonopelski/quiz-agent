"""Pure deterministic scoring functions (spec §9).

These are workflow-safe: no I/O, no randomness, no datetime.
"""


def score_single_answer(selected: int, correct: int) -> float:
    """Score a single-answer question: 4.0 if correct, 0.0 otherwise."""
    return 4.0 if selected == correct else 0.0


def score_multi_answer(selected: list[int], correct: list[int]) -> float:
    """Score a multi-answer question: proportional partial credit.

    No false-positive penalty per spec:
    > partial credit proportional to correctly selected answers
    """
    if not correct:
        return 0.0
    hits = len(set(selected) & set(correct))
    score = 4.0 * hits / len(correct)
    return min(4.0, max(0.0, score))


def compute_weighted_final(scores: list[float]) -> tuple[float, float]:
    """Compute geometric weighted final score.

    Returns (final_score, final_score_pct).
    Weights: w_i = 1.0 * 1.1^i for zero-based index i.
    """
    if not scores:
        return 0.0, 0.0

    total_weighted = 0.0
    total_weight = 0.0
    for i, score in enumerate(scores):
        weight = 1.0 * (1.1**i)
        total_weighted += weight * score
        total_weight += weight

    final_score = total_weighted / total_weight
    final_score_pct = (final_score / 4.0) * 100.0
    return final_score, final_score_pct
