import sqlite3
import threading
import time

import config

_lock = threading.RLock()
_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

_conn.executescript("""
CREATE TABLE IF NOT EXISTS links(
  platform TEXT, chat_id TEXT, username TEXT, chat_type TEXT, linked_at REAL,
  PRIMARY KEY(platform, chat_id));
CREATE TABLE IF NOT EXISTS states(
  platform TEXT, chat_id TEXT, step TEXT, username TEXT, otp_hash TEXT,
  expires REAL, attempts INTEGER, starter TEXT,
  PRIMARY KEY(platform, chat_id));
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, action TEXT,
  platform TEXT, chat_id TEXT, username TEXT, provider TEXT,
  status TEXT, created REAL);
CREATE TABLE IF NOT EXISTS groups(
  platform TEXT, chat_id TEXT, muted INTEGER DEFAULT 0, cooldown INTEGER,
  PRIMARY KEY(platform, chat_id));
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
""")


def _q(sql, args=(), one=False, write=False):
    with _lock:
        cur = _conn.execute(sql, args)
        if write:
            _conn.commit()
            return cur.lastrowid
        rows = cur.fetchall()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(r) for r in rows]


# ----- links -----
def link_get(p, c):
    return _q("SELECT * FROM links WHERE platform=? AND chat_id=?", (p, c), one=True)


def link_set(p, c, username, chat_type):
    _q("INSERT OR REPLACE INTO links VALUES(?,?,?,?,?)",
       (p, c, username, chat_type, time.time()), write=True)


def link_by_username(username):
    return _q("SELECT * FROM links WHERE lower(username)=lower(?)", (username,), one=True)


def link_del_username(username):
    _q("DELETE FROM links WHERE lower(username)=lower(?)", (username,), write=True)


def link_del(p, c):
    _q("DELETE FROM links WHERE platform=? AND chat_id=?", (p, c), write=True)


# ----- login state -----
def state_get(p, c):
    return _q("SELECT * FROM states WHERE platform=? AND chat_id=?", (p, c), one=True)


def state_set(p, c, step, username="", otp_hash="", expires=0.0, attempts=0, starter=""):
    _q("INSERT OR REPLACE INTO states VALUES(?,?,?,?,?,?,?,?)",
       (p, c, step, username, otp_hash, expires, attempts, starter), write=True)


def state_del(p, c):
    _q("DELETE FROM states WHERE platform=? AND chat_id=?", (p, c), write=True)


# ----- requests sent to providers -----
def request_add(order_id, action, p, c, username, provider):
    return _q("INSERT INTO requests(order_id,action,platform,chat_id,username,provider,status,created)"
              " VALUES(?,?,?,?,?,?,?,?)",
              (str(order_id), action, p, c, username, provider, "sent", time.time()), write=True)


def request_get(rid):
    return _q("SELECT * FROM requests WHERE id=?", (rid,), one=True)


def request_set_status(rid, status):
    _q("UPDATE requests SET status=? WHERE id=?", (status, rid), write=True)


def request_recent(order_id, action, window):
    return _q("SELECT * FROM requests WHERE order_id=? AND action=? AND created>? "
              "ORDER BY id DESC LIMIT 1", (str(order_id), action, time.time() - window), one=True)


# ----- groups -----
def group_get(p, c):
    row = _q("SELECT * FROM groups WHERE platform=? AND chat_id=?", (p, c), one=True)
    if not row:
        return {"muted": 0, "cooldown": config.GROUP_COOLDOWN}
    if row["cooldown"] is None:
        row["cooldown"] = config.GROUP_COOLDOWN
    return row


def group_set(p, c, muted=None, cooldown=None):
    cur = group_get(p, c)
    muted = cur["muted"] if muted is None else muted
    cooldown = cur["cooldown"] if cooldown is None else cooldown
    _q("INSERT OR REPLACE INTO groups VALUES(?,?,?,?)", (p, c, muted, cooldown), write=True)


# ----- small key/value (locks, round robin, notice timers) -----
def kv_get(k, default=None):
    row = _q("SELECT v FROM kv WHERE k=?", (k,), one=True)
    return row["v"] if row else default


def kv_set(k, v):
    _q("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)), write=True)
