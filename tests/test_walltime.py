"""Unit tests for desktop walltime estimation (no real QE)."""

from __future__ import annotations

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import DFTConfig, EPWConfig, RunConfig
from siscforge.walltime import (
    WalltimeTracker,
    dfpt_q_grid,
    estimate_campaign_walltime,
    estimate_candidate_walltime,
    format_campaign_estimate_lines,
    format_duration_band,
    heartbeat_eta_suffix,
    n_atoms_from_candidate,
    parse_ph_progress,
    remaining_time_hint,
    resolve_walltime_tier,
    should_print_walltime_estimate,
)


def _screening_dft(**kwargs) -> DFTConfig:
    data = dict(
        quality_tag="screening",
        nproc=8,
        qpoints=[2, 2, 2],
        do_epw=True,
        epw=EPWConfig(
            enabled=True,
            nqc=[2, 2, 2],
            nkf=[6, 6, 6],
            nqf=[6, 6, 6],
            npool=8,
        ),
    )
    data.update(kwargs)
    return DFTConfig(**data)


def _dense_dft(**kwargs) -> DFTConfig:
    data = dict(
        quality_tag="production",
        nproc=16,
        qpoints=[4, 4, 4],
        do_epw=True,
        epw=EPWConfig(
            enabled=True,
            nqc=[4, 4, 4],
            nkf=[12, 12, 12],
            nqf=[12, 12, 12],
            npool=16,
        ),
    )
    data.update(kwargs)
    return DFTConfig(**data)


def _phonon_only_dense_q(**kwargs) -> DFTConfig:
    """Phonon-only map: screening tag, unused default epw.nqc=2³, real q=4³."""
    data = dict(
        quality_tag="screening",
        nproc=16,
        qpoints=[4, 4, 4],
        kpoints=[4, 4, 4],
        do_epw=False,
        epw=EPWConfig(enabled=False, nqc=[2, 2, 2]),
    )
    data.update(kwargs)
    return DFTConfig(**data)


def test_resolve_tier_screening_vs_dense() -> None:
    assert resolve_walltime_tier(_screening_dft()) == "screening"
    assert resolve_walltime_tier(_dense_dft()) == "workstation_dense"
    assert (
        resolve_walltime_tier(
            _dense_dft(qpoints=[6, 6, 6], epw=EPWConfig(nqc=[6, 6, 6], nkf=[18, 18, 18]))
        )
        == "production"
    )
    assert resolve_walltime_tier(_screening_dft(), explicit="workstation_dense") == (
        "workstation_dense"
    )


def test_estimate_nonempty_bands() -> None:
    est = estimate_candidate_walltime(_screening_dft(), n_atoms=8, n_candidates=6)
    assert est.dfpt_band()
    assert est.full_band()
    assert est.campaign_band()
    assert est.dfpt_lo_h > 0
    assert est.dfpt_hi_h >= est.dfpt_lo_h
    assert est.full_hi_h >= est.full_lo_h
    assert est.campaign_hi_h >= est.full_hi_h
    assert "order-of-magnitude" in est.per_candidate_line()
    assert "6 candidate" in est.campaign_line()


def test_dense_tier_geq_screening_lower_bound() -> None:
    scr = estimate_candidate_walltime(_screening_dft(nproc=16), n_atoms=8)
    dense = estimate_candidate_walltime(_dense_dft(nproc=16), n_atoms=8)
    # denser tier lower bound should be ≥ screening lower bound
    assert dense.dfpt_lo_h >= scr.dfpt_lo_h
    assert dense.full_lo_h >= scr.full_lo_h
    assert dense.full_hi_h >= scr.full_hi_h


def test_more_atoms_increases_estimate() -> None:
    small = estimate_candidate_walltime(_screening_dft(), n_atoms=2)
    big = estimate_candidate_walltime(_screening_dft(), n_atoms=8)
    assert big.full_lo_h > small.full_lo_h


def test_more_nproc_decreases_estimate() -> None:
    slow = estimate_candidate_walltime(_screening_dft(nproc=4), n_atoms=8)
    fast = estimate_candidate_walltime(_screening_dft(nproc=16), n_atoms=8)
    assert fast.full_hi_h < slow.full_hi_h


def test_walltime_scale_stretches_band() -> None:
    base = estimate_candidate_walltime(_screening_dft(), n_atoms=8, scale=1.0)
    scaled = estimate_candidate_walltime(_screening_dft(), n_atoms=8, scale=2.0)
    assert abs(scaled.full_lo_h / base.full_lo_h - 2.0) < 1e-6


def test_campaign_lines_format() -> None:
    est = estimate_campaign_walltime(_screening_dft(), n_candidates=6)
    lines = format_campaign_estimate_lines(est)
    blob = "\n".join(lines)
    assert "Estimated walltime" in blob
    assert "per candidate" in blob
    assert "this campaign" in blob
    assert "6 candidate" in blob
    assert "Tip: safe to interrupt" in blob
    assert "heuristic" in blob.lower() or "not a guarantee" in blob


def test_format_duration_band_units() -> None:
    assert "min" in format_duration_band(0.25, 0.75) or "h" in format_duration_band(
        0.25, 0.75
    )
    assert "h" in format_duration_band(2.0, 8.0)
    assert "d" in format_duration_band(24.0, 72.0)


def test_should_print_only_for_qe() -> None:
    run = RunConfig(estimate_walltime=True)
    assert should_print_walltime_estimate("qe-epw", run)
    assert should_print_walltime_estimate("qe", run)
    assert not should_print_walltime_estimate("mock", run)
    assert not should_print_walltime_estimate(
        "qe-epw", RunConfig(estimate_walltime=False)
    )


def test_parse_ph_progress_q_of() -> None:
    text = (
        "header\n"
        "     (   8 q-points):\n"
        "     Calculation of q =    0.0  0.0  0.0\n"
        "     Calculation of q =    0.5  0.0  0.0\n"
        "     Calculation of q =    0.5  0.5  0.0\n"
    )
    prog = parse_ph_progress(text)
    assert prog is not None
    frac, label = prog
    assert abs(frac - 3 / 8) < 1e-9
    assert "3/8" in label


def test_parse_ph_progress_explicit_of() -> None:
    text = "q-point #   4  of   8\nRepresentation #  1\n"
    prog = parse_ph_progress(text)
    assert prog is not None
    assert abs(prog[0] - 0.5) < 1e-9


def test_parse_ph_progress_none_when_unknown() -> None:
    assert parse_ph_progress("iter # 12 total cpu time") is None
    assert parse_ph_progress("") is None
    assert parse_ph_progress(None) is None


def test_remaining_time_hint_needs_real_progress() -> None:
    # Too early
    assert remaining_time_hint(3600.0, 0.02) is None
    # Mid progress after 10 h → remaining band present
    hint = remaining_time_hint(10 * 3600.0, 0.25)
    assert hint is not None
    assert "remaining" in hint
    # Almost done
    assert remaining_time_hint(3600.0, 0.99) is None


def test_heartbeat_eta_suffix_only_when_parseable() -> None:
    log = "     (   4 q-points):\n" + "\n".join(
        "     Calculation of q =    0.0  0.0  0.0" for _ in range(2)
    )
    # 50% after 2h → hint
    s = heartbeat_eta_suffix(log, 2 * 3600.0, enabled=True)
    assert "progress" in s
    assert "remaining" in s or "2/4" in s
    # Disabled
    assert heartbeat_eta_suffix(log, 2 * 3600.0, enabled=False) == ""
    # Unparseable log → empty or no fake remaining
    s2 = heartbeat_eta_suffix("noise only", 2 * 3600.0, enabled=True)
    assert s2 == ""


def test_tracker_observed_scale() -> None:
    tr = WalltimeTracker()
    tr.start("a")
    # Simulate by pushing directly
    tr.observed_h.append(4.0)
    tr.predictions_h.append(2.0)
    scale = tr.observed_scale()
    assert scale is not None
    assert abs(scale - 2.0) < 1e-6
    # Clamp high
    tr.observed_h.append(100.0)
    tr.predictions_h.append(1.0)
    assert tr.observed_scale() <= 3.0


def test_n_atoms_defaults() -> None:
    c = StructureCandidate(formula="NbN")
    assert n_atoms_from_candidate(c) == 2
    c2 = StructureCandidate(formula="Nb0.5Ti0.5N", metadata={"n_atoms": 8})
    assert n_atoms_from_candidate(c2) == 8


def test_screening_shortlist_vs_dense_refine_example_lines() -> None:
    """Document-style startup bands for the acceptance examples."""
    scr = estimate_campaign_walltime(
        _screening_dft(nproc=8),
        n_candidates=6,
    )
    dense = estimate_campaign_walltime(
        _dense_dft(nproc=16),
        n_candidates=2,
    )
    scr_lines = format_campaign_estimate_lines(scr)
    dense_lines = format_campaign_estimate_lines(dense)
    assert any("6 candidate" in ln for ln in scr_lines)
    assert any("2 candidate" in ln for ln in dense_lines)
    # Dense campaign lower bound should exceed screening per-candidate floor
    assert dense.full_lo_h >= scr.full_lo_h
    # Print-friendly smoke (used when writing acceptance examples)
    assert scr.per_candidate_line()
    assert dense.per_candidate_line()


def test_phonon_only_q_product_ignores_epw_nqc() -> None:
    """Issue #67: do_epw=false must use dft.qpoints, not default epw.nqc=2³."""
    dft = _phonon_only_dense_q(kpoints=[12, 12, 12])
    assert dfpt_q_grid(dft) == [4, 4, 4]
    est = estimate_candidate_walltime(dft, n_atoms=2)
    assert est.q_product == 64
    assert est.k_product == 12 * 12 * 12
    assert not est.do_epw
    blob = "\n".join(format_campaign_estimate_lines(est))
    assert "q-mesh=64" in blob
    assert "q-mesh=8 " not in blob
    assert "k-mesh=1728" in blob


def test_phonon_only_dense_q_upgrades_tier() -> None:
    """quality_tag=screening + q=4³ must not stay on the cheap screening band."""
    assert resolve_walltime_tier(_phonon_only_dense_q()) == "workstation_dense"
    assert resolve_walltime_tier(_screening_dft()) == "screening"


def test_phonon_only_dense_q_band_is_multi_hour() -> None:
    """4³ / 2-atom / 16-core / k=12³ must not advertise a ~1 h campaign."""
    dft = _phonon_only_dense_q(kpoints=[12, 12, 12])
    est = estimate_candidate_walltime(dft, n_atoms=2)
    # Observed ZrN k12 DFPT ~5.8–7 h; band may stay wide but not ~1 h.
    assert est.tier == "workstation_dense"
    assert est.full_lo_h >= 2.0
    assert est.full_hi_h >= 6.0
    assert est.dfpt_lo_h >= 2.0
    blob = "\n".join(format_campaign_estimate_lines(est))
    assert "1.1 h" not in blob
    assert "7 min" not in blob


def test_epw_on_still_uses_nqc() -> None:
    dft = _screening_dft(qpoints=[4, 4, 4])
    est = estimate_candidate_walltime(dft, n_atoms=8)
    assert est.do_epw
    assert est.q_product == 8
    assert dfpt_q_grid(dft) == [2, 2, 2]


def test_denser_k_increases_estimate() -> None:
    lo = estimate_candidate_walltime(_phonon_only_dense_q(kpoints=[4, 4, 4]), n_atoms=2)
    hi = estimate_candidate_walltime(_phonon_only_dense_q(kpoints=[12, 12, 12]), n_atoms=2)
    assert hi.dfpt_lo_h > lo.dfpt_lo_h
    assert hi.full_hi_h > lo.full_hi_h
