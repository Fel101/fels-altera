"""Coordinate application requests/background writers with consistent snapshots.

Leases cross thread boundaries: reserve before spawning, release after all writes.
Maintenance refuses to start while work is active and refuses new work while it runs.
"""
from contextlib import contextmanager
import math
import os
import threading
import time
import uuid



class StateBusy(RuntimeError):
    def __init__(self, message="Altera is updating saved data. Please try again shortly.", status=503):
        super().__init__(message)
        self.status = status


class Lease:
    def __init__(self, gate, key):
        self.gate, self.key = gate, key

    def release(self):
        with self.gate.lock:
            self.gate.active.pop(self.key, None)

    def __del__(self):
        self.release()


class StateGate:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = {}
        self.maintenance = False
        self.fault = None
        self.draining = False
        self._jobs = set()

    def _reap_jobs(self):
        # Call under self.lock. A released callback lease does not prove that
        # its Python thread (including thread-local cleanup) has exited.
        for job in tuple(self._jobs):
            if not job.starting and not job.thread.is_alive():
                job.thread.join(timeout=0)
                self._jobs.remove(job)

    @property
    def owned_jobs(self):
        with self.lock:
            self._reap_jobs()
            return len(self._jobs)

    def start_job(self, target, args=(), *, name='altera-background'):
        with self.lock:
            self._reap_jobs()
            lease = self.enter(name)
            job = _Job()
            self._jobs.add(job)

        def run():
            try:
                target(*args)
            finally:
                lease.release()

        try:
            job.thread = threading.Thread(target=run, name=name, daemon=True)
            job.thread.start()
        except BaseException:
            with self.lock:
                job.starting = False
                if job.thread is None or job.thread.ident is None:
                    self._jobs.remove(job)
                    lease.release()
            raise
        with self.lock:
            job.starting = False
        return job.thread

    def join_jobs(self, *, timeout):
        """Join only threads started by this gate; never cancel or discover work."""
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('A finite background-job join timeout is required.')
        deadline = time.monotonic() + timeout
        with self.lock:
            self._reap_jobs()
            jobs = tuple(self._jobs)
            if any(job.starting for job in jobs):
                return False
        for job in jobs:
            if job.thread is threading.current_thread():
                return False
            job.thread.join(timeout=max(0., deadline - time.monotonic()))
        return self.owned_jobs == 0

    def enter(self, label):
        with self.lock:
            if self.draining or self.maintenance or self.fault:
                raise StateBusy(self.fault or "Altera is updating saved data. Please try again shortly.")
            key = uuid.uuid4().hex
            self.active[key] = label
            return Lease(self, key)


    def enter_backup_request(self):
        """Own backup work while permitting recovery from a faulted dataset."""
        with self.lock:
            if self.draining or self.maintenance:
                raise StateBusy("Altera is updating saved data. Please try again shortly.")
            key = uuid.uuid4().hex
            self.active[key] = 'request:backups'
            return Lease(self, key)

    @contextmanager
    def exclusive(self, *, allow_lease=None):
        with self.lock:
            allowed = {allow_lease.key} if allow_lease is not None and allow_lease.gate is self else set()
            if self.draining or self.maintenance or self.owned_jobs or set(self.active) - allowed:
                raise StateBusy("Wait for replies, pictures and notes to finish, then try again.", 409)
            self.maintenance = True
        try:
            yield
        finally:
            with self.lock:
                self.maintenance = False

    def fail_closed(self, message):
        with self.lock:
            self.fault = message

    def begin_shutdown(self):
        """Permanently fence new work in this process; existing leases may finish."""
        with self.lock:
            self.draining = True

    def recovered(self):
        with self.lock:
            self.fault = None


class _Job:
    def __init__(self):
        self.thread = None
        self.starting = True


STATE = StateGate()


def request_lease(gate=None):
    """Current request's allowance, including an outer host-owned lease.

    This only identifies existing work; callers still need exclusive admission.
    Background work and services using another gate receive no allowance.
    """
    from flask import g, has_request_context
    if not has_request_context():
        return None
    lease = getattr(g, 'altera_state_lease', None)
    gate = STATE if gate is None else gate
    with gate.lock:
        return lease if type(lease) is Lease and lease.gate is gate and lease.key in gate.active else None


def start_job(target, args=(), *, name="altera-background"):
    return STATE.start_job(target, args, name=name)


class LeasedIterator:
    def __init__(self, iterable, lease):
        self.source, self.lease = iterable, lease
        self.iterator = None
        self._finish_lock = threading.Lock()
        self._finished = False
        try:
            self.iterator = iter(iterable)
        except BaseException:
            self.close()
            raise

    def __iter__(self):
        return self

    def __next__(self):
        if self._finished:
            raise StopIteration
        try:
            return next(self.iterator)
        except BaseException:
            self.close()
            raise

    def close(self):
        with self._finish_lock:
            if self._finished:
                return
            try:
                try:
                    if self.iterator is not self.source:
                        close_iterator = getattr(self.iterator, 'close', None)
                        if close_iterator is not None:
                            close_iterator()
                finally:
                    close_source = getattr(self.source, 'close', None)
                    if close_source is not None:
                        close_source()
            except BaseException:
                # A failed stream close cannot be treated as proof that its
                # files are released. Keep later maintenance fenced.
                self.lease.gate.begin_shutdown()
                raise
            finally:
                self._finished = True
                self.lease.release()


def install_state_gate(app):
    from flask import g, jsonify, redirect, request
    if app.extensions.get("altera_state_gate"):
        return
    app.extensions["altera_state_gate"] = STATE
    from core.config import ROOT
    if os.path.lexists(ROOT / "backups/restore-in-progress.json"):
        STATE.fail_closed("An interrupted restore needs recovery. Open Backup recovery before resuming Altera.")

    @app.before_request
    def reserve_request():
        # Suite discovery only returns build metadata and never opens the instance data.
        if request.endpoint == "system.suite_health":
            return None
        if request.blueprint == 'backups':
            # Backup listing/preview can initialize or clean staging too. Own
            # every request while still admitting fault-recovery endpoints.
            g.altera_state_lease = STATE.enter_backup_request()
        elif request.endpoint != "static":
            g.altera_state_lease = STATE.enter(f"request:{request.endpoint or 'unknown'}")
        if request.blueprint != "backups" and request.endpoint not in {"static", "system.suite_unload"}:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                from core.datastore import get_datastore
                epoch = get_datastore().get_key("dataset_epoch") or {}
                supplied = request.headers.get("X-Altera-Dataset")
                if not supplied and request.is_json:
                    data = request.get_json(silent=True)
                    supplied = data.get("dataset_epoch") if isinstance(data, dict) else None
                if epoch.get("required") and supplied != epoch.get("value"):
                    return jsonify(error="Altera's data was restored. Reload Altera and review any unsent drafts before sending.", code="dataset_changed"), 409

    @app.after_request
    def release_request(response):
        lease = g.pop("altera_state_lease", None)
        if lease:
            if response.is_streamed:
                response.response = LeasedIterator(response.response, lease)
            else:
                lease.release()
        return response

    @app.teardown_request
    def release_failed_request(error):
        lease = g.pop("altera_state_lease", None)
        if lease:
            lease.release()

    @app.errorhandler(StateBusy)
    def busy(error):
        if STATE.fault and request.method == "GET" and request.accept_mimetypes.best == "text/html":
            return redirect("/api/backups/recovery")
        return jsonify(error=str(error), code="state_busy", recovery_url="/api/backups/recovery" if STATE.fault else None), error.status, {"Retry-After": "5", "Cache-Control": "no-store"}
