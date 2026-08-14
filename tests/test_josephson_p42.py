"""P4.2 — fabrication-compatibility heuristics + optional JJ secondary sort."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from siscforge.cli.main import app
from siscforge.export import CSV_FIELDNAMES, write_evaluations_csv, write_synthesis_cards
from siscforge.josephson import (
    HEURISTIC_CAVEAT,
    NON_SIS_AB_CAVEAT,
    RANKING_ONLY_CAVEAT,
    apply_secondary_ranking,
    attach_josephson_metrics,
    format_fab_notes_for_csv,
    infer_fabrication_hints,
    josephson_is_enabled,
    normalize_secondary_ranking,
    secondary_ranking_summary,
    suggest_junction_class,
    thermal_compatibility,
)
from siscforge.models import (
    CampaignConfig,
    CandidateEvaluation,
    ElectronPhononResult,
    JosephsonConfig,
    JosephsonFabricationHints,
    JosephsonMetrics,
    SiFeasibilityScore,
    StructureCandidate,
)
from siscforge.ranking import rank_evaluations

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "nbn_mgb2_josephson_tier1.yaml"
DUMMY = ROOT / "examples" / "dummy_campaign.yaml"


def _eph(*, tc: float = 16.0) -> ElectronPhononResult:
    return ElectronPhononResult(
        lambda_total=1.1,
        omega_log=250.0,
        mu_star=0.1,
        Tc_allen_dynes=tc,
        Tc_eliashberg=tc,
        converged=True,
        status="ok",
        quality_tag="screening",
    )


def _si(
    *,
    ceiling: float | None = 550.0,
    flags: list[str] | None = None,
    buffers: list[str] | None = None,
    membrane: bool = False,
    membrane_note: str = "",
    thermal: str = "TiN PVD/ALD typically ~200–550 °C",
) -> SiFeasibilityScore:
    return SiFeasibilityScore(
        total=60.0,
        process_temp_ceiling_c=ceiling,
        chemical_flags=flags if flags is not None else ["nitrogen_window"],
        recommended_buffers=buffers if buffers is not None else ["TiN", "AlN/TiN"],
        membrane_transfer_candidate=membrane,
        membrane_transfer_note=membrane_note,
        thermal_window_note=thermal,
    )


def _ev(
    *,
    formula: str = "NbN",
    family: str = "tm_nitride",
    tc: float = 16.0,
    rank: int | None = 1,
    cid: str | None = None,
    si: SiFeasibilityScore | None = None,
    eph: ElectronPhononResult | None = ...,  # type: ignore[assignment]
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=StructureCandidate(
            formula=formula,
            material_family=family,  # type: ignore[arg-type]
            candidate_id=cid or f"id-{formula}",
        ),
        electron_phonon=_eph(tc=tc) if eph is ... else eph,
        si_feasibility=si if si is not None else _si(),
        performance_score=tc,
        performance_score_source="epw",
        rank=rank,
        status="ok",
    )


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def test_nbn_with_si_fields_is_sis_and_notes() -> None:
    ev = _ev(formula="NbN", family="tm_nitride", si=_si(ceiling=550.0))
    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    assert hints.suggested_junction_class == "SIS"
    assert hints.status == "ok"
    assert hints.heuristic is True
    assert hints.notes
    assert any("heuristic" in n.lower() for n in hints.notes)
    assert HEURISTIC_CAVEAT in hints.notes
    assert NON_SIS_AB_CAVEAT not in hints.notes
    assert "sis" in hints.flags
    assert "ab_sis_formula" in hints.flags
    assert "ab_sis_proxy_on_nonsis_class" not in hints.flags
    assert hints.recommended_stacks
    assert hints.chemical_flags == ["nitrogen_window"]


def test_mgb2_is_sns_even_if_assume_sis() -> None:
    ev = _ev(formula="MgB2", family="mgb2_boride", tc=39.0, si=_si(ceiling=550.0))
    hints = infer_fabrication_hints(
        ev, JosephsonMetrics(status="ok"), JosephsonConfig(assume_SIS=True)
    )
    assert hints.suggested_junction_class == "SNS"
    assert "ramp_edge" in hints.alternative_classes
    assert any("assume_SIS is recorded" in n for n in hints.notes)
    assert NON_SIS_AB_CAVEAT in hints.notes
    assert "ab_sis_proxy_on_nonsis_class" in hints.flags
    assert "ab_sis_formula" in hints.flags


def test_nitride_assume_sis_false_gets_ab_mismatch_note() -> None:
    ev = _ev(family="tm_nitride")
    hints = infer_fabrication_hints(
        ev, JosephsonMetrics(status="ok"), JosephsonConfig(assume_SIS=False)
    )
    assert hints.suggested_junction_class == "SNS"
    assert NON_SIS_AB_CAVEAT in hints.notes


def test_high_process_temp_sets_thermal_caution() -> None:
    ev = _ev(
        si=_si(
            ceiling=900.0,
            flags=["nitrogen_window", "high_thermal_budget"],
            buffers=["AlN/TiN"],
        )
    )
    beol, caution, flags, notes = thermal_compatibility(
        process_temp_ceiling_c=900.0,
        chemical_flags=["nitrogen_window", "high_thermal_budget"],
        beol_temp_c=400.0,
    )
    assert beol is False
    assert caution is True
    assert "thermal_budget_caution" in flags
    assert any("900" in n for n in notes)

    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    assert hints.thermal_budget_caution is True
    assert hints.beol_friendly is False
    assert "thermal_budget_caution" in hints.flags


def test_low_ceiling_is_beol_friendly() -> None:
    ev = _ev(si=_si(ceiling=350.0, flags=["nitrogen_window"], thermal="cool sputter"))
    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    assert hints.beol_friendly is True
    assert hints.thermal_budget_caution is False
    assert "beol_friendly" in hints.flags


def test_missing_si_degrades_to_family_class() -> None:
    ev = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="NbN", material_family="tm_nitride", candidate_id="no-si"
        ),
        electron_phonon=_eph(),
        si_feasibility=None,
        performance_score=16.0,
        performance_score_source="epw",
        rank=1,
    )
    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    assert hints.suggested_junction_class == "SIS"
    assert hints.beol_friendly is None
    assert "si_missing" in hints.flags
    assert any("Si-feasibility missing" in n for n in hints.notes)


def test_missing_family_and_si_is_unknown() -> None:
    primary, alts, notes = suggest_junction_class(
        family=None, assume_sis=True, membrane=False
    )
    assert primary == "unknown"
    assert alts == []
    assert any("unknown" in n.lower() for n in notes)


def test_skipped_tier1_still_emits_hints() -> None:
    ev = _ev(eph=None, si=_si(ceiling=550.0))
    hints = infer_fabrication_hints(
        ev, JosephsonMetrics(status="skipped"), JosephsonConfig()
    )
    assert hints.suggested_junction_class == "SIS"
    assert "tier1_missing" in hints.flags
    assert any("skipped" in n.lower() or "missing" in n.lower() for n in hints.notes)


def test_membrane_adds_ramp_edge_alternative() -> None:
    ev = _ev(si=_si(membrane=True, membrane_note="high mismatch"))
    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    assert hints.suggested_junction_class == "SIS"
    assert "ramp_edge" in hints.alternative_classes
    assert "membrane_transfer" in hints.flags


def test_nickelate_stays_unknown_class() -> None:
    ev = _ev(formula="NdNiO2", family="nickelate", si=_si(ceiling=500.0))
    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    assert hints.suggested_junction_class == "unknown"
    assert "unknown_class" in hints.flags


# ---------------------------------------------------------------------------
# Attachment / inert / secondary sort
# ---------------------------------------------------------------------------


def test_disabled_is_still_identity() -> None:
    evs = [_ev(rank=1), _ev(formula="MgB2", family="mgb2_boride", tc=39.0, rank=2)]
    cfg = JosephsonConfig(enabled=False)
    assert josephson_is_enabled(cfg) is False
    out = attach_josephson_metrics(evs, cfg)
    assert out is evs
    assert all(ev.josephson is None for ev in out)


def test_enabled_attaches_fabrication_by_default() -> None:
    evs = rank_evaluations([_ev(rank=None)])
    out = attach_josephson_metrics(
        evs, JosephsonConfig(enabled=True, shortlist_only=False)
    )
    jj = out[0].josephson
    assert jj is not None
    assert jj.status == "ok"
    assert jj.fabrication is not None
    assert jj.fabrication.suggested_junction_class == "SIS"
    assert jj.fabrication.notes


def test_fabrication_hints_opt_out() -> None:
    evs = [_ev(rank=1)]
    cfg = JosephsonConfig(enabled=True, fabrication_hints=False, shortlist_only=False)
    out = attach_josephson_metrics(evs, cfg)
    assert out[0].josephson is not None
    assert out[0].josephson.fabrication is None


def test_secondary_sort_reorders_jj_rows_only() -> None:
    """Higher IcRn presented first; rank / composite_score stay put."""
    lo = _ev(formula="Lo", tc=8.0, rank=1, cid="lo")
    hi = _ev(formula="Hi", tc=30.0, rank=2, cid="hi")
    rest = _ev(formula="Rest", tc=12.0, rank=3, cid="rest")
    # Attach only first two (shortlist_size=2)
    cfg = JosephsonConfig(
        enabled=True,
        shortlist_only=True,
        shortlist_size=2,
        secondary_ranking="icrn",
    )
    out = attach_josephson_metrics([lo, hi, rest], cfg)
    assert out[0].josephson is not None
    assert out[1].josephson is not None
    assert out[2].josephson is None
    # Presentation: Hi (larger IcRn) then Lo, Rest stays last.
    assert [e.candidate.candidate_id for e in out] == ["hi", "lo", "rest"]
    # Rank identity unchanged.
    by_id = {e.candidate.candidate_id: e for e in out}
    assert by_id["lo"].rank == 1
    assert by_id["hi"].rank == 2
    assert by_id["rest"].rank == 3
    assert by_id["hi"].josephson.secondary_order == 1
    assert by_id["lo"].josephson.secondary_order == 2
    assert by_id["hi"].josephson.secondary_ranking == "icrn"
    # composite_score was never written — still None — and not invented.
    assert by_id["lo"].composite_score == lo.composite_score
    assert by_id["hi"].composite_score == hi.composite_score
    banner = secondary_ranking_summary(out)
    assert banner is not None
    assert "icrn" in banner
    assert "rank identity unchanged" in banner


def test_secondary_sort_logs_presentation_note(caplog: pytest.LogCaptureFixture) -> None:
    evs = [
        _ev(formula="A", tc=8.0, rank=1, cid="a"),
        _ev(formula="B", tc=30.0, rank=2, cid="b"),
    ]
    with caplog.at_level("INFO", logger="siscforge.josephson.attach"):
        attach_josephson_metrics(
            evs,
            JosephsonConfig(enabled=True, shortlist_only=False, secondary_ranking="jc"),
        )
    assert any("presentation only" in rec.message for rec in caplog.records)


def test_csv_notes_put_permanent_caveats_first() -> None:
    ev = _ev(formula="MgB2", family="mgb2_boride", tc=39.0)
    hints = infer_fabrication_hints(ev, JosephsonMetrics(status="ok"), JosephsonConfig())
    joined = format_fab_notes_for_csv(hints.notes)
    assert joined.startswith(HEURISTIC_CAVEAT)
    assert RANKING_ONLY_CAVEAT.split("—")[0].strip() in joined or "RANKING ONLY" in joined
    # Caveats precede the family/class science notes.
    assert joined.index(HEURISTIC_CAVEAT) < joined.index("mgb2_boride")
    assert NON_SIS_AB_CAVEAT in joined


def test_normalize_secondary_ranking_matches_config() -> None:
    """fabrication wrapper and JosephsonConfig share one coerce path."""
    for raw, expected in (
        (False, "none"),
        (True, "icrn"),
        ("none", "none"),
        ("icrn", "icrn"),
        ("jc", "jc"),
        ("OFF", "none"),
    ):
        assert normalize_secondary_ranking(raw) == expected
        assert JosephsonConfig.normalize_secondary_ranking(raw) == expected
        assert JosephsonConfig(secondary_ranking=raw).secondary_ranking == expected  # type: ignore[arg-type]


def test_secondary_none_preserves_order() -> None:
    a = _ev(formula="A", tc=8.0, rank=1, cid="a")
    b = _ev(formula="B", tc=30.0, rank=2, cid="b")
    cfg = JosephsonConfig(enabled=True, shortlist_only=False, secondary_ranking="none")
    out = attach_josephson_metrics([a, b], cfg)
    assert [e.candidate.candidate_id for e in out] == ["a", "b"]
    assert all(e.josephson.secondary_order is None for e in out)


def test_secondary_sort_does_not_change_primary_ranking() -> None:
    a = _ev(formula="hiTc", tc=30.0, cid="hi", rank=None)
    b = _ev(formula="loTc", tc=8.0, cid="lo", rank=None)
    a.si_feasibility = _si(ceiling=350.0)
    a.si_feasibility.total = 40.0
    b.si_feasibility = _si(ceiling=350.0)
    b.si_feasibility.total = 80.0
    plain = rank_evaluations([a, b])
    annotated = attach_josephson_metrics(
        [e.model_copy() for e in plain],
        JosephsonConfig(enabled=True, shortlist_only=False, secondary_ranking="jc"),
    )
    # Re-rank after attach — composite order / scores stay the same.
    again = rank_evaluations(annotated)
    assert [e.candidate.candidate_id for e in again] == [
        e.candidate.candidate_id for e in plain
    ]
    assert [e.composite_score for e in again] == [e.composite_score for e in plain]


def test_apply_secondary_ranking_identity_when_none() -> None:
    evs = [_ev(rank=1), _ev(formula="MgB2", family="mgb2_boride", rank=2)]
    cfg = JosephsonConfig(enabled=True, secondary_ranking="none")
    assert apply_secondary_ranking(evs, cfg) is evs


# ---------------------------------------------------------------------------
# Export / CLI
# ---------------------------------------------------------------------------


def test_export_includes_fabrication_columns(tmp_path: Path) -> None:
    ev = attach_josephson_metrics(
        [_ev(rank=1)], JosephsonConfig(enabled=True, shortlist_only=False)
    )[0]
    ranked = rank_evaluations([ev])
    ranked = attach_josephson_metrics(
        ranked, JosephsonConfig(enabled=True, shortlist_only=False)
    )
    csv_path = write_evaluations_csv(ranked, tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    for col in (
        "josephson_junction_class",
        "josephson_beol_friendly",
        "josephson_thermal_caution",
        "josephson_fab_flags",
        "josephson_fab_notes",
        "josephson_secondary_order",
    ):
        assert col in header
        assert col in CSV_FIELDNAMES
    body = csv_path.read_text()
    assert "SIS" in body
    assert "heuristic" in body.lower() or "not process qualification" in body.lower()
    # Secondary columns sit next to status (presentation-sort visibility).
    header_cols = header.split(",")
    status_i = header_cols.index("josephson_status")
    assert header_cols[status_i + 1] == "josephson_secondary_ranking"
    assert header_cols[status_i + 2] == "josephson_secondary_order"

    cards = write_synthesis_cards(ranked, tmp_path / "cards.md", campaign_name="p42")
    text = cards.read_text()
    assert "Fabrication compatibility (P4.2)" in text
    assert "heuristic" in text.lower()
    assert "not process qualification" in text.lower()
    assert "approximate / ranking only" in text.lower()


def test_secondary_sort_banner_on_cards(tmp_path: Path) -> None:
    evs = attach_josephson_metrics(
        [_ev(formula="A", tc=8.0, rank=1, cid="a"), _ev(formula="B", tc=30.0, rank=2, cid="b")],
        JosephsonConfig(enabled=True, shortlist_only=False, secondary_ranking="icrn"),
    )
    cards = write_synthesis_cards(evs, tmp_path / "cards.md")
    text = cards.read_text()
    assert "secondary sort" in text.lower()
    assert "composite_score" in text
    assert "unchanged" in text.lower()


def test_enabled_dry_run_shows_fabrication(tmp_path: Path) -> None:
    if not EXAMPLE.is_file():
        pytest.skip("p41/p42 example missing")
    runner = CliRunner()
    out = tmp_path / "jj"
    result = runner.invoke(app, ["run", "--dry-run", str(EXAMPLE), "-o", str(out)])
    assert result.exit_code == 0, result.stdout + result.stderr
    import json

    data = json.loads((out / "evaluations.json").read_text())
    evs = [CandidateEvaluation.model_validate(row) for row in data]
    populated = [e for e in evs if e.josephson is not None and e.josephson.status == "ok"]
    assert populated
    hinted = [e for e in populated if e.josephson.fabrication is not None]
    assert hinted, "enabled dry-run should attach fabrication hints"
    classes = {e.josephson.fabrication.suggested_junction_class for e in hinted}
    assert classes & {"SIS", "SNS", "ramp_edge", "unknown"}
    assert any(e.josephson.fabrication.notes for e in hinted)
    csv_text = (out / "evaluations.csv").read_text()
    assert "josephson_junction_class" in csv_text
    cards = (out / "synthesis_cards.md").read_text()
    assert "Fabrication compatibility (P4.2)" in cards
    assert "heuristic" in cards.lower()
    assert "approximate / ranking only" in cards.lower()
    # At least one non-SIS family (MgB2) should carry the AB-mismatch note.
    if any(
        e.josephson.fabrication.suggested_junction_class != "SIS"
        for e in hinted
    ):
        assert "Ambegaokar–Baratoff" in cards or "Ambegaokar" in cards
        assert "Tier-1 formula note" in cards or "SNS / proximity" in cards


def test_dummy_dry_run_still_inert(tmp_path: Path) -> None:
    if not DUMMY.is_file():
        pytest.skip("dummy campaign missing")
    runner = CliRunner()
    out = tmp_path / "dummy"
    result = runner.invoke(app, ["run", "--dry-run", str(DUMMY), "-o", str(out)])
    assert result.exit_code == 0, result.stdout + result.stderr
    import json

    data = json.loads((out / "evaluations.json").read_text())
    for row in data:
        ev = CandidateEvaluation.model_validate(row)
        assert ev.josephson is None
    cards = (out / "synthesis_cards.md").read_text()
    assert "Fabrication compatibility (P4.2)" not in cards


def test_config_coerces_legacy_bool_and_validates() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "p42",
            "josephson": {
                "enabled": True,
                "secondary_ranking": False,
                "fabrication_hints": True,
            },
        }
    )
    assert cfg.josephson.secondary_ranking == "none"
    assert cfg.josephson.fabrication_hints is True
    assert cfg.josephson.beol_temp_ceiling_c == 400.0

    on = JosephsonConfig(enabled=True, secondary_ranking=True)  # type: ignore[arg-type]
    assert on.secondary_ranking == "icrn"
    icrn = JosephsonConfig(secondary_ranking="icrn")
    assert icrn.secondary_ranking == "icrn"
    jc = JosephsonConfig(secondary_ranking="jc")
    assert jc.secondary_ranking == "jc"
    with pytest.raises(ValidationError):
        JosephsonConfig(secondary_ranking="pareto")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        JosephsonConfig(beol_temp_ceiling_c=-10.0)


def test_example_yaml_loads_p42_knobs() -> None:
    cfg = CampaignConfig.from_yaml(EXAMPLE)
    assert cfg.josephson.enabled is True
    assert cfg.josephson.fabrication_hints is True
    assert cfg.josephson.secondary_ranking == "none"


def test_docs_exist() -> None:
    doc = (ROOT / "docs" / "phase4-p42-fabrication.md").read_text()
    assert "SIS" in doc
    assert "SNS" in doc
    assert "ramp_edge" in doc
    assert "heuristic" in doc.lower()
    assert "secondary_ranking" in doc
    assert "not process qualification" in doc.lower() or "not a foundry" in doc.lower()
    assert "Usadel" in doc
    assert "list index" in doc.lower() or "list-order contract" in doc.lower()
    assert "suggest_junction_class" in doc
    assert "unknown" in doc
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text()
    assert "P4.2" in roadmap
    assert "done" in roadmap.lower()


def test_hints_round_trip() -> None:
    ev = attach_josephson_metrics(
        [_ev(rank=1)], JosephsonConfig(enabled=True, shortlist_only=False)
    )[0]
    restored = CandidateEvaluation.model_validate(ev.model_dump(mode="json"))
    assert restored.josephson is not None
    assert restored.josephson.fabrication is not None
    assert restored.josephson.fabrication.suggested_junction_class == (
        ev.josephson.fabrication.suggested_junction_class
    )
    assert isinstance(restored.josephson.fabrication, JosephsonFabricationHints)
