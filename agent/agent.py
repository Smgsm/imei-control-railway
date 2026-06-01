#!/usr/bin/env python3
import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

IMEI_RE = re.compile(r"(?<!\d)(\d{15})(?!\d)")


def iso_from_timestamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ["server_url", "token", "location_code", "location_name", "folder"]:
        if not cfg.get(key):
            raise SystemExit(f"Config ausente: {key}")
    cfg["server_url"] = cfg["server_url"].rstrip("/")
    cfg["interval_seconds"] = int(cfg.get("interval_seconds") or 60)
    cfg["recent_minutes"] = int(cfg.get("recent_minutes") or 1440)
    cfg["extensions"] = {e.lower() for e in cfg.get("extensions", [])}
    return cfg


def state_db(config_path):
    path = Path(config_path).with_suffix(".state.sqlite3")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_files (
          path TEXT PRIMARY KEY,
          mtime REAL NOT NULL,
          size INTEGER NOT NULL,
          sent_at TEXT NOT NULL
        )
    """)
    return conn


def was_sent(conn, path, stat):
    row = conn.execute("SELECT 1 FROM sent_files WHERE path=? AND mtime=? AND size=?", (str(path), stat.st_mtime, stat.st_size)).fetchone()
    return bool(row)


def mark_sent(conn, path, stat):
    conn.execute("""
        INSERT INTO sent_files (path, mtime, size, sent_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, sent_at=excluded.sent_at
    """, (str(path), stat.st_mtime, stat.st_size, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def recent_files(cfg):
    root = Path(cfg["folder"])
    if not root.exists():
        raise FileNotFoundError(f"Pasta nao encontrada: {root}")
    cutoff = time.time() - cfg["recent_minutes"] * 60
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if cfg["extensions"] and path.suffix.lower() not in cfg["extensions"]:
            continue
        stat = path.stat()
        if stat.st_mtime >= cutoff:
            yield path, stat


def extract_imeis(path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return sorted(set(IMEI_RE.findall(text)))


def send(cfg, records):
    payload = {
        "location_code": cfg["location_code"],
        "location_name": cfg["location_name"],
        "folder": cfg["folder"],
        "records": records,
    }
    req = urllib.request.Request(
        cfg["server_url"] + "/api/ingest",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Ingest-Token": cfg["token"]},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def scan_once(cfg, conn):
    records = []
    files = []
    for path, stat in recent_files(cfg):
        if was_sent(conn, path, stat):
            continue
        imeis = extract_imeis(path)
        if not imeis:
            mark_sent(conn, path, stat)
            continue
        for imei in imeis:
            records.append({"imei": imei, "file_name": path.name, "file_path": str(path), "modified_at": iso_from_timestamp(stat.st_mtime)})
        files.append((path, stat))
    if not records:
        print("Nenhum IMEI novo encontrado.")
        return
    result = send(cfg, records)
    for path, stat in files:
        mark_sent(conn, path, stat)
    print(f"Enviados {result.get('saved', 0)} IMEIs de {len(files)} arquivo(s).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    conn = state_db(args.config)
    while True:
        try:
            scan_once(cfg, conn)
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}")
        except Exception as e:
            print(f"Erro: {e}")
        if args.once:
            break
        time.sleep(cfg["interval_seconds"])


if __name__ == "__main__":
    main()
