from bench import booking
from parts import extra_time, usable
from store import one, require


def plan(db):
    stock = [(part, kind) for part, kind in db.execute('SELECT id,kind FROM donations ORDER BY id')
             if usable(db, part) and not one(db, 'SELECT 1 FROM allocations WHERE part=?', part)]
    owner = {}
    edges = {}

    def place(job, seen):
        for _, part in edges[job]:
            if part in seen:
                continue
            seen.add(part)
            if part not in owner or place(owner[part], seen):
                owner[part] = job
                return True
        return False

    for job, need, minutes in db.execute('''SELECT id,need,minutes FROM requests r
            WHERE NOT EXISTS (SELECT 1 FROM allocations a WHERE a.job=r.id)
            ORDER BY priority DESC,id'''):
        slot = booking(db, job)
        if slot:
            edges[job] = sorted((extra, part) for part, kind in stock
                                if (extra := extra_time(db, kind, need)) is not None and minutes + extra <= slot[1])
            place(job, set())
    return dict(sorted((job, part) for part, job in owner.items()))


def apply(db, op, args, event):
    if op == 'request':
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?)', args)
        return
    if op == 'allocate':
        require(not args, 'allocate takes no arguments')
        assignments = plan(db)
        require(assignments, 'no feasible allocation')
        db.executemany('INSERT INTO allocations(job,part) VALUES(?,?)', assignments.items())
        return
    job = args[0]
    part, fitting, returned = one(db, 'SELECT part,fitting,returned FROM allocations WHERE job=?', job) or (None, None, 0)
    require(part and not returned, 'no open allocation')
    if op in ('fit', 'release', 'handover'):
        require(len(args) == 1, 'expected one request id')
    if op == 'fit':
        evidence = usable(db, part)
        require(not fitting and evidence, 'part unavailable for fitting')
        db.execute('INSERT INTO fittings VALUES(?,?,?,?,?,?)',
                   (event, job, part, booking(db, job)[0], evidence[0], 'mounted'))
        db.execute('UPDATE allocations SET fitting=? WHERE job=?', (event, job))
    elif op == 'release':
        if fitting:
            db.execute("UPDATE fittings SET status='removed' WHERE id=?", (fitting,))
        db.execute('DELETE FROM allocations WHERE job=?', (job,))
    elif op == 'check':
        _, reviewer, passed = args
        mounted = one(db, 'SELECT worker FROM fittings WHERE id=?', fitting)
        require(mounted and reviewer != mounted[0], 'independent check required')
        require(one(db, 'SELECT 1 FROM requests r JOIN volunteers v ON r.skill=v.skill WHERE r.id=? AND v.id=?', job, reviewer), 'reviewer skill mismatch')
        evidence = usable(db, part)
        require(evidence, 'part unavailable for independent check')
        db.execute('INSERT INTO checks VALUES(?,?,?,?,?)', (event, fitting, reviewer, evidence[0], passed))
    else:
        latest = one(db, 'SELECT inspection_rev,passed FROM checks WHERE fitting=? ORDER BY rowid DESC LIMIT 1', fitting)
        evidence = usable(db, part)
        require(evidence and latest == (evidence[0], 1), 'handover blocked by inspection or independent check')
        db.execute("UPDATE fittings SET status='returned' WHERE id=?", (fitting,))
        db.execute('UPDATE allocations SET returned=1 WHERE job=?', (job,))
