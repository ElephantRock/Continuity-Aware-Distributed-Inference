from pathlib import Path

p = Path("experiments/c73_retention_engine.py")
text = p.read_text()

old = '''        self.specs = {item.state_id: item for item in case.states}
        self.truth = {item.state_id: _Truth(True, item.initial_lifecycle) for item in case.states}
        self.sessions = {item.session_id: item.initially_live for item in case.sessions}
'''
new = '''        self.specs = {item.state_id: item for item in case.states}
        self.truth = {item.state_id: _Truth(True, item.initial_lifecycle) for item in case.states}
        self.sessions = {item.session_id: item.initially_live for item in case.sessions}
        ordered_admissions = sorted(
            (event for event in case.events if event.kind is RetentionEventKind.ADMIT),
            key=lambda event: (
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            ),
        )
        self.admission_occurrence_ordinals = {
            event.event_id: ordinal
            for ordinal, event in enumerate(ordered_admissions)
        }
'''
if text.count(old) != 1:
    raise SystemExit("run-init anchor drift")
text = text.replace(old, new)

old = '''        truth.produced = True
        if not truth.valid:
            self._record(event.time_seconds, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "INVALID_STATE")
            return
        if state_id in self.resident:
            raise ValueError("State cannot be admitted while already resident")
        if self.policy in {RetentionPolicyID.LRU, RetentionPolicyID.FIXED_TTL}:
'''
new = '''        truth.produced = True
        if not truth.valid:
            self._record(event.time_seconds, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "INVALID_STATE")
            return
        if state_id in self.resident:
            # ADMIT is a common admission/re-admission opportunity in the shared
            # Program event stream. Policies that already retain the immutable
            # State consume no new admission and do not refresh recency or TTL.
            self._record(
                event.time_seconds,
                RetentionAuditKind.ADMISSION_SKIPPED,
                state_id,
                "ALREADY_RESIDENT",
            )
            return
        if self.policy in {RetentionPolicyID.LRU, RetentionPolicyID.FIXED_TTL}:
'''
if text.count(old) != 1:
    raise SystemExit("admit resident anchor drift")
text = text.replace(old, new)

old = '''            spec.admission_ordinal,
            expiry,
        )
'''
new = '''            self.admission_occurrence_ordinals[event.event_id],
            expiry,
        )
'''
if text.count(old) != 1:
    raise SystemExit("resident admission ordinal anchor drift")
text = text.replace(old, new)

p.write_text(text)
