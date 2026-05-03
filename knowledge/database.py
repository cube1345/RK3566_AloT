# 临时文件系统数据库 — SQLite 封装
import sqlite3
import time
from pathlib import Path
from typing import Any
from config import DB_PATH


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                count INTEGER DEFAULT 1,
                last_updated REAL NOT NULL
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_snapshot TEXT NOT NULL,
                actions_taken TEXT,
                llm_reasoning TEXT,
                anomaly_detected INTEGER DEFAULT 0,
                timestamp REAL NOT NULL
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS routines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pattern_type TEXT,
                pattern_data TEXT NOT NULL,
                actions TEXT,
                auto_detected INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0.5,
                last_fired_at REAL,
                enabled INTEGER DEFAULT 1,
                created_at REAL DEFAULT (strftime('%s','now'))
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_behaviors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                behavior_type TEXT NOT NULL,
                detail TEXT,
                agent TEXT,
                hour_of_day INTEGER,
                day_of_week INTEGER,
                duration_ms REAL DEFAULT 0,
                timestamp REAL NOT NULL
            )""")
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_behavior_type ON user_behaviors(behavior_type)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_behavior_hour ON user_behaviors(hour_of_day)
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL UNIQUE,
                vector_json TEXT NOT NULL,
                confidence REAL DEFAULT 0.3,
                sample_count INTEGER DEFAULT 0,
                last_updated REAL NOT NULL
            )""")
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

    # ---- 用户偏好学习 ----
    def save_pref(self, key: str, value: str, confidence_delta: float = 0.15):
        cur = self.conn.execute(
            "SELECT confidence, count FROM user_prefs WHERE key=?", (key,)
        )
        row = cur.fetchone()
        if row:
            new_conf = min(1.0, row[0] + confidence_delta)
            self.conn.execute(
                "UPDATE user_prefs SET value=?, confidence=?, count=count+1, last_updated=? WHERE key=?",
                (value, new_conf, time.time(), key),
            )
        else:
            initial = min(1.0, 0.5 + confidence_delta)
            self.conn.execute(
                "INSERT INTO user_prefs(key, value, confidence, last_updated) VALUES (?,?,?,?)",
                (key, value, initial, time.time()),
            )
        self.conn.commit()

    def get_prefs(self, min_confidence: float = 0.3) -> dict:
        rows = self.conn.execute(
            "SELECT key, value FROM user_prefs WHERE confidence >= ?", (min_confidence,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def delete_pref(self, key: str):
        self.conn.execute("DELETE FROM user_prefs WHERE key=?", (key,))
        self.conn.commit()

    # ---- AI决策审计 ----
    def log_ai_decision(self, snapshot: str, actions: str, reasoning: str = "",
                        anomaly: bool = False):
        self.conn.execute(
            "INSERT INTO ai_decisions(sensor_snapshot, actions_taken, llm_reasoning, anomaly_detected, timestamp) VALUES (?,?,?,?,?)",
            (snapshot, actions, reasoning, 1 if anomaly else 0, time.time()),
        )
        self.conn.commit()

    def recent_ai_decisions(self, limit: int = 5) -> list[dict]:
        return self._fetch_dict(
            "SELECT * FROM ai_decisions ORDER BY timestamp DESC LIMIT ?", (limit,)
        )

    # ---- 知识查询 (v5.1 - 历史对比) ----
    def query_sensor_same_hour(self, sensor: str, days: int = 7) -> list[dict]:
        """同时段历史对比：过去N天同一小时的数据"""
        cutoff = time.time() - days * 86400
        return self._fetch_dict(
            """SELECT value, unit, timestamp FROM sensor_log
               WHERE sensor=? AND timestamp>=?
                 AND strftime('%H', datetime(timestamp, 'unixepoch')) = strftime('%H', datetime(?, 'unixepoch'))
               ORDER BY timestamp""",
            (sensor, cutoff, time.time()),
        )

    def query_sensor_hourly_profile(self, sensor: str, days: int = 7) -> list[dict]:
        """日均曲线：按小时分组的 AVG/MIN/MAX"""
        cutoff = time.time() - days * 86400
        return self._fetch_dict(
            """SELECT strftime('%H', datetime(timestamp, 'unixepoch')) AS hour,
                      AVG(value) AS avg, MIN(value) AS min, MAX(value) AS max, COUNT(*) AS n
               FROM sensor_log WHERE sensor=? AND timestamp>=?
               GROUP BY hour ORDER BY hour""",
            (sensor, cutoff),
        )

    def query_sensor_correlation(self, sensor_a: str, sensor_b: str,
                                  hours: int = 24) -> list[dict]:
        """两传感器时间对齐关联 (分钟桶 JOIN)"""
        cutoff = time.time() - hours * 3600
        return self._fetch_dict(
            """SELECT a.bucket, a.value AS a_val, b.value AS b_val FROM
               (SELECT (timestamp/60)*60 AS bucket, AVG(value) AS value
                FROM sensor_log WHERE sensor=? AND timestamp>=? GROUP BY bucket) a
               JOIN
               (SELECT (timestamp/60)*60 AS bucket, AVG(value) AS value
                FROM sensor_log WHERE sensor=? AND timestamp>=? GROUP BY bucket) b
               ON a.bucket=b.bucket ORDER BY a.bucket""",
            (sensor_a, cutoff, sensor_b, cutoff),
        )

    # ---- 规律/日程 (v5.1) ----
    def save_routine(self, name: str, pattern_data: str,
                      pattern_type: str = "", actions: str = "",
                      auto_detected: bool = False) -> int:
        c = self.conn.execute(
            "INSERT INTO routines(name, pattern_type, pattern_data, actions, auto_detected) VALUES (?,?,?,?,?)",
            (name, pattern_type, pattern_data, actions, 1 if auto_detected else 0),
        )
        self.conn.commit()
        return c.lastrowid

    def list_routines(self, enabled_only: bool = True) -> list[dict]:
        if enabled_only:
            return self._fetch_dict("SELECT * FROM routines WHERE enabled=1 ORDER BY id")
        return self._fetch_dict("SELECT * FROM routines ORDER BY id")

    def update_routine(self, routine_id: int, **kwargs):
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [routine_id]
        self.conn.execute(f"UPDATE routines SET {sets} WHERE id=?", vals)
        self.conn.commit()

    def fire_routine(self, routine_id: int):
        self.conn.execute(
            "UPDATE routines SET last_fired_at=? WHERE id=?", (time.time(), routine_id),
        )
        self.conn.commit()

    def delete_routine(self, routine_id: int):
        self.conn.execute("DELETE FROM routines WHERE id=?", (routine_id,))
        self.conn.commit()

    # ---- 用户行为追踪 (v5.2) ----
    def log_behavior(self, behavior_type: str, detail: str = "", agent: str = "",
                     hour: int = None, day: int = None, duration_ms: float = 0):
        self.conn.execute(
            "INSERT INTO user_behaviors(behavior_type, detail, agent, hour_of_day, day_of_week, duration_ms, timestamp) VALUES (?,?,?,?,?,?,?)",
            (behavior_type, detail, agent, hour, day, duration_ms, time.time()),
        )
        self.conn.commit()

    def get_behavior_stats(self, hours: int = 168) -> dict:
        """返回最近N小时的行为统计"""
        cutoff = time.time() - hours * 3600
        total = self.conn.execute(
            "SELECT COUNT(*) FROM user_behaviors WHERE timestamp>=?", (cutoff,)
        ).fetchone()[0]
        if total == 0:
            return {"total": 0, "by_type": {}, "by_hour": {}, "by_agent": {}}
        by_type_rows = self.conn.execute(
            "SELECT behavior_type, COUNT(*) FROM user_behaviors WHERE timestamp>=? GROUP BY behavior_type",
            (cutoff,),
        ).fetchall()
        by_hour_rows = self.conn.execute(
            "SELECT hour_of_day, COUNT(*) FROM user_behaviors WHERE timestamp>=? AND hour_of_day IS NOT NULL GROUP BY hour_of_day ORDER BY hour_of_day",
            (cutoff,),
        ).fetchall()
        by_agent_rows = self.conn.execute(
            "SELECT agent, COUNT(*) FROM user_behaviors WHERE timestamp>=? AND agent!='' GROUP BY agent",
            (cutoff,),
        ).fetchall()
        return {
            "total": total,
            "by_type": {r[0]: r[1] for r in by_type_rows},
            "by_hour": {r[0]: r[1] for r in by_hour_rows},
            "by_agent": {r[0]: r[1] for r in by_agent_rows},
        }

    def get_behavior_hourly_histogram(self, days: int = 30) -> list[dict]:
        """按时段统计行为分布"""
        cutoff = time.time() - days * 86400
        return self._fetch_dict(
            "SELECT hour_of_day, COUNT(*) AS count FROM user_behaviors WHERE timestamp>=? AND hour_of_day IS NOT NULL GROUP BY hour_of_day ORDER BY hour_of_day",
            (cutoff,),
        )

    def get_recent_commands(self, limit: int = 100) -> list[dict]:
        return self._fetch_dict(
            "SELECT * FROM user_behaviors WHERE behavior_type='command' ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    def get_behavior_by_date(self, days: int = 30) -> list[dict]:
        """按日期+时段统计行为活跃度"""
        cutoff = time.time() - days * 86400
        return self._fetch_dict(
            "SELECT date(datetime(timestamp, 'unixepoch')) AS dt, hour_of_day, COUNT(*) AS cnt FROM user_behaviors WHERE timestamp>=? AND hour_of_day IS NOT NULL GROUP BY dt, hour_of_day ORDER BY dt, hour_of_day",
            (cutoff,),
        )

    # ---- 用户画像 (v5.2) ----
    def save_profile_dimension(self, dimension: str, vector_json: str,
                                confidence_delta: float = 0.1):
        cur = self.conn.execute(
            "SELECT confidence, sample_count FROM user_profile WHERE dimension=?", (dimension,)
        )
        row = cur.fetchone()
        if row:
            new_conf = min(1.0, row[0] + confidence_delta)
            self.conn.execute(
                "UPDATE user_profile SET vector_json=?, confidence=?, sample_count=sample_count+1, last_updated=? WHERE dimension=?",
                (vector_json, new_conf, time.time(), dimension),
            )
        else:
            initial = min(1.0, 0.3 + confidence_delta)
            self.conn.execute(
                "INSERT INTO user_profile(dimension, vector_json, confidence, sample_count, last_updated) VALUES (?,?,?,1,?)",
                (dimension, vector_json, initial, time.time()),
            )
        self.conn.commit()

    def get_profile(self, dimensions: list[str] = None) -> dict:
        """获取指定(或全部)画像维度, 返回 {dimension: parsed_vector}"""
        import json
        if dimensions:
            placeholders = ",".join("?" * len(dimensions))
            rows = self.conn.execute(
                f"SELECT dimension, vector_json, confidence, sample_count FROM user_profile WHERE dimension IN ({placeholders})",
                dimensions,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT dimension, vector_json, confidence, sample_count FROM user_profile"
            ).fetchall()
        result = {}
        for r in rows:
            try:
                result[r[0]] = {"value": json.loads(r[1]), "confidence": r[2], "samples": r[3]}
            except (json.JSONDecodeError, TypeError):
                result[r[0]] = {"value": r[1], "confidence": r[2], "samples": r[3]}
        return result

    def get_full_profile(self) -> dict:
        """仪表盘用的完整画像"""
        profile = self.get_profile()
        return {
            "dimensions": profile,
            "updated_at": time.time(),
        }

    # ---- 规律自动发现 (v5.2) ----
    def detect_routines_sql(self, min_occurrences: int = 3, min_days: int = 2) -> list[dict]:
        """基于SQL聚合发现重复行为模式"""
        results = self.conn.execute(
            """SELECT behavior_type, agent, hour_of_day, COUNT(*) AS total,
                      COUNT(DISTINCT date(datetime(timestamp, 'unixepoch'))) AS day_count
               FROM user_behaviors WHERE hour_of_day IS NOT NULL
               GROUP BY behavior_type, agent, hour_of_day
               HAVING total >= ? AND day_count >= ?
               ORDER BY total DESC""",
            (min_occurrences, min_days),
        ).fetchall()
        return [
            {"behavior_type": r[0], "agent": r[1], "hour": r[2],
             "total": r[3], "day_count": r[4]}
            for r in results
        ]

    def close(self):
        self.conn.close()
