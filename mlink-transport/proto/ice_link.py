"""WAN path whose socket is the ICE agent's nominated connection.

mlink send/recv call aioice. The peer address is ignored: on a TURN
nomination aioice encapsulates, and the relay port changes every run.
"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_mod
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from proto.config import PathConfig
from proto.sockets import UdpSocketFactory

log = logging.getLogger("mlink.ice")

_READY_TIMEOUT_S = 120.0


class IceStopped(Exception):
    """Ctrl-C before nomination."""


def _turn_root() -> Path:
    # mlink-transport/proto/ice_link.py -> sibling turn/ next to mlink-transport.
    root = Path(__file__).resolve().parents[2] / "turn"
    if not (root / "agent" / "session.py").is_file():
        raise RuntimeError(
            f"ICE agent not found at {root} "
            "(mlink-transport and turn must be sibling directories)"
        )
    return root


def _load_turn() -> tuple[Any, Any, Any]:
    root = str(_turn_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from agent.ice import format_path, load_config
    from agent.session import connect_agent

    return load_config, connect_agent, format_path


def _split_host_port(text: str) -> tuple[str, int] | None:
    host, sep, port_s = text.rpartition(":")
    if not sep or not host or not port_s.isdigit():
        return None
    port = int(port_s)
    if port < 1 or port > 65535:
        return None
    return host, port


class IceLink:
    """Datagram socket backed by a live ``IceAgent`` on its own event loop."""

    def __init__(self, ice_config: str, *, expect_role: str | None = None) -> None:
        self.ice_config = ice_config
        self.expect_role = expect_role
        self.nominated: dict[str, Any] = {}
        self._peer_addr = ("0.0.0.0", 9)
        self._inbox: queue_mod.Queue[bytes] = queue_mod.Queue()
        self._rx, self._tx = socket.socketpair()
        self._rx.setblocking(False)
        self._tx.setblocking(False)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._agent: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._closed = False
        self._close_started = False
        self._stop_recv: asyncio.Future[None] | None = None

    def start(self, stop: threading.Event | None = None) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name="mlink-ice", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while not self._ready.wait(0.2):
            if stop is not None and stop.is_set():
                self.close()
                raise IceStopped()
            if time.monotonic() >= deadline:
                self.close()
                raise TimeoutError("ICE nomination timed out after 120s")
        if self._error is not None:
            err = self._error
            self.close()
            log.error("ICE failed: %s", err)
            raise err

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        del addr  # nominated pair; aioice chooses the remote and TURN framing
        if self._closed or self._loop is None or self._agent is None:
            return 0
        fut = asyncio.run_coroutine_threadsafe(self._agent.send(data), self._loop)
        try:
            fut.result(timeout=1.0)
        except Exception as exc:
            log.warning("ice send failed: %s", exc)
            return 0
        return len(data)

    def recvfrom(self) -> tuple[bytes, tuple[str, int]] | None:
        self._drain_wake()
        try:
            data = self._inbox.get_nowait()
        except queue_mod.Empty:
            return None
        return data, self._peer_addr

    def fileno(self) -> int:
        if self._closed:
            return -1
        try:
            return self._rx.fileno()
        except OSError:
            return -1

    def close(self) -> None:
        if self._close_started:
            return
        self._close_started = True
        self._closed = True
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._fire_stop)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        for sock in (self._rx, self._tx):
            try:
                sock.close()
            except OSError:
                pass

    def _fire_stop(self) -> None:
        fut = self._stop_recv
        if fut is not None and not fut.done():
            fut.set_result(None)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._amain())
        except Exception as exc:
            self._error = exc
            self._ready.set()

    async def _amain(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_recv = self._loop.create_future()
        load_config, connect_agent, format_path = _load_turn()
        cfg_path = Path(self.ice_config)
        if not cfg_path.is_file():
            raise FileNotFoundError(f"ICE config not found: {cfg_path}")
        cfg = load_config(cfg_path)
        role = cfg.get("role")
        if self.expect_role is not None and role != self.expect_role:
            raise RuntimeError(
                f"ICE config role is {role}; this daemon requires {self.expect_role}"
            )
        try:
            self._agent = await connect_agent(cfg)
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return
        if self._closed:
            self._ready.set()
            await self._agent.close()
            self._agent = None
            return
        info = self._agent.path()
        self.nominated = info
        remote = _split_host_port(str(info.get("remote") or ""))
        if remote is not None:
            self._peer_addr = remote
        log.info("nominated %s", format_path(info))
        self._ready.set()
        try:
            while not self._closed:
                assert self._stop_recv is not None
                recv_task = asyncio.create_task(self._agent.recv())
                done, _pending = await asyncio.wait(
                    {recv_task, self._stop_recv},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._stop_recv in done or self._closed:
                    recv_task.cancel()
                    try:
                        await recv_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    break
                try:
                    data = recv_task.result()
                except Exception as exc:
                    if not self._closed:
                        log.warning("ice recv ended: %s", exc)
                    break
                self._inbox.put(data)
                try:
                    self._tx.send(b"\x00")
                except OSError:
                    pass
        finally:
            if self._agent is not None:
                await self._agent.close()
                self._agent = None

    def _drain_wake(self) -> None:
        while True:
            try:
                if not self._rx.recv(4096):
                    return
            except (BlockingIOError, OSError):
                return


class IceSocketFactory:
    """UDP paths stay on ``UdpSocketFactory``. ``transport: ice`` nominates first."""

    def __init__(
        self,
        *,
        expect_role: str | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        self._udp = UdpSocketFactory()
        self._links: list[IceLink] = []
        self._expect_role = expect_role
        self._stop = stop

    def create(self, path: PathConfig) -> Any:
        if path.transport != "ice":
            return self._udp.create(path)
        link = IceLink(path.ice_config, expect_role=self._expect_role)
        try:
            link.start(self._stop)
        except Exception:
            link.close()
            raise
        self._links.append(link)
        return link

    def close(self) -> None:
        for link in self._links:
            link.close()
        self._links.clear()
        self._udp.close()
