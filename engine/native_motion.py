"""Native motion nodes: linear keyframes and captured local geometric masks.

Serialization is checked against the current native parser. UI evidence is
tracked separately; accepting JSON is not proof that it renders correctly.
"""
from copy import deepcopy
import math
import uuid

import native_resources as resources

KEYFRAMES = {
    'x': ('KFTypePositionX', -5, 5), 'y': ('KFTypePositionY', -5, 5),
    'scale': ('KFTypeScaleX', .01, 10), 'rotation': ('KFTypeRotation', -360, 360),
    'opacity': ('KFTypeAlpha', 0, 1), 'volume': ('KFTypeVolume', 0, 4),
}
MASKS = {'circle': '圆形', 'rectangle': '矩形', 'line': '线性', 'mirror': '镜面', 'star': '星形', 'heart': '爱心'}


def require(value, message):
    if not value:
        raise ValueError(message)


def numeric(value, low, high, label):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            label + ' outside range')


def uid():
    return str(uuid.uuid4()).upper()


def validate(spec, kind):
    if 'opacity' in spec:
        require(kind in {'video', 'text'}, 'Opacity needs a visual segment')
        numeric(spec['opacity'], 0, 1, 'Opacity')
    if 'keyframes' in spec:
        series = spec['keyframes']
        allowed = set(KEYFRAMES) if kind == 'video' else {'volume'} if kind == 'audio' else {'x', 'y', 'scale', 'rotation'}
        require(isinstance(series, dict) and series and not set(series) - allowed, 'Unsupported keyframe channel')
        require(spec.get('speed', 1) == 1 and spec.get('source_start_us', 0) == 0,
                'Keyframes on trimmed/speed-adjusted sources need a separately verified time mapping')
        for channel, points in series.items():
            require(isinstance(points, list) and len(points) >= 2, 'Use at least two keyframe points')
            previous = -1
            for point in points:
                require(isinstance(point, dict) and set(point) == {'at_us', 'value'}, 'Use linear at_us/value keyframes')
                at = point['at_us']
                require(type(at) is int and previous < at <= spec['duration_us'], 'Keyframes must be ordered and inside the segment')
                previous = at
                numeric(point['value'], *KEYFRAMES[channel][1:], 'Keyframe value')
            require(points[0]['at_us'] == 0, 'The first keyframe must start at the segment beginning')
            if channel in spec:
                require(abs(points[0]['value'] - spec[channel]) < 1e-7, 'Static value conflicts with first keyframe')
    if 'mask' in spec:
        require(kind == 'video', 'Masks require visual media on a video track')
        mask = spec['mask']
        require(isinstance(mask, dict) and not set(mask) - {
                'shape', 'width', 'height', 'x', 'y', 'rotation', 'feather', 'invert', 'round_corner'}, 'Unsupported mask fields')
        require(mask.get('shape') in MASKS, 'Unsupported geometric mask shape')
        require(type(mask.get('invert', False)) is bool, 'Mask invert must be boolean')
        require(mask['shape'] != 'line' or not {'width', 'height'}.intersection(mask),
                'Line is a half-plane mask; width and height are not effective controls')
        for name, default, low, high in [('width', .28, .001, 5), ('height', .5, .001, 5),
                                        ('x', 0, -5, 5), ('y', 0, -5, 5), ('rotation', 0, -360, 360),
                                        ('feather', 0, 0, 1), ('round_corner', 0, 0, 1)]:
            numeric(mask.get(name, default), low, high, 'Mask ' + name)
        require(mask.get('round_corner', 0) == 0 or mask['shape'] == 'rectangle', 'Round corners require a rectangle mask')


def keyframe_groups(spec):
    groups = []
    for channel, points in spec.get('keyframes', {}).items():
        groups.append({'id': uid(), 'material_id': '', 'property_type': KEYFRAMES[channel][0], 'keyframe_list': [
            {'id': uid(), 'curveType': 'Line', 'graphID': '', 'left_control': {'x': 0.0, 'y': 0.0},
             'right_control': {'x': 0.0, 'y': 0.0}, 'time_offset': p['at_us'], 'values': [p['value']]}
            for p in points]})
    return groups


def mask_config(mask):
    entry = resources.definition('mask/' + mask['shape'])
    return {'aspectRatio': entry['aspect_ratio'], 'centerX': mask.get('x', 0), 'centerY': mask.get('y', 0),
            'width': mask.get('width', .28), 'height': mask.get('height', 1 if mask['shape'] == 'line' else .5),
            'rotation': mask.get('rotation', 0), 'invert': mask.get('invert', False),
            'feather': mask.get('feather', 0), 'roundCorner': mask.get('round_corner', 0)}


def mask_material(mask, target):
    node = resources.material('mask/' + mask['shape'], target)
    node.update(id=uid(), constant_material_id=uid(), config=mask_config(mask))
    return node


def apply(segment, materials, spec, kind, target=None):
    validate(spec, kind)
    if 'opacity' in spec:
        segment['clip']['alpha'] = spec['opacity']
    if 'keyframes' in spec:
        segment['common_keyframes'] = keyframe_groups(spec)
        for channel, points in spec['keyframes'].items():
            value = points[0]['value']
            if channel in {'x', 'y'}:
                segment['clip']['transform'][channel] = value
            elif channel == 'scale':
                segment['clip']['scale'] = {'x': value, 'y': value}
            elif channel in {'rotation', 'opacity'}:
                segment['clip']['alpha' if channel == 'opacity' else channel] = value
            else:
                segment['volume'] = value
        if 'scale' in spec['keyframes']:
            segment['uniform_scale'] = {'on': True, 'value': 1.0}
    if 'mask' in spec:
        require(target is not None, 'Masks need a draft-owned resource target')
        mask = mask_material(spec['mask'], target)
        materials.setdefault('common_mask', []).append(mask)
        segment.setdefault('extra_material_refs', []).append(mask['id'])
        segment['enable_video_mask'] = True


def verify(actual, index, spec, kind, tolerance):
    animated = set(spec.get('keyframes', {}))
    if 'opacity' in spec and 'opacity' not in animated:
        require(abs(actual['clip'].get('alpha', 1) - spec['opacity']) < 1e-5, 'Opacity changed')
    if 'keyframes' in spec:
        groups = actual.get('common_keyframes', [])
        require(len(groups) == len(animated), 'Keyframe channel count changed')
        by_type = {g['property_type']: g for g in groups}
        require(len(by_type) == len(groups), 'Duplicate keyframe channel')
        for channel, wanted in spec['keyframes'].items():
            group = by_type.get(KEYFRAMES[channel][0])
            require(group is not None, 'Keyframe channel disappeared')
            points = group['keyframe_list']
            require(len(points) == len(wanted), 'Keyframe point count changed')
            for point, expected in zip(points, wanted):
                require(point.get('curveType', 'Line') == 'Line', 'Keyframe interpolation changed')
                require(abs(point.get('time_offset', 0) - expected['at_us']) <= tolerance, 'Keyframe time changed')
                require(len(point['values']) == 1 and abs(point['values'][0] - expected['value']) < 1e-5,
                        'Keyframe value changed')
        if 'scale' in animated:
            require(actual.get('uniform_scale', {}).get('on', True) is True, 'Uniform keyframe scale changed')
    if 'mask' in spec:
        masks = [index[r][1] for r in actual.get('extra_material_refs', []) if index[r][0] == 'common_mask']
        require(len(masks) == 1, 'Mask binding changed')
        wanted = resources.definition('mask/' + spec['mask']['shape'])['material']
        for field in ('category', 'category_id', 'resource_type', 'resource_id'):
            require(masks[0].get(field) == wanted[field], 'Mask resource identity changed: ' + field)
        require(actual.get('enable_video_mask', True) is True, 'Mask disabled')
        config = masks[0]['config']
        for field, value in mask_config(spec['mask']).items():
            if spec['mask']['shape'] == 'line' and field == 'height':
                # Native canonicalizes this unused half-plane field to 1.
                # The public plan rejects width/height for line masks.
                continue
            default = False if field == 'invert' else 1 if field in {'aspectRatio', 'width', 'height'} else 0
            actual_value = config.get(field, default)
            require(type(actual_value) is bool and actual_value == value if field == 'invert'
                    else type(actual_value) in (int, float) and abs(actual_value - value) < 1e-5,
                    'Mask parameter changed: ' + field)
