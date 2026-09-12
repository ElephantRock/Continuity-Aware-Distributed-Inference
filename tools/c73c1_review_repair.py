from pathlib import Path

p = Path("experiments/c73c_protocol.py")
text = p.read_text()

old = '''    if not isinstance(base_manifest, C7ExperimentManifest):
        raise TypeError("base_manifest must be C7ExperimentManifest")
    if base_manifest.seed is None:
        raise ValueError("C7.3c synthetic base manifest requires a frozen seed")
    params = dict(base_manifest.parameters)
    if base_manifest.series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
'''
new = '''    if not isinstance(base_manifest, C7ExperimentManifest):
        raise TypeError("base_manifest must be C7ExperimentManifest")
    if base_manifest.policy_id is not PolicyID.B4:
        raise ValueError("C7.3c base manifest must hold underlying routing/control PolicyID.B4")
    if base_manifest.workload_class is not WorkloadClass.SYNTHETIC_STRESS:
        raise ValueError("C7.3c base manifest must be SYNTHETIC_STRESS")
    if base_manifest.hardware_id not in C7_SUPPORTED_HARDWARE_IDS:
        raise ValueError("C7.3c base manifest hardware is outside the accepted C6 runtime family")
    if base_manifest.seed is None:
        raise ValueError("C7.3c synthetic base manifest requires a frozen seed")
    params = dict(base_manifest.parameters)
    sources = dict(base_manifest.parameter_sources)
    required_parameters = {
        ExperimentSeries.P2_TOOL_GAP_RETENTION: {
            "cache_capacity_ratio",
            "state_tokens",
            "tool_gap_seconds",
            "tool_return_probability",
        },
        ExperimentSeries.P3_BRANCH_CACHE_PRESSURE: {
            "branch_width",
            "cache_capacity_ratio",
            "speculative_fraction",
            "state_tokens",
        },
    }.get(base_manifest.series)
    if required_parameters is None:
        raise ValueError("C7.3c base manifest must be P2 or P3")
    if set(params) != required_parameters or set(sources) != required_parameters:
        raise ValueError("C7.3c base manifest must contain exactly the frozen P2/P3 parameter set")
    if any(source is not ParameterSource.P_SRC4 for source in sources.values()):
        raise ValueError("C7.3c base manifest parameters must all retain P-SRC4 classification")
    if params["state_tokens"] != C73_DEFAULT_STATE_TOKENS:
        raise ValueError("C7.3c base manifest must retain frozen state_tokens=16")
    if base_manifest.series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
'''
if text.count(old) != 1:
    raise SystemExit("validate_case anchor drift")
text = text.replace(old, new)

old = '''                "case_must_regenerate_exactly_from_base_manifest_parameters_and_seed": True,
                "same_semantic_validity_outcomes": True,
'''
new = '''                "case_must_regenerate_exactly_from_base_manifest_parameters_and_seed": True,
                "base_manifest_exact_series_parameter_set_and_psrc4_required": True,
                "same_semantic_validity_outcomes": True,
'''
if text.count(old) != 1:
    raise SystemExit("paired fairness anchor drift")
text = text.replace(old, new)

old = '''                "support_metrics": ["USR", "RR", "P2_TOOL_RETURN_TTFT", "CCR"],
                "wsr_can_trigger_support": False,
'''
new = '''                "support_metrics": ["USR", "RR", "P2_TOOL_RETURN_TTFT", "CCR"],
                "ttft_hardware_support_rule": (
                    "same P2 cell must be favorable versus all primary baselines on both "
                    "accepted C6 hardware-profile strata"
                ),
                "single_hardware_ttft_can_trigger_h5": False,
                "wsr_can_trigger_support": False,
'''
if text.count(old) != 1:
    raise SystemExit("H5 hardware anchor drift")
text = text.replace(old, new)

p.write_text(text)

p = Path("tests/experiments/test_c73c_protocol.py")
text = p.read_text()
text = text.replace(
    "from __future__ import annotations\n\nfrom pathlib import Path\n",
    "from __future__ import annotations\n\nfrom dataclasses import replace\nfrom pathlib import Path\n",
    1,
)
old = '''def test_metric_components_preserve_ratio_numerators_and_zero_denominators() -> None:
'''
insert = '''def test_case_base_cross_binding_rejects_extra_axes_and_wrong_underlying_policy() -> None:
    cell = _p2_gap_cache(5.0, 1.0)
    case = build_primary_program_case(cell, seed=5)
    base = build_base_manifest(
        cell,
        seed=5,
        hardware_id="a100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    extra_params = tuple(sorted(base.parameters + (("extra_axis", 1.0),)))
    extra_sources = tuple(sorted(base.parameter_sources + (("extra_axis", ParameterSource.P_SRC4),)))
    extra = replace(base, parameters=extra_params, parameter_sources=extra_sources)
    with pytest.raises(ValueError, match="exactly the frozen P2/P3 parameter set"):
        retention_manifest_variants(case=case, base_manifest=extra)

    wrong_policy = replace(base, policy_id=PolicyID.LRU)
    with pytest.raises(ValueError, match="PolicyID.B4"):
        retention_manifest_variants(case=case, base_manifest=wrong_policy)


def test_metric_components_preserve_ratio_numerators_and_zero_denominators() -> None:
'''
if text.count(old) != 1:
    raise SystemExit("metric-test insertion anchor drift")
text = text.replace(old, insert)

old = '''    assert payload["h5_rule"]["primary_baselines"] == [
        "LRU", "FIXED_TTL(5s)", "SESSION_PINNING"
    ]
'''
new = '''    assert payload["h5_rule"]["primary_baselines"] == [
        "LRU", "FIXED_TTL(5s)", "SESSION_PINNING"
    ]
    assert payload["h5_rule"]["single_hardware_ttft_can_trigger_h5"] is False
    assert "both accepted C6 hardware-profile strata" in payload["h5_rule"]["ttft_hardware_support_rule"]
'''
if text.count(old) != 1:
    raise SystemExit("H5 test anchor drift")
text = text.replace(old, new)

p.write_text(text)
