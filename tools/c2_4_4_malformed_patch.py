from pathlib import Path

oracle = Path('simulator/fault_oracle.py')
text = oracle.read_text()
old = '''    if not _string_tuple(record.produced_event_ids):
        errors.append(f"{prefix}: produced_event_ids must be tuple[str, ...]")
    if not _string_tuple(record.cancelled_event_ids):
        errors.append(f"{prefix}: cancelled_event_ids must be tuple[str, ...]")

    produced = record.produced_event_ids if isinstance(record.produced_event_ids, tuple) else ()
    cancelled = (
        record.cancelled_event_ids
        if isinstance(record.cancelled_event_ids, tuple)
        else ()
    )
    if len(set(produced)) != len(produced):
        errors.append(f"{prefix}: produced_event_ids contain duplicates")
    if len(set(cancelled)) != len(cancelled):
        errors.append(f"{prefix}: cancelled_event_ids contain duplicates")
    if set(produced) & set(cancelled):
        errors.append(
            f"{prefix}: one EventID is both produced and cancelled by the same fault"
        )
'''
new = '''    produced_valid = _string_tuple(record.produced_event_ids)
    cancelled_valid = _string_tuple(record.cancelled_event_ids)
    if not produced_valid:
        errors.append(f"{prefix}: produced_event_ids must be tuple[str, ...]")
    if not cancelled_valid:
        errors.append(f"{prefix}: cancelled_event_ids must be tuple[str, ...]")

    produced = record.produced_event_ids if produced_valid else ()
    cancelled = record.cancelled_event_ids if cancelled_valid else ()
    if len(set(produced)) != len(produced):
        errors.append(f"{prefix}: produced_event_ids contain duplicates")
    if len(set(cancelled)) != len(cancelled):
        errors.append(f"{prefix}: cancelled_event_ids contain duplicates")
    if set(produced) & set(cancelled):
        errors.append(
            f"{prefix}: one EventID is both produced and cancelled by the same fault"
        )
'''
if old not in text:
    raise SystemExit('missing malformed EventID target')
text = text.replace(old, new, 1)
oracle.write_text(text)

tests = Path('tests/simulator/test_fault_oracle.py')
t = tests.read_text()
t += '''


def test_malformed_unhashable_produced_or_cancelled_event_ids_are_reported_not_raised():
    sim, injector, record = _delay_case()
    malformed_produced = replace(record, produced_event_ids=([],))
    malformed_cancelled = replace(record, cancelled_event_ids=([],))
    report = FaultTrustOracle(
        sim,
        (malformed_produced, malformed_cancelled),
        injector_seed=injector.seed,
    ).inspect()
    assert any(
        "produced_event_ids must be tuple[str, ...]" in item
        for item in report.violations
    )
    assert any(
        "cancelled_event_ids must be tuple[str, ...]" in item
        for item in report.violations
    )
'''
tests.write_text(t)
