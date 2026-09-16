from __future__ import annotations

import pytest

from prototype.c8_harness import WorkerSocketSendClosed, _sendall_or_worker_closed


class _FakeSocket:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.frames: list[bytes] = []

    def sendall(self, frame: bytes) -> None:
        if self.error is not None:
            raise self.error
        self.frames.append(frame)


@pytest.mark.parametrize(
    "error",
    [
        BrokenPipeError("broken"),
        ConnectionResetError("reset"),
        ConnectionAbortedError("aborted"),
    ],
)
def test_worker_send_peer_close_is_normalized(error: BaseException) -> None:
    sock = _FakeSocket(error)
    with pytest.raises(WorkerSocketSendClosed) as exc_info:
        _sendall_or_worker_closed(sock, b"frame")
    assert type(error).__name__ in str(exc_info.value)


def test_worker_send_success_is_unchanged() -> None:
    sock = _FakeSocket()
    _sendall_or_worker_closed(sock, b"frame")
    assert sock.frames == [b"frame"]
