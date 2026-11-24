import json

from store import one, require


def failed_check(db, fitting):
    return one(db, '''SELECT c.id FROM fittings f JOIN allocations a ON a.fitting=f.id
                      JOIN checks c ON c.fitting=f.id
                      WHERE f.id=? AND f.status='mounted' AND a.returned=0
                        AND c.rowid=(SELECT max(rowid) FROM checks WHERE fitting=f.id)
                        AND c.passed=0''', fitting)


def apply(db, op, args, event):
    if op == 'probe-card':
        fitting, check, probe, labels = args
        require(failed_check(db, fitting) == (check,), 'probe card requires latest failed check')
        require(isinstance(probe, str) and probe.strip(), 'probe name required')
        require(isinstance(labels, dict) and labels and all(
            key.strip() and isinstance(value, str) and value.strip()
            for key, value in labels.items()), 'probe labels required')
        db.execute('INSERT INTO probe_cards VALUES(?,?,?,?,?)',
                   (event, fitting, check, probe, json.dumps(labels, sort_keys=True)))
        return

    card_id = args[0]
    card = one(db, 'SELECT fitting, failed, probe, labels FROM probe_cards WHERE id=?', card_id)
    require(card, 'unknown probe card')
    fitting, failed, probe, labels = card
    require(failed_check(db, fitting) == (failed,), 'probe card is closed')
    if op == 'probe-issue':
        require(len(args) == 1, 'expected one probe card id')
        db.execute('INSERT INTO probe_issues VALUES(?,?,?)', (event, card_id, probe))
    else:
        _, issue_id, reading = args
        require(one(db, 'SELECT 1 FROM probe_issues WHERE id=? AND card=?', issue_id, card_id),
                'reading requires issued probe')
        labels = json.loads(labels)
        require(reading in labels, 'unknown probe reading')
        db.execute('INSERT INTO probe_results VALUES(?,?,?,?)',
                   (event, issue_id, reading, labels[reading]))
