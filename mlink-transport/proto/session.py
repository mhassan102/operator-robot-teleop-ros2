"""One mlink endpoint: send copies, recv first-good, heartbeats, probes."""

from __future__ import annotations

from collections import defaultdict, deque

from proto.clock import Clock
from proto.config import MlinkConfig
from proto.dedupe import DedupeWindow
from proto.header import (
    FLAG_ECHO,
    FLAG_HEARTBEAT,
    FLAG_PROBE,
    MAX_PAYLOAD,
    TC_CONTROL,
    TC_MEDIA,
    HeaderError,
    Packet,
    PayloadTooLarge,
    decode,
    encode,
)
from proto.path import Path
from proto.scheduler import eligible_paths
from proto.sockets import SocketFactory


class MlinkSession:
    def __init__(
        self,
        config: MlinkConfig,
        clock: Clock,
        socket_factory: SocketFactory,
    ) -> None:
        self.config = config
        self.clock = clock
        now = clock.monotonic_us()
        # First tick should emit heartbeats/probes immediately.
        self.paths: list[Path] = []
        for i, pc in enumerate(config.paths):
            sock = socket_factory.create(pc)
            path = Path(
                i,
                pc,
                sock,
                now_us=now,
                loss_window=config.loss_window,
                rtt_alpha=config.rtt_alpha,
            )
            path.last_hb_tx_us = now - config.heartbeat_interval_us
            path.last_probe_tx_us = now - config.probe_interval_us
            self.paths.append(path)
        self.dedupe = DedupeWindow(config.dedupe_window)
        self._seq = 0
        self._q_control: deque[tuple[bytes, int]] = deque()
        self._q_media: deque[tuple[bytes, int]] = deque()

    @property
    def session_id(self) -> int:
        return self.config.session_id

    def path(self, name: str) -> Path:
        for p in self.paths:
            if p.name == name:
                return p
        raise KeyError(name)

    def send(self, payload: bytes, traffic_class: int = TC_CONTROL) -> int | None:
        """Queue and flush one app datagram. Returns the data seq, or None if dropped.

        Media may be dropped if the media queue is full. Control is not
        dropped for queue length. Drain always sends control first so
        media never blocks it.
        """
        if len(payload) > self.config.max_payload or len(payload) > MAX_PAYLOAD:
            raise PayloadTooLarge(
                f"payload {len(payload)} bytes exceeds max {self.config.max_payload}"
            )
        if traffic_class not in (TC_CONTROL, TC_MEDIA):
            raise ValueError(f"bad traffic_class {traffic_class}")
        if traffic_class == TC_MEDIA:
            if len(self._q_media) >= self.config.max_media_queue:
                return None
            self._q_media.append((payload, traffic_class))
        else:
            self._q_control.append((payload, traffic_class))
        seqs = self.flush()
        return seqs[-1] if seqs else None

    def flush(self) -> list[int]:
        seqs: list[int] = []
        while self._q_control:
            payload, tc = self._q_control.popleft()
            seqs.append(self._emit(payload, tc))
        while self._q_media:
            payload, tc = self._q_media.popleft()
            seqs.append(self._emit(payload, tc))
        return seqs

    def enqueue(self, payload: bytes, traffic_class: int = TC_CONTROL) -> None:
        """Queue without sending (tests control vs media drain order)."""
        if len(payload) > self.config.max_payload:
            raise PayloadTooLarge(
                f"payload {len(payload)} bytes exceeds max {self.config.max_payload}"
            )
        if traffic_class == TC_MEDIA:
            if len(self._q_media) >= self.config.max_media_queue:
                return
            self._q_media.append((payload, traffic_class))
        else:
            self._q_control.append((payload, traffic_class))

    def tick(self) -> None:
        """Send due heartbeats/probes and apply the down timeout."""
        now = self.clock.monotonic_us()
        timeout = self.config.down_timeout_us
        for path in self.paths:
            if now - path.last_heard_us >= timeout:
                path.up = False
            if path.up:
                if now - path.last_hb_tx_us >= self.config.heartbeat_interval_us:
                    self._send_heartbeat(path, probe=False)
                    path.last_hb_tx_us = now
            else:
                if now - path.last_probe_tx_us >= self.config.probe_interval_us:
                    self._send_heartbeat(path, probe=True)
                    path.last_probe_tx_us = now

    def poll(self) -> list[bytes]:
        """Read every socket, process frames, return newly delivered payloads.

        Heartbeats/probes update path stats and are not delivered.
        Data copies of the same seq are grouped so loss accounting sees
        every copy that arrived in this poll before recording misses.
        First good copy is delivered immediately; no reorder hold.
        """
        incoming: list[tuple[Path, bytes]] = []
        for path in self.paths:
            while True:
                recvd = path.socket.recvfrom()
                if recvd is None:
                    break
                incoming.append((path, recvd[0]))

        decoded: list[tuple[Path, Packet]] = []
        for path, raw in incoming:
            try:
                pkt = decode(raw)
            except HeaderError:
                continue
            if pkt.session_id != self.session_id:
                continue
            decoded.append((path, pkt))

        now = self.clock.monotonic_us()
        data_pkts: list[tuple[Path, Packet]] = []
        for path, pkt in decoded:
            path.mark_heard(now)
            if pkt.is_control_plane:
                if pkt.is_echo:
                    path.observe_rtt(now - pkt.timestamp_us)
                else:
                    path.observe_heartbeat(pkt.seq)
                    self._send_echo(path, pkt)
            else:
                data_pkts.append((path, pkt))

        by_seq: dict[int, list[tuple[Path, Packet]]] = defaultdict(list)
        for path, pkt in data_pkts:
            by_seq[pkt.seq].append((path, pkt))

        delivered: list[bytes] = []
        # Preserve first-seen order of seqs in this poll.
        for seq, copies in by_seq.items():
            got_ids = {p.path_id for p, _ in copies}
            for path in self.paths:
                if path.up:
                    path.observe_data_outcome(path.path_id in got_ids)
            first_pkt = copies[0][1]
            if self.dedupe.observe(seq) == "deliver":
                delivered.append(first_pkt.payload)
        return delivered

    def _emit(self, payload: bytes, traffic_class: int) -> int:
        self._seq += 1
        seq = self._seq
        now = self.clock.monotonic_us()
        for path in eligible_paths(self.paths, self.config.loss_threshold):
            pkt = Packet(
                flags=0,
                traffic_class=traffic_class,
                path_id=path.path_id,
                session_id=self.session_id,
                seq=seq,
                timestamp_us=now,
                payload=payload,
            )
            path.socket.sendto(encode(pkt), path.peer_addr)
        return seq

    def _send_heartbeat(self, path: Path, *, probe: bool) -> None:
        path.hb_seq += 1
        flags = FLAG_HEARTBEAT
        if probe:
            flags |= FLAG_PROBE
        pkt = Packet(
            flags=flags,
            traffic_class=TC_CONTROL,
            path_id=path.path_id,
            session_id=self.session_id,
            seq=path.hb_seq,
            timestamp_us=self.clock.monotonic_us(),
            payload=b"",
        )
        path.socket.sendto(encode(pkt), path.peer_addr)

    def _send_echo(self, path: Path, original: Packet) -> None:
        flags = FLAG_HEARTBEAT | FLAG_ECHO
        if original.is_probe:
            flags |= FLAG_PROBE
        pkt = Packet(
            flags=flags,
            traffic_class=TC_CONTROL,
            path_id=path.path_id,
            session_id=self.session_id,
            seq=original.seq,
            timestamp_us=original.timestamp_us,
            payload=b"",
        )
        path.socket.sendto(encode(pkt), path.peer_addr)
