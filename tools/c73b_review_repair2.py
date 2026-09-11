from pathlib import Path

p = Path("experiments/c73_retention_engine.py")
text = p.read_text()

start = text.index("        admits = {state_id: 0 for state_id in state_ids}\n")
end = text.index("\n    @property\n    def fingerprint", start)
replacement = '''        admit_events: dict[str, list[RetentionEvent]] = {
            state_id: [] for state_id in state_ids
        }
        for event in self.events:
            if event.kind is RetentionEventKind.ADMIT:
                admit_events[event.state_id].append(event)  # type: ignore[index]
        if any(not events for events in admit_events.values()):
            raise ValueError("every declared State must have at least one ADMIT event")

        state_by_id = {item.state_id: item for item in self.states}
        first_admits = sorted(
            (
                min(
                    events,
                    key=lambda event: (
                        event.time_seconds,
                        event.phase,
                        event.ordinal,
                        event.event_id,
                    ),
                )
                for events in admit_events.values()
            ),
            key=lambda event: (
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            ),
        )
        first_admit_key: dict[str, tuple[float, int, int, str]] = {}
        for expected_ordinal, event in enumerate(first_admits):
            state_id = event.state_id
            if state_by_id[state_id].admission_ordinal != expected_ordinal:  # type: ignore[index]
                raise ValueError(
                    "State admission_ordinal must match deterministic global first-ADMIT order"
                )
            first_admit_key[state_id] = (  # type: ignore[index]
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
            if event_key < first_admit_key[event.state_id]:  # type: ignore[index]
                raise ValueError(
                    f"{event.kind.value} cannot occur before first ADMIT for the same State"
                )
'''
text = text[:start] + replacement + text[end:]

start = text.index("    def _capacity_enforce(self, now: float, admitted_ids: tuple[str, ...]) -> None:\n")
end = text.index("\n    def _next_expiry", start)
replacement = '''    def _capacity_enforce(self, now: float, admitted_ids: tuple[str, ...]) -> None:
        pending_evictions: list[tuple[str, str]] = []
        if self.policy is RetentionPolicyID.SESSION_PINNING:
            if self.resident_bytes > self.manifest.capacity_bytes:
                raise AssertionError("session pinning overcommit escaped pre-admission infeasibility")
        elif self.policy in {RetentionPolicyID.LRU, RetentionPolicyID.FIXED_TTL}:
            while self.resident_bytes > self.manifest.capacity_bytes:
                state_id = min(
                    self.resident,
                    key=lambda sid: lru_eviction_key(
                        last_touch_time_seconds=self.resident[sid].last_touch_time_seconds,
                        admission_ordinal=self.resident[sid].admission_ordinal,
                        state_id=sid,
                    ),
                )
                self._close_interval(
                    state_id,
                    now,
                    ResidencyEndReason.EVICTION,
                    remove=True,
                )
                pending_evictions.append((state_id, "LRU_CAPACITY"))
        elif self.policy is RetentionPolicyID.LIFECYCLE_B4:
            if self._active_protected_bytes() > self.manifest.capacity_bytes:
                self._mark_infeasible(now, None, "B4_ACTIVE_PROTECTED_BYTES_EXCEED_CAPACITY")
            while self.resident_bytes > self.manifest.capacity_bytes:
                candidates = [
                    state_id
                    for state_id in self.resident
                    if self.truth[state_id].lifecycle is not StateLifecycle.ACTIVE
                ]
                if not candidates:
                    raise AssertionError("B4 protected overcapacity cannot be repaired by ordinary eviction")
                state_id = min(
                    candidates,
                    key=lambda sid: lifecycle_eviction_key(
                        lifecycle=self.truth[sid].lifecycle,
                        last_touch_time_seconds=self.resident[sid].last_touch_time_seconds,
                        admission_ordinal=self.resident[sid].admission_ordinal,
                        state_id=sid,
                    ),
                )
                lifecycle = self.truth[state_id].lifecycle.name
                self._close_interval(
                    state_id,
                    now,
                    ResidencyEndReason.EVICTION,
                    remove=True,
                )
                pending_evictions.append((state_id, f"B4_{lifecycle}_CAPACITY"))

        # Admission and capacity enforcement are one logical phase boundary. If
        # multiple same-time admissions require multiple evictions, mutate the
        # resident set to its final within-capacity state first, then emit the
        # per-State eviction audit records. No externally observable audit
        # snapshot therefore represents capacity overcommit.
        for state_id, reason in pending_evictions:
            self._record(now, RetentionAuditKind.EVICTED, state_id, reason)

        for state_id in admitted_ids:
            if state_id in self.resident:
                self._record(now, RetentionAuditKind.ADMITTED, state_id, "RESIDENT_AFTER_CAPACITY_ENFORCEMENT")
            elif not any(
                record.time_seconds == now
                and record.state_id == state_id
                and record.kind in {
                    RetentionAuditKind.ADMISSION_SKIPPED,
                    RetentionAuditKind.CAPACITY_INFEASIBLE,
                    RetentionAuditKind.EVICTED,
                }
                for record in self.audit
            ):
                self._record(now, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "NOT_RESIDENT_AFTER_CAPACITY_ENFORCEMENT")
'''
text = text[:start] + replacement + text[end:]

p.write_text(text)
