"""Narrow database access layer for NimHunt administrator views and audit data."""

from __future__ import annotations

from typing import Any

import constants as const
import database as schema
import db_access

RowDict = dict[str, Any]

AUDIT_TABLE = "ADMIN_AUDIT_LOG"
BAN_TABLE = "ADMIN_SPOT_BAN"


async def ensure_admin_tables(db) -> None:
    """Create additive admin-only tables without changing NimHunt's core schema version."""
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            detail TEXT,
            created_at INTEGER NOT NULL DEFAULT (unixepoch())
        );
        """
    )
    await db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_admin_audit_created
        ON {AUDIT_TABLE}(created_at DESC, id DESC);
        """
    )
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {BAN_TABLE} (
            spot_id INTEGER PRIMARY KEY,
            report_id INTEGER,
            reason TEXT,
            state TEXT NOT NULL
                CHECK (state IN ('pending_sweep', 'swept', 'blocked')),
            sweep_trans_id INTEGER,
            sweep_amount INTEGER NOT NULL DEFAULT 0
                CHECK (sweep_amount >= 0),
            created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
            FOREIGN KEY (spot_id)
                REFERENCES {schema.SPOT_TABLE_NAME}({schema.SPOT_ID})
                ON DELETE RESTRICT,
            FOREIGN KEY (report_id)
                REFERENCES {schema.REPORT_TABLE_NAME}({schema.REPORT_ID})
                ON DELETE SET NULL,
            FOREIGN KEY (sweep_trans_id)
                REFERENCES {schema.TRANS_TABLE_NAME}({schema.TRANS_ID})
                ON DELETE SET NULL
        );
        """
    )
    await db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_admin_spot_ban_state
        ON {BAN_TABLE}(state, updated_at, spot_id);
        """
    )
    await db.commit()


async def record_audit(
    db,
    *,
    action: str,
    target_type: str,
    target_id: int | None = None,
    detail: str | None = None,
) -> int:
    action = str(action or "").strip()
    target_type = str(target_type or "").strip()
    if not action or not target_type:
        raise ValueError("audit action and target_type are required")
    clean_detail = str(detail or "").strip()[:2000] or None
    cur = await db.execute(
        f"""
        INSERT INTO {AUDIT_TABLE} (action, target_type, target_id, detail)
        VALUES (?, ?, ?, ?);
        """,
        (
            action[:120],
            target_type[:80],
            None if target_id is None else int(target_id),
            clean_detail,
        ),
    )
    return int(cur.lastrowid)


async def dashboard_metrics(db) -> RowDict:
    """Return the exact active/total values shown in the admin header."""
    await ensure_admin_tables(db)
    row = await (
        await db.execute(
            f"""
            SELECT
                (SELECT COUNT(*)
                   FROM {schema.USER_TABLE_NAME}
                  WHERE {schema.USER_STATUS} = ?) AS active_users,
                (SELECT COUNT(*)
                   FROM {schema.USER_TABLE_NAME}) AS total_users,
                (SELECT COUNT(*)
                   FROM {schema.SPOT_TABLE_NAME}
                  WHERE {schema.SPOT_STATUS} = ?
                    AND {schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
                    AND ({schema.SPOT_STARTS_AT} IS NULL OR {schema.SPOT_STARTS_AT} <= unixepoch())
                    AND (
                        {schema.SPOT_STARTS_AT} IS NULL
                        OR ({schema.SPOT_STARTS_AT} + {schema.SPOT_ENDS_AT}) > unixepoch()
                    )) AS active_spots,
                (SELECT COUNT(*)
                   FROM {schema.SPOT_TABLE_NAME}) AS total_spots,
                (SELECT COUNT(*)
                   FROM {schema.REPORT_TABLE_NAME}
                  WHERE {schema.REPORT_STATUS} = ?) AS pending_reports,
                (SELECT COUNT(*)
                   FROM {schema.REPORT_TABLE_NAME}) AS total_reports;
            """,
            (
                const.USER_STATUS_ACTIVE,
                const.SPOT_STATUS_PUBLISHED,
                const.REPORT_STATUS_PENDING,
            ),
        )
    ).fetchone()
    return {
        "active_users": int(row["active_users"] or 0),
        "total_users": int(row["total_users"] or 0),
        "active_spots": int(row["active_spots"] or 0),
        "total_spots": int(row["total_spots"] or 0),
        "pending_reports": int(row["pending_reports"] or 0),
        "total_reports": int(row["total_reports"] or 0),
    }


async def user_growth(db, *, days: int = 30) -> list[RowDict]:
    """Return registration counts for the most recent calendar days."""
    days = max(1, min(int(days), 90))
    rows = await db.execute_fetchall(
        f"""
        WITH RECURSIVE dates(day, offset) AS (
            SELECT date('now', '-{days - 1} days'), 0
            UNION ALL
            SELECT date(day, '+1 day'), offset + 1
            FROM dates
            WHERE offset < {days - 1}
        ),
        registrations AS (
            SELECT
                date({schema.USER_CREATED_AT}, 'unixepoch') AS day,
                COUNT(*) AS count
            FROM {schema.USER_TABLE_NAME}
            WHERE {schema.USER_CREATED_AT} >= unixepoch('now', '-{days - 1} days', 'start of day')
            GROUP BY date({schema.USER_CREATED_AT}, 'unixepoch')
        )
        SELECT dates.day AS day, COALESCE(registrations.count, 0) AS count
        FROM dates
        LEFT JOIN registrations ON registrations.day = dates.day
        ORDER BY dates.day ASC;
        """
    )
    values = [{"day": str(row["day"]), "count": int(row["count"] or 0)} for row in rows]
    peak = max((item["count"] for item in values), default=0)
    for item in values:
        item["percent"] = 0 if peak <= 0 else round((item["count"] / peak) * 100, 2)
    return values


async def spot_creation_leaderboard(db, *, limit: int = 10) -> list[RowDict]:
    limit = max(1, min(int(limit), 50))
    rows = await db.execute_fetchall(
        f"""
        SELECT
            u.{schema.USER_ID} AS user_id,
            u.{schema.USER_DISPLAY_NAME} AS display_name,
            u.{schema.USER_STATUS} AS user_status,
            COUNT(s.{schema.SPOT_ID}) AS spot_count
        FROM {schema.SPOT_TABLE_NAME} s
        JOIN {schema.USER_TABLE_NAME} u
          ON u.{schema.USER_ID} = s.{schema.SPOT_CREATED_BY}
        GROUP BY u.{schema.USER_ID}, u.{schema.USER_DISPLAY_NAME}, u.{schema.USER_STATUS}
        ORDER BY spot_count DESC, u.{schema.USER_ID} ASC
        LIMIT ?;
        """,
        (limit,),
    )
    return [dict(row) for row in rows]


async def pending_reports(db, *, limit: int = 50) -> list[RowDict]:
    limit = max(1, min(int(limit), 100))
    rows = await db.execute_fetchall(
        f"""
        SELECT
            r.{schema.REPORT_ID} AS report_id,
            r.{schema.REPORT_REASON} AS reason,
            r.{schema.REPORT_DETAILS} AS details,
            r.{schema.REPORT_STATUS} AS report_status,
            r.{schema.REPORT_MODERATOR_NOTE} AS moderator_note,
            r.{schema.REPORT_CREATED_AT} AS report_created_at,
            r.{schema.REPORT_REVIEWED_AT} AS reviewed_at,

            reporter.{schema.USER_ID} AS reporter_id,
            reporter.{schema.USER_DISPLAY_NAME} AS reporter_name,
            reporter.{schema.USER_STATUS} AS reporter_status,

            s.{schema.SPOT_ID} AS spot_id,
            s.{schema.SPOT_LINK} AS spot_link,
            s.{schema.SPOT_TITLE} AS spot_title,
            s.{schema.SPOT_STATUS} AS spot_status,
            s.{schema.SPOT_CITY} AS spot_city,
            s.{schema.SPOT_COUNTRY} AS spot_country,

            owner.{schema.USER_ID} AS owner_id,
            owner.{schema.USER_DISPLAY_NAME} AS owner_name,
            owner.{schema.USER_STATUS} AS owner_status,

            (
                SELECT COUNT(*)
                FROM {schema.REPORT_TABLE_NAME} all_reports
                WHERE all_reports.{schema.REPORT_SPOT_ID} = r.{schema.REPORT_SPOT_ID}
            ) AS spot_report_count
        FROM {schema.REPORT_TABLE_NAME} r
        JOIN {schema.USER_TABLE_NAME} reporter
          ON reporter.{schema.USER_ID} = r.{schema.REPORT_REPORTED_BY}
        JOIN {schema.SPOT_TABLE_NAME} s
          ON s.{schema.SPOT_ID} = r.{schema.REPORT_SPOT_ID}
        JOIN {schema.USER_TABLE_NAME} owner
          ON owner.{schema.USER_ID} = s.{schema.SPOT_CREATED_BY}
        WHERE r.{schema.REPORT_STATUS} = ?
        ORDER BY r.{schema.REPORT_CREATED_AT} ASC, r.{schema.REPORT_ID} ASC
        LIMIT ?;
        """,
        (const.REPORT_STATUS_PENDING, limit),
    )
    return [dict(row) for row in rows]


async def recent_audit(db, *, limit: int = 20) -> list[RowDict]:
    await ensure_admin_tables(db)
    rows = await db.execute_fetchall(
        f"""
        SELECT id, action, target_type, target_id, detail, created_at
        FROM {AUDIT_TABLE}
        ORDER BY created_at DESC, id DESC
        LIMIT ?;
        """,
        (max(1, min(int(limit), 100)),),
    )
    return [dict(row) for row in rows]


async def set_report_status(
    db,
    *,
    report_id: int,
    status: int,
    moderator_note: str | None = None,
) -> None:
    if int(status) not in {
        const.REPORT_STATUS_PENDING,
        const.REPORT_STATUS_APPROVED,
        const.REPORT_STATUS_DISMISSED,
    }:
        raise ValueError("invalid report status")
    clean_note = str(moderator_note or "").strip()[:2000] or None
    cur = await db.execute(
        f"""
        UPDATE {schema.REPORT_TABLE_NAME}
        SET {schema.REPORT_STATUS} = ?,
            {schema.REPORT_MODERATOR_NOTE} = ?
        WHERE {schema.REPORT_ID} = ?;
        """,
        (int(status), clean_note, int(report_id)),
    )
    if int(cur.rowcount or 0) != 1:
        raise ValueError(f"report id={report_id} does not exist")


async def set_user_status(db, *, user_id: int, status: int) -> None:
    status = int(status)
    if status == const.USER_STATUS_ACTIVE:
        await db_access.set_user_status_to_active(db, user_id=int(user_id))
    elif status == const.USER_STATUS_LIMITED:
        await db_access.set_user_status_to_limited(db, user_id=int(user_id))
    elif status == const.USER_STATUS_BANNED:
        await db_access.set_user_status_to_banned(db, user_id=int(user_id))
    else:
        raise ValueError("invalid user status")


async def get_ban_record(db, *, spot_id: int) -> RowDict | None:
    await ensure_admin_tables(db)
    cur = await db.execute(
        f"SELECT * FROM {BAN_TABLE} WHERE spot_id = ?;",
        (int(spot_id),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def upsert_ban_record(
    db,
    *,
    spot_id: int,
    report_id: int | None,
    reason: str | None,
    state: str,
    sweep_trans_id: int | None = None,
    sweep_amount: int = 0,
) -> None:
    if state not in {"pending_sweep", "swept", "blocked"}:
        raise ValueError("invalid admin Spot ban state")
    clean_reason = str(reason or "").strip()[:2000] or None
    await db.execute(
        f"""
        INSERT INTO {BAN_TABLE} (
            spot_id, report_id, reason, state, sweep_trans_id, sweep_amount
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(spot_id) DO UPDATE SET
            report_id = COALESCE(excluded.report_id, {BAN_TABLE}.report_id),
            reason = COALESCE(excluded.reason, {BAN_TABLE}.reason),
            state = excluded.state,
            sweep_trans_id = COALESCE(excluded.sweep_trans_id, {BAN_TABLE}.sweep_trans_id),
            sweep_amount = CASE
                WHEN excluded.sweep_amount > 0 THEN excluded.sweep_amount
                ELSE {BAN_TABLE}.sweep_amount
            END,
            updated_at = unixepoch();
        """,
        (
            int(spot_id),
            None if report_id is None else int(report_id),
            clean_reason,
            state,
            None if sweep_trans_id is None else int(sweep_trans_id),
            max(0, int(sweep_amount)),
        ),
    )


async def pending_banned_spot_ids(db, *, limit: int = 50) -> list[int]:
    await ensure_admin_tables(db)
    rows = await db.execute_fetchall(
        f"""
        SELECT spot_id
        FROM {BAN_TABLE}
        WHERE state IN ('pending_sweep', 'blocked')
        ORDER BY updated_at ASC, spot_id ASC
        LIMIT ?;
        """,
        (max(1, min(int(limit), 100)),),
    )
    return [int(row["spot_id"]) for row in rows]
