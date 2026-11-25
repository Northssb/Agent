"""读取或更新当前系统用户的个人排程快照。"""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

from plan_resume import build_snapshot, record_feedback


def read_snapshot(db):
    # 初次保存前，以及首次写入回滚后，都可能还没有数据表。
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan'").fetchone()
    row = db.execute("SELECT snapshot FROM plan WHERE id=1").fetchone() if exists else None
    return json.loads(row[0]) if row else {"revision": 0, "tasks": [], "days": []}


def show(path):
    if not path.exists():
        return {"revision": 0, "tasks": [], "days": []}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return read_snapshot(db)


def save(path, data, update):
    if not isinstance(data, dict) or type(data.get("revision")) is not int or data["revision"] < 0:
        raise ValueError("请先读取本机计划，并传入其整数 revision。")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    attributes = path.parent.stat()
    if attributes.st_uid != os.getuid() or attributes.st_mode & 0o077:
        raise ValueError("个人计划目录须仅限当前用户访问。")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        previous = read_snapshot(db)
        if previous["revision"] != data["revision"]:
            raise ValueError("计划已在其他对话中更新，请重新 show 并合并本次变更。")
        result = update(previous, data)
        result["revision"] = data["revision"] + 1
        stored = json.dumps(result, ensure_ascii=False, allow_nan=False)
        db.execute("CREATE TABLE IF NOT EXISTS plan (id INTEGER PRIMARY KEY CHECK (id=1), snapshot TEXT NOT NULL)")
        db.execute("INSERT OR REPLACE INTO plan (id, snapshot) VALUES (1, ?)", (stored,))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["show", "schedule", "feedback"])
    action = parser.parse_args().action
    path = Path.home() / ".local" / "share" / "personal-worktime-agent" / "plan.sqlite3"
    os.umask(0o077)
    try:
        if action == "show":
            result = show(path)
        else:
            update = build_snapshot if action == "schedule" else record_feedback
            result = save(path, json.load(sys.stdin), update)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error) as error:
        sys.exit(f"个人计划未更新。{error}")
    print(json.dumps(result, ensure_ascii=False))
