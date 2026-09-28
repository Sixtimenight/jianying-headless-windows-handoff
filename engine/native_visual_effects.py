"""Captured filter/effect tracks and resource-backed text styles.

Resources remain local previews with their original identity and restrictions.
This module does not authorize export, download anything, or inspect accounts.
"""
from copy import deepcopy
import json
import math
import uuid

import native_resources as resources

NAMES = {'filter': set(), 'effect': {'light-shake'}, 'text': set()}
BUCKETS = {'filter': 'effects', 'effect': 'video_effects'}
PARAMS = {'range': ('effects_adjust_range', .15), 'speed': ('effects_adjust_speed', .33)}


def require(value, message):
    if not value:
        raise ValueError(message)


def numeric(value, label):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1,
            label + ' must be finite and in 0..1')


def uid():
    return str(uuid.uuid4()).upper()


def validate(spec, kind):
    if kind in BUCKETS:
        require(isinstance(spec.get('name'), str) and spec['name'] in NAMES[kind],
                'Visual effect has no current native capture')
        if kind == 'filter':
            numeric(spec.get('strength', 1), 'Filter strength')
        else:
            params = spec.get('params', {})
            require(isinstance(params, dict) and not set(params) - set(PARAMS), 'Unsupported effect parameters')
            for name, (_, default) in PARAMS.items():
                numeric(params.get(name, default), 'Effect ' + name)
    if 'text_effect' in spec:
        require(kind == 'text', 'Text effects require a text segment')
        value = spec['text_effect']
        require(isinstance(value, dict) and set(value) == {'name'} and isinstance(value['name'], str)
                and value['name'] in NAMES['text'], 'Text effect has no current native capture')
        require(not {'color', 'border_color', 'border_width'}.intersection(spec),
                'Captured text effect owns fill and stroke; custom colors or borders are not supported together')


def overlay_track(kind, name, spec):
    entry = resources.definition(kind + '/' + spec['name'])
    track = deepcopy(entry['track_template'])
    track.update(id=uid(), segments=[], name=name, is_default_name=False)
    return track


def overlay_segment(kind, spec, target, track_index):
    key = kind + '/' + spec['name']
    entry = resources.definition(key)
    node = resources.material(key, target)
    node['id'] = uid()
    if kind == 'filter':
        node['value'] = spec.get('strength', 1)
    else:
        values = {native: spec.get('params', {}).get(name, default) for name, (native, default) in PARAMS.items()}
        for param in node['adjust_params']:
            param['value'] = values[param['name']]
    segment = deepcopy(entry['segment_template'])
    segment.update(id=uid(), material_id=node['id'], track_render_index=track_index,
                   target_timerange={'start': spec.get('start_us', 0), 'duration': spec['duration_us']})
    return segment, node


def apply_text(segment, materials, spec, target):
    if 'text_effect' not in spec:
        return
    node = resources.material('text-effect/' + spec['text_effect']['name'], target)
    node['id'] = uid()
    text = next(m for m in materials['texts'] if m['id'] == segment['material_id'])
    content = json.loads(text['content'])
    for style in content['styles']:
        style['effectStyle'] = {'id': node['resource_id'], 'path': node['path']}
        # The native captured flower style supplies its own strokes and fill.
        style.pop('strokes', None)
    text['content'] = json.dumps(content, ensure_ascii=False, separators=(',', ':'))
    materials.setdefault('effects', []).append(node)
    segment.setdefault('extra_material_refs', []).append(node['id'])


def verify_node(key, node, target, resolve_path, allow_native_cache):
    template = resources.definition(key)['material']
    for field in ('type', 'effect_id', 'resource_id', 'third_resource_id', 'sub_type', 'source_platform',
                  'apply_target_type', 'category_id'):
        if field in template:
            require(node.get(field) == template[field], 'Visual effect resource identity changed: ' + field)
    return resources.verify_binding(key, node.get('path', ''), target, resolve_path, allow_native_cache)


def verify(segment, index, spec, kind, target, resolve_path, allow_native_cache=False):
    bindings = []
    if kind in BUCKETS:
        bucket, node = index[segment['material_id']]
        require(bucket == BUCKETS[kind], 'Visual effect primary material binding changed')
        bindings.append(verify_node(kind + '/' + spec['name'], node, target, resolve_path, allow_native_cache))
        expected = spec.get('strength', 1) if kind == 'filter' else 1
        numeric(node.get('value', 1), 'Visual effect value')
        require(abs(node.get('value', 1) - expected) < 1e-5, 'Visual effect strength changed')
        if kind == 'effect':
            params = node.get('adjust_params', [])
            values = {p['name']: p.get('value', p.get('default_value')) for p in params}
            require(len(values) == len(params) == len(PARAMS) and set(values) == {p[0] for p in PARAMS.values()},
                    'Visual effect parameters changed')
            for name, (native, default) in PARAMS.items():
                numeric(values[native], 'Visual effect parameter')
                require(abs(values[native] - spec.get('params', {}).get(name, default)) < 1e-5,
                        'Visual effect parameter changed: ' + name)
    if kind == 'text':
        # Native save can duplicate an identical material and its reference.
        refs = {r for r in segment.get('extra_material_refs', [])
                if index[r][0] == 'effects' and index[r][1].get('type') == 'text_effect'}
        wanted = spec.get('text_effect')
        require(len(refs) == (1 if wanted else 0), 'Text effect binding changed')
        content = json.loads(index[segment['material_id']][1]['content'])
        if wanted:
            node = index[next(iter(refs))][1]
            key = 'text-effect/' + wanted['name']
            bindings.append(verify_node(key, node, target, resolve_path, allow_native_cache))
            numeric(node.get('value', 1), 'Text effect value')
            require(abs(node.get('value', 1) - 1) < 1e-5, 'Text effect strength changed')
            for style in content['styles']:
                fill = style.get('fill', {})
                content_fill = fill.get('content', {})
                solid = content_fill.get('solid', {})
                color = solid.get('color', [])
                require(content_fill.get('render_type') == 'solid' and len(color) == 3
                        and all(type(v) in (int, float) and abs(v - 1) < 1e-5 for v in color)
                        and fill.get('alpha', 1) == solid.get('alpha', 1) == 1 and not style.get('strokes'),
                        'Text effect base fill or stroke changed')
                effect_style = style.get('effectStyle', {})
                require(effect_style.get('id') == node['resource_id'], 'Text style effect identity changed')
                bindings.append(resources.verify_binding(key, effect_style.get('path', ''), target,
                                                          resolve_path, allow_native_cache))
        else:
            require(all(not s.get('effectStyle') for s in content['styles']), 'Unplanned text effect appeared')
    return bindings
