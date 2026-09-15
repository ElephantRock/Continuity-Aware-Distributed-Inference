from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
from pathlib import Path
import socket
import subprocess
import time
from typing import Mapping

from .c8_authority import authority_process_main
from .c8_events import read_events
from .c8_harness import harness_process_main
from .c8_worker import worker_process_main


class DirtyCheckoutError(RuntimeError):
    pass


def resolve_clean_checkout(repo_root: str | Path) -> str:
    """Resolve the executing git SHA and reject tracked/non-ignored untracked drift."""

    root = Path(repo_root)
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise DirtyCheckoutError("unable to resolve clean git checkout") from exc
    if len(head) != 40:
        raise DirtyCheckoutError("git HEAD is not a full 40-character SHA")
    if status.strip():
        raise DirtyCheckoutError("execution checkout is not clean")
    return head


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_event(
    path: str | Path,
    action: str,
    *,
    result: str | None = None,
    timeout_s: float = 8.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for event in read_events(path):
            if event.get("action") != action:
                continue
            if result is not None and event.get("result") != result:
                continue
            return event
        time.sleep(0.01)
    raise TimeoutError(f"event {action!r} was not observed in {path}")


@dataclass(frozen=True, slots=True)
class ReportedTopology:
    authority_pid: int
    harness_pid: int
    worker_pids: tuple[int, ...]

    @property
    def all_pids(self) -> tuple[int, ...]:
        return (self.authority_pid, self.harness_pid, *self.worker_pids)

    @property
    def all_distinct(self) -> bool:
        return len(self.all_pids) == len(set(self.all_pids))


class C8SubstrateRuntime:
    """Test/conformance orchestrator; semantic traffic still crosses TCP via harness."""

    def __init__(
        self,
        work_dir: str | Path,
        *,
        upstream_faults: Mapping[str, tuple[str, ...]] | None = None,
        downstream_faults: Mapping[str, tuple[str, ...]] | None = None,
        queue_capacity: int = 64,
        shutdown_worker_after_completion: bool = True,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.upstream_faults = dict(upstream_faults or {})
        self.downstream_faults = dict(downstream_faults or {})
        self.queue_capacity = queue_capacity
        self.shutdown_worker_after_completion = shutdown_worker_after_completion
        self.host = "127.0.0.1"
        self.authority_port = reserve_loopback_port()
        self.worker_port = reserve_loopback_port()
        while self.worker_port == self.authority_port:
            self.worker_port = reserve_loopback_port()

        self.authority_log = self.work_dir / "authority.jsonl"
        self.harness_log = self.work_dir / "harness.jsonl"
        self.worker_logs: list[Path] = []
        self._ctx = mp.get_context("spawn")
        self.authority: mp.Process | None = None
        self.harness: mp.Process | None = None
        self.worker: mp.Process | None = None
        self._worker_generation = 0

    def start(self, *, worker_id: str = "worker-1", work_delay_s: float = 0.0) -> None:
        if self.authority is not None or self.harness is not None:
            raise RuntimeError("runtime is already started")
        self.authority = self._ctx.Process(
            target=authority_process_main,
            args=(self.host, self.authority_port, self.authority_log),
            kwargs={"shutdown_worker_after_completion": self.shutdown_worker_after_completion},
            name="c8-authority",
        )
        self.authority.start()
        wait_for_event(self.authority_log, "LISTENING")

        self.harness = self._ctx.Process(
            target=harness_process_main,
            args=(
                self.host,
                self.authority_port,
                self.host,
                self.worker_port,
                self.harness_log,
                self.upstream_faults,
                self.downstream_faults,
                self.queue_capacity,
            ),
            name="c8-fault-harness",
        )
        self.harness.start()
        wait_for_event(self.harness_log, "WORKER_LISTENING")
        wait_for_event(self.authority_log, "HARNESS_REGISTERED")
        self.start_worker(worker_id=worker_id, work_delay_s=work_delay_s)

    def start_worker(self, *, worker_id: str, work_delay_s: float = 0.0) -> mp.Process:
        if self.harness is None or not self.harness.is_alive():
            raise RuntimeError("fault harness must be running before worker start")
        if self.worker is not None and self.worker.is_alive():
            raise RuntimeError("an active worker already exists")
        self._worker_generation += 1
        log_path = self.work_dir / f"worker-{self._worker_generation}.jsonl"
        self.worker_logs.append(log_path)
        worker = self._ctx.Process(
            target=worker_process_main,
            args=(self.host, self.worker_port, worker_id, log_path, work_delay_s),
            name=f"c8-worker-{self._worker_generation}",
        )
        worker.start()
        self.worker = worker
        wait_for_event(log_path, "PROCESS_STARTED")
        return worker

    def terminate_worker(self, *, timeout_s: float = 5.0) -> int:
        if self.worker is None:
            raise RuntimeError("no worker exists")
        pid = self.worker.pid
        if pid is None:
            raise RuntimeError("worker has no OS PID")
        if self.worker.is_alive():
            self.worker.terminate()
        self.worker.join(timeout_s)
        if self.worker.is_alive():
            self.worker.kill()
            self.worker.join(timeout_s)
        if self.worker.is_alive():
            raise RuntimeError("worker could not be terminated")
        return pid

    def restart_worker(
        self,
        *,
        worker_id: str = "worker-restarted",
        work_delay_s: float = 0.0,
    ) -> tuple[int, mp.Process]:
        old_pid = self.terminate_worker()
        wait_for_event(self.authority_log, "PROCESS_FAILURE_OBSERVED")
        new_worker = self.start_worker(worker_id=worker_id, work_delay_s=work_delay_s)
        if new_worker.pid == old_pid:
            raise RuntimeError("OS reused worker PID immediately; restart evidence is ambiguous")
        return old_pid, new_worker

    def reported_topology(self) -> ReportedTopology:
        authority_started = wait_for_event(self.authority_log, "PROCESS_STARTED")
        harness_started = wait_for_event(self.harness_log, "PROCESS_STARTED")
        worker_pids = tuple(
            int(wait_for_event(path, "PROCESS_STARTED")["pid"])
            for path in self.worker_logs
        )
        return ReportedTopology(
            authority_pid=int(authority_started["pid"]),
            harness_pid=int(harness_started["pid"]),
            worker_pids=worker_pids,
        )

    def stop(self, *, timeout_s: float = 3.0) -> None:
        for process in (self.worker, self.harness, self.authority):
            if process is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(timeout_s)
            if process.is_alive():
                process.kill()
                process.join(timeout_s)

    def __enter__(self) -> "C8SubstrateRuntime":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()
