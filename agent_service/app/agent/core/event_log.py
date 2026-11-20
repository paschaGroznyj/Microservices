"""Daily private JSONL; UTC calendar retention, disk budget wins over age.

Linux flock serializes workers sharing a directory. Open on every write so a
worker never keeps writing to an inode another worker has pruned.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time

_DAILY = re.compile(r'^\d{2}_\d{2}_\d{4}\.jsonl$')
_LEGACY = re.compile(r'^events\.jsonl(?:\.\d+)?$')


class DailyEventHandler(logging.Handler):
    def __init__(self, directory, *, days=7, max_bytes=500 * 1024 * 1024,
                 cleanup_interval=60, clock=time.time):
        super().__init__()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.days = max(1, int(days))
        self.max_bytes = max(1, int(max_bytes))
        self.clock = clock
        self._stop = threading.Event()
        self._worker = None
        # Private lock from first creation; never unlink while workers use it.
        self._lock_fd = os.open(self.directory / '.events.lock',
                                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self.cleanup()
        except BaseException:
            os.close(self._lock_fd)
            raise
        if cleanup_interval > 0:
            self._worker = threading.Thread(target=self._maintain,
                args=(cleanup_interval,), name='agent-log-retention', daemon=True)
            self._worker.start()

    @contextmanager
    def _locked(self):
        with self.lock:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def _files(self):
        # Never remove unrelated files or follow symlinks.
        return [p for p in self.directory.iterdir()
                if (_DAILY.fullmatch(p.name) or _LEGACY.fullmatch(p.name))
                and not p.is_symlink() and p.is_file()]

    def _day(self, path):
        if _DAILY.fullmatch(path.name):
            try:
                return datetime.strptime(path.stem, '%d_%m_%Y').date()
            except ValueError:
                pass
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()

    def _prune(self, now, incoming=0, active=None):
        cutoff = datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=self.days - 1)
        files = self._files()
        for p in files:
            if self._day(p) < cutoff:
                p.unlink(missing_ok=True)
        files = sorted(self._files(), key=lambda p: (self._day(p), p.stat().st_mtime, p.name))
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total + incoming <= self.max_bytes:
                break
            if p == active:
                continue
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
        # One busy day may exceed the entire budget: keep its newest complete
        # records, using a streaming copy, not a 500 MiB in-memory buffer.
        if active is not None and active.exists() and total + incoming > self.max_bytes:
            if active.is_symlink():
                raise OSError('event log must not be a symlink')
            # Reserve 10% headroom: do not copy a nearly 500 MiB day on every
            # subsequent tiny record once the budget has been reached.
            self._trim(active, max(0, self.max_bytes - incoming - self.max_bytes // 10))

    def _trim(self, path, keep_bytes):
        size = path.stat().st_size
        fd, name = tempfile.mkstemp(prefix='.events-trim-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as dst, path.open('rb') as src:
                src.seek(max(0, size - keep_bytes))
                if src.tell():
                    src.readline()  # discard partial/oldest record
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def cleanup(self):
        with self._locked():
            self._prune(self.clock())

    def _maintain(self, interval):
        while not self._stop.wait(interval):
            try:
                self.cleanup()
            except OSError:
                # Never recurse through logging from its own retention thread.
                pass

    def emit(self, record):
        try:
            raw = (self.format(record) + '\n').encode('utf-8')
            if len(raw) > self.max_bytes:
                return  # never split JSON, never exceed the configured budget
            now = self.clock()
            path = self.directory / (datetime.fromtimestamp(now, timezone.utc).strftime('%d_%m_%Y') + '.jsonl')
            with self._locked():
                self._prune(now, len(raw), path)
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'ab') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
        except Exception:
            self.handleError(record)

    def close(self):
        self._stop.set()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=2)
        with self.lock:
            fd = getattr(self, '_lock_fd', None)
            if fd is not None:
                os.close(fd)
                self._lock_fd = None
        super().close()
