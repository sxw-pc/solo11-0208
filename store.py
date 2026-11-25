import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS donations(id TEXT PRIMARY KEY, kind TEXT NOT NULL, donor TEXT NOT NULL, policy TEXT CHECK(policy IN ('keep','release')) NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS inspections(part TEXT REFERENCES donations, rev INTEGER NOT NULL CHECK(rev>0), usable INTEGER CHECK(usable IN (0,1)) NOT NULL, PRIMARY KEY(part,rev)) STRICT;
CREATE TABLE IF NOT EXISTS substitutes(kind TEXT, need TEXT, minutes INTEGER CHECK(minutes>0) NOT NULL, PRIMARY KEY(kind,need), CHECK(kind<>need)) STRICT;
CREATE TABLE IF NOT EXISTS volunteers(id TEXT PRIMARY KEY, skill TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS bookings(job TEXT PRIMARY KEY REFERENCES requests, station TEXT NOT NULL, worker TEXT REFERENCES volunteers NOT NULL, start INTEGER NOT NULL CHECK(start>=0), stop INTEGER NOT NULL CHECK(stop>start)) STRICT;
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, need TEXT NOT NULL, skill TEXT NOT NULL, minutes INTEGER CHECK(minutes>0) NOT NULL, priority INTEGER CHECK(priority>=0) NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS fittings(
    id TEXT PRIMARY KEY, job TEXT REFERENCES requests NOT NULL, part TEXT REFERENCES donations NOT NULL,
    worker TEXT REFERENCES volunteers NOT NULL, inspection_rev INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('mounted','removed','returned')),
    FOREIGN KEY(part,inspection_rev) REFERENCES inspections(part,rev)
) STRICT;
CREATE TABLE IF NOT EXISTS allocations(
    job TEXT PRIMARY KEY REFERENCES requests, part TEXT UNIQUE REFERENCES donations NOT NULL,
    fitting TEXT UNIQUE REFERENCES fittings, returned INTEGER NOT NULL DEFAULT 0 CHECK(returned IN (0,1))
) STRICT;
CREATE TABLE IF NOT EXISTS checks(
    id TEXT PRIMARY KEY, fitting TEXT REFERENCES fittings NOT NULL, reviewer TEXT REFERENCES volunteers NOT NULL,
    inspection_rev INTEGER NOT NULL, passed INTEGER CHECK(passed IN (0,1)) NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS filings(
    id TEXT PRIMARY KEY, station TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('accepted','rejected','pending')), reason TEXT, batch TEXT NOT NULL
) STRICT;
"""


def connect(path, readonly=False):
    db = sqlite3.connect(path, isolation_level=None, uri=readonly)
    db.execute('PRAGMA foreign_keys=ON')
    if not readonly:
        db.executescript(SCHEMA)
    return db


def one(db, sql, *args):
    return db.execute(sql, args).fetchone()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)
