from store import one, require


def usable(db, part):
    return one(db, """SELECT 1 FROM donations d JOIN inspections i ON d.id=i.part
        WHERE d.id=? AND d.policy='release' AND i.usable=1
        AND i.rev=(SELECT max(rev) FROM inspections WHERE part=d.id)""", part)


def extra_time(db, kind, need):
    if kind == need:
        return 0
    edge = one(db, 'SELECT minutes FROM substitutes WHERE kind=? AND need=?', kind, need)
    return edge[0] if edge else None


def apply(db, op, args):
    if op in ('donate', 'inspect', 'substitute'):
        sql = {'donate': 'INSERT INTO donations VALUES(?,?,?,?)',
               'inspect': 'INSERT INTO inspections VALUES(?,?,?)',
               'substitute': 'INSERT INTO substitutes VALUES(?,?,?)'}
        db.execute(sql[op], args)
    else:
        part, policy = args
        require(not one(db, 'SELECT 1 FROM allocations WHERE part=?', part), 'part committed')
        require(db.execute('UPDATE donations SET policy=? WHERE id=?', (policy, part)).rowcount, 'unknown part')
