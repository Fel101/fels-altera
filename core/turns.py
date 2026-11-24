"""Durable chat requests. A browser disconnect never starts a second model turn.

One process owns model work. SQLite keeps results across restarts; unfinished work
from a previous process becomes interrupted, rather than being silently repeated.
"""
import contextvars
from contextlib import contextmanager
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid

from core.owned_iterator import owned_iterator
from core.suite_gpu import GpuWaitError

LOGGER = logging.getLogger(__name__)
CURRENT_TURN = contextvars.ContextVar("altera_turn", default=None)
TERMINAL = {"saved", "failed", "interrupted"}
# The model answered with nothing to show, even when asked again (core/chat.py): said as it is, not as a general failure
EMPTY_REPLY = "The model sent back an empty reply, twice. Your message is saved: send it again, or try another model."


class EmptyReply(RuntimeError):
    def __init__(self):
        super().__init__(EMPTY_REPLY)
PROCESS_ID = uuid.uuid4().hex


class TurnError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Turn:
    def __init__(self, service, request_id, regenerate=None):
        self.service, self.request_id = service, request_id
        self.cancel = threading.Event()
        # Text can already be saved while speech is still being synthesized.
        # Silencing that speech must not change the completed answer to a draft.
        self.speech_cancel = threading.Event()
        self.regenerate = regenerate
        self.finish_lock = threading.RLock()
        self.upstream = None
        self.cleanup_unconfirmed = False
        self.metrics = {}

    def check_cancelled(self):
        if self.cancel.is_set():
            raise InterruptedError("Reply stopped")

    def update(self, **changes):
        self.service.update(self.request_id, **changes)


class TurnService:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.jobs = {}
        with self.connection() as conn:
            conn.execute("""UPDATE chat_turns SET
                            status=CASE WHEN EXISTS (SELECT 1 FROM messages m WHERE m.id=chat_turns.assistant_message_id
                                AND m.session_id=chat_turns.session_id AND m.role='assistant') THEN 'saved' ELSE 'interrupted' END,
                            error=CASE WHEN EXISTS (SELECT 1 FROM messages m WHERE m.id=chat_turns.assistant_message_id
                                AND m.session_id=chat_turns.session_id AND m.role='assistant') THEN ''
                                ELSE 'Altera restarted before this reply finished.' END
                            WHERE status IN ('sending','streaming') AND process_id != ?""", (PROCESS_ID,))
            conn.execute("UPDATE chat_turns SET expression_pending=NULL WHERE process_id != ? AND status != 'saved' AND expression_pending IS NOT NULL", (PROCESS_ID,))
            pending = [row['request_id'] for row in conn.execute(
                "SELECT request_id FROM chat_turns WHERE process_id != ? AND status='saved' AND expression_pending IS NOT NULL ORDER BY assistant_message_id LIMIT 1000", (PROCESS_ID,))]
        from core.expression import consume_pending
        for request_id in pending:
            try:
                consume_pending(self.store, request_id)
            except Exception:
                LOGGER.exception('Could not recover saved expression: request_id=%s', request_id)

    @contextmanager
    def connection(self):
        conn = self.store._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, request_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM chat_turns WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def update(self, request_id, **changes):
        allowed = {"status", "response", "error", "user_message_id", "assistant_message_id", "first_text_ms", "elapsed_ms", "diagnostics"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("Invalid turn fields")
        with self.connection() as conn:
            conn.execute("UPDATE chat_turns SET " + ",".join(f"{k}=?" for k in changes) +
                         ",updated_at=CURRENT_TIMESTAMP WHERE request_id=?", (*changes.values(), request_id))
        with self.changed:
            self.changed.notify_all()

    def submit(self, request_id, session_id, message, greet, produce, app, regenerate=None, dataset_epoch=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", request_id):
            raise TurnError("request_id must contain 8–100 letters, numbers, underscores or hyphens.", 400)
        identity = [session_id, message, greet] + ([regenerate] if regenerate else [])
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            epoch = self.store.get_key("dataset_epoch") or {}
            if (epoch.get("required") or dataset_epoch is not None) and dataset_epoch != epoch.get("value"):
                raise TurnError("Altera's data was restored. Reload Altera and review any unsent drafts before sending.", 409)
            with self.connection() as conn:
                if conn.execute("SELECT 1 FROM chat_request_tombstones WHERE request_id=?", (request_id,)).fetchone():
                    raise TurnError("This message was changed or deleted. Its old request cannot be sent again.", 410)
            old = self.get(request_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise TurnError("This request ID already belongs to a different message.")
                return old
            if session_id is not None and not self.store.get_session(session_id):
                raise TurnError("Session not found", 404)
            if session_id is not None:
                with self.connection() as conn:
                    busy = conn.execute("SELECT 1 FROM chat_turns WHERE session_id=? AND status IN ('sending','streaming')",
                                        (session_id,)).fetchone()
                if busy:
                    raise TurnError("A reply is already being written in this conversation.")
            if greet and session_id and self.store.get_message_count(session_id):
                raise TurnError("She has already said hello.")
            from core.state_gate import STATE, start_job
            lease = STATE.enter("reply-start")
            try:
                session_id = session_id or self.store.create_session("New Conversation")
                with self.connection() as conn:
                    try:
                        conn.execute("""INSERT INTO chat_turns (request_id,session_id,fingerprint,status,process_id,target_message_id)
                                        VALUES (?,?,?,'sending',?,?)""", (request_id, session_id, fingerprint, PROCESS_ID,
                                                                       (regenerate or {}).get("assistant_message_id")))
                    except sqlite3.IntegrityError as exc:
                        raise TurnError("A reply is already being written in this conversation.") from exc
                turn = Turn(self, request_id, regenerate)
                self.jobs[request_id] = turn
                try:
                    start_job(self._run, (turn, produce, session_id, app), name="altera-reply")
                except BaseException:
                    self.jobs.pop(request_id, None)
                    self.update(request_id, status="failed", error="The reply worker could not start.")
                    raise
                return self.get(request_id)
            finally:
                lease.release()

    def stop(self, request_id):
        with self.lock:
            turn = self.jobs.get(request_id)
        if not turn:
            row = self.get(request_id)
            if row is None:
                raise TurnError("Request not found", 404)
            return row["status"]
        # Never take finish_lock while holding the service lock: persistence notifies its condition.
        with turn.finish_lock:
            row = self.get(request_id)
            turn.speech_cancel.set()
            if row["assistant_message_id"]:
                return "saved"
            turn.cancel.set()
            upstream = turn.upstream
        try:
            close = getattr(upstream, "close", None)
            if close:
                close()
        except BaseException as error:
            LOGGER.debug("The stopped model stream was already closed", exc_info=True)
            if not isinstance(error, Exception):
                raise
        return "stopping"

    def _run(self, turn, produce, session_id, app):
        started = time.monotonic()
        token = CURRENT_TURN.set(turn)
        text = ""
        try:
            with app.app_context():
                with owned_iterator(produce(session_id)) as produced:
                    for event in produced:
                        if event.get("response"):
                            text += event["response"]
                            changes = {"response": text, "status": "streaming"}
                            if self.get(turn.request_id)["first_text_ms"] is None:
                                changes["first_text_ms"] = round((time.monotonic() - started) * 1000)
                            turn.update(**changes)
                        elif event.get("error"):
                            raise RuntimeError(event["error"])
                        self._event(turn.request_id, event)
                row = self.get(turn.request_id)
                if not row["assistant_message_id"]:
                    raise RuntimeError("The reply could not be confirmed as saved.")
                turn.update(status="saved")
        except InterruptedError:
            row = self.get(turn.request_id)
            if row["assistant_message_id"]:
                turn.update(status="saved", error="")
            else:
                turn.update(status="interrupted", error="Reply stopped. Your partial reply has been kept.")
        except Exception as failure:
            row = self.get(turn.request_id)
            if row["assistant_message_id"]:
                LOGGER.exception("Reply follow-up failed after saving: request_id=%s", turn.request_id)
                turn.update(status="saved", error="")
            elif turn.cancel.is_set():
                turn.update(status="interrupted", error="Reply stopped. Your partial reply has been kept.")
            else:
                LOGGER.exception("Reply failed: request_id=%s", turn.request_id)
                if (turn.regenerate or {}).get("assistant_message_id"):
                    error = "The replacement could not be completed. Your previous answer is unchanged."
                elif isinstance(failure, GpuWaitError):
                    error = str(failure)
                elif row["user_message_id"]:
                    error = EMPTY_REPLY if str(failure) == EMPTY_REPLY else "The reply could not be completed. Your message is saved."
                else:
                    error = "The message could not be confirmed as saved. Reconnect before retrying."
                turn.update(status="failed", error=error)
        finally:
            try:
                with app.app_context():
                    row = self.get(turn.request_id)
                    if not turn.cleanup_unconfirmed and row and row['status'] == 'saved' and row['user_message_id'] and row['assistant_message_id']:
                        self._record_saved_exchange(turn, session_id, row)
            except BaseException:
                LOGGER.exception('Could not inspect saved exchange: request_id=%s', turn.request_id)
            CURRENT_TURN.reset(token)
            try:
                turn.metrics['visible_output_tokens_estimate'] = round(len(text) / 3.2) if text else None
                turn.update(elapsed_ms=round((time.monotonic() - started) * 1000), diagnostics=json.dumps(turn.metrics, allow_nan=False))
            except Exception:
                LOGGER.exception("Could not persist reply timing: request_id=%s", turn.request_id)
            finally:
                if not turn.cleanup_unconfirmed:
                    try:
                        from core.episodes import get_episode_service
                        get_episode_service(self.store).release_recall(turn.request_id)
                    except Exception:
                        LOGGER.exception('Could not release episodic recall reservation: request_id=%s', turn.request_id)
                    try:
                        from core.voice_notes import settle_linked_turn
                        settle_linked_turn(self.store, turn.request_id)
                    except Exception:
                        LOGGER.exception('Could not retain voice-note reply media: request_id=%s', turn.request_id)
                with self.changed:
                    self.jobs.pop(turn.request_id, None)
                    self.changed.notify_all()

    def _record_saved_exchange(self, turn, session_id, row):
        """Apply source-checked follow-up once, including after a late failure."""
        try:
            from core.episodes import get_episode_service
            get_episode_service(self.store).record_recall(session_id, getattr(turn, 'recall_epoch', None) or self.store.dataset_epoch()['value'],
                getattr(turn, 'episode_inputs', []), row['user_message_id'], row['assistant_message_id'], request_id=turn.request_id)
        except Exception:
            LOGGER.exception('Could not retain episodic recall usage: request_id=%s', turn.request_id)
        try:
            from core.memory import get_memory_manager
            from core.memory_recovery import consume_pending
            consume_pending(self, turn.request_id, get_memory_manager())
        except Exception:
            LOGGER.exception('Could not retain memory recency: request_id=%s', turn.request_id)
        try:
            from core.expression import consume_pending
            consume_pending(self.store, turn.request_id)
        except Exception:
            LOGGER.exception('Could not retain companion expression: request_id=%s', turn.request_id)

    def _event(self, request_id, event):
        with self.connection() as conn:
            conn.execute("INSERT INTO chat_turn_events (request_id,event) VALUES (?,?)", (request_id, json.dumps(event)))
        with self.changed:
            self.changed.notify_all()

    def events(self, request_id):
        cursor = 0
        while True:
            row = self.get(request_id)
            if row is None:
                return
            with self.connection() as conn:
                events = conn.execute("SELECT id,event FROM chat_turn_events WHERE request_id=? AND id>? ORDER BY id",
                                      (request_id, cursor)).fetchall()
            for item in events:
                cursor = item["id"]
                data = json.loads(item["event"])
                # Only the ledger can confirm persistence. The producer's legacy completion events are suppressed.
                if data.get("status") not in {"final", "done"}:
                    yield data
            # Wait for the worker to exit, so all events and timing are durable before completing.
            with self.changed:
                running = request_id in self.jobs
                if not events and running:
                    self.changed.wait(timeout=1)
            if not running and not events:
                # The worker may have committed between our first read and releasing its job slot.
                with self.connection() as conn:
                    if conn.execute("SELECT 1 FROM chat_turn_events WHERE request_id=? AND id>? LIMIT 1", (request_id, cursor)).fetchone():
                        continue
                row = self.get(request_id)
                other = 0
                if row["status"] == "saved" and row["assistant_message_id"]:
                    with self.connection() as conn:
                        other = conn.execute("SELECT COUNT(*) FROM message_versions WHERE message_id=?",
                                             (row["assistant_message_id"],)).fetchone()[0]
                yield {"status": row["status"], "request_id": request_id, "session_id": row["session_id"],
                       "message_id": row["assistant_message_id"], "user_message_id": row["user_message_id"], "error": row["error"] or None,
                       "target_message_id": row["target_message_id"], "other_versions": max(0, other - 1),
                       "response": row["response"] if row["status"] != "saved" else None}
                return
            if not events:
                yield {"status": "heartbeat"}


def get_turn_service(store=None):
    from core.datastore import get_datastore
    store = store or get_datastore()
    # Initialization and interrupted-turn recovery happen once per store/process.
    with _service_lock:
        if not hasattr(store, "_turn_service"):
            store._turn_service = TurnService(store)
        return store._turn_service


_service_lock = threading.Lock()
