import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import bench
import parts
import repairs
from store import connect, one, require

GROUPS = {parts: 'donate inspect substitute retention', bench: 'volunteer book cancel',
          repairs: 'request allocate fit release check handover'}


def apply_event(db, row):
    require(isinstance(row, list) and len(row) >= 2, 'invalid event row')
    event, op, *args = row
    require(isinstance(event, str) and event.strip(), 'event id required')
    payload = json.dumps([event, op, *args], ensure_ascii=False, separators=(',', ':'))
    old = one(db, 'SELECT payload FROM journal WHERE id=?', event)
    if old:
        require(old == (payload,), 'event id conflict')
        return False
    module = next((m for m, names in GROUPS.items() if op in names.split()), None)
    require(module, f'unknown operation {op}')
    if module is repairs:
        module.apply(db, op, args, event)
    else:
        module.apply(db, op, args)
    db.execute('INSERT INTO journal VALUES(?,?)', (event, payload))
    return True


def load(db, events):
    db.execute('BEGIN IMMEDIATE')
    try:
        require(isinstance(events, list), 'event batch must be an array')
        for row in events:
            apply_event(db, row)
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
    parser.add_argument('action', choices=('demo', 'load', 'plan', 'audit', 'intake'))
    parser.add_argument('file', nargs='*', type=Path)
    parser.add_argument('--accept', help='intake: submit the preview option with this verdict receipt id or index')
    args = parser.parse_args()
    try:
        require(args.action != 'load' or len(args.file) == 1, 'load requires exactly one fixture path')
        require(args.action != 'intake' or len(args.file) == 2, 'intake requires exactly two receipt files')
        require(args.action in ('load', 'intake') or not args.file,
                'a fixture path is required only for load or intake')
        require(args.action == 'intake' or args.accept is None, '--accept is only valid for intake')
        readonly = args.action in ('audit', 'plan') or (args.action == 'intake' and args.accept is None)
        path = args.db.resolve().as_uri() + '?mode=ro' if readonly else args.db
        with closing(connect(path, readonly)) as db:
            if args.action in ('demo', 'load'):
                fixture = args.file[0] if args.file else Path(__file__).with_name('fixtures') / 'demo.json'
                load(db, json.loads(fixture.read_text(encoding='utf-8')))
            elif args.action == 'intake':
                import capture_merge
                files = [(f.name, json.loads(f.read_text(encoding='utf-8'))) for f in args.file]
                receipts = capture_merge.normalize(files)
                if args.accept is None:
                    print(json.dumps(capture_merge.present(capture_merge.preview(db, receipts)),
                                     ensure_ascii=False))
                else:
                    chosen = capture_merge.submit(db, receipts, args.accept)
                    print(f"submitted {' '.join(r['id'] for r in chosen['accepted'])}")
            if args.action == 'audit':
                import capture_merge
                db.execute('BEGIN')
                events = [json.loads(row[0]) for row in db.execute('SELECT payload FROM journal ORDER BY rowid')]
                filed = capture_merge.filings(db)
                with closing(connect(':memory:')) as replay:
                    capture_merge.replay(replay, events, filed)
                    require(snapshot(db) == snapshot(replay), 'audit mismatch')
                    print(f'audit OK: {len(events)} events')
                    if filed:
                        accepted, held = capture_merge.verify_returned(replay, filed)
                        print(f'receipts OK: {accepted} accepted, {held} filed for review')
                    report(replay)
            else:
                db.execute('BEGIN')
                report(db)
    except (ValueError, TypeError, IndexError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
