"""Tests for the Hotspot Score engine. No Git repository is needed here."""

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from git_detective.core.hotspots import (
    DEFAULT_WEIGHTS,
    HotspotWeights,
    compute_hotspots,
    count_recent_commits,
    normalize_log,
)
from git_detective.models import CommitInfo, FileChange

REFERENCE = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)
OLD = REFERENCE - timedelta(days=180)
RECENT = REFERENCE - timedelta(days=5)

_sha_counter = 0


def commit(when: datetime, *changes: tuple[str, int, int]) -> CommitInfo:
    """Build a commit touching ``(path, added, deleted)`` files at ``when``."""
    global _sha_counter
    _sha_counter += 1
    return CommitInfo(
        hexsha=f"{_sha_counter:040x}",
        author_name="Alice",
        author_email="alice@example.com",
        authored_at=when,
        message="msg",
        files=tuple(
            FileChange(path, added, deleted) for path, added, deleted in changes
        ),
    )


def scores(commits: list[CommitInfo], **kwargs: object) -> dict[str, float]:
    ranked = compute_hotspots(commits, reference_time=REFERENCE, **kwargs)
    return {h.path: h.score for h in ranked}


class TestNormalizeLog:
    def test_empty_input(self) -> None:
        assert normalize_log([]) == []

    def test_all_zeros_stay_zero(self) -> None:
        assert normalize_log([0, 0, 0]) == [0.0, 0.0, 0.0]

    def test_max_maps_to_one_and_zero_to_zero(self) -> None:
        result = normalize_log([0, 7, 42])

        assert result[0] == 0.0
        assert result[2] == 1.0

    def test_known_values(self) -> None:
        # log1p(1) / log1p(3) == log(2) / log(4) == 0.5
        assert normalize_log([0, 1, 3]) == pytest.approx([0.0, 0.5, 1.0])

    def test_single_value(self) -> None:
        assert normalize_log([5]) == [1.0]

    def test_is_monotonic(self) -> None:
        result = normalize_log([1, 2, 5, 10, 100, 1000])

        assert result == sorted(result)

    def test_dampens_large_outliers(self) -> None:
        small, _ = normalize_log([100, 10_000])

        linear = 100 / 10_000
        assert small == pytest.approx(0.501, abs=1e-3)
        assert small > 50 * linear

    def test_rejects_negative_values(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            normalize_log([1, -1])


class TestHotspotWeights:
    def test_defaults_sum_to_one(self) -> None:
        w = DEFAULT_WEIGHTS
        assert (w.frequency, w.churn, w.recency) == (0.45, 0.35, 0.20)
        assert math.isclose(w.frequency + w.churn + w.recency, 1.0)

    def test_tolerates_float_rounding(self) -> None:
        HotspotWeights(frequency=0.1, churn=0.2, recency=0.7)

    @pytest.mark.parametrize(
        ("frequency", "churn", "recency"),
        [(0.5, 0.5, 0.5), (0.2, 0.2, 0.2), (0.0, 0.0, 0.0)],
    )
    def test_rejects_weights_not_summing_to_one(
        self, frequency: float, churn: float, recency: float
    ) -> None:
        with pytest.raises(ValueError, match="sum to 1"):
            HotspotWeights(frequency, churn, recency)

    def test_rejects_negative_weight(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            HotspotWeights(frequency=1.2, churn=-0.2, recency=0.0)


class TestComputeHotspots:
    def test_empty_dataset(self) -> None:
        assert compute_hotspots([], reference_time=REFERENCE) == []

    def test_commit_without_files(self) -> None:
        assert compute_hotspots([commit(RECENT)], reference_time=REFERENCE) == []

    def test_single_recently_active_file_scores_100(self) -> None:
        (hotspot,) = compute_hotspots(
            [commit(RECENT, ("app.py", 3, 1))], reference_time=REFERENCE
        )

        assert hotspot.path == "app.py"
        assert (hotspot.commits, hotspot.churn, hotspot.recent_commits) == (1, 4, 1)
        assert hotspot.score == 100.0

    def test_single_inactive_file_scores_without_recency(self) -> None:
        (hotspot,) = compute_hotspots(
            [commit(OLD, ("app.py", 3, 1))], reference_time=REFERENCE
        )

        # F = C = 1, R = 0  ->  100 * (0.45 + 0.35)
        assert hotspot.recent_commits == 0
        assert hotspot.score == pytest.approx(80.0)

    def test_score_matches_formula(self) -> None:
        commits = [
            commit(OLD, ("a.py", 10, 0), ("b.py", 1, 0)),
            commit(RECENT, ("a.py", 5, 5)),
        ]
        # a.py: commits=2, churn=20, recent=1 -> the maximum of every metric
        # b.py: commits=1, churn=1,  recent=0
        expected_b = 100 * (
            0.45 * math.log1p(1) / math.log1p(2)
            + 0.35 * math.log1p(1) / math.log1p(20)
            + 0.20 * 0.0
        )

        result = scores(commits)

        assert result["a.py"] == pytest.approx(100.0)
        assert result["b.py"] == pytest.approx(expected_b)

    @pytest.mark.parametrize(
        "commits",
        [
            [commit(OLD, ("a", 0, 0))],
            [commit(RECENT, ("a", 50_000, 0), ("b", 1, 0))],
            [commit(OLD, ("a", 1, 1)), commit(RECENT, ("b", 1, 1), ("c", 9, 9))],
            [commit(RECENT, (f"f{i}", i * 10, i)) for i in range(20)],
        ],
    )
    def test_scores_are_within_0_and_100(self, commits: list[CommitInfo]) -> None:
        for hotspot in compute_hotspots(commits, reference_time=REFERENCE):
            assert 0.0 <= hotspot.score <= 100.0

    def test_zero_churn_file_still_scores_on_frequency(self) -> None:
        commits = [commit(OLD, ("logo.png", 0, 0), ("app.py", 10, 0))]

        result = scores(commits)

        # Same frequency, but the binary file gets no churn component.
        assert result["logo.png"] == pytest.approx(45.0)
        assert result["app.py"] == pytest.approx(80.0)

    def test_all_files_with_zero_churn(self) -> None:
        commits = [commit(RECENT, ("a.png", 0, 0), ("b.png", 0, 0))]

        result = scores(commits)

        # C = 0 for every file, no division by zero: 100 * (0.45 + 0.20)
        assert result == {"a.png": pytest.approx(65.0), "b.png": pytest.approx(65.0)}

    def test_no_recent_activity_caps_scores(self) -> None:
        commits = [commit(OLD, ("a.py", 5, 5)), commit(OLD, ("b.py", 1, 0))]

        assert max(scores(commits).values()) == pytest.approx(80.0)

    def test_log_normalization_limits_outlier_dominance(self) -> None:
        commits = [commit(OLD, ("generated.json", 50_000, 0), ("app.py", 100, 0))]

        churn_component = scores(commits)["app.py"] - 45.0

        # Linear normalization would give 35 * 100 / 50_000 = 0.07 points.
        assert churn_component > 10.0


class TestWeights:
    @pytest.fixture
    def commits(self) -> list[CommitInfo]:
        return [
            # often.py: most commits, all old, small churn
            *[commit(OLD, ("often.py", 1, 0)) for _ in range(5)],
            # big.py: one old commit with huge churn
            commit(OLD, ("big.py", 1000, 0)),
            # fresh.py: only recent commits
            commit(RECENT, ("fresh.py", 1, 0)),
            commit(RECENT, ("fresh.py", 1, 0)),
        ]

    @pytest.mark.parametrize(
        ("weights", "leader"),
        [
            (HotspotWeights(frequency=1.0, churn=0.0, recency=0.0), "often.py"),
            (HotspotWeights(frequency=0.0, churn=1.0, recency=0.0), "big.py"),
            (HotspotWeights(frequency=0.0, churn=0.0, recency=1.0), "fresh.py"),
        ],
    )
    def test_weights_change_the_leader(
        self, commits: list[CommitInfo], weights: HotspotWeights, leader: str
    ) -> None:
        ranked = compute_hotspots(commits, reference_time=REFERENCE, weights=weights)

        assert ranked[0].path == leader
        assert ranked[0].score == pytest.approx(100.0)

    def test_zero_weight_ignores_metric(self, commits: list[CommitInfo]) -> None:
        weights = HotspotWeights(frequency=0.5, churn=0.5, recency=0.0)

        result = scores(commits, weights=weights)

        assert scores(commits, weights=weights, recent_days=365) == result


class TestRecentActivity:
    def test_window_bounds_are_inclusive(self) -> None:
        cutoff = REFERENCE - timedelta(days=30)
        commits = [
            commit(cutoff, ("at_cutoff.py", 1, 0)),
            commit(cutoff - timedelta(seconds=1), ("before.py", 1, 0)),
            commit(REFERENCE, ("at_reference.py", 1, 0)),
            commit(REFERENCE + timedelta(seconds=1), ("future.py", 1, 0)),
        ]

        counts = count_recent_commits(commits, since=cutoff, until=REFERENCE)

        assert counts == {"at_cutoff.py": 1, "at_reference.py": 1}

    def test_counts_only_recent_commits_per_file(self) -> None:
        commits = [
            commit(OLD, ("a.py", 1, 0)),
            commit(RECENT, ("a.py", 1, 0), ("b.py", 1, 0)),
            commit(RECENT, ("a.py", 1, 0)),
        ]

        ranked = compute_hotspots(commits, reference_time=REFERENCE)

        recent = {h.path: h.recent_commits for h in ranked}
        assert recent == {"a.py": 2, "b.py": 1}

    def test_recent_days_controls_window(self) -> None:
        commits = [commit(REFERENCE - timedelta(days=45), ("a.py", 1, 0))]

        (narrow,) = compute_hotspots(commits, reference_time=REFERENCE, recent_days=30)
        (wide,) = compute_hotspots(commits, reference_time=REFERENCE, recent_days=60)

        assert narrow.recent_commits == 0
        assert wide.recent_commits == 1
        assert wide.score > narrow.score

    def test_works_with_non_utc_timezones(self) -> None:
        warsaw_summer = timezone(timedelta(hours=2))
        when = (REFERENCE - timedelta(hours=1)).astimezone(warsaw_summer)

        (hotspot,) = compute_hotspots(
            [commit(when, ("a.py", 1, 0))], reference_time=REFERENCE, recent_days=1
        )

        assert hotspot.recent_commits == 1

    def test_rejects_naive_reference_time(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            compute_hotspots([], reference_time=datetime(2024, 6, 30))

    @pytest.mark.parametrize("days", [0, -5])
    def test_rejects_non_positive_recent_days(self, days: int) -> None:
        with pytest.raises(ValueError, match="recent_days"):
            compute_hotspots([], reference_time=REFERENCE, recent_days=days)


class TestDeterministicOrder:
    def test_sorted_by_score_descending(self) -> None:
        commits = [
            commit(OLD, ("low.py", 1, 0)),
            commit(RECENT, ("high.py", 50, 0)),
            commit(RECENT, ("high.py", 50, 0)),
        ]

        ranked = compute_hotspots(commits, reference_time=REFERENCE)

        assert [h.path for h in ranked] == ["high.py", "low.py"]
        assert ranked[0].score > ranked[1].score

    def test_ties_are_broken_alphabetically(self) -> None:
        commits = [commit(RECENT, ("c.py", 1, 0), ("a.py", 1, 0), ("b.py", 1, 0))]

        ranked = compute_hotspots(commits, reference_time=REFERENCE)

        assert [h.path for h in ranked] == ["a.py", "b.py", "c.py"]
        assert len({h.score for h in ranked}) == 1

    def test_input_order_does_not_matter(self) -> None:
        commits = [
            commit(OLD, ("a.py", 3, 0), ("b.py", 3, 0)),
            commit(RECENT, ("c.py", 1, 1)),
            commit(RECENT, ("a.py", 2, 0), ("d.py", 2, 0)),
        ]

        forward = compute_hotspots(commits, reference_time=REFERENCE)
        backward = compute_hotspots(commits[::-1], reference_time=REFERENCE)

        assert forward == backward
