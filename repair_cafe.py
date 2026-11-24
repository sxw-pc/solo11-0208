import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import bench
import parts
import repairs
import scan_intake
from store import connect, one, require

GROUPS = {parts: 'donate inspect substitute retention', bench: 'volunteer book cancel',
          repairs: 'request allocate fit release check handover'}


def load(db, events):
    db.execute('BEGIN IMMEDIATE')
    try:
        require(isinstance(events, list), 'event batch must be an array')
        for row in events:
            require(isinstance(row, list) and len(row) >= 2, 'invalid event row')
            event, op, *args = row
            require(isinstance(event, str) and event.strip(), 'event id required')
            payload = json.dumps([event, op, *args], ensure_ascii=False, separators=(',', ':'))
            old = one(db, 'SELECT payload FROM journal WHERE id=?', event)
            if old:
                require(old == (payload,), 'event id conflict')
                continue
            if op == 'receive-scan-bag':
                source, bag = args
                for donation in scan_intake.donations(source, bag):
                    parts.apply(db, 'donate', donation)
            else:
                module = next((m for m, names in GROUPS.items() if op in names.split()), None)
                require(module, f'unknown operation {op}')
                if module is repairs:
                    module.apply(db, op, args, event)
                else:
                    module.apply(db, op, args)
            db.execute('INSERT INTO journal VALUES(?,?)', (event, payload))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def snapshot(db):
    return {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            for table in ('journal', 'donations', 'inspections', 'substitutes', 'volunteers', 'bookings', 'requests', 'fittings', 'allocations', 'checks')}


def report(db):
    print('allocations', json.dumps(dict(db.execute('SELECT job,part FROM allocations ORDER BY job'))))
    print('returned', json.dumps([job for job, in db.execute('SELECT job FROM allocations WHERE returned=1 ORDER BY job')]))
    print('donations', json.dumps({part: bool(parts.usable(db, part)) for part, in db.execute('SELECT id FROM donations ORDER BY id')}))
    print('booked_minutes', json.dumps(dict(db.execute('SELECT job,stop-start FROM bookings ORDER BY job'))))
    print('fitted', json.dumps([job for job, in db.execute('SELECT job FROM allocations WHERE fitting IS NOT NULL ORDER BY job')]))
    print('next', json.dumps(repairs.plan(db)))


def main():
    parser = argparse.ArgumentParser(description='Offline repair café ledger')
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('action', choices=('demo', 'load', 'plan', 'audit', 'scan-intake'))
    parser.add_argument('file', nargs='?', type=Path)
    args = parser.parse_args()
    try:
        require((args.action in ('load', 'scan-intake')) == bool(args.file),
                'a fixture path is required only for load and scan-intake')
        readonly = args.action in ('audit', 'plan')
        path = args.db.resolve().as_uri() + '?mode=ro' if readonly else args.db
        with closing(connect(path, readonly)) as db:
            if args.action in ('demo', 'load'):
                path = args.file or Path(__file__).with_name('fixtures') / 'demo.json'
                load(db, json.loads(path.read_text(encoding='utf-8')))
            if args.action == 'scan-intake':
                load(db, scan_intake.receive(json.loads(args.file.read_text(encoding='utf-8'))))
            if args.action == 'audit':
                db.execute('BEGIN')
                events = [json.loads(row[0]) for row in db.execute('SELECT payload FROM journal ORDER BY rowid')]
                with closing(connect(':memory:')) as replay:
                    load(replay, events)
                    require(snapshot(db) == snapshot(replay), 'audit mismatch')
                    print(f'audit OK: {len(events)} events')
                    report(replay)
            else:
                db.execute('BEGIN')
                report(db)
    except (ValueError, TypeError, IndexError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
