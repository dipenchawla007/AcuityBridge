"""
SQLite persistence for the local pilot sandbox.

The in-memory ``PolicyRegistry`` and ``AuditLog`` remain the library
interfaces. These subclasses add a single SQLite file so a sandbox
process can restart without dropping partner policies or the hash chain.

The audit table is insert-only through this API. Startup reloads every
entry and runs ``verify_chain()``. A modified row fails that check.

DISCLAIMER: SQLite in this repository is a local sandbox store for
synthetic workflow data. It is not the production WORM archive described
in the deployment notes.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from acuitybridge.audit import AuditEntry, AuditLog
from acuitybridge.config import PartnerPolicy, PolicyRegistry
from acuitybridge.escalation import EscalationCase


class AuditChainError(RuntimeError):
    """Raised when a persisted audit chain does not verify."""


class SqliteBackend:
    """One SQLite file shared by policies, the audit chain, and cases."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS policies (
                    org_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_entries (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL UNIQUE,
                    org_id TEXT NOT NULL,
                    entry_hash TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS escalation_cases (
                    case_id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS check_ins (
                    check_in_id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    flag TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wearable_readings (
                    reading_id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_policy(self, org_id: str, payload: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO policies (org_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(org_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (org_id, payload, now),
            )
            self._conn.commit()

    def load_policies(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM policies ORDER BY org_id ASC"
            ).fetchall()
        return [row["payload"] for row in rows]

    def insert_audit(self, entry_id: str, org_id: str, entry_hash: str, payload: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_entries (entry_id, org_id, entry_hash, payload)
                VALUES (?, ?, ?, ?)
                """,
                (entry_id, org_id, entry_hash, payload),
            )
            self._conn.commit()

    def load_audit(self) -> list[tuple[str, str]]:
        """Return ``(payload_json, entry_hash)`` in chain order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload, entry_hash FROM audit_entries ORDER BY seq ASC"
            ).fetchall()
        return [(row["payload"], row["entry_hash"]) for row in rows]

    def upsert_case(self, case_id: str, org_id: str, state: str, payload: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO escalation_cases (case_id, org_id, state, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    org_id = excluded.org_id,
                    state = excluded.state,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (case_id, org_id, state, payload, now),
            )
            self._conn.commit()

    def load_cases(self, org_id: str | None = None) -> list[str]:
        with self._lock:
            if org_id is None:
                rows = self._conn.execute(
                    "SELECT payload FROM escalation_cases ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT payload FROM escalation_cases
                    WHERE org_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (org_id,),
                ).fetchall()
        return [row["payload"] for row in rows]

    def get_case(self, case_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM escalation_cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
        if row is None:
            return None
        return row["payload"]

    def insert_check_in(
        self,
        check_in_id: str,
        org_id: str,
        participant_id: str,
        flag: str,
        payload: str,
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO check_ins (
                    check_in_id, org_id, participant_id, flag, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (check_in_id, org_id, participant_id, flag, payload, created_at),
            )
            self._conn.commit()

    def load_check_ins(self, org_id: str, limit: int = 50) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload FROM check_ins
                WHERE org_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (org_id, limit),
            ).fetchall()
        return [row["payload"] for row in rows]

    def insert_wearable(
        self,
        reading_id: str,
        org_id: str,
        participant_id: str,
        source: str,
        metric_name: str,
        payload: str,
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO wearable_readings (
                    reading_id, org_id, participant_id, source, metric_name, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (reading_id, org_id, participant_id, source, metric_name, payload, created_at),
            )
            self._conn.commit()

    def load_wearables(self, org_id: str, limit: int = 50) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload FROM wearable_readings
                WHERE org_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (org_id, limit),
            ).fetchall()
        return [row["payload"] for row in rows]


class SqlitePolicyRegistry(PolicyRegistry):
    """Policy registry that reloads and writes through SQLite."""

    def __init__(self, backend: SqliteBackend) -> None:
        super().__init__()
        self._backend = backend
        for payload in backend.load_policies():
            policy = PartnerPolicy.model_validate_json(payload)
            self._policies[policy.org_id] = policy

    def register(self, policy: PartnerPolicy) -> None:
        super().register(policy)
        try:
            stored = self._policies[policy.org_id]
            self._backend.upsert_policy(stored.org_id, stored.model_dump_json())
        except Exception:
            self._policies.pop(policy.org_id, None)
            raise

    def update(self, policy: PartnerPolicy) -> None:
        previous = self._policies.get(policy.org_id)
        super().update(policy)
        try:
            stored = self._policies[policy.org_id]
            self._backend.upsert_policy(stored.org_id, stored.model_dump_json())
        except Exception:
            if previous is None:
                self._policies.pop(policy.org_id, None)
            else:
                self._policies[policy.org_id] = previous
            raise


class SqliteAuditLog(AuditLog):
    """Append-only audit log mirrored to SQLite.

    The hash chain is verified when the log is opened. Later appends
    insert a new row and never update an existing one.
    """

    def __init__(self, backend: SqliteBackend) -> None:
        super().__init__()
        self._backend = backend
        self._lock = threading.RLock()
        for payload, entry_hash in backend.load_audit():
            entry = AuditEntry.model_validate_json(payload)
            self._entries.append(entry)
            self._hashes.append(entry_hash)
        valid, broken_at = super().verify_chain()
        if not valid:
            raise AuditChainError(
                f"Persisted audit chain is broken at index {broken_at}."
            )

    def append(self, entry: AuditEntry) -> AuditEntry:
        with self._lock:
            result = super().append(entry)
            try:
                self._backend.insert_audit(
                    result.entry_id,
                    result.org_id,
                    self._hashes[-1],
                    result.model_dump_json(),
                )
            except Exception:
                self._entries.pop()
                self._hashes.pop()
                raise
            return result

    def verify_chain(self) -> tuple[bool, int | None]:
        with self._lock:
            return super().verify_chain()

    def query(self, org_id: str, event_type=None, time_start=None, time_end=None, actor_id=None):
        with self._lock:
            return super().query(
                org_id,
                event_type=event_type,
                time_start=time_start,
                time_end=time_end,
                actor_id=actor_id,
            )

    def export_for_review(self, org_id: str, time_start=None, time_end=None):
        with self._lock:
            return super().export_for_review(
                org_id, time_start=time_start, time_end=time_end
            )


class SqliteCaseStore:
    """Persistence for escalation cases owned by the pilot console."""

    def __init__(self, backend: SqliteBackend) -> None:
        self._backend = backend

    def upsert(self, case: EscalationCase) -> None:
        self._backend.upsert_case(
            case.case_id,
            case.org_id,
            case.state.value,
            case.model_dump_json(),
        )

    def list(self, org_id: str | None = None) -> list[EscalationCase]:
        return [
            EscalationCase.model_validate_json(payload)
            for payload in self._backend.load_cases(org_id)
        ]

    def get(self, case_id: str) -> EscalationCase:
        payload = self._backend.get_case(case_id)
        if payload is None:
            raise KeyError(f"No escalation case with id '{case_id}'.")
        return EscalationCase.model_validate_json(payload)


class SqliteCheckInStore:
    """Recent synthetic check-ins for the pilot console."""

    def __init__(self, backend: SqliteBackend) -> None:
        self._backend = backend

    def insert(self, record: dict) -> None:
        self._backend.insert_check_in(
            check_in_id=record["check_in_id"],
            org_id=record["org_id"],
            participant_id=record["participant_id"],
            flag=record["flag"],
            payload=_dump(record),
            created_at=record["created_at"],
        )

    def list(self, org_id: str, limit: int = 50) -> list[dict]:
        import json

        return [json.loads(payload) for payload in self._backend.load_check_ins(org_id, limit)]


class SqliteWearableStore:
    """Normalized wearable readings for the pilot console."""

    def __init__(self, backend: SqliteBackend) -> None:
        self._backend = backend

    def insert(self, record: dict) -> None:
        self._backend.insert_wearable(
            reading_id=record["reading_id"],
            org_id=record["org_id"],
            participant_id=record["participant_id"],
            source=record["source"],
            metric_name=record["metric_name"],
            payload=_dump(record),
            created_at=record["created_at"],
        )

    def list(self, org_id: str, limit: int = 50) -> list[dict]:
        import json

        return [json.loads(payload) for payload in self._backend.load_wearables(org_id, limit)]


def _dump(record: dict) -> str:
    import json

    return json.dumps(record, sort_keys=True)
