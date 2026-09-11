from pathlib import Path

p = Path("experiments/c73_retention_engine.py")
text = p.read_text()

old = '''        if any(count != 1 for count in admits.values()):
            raise ValueError("every declared State must have exactly one ADMIT event")
'''
new = '''        if any(count != 1 for count in admits.values()):
            raise ValueError("every declared State must have exactly one ADMIT event")

        state_by_id = {item.state_id: item for item in self.states}
        ordered_admits = sorted(
            (event for event in self.events if event.kind is RetentionEventKind.ADMIT),
            key=lambda event: (
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            ),
        )
        admit_order_key: dict[str, tuple[float, int, int, str]] = {}
        for expected_ordinal, event in enumerate(ordered_admits):
            state_id = event.state_id
            if state_by_id[state_id].admission_ordinal != expected_ordinal:  # type: ignore[index]
                raise ValueError(
                    "State admission_ordinal must match deterministic global ADMIT order"
                )
            admit_order_key[state_id] = (  # type: ignore[index]
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            )
        for event in self.events:
            if event.kind not in {RetentionEventKind.REUSE, RetentionEventKind.INVALIDATE}:
                continue
            event_key = (
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            )
            if event_key < admit_order_key[event.state_id]:  # type: ignore[index]
                raise ValueError(
                    f"{event.kind.value} cannot occur before ADMIT for the same State"
                )
'''
if text.count(old) != 1:
    raise SystemExit("Program-case validation anchor drift")
text = text.replace(old, new)

old = '''    params = dict(base_manifest.parameters)
    sources = dict(base_manifest.parameter_sources)
    if params.get("state_tokens") != C73_DEFAULT_STATE_TOKENS:
'''
new = '''    params = dict(base_manifest.parameters)
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
    }[base_manifest.series]
    if set(params) != required_parameters:
        raise ValueError(
            "C7.3b base manifest must contain exactly the required parameters for its frozen P2/P3 series"
        )
    if set(sources) != required_parameters:
        raise ValueError(
            "C7.3b base manifest parameter sources must exactly cover the required parameters"
        )
    if params.get("state_tokens") != C73_DEFAULT_STATE_TOKENS:
'''
if text.count(old) != 1:
    raise SystemExit("base-manifest validation anchor drift")
p.write_text(text.replace(old, new))
