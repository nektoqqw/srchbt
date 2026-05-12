"""SQLite-хранилище пользователей и сохранённых юзернеймов."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Generator, Iterable, Literal, Optional

from plus_tariffs import PLUS_TARIFFS

_DB_LOCK = Lock()

# Максимум сохранённых @ников на одного пользователя (PLUS).
SAVED_USERNAMES_LIMIT = 20


@dataclass
class UserRow:
    user_id: int
    searches_used: int
    is_plus: int
    created_at: str
    has_luck: int = 0
    # ISO-8601 UTC; None = PLUS без даты окончания (промо / тариф «навсегда»)
    plus_expires_at: str | None = None
    # «Удача» по времени / навсегда (оплата); has_luck без даты = старый промо «без срока»
    luck_expires_at: str | None = None
    luck_forever: int = 0
    # 1 = PLUS сам отключил учёт «Удачи» в крутке (подписка удачи может оставаться активной)
    luck_roll_paused: int = 0


@dataclass
class FragmentItemRow:
    username: str
    price_usd: float | None
    source_url: str
    imported_at: str


def _plus_promo_allowed_days() -> frozenset[int | None]:
    return frozenset(t.days for t in PLUS_TARIFFS)


def _migrate_dynamic_promos(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(dynamic_promos)")
    cols = {str(row[1]) for row in cur.fetchall()}
    if "plus_days" not in cols:
        conn.execute("ALTER TABLE dynamic_promos ADD COLUMN plus_days INTEGER")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_users_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(users)")
    cols = {str(row[1]) for row in cur.fetchall()}
    if "has_luck" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN has_luck INTEGER NOT NULL DEFAULT 0"
        )
    if "plus_expires_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN plus_expires_at TEXT")
    if "luck_expires_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN luck_expires_at TEXT")
    if "luck_forever" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN luck_forever INTEGER NOT NULL DEFAULT 0"
        )
    if "luck_roll_paused" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN luck_roll_paused INTEGER NOT NULL DEFAULT 0"
        )


def _plus_expires_expired(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= dt


def _luck_expires_expired(raw: str | None) -> bool:
    return _plus_expires_expired(raw)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                searches_used INTEGER NOT NULL DEFAULT 0,
                is_plus INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        _migrate_users_schema(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_usernames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, username),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            """
        )
        conn.commit()

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fragment_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                price_usd REAL,
                source_url TEXT NOT NULL,
                imported_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(username, source_url)
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roll_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                rarity TEXT NOT NULL,
                predicted_price_usd REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS luck_promo_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_kv (
                k TEXT PRIMARY KEY NOT NULL,
                v TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dynamic_promos (
                code TEXT PRIMARY KEY NOT NULL,
                kind TEXT NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dynamic_promo_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(code, user_id)
            );
            """
        )
        _migrate_dynamic_promos(conn)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                referred_user_id INTEGER PRIMARY KEY NOT NULL,
                referrer_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_referrals_referrer "
            "ON referrals(referrer_user_id)"
        )

        conn.commit()


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        init_db(path)

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        with _DB_LOCK:
            conn = _connect(self._path)
            try:
                cur = conn.cursor()
                yield cur
                conn.commit()
            finally:
                conn.close()

    def get_or_create_user(self, user_id: int) -> UserRow:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO users (user_id, searches_used, is_plus) VALUES (?, 0, 0)",
                    (user_id,),
                )
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cur.fetchone()
            assert row is not None
            if int(row["is_plus"]) and row["plus_expires_at"] and _plus_expires_expired(
                str(row["plus_expires_at"])
            ):
                cur.execute(
                    "UPDATE users SET is_plus = 0, plus_expires_at = NULL WHERE user_id = ?",
                    (user_id,),
                )
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cur.fetchone()
                assert row is not None
            luck_exp = row["luck_expires_at"]
            lf = int(row["luck_forever"])
            if luck_exp and not lf and _luck_expires_expired(str(luck_exp)):
                cur.execute(
                    """
                    UPDATE users SET has_luck = 0, luck_expires_at = NULL, luck_roll_paused = 0
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cur.fetchone()
                assert row is not None
            return UserRow(
                user_id=row["user_id"],
                searches_used=row["searches_used"],
                is_plus=row["is_plus"],
                created_at=row["created_at"],
                has_luck=int(row["has_luck"]),
                plus_expires_at=row["plus_expires_at"],
                luck_expires_at=row["luck_expires_at"],
                luck_forever=int(row["luck_forever"]),
                luck_roll_paused=int(row["luck_roll_paused"]),
            )

    def user_exists(self, user_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            return cur.fetchone() is not None

    def referral_count(self, referrer_user_id: int) -> int:
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id = ?",
                (referrer_user_id,),
            )
            return int(cur.fetchone()[0])

    def try_register_referral(
        self,
        referred_user_id: int,
        referrer_user_id: int | None,
        *,
        bonus_hours: int,
    ) -> bool:
        """
        Одна запись на приглашённого (PRIMARY KEY referred_user_id).
        При успехе — бонус PLUS пригласившему (часы), если у него не PLUS «навсегда».
        """
        if referrer_user_id is None or referrer_user_id <= 0:
            return False
        if referrer_user_id == referred_user_id:
            return False
        if bonus_hours <= 0:
            return False
        if not self.user_exists(referrer_user_id):
            return False
        self.get_or_create_user(referred_user_id)
        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO referrals (referred_user_id, referrer_user_id)
                    VALUES (?, ?)
                    """,
                    (referred_user_id, referrer_user_id),
                )
        except sqlite3.IntegrityError:
            return False
        self.extend_plus_hours(referrer_user_id, bonus_hours)
        return True

    def extend_plus_hours(self, user_id: int, hours: int) -> None:
        """Включает PLUS и продлевает на ``hours`` от текущего окончания или от сейчас."""
        if hours <= 0:
            return
        self.get_or_create_user(user_id)
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                "SELECT is_plus, plus_expires_at FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            assert row is not None
            is_plus = int(row["is_plus"])
            exp_raw = row["plus_expires_at"]
            if is_plus and exp_raw is None:
                # PLUS без даты окончания — не трогаем срок
                return
            base = now
            if is_plus and exp_raw:
                raw = str(exp_raw)
                try:
                    pe = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if pe.tzinfo is None:
                        pe = pe.replace(tzinfo=timezone.utc)
                    if pe > base:
                        base = pe
                except ValueError:
                    pass
            new_exp = base + timedelta(hours=hours)
            cur.execute(
                "UPDATE users SET is_plus = 1, plus_expires_at = ? WHERE user_id = ?",
                (new_exp.isoformat(), user_id),
            )

    def searches_remaining(self, user_id: int, free_limit: int) -> Optional[int]:
        """None = безлимит (PLUS)."""
        u = self.get_or_create_user(user_id)
        if u.is_plus:
            return None
        return max(0, free_limit - u.searches_used)

    def can_search(self, user_id: int, free_limit: int) -> bool:
        u = self.get_or_create_user(user_id)
        if u.is_plus:
            return True
        return u.searches_used < free_limit

    def increment_search(self, user_id: int) -> None:
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            cur.execute(
                "UPDATE users SET searches_used = searches_used + 1 WHERE user_id = ?",
                (user_id,),
            )

    def is_search_globally_blocked(self) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT v FROM app_kv WHERE k = ?", ("search_block_all",))
            row = cur.fetchone()
            return row is not None and str(row[0]) == "1"

    def set_search_globally_blocked(self, blocked: bool) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO app_kv (k, v) VALUES (?, ?)",
                ("search_block_all", "1" if blocked else "0"),
            )

    def get_legal_document_url(self, kind: str) -> str:
        """kind: terms | privacy — URL для пользовательского соглашения / политики."""
        key = {"terms": "doc_terms_url", "privacy": "doc_privacy_url"}.get(kind)
        if not key:
            return ""
        with self._cursor() as cur:
            cur.execute("SELECT v FROM app_kv WHERE k = ?", (key,))
            row = cur.fetchone()
            if not row or row[0] is None:
                return ""
            return str(row[0]).strip()

    def set_legal_document_url(self, kind: str, url: str) -> None:
        if kind not in ("terms", "privacy"):
            return
        key = "doc_terms_url" if kind == "terms" else "doc_privacy_url"
        u = url.strip()
        with self._cursor() as cur:
            if not u:
                cur.execute("DELETE FROM app_kv WHERE k = ?", (key,))
            else:
                cur.execute(
                    "INSERT OR REPLACE INTO app_kv (k, v) VALUES (?, ?)",
                    (key, u),
                )

    def list_all_user_ids(self) -> list[int]:
        with self._cursor() as cur:
            cur.execute("SELECT user_id FROM users ORDER BY user_id")
            return [int(r[0]) for r in cur.fetchall()]

    def count_users(self, *, plus_only: bool = False) -> int:
        with self._cursor() as cur:
            if plus_only:
                cur.execute("SELECT COUNT(*) FROM users WHERE is_plus = 1")
            else:
                cur.execute("SELECT COUNT(*) FROM users")
            return int(cur.fetchone()[0])

    def list_user_ids_page(
        self, *, plus_only: bool, offset: int, limit: int = 35
    ) -> tuple[list[int], int]:
        total = self.count_users(plus_only=plus_only)
        with self._cursor() as cur:
            if plus_only:
                cur.execute(
                    """
                    SELECT user_id FROM users WHERE is_plus = 1
                    ORDER BY user_id LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
            else:
                cur.execute(
                    "SELECT user_id FROM users ORDER BY user_id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ids = [int(r[0]) for r in cur.fetchall()]
        return ids, total

    def list_plus_user_ids(self) -> list[int]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT user_id FROM users WHERE is_plus = 1 ORDER BY user_id"
            )
            return [int(r[0]) for r in cur.fetchall()]

    def has_active_dynamic_promo(self, kind: str) -> bool:
        if kind not in ("plus", "luck"):
            return False
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM dynamic_promos WHERE kind = ? AND is_active = 1 LIMIT 1",
                (kind,),
            )
            return cur.fetchone() is not None

    def dynamic_promo_create(
        self,
        code: str,
        kind: str,
        max_uses: int,
        *,
        plus_days: int | None = None,
    ) -> tuple[bool, str]:
        if kind not in ("plus", "luck"):
            return False, "kind"
        if kind == "luck" and plus_days is not None:
            return False, "kind"
        allowed = _plus_promo_allowed_days()
        if kind == "plus" and plus_days not in allowed:
            return False, "plus_days"
        c = code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{3,40}", c):
            return False, "format"
        mu = max(0, int(max_uses))
        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dynamic_promos (code, kind, max_uses, is_active, plus_days)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (c, kind, mu, plus_days if kind == "plus" else None),
                )
        except sqlite3.IntegrityError:
            return False, "exists"
        return True, "ok"

    def dynamic_promo_redeem(
        self, code: str, user_id: int, kind: str
    ) -> tuple[bool, str, int | None]:
        """
        Для kind ``plus`` при успехе третий элемент — число дней продления или ``None`` (без срока).
        Для ``luck`` третий элемент всегда ``None``.
        """
        if kind not in ("plus", "luck"):
            return False, "kind", None
        c = code.strip().upper()
        if not c:
            return False, "empty", None
        with self._cursor() as cur:
            cur.execute(
                "SELECT kind, max_uses, is_active, plus_days FROM dynamic_promos WHERE code = ?",
                (c,),
            )
            row = cur.fetchone()
            if not row:
                return False, "not_found", None
            if int(row["is_active"]) != 1:
                return False, "inactive", None
            if str(row["kind"]) != kind:
                return False, "wrong_kind", None
            cur.execute(
                "SELECT 1 FROM dynamic_promo_uses WHERE code = ? AND user_id = ?",
                (c, user_id),
            )
            if cur.fetchone():
                return False, "already", None
            max_uses = int(row["max_uses"])
            if max_uses > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM dynamic_promo_uses WHERE code = ?",
                    (c,),
                )
                n = int(cur.fetchone()[0])
                if n >= max_uses:
                    return False, "limit", None
            plus_out: int | None = None
            if kind == "plus":
                pd = row["plus_days"]
                plus_out = None if pd is None else int(pd)
            cur.execute(
                "INSERT INTO dynamic_promo_uses (code, user_id) VALUES (?, ?)",
                (c, user_id),
            )
        return True, "ok", plus_out

    def dynamic_promo_list(
        self, *, limit: int = 25
    ) -> list[tuple[str, str, int, int, str, int | None]]:
        """code, kind, max_uses, is_active, created_at, plus_days (только PLUS; иначе None)."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT code, kind, max_uses, is_active, created_at, plus_days
                FROM dynamic_promos
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            out: list[tuple[str, str, int, int, str, int | None]] = []
            for r in rows:
                pd = r["plus_days"]
                out.append(
                    (
                        str(r["code"]),
                        str(r["kind"]),
                        int(r["max_uses"]),
                        int(r["is_active"]),
                        str(r["created_at"]),
                        None if pd is None else int(pd),
                    )
                )
            return out

    def dynamic_promo_deactivate(self, code: str) -> bool:
        c = code.strip().upper()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE dynamic_promos SET is_active = 0 WHERE code = ?",
                (c,),
            )
            return cur.rowcount > 0

    def dynamic_promo_delete(self, code: str) -> bool:
        """Полностью удаляет промокод и все активации."""
        c = code.strip().upper()
        if not c:
            return False
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM dynamic_promos WHERE code = ?", (c,))
            if not cur.fetchone():
                return False
            cur.execute("DELETE FROM dynamic_promo_uses WHERE code = ?", (c,))
            cur.execute("DELETE FROM dynamic_promos WHERE code = ?", (c,))
            return True

    def set_plus(self, user_id: int, enabled: bool = True) -> None:
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            if enabled:
                cur.execute(
                    "UPDATE users SET is_plus = 1, plus_expires_at = NULL WHERE user_id = ?",
                    (user_id,),
                )
            else:
                cur.execute(
                    "UPDATE users SET is_plus = 0, plus_expires_at = NULL WHERE user_id = ?",
                    (user_id,),
                )

    def extend_plus_days(self, user_id: int, days: int) -> None:
        """Включает PLUS и продлевает срок на ``days`` от текущего окончания (или от сейчас)."""
        if days <= 0:
            return
        self.get_or_create_user(user_id)
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                "SELECT is_plus, plus_expires_at FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            base = now
            if row and int(row["is_plus"]) and row["plus_expires_at"]:
                raw = str(row["plus_expires_at"])
                try:
                    pe = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if pe.tzinfo is None:
                        pe = pe.replace(tzinfo=timezone.utc)
                    if pe > base:
                        base = pe
                except ValueError:
                    pass
            new_exp = base + timedelta(days=days)
            cur.execute(
                "UPDATE users SET is_plus = 1, plus_expires_at = ? WHERE user_id = ?",
                (new_exp.isoformat(), user_id),
            )

    def set_plus_forever_paid(self, user_id: int) -> None:
        """PLUS без даты окончания (как промо)."""
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            cur.execute(
                "UPDATE users SET is_plus = 1, plus_expires_at = NULL WHERE user_id = ?",
                (user_id,),
            )

    def is_plus(self, user_id: int) -> bool:
        return bool(self.get_or_create_user(user_id).is_plus)

    def set_luck(self, user_id: int, enabled: bool = True) -> None:
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            if enabled:
                cur.execute(
                    """
                    UPDATE users SET has_luck = 1, luck_expires_at = NULL, luck_forever = 0,
                    luck_roll_paused = 0
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )
            else:
                cur.execute(
                    """
                    UPDATE users SET has_luck = 0, luck_expires_at = NULL, luck_forever = 0,
                    luck_roll_paused = 0
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )

    def is_luck(self, user_id: int) -> bool:
        u = self.get_or_create_user(user_id)
        if u.luck_forever:
            return True
        if u.luck_expires_at and not _luck_expires_expired(str(u.luck_expires_at)):
            return True
        return bool(u.has_luck)

    def is_luck_roll_active(self, user_id: int) -> bool:
        """Учитывать ли «Удачу» в подборе: активна подписка удачи и (для PLUS) не на паузе."""
        if not self.is_luck(user_id):
            return False
        u = self.get_or_create_user(user_id)
        if not int(u.is_plus):
            return True
        return int(u.luck_roll_paused) == 0

    def set_luck_roll_paused(self, user_id: int, paused: bool) -> None:
        """Пауза «Удачи» в крутке — только при активной подписке PLUS и активной удаче."""
        if not self.is_plus(user_id) or not self.is_luck(user_id):
            return
        v = 1 if paused else 0
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            cur.execute(
                "UPDATE users SET luck_roll_paused = ? WHERE user_id = ?",
                (v, user_id),
            )

    def extend_luck_delta(self, user_id: int, delta: timedelta) -> None:
        """Продлевает «Удачу» на интервал от текущего окончания или от сейчас."""
        if delta.total_seconds() <= 0:
            return
        self.get_or_create_user(user_id)
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                "SELECT has_luck, luck_expires_at, luck_forever FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            assert row is not None
            if int(row["luck_forever"]):
                return
            base = now
            raw = row["luck_expires_at"]
            if raw and not _luck_expires_expired(str(raw)):
                try:
                    pe = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if pe.tzinfo is None:
                        pe = pe.replace(tzinfo=timezone.utc)
                    if pe > base:
                        base = pe
                except ValueError:
                    pass
            new_exp = base + delta
            cur.execute(
                """
                UPDATE users SET has_luck = 1, luck_expires_at = ?, luck_forever = 0
                WHERE user_id = ?
                """,
                (new_exp.isoformat(), user_id),
            )

    def set_luck_forever_paid(self, user_id: int) -> None:
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE users SET has_luck = 1, luck_forever = 1, luck_expires_at = NULL
                WHERE user_id = ?
                """,
                (user_id,),
            )

    def luck_promo_try_consume(self, code: str, max_uses: int) -> bool:
        """
        Глобальный лимит активаций промокода «Удача».
        При ``max_uses`` <= 0 лимита нет (всегда True).
        """
        c = code.strip().upper()
        if not c or max_uses <= 0:
            return True
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM luck_promo_redemptions WHERE code = ?",
                (c,),
            )
            n = int(cur.fetchone()[0])
            if n >= max_uses:
                return False
            cur.execute("INSERT INTO luck_promo_redemptions (code) VALUES (?)", (c,))
        return True

    def luck_promo_uses_count(self, code: str) -> int:
        c = code.strip().upper()
        if not c:
            return 0
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM luck_promo_redemptions WHERE code = ?",
                (c,),
            )
            return int(cur.fetchone()[0])

    def saved_usernames_count(self, user_id: int) -> int:
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM saved_usernames WHERE user_id = ?",
                (user_id,),
            )
            return int(cur.fetchone()[0])

    def save_username(
        self, user_id: int, username: str
    ) -> Literal["not_plus", "limit", "duplicate", "saved"]:
        if not self.is_plus(user_id):
            return "not_plus"
        self.get_or_create_user(user_id)
        u = username.lower()
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM saved_usernames WHERE user_id = ?",
                (user_id,),
            )
            if int(cur.fetchone()[0]) >= SAVED_USERNAMES_LIMIT:
                return "limit"
            try:
                cur.execute(
                    "INSERT INTO saved_usernames (user_id, username) VALUES (?, ?)",
                    (user_id, u),
                )
                return "saved"
            except sqlite3.IntegrityError:
                return "duplicate"

    def list_saved(self, user_id: int, limit: int | None = None) -> list[str]:
        lim = SAVED_USERNAMES_LIMIT if limit is None else limit
        self.get_or_create_user(user_id)
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT username FROM saved_usernames
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, lim),
            )
            return [r[0] for r in cur.fetchall()]

    def remove_saved(self, user_id: int, username: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM saved_usernames WHERE user_id = ? AND username = ?",
                (user_id, username.lower()),
            )

    # ---- Fragment import / dataset ----

    def upsert_fragment_item(
        self,
        *,
        username: str,
        price_usd: float | None,
        source_url: str,
    ) -> None:
        username = username.lower()
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO fragment_items (username, price_usd, source_url)
                VALUES (?, ?, ?)
                """,
                (username, price_usd, source_url),
            )

    def get_fragment_prices(self, username: str, *, limit: int = 50) -> list[float]:
        username = username.lower()
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT price_usd
                FROM fragment_items
                WHERE username = ? AND price_usd IS NOT NULL
                ORDER BY imported_at DESC
                LIMIT ?
                """,
                (username, limit),
            )
            rows = cur.fetchall()
            return [float(r[0]) for r in rows if r[0] is not None]

    def iter_fragment_items(self, *, limit: int = 5000) -> list[FragmentItemRow]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT username, price_usd, source_url, imported_at
                FROM fragment_items
                ORDER BY imported_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                FragmentItemRow(
                    username=r["username"],
                    price_usd=r["price_usd"],
                    source_url=r["source_url"],
                    imported_at=r["imported_at"],
                )
                for r in rows
            ]

    def top_fragment_month(
        self,
        *,
        days: int = 30,
        limit: int = 10,
        only_letters_len: tuple[int, int] | None = (5, 6),
    ) -> list[tuple[str, float]]:
        import datetime as _dt

        since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        args: list[object] = [since, limit]

        where = "imported_at >= ?"
        # SQLite REGEXP нет по умолчанию, поэтому фильтруем длину/буквы в Python ниже.

        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT username, MAX(price_usd) as best_price
                FROM fragment_items
                WHERE {where}
                GROUP BY username
                HAVING best_price IS NOT NULL
                ORDER BY best_price DESC
                LIMIT ?
                """,
                tuple(args),
            )
            rows = cur.fetchall()

        out: list[tuple[str, float]] = []
        for r in rows:
            uname = str(r[0]).lower()
            price = float(r[1])
            if only_letters_len is not None:
                min_len, max_len = only_letters_len
                if not (min_len <= len(uname) <= max_len and uname.isalpha() and uname.islower()):
                    continue
            out.append((uname, price))
        return out[:limit]

    # ---- Roll events / monthly top ----

    def add_roll_event(
        self,
        *,
        user_id: int,
        username: str,
        rarity: str,
        predicted_price_usd: float | None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO roll_events (user_id, username, rarity, predicted_price_usd)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, username.lower(), rarity, predicted_price_usd),
            )

    def top_roll_month(
        self,
        *,
        days: int = 30,
        limit: int = 10,
    ) -> list[tuple[str, str, float]]:
        import datetime as _dt

        since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT username, rarity, MAX(predicted_price_usd) as best_pred
                FROM roll_events
                WHERE created_at >= ?
                GROUP BY username
                HAVING best_pred IS NOT NULL
                ORDER BY best_pred DESC
                LIMIT ?
                """,
                (since, limit),
            )
            rows = cur.fetchall()
            return [(r[0], r[1], float(r[2])) for r in rows]
