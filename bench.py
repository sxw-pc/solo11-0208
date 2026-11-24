from store import one, require


def booking(db, job):
    return one(db, 'SELECT worker,stop-start FROM bookings WHERE job=?', job)


def apply(db, op, args):
    if op == 'volunteer':
        db.execute('INSERT INTO volunteers VALUES(?,?)', args)
    elif op == 'book':
        job, station, worker, start, stop = args
        require(one(db, 'SELECT 1 FROM requests r JOIN volunteers v ON r.skill=v.skill WHERE r.id=? AND v.id=?', job, worker), 'skill mismatch')
        require(not one(db, '''SELECT 1 FROM bookings WHERE (station=? OR worker=?)
            AND start<? AND stop>?''', station, worker, stop, start), 'station or worker overlap')
        db.execute('INSERT INTO bookings VALUES(?,?,?,?,?)', args)
    else:
        job, = args
        require(not one(db, 'SELECT 1 FROM allocations WHERE job=?', job), 'release allocation first')
        require(db.execute('DELETE FROM bookings WHERE job=?', args).rowcount, 'no booking')
