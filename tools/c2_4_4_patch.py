from pathlib import Path

oracle_path = Path("simulator/fault_oracle.py")
text = oracle_path.read_text()
old = '''    if params is None:
        errors.append(
            f"{prefix}: parameters are not a canonical frozen scalar mapping"
        )
    elif frozenset(params) != _CLASS_PARAMETER_KEYS[record.fault_class]:
        errors.append(
            f"{prefix}: parameter keys for {record.fault_class.value} do not match schema"
        )
'''
new = '''    if params is None:
        errors.append(
            f"{prefix}: parameters are not a canonical frozen scalar mapping"
        )
    elif frozenset(params) != _CLASS_PARAMETER_KEYS[record.fault_class]:
        errors.append(
            f"{prefix}: parameter keys for {record.fault_class.value} do not match schema"
        )
    else:
        numeric_fields: tuple[str, ...] = ()
        identity_fields: tuple[str, ...] = ()
        if record.fault_class is FaultClass.DELIVERY_DELAY:
            numeric_fields = ("original_time", "replacement_time")
            identity_fields = ("replacement_event_id",)
        elif record.fault_class is FaultClass.DELIVERY_DROP:
            numeric_fields = ("original_time",)
        elif record.fault_class is FaultClass.DELIVERY_DUPLICATE:
            numeric_fields = ("original_time", "duplicate_time")
            identity_fields = ("duplicate_event_id",)
        elif record.fault_class is FaultClass.DELIVERY_REORDER:
            numeric_fields = ("anchor_time", "original_time", "replacement_time")
            identity_fields = ("anchor_event_id", "replacement_event_id")
        elif record.fault_class is FaultClass.ATTEMPT_TIMEOUT:
            identity_fields = ("request_id", "retry_attempt_id", "event_id")
        elif record.fault_class is FaultClass.LATE_ATTEMPT_RESULT:
            identity_fields = ("request_id", "event_id")
        elif record.fault_class is FaultClass.STALE_ATTEMPT_OBSERVATION:
            identity_fields = (
                "request_id",
                "evidence_id",
                "output_id",
                "event_id",
            )
            numeric_fields = ("observed_at",)
        else:
            identity_fields = ("event_id",)

        for field in numeric_fields:
            if not _finite_nonnegative(params.get(field)):
                errors.append(
                    f"{prefix}: parameter {field} must be finite and non-negative"
                )
        for field in identity_fields:
            if not _is_nonempty_string(params.get(field)):
                errors.append(
                    f"{prefix}: parameter {field} must be a non-empty string"
                )

        if record.fault_class is FaultClass.ATTEMPT_TIMEOUT:
            retry_id = params.get("retry_attempt_id")
            if _is_nonempty_string(retry_id) and retry_id == record.target:
                errors.append(
                    f"{prefix}: retry_attempt_id must differ from timed-out Attempt"
                )
        if record.fault_class is FaultClass.STALE_ATTEMPT_OBSERVATION:
            observed_at = params.get("observed_at")
            if (
                _finite_nonnegative(observed_at)
                and _finite_nonnegative(record.injection_time)
                and _finite_nonnegative(record.duration)
                and float(observed_at)
                > float(record.injection_time) + float(record.duration) + 1e-12
            ):
                errors.append(
                    f"{prefix}: observed_at cannot exceed observation delivery time"
                )
        if record.fault_class in {
            FaultClass.WORKER_FAILURE,
            FaultClass.REPLICA_LOSS,
            FaultClass.REPLICA_EVICTION,
        } and _finite_nonnegative(record.duration) and float(record.duration) != 0.0:
            errors.append(
                f"{prefix}: physical resource fault duration must be zero"
            )
'''
if old not in text:
    raise SystemExit("missing parameter schema target")
text = text.replace(old, new, 1)
oracle_path.write_text(text)

tests_path = Path("tests/simulator/test_fault_oracle.py")
tests = tests_path.read_text()
tests = tests.replace(
    "from simulator.faults import FaultClass, FaultInjector, ProbabilisticFaultDecision\n",
    "from simulator.fault_linkage import CrossLayerFaultInjector\nfrom simulator.faults import FaultClass, FaultInjector, ProbabilisticFaultDecision\n",
    1,
)
tests += '''


def test_delivery_time_parameter_wrong_scalar_type_is_rejected_by_schema():
    sim, injector, record = _delay_case()
    forged = replace(
        record,
        parameters=freeze_payload(
            {
                "original_time": "not-a-time",
                "replacement_time": 5.0,
                "replacement_event_id": record.produced_event_ids[0],
            }
        ),
    )
    report = FaultTrustOracle(sim, (forged,), injector_seed=injector.seed).inspect()
    assert any(
        "parameter original_time must be finite and non-negative" in item
        for item in report.violations
    )


def test_physical_resource_fault_duration_must_be_zero():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    injector = FaultInjector(sim, resources)
    record = injector.fail_worker("w1", fault_id="down")
    forged = replace(record, duration=1.0)
    report = FaultTrustOracle(
        sim,
        (forged,),
        injector_seed=injector.seed,
        resources=resources,
    ).inspect()
    assert any(
        "physical resource fault duration must be zero" in item
        for item in report.violations
    )


def test_cross_layer_identity_parameters_must_be_nonempty():
    sim = DiscreteEventSimulator()
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    sim.run(until=1)
    injector = CrossLayerFaultInjector(sim)
    record = injector.inject_attempt_timeout(
        adapter,
        "r",
        "a1",
        "a2",
        at=2,
        fault_id="timeout",
    )
    forged = replace(
        record,
        parameters=freeze_payload(
            {
                "request_id": "",
                "retry_attempt_id": "a2",
                "event_id": record.produced_event_ids[0],
            }
        ),
    )
    report = FaultTrustOracle(
        sim,
        (forged,),
        injector_seed=injector.seed,
        adapter=adapter,
    ).inspect()
    assert any(
        "parameter request_id must be a non-empty string" in item
        for item in report.violations
    )
'''
tests_path.write_text(tests)
