"""Record numbered stub handoffs at the collection counter."""

from store import one, require


def completed(db, job):
    return bool(one(db, 'SELECT 1 FROM allocations WHERE job=? AND returned=1', job))


def apply(db, operation, args):
    if operation == 'counter-tag':
        job, tag = args
        require(isinstance(tag, str) and tag.strip(), 'printed tag is required')
        require(completed(db, job), 'repair is not cleared for the counter')
        db.execute('INSERT INTO counter_tags VALUES(?,?)', (tag, job))
        return

    tag, station, stub = args
    require(all(isinstance(value, str) and value.strip() for value in (tag, station, stub)),
            'tag, collection station and original stub are required')
    require(one(db, 'SELECT 1 FROM counter_tags WHERE tag=?', tag), 'unknown collection tag')
    require(not one(db, 'SELECT 1 FROM counter_pickups WHERE tag=?', tag), 'tag already collected')
    db.execute('INSERT INTO counter_pickups VALUES(?,?,?)', (tag, station, stub))


def report(db):
    if not has_counter(db):
        return []
    return [{'tag': tag, 'job': job, 'station': station, 'stub': stub}
            for tag, job, station, stub in db.execute('''
                SELECT tag, job, station, stub FROM counter_tags
                LEFT JOIN counter_pickups USING(tag) ORDER BY tag
            ''')]


def has_counter(db):
    return bool(one(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='counter_tags'"))


def snapshot(db):
    tables = ('counter_tags', 'counter_pickups')
    return {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            if has_counter(db) else [] for table in tables}