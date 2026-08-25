"""Diversity measurement and duplicate refusal.

A population that converges to thirty near-identical genomes has stopped searching, but
it still produces a leaderboard, a top decile and a stream of children — so it looks
healthy right up until it explains nothing. These measures make homogeneity observable
before that happens.

Nothing here is novelty *search*: no bonus steers selection toward strangeness. The
controls are a floor (a proposal too close to something already in the population is
refused) and a report (the Control Tower warns when concentration climbs). That is
deliberately the minimum — a novelty pressure tuned before we can measure whether the
loop works at all would be untestable.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import genome as genome_mod


@dataclass
class DiversityReport:
    n: int
    mean_pairwise_distance: float
    min_pairwise_distance: float
    distinct_genomes: int
    family_shares: dict[str, float]
    parent_shares: dict[str, float]
    top_family: str | None
    top_family_share: float
    top_parent_share: float
    warnings: list[str]

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean_pairwise_distance": self.mean_pairwise_distance,
            "min_pairwise_distance": self.min_pairwise_distance,
            "distinct_genomes": self.distinct_genomes,
            "family_shares": self.family_shares,
            "parent_shares": self.parent_shares,
            "top_family": self.top_family,
            "top_family_share": self.top_family_share,
            "top_parent_share": self.top_parent_share,
            "warnings": list(self.warnings),
        }


# Thresholds at which the Control Tower starts warning. Deliberately loose: they exist
# to catch collapse, not to police a healthy population's drift toward what works.
COLLAPSE_MEAN_DISTANCE = 0.05
CONCENTRATION_FAMILY_SHARE = 0.70
CONCENTRATION_PARENT_SHARE = 0.50


def measure(
    members: list[dict],
    *,
    collapse_mean_distance: float = COLLAPSE_MEAN_DISTANCE,
    family_share_limit: float = CONCENTRATION_FAMILY_SHARE,
    parent_share_limit: float = CONCENTRATION_PARENT_SHARE,
) -> DiversityReport:
    """Measure a population. Each member is `{document, family, parent_uuid, hash}`."""
    n = len(members)
    if n == 0:
        return DiversityReport(0, 0.0, 0.0, 0, {}, {}, None, 0.0, 0.0, [])

    docs = [m.get("document") or {} for m in members]
    hashes = {str(m.get("hash")) for m in members if m.get("hash")}

    distances: list[float] = []
    min_d = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            d = genome_mod.distance(docs[i], docs[j])
            distances.append(d)
            min_d = min(min_d, d)
    mean_d = round(sum(distances) / len(distances), 6) if distances else 0.0

    fam_counts: dict[str, int] = {}
    par_counts: dict[str, int] = {}
    for m in members:
        fam = str(m.get("family") or "unassigned")
        fam_counts[fam] = fam_counts.get(fam, 0) + 1
        parent = m.get("parent_uuid")
        if parent:
            par_counts[str(parent)] = par_counts.get(str(parent), 0) + 1

    fam_shares = {k: round(v / n, 4) for k, v in sorted(fam_counts.items())}
    par_shares = {k: round(v / n, 4) for k, v in sorted(par_counts.items())}
    top_family, top_family_share = (
        max(fam_shares.items(), key=lambda kv: kv[1]) if fam_shares else (None, 0.0)
    )
    top_parent_share = max(par_shares.values()) if par_shares else 0.0

    warnings: list[str] = []
    if n > 2 and mean_d < collapse_mean_distance:
        warnings.append(
            f"diversity collapsing: mean pairwise genome distance {mean_d:.3f} is below "
            f"{collapse_mean_distance:.3f}"
        )
    if len(hashes) < n:
        warnings.append(
            f"only {len(hashes)} distinct genomes across {n} members — duplicates are in "
            "the population"
        )
    if top_family and top_family_share >= family_share_limit:
        warnings.append(
            f"strategy-family concentration: {top_family_share:.0%} of the cohort is "
            f"{top_family!r}"
        )
    if top_parent_share >= parent_share_limit:
        warnings.append(
            f"parent concentration: one parent accounts for {top_parent_share:.0%} of the "
            "cohort"
        )

    return DiversityReport(
        n=n,
        mean_pairwise_distance=mean_d,
        min_pairwise_distance=round(min_d, 6),
        distinct_genomes=len(hashes),
        family_shares=fam_shares,
        parent_shares=par_shares,
        top_family=top_family,
        top_family_share=round(float(top_family_share), 4),
        top_parent_share=round(float(top_parent_share), 4),
        warnings=warnings,
    )


def novelty_check(
    document: dict,
    existing: list[dict],
    *,
    min_distance: float,
) -> tuple[bool, float, str | None]:
    """Is this genome new enough to admit? Returns (ok, nearest_distance, reason).

    Two separate refusals. An exact hash match is a duplicate: the same strategy already
    exists and a second copy adds no information. A distance below the floor is a
    near-duplicate: distinct on paper, but not different enough for the difference in
    its results to be attributable to the change rather than to noise — which would make
    the mutation unfalsifiable."""
    if not existing:
        return True, 1.0, None
    target_hash = genome_mod.genome_hash(document)
    for other in existing:
        if genome_mod.genome_hash(other) == target_hash:
            return False, 0.0, "identical to a genome already in the population"
    nearest_d, _ = genome_mod.nearest(document, existing)
    if nearest_d < min_distance:
        return (
            False,
            nearest_d,
            f"distance {nearest_d:.4f} to the nearest existing genome is below the "
            f"program floor of {min_distance:.4f}",
        )
    return True, nearest_d, None


__all__ = [
    "COLLAPSE_MEAN_DISTANCE",
    "CONCENTRATION_FAMILY_SHARE",
    "CONCENTRATION_PARENT_SHARE",
    "DiversityReport",
    "measure",
    "novelty_check",
]
