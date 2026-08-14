"""P4.1 — JosephsonMetrics + Tier-1 analytic estimates."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from siscforge.cli.main import app
from siscforge.export import CSV_FIELDNAMES, write_evaluations_csv, write_synthesis_cards
from siscforge.josephson import (
    BCS_GAP_RATIO,
    KB_MEV_PER_K,
    RANKING_ONLY_CAVEAT,
    ambegaokar_baratoff_icrn_mV,
    attach_josephson_metrics,
    bcs_gap_meV,
    estimate_tier1,
    extract_gap,
    jc_proxy_A_per_cm2,
    josephson_is_enabled,
    resolve_tc_K,
    switching_energy_eV,
)
from siscforge.models import (
    CampaignConfig,
    CandidateEvaluation,
    DMFTResult,
    ElectronPhononResult,
    JosephsonConfig,
    JosephsonMetrics,
    SiFeasibilityScore,
    StructureCandidate,
)
from siscforge.ranking import rank_evaluations

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "nbn_mgb2_josephson_tier1.yaml"
DUMMY = ROOT / "examples" / "dummy_campaign.yaml"

# Experimental-scale bands used for order-of-magnitude checks (factor 3).
# Δ and IcRn are typical low-T tunnel-junction / tunneling scales.
_SANITY = {
    "Nb": {
        "tc": 9.25,
        "gap_lo": 1.0,
        "gap_hi": 4.5,  # 1.5 meV × 3
        "icrn_lo": 0.7,
        "icrn_hi": 7.5,
        "jc_lo": 1.0e3,
        "jc_hi": 1.0e5,
    },
    "NbN": {
        "tc": 16.0,
        "gap_lo": 1.5,
        "gap_hi": 9.0,
        "icrn_lo": 1.0,
        "icrn_hi": 12.0,
        "jc_lo": 2.0e3,
        "jc_hi": 1.5e5,
    },
    "MgB2": {
        "tc": 39.0,
        "gap_lo": 2.0,  # π-gap lower bound
        "gap_hi": 18.0,  # σ-gap × ~2.5
        "icrn_lo": 2.0,
        "icrn_hi": 28.0,
        "jc_lo": 3.0e3,
        "jc_hi": 3.0e5,
    },
}


def _eph(
    *,
    tc: float = 16.0,
    gap_meV: float | None = None,
    status: str = "ok",
    quality_tag: str = "screening",
    summary: dict | None = None,
    raw: dict | None = None,
) -> ElectronPhononResult:
    return ElectronPhononResult(
        lambda_total=1.1,
        omega_log=250.0,
        mu_star=0.1,
        Tc_allen_dynes=tc,
        Tc_eliashberg=tc,
        gap_meV=gap_meV,
        converged=True,
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
        alpha2F_summary=summary or {},
        raw=raw or {},
    )


def _ev(
    *,
    formula: str = "NbN",
    family: str = "tm_nitride",
    eph: ElectronPhononResult | None = None,
    dmft: DMFTResult | None = None,
    tc: float | None = None,
    source: str | None = None,
    rank: int | None = None,
    cid: str | None = None,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=StructureCandidate(
            formula=formula,
            material_family=family,  # type: ignore[arg-type]
            candidate_id=cid or f"id-{formula}",
        ),
        electron_phonon=eph,
        dmft=dmft,
        si_feasibility=SiFeasibilityScore(total=55.0),
        performance_score=tc,
        performance_score_source=source,
        rank=rank,
        status="ok" if eph is not None else "pending",
    )


# ---------------------------------------------------------------------------
# Pure analytics
# ---------------------------------------------------------------------------


def test_bcs_gap_matches_weak_coupling_constant() -> None:
    gap = bcs_gap_meV(9.25)
    expected = BCS_GAP_RATIO * KB_MEV_PER_K * 9.25
    assert gap == pytest.approx(expected)
    assert 1.3 < gap < 1.6


def test_ab_icrn_t0_is_pi_over_two_times_gap() -> None:
    gap = 1.5
    icrn = ambegaokar_baratoff_icrn_mV(gap)
    assert icrn == pytest.approx((math.pi / 2.0) * gap)
    # finite T reduces IcRn
    warm = ambegaokar_baratoff_icrn_mV(gap, temperature_K=4.2)
    assert 0.0 < warm < icrn


def test_temperature_at_or_above_tc_zeros_transport() -> None:
    """Fixed-Δ AB stays finite above Tc; estimate_tier1 must zero proxies."""
    ev = _ev(eph=_eph(tc=16.0), tc=16.0, source="epw")
    cold = estimate_tier1(ev, JosephsonConfig(enabled=True, temperature_K=4.2))
    at_tc = estimate_tier1(ev, JosephsonConfig(enabled=True, temperature_K=16.0))
    above = estimate_tier1(ev, JosephsonConfig(enabled=True, temperature_K=20.0))
    default_t0 = estimate_tier1(ev, JosephsonConfig(enabled=True, temperature_K=None))

    assert cold.status == "ok"
    assert cold.icrn_mV is not None and cold.icrn_mV > 0.0
    assert cold.jc_A_per_cm2 is not None and cold.jc_A_per_cm2 > 0.0
    assert "t_ge_tc_transport_zeroed" not in cold.formula_tags

    for metrics in (at_tc, above):
        assert metrics.status == "ok"
        assert metrics.icrn_mV == 0.0
        assert metrics.jc_A_per_cm2 == 0.0
        assert metrics.switching_energy_eV == 0.0
        assert metrics.ej_K == 0.0
        assert metrics.ic_uA == 0.0
        assert metrics.gap_meV == pytest.approx(cold.gap_meV)
        assert "t_ge_tc_transport_zeroed" in metrics.formula_tags
        assert "T-independent" in metrics.notes
        assert metrics.raw.get("t_ge_tc") is True

    # Bare AB formula is unchanged (still finite); the guard is in estimate_tier1.
    bare = ambegaokar_baratoff_icrn_mV(cold.gap_meV or 0.0, temperature_K=20.0)
    assert bare > 0.0
    assert default_t0.icrn_mV == pytest.approx((math.pi / 2.0) * (default_t0.gap_meV or 0.0))


def test_jc_and_ej_are_positive_and_area_consistent() -> None:
    icrn = ambegaokar_baratoff_icrn_mV(1.5)
    jc = jc_proxy_A_per_cm2(icrn, rna_ohm_um2=20.0)
    ej, ej_k, ic_ua = switching_energy_eV(jc, reference_area_um2=1.0)
    assert jc > 0.0
    assert ej > 0.0
    assert ej_k == pytest.approx(ej / (KB_MEV_PER_K * 1e-3), rel=1e-6)
    # double the area → double Ic, same Jc
    _, _, ic2 = switching_energy_eV(jc, reference_area_um2=2.0)
    assert ic2 == pytest.approx(2.0 * ic_ua)


@pytest.mark.parametrize("name", ["Nb", "NbN", "MgB2"])
def test_literature_tc_order_of_magnitude(name: str) -> None:
    """BCS + AB from literature Tc lands within a loose factor ~2–3 of experiment."""
    spec = _SANITY[name]
    family = {
        "Nb": "other",
        "NbN": "tm_nitride",
        "MgB2": "mgb2_boride",
    }[name]
    ev = _ev(
        formula=name,
        family=family,
        eph=_eph(tc=spec["tc"]),
        tc=spec["tc"],
        source="epw",
    )
    metrics = estimate_tier1(ev, JosephsonConfig(enabled=True))
    assert metrics.approximate is True
    assert metrics.status == "ok"
    assert metrics.gap_source == "bcs_from_tc"
    assert spec["gap_lo"] <= metrics.gap_meV <= spec["gap_hi"]
    assert spec["icrn_lo"] <= metrics.icrn_mV <= spec["icrn_hi"]
    assert spec["jc_lo"] <= metrics.jc_A_per_cm2 <= spec["jc_hi"]
    assert RANKING_ONLY_CAVEAT.split("—")[0].strip() in metrics.notes
    assert "approximate" in metrics.notes.lower()


def test_explicit_eliashberg_gap_beats_bcs() -> None:
    ev = _ev(eph=_eph(tc=16.0, gap_meV=2.9), tc=16.0, source="epw")
    got = extract_gap(ev)
    assert got.usable is True
    assert got.gap_meV == pytest.approx(2.9)
    assert got.source == "eliashberg"
    metrics = estimate_tier1(ev)
    assert metrics.gap_meV == pytest.approx(2.9)
    assert metrics.gap_source == "eliashberg"
    assert "bcs_gap_fallback" not in metrics.formula_tags


def test_gap_from_alpha2f_summary() -> None:
    ev = _ev(eph=_eph(tc=16.0, summary={"eliashberg_gap_meV": 2.7}))
    got = extract_gap(ev)
    assert got.gap_meV == pytest.approx(2.7)
    assert got.source == "eliashberg"


def test_gap_eV_key_is_converted() -> None:
    ev = _ev(eph=_eph(tc=16.0, raw={"gap_eV": 0.0025}))
    got = extract_gap(ev)
    assert got.gap_meV == pytest.approx(2.5)


def test_dmft_performance_score_is_not_a_gap() -> None:
    ev = _ev(
        formula="NdNiO2",
        family="nickelate",
        eph=None,
        dmft=DMFTResult(
            status="ok",
            converged=True,
            leading_pairing_eigenvalue=1.0,
            solver="solid_dmft",
        ),
        tc=25.0,
        source="dmft_pairing",
    )
    tc, src = resolve_tc_K(ev)
    assert tc is None
    assert src is None
    got = extract_gap(ev)
    assert got.usable is False
    metrics = estimate_tier1(ev)
    assert metrics.status == "skipped"
    assert metrics.gap_meV is None
    assert metrics.icrn_mV is None


def test_missing_inputs_skip_without_raising() -> None:
    ev = _ev(eph=None, tc=None, source=None)
    metrics = estimate_tier1(ev)
    assert metrics.status == "skipped"
    assert metrics.approximate is True
    assert metrics.gap_meV is None


def test_family_gap_ratio_override() -> None:
    ev = _ev(eph=_eph(tc=16.0), family="tm_nitride")
    cfg = JosephsonConfig(enabled=True, family_gap_ratios={"tm_nitride": 2.05})
    metrics = estimate_tier1(ev, cfg)
    assert metrics.gap_meV == pytest.approx(bcs_gap_meV(16.0, ratio=2.05))


def test_approximate_cannot_be_cleared() -> None:
    m = JosephsonMetrics(approximate=False, status="ok", gap_meV=1.5)
    assert m.approximate is True


# ---------------------------------------------------------------------------
# Attachment / inert default
# ---------------------------------------------------------------------------


def test_disabled_is_identity() -> None:
    evs = [
        _ev(formula="NbN", eph=_eph(tc=16.0), rank=1),
        _ev(formula="MgB2", family="mgb2_boride", eph=_eph(tc=39.0), rank=2),
    ]
    cfg = JosephsonConfig(enabled=False)
    assert josephson_is_enabled(cfg) is False
    out = attach_josephson_metrics(evs, cfg)
    assert out is evs
    assert all(ev.josephson is None for ev in out)


def test_default_campaign_is_inert() -> None:
    cfg = CampaignConfig(name="p41-default")
    assert cfg.josephson.enabled is False
    evs = rank_evaluations([_ev(eph=_eph(tc=16.0), tc=16.0, source="epw")])
    out = attach_josephson_metrics(evs, cfg.josephson)
    assert out[0].josephson is None


def test_enabled_populates_top_n_only() -> None:
    evs = [
        _ev(formula="A", eph=_eph(tc=20.0), tc=20.0, source="epw", rank=1, cid="a"),
        _ev(formula="B", eph=_eph(tc=15.0), tc=15.0, source="epw", rank=2, cid="b"),
        _ev(formula="C", eph=_eph(tc=10.0), tc=10.0, source="epw", rank=3, cid="c"),
    ]
    cfg = JosephsonConfig(enabled=True, shortlist_only=True, shortlist_size=2)
    out = attach_josephson_metrics(evs, cfg)
    assert out[0].josephson is not None and out[0].josephson.status == "ok"
    assert out[1].josephson is not None and out[1].josephson.status == "ok"
    assert out[2].josephson is None


def test_enabled_without_rank_skips_shortlist_gate(caplog: pytest.LogCaptureFixture) -> None:
    """Unranked rows are left unset when shortlist_only requires a rank."""
    ev = _ev(eph=_eph(tc=16.0), rank=None)
    with caplog.at_level("WARNING", logger="siscforge.josephson.attach"):
        out = attach_josephson_metrics([ev], JosephsonConfig(enabled=True, shortlist_size=5))
    assert out[0].josephson is None
    assert any("rank is missing" in rec.message for rec in caplog.records)


def test_shortlist_only_false_annotates_unranked() -> None:
    ev = _ev(eph=_eph(tc=16.0), rank=None)
    cfg = JosephsonConfig(enabled=True, shortlist_only=False)
    out = attach_josephson_metrics([ev], cfg)
    assert out[0].josephson is not None
    assert out[0].josephson.status == "ok"


def test_attach_never_raises_on_garbage() -> None:
    ev = _ev(eph=_eph(tc=16.0), rank=1)
    # Force a bad config object that still looks enabled
    class _Boom:
        enabled = True
        shortlist_only = False
        shortlist_size = 20

        def __getattribute__(self, name: str):
            if name in {"enabled", "shortlist_only", "shortlist_size"}:
                return object.__getattribute__(self, name)
            raise RuntimeError("boom")

    # estimate_tier1 uses JosephsonConfig defaults when knobs explode? The
    # attach helper must swallow unexpected exceptions.
    out = attach_josephson_metrics([ev], _Boom())  # type: ignore[arg-type]
    assert len(out) == 1
    skipped = out[0].josephson
    assert skipped is not None
    assert skipped.status == "skipped"
    assert skipped.raw.get("reason") == "attach_failed"
    assert "boom" in str(skipped.raw.get("error", "")).lower()
    assert "boom" in skipped.notes.lower()


def test_ranker_has_no_josephson_fork() -> None:
    """Enabling Josephson must not change composite order / Pareto."""
    a = _ev(formula="hiTc", eph=_eph(tc=30.0), tc=30.0, source="epw", cid="hi")
    b = _ev(formula="loTc", eph=_eph(tc=8.0), tc=8.0, source="epw", cid="lo")
    a.si_feasibility = SiFeasibilityScore(total=40.0)
    b.si_feasibility = SiFeasibilityScore(total=80.0)
    plain = rank_evaluations([a, b])
    annotated = attach_josephson_metrics(
        [e.model_copy() for e in plain],
        JosephsonConfig(enabled=True, shortlist_only=False),
    )
    # Re-rank after attach — order and scores stay the same.
    again = rank_evaluations(annotated)
    assert [e.candidate.candidate_id for e in again] == [
        e.candidate.candidate_id for e in plain
    ]
    assert [e.composite_score for e in again] == [e.composite_score for e in plain]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_csv_and_cards_are_caveated(tmp_path: Path) -> None:
    ev = _ev(
        formula="NbN",
        eph=_eph(tc=16.0),
        tc=16.0,
        source="epw",
        rank=1,
    )
    ev = attach_josephson_metrics([ev], JosephsonConfig(enabled=True, shortlist_only=False))[0]
    ranked = rank_evaluations([ev])
    ranked = attach_josephson_metrics(
        ranked, JosephsonConfig(enabled=True, shortlist_only=False)
    )
    csv_path = write_evaluations_csv(ranked, tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    for col in (
        "josephson_approximate",
        "josephson_gap_meV",
        "josephson_icrn_mV",
        "josephson_jc_A_per_cm2",
        "josephson_switching_energy_eV",
        "josephson_notes",
    ):
        assert col in header
        assert col in CSV_FIELDNAMES
    body = csv_path.read_text()
    assert "True" in body or "true" in body.lower()
    assert "RANKING ONLY" in body or "approximate" in body.lower()

    cards = write_synthesis_cards(ranked, tmp_path / "cards.md", campaign_name="p41")
    text = cards.read_text()
    assert "approximate / ranking only" in text.lower()
    assert "Ambegaokar–Baratoff" in text or "Ambegaokar" in text
    assert "Josephson metrics" in text
    assert "Not** a device-design" in text or "not a device-design" in text.lower()


def test_disabled_export_has_empty_josephson_columns(tmp_path: Path) -> None:
    ev = rank_evaluations([_ev(eph=_eph(tc=16.0), tc=16.0, source="epw")])[0]
    assert ev.josephson is None
    csv_path = write_evaluations_csv([ev], tmp_path / "out.csv")
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    idx = header.index("josephson_gap_meV")
    # empty cell for the data row
    cols = rows[1].split(",")
    assert cols[idx] == ""
    cards = write_synthesis_cards([ev], tmp_path / "cards.md")
    assert "Josephson metrics (P4.1)" not in cards.read_text()


# ---------------------------------------------------------------------------
# CLI / examples / docs
# ---------------------------------------------------------------------------


def test_dummy_dry_run_stays_inert(tmp_path: Path) -> None:
    if not DUMMY.is_file():
        pytest.skip("dummy campaign missing")
    runner = CliRunner()
    out = tmp_path / "dummy"
    result = runner.invoke(app, ["run", "--dry-run", str(DUMMY), "-o", str(out)])
    assert result.exit_code == 0, result.stdout + result.stderr
    import json

    data = json.loads((out / "evaluations.json").read_text())
    assert data
    for row in data:
        ev = CandidateEvaluation.model_validate(row)
        assert ev.josephson is None
    csv_text = (out / "evaluations.csv").read_text()
    assert "josephson_gap_meV" in csv_text.splitlines()[0]
    cards = (out / "synthesis_cards.md").read_text()
    assert "Josephson metrics (P4.1)" not in cards


def test_enabled_dry_run_example_exports_metrics(tmp_path: Path) -> None:
    if not EXAMPLE.is_file():
        pytest.skip("p41 example missing")
    runner = CliRunner()
    out = tmp_path / "jj"
    result = runner.invoke(app, ["run", "--dry-run", str(EXAMPLE), "-o", str(out)])
    assert result.exit_code == 0, result.stdout + result.stderr
    import json

    data = json.loads((out / "evaluations.json").read_text())
    evs = [CandidateEvaluation.model_validate(row) for row in data]
    populated = [e for e in evs if e.josephson is not None and e.josephson.status == "ok"]
    assert populated, "enabled dry-run should attach JosephsonMetrics when Tc exists"
    for ev in populated:
        assert ev.josephson.approximate is True
        assert ev.josephson.gap_meV and ev.josephson.gap_meV > 0
        assert ev.josephson.icrn_mV and ev.josephson.icrn_mV > 0
        assert "ranking only" in ev.josephson.notes.lower()
    csv_text = (out / "evaluations.csv").read_text()
    assert "josephson_icrn_mV" in csv_text
    assert "RANKING ONLY" in csv_text or "approximate" in csv_text.lower()
    cards = (out / "synthesis_cards.md").read_text()
    assert "approximate / ranking only" in cards.lower()
    assert "Josephson metrics" in cards


def test_example_yaml_loads() -> None:
    cfg = CampaignConfig.from_yaml(EXAMPLE)
    assert cfg.josephson.enabled is True
    assert cfg.josephson.model_tier == "analytic_AB"
    assert cfg.josephson.rna_ohm_um2 == 20.0


def test_yaml_knobs_validate() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "p41",
            "josephson": {
                "enabled": True,
                "shortlist_size": 8,
                "rna_ohm_um2": 10.0,
                "bcs_gap_ratio": 2.0,
                "family_gap_ratios": {"tm_nitride": 2.05},
            },
        }
    )
    assert cfg.josephson.shortlist_size == 8
    assert cfg.josephson.family_gap_ratios["tm_nitride"] == 2.05
    with pytest.raises(ValidationError):
        JosephsonConfig(rna_ohm_um2=-1.0)
    with pytest.raises(ValidationError):
        JosephsonConfig(temperature_K=-4.0)
    with pytest.raises(ValidationError):
        JosephsonConfig(family_gap_ratios={"tm_nitride": -1.0})
    with pytest.raises(ValidationError):
        JosephsonConfig(family_gap_ratios={"tm_nitride": 0.0})
    with pytest.raises(ValidationError):
        JosephsonConfig(family_gap_ratios={"tm_nitride": float("nan")})


def test_docs_exist() -> None:
    doc = (ROOT / "docs" / "phase4-p41-josephson-tier1.md").read_text()
    assert "Ambegaokar" in doc
    assert "meV" in doc
    assert "ranking only" in doc.lower()
    assert "dmft_pairing" in doc
    assert "1.764" in doc
    assert "P4.2" in doc
    assert "family_gap_ratios" in doc
    assert "tm_nitride" in doc
    assert "Temperature-independent" in doc
    assert "assume_SIS" in doc
    assert "1/RnA" in doc
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text()
    assert "P4.1" in roadmap


def test_metrics_round_trip() -> None:
    ev = estimate_tier1(_ev(eph=_eph(tc=16.0), rank=1))
    wrapped = _ev(eph=_eph(tc=16.0))
    wrapped = wrapped.model_copy(update={"josephson": ev})
    restored = CandidateEvaluation.model_validate(wrapped.model_dump(mode="json"))
    assert restored.josephson is not None
    assert restored.josephson.gap_meV == pytest.approx(ev.gap_meV)
    assert restored.josephson.approximate is True
