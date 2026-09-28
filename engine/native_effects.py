"""Native resource-backed effects with explicit timing and source-handle policy."""
from pathlib import Path
import uuid

import native_resources as resources

TRANSITIONS = {'dissolve'}
MICROS = 1_000_000


def require(value, message):
    if not value:
        raise ValueError(message)


def transition_audit(plan, assets):
    result = []
    fps = plan['canvas']['fps']
    for ti, track in enumerate(plan['tracks']):
        for si, spec in enumerate(track['segments']):
            if 'transition_out' not in spec:
                continue
            require(ti == 0 and track['type'] == 'video', 'Transitions currently target the main video track')
            value = spec['transition_out']
            require(isinstance(value, dict) and not set(value) - {'name', 'duration_us', 'edge_policy'},
                    'Unsupported transition fields')
            require(value.get('name') in TRANSITIONS, 'Transition has no current native resource capture')
            require(si + 1 < len(track['segments']), 'A transition needs a following segment')
            following = track['segments'][si + 1]
            require(spec.get('start_us', 0) + spec['duration_us'] == following.get('start_us', 0),
                    'Transitions require contiguous adjacent segments')
            span = value.get('duration_us')
            require(type(span) is int and 0 < span <= min(spec['duration_us'], following['duration_us']),
                    'Transition duration must fit both neighboring segments')
            frames = round(span * fps / MICROS)
            require(frames >= 2 and frames % 2 == 0 and abs(frames * MICROS / fps - span) <= 2,
                    'Centered transitions require an even number of timeline frames')
            policy = value.get('edge_policy', 'require-handles')
            require(policy in {'require-handles', 'repeat-edge'}, 'Unsupported transition edge policy')
            left = assets[str(Path(spec['source']).resolve())]
            right = assets[str(Path(following['source']).resolve())]
            left_need = span / 2 * spec.get('speed', 1)
            right_need = span / 2 * following.get('speed', 1)
            left_room = left['duration_us'] - spec.get('source_start_us', 0) - spec.get('source_duration_us', spec['duration_us'])
            right_room = following.get('source_start_us', 0)
            shortages = []
            if left.get('media_type') != 'photo' and left_room + 1 < left_need:
                shortages.append('left-tail')
            if right.get('media_type') != 'photo' and right_room + 1 < right_need:
                shortages.append('right-head')
            require(not shortages or policy == 'repeat-edge',
                    'Transition source handles are insufficient; explicitly choose repeat-edge or adjust trims')
            result.append({'track_index': ti, 'segment_index': si, 'name': value['name'], 'duration_us': span,
                           'frames': frames, 'edge_policy': policy, 'repeated_edges': shortages,
                           'timeline_duration_unchanged': True})
    return result


def apply(segment, materials, spec, target):
    if 'transition_out' in spec:
        value = spec['transition_out']
        node = resources.material('transition/' + value['name'], target)
        node.update(id=str(uuid.uuid4()).upper(), duration=value['duration_us'])
        materials.setdefault('transitions', []).append(node)
        segment.setdefault('extra_material_refs', []).append(node['id'])


def verify(segment, index, spec, tolerance, target, resolve_native_path, allow_native_cache=False):
    transitions = [index[r][1] for r in segment.get('extra_material_refs', []) if index[r][0] == 'transitions']
    wanted = spec.get('transition_out')
    require(len(transitions) == (1 if wanted else 0), 'Transition binding changed')
    if not wanted:
        return []
    node = transitions[0]
    key = 'transition/' + wanted['name']
    template = resources.definition(key)['material']
    for field in ('type', 'resource_id', 'effect_id', 'third_resource_id', 'is_overlap'):
        require(node.get(field) == template[field], 'Transition resource identity changed: ' + field)
    require(type(node.get('duration')) is int and abs(node['duration'] - wanted['duration_us']) <= tolerance,
            'Transition duration changed')
    return [resources.verify_binding(key, node['path'], target, resolve_native_path, allow_native_cache)]
