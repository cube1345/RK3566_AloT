# 临时文件系统数据库 — SQLite 封装
import sqlite3
import time
from pathlib import Path
from typing import Any
from config import DB_PATH


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class Database:
    def __init__(self):
        self.conn = ensure_db()
        self._init_tables()

    def _init_tables(self):
        c = self.conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS sensor_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                timestamp REAL NOT NULL
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                detail TEXT,
                source TEXT,
                timestamp REAL NOT NULL
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS foods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT,
                quantity REAL DEFAULT 1,
                unit TEXT DEFAULT '个',
                purchase_date TEXT,
                expiry_date TEXT NOT NULL,
                storage TEXT DEFAULT '冷藏',
                notes TEXT,
                created_at REAL DEFAULT (strftime('%s','now'))
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS home_tips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                content TEXT NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now'))
            )""")
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_sensor_log_sensor ON sensor_log(sensor)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_sensor_log_time ON sensor_log(timestamp)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_foods_expiry ON foods(expiry_date)
        """)
        self.conn.commit()

    # ---- 传感器日志 ----
    def log_sensor(self, sensor: str, value: float, unit: str = ""):
        self.conn.execute(
            "INSERT INTO sensor_log(sensor, value, unit, timestamp) VALUES (?,?,?,?)",
            (sensor, value, unit, time.time()),
        )
        self.conn.commit()

    def query_sensor(self, sensor: str, hours: int = 24) -> list[dict]:
        cutoff = time.time() - hours * 3600
        return self._fetch_dict(
            "SELECT value, unit, timestamp FROM sensor_log WHERE sensor=? AND timestamp>=? ORDER BY timestamp",
            (sensor, cutoff),
        )

    # ---- 事件日志 ----
    def log_event(self, event_type: str, detail: str = "", source: str = ""):
        self.conn.execute(
            "INSERT INTO event_log(event_type, detail, source, timestamp) VALUES (?,?,?,?)",
            (event_type, detail, source, time.time()),
        )
        self.conn.commit()

    def query_events(self, hours: int = 24) -> list[dict]:
        cutoff = time.time() - hours * 3600
        return self._fetch_dict(
            "SELECT event_type, detail, source, timestamp FROM event_log WHERE timestamp>=? ORDER BY timestamp DESC",
            (cutoff,),
        )

    # ---- 食材管理 ----
    def add_food(self, name: str, expiry_date: str, storage: str = "冷藏",
                 quantity: float = 1, unit: str = "个", category: str = "",
                 notes: str = "") -> int:
        c = self.conn.execute(
            "INSERT INTO foods(name, category, quantity, unit, purchase_date, expiry_date, storage, notes) VALUES (?,?,?,?,date('now'),?,?,?)",
            (name, category, quantity, unit, expiry_date, storage, notes),
        )
        self.conn.commit()
        return c.lastrowid

    def _fetch_dict(self, sql: str, params=()) -> list[dict]:
        cur = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def list_foods(self, expiring_days: int = None) -> list[dict]:
        if expiring_days:
            import datetime
            deadline = (datetime.date.today() + datetime.timedelta(days=expiring_days)).isoformat()
            return self._fetch_dict(
                "SELECT * FROM foods WHERE expiry_date <= ? ORDER BY expiry_date",
                (deadline,),
            )
        return self._fetch_dict("SELECT * FROM foods ORDER BY expiry_date")

    def remove_food(self, food_id: int):
        self.conn.execute("DELETE FROM foods WHERE id=?", (food_id,))
        self.conn.commit()

    def update_food(self, food_id: int, **kwargs):
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [food_id]
        self.conn.execute(f"UPDATE foods SET {sets} WHERE id=?", vals)
        self.conn.commit()

    def search_food(self, keyword: str) -> list[dict]:
        return self._fetch_dict(
            "SELECT * FROM foods WHERE name LIKE ?", (f"%{keyword}%",)
        )

    # ---- 家庭小贴士 ----
    def add_tip(self, category: str, content: str) -> int:
        c = self.conn.execute(
            "INSERT INTO home_tips(category, content) VALUES (?,?)",
            (category, content),
        )
        self.conn.commit()
        return c.lastrowid

    def list_tips(self, category: str = None) -> list[dict]:
        if category:
            return self._fetch_dict(
                "SELECT * FROM home_tips WHERE category=? ORDER BY created_at DESC",
                (category,),
            )
        return self._fetch_dict("SELECT * FROM home_tips ORDER BY created_at DESC")

    def close(self):
        self.conn.close()
