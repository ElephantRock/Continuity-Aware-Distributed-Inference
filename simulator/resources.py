from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Optional

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent


class WorkerStatus(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReplicaRuntimeStatus(str, Enum):
    MATERIALIZING = "MATERIALIZING"
    AVAILABLE = "AVAILABLE"
    EVICTED = "EVICTED"
    LOST = "LOST"


class TransferStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Worker:
    id: str
    capacity: int = 1
    status: WorkerStatus = WorkerStatus.UP


@dataclass(frozen=True, slots=True)
class ResourceTask:
    id: str
    worker_id: str
    duration: float
    status: TaskStatus
    enqueued_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    completion_event_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class NetworkLink:
    id: str
    source_id: str
    destination_id: str
    latency: float
    bandwidth_bytes_per_time: float

    def transfer_duration(self, size_bytes: int) -> float:
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        return self.latency + (size_bytes / self.bandwidth_bytes_per_time)


@dataclass(frozen=True, slots=True)
class ReplicaRuntime:
    """Non-authoritative physical runtime shadow of a C1 StateReplica identity."""

    replica_id: str
    state_id: str
    location_id: str
    size_bytes: int
    status: ReplicaRuntimeStatus
    completion_event_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StateTransfer:
    id: str
    replica_id: str
    link_id: str
    source_id: str
    destination_id: str
    size_bytes: int
    status: TransferStatus
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    completion_event_id: Optional[str] = None


class ResourceModel:
    """Deterministic physical-resource state layered around the C2 event engine.

    This model intentionally does not own or mutate C1 semantic state. ReplicaRuntime
    records reference semantic State/Replica IDs but are only simulated physical facts.
    """

    def __init__(self, simulator: DiscreteEventSimulator) -> None:
        if not isinstance(simulator, DiscreteEventSimulator):
            raise TypeError("simulator must be DiscreteEventSimulator")
        self.simulator = simulator
        self.workers: dict[str, Worker] = {}
        self.links: dict[str, NetworkLink] = {}
        self.tasks: dict[str, ResourceTask] = {}
        self.worker_queues: dict[str, list[str]] = {}
        self.worker_active: dict[str, list[str]] = {}
        self.replicas: dict[str, ReplicaRuntime] = {}
        self.transfers: dict[str, StateTransfer] = {}

        simulator.register_handler(EventKind.WORKER_TASK_ENQUEUED, self._on_task_enqueued)
        simulator.register_handler(EventKind.WORKER_TASK_COMPLETED, self._on_task_completed)
        simulator.register_handler(EventKind.WORKER_FAILED, self._on_worker_failed)
        simulator.register_handler(EventKind.WORKER_RECOVERED, self._on_worker_recovered)
        simulator.register_handler(EventKind.STATE_MATERIALIZATION_STARTED, self._on_materialization_started)
        simulator.register_handler(EventKind.STATE_MATERIALIZED, self._on_state_materialized)
        simulator.register_handler(EventKind.STATE_TRANSFER_STARTED, self._on_transfer_started)
        simulator.register_handler(EventKind.STATE_TRANSFER_COMPLETED, self._on_transfer_completed)
        simulator.register_handler(EventKind.STATE_EVICTED, self._on_state_evicted)
        simulator.register_handler(EventKind.STATE_LOST, self._on_state_lost)

    def add_worker(self, worker_id: str, *, capacity: int = 1) -> Worker:
        self._require_id(worker_id, "worker_id")
        if worker_id in self.workers:
            raise ValueError(f"duplicate worker_id: {worker_id}")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        worker = Worker(worker_id, capacity)
        self.workers[worker_id] = worker
        self.worker_queues[worker_id] = []
        self.worker_active[worker_id] = []
        return worker

    def add_link(
        self,
        link_id: str,
        source_id: str,
        destination_id: str,
        *,
        latency: float,
        bandwidth_bytes_per_time: float,
    ) -> NetworkLink:
        self._require_id(link_id, "link_id")
        self._require_worker(source_id)
        self._require_worker(destination_id)
        if link_id in self.links:
            raise ValueError(f"duplicate link_id: {link_id}")
        latency_value = self._finite_nonnegative(latency, "latency")
        bandwidth_value = self._finite_positive(bandwidth_bytes_per_time, "bandwidth_bytes_per_time")
        link = NetworkLink(link_id, source_id, destination_id, latency_value, bandwidth_value)
        self.links[link_id] = link
        return link

    def enqueue_task(self, worker_id: str, task_id: str, *, duration: float) -> ResourceTask:
        self._require_worker(worker_id)
        self._require_id(task_id, "task_id")
        if task_id in self.tasks:
            raise ValueError(f"duplicate task_id: {task_id}")
        duration_value = self._finite_nonnegative(duration, "duration")
        task = ResourceTask(task_id, worker_id, duration_value, TaskStatus.QUEUED, self.simulator.now)
        self.tasks[task_id] = task
        self.worker_queues[worker_id].append(task_id)
        try:
            self.simulator.schedule(
                EventKind.WORKER_TASK_ENQUEUED,
                delay=0,
                event_id=f"task-enqueued:{task_id}",
                payload={"task_id": task_id, "worker_id": worker_id},
            )
        except Exception:
            self.worker_queues[worker_id].remove(task_id)
            del self.tasks[task_id]
            raise
        return task

    def fail_worker(self, worker_id: str) -> SimEvent:
        self._require_worker(worker_id)
        return self.simulator.schedule(
            EventKind.WORKER_FAILED,
            delay=0,
            payload={"worker_id": worker_id},
        )

    def recover_worker(self, worker_id: str) -> SimEvent:
        self._require_worker(worker_id)
        return self.simulator.schedule(
            EventKind.WORKER_RECOVERED,
            delay=0,
            payload={"worker_id": worker_id},
        )

    def materialize_replica(
        self,
        replica_id: str,
        state_id: str,
        location_id: str,
        *,
        size_bytes: int,
        duration: float,
    ) -> ReplicaRuntime:
        self._require_id(replica_id, "replica_id")
        self._require_id(state_id, "state_id")
        self._require_worker(location_id)
        if replica_id in self.replicas:
            raise ValueError(f"duplicate replica_id: {replica_id}")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        duration_value = self._finite_nonnegative(duration, "duration")
        replica = ReplicaRuntime(
            replica_id,
            state_id,
            location_id,
            size_bytes,
            ReplicaRuntimeStatus.MATERIALIZING,
        )
        self.replicas[replica_id] = replica
        try:
            self.simulator.schedule(
                EventKind.STATE_MATERIALIZATION_STARTED,
                delay=0,
                event_id=f"materialize-start:{replica_id}",
                payload={"replica_id": replica_id, "duration": duration_value},
            )
        except Exception:
            del self.replicas[replica_id]
            raise
        return replica

    def start_transfer(self, transfer_id: str, replica_id: str, link_id: str) -> StateTransfer:
        self._require_id(transfer_id, "transfer_id")
        if transfer_id in self.transfers:
            raise ValueError(f"duplicate transfer_id: {transfer_id}")
        replica = self._require_replica(replica_id)
        if replica.status is not ReplicaRuntimeStatus.AVAILABLE:
            raise ValueError("replica must be AVAILABLE to start transfer")
        link = self._require_link(link_id)
        if replica.location_id != link.source_id:
            raise ValueError("replica location must match link source")
        transfer = StateTransfer(
            transfer_id,
            replica_id,
            link_id,
            link.source_id,
            link.destination_id,
            replica.size_bytes,
            TransferStatus.SCHEDULED,
        )
        self.transfers[transfer_id] = transfer
        try:
            self.simulator.schedule(
                EventKind.STATE_TRANSFER_STARTED,
                delay=0,
                event_id=f"transfer-start:{transfer_id}",
                payload={"transfer_id": transfer_id},
            )
        except Exception:
            del self.transfers[transfer_id]
            raise
        return transfer

    def evict_replica(self, replica_id: str) -> SimEvent:
        self._require_replica(replica_id)
        return self.simulator.schedule(
            EventKind.STATE_EVICTED,
            delay=0,
            payload={"replica_id": replica_id},
        )

    def lose_replica(self, replica_id: str) -> SimEvent:
        self._require_replica(replica_id)
        return self.simulator.schedule(
            EventKind.STATE_LOST,
            delay=0,
            payload={"replica_id": replica_id},
        )

    def _on_task_enqueued(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        worker_id = dict(event.payload)["worker_id"]
        self._dispatch(worker_id)

    def _dispatch(self, worker_id: str) -> None:
        worker = self.workers[worker_id]
        queue = self.worker_queues[worker_id]
        active = self.worker_active[worker_id]
        while worker.status is WorkerStatus.UP and queue and len(active) < worker.capacity:
            task_id = queue.pop(0)
            task = self.tasks[task_id]
            completion = self.simulator.schedule(
                EventKind.WORKER_TASK_COMPLETED,
                delay=task.duration,
                event_id=f"task-complete:{task_id}",
                payload={"task_id": task_id, "worker_id": worker_id},
            )
            self.tasks[task_id] = replace(
                task,
                status=TaskStatus.RUNNING,
                started_at=self.simulator.now,
                completion_event_id=completion.event_id,
            )
            active.append(task_id)

    def _on_task_completed(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        payload = dict(event.payload)
        task_id = payload["task_id"]
        worker_id = payload["worker_id"]
        task = self.tasks.get(task_id)
        if task is None or task.status is not TaskStatus.RUNNING:
            return
        active = self.worker_active[worker_id]
        if task_id in active:
            active.remove(task_id)
        self.tasks[task_id] = replace(task, status=TaskStatus.COMPLETED, completed_at=self.simulator.now)
        self._dispatch(worker_id)

    def _on_worker_failed(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        worker_id = dict(event.payload)["worker_id"]
        worker = self.workers[worker_id]
        if worker.status is WorkerStatus.DOWN:
            return
        self.workers[worker_id] = replace(worker, status=WorkerStatus.DOWN)

        active = list(self.worker_active[worker_id])
        self.worker_active[worker_id].clear()
        for task_id in active:
            task = self.tasks[task_id]
            if task.completion_event_id:
                self.simulator.cancel(task.completion_event_id)
            self.tasks[task_id] = replace(task, status=TaskStatus.FAILED, completed_at=self.simulator.now)
            self.simulator.schedule(
                EventKind.WORKER_TASK_FAILED,
                delay=0,
                payload={"task_id": task_id, "worker_id": worker_id},
            )

        for replica_id, replica in list(self.replicas.items()):
            if replica.location_id != worker_id or replica.status in {ReplicaRuntimeStatus.EVICTED, ReplicaRuntimeStatus.LOST}:
                continue
            if replica.completion_event_id:
                self.simulator.cancel(replica.completion_event_id)
            self.replicas[replica_id] = replace(replica, status=ReplicaRuntimeStatus.LOST, completion_event_id=None)
            self.simulator.schedule(
                EventKind.STATE_LOST,
                delay=0,
                payload={"replica_id": replica_id},
            )

        for transfer_id, transfer in list(self.transfers.items()):
            if transfer.status is not TransferStatus.RUNNING:
                continue
            if worker_id not in {transfer.source_id, transfer.destination_id}:
                continue
            if transfer.completion_event_id:
                self.simulator.cancel(transfer.completion_event_id)
            self.transfers[transfer_id] = replace(
                transfer,
                status=TransferStatus.FAILED,
                completed_at=self.simulator.now,
            )
            self.simulator.schedule(
                EventKind.STATE_TRANSFER_FAILED,
                delay=0,
                payload={"transfer_id": transfer_id},
            )

    def _on_worker_recovered(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        worker_id = dict(event.payload)["worker_id"]
        worker = self.workers[worker_id]
        if worker.status is WorkerStatus.UP:
            return
        self.workers[worker_id] = replace(worker, status=WorkerStatus.UP)
        self._dispatch(worker_id)

    def _on_materialization_started(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        payload = dict(event.payload)
        replica_id = payload["replica_id"]
        duration = payload["duration"]
        replica = self.replicas.get(replica_id)
        if replica is None or replica.status is not ReplicaRuntimeStatus.MATERIALIZING:
            return
        if self.workers[replica.location_id].status is WorkerStatus.DOWN:
            self.replicas[replica_id] = replace(replica, status=ReplicaRuntimeStatus.LOST)
            self.simulator.schedule(EventKind.STATE_LOST, delay=0, payload={"replica_id": replica_id})
            return
        completion = self.simulator.schedule(
            EventKind.STATE_MATERIALIZED,
            delay=duration,
            event_id=f"materialize-complete:{replica_id}",
            payload={"replica_id": replica_id},
        )
        self.replicas[replica_id] = replace(replica, completion_event_id=completion.event_id)

    def _on_state_materialized(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        replica_id = dict(event.payload)["replica_id"]
        replica = self.replicas.get(replica_id)
        if replica is None or replica.status is not ReplicaRuntimeStatus.MATERIALIZING:
            return
        if self.workers[replica.location_id].status is WorkerStatus.DOWN:
            self.replicas[replica_id] = replace(replica, status=ReplicaRuntimeStatus.LOST, completion_event_id=None)
            self.simulator.schedule(EventKind.STATE_LOST, delay=0, payload={"replica_id": replica_id})
            return
        self.replicas[replica_id] = replace(
            replica,
            status=ReplicaRuntimeStatus.AVAILABLE,
            completion_event_id=None,
        )

    def _on_transfer_started(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        transfer_id = dict(event.payload)["transfer_id"]
        transfer = self.transfers.get(transfer_id)
        if transfer is None or transfer.status is not TransferStatus.SCHEDULED:
            return
        replica = self.replicas[transfer.replica_id]
        if (
            replica.status is not ReplicaRuntimeStatus.AVAILABLE
            or replica.location_id != transfer.source_id
            or self.workers[transfer.source_id].status is WorkerStatus.DOWN
            or self.workers[transfer.destination_id].status is WorkerStatus.DOWN
        ):
            self.transfers[transfer_id] = replace(
                transfer,
                status=TransferStatus.FAILED,
                started_at=self.simulator.now,
                completed_at=self.simulator.now,
            )
            self.simulator.schedule(EventKind.STATE_TRANSFER_FAILED, delay=0, payload={"transfer_id": transfer_id})
            return
        link = self.links[transfer.link_id]
        completion = self.simulator.schedule(
            EventKind.STATE_TRANSFER_COMPLETED,
            delay=link.transfer_duration(transfer.size_bytes),
            event_id=f"transfer-complete:{transfer_id}",
            payload={"transfer_id": transfer_id},
        )
        self.transfers[transfer_id] = replace(
            transfer,
            status=TransferStatus.RUNNING,
            started_at=self.simulator.now,
            completion_event_id=completion.event_id,
        )

    def _on_transfer_completed(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        transfer_id = dict(event.payload)["transfer_id"]
        transfer = self.transfers.get(transfer_id)
        if transfer is None or transfer.status is not TransferStatus.RUNNING:
            return
        replica = self.replicas[transfer.replica_id]
        if (
            replica.status is not ReplicaRuntimeStatus.AVAILABLE
            or replica.location_id != transfer.source_id
            or self.workers[transfer.source_id].status is WorkerStatus.DOWN
            or self.workers[transfer.destination_id].status is WorkerStatus.DOWN
        ):
            self.transfers[transfer_id] = replace(
                transfer,
                status=TransferStatus.FAILED,
                completed_at=self.simulator.now,
                completion_event_id=None,
            )
            self.simulator.schedule(EventKind.STATE_TRANSFER_FAILED, delay=0, payload={"transfer_id": transfer_id})
            return
        self.replicas[transfer.replica_id] = replace(replica, location_id=transfer.destination_id)
        self.transfers[transfer_id] = replace(
            transfer,
            status=TransferStatus.COMPLETED,
            completed_at=self.simulator.now,
            completion_event_id=None,
        )
        self.simulator.schedule(
            EventKind.STATE_MOVED,
            delay=0,
            payload={
                "replica_id": transfer.replica_id,
                "source_id": transfer.source_id,
                "destination_id": transfer.destination_id,
            },
        )

    def _on_state_evicted(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        replica_id = dict(event.payload)["replica_id"]
        replica = self.replicas.get(replica_id)
        if replica is None or replica.status is ReplicaRuntimeStatus.LOST:
            return
        if replica.completion_event_id:
            self.simulator.cancel(replica.completion_event_id)
        self.replicas[replica_id] = replace(
            replica,
            status=ReplicaRuntimeStatus.EVICTED,
            completion_event_id=None,
        )

    def _on_state_lost(self, _sim: DiscreteEventSimulator, event: SimEvent) -> None:
        replica_id = dict(event.payload)["replica_id"]
        replica = self.replicas.get(replica_id)
        if replica is None or replica.status is ReplicaRuntimeStatus.LOST:
            return
        if replica.completion_event_id:
            self.simulator.cancel(replica.completion_event_id)
        self.replicas[replica_id] = replace(
            replica,
            status=ReplicaRuntimeStatus.LOST,
            completion_event_id=None,
        )

    def _require_worker(self, worker_id: str) -> Worker:
        worker = self.workers.get(worker_id)
        if worker is None:
            raise KeyError(f"unknown worker: {worker_id}")
        return worker

    def _require_link(self, link_id: str) -> NetworkLink:
        link = self.links.get(link_id)
        if link is None:
            raise KeyError(f"unknown link: {link_id}")
        return link

    def _require_replica(self, replica_id: str) -> ReplicaRuntime:
        replica = self.replicas.get(replica_id)
        if replica is None:
            raise KeyError(f"unknown replica: {replica_id}")
        return replica

    @staticmethod
    def _require_id(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _finite_nonnegative(value: float, name: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{name} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return numeric

    @staticmethod
    def _finite_positive(value: float, name: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{name} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{name} must be finite and positive")
        return numeric
