import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS scans (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        # An in-process worker cannot survive an application restart.
        with self.connect() as db:
            for scan_id, payload in db.execute("SELECT id, payload FROM scans").fetchall():
                record = json.loads(payload)
                if record["status"] in {"queued", "running"}:
                    record.update(status="failed", stage="服务重启", error="服务已重启，请重新提交扫描。")
                    db.execute("UPDATE scans SET payload=? WHERE id=?", (json.dumps(record, ensure_ascii=False), scan_id))

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def put(self, record):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO scans VALUES (?, ?)", (record["id"], json.dumps(record, ensure_ascii=False)))

    def get(self, scan_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM scans WHERE id=?", (scan_id,)).fetchone()
        return json.loads(row[0]) if row else None
