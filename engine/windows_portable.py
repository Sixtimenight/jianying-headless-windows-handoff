#!/usr/bin/env python3
"""Build and verify a bounded, non-native Windows FFmpeg render snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import ffmpeg_tools

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = 'jy14-windows-render-build/v1'
PLAN_SCHEMA = 'jy14-headless-plan/v1'
TIMELINE_NAME = 'render-timeline.json'
MICROS = 1_000_000


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def packed(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    value = json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                       parse_constant=lambda item: (_ for _ in ()).throw(
                           ValueError('Nonfinite JSON number: ' + item)))
    require(isinstance(value, dict), 'JSON root must be an object')
    return value


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(value if isinstance(value, bytes) else packed(value))
        stream.flush()
        os.fsync(stream.fileno())


def relative_to(path, parent):
    try:
        return Path(path).resolve().relative_to(Path(parent).resolve())
    except ValueError as error:
        raise ValueError('Path is outside the verified build: ' + str(path)) from error


def new_work_directory(path):
    path = Path(path)
    require(path.is_absolute(), 'Output must be an absolute path')
    resolved = path.resolve()
    work = (ROOT / 'work').resolve()
    require(resolved != work and work in resolved.parents,
            'Output must be a new directory below the project work directory')
    require(not path.exists(), 'Output directory already exists')
    return resolved


def regular_file(path, label):
    require(isinstance(path, (str, Path)) and str(path), label + ' path is required')
    value = Path(path)
    require(value.is_absolute(), label + ' path must be absolute')
    info = value.lstat()
    require(stat.S_ISREG(info.st_mode) and not value.is_symlink(),
            label + ' must be a non-symlink regular file')
    return value.resolve(strict=True)


def files_manifest(folder):
    root = Path(folder).resolve(strict=True)
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'Build contains a symlink: ' + str(path))
        if path.is_dir():
            continue
        require(path.is_file() and path.resolve().is_relative_to(root),
                'Build contains a nonregular or external file')
        relative = path.relative_to(root).as_posix()
        result[relative] = {'size': path.stat().st_size, 'sha256': digest(path)}
    return result


def number(value, label, low, high):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            label + ' is out of range')
    return value


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum,
            label + ' must be an integer >= ' + str(minimum))
    return value


def keys(value, allowed, label):
    require(isinstance(value, dict), label + ' must be an object')
    unknown = set(value).difference(allowed)
    require(not unknown, label + ' has unsupported fields: ' + ', '.join(sorted(unknown)))


def probe(path, executable):
    path = regular_file(path, 'Media')
    before = path.stat()
    result = subprocess.run([str(executable), '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=60)
    require(result.returncode == 0, 'ffprobe failed for ' + path.name + ': ' + result.stderr.strip())
    value = json.loads(result.stdout)
    videos = [s for s in value.get('streams', []) if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in value.get('streams', []) if s.get('codec_type') == 'audio']
    require(len(videos) <= 1 and len(audios) <= 1 and (videos or audios),
            'Media must contain at most one video and one audio stream')
    stream = videos[0] if videos else audios[0]
    duration = float(stream.get('duration') or value.get('format', {}).get('duration') or 0)
    require(math.isfinite(duration) and duration > 0, 'Media duration is unavailable')
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            'Media changed during probe')
    return {'source': str(path), 'kind': 'video' if videos else 'audio',
            'duration_us': round(duration * MICROS), 'has_audio': bool(audios),
            'type': 'video' if videos else 'audio', 'sha256': digest(path),
            'size': before.st_size, 'suffix': path.suffix.lower(),
            'width': stream.get('width', 0), 'height': stream.get('height', 0)}


def validate_plan(plan, ffprobe):
    keys(plan, {'schema', 'name', 'canvas', 'tracks'}, 'Plan')
    require(plan.get('schema') == PLAN_SCHEMA, 'Unsupported plan schema')
    require(isinstance(plan.get('name'), str) and plan['name'].strip(), 'Plan name is required')
    canvas = plan.get('canvas')
    keys(canvas, {'width', 'height', 'fps'}, 'Canvas')
    width = integer(canvas.get('width'), 'Canvas width', 16)
    height = integer(canvas.get('height'), 'Canvas height', 16)
    require(width <= 8192 and height <= 8192, 'Canvas dimension exceeds 8192')
    fps = integer(canvas.get('fps'), 'FPS', 1)
    require(fps in {24, 25, 30, 50, 60}, 'Unsupported FPS')
    tracks = plan.get('tracks')
    require(isinstance(tracks, list) and tracks and tracks[0].get('type') == 'video',
            'The first track must be the main video track')
    assets, duration = {}, 0
    common = {'start_us', 'duration_us'}
    media_fields = common | {'source', 'source_start_us', 'source_duration_us', 'speed', 'volume'}
    video_fields = media_fields | {'scale', 'x', 'y'}
    text_fields = common | {'text', 'size', 'x', 'y', 'color', 'border_color', 'border_width'}
    for ti, track in enumerate(tracks):
        keys(track, {'type', 'name', 'segments'}, 'Track')
        kind = track.get('type')
        require(kind in {'video', 'audio', 'text'},
                'Windows FFmpeg build supports only video, audio and text tracks')
        require(isinstance(track.get('segments'), list) and track['segments'],
                'Each track requires at least one segment')
        prior_end = 0
        for segment in track['segments']:
            keys(segment, video_fields if kind == 'video' else
                 media_fields if kind == 'audio' else text_fields, kind + ' segment')
            start = integer(segment.get('start_us', 0), 'start_us')
            duration_us = integer(segment.get('duration_us'), 'duration_us', 1)
            require(start >= prior_end, 'Segments on one track must be ordered and nonoverlapping')
            prior_end = start + duration_us
            duration = max(duration, prior_end)
            if kind == 'text':
                require(isinstance(segment.get('text'), str) and segment['text'].strip(),
                        'Text must be nonempty')
                number(segment.get('size', 6), 'Text size', 1, 100)
                number(segment.get('x', 0), 'Text x', -5, 5)
                number(segment.get('y', -.78), 'Text y', -5, 5)
                continue
            source = str(regular_file(segment.get('source'), 'Media'))
            if source not in assets:
                assets[source] = probe(source, ffprobe)
            asset = assets[source]
            require(asset['kind'] == kind, 'Track type and media type disagree')
            source_start = integer(segment.get('source_start_us', 0), 'source_start_us')
            source_duration = integer(segment.get('source_duration_us', duration_us),
                                      'source_duration_us', 1)
            speed = number(segment.get('speed', 1), 'speed', .1, 8)
            require(abs(source_duration / speed - duration_us) <= 2,
                    'Source and target durations disagree with speed')
            require(source_start + source_duration <= asset['duration_us'] + 2,
                    'Source trim exceeds media duration')
            number(segment.get('volume', 1), 'volume', 0, 4)
            if kind == 'video':
                number(segment.get('scale', 1), 'scale', .01, 10)
                number(segment.get('x', 0), 'x', -5, 5)
                number(segment.get('y', 0), 'y', -5, 5)
    main_end = max(s.get('start_us', 0) + s['duration_us'] for s in tracks[0]['segments'])
    require(duration == main_end, 'Overlay, audio and text must fit within the main video duration')
    return assets, main_end


def copy_asset(asset, render):
    source = Path(asset['source'])
    relative = Path('Resources') / (asset['sha256'][:24] + asset['suffix'])
    destination = render / relative
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    require(destination.stat().st_size == asset['size'] and digest(destination) == asset['sha256'],
            'Copied media differs from its verified source')
    require(digest(source) == asset['sha256'], 'Source media changed while copying')
    asset['relative'] = relative.as_posix()


def compile_timeline(plan, assets):
    materials = {'videos': [], 'audios': [], 'texts': []}
    tracks = []
    media_ids, text_index = {}, 0
    for ti, source_track in enumerate(plan['tracks']):
        kind = source_track['type']
        track = {'type': kind, 'name': source_track.get('name', kind), 'segments': []}
        for si, source_segment in enumerate(source_track['segments']):
            segment = {'id': f'{kind}-{ti}-{si}',
                       'target_timerange': {'start': source_segment.get('start_us', 0),
                                            'duration': source_segment['duration_us']}}
            if kind == 'text':
                material_id = f'text-material-{text_index}'
                text_index += 1
                materials['texts'].append({'id': material_id, 'text': source_segment['text'],
                    'font_size': source_segment.get('size', 6),
                    'color': source_segment.get('color', '#FFFFFF'),
                    'border_color': source_segment.get('border_color', '#000000'),
                    'border_width': source_segment.get('border_width', .05)})
                segment.update(material_id=material_id, x=source_segment.get('x', 0),
                               y=source_segment.get('y', -.78))
            else:
                source = str(Path(source_segment['source']).resolve())
                asset = assets[source]
                key = (kind, source)
                if key not in media_ids:
                    material_id = f'{kind}-material-{len(media_ids)}'
                    media_ids[key] = material_id
                    materials['videos' if kind == 'video' else 'audios'].append({
                        'id': material_id, 'path': asset['relative'], 'type': kind,
                        'has_audio': asset['has_audio'], 'sha256': asset['sha256'],
                        'width': asset['width'], 'height': asset['height']})
                segment.update(material_id=media_ids[key],
                    source_timerange={'start': source_segment.get('source_start_us', 0),
                                      'duration': source_segment.get('source_duration_us',
                                                                     source_segment['duration_us'])},
                    speed=source_segment.get('speed', 1), volume=source_segment.get('volume', 1))
                if kind == 'video':
                    segment.update(scale=source_segment.get('scale', 1),
                                   x=source_segment.get('x', 0), y=source_segment.get('y', 0))
            track['segments'].append(segment)
        tracks.append(track)
    duration = max(s['target_timerange']['start'] + s['target_timerange']['duration']
                   for t in tracks for s in t['segments'])
    return {'schema': 'jy14-windows-render-timeline/v1', 'canvas_config': plan['canvas'],
            'fps': plan['canvas']['fps'], 'duration': duration,
            'materials': materials, 'tracks': tracks}


def verify_build(build):
    build = Path(build).resolve(strict=True)
    record = read_json(build / 'build.json')
    require(record.get('schema') == SCHEMA, 'Export requires a verified Windows render build')
    require(digest(build / 'plan.json') == record.get('plan_sha256'), 'Plan changed after build')
    render = (build / 'render').resolve(strict=True)
    require(files_manifest(render) == record.get('files'), 'Verified render snapshot changed')
    timeline_path = render / TIMELINE_NAME
    require(digest(timeline_path) == record.get('timeline_sha256'), 'Render timeline changed')
    timeline = read_json(timeline_path)
    require(timeline.get('schema') == 'jy14-windows-render-timeline/v1',
            'Unsupported render timeline')
    manifest = record['files']
    used = set()
    materials = timeline.get('materials', {})
    by_id = {}
    for bucket in ('videos', 'audios', 'texts'):
        for material in materials.get(bucket, []):
            require(material.get('id') not in by_id, 'Duplicate material id')
            by_id[material.get('id')] = material
    for track in timeline.get('tracks', []):
        for segment in track.get('segments', []):
            require(segment.get('material_id') in by_id, 'Segment references an unknown material')
    for bucket in ('videos', 'audios'):
        for material in materials.get(bucket, []):
            relative = Path(material.get('path', ''))
            require(relative.parts and not relative.is_absolute() and '..' not in relative.parts
                    and relative.parts[0] == 'Resources', 'Unsafe media dependency path')
            name = relative.as_posix()
            require(name in manifest, 'Media dependency is absent from the verified manifest')
            path = render / relative
            require(path.is_file() and not path.is_symlink()
                    and path.resolve().is_relative_to(render), 'Media dependency left the verified build')
            require(digest(path) == manifest[name]['sha256'] == material.get('sha256'),
                    'Media dependency digest changed')
            used.add(name)
    require(used == set(manifest).difference({TIMELINE_NAME}),
            'Verified build contains an unregistered media dependency')
    return build, record, timeline


def build(plan_path, out, ffprobe=None):
    plan_path = regular_file(plan_path, 'Plan')
    out = new_work_directory(out)
    executable = ffmpeg_tools.resolve_tool('ffprobe', ffprobe, 'JIANYING_FFPROBE')
    plan = read_json(plan_path)
    assets, duration = validate_plan(plan, executable)
    out.mkdir(parents=True)
    write_new(out / 'plan.json', plan_path.read_bytes())
    render = out / 'render'
    render.mkdir()
    for asset in assets.values():
        copy_asset(asset, render)
    timeline = compile_timeline(plan, assets)
    write_new(render / TIMELINE_NAME, timeline)
    record = {'schema': SCHEMA, 'plan_sha256': digest(out / 'plan.json'),
              'timeline_sha256': digest(render / TIMELINE_NAME), 'files': files_manifest(render),
              'duration_us': duration, 'fps': plan['canvas']['fps'],
              'native_editable_draft': False, 'ffprobe_version': ffmpeg_tools.version(executable)}
    write_new(out / 'build.json', record)
    verify_build(out)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    create = sub.add_parser('build')
    create.add_argument('--plan', required=True); create.add_argument('--out', required=True)
    create.add_argument('--ffprobe')
    verify = sub.add_parser('verify-build'); verify.add_argument('--build', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            executable = ffmpeg_tools.resolve_tool('ffprobe', None, 'JIANYING_FFPROBE')
            result = {'status': 'ok', 'backend': 'windows-ffmpeg',
                      'ffprobe_version': ffmpeg_tools.version(executable),
                      'native_editable_draft': False}
        elif args.command == 'build':
            result = build(args.plan, args.out, args.ffprobe)
        else:
            _, result, _ = verify_build(args.build)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print('Windows portable build failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
