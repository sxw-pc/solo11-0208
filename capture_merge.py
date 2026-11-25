"""Dual-station offline receipt intake for the repair café ledger.

Two capture stations record ordered JSON receipts offline (station, original
event id, observed records).  After the shift the receiving clerk previews the
batch: for every authorized inspection verdict in the conflicting group the
preview offers one replayable receipt set, preferring the set that keeps the
most coexisting receipts, and lists why each remaining original is not
accepted.  The clerk then submits one option; the same receipt set and order
is re-verified against the current ledger and only then enters the business
journal.  Every original, accepted or not, is filed with its source for
manual review, and audit replays the accepted receipts and checks the
returned list.

Receipt file shape:
    {"station": "A", "inspect_authorized": true, "receipts": [
        {"id": "A1", "op": "fit", "args": ["flex"],
         "observed": {"part": "tab", "inspection_rev": 2, "fitting": null}},
        ...]}

Fit, check and handover receipts carry an observed record: the physical part,
the highest passing inspection revision and the fitting identity as seen by
the worker.  On intake each field must match the ledger state at the same
step, immediately before the event is applied.
"""

import hashlib
import json
import sqlite3

import repair_cafe
from parts import usable
from store import connect, one, require

MAX_RECEIPTS = 12
MAX_STATIONS = 2
OBSERVED_OPS = 'fit check handover'.split()


def normalize(files):
    """Validate and merge per-station receipt files into one ordered batch.

    files: list of (source label, parsed JSON object).  Returns receipt dicts
    carrying station, source, the original payload and per-station sequence.
    """
    require(files, 'station files required')
    require(len(files) <= MAX_STATIONS, 'batch exceeds two stations')
    receipts, stations, ids = [], set(), set()
    for source, doc in files:
        require(isinstance(doc, dict), f'{source}: station file must be an object')
        station = doc.get('station')
        require(isinstance(station, str) and station.strip(), f'{source}: station id required')
        require(station not in stations, f'duplicate station {station}')
        stations.add(station)
        authorized = bool(doc.get('inspect_authorized'))
        rows = doc.get('receipts')
        require(isinstance(rows, list), f'{source}: receipts must be an array')
        for seq, obj in enumerate(rows):
            require(isinstance(obj, dict), f'{source}: receipt must be an object')
            rid, op = obj.get('id'), obj.get('op')
            require(isinstance(rid, str) and rid.strip(), f'{source}: receipt id required')
            require(rid not in ids, f'duplicate receipt id {rid}')
            ids.add(rid)
            require(isinstance(op, str) and op.strip(), f'{rid}: receipt op required')
            args = obj.get('args', [])
            require(isinstance(args, list), f'{rid}: receipt args must be an array')
            observed = obj.get('observed')
            if op in OBSERVED_OPS:
                require(isinstance(observed, dict), f'{rid}: observed record required for {op}')
                require({'part', 'inspection_rev', 'fitting'} <= set(observed),
                        f'{rid}: observed record incomplete')
            else:
                require(observed is None or isinstance(observed, dict), f'{rid}: observed must be an object')
            receipts.append({'id': rid, 'op': op, 'args': args, 'observed': observed,
                             'station': station, 'source': source, 'authorized': authorized, 'seq': seq,
                             'payload': json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))})
    require(len(receipts) <= MAX_RECEIPTS, 'batch exceeds twelve receipts')
    require(len(_conflict_groups(receipts)) <= 1, 'batch has more than one conflicting inspection')
    return receipts


def event_row(receipt):
    return [receipt['id'], receipt['op'], *receipt['args']]


def check_observations(db, receipt):
    """Verify a fit/check/handover receipt against the pre-event ledger state."""
    op = receipt['op']
    if op not in OBSERVED_OPS:
        return
    obs, rid = receipt.get('observed'), receipt['id']
    require(isinstance(obs, dict), f'{rid}: observed record required for {op}')
    job = receipt['args'][0] if receipt['args'] else None
    row = job and one(db, 'SELECT part, fitting FROM allocations WHERE job=? AND returned=0', job)
    require(row, f'{rid}: no open allocation for {job}')
    part, fitting = row
    evidence = usable(db, part)
    current = evidence[0] if evidence else None
    require(obs.get('part') == part, f"{rid}: observed part {obs.get('part')}, ledger has {part}")
    require(obs.get('inspection_rev') == current,
            f"{rid}: observed inspection rev {obs.get('inspection_rev')}, ledger highest pass is {current}")
    require(obs.get('fitting') == fitting,
            f"{rid}: observed fitting {obs.get('fitting')}, ledger has {fitting}")


def preview(db, receipts):
    """Compute the selectable options against a throwaway copy of the ledger."""
    mem = connect(':memory:')
    try:
        db.backup(mem)
        return _preview_on(mem, receipts)
    finally:
        mem.close()


def present(options):
    """Shape preview options for display: replayable ids plus per-original reasons."""
    return {'options': [
        {'verdict': option['verdict'],
         'accepted': [r['id'] for r in option['accepted']],
         'unaccepted': [{'id': f['id'], 'station': f['station'], 'source': f['source'],
                         'status': f['status'], 'reason': f['reason']}
                        for f in option['filed'] if f['status'] != 'accepted']}
        for option in options]}


def submit(db, receipts, accept):
    """Re-verify the clerk's chosen option on the current ledger and record it.

    The whole accepted set must replay in order; the batch enters the journal
    and every original is filed in one transaction.  Re-submitting the same
    option is a no-op.
    """
    options = preview(db, receipts)
    chosen = next((o for i, o in enumerate(options)
                   if accept == str(o['verdict']) or accept == str(i)), None)
    require(chosen, f'no preview option {accept}')
    require(chosen['verdict'] is None or any(r['id'] == chosen['verdict'] for r in chosen['accepted']),
            f"verdict {chosen['verdict']} is not acceptable on the current ledger")
    batch = _batch_id(receipts)
    db.execute('BEGIN IMMEDIATE')
    try:
        for r in chosen['accepted']:
            old = one(db, 'SELECT payload FROM journal WHERE id=?', r['id'])
            if old:
                require(old == (_row_payload(r),), 'event id conflict')
                continue
            check_observations(db, r)
            repair_cafe.apply_event(db, event_row(r))
        for f in chosen['filed']:
            row = (f['id'], f['station'], f['source'], f['payload'], f['status'], f['reason'], batch)
            old = one(db, 'SELECT station, source, payload, status, reason, batch FROM filings WHERE id=?', f['id'])
            if old:
                require(old == row[1:], f"filing id conflict: {f['id']}")
                continue
            db.execute('INSERT INTO filings VALUES(?,?,?,?,?,?,?)', row)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return chosen


def validate_order(db, ordered):
    """Raise on the first receipt that fails in this exact order (rolled back)."""
    ids = {r['id'] for r in ordered}
    refs = {r['id']: _refs(r, ids) for r in ordered}
    accepted = set()
    db.execute('SAVEPOINT intake_validate')
    try:
        for r in ordered:
            for ref in refs[r['id']]:
                require(ref in accepted, f"{r['id']}: referenced receipt {ref} not accepted")
            check_observations(db, r)
            repair_cafe.apply_event(db, event_row(r))
            accepted.add(r['id'])
    finally:
        db.execute('ROLLBACK TO intake_validate')
        db.execute('RELEASE intake_validate')


def filings(db):
    """Read filed originals back, with their receipt payloads parsed."""
    if not one(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='filings'"):
        return []
    out = []
    for rid, station, source, payload, status, reason, batch in db.execute(
            'SELECT id, station, source, payload, status, reason, batch FROM filings ORDER BY rowid'):
        doc = json.loads(payload)
        receipt = {'id': rid, 'op': doc['op'], 'args': doc.get('args', []), 'observed': doc.get('observed'),
                   'station': station, 'source': source}
        out.append({'id': rid, 'station': station, 'source': source, 'status': status,
                    'reason': reason, 'batch': batch, 'receipt': receipt})
    return out


def replay(db, events, filed):
    """Replay journal events, checking each accepted receipt at its own step."""
    accepted = {f['id']: f['receipt'] for f in filed if f['status'] == 'accepted'}
    verdicts = {}
    db.execute('BEGIN IMMEDIATE')
    try:
        seen = set()
        for row in events:
            seen.add(row[0])
            receipt = accepted.get(row[0])
            if receipt:
                check_observations(db, receipt)
            repair_cafe.apply_event(db, row)
        missing = [rid for rid in accepted if rid not in seen]
        require(not missing, f'accepted receipt missing from journal: {missing[0]}')
        for f in filed:
            receipt = f['receipt']
            if f['status'] == 'accepted' and receipt['op'] == 'inspect':
                part, rev, verdict = receipt['args']
                require(verdicts.get((part, rev), verdict) == verdict,
                        f'conflicting accepted inspections for {part} rev {rev}')
                verdicts[(part, rev)] = verdict
        db.commit()
    except BaseException:
        db.rollback()
        raise


def verify_returned(db, filed):
    """Every accepted handover receipt must appear on the returned list."""
    returned = {job for job, in db.execute('SELECT job FROM allocations WHERE returned=1')}
    accepted = [f for f in filed if f['status'] == 'accepted']
    for f in accepted:
        receipt = f['receipt']
        if receipt['op'] == 'handover':
            job = receipt['args'][0]
            require(job in returned, f'returned list missing {job}')
    return len(accepted), len(filed) - len(accepted)


def _row_payload(receipt):
    return json.dumps(event_row(receipt), ensure_ascii=False, separators=(',', ':'))


def _batch_id(receipts):
    digest = hashlib.sha1()
    for r in receipts:
        digest.update(r['payload'].encode('utf-8'))
    return digest.hexdigest()[:12]


def _conflict_groups(receipts):
    groups = {}
    for r in receipts:
        if r['op'] == 'inspect' and len(r['args']) == 3:
            groups.setdefault(tuple(r['args'][:2]), []).append(r)
    return {key: rs for key, rs in groups.items()
            if len({json.dumps(r['args'][2]) for r in rs}) > 1}


def _refs(receipt, batch_ids):
    observed = receipt.get('observed') or {}
    fitting = observed.get('fitting')
    return [fitting] if isinstance(fitting, str) and fitting in batch_ids else []


def _interleavings(seqs):
    """All interleavings that keep each station's own event order."""
    seqs = [list(s) for s in seqs if s]
    if not seqs:
        yield []
        return
    for i, seq in enumerate(seqs):
        rest = seqs[:i] + ([seq[1:]] if len(seq) > 1 else []) + seqs[i + 1:]
        for combo in _interleavings(rest):
            yield [seq[0], *combo]


def _refs_ok(order, refs):
    pos = {r['id']: i for i, r in enumerate(order)}
    return all(pos[ref] < pos[r['id']] for r in order for ref in refs[r['id']] if ref in pos)


def _simulate(db, order, refs):
    """Replay an order on db (rolled back); return (accepted, dropped reasons)."""
    accepted, dropped = [], {}
    db.execute('SAVEPOINT intake_sim')
    try:
        for r in order:
            missing = [ref for ref in refs[r['id']] if ref not in {a['id'] for a in accepted}]
            if missing:
                dropped[r['id']] = f'referenced receipt {missing[0]} not accepted'
                continue
            old = one(db, 'SELECT payload FROM journal WHERE id=?', r['id'])
            if old:
                if old == (_row_payload(r),):
                    accepted.append(r)
                else:
                    dropped[r['id']] = 'event id conflict'
                continue
            db.execute('SAVEPOINT intake_step')
            try:
                check_observations(db, r)
                repair_cafe.apply_event(db, event_row(r))
                db.execute('RELEASE intake_step')
                accepted.append(r)
            except (ValueError, sqlite3.Error) as exc:
                db.execute('ROLLBACK TO intake_step')
                db.execute('RELEASE intake_step')
                dropped[r['id']] = str(exc)
    finally:
        db.execute('ROLLBACK TO intake_sim')
        db.execute('RELEASE intake_sim')
    return accepted, dropped


def _best_option(db, receipts, chosen, losers, pre_dropped):
    """The interleaving that keeps the most coexisting receipts for one verdict."""
    candidates = [r for r in receipts if r['id'] not in losers and r['id'] not in pre_dropped]
    ids = {r['id'] for r in receipts}
    refs = {r['id']: _refs(r, ids) for r in candidates}
    stations = sorted({r['station'] for r in candidates})
    seqs = [[r for r in candidates if r['station'] == s] for s in stations]
    best = None
    for order in _interleavings(seqs):
        if not _refs_ok(order, refs):
            continue
        accepted, dropped = _simulate(db, order, refs)
        if best is None or len(accepted) > len(best[0]):
            best = (accepted, dropped)
    accepted, dropped = best
    filed = []
    for r in receipts:
        rid = r['id']
        if any(a['id'] == rid for a in accepted):
            status, reason = 'accepted', None
        elif rid in losers:
            status = 'rejected'
            reason = f"conflicts with accepted inspection {chosen['id']} from station {chosen['station']}"
        elif rid in pre_dropped:
            status, reason = 'pending', pre_dropped[rid]
        else:
            status, reason = 'pending', dropped[rid]
        filed.append({**r, 'status': status, 'reason': reason})
    return {'verdict': chosen['id'] if chosen else None, 'accepted': accepted, 'filed': filed}


def _preview_on(db, receipts):
    groups = _conflict_groups(receipts)
    pre_dropped = {r['id']: f"station {r['station']} lacks inspection authorization"
                   for r in receipts if r['op'] == 'inspect' and not r['authorized']}
    if not groups:
        return [_best_option(db, receipts, None, set(), pre_dropped)]
    (key, group), = groups.items()
    choices = sorted((r for r in group if r['authorized']), key=lambda r: r['id'])
    if not choices:
        part, rev = key
        pre = dict(pre_dropped)
        for r in group:
            pre.setdefault(r['id'], f'no authorized inspection verdict for {part} rev {rev}')
        return [_best_option(db, receipts, None, set(), pre)]
    options = []
    for chosen in choices:
        verdict = json.dumps(chosen['args'][2])
        losers = {r['id'] for r in group if json.dumps(r['args'][2]) != verdict}
        options.append(_best_option(db, receipts, chosen, losers, pre_dropped))
    options.sort(key=lambda o: (-len(o['accepted']), o['verdict']))
    return options
