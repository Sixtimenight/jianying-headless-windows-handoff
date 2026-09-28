"""Native editable compound graphs and their draft-local library sidecars.

The captured 11.4.2 representation embeds the child timeline in a combination
material. Its subdraft file is a second wrapper around the same child, not a
rendered movie. All copying is local and retains the editable child structure.
"""
from copy import deepcopy
from pathlib import Path
import re
import shutil
import time

import jy14_headless as j

PROFILE = 'jy14-headless-macos-11.4.2'
SUPPORTED_PROFILES = frozenset((PROFILE, 'jy14-headless-macos-11.5.0'))
BLUEPRINT = Path(__file__).with_name('compound-blueprint.json')
SUBDRAFT_TOKEN = '##_subdraft_placeholder_536E1D01-0D97-4295-AA34-0CC47A957B82_##/'
SIDECARS = {'draft_file_path': 'draft_content.json', 'draft_cover_path': 'draft_cover.jpg',
            'draft_config_path': 'sub_draft_config.json'}


def require_publishable(root):
    """Keep live registration closed until native save/reopen preserves sidecars."""
    j.require(not root.get('materials', {}).get('drafts'),
              'Compound live registration is blocked: native 11.4.2 save loses the subdraft project directory; '
              'offline construction/export does not establish editable persistence')


def graph(root):
    """Yield (owning combination material or None, timeline), rejecting cycles."""
    seen = set()

    def walk(owner, timeline, depth):
        j.require(depth <= 8 and len(seen) < 256, 'Compound graph exceeds bounded depth/count')
        j.require(isinstance(timeline, dict) and isinstance(timeline.get('id'), str), 'Missing compound timeline identity')
        j.require(timeline['id'] not in seen, 'Repeated/cyclic compound timeline identity')
        seen.add(timeline['id'])
        yield owner, timeline
        for node in timeline.get('materials', {}).get('drafts', []):
            j.require(node.get('type') == 'combination' and node.get('combination_type') == 'none',
                      'Only captured local combination compounds are supported')
            j.require(isinstance(node.get('combination_id'), str) and node['combination_id'],
                      'Missing native combination identity')
            yield from walk(node, node.get('draft'), depth + 1)

    yield from walk(None, root, 0)


def validate(root, basic_validation):
    rows = list(graph(root))
    for _, timeline in rows:
        end = basic_validation(timeline)
        j.require(abs(end - timeline.get('duration', 0)) <= 1, 'Compound timeline duration does not match its clips')
        nodes = timeline.get('materials', {}).get('drafts', [])
        videos = {v['id']: v for v in timeline.get('materials', {}).get('videos', [])}
        all_segments = [s for t in timeline.get('tracks', []) for s in t.get('segments', [])]
        for node in nodes:
            child = node['draft']
            users = [s for s in all_segments if node['id'] in s.get('extra_material_refs', [])]
            j.require(len(users) == 1, 'Compound material must have exactly one unambiguous clip owner')
            segment = users[0]
            video = videos.get(segment['material_id'], {})
            j.require(video.get('type') == 'video' and not video.get('path') and video.get('extra_type_option') == 2,
                      'Compound owner is not the captured native video placeholder')
            j.require(video.get('duration') == child['duration'] and
                      all(video.get(k) == child.get('canvas_config', {}).get(k) for k in ('width', 'height')),
                      'Compound placeholder dimensions/duration differ from the child')
            source = segment.get('source_timerange', {})
            start = j.integer(source.get('start', 0), 'Compound source start')
            duration = j.integer(source.get('duration'), 'Compound source duration', 1)
            speed = j.number(segment.get('speed', 1), 'Compound speed', .1, 8)
            j.require(start + duration <= child['duration'] + 1 and
                      abs(duration / speed - segment['target_timerange']['duration']) <= 1,
                      'Compound source range or speed is inconsistent')
    return rows


def select(root, timeline_id=None):
    if timeline_id is None:
        return root
    matches = [t for _, t in graph(root) if t['id'] == timeline_id]
    j.require(len(matches) == 1, 'Unknown or ambiguous nested timeline_id')
    return matches[0]


def normalize_paths(value, target):
    if isinstance(value, dict):
        return {key: str(j.native_media_path(item, target)) if key.endswith('path') and isinstance(item, str)
                and item and (item.startswith(j.DRAFT_PATH_TOKEN) or item.startswith('./Resources/'))
                else normalize_paths(item, target) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_paths(item, target) for item in value]
    return value


def paths(node, target):
    child_id = node['draft']['id']
    j.require(re.fullmatch(r'[A-Fa-f0-9-]{36}', child_id), 'Unsafe compound directory identity')
    result = {}
    for key, filename in SIDECARS.items():
        raw = node.get(key)
        j.require(isinstance(raw, str) and raw, 'Compound sidecar path is missing: ' + key)
        path = j.native_media_path(raw, target)
        expected = target / 'subdraft' / child_id / filename
        j.require(path == expected, 'Compound sidecar must be in its own draft-local directory')
        result[key] = path
    return result


def check_sidecars(root, target, folder, preserved):
    """Compare embedded children against their native library wrappers."""
    checked = []
    for owner, child in graph(root):
        if owner is None:
            continue
        resolved = paths(owner, target)
        physical = {key: folder / path.relative_to(target) for key, path in resolved.items()}
        for path in physical.values():
            j.require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()),
                      'Missing, symlinked or escaping compound sidecar')
        sidecar = j.read_json(physical['draft_file_path'])
        wrapped = sidecar.get('materials', {}).get('drafts', [])
        j.require(len(wrapped) == 1 and wrapped[0].get('id') == owner['id'] and
                  wrapped[0].get('combination_id') == owner['combination_id'], 'Compound sidecar identity mismatch')
        saved = wrapped[0].get('draft')
        compared, identity_changes = normalize_companion_ids(child, saved, nested_root=True)
        preserved(normalize_paths(child, target), normalize_paths(compared, target))
        check_order(child, saved)
        config = j.read_json(physical['draft_config_path'])
        j.require(config.get('id') == child['id'] and config.get('project_id') == child['id'] and
                  config.get('is_from_sub_draft') is True and not config.get('is_from_multi_timeline') and
                  config.get('name') == child['name'] and config.get('rough_cut_duration') == child['duration'] and
                  config.get('rough_cut_start', 0) == 0 and config.get('draft_json_file') == 'draft_content.json' and
                  config.get('cover_path') == 'draft_cover.jpg' and not config.get('audio_path'),
                  'Unsupported or inconsistent compound library configuration')
        checked.append({'timeline_id': child['id'], 'material_id': owner['id'],
                        'files': {key: str(p.relative_to(folder)) for key, p in physical.items()},
                        'empty_companion_identity_changes': identity_changes})
    return checked


def check_order(expected, actual):
    expected_rows = {t['id']: t for _, t in graph(expected)}
    actual_rows = {t['id']: t for _, t in graph(actual)}
    j.require(set(expected_rows) == set(actual_rows), 'Compound timeline count/identity changed')
    for tid, wanted in expected_rows.items():
        got = actual_rows[tid]
        j.require([t['id'] for t in wanted.get('tracks', [])] == [t['id'] for t in got.get('tracks', [])],
                  'Nested track order/count changed')
        for left, right in zip(wanted.get('tracks', []), got.get('tracks', [])):
            j.require([s['id'] for s in left.get('segments', [])] == [s['id'] for s in right.get('segments', [])],
                      'Nested segment order/count changed')


def normalize_companion_ids(expected, actual, nested_root=False):
    """Match only empty photo audio-companion nodes recreated by native save.

    Original and replacement nodes must be uniquely referenced, same kind, and
    entirely default-valued. All other IDs, effect parameters and refs stay strict.
    This only changes a comparison copy, never the live timeline or expected data.
    """
    actual_rows = {timeline['id']: timeline for _, timeline in graph(actual)}
    replacements, changes = {}, []

    def default_node(bucket, node):
        wanted_type = 'vocal_separation' if bucket == 'vocal_separations' else None
        return bucket in {'loudnesses', 'vocal_separations'} and node.get('type') == wanted_type and not any(
            value for key, value in node.items() if key not in {'id', 'type'})

    for owner, wanted in graph(expected):
        if owner is None and not nested_root:
            continue
        got = actual_rows.get(wanted['id'])
        if got is None:
            continue
        old_index = {node['id']: (bucket, node) for bucket, nodes in wanted.get('materials', {}).items() for node in nodes}
        new_index = {node['id']: (bucket, node) for bucket, nodes in got.get('materials', {}).items() for node in nodes}
        old_segments = [s for t in wanted.get('tracks', []) for s in t.get('segments', [])]
        new_segments = {s['id']: s for t in got.get('tracks', []) for s in t.get('segments', [])}
        for old_segment in old_segments:
            material = old_index.get(old_segment.get('material_id'), ('', {}))[1]
            if material.get('type') != 'photo' or material.get('has_audio') is not False:
                continue
            new_segment = new_segments.get(old_segment['id'], {})
            old_refs, new_refs = old_segment.get('extra_material_refs', []), new_segment.get('extra_material_refs', [])
            if len(old_refs) != len(new_refs):
                continue
            for old_id, new_id in zip(old_refs, new_refs):
                if old_id == new_id or old_id not in old_index or new_id not in new_index:
                    continue
                bucket, old_node = old_index[old_id]
                new_bucket, new_node = new_index[new_id]
                if bucket != new_bucket or not default_node(bucket, old_node) or not default_node(bucket, new_node):
                    continue
                if old_id in new_index or new_id in old_index:
                    continue
                old_users = sum(old_id in s.get('extra_material_refs', []) for s in old_segments)
                new_users = sum(new_id in s.get('extra_material_refs', []) for s in new_segments.values())
                if old_users != 1 or new_users != 1:
                    continue
                j.require(new_id not in replacements and old_id not in replacements.values(), 'Ambiguous native companion ID mapping')
                replacements[new_id] = old_id
                changes.append({'timeline_id': wanted['id'], 'segment_id': old_segment['id'], 'bucket': bucket,
                                'expected_id': old_id, 'native_id': new_id, 'parameters_unchanged_and_default': True})
    return j.remap(deepcopy(actual), replacements), changes


def all_segments(root):
    for _, timeline in graph(root):
        for track in timeline.get('tracks', []):
            for segment in track.get('segments', []):
                yield timeline, track, segment


def wrap_all(timeline, name, target):
    """Wrap every track, without flattening or changing any child clip ID."""
    j.require(isinstance(name, str) and name.strip(), 'Compound name must be nonempty')
    j.require(timeline.get('duration', 0) > 0 and timeline.get('tracks'), 'Cannot wrap an empty timeline')
    # Do not implicitly reinterpret root-level grouping or external timeline relationships.
    for key in ('relationships', 'group_container', 'keyframe_graph_list', 'time_marks'):
        j.require(not timeline.get(key), 'Compound creation requires dedicated handling for ' + key)
    proto = j.read_json(BLUEPRINT)
    j.require(proto['schema'] == 'jy14-native-compound-blueprint/v1' and proto['runtime_profile'] == PROFILE,
              'Unsupported compound blueprint')
    child_id = j.identifier()
    material_id = j.identifier()
    combination_id = j.identifier()
    seeds = [proto['wrapper_track']['id']]
    seeds += [s['id'] for s in proto['wrapper_track']['segments']]
    seeds += [m['id'] for nodes in proto['wrapper_materials'].values() for m in nodes]
    ids = {old: j.identifier() for old in seeds}
    seed_owner = proto['wrapper_materials']['drafts'][0]
    ids.update({seed_owner['id']: material_id, seed_owner['combination_id']: combination_id,
                proto['child_seed_id']: child_id})
    track = j.remap(deepcopy(proto['wrapper_track']), ids)
    materials = j.remap(deepcopy(proto['wrapper_materials']), ids)
    child = deepcopy(timeline)
    child.update(id=child_id, name=name)
    child.setdefault('config', {})['maintrack_adsorb'] = False
    child['render_index_track_mode_on'] = True
    owner = materials['drafts'][0]
    owner['draft'] = child
    for key, filename in SIDECARS.items():
        owner[key] = str(target / 'subdraft' / child_id / filename)
    video = materials['videos'][0]
    video.update(duration=child['duration'], material_name=name,
                 width=child['canvas_config']['width'], height=child['canvas_config']['height'])
    segment = track['segments'][0]
    segment['source_timerange'] = {'start': 0, 'duration': child['duration']}
    segment['target_timerange'] = {'start': 0, 'duration': child['duration']}
    track['name'] = name
    track['is_default_name'] = False
    timeline.update(tracks=[track], materials=materials, keyframes={})
    return {'op': 'create_compound', 'name': name, 'created_timeline_id': child_id,
            'created_material_id': material_id, 'created_segment_id': segment['id'],
            'internal_tracks': len(child['tracks']), 'duration_us': child['duration']}


def write_sidecars(root, target, folder, source, write_owned, rebase):
    proto = j.read_json(BLUEPRINT)
    written = []
    for owner, child in graph(root):
        if owner is None:
            continue
        resolved = paths(owner, target)
        physical = {key: folder / path.relative_to(target) for key, path in resolved.items()}
        content = physical['draft_file_path']
        if content.exists():
            sidecar = rebase(j.read_json(content), source, target)
            config = rebase(j.read_json(physical['draft_config_path']), source, target)
        else:
            content.parent.mkdir(parents=True, exist_ok=True)
            sidecar = deepcopy(proto['sidecar'])
            seeds = []
            def identifiers(value):
                if isinstance(value, dict):
                    if isinstance(value.get('id'), str) and value['id']:
                        seeds.append(value['id'])
                    for item in value.values():
                        identifiers(item)
                elif isinstance(value, list):
                    for item in value:
                        identifiers(item)
            identifiers(sidecar)
            ids = {old: j.identifier() for old in seeds}
            old_owner = sidecar['materials']['drafts'][0]
            ids[old_owner['id']] = owner['id']
            ids[old_owner['combination_id']] = owner['combination_id']
            sidecar = j.remap(sidecar, ids)
            config = deepcopy(proto['subdraft_config'])
            now = time.time_ns() // 1000
            config.update(id=child['id'], project_id=child['id'], create_time=now // 1_000_000,
                          import_time_ms=now // 1000)
            cover = folder / 'draft_cover.jpg'
            j.require(cover.is_file() and not cover.is_symlink(), 'Missing task-owned cover for compound library entry')
            shutil.copy2(cover, physical['draft_cover_path'])
        wrapped = sidecar['materials']['drafts']
        j.require(len(wrapped) == 1 and wrapped[0]['id'] == owner['id'], 'Unexpected compound library wrapper')
        wrapped[0]['draft'] = deepcopy(child)
        for key, filename in SIDECARS.items():
            wrapped[0][key] = SUBDRAFT_TOKEN + filename
        sidecar['canvas_config'] = deepcopy(child['canvas_config'])
        sidecar['fps'] = child.get('fps', root.get('fps', 30))
        sidecar['materials']['videos'][0].update(duration=child['duration'], material_name=child['name'],
                                               width=child['canvas_config']['width'], height=child['canvas_config']['height'])
        for segment in sidecar['tracks'][0]['segments']:
            segment['source_timerange'] = {'start': 0, 'duration': child['duration']}
            segment['target_timerange'] = {'start': 0, 'duration': child['duration']}
        config.update(name=child['name'], rough_cut_duration=child['duration'])
        write_owned(content, sidecar)
        write_owned(physical['draft_config_path'], config)
        written.append(child['id'])
    return written
