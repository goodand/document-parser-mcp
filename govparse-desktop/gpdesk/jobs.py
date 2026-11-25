"""Document jobs: every open gets a job id bound to the source SHA-256.

Background results carry (job_id, sha256). Only the current job may change the
visible document; results of older jobs stay attributed to their own job.
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from dataclasses import dataclass, field
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Job:
    job_id: str
    source: Path
    sha256: str
    display_name: str
    state: dict = field(default_factory=dict)


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._current: str | None = None
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def open(self, source: Path) -> Job:
        source = Path(source)
        digest = sha256_file(source)
        with self._lock:
            job_id = f"j{next(self._seq):04d}-{digest[:8]}"
            job = Job(job_id, source, digest, source.name)
            self._jobs[job_id] = job
            self._current = job_id
        return job

    def get(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def is_current(self, job_id: str) -> bool:
        return job_id == self._current

    @property
    def current(self) -> Job | None:
        return self._jobs.get(self._current) if self._current else None

    def accept(self, job_id: str, sha256: str) -> bool:
        """True only if a result may update the visible document."""
        job = self._jobs.get(job_id)
        return job is not None and self.is_current(job_id) and job.sha256 == sha256
