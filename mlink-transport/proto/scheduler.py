"""v1 scheduler: copy on every up path whose loss is within threshold."""

from __future__ import annotations

from collections.abc import Sequence

from proto.path import Path


def eligible_paths(paths: Sequence[Path], loss_threshold: float) -> list[Path]:
    """Paths that should carry an app-data copy.

    Down paths are skipped (probed separately). Up but lossy paths are
    skipped for data; heartbeats still go out on them.
    """
    return [p for p in paths if p.up and p.loss() <= loss_threshold]
