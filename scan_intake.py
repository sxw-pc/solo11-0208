"""Decode a closed locker scanner export into atomically receivable bags."""

import json

from store import require


def text(value):
    return isinstance(value, str) and bool(value.strip())


def donations(source, bag):
    require(text(source), 'scanner source required')
    require(isinstance(bag, dict) and set(bag) == {'id', 'count', 'frames'}, 'invalid bag header')
    bag_id, count, frames = bag['id'], bag['count'], bag['frames']
    require(text(bag_id), 'bag id required')
    require(type(count) is int and count >= 0, f'invalid seal count for {bag_id}')
    require(isinstance(frames, list) and len(frames) == count, f'incomplete bag {bag_id}')
    seen = set()
    result = []
    fields = {'slot', 'part', 'kind', 'donor', 'policy'}
    for position, frame in enumerate(frames, 1):
        require(isinstance(frame, dict) and set(frame) == fields, f'invalid frame in {bag_id}:{position}')
        require(type(frame['slot']) is int and frame['slot'] == position, f'out-of-order slot in {bag_id}:{position}')
        part, kind, donor, policy = (frame[key] for key in ('part', 'kind', 'donor', 'policy'))
        require(all(text(value) for value in (part, kind, donor)), f'incomplete label in {bag_id}:{position}')
        require(policy in ('keep', 'release'), f'invalid permission in {bag_id}:{position}')
        require(part not in seen, f'repeated part {part} in {bag_id}')
        seen.add(part)
        result.append((part, kind, donor, policy))
    return result


def receive(document):
    require(isinstance(document, dict) and set(document) == {'source', 'bags'}, 'invalid scanner export')
    source, bags = document['source'], document['bags']
    require(text(source) and isinstance(bags, list), 'scanner source and bags required')
    events = []
    seen = set()
    for bag in bags:
        donations(source, bag)
        require(bag['id'] not in seen, f'repeated bag {bag["id"]}')
        seen.add(bag['id'])
        event_id = 'scanner:' + json.dumps([source, bag['id']], ensure_ascii=False, separators=(',', ':'))
        events.append([event_id, 'receive-scan-bag', source, bag])
    return events
