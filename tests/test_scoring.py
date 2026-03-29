"""Tests for pure scoring functions (spec §9)."""

import pytest

from app.scoring import compute_weighted_final, score_multi_answer, score_single_answer


class TestScoreSingleAnswer:
    def test_correct(self):
        assert score_single_answer(2, 2) == 4.0

    def test_wrong(self):
        assert score_single_answer(1, 2) == 0.0

    def test_zero_index(self):
        assert score_single_answer(0, 0) == 4.0


class TestScoreMultiAnswer:
    def test_all_correct(self):
        assert score_multi_answer([0, 2], [0, 2]) == 4.0

    def test_none_correct(self):
        assert score_multi_answer([1, 3], [0, 2]) == 0.0

    def test_partial_credit(self):
        # 2 out of 3 correct
        score = score_multi_answer([0, 1], [0, 1, 2])
        assert score == pytest.approx(4.0 * 2 / 3)

    def test_one_of_two(self):
        assert score_multi_answer([0], [0, 1]) == pytest.approx(2.0)

    def test_extra_selections_no_penalty(self):
        # Selected [0, 1, 3], correct is [0, 1] — hits=2, no penalty for 3
        assert score_multi_answer([0, 1, 3], [0, 1]) == 4.0

    def test_empty_correct(self):
        assert score_multi_answer([0], []) == 0.0

    def test_empty_selected(self):
        assert score_multi_answer([], [0, 1]) == 0.0

    def test_clamped_to_max(self):
        # Even with duplicates in selected, can't exceed 4.0
        assert score_multi_answer([0, 0, 0], [0]) <= 4.0


class TestComputeWeightedFinal:
    def test_empty_scores(self):
        score, pct = compute_weighted_final([])
        assert score == 0.0
        assert pct == 0.0

    def test_all_perfect(self):
        scores = [4.0, 4.0, 4.0]
        final, pct = compute_weighted_final(scores)
        assert final == pytest.approx(4.0)
        assert pct == pytest.approx(100.0)

    def test_all_zero(self):
        scores = [0.0, 0.0, 0.0]
        final, pct = compute_weighted_final(scores)
        assert final == pytest.approx(0.0)
        assert pct == pytest.approx(0.0)

    def test_known_values(self):
        # 3 questions: scores [4, 0, 4]
        # weights: w0=1.0, w1=1.1, w2=1.21
        # weighted = 1.0*4 + 1.1*0 + 1.21*4 = 4 + 0 + 4.84 = 8.84
        # total_weight = 1.0 + 1.1 + 1.21 = 3.31
        # final = 8.84 / 3.31 = 2.6706...
        # pct = 2.6706 / 4 * 100 = 66.767...
        scores = [4.0, 0.0, 4.0]
        final, pct = compute_weighted_final(scores)
        assert final == pytest.approx(8.84 / 3.31)
        assert pct == pytest.approx((8.84 / 3.31) / 4.0 * 100.0)

    def test_later_questions_weighted_more(self):
        # Score only on last question should yield higher final than score only on first
        scores_first = [4.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        scores_last = [0.0, 0.0, 0.0, 0.0, 0.0, 4.0]
        final_first, _ = compute_weighted_final(scores_first)
        final_last, _ = compute_weighted_final(scores_last)
        assert final_last > final_first

    def test_single_question(self):
        final, pct = compute_weighted_final([3.0])
        assert final == pytest.approx(3.0)
        assert pct == pytest.approx(75.0)
