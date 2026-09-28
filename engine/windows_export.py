"""Render a verified Windows snapshot to MP4 without native Jianying access."""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

import ffmpeg_graph
import ffmpeg_tools
import windows_portable as portable

SCHEMA = 'jy14-ffmpeg-export/v2'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def write(path, value):
    portable.write_new(path, value)


def verified_font(path, job):
    source = portable.regular_file(path, 'Font')
    require(source.suffix.lower() in {'.ttf', '.otf', '.ttc'}, 'Font must be TTF, OTF or TTC')
    before = source.stat()
    require(12 <= before.st_size <= 100 * 1024 * 1024, 'Font size is invalid')
    fingerprint = digest(source)
    target = job / 'font' / ('render-font' + source.suffix.lower())
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    after = source.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            and digest(source) == fingerprint and digest(target) == fingerprint,
            'Font changed while snapshotting')
    return target, {'name': source.name, 'size': before.st_size, 'sha256': fingerprint}


def stage_timeline(timeline, build, record, job):
    render = (Path(build) / 'render').resolve(strict=True)
    staged = job / 'staged'
    staged.mkdir()
    result = json.loads(json.dumps(timeline))
    copied = {}
    for bucket in ('videos', 'audios'):
        for material in result['materials'].get(bucket, []):
            relative = Path(material['path'])
            name = relative.as_posix()
            require(name in record['files'] and name != portable.TIMELINE_NAME,
                    'Unregistered media dependency')
            source = render / relative
            require(source.is_file() and not source.is_symlink()
                    and source.resolve().is_relative_to(render), 'Unsafe media dependency')
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(source, destination)
            expected = record['files'][name]
            require(destination.stat().st_size == expected['size']
                    and digest(destination) == expected['sha256'], 'Staged media digest changed')
            material['path'] = destination.relative_to(job).as_posix()
            copied[name] = expected['sha256']
    return result, copied


def fraction(value):
    if not value or value == '0/0':
        return 0.0
    left, separator, right = str(value).partition('/')
    return float(left) / float(right) if separator else float(value)


def probe(executable, output, job, expected, timeout):
    result = subprocess.run(
        [str(executable), '-v', 'error', '-count_frames', '-show_streams', '-show_format',
         '-of', 'json', str(output)], cwd=job, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=timeout)
    (job / 'ffprobe.stdout.log').write_bytes(result.stdout.encode('utf-8'))
    (job / 'ffprobe.stderr.log').write_bytes(result.stderr.encode('utf-8'))
    require(result.returncode == 0, 'ffprobe failed: ' + result.stderr.strip())
    value = json.loads(result.stdout)
    video = [s for s in value.get('streams', []) if s.get('codec_type') == 'video']
    audio = [s for s in value.get('streams', []) if s.get('codec_type') == 'audio']
    require(len(video) == 1 and video[0].get('codec_name') == 'h264', 'Expected one H.264 stream')
    require(video[0].get('pix_fmt') == 'yuv420p', 'Expected yuv420p output')
    canvas = expected['canvas_config']
    require((int(video[0]['width']), int(video[0]['height'])) ==
            (int(canvas['width']), int(canvas['height'])), 'Output dimensions differ')
    require(abs(fraction(video[0].get('avg_frame_rate')) - float(expected['fps'])) < .001,
            'Output FPS differs')
    duration = float(value.get('format', {}).get('duration', 0))
    expected_seconds = expected['duration'] / 1_000_000
    require(abs(duration - expected_seconds) <= max(.08, 2 / expected['fps']),
            'Output duration differs')
    frames = int(video[0].get('nb_read_frames') or video[0].get('nb_frames') or 0)
    span = Fraction(expected['duration'], 1_000_000) * int(expected['fps'])
    nearest = round(span)
    # A microsecond-quantized frame boundary has one valid count. Only a
    # genuinely fractional span may round to either adjacent count.
    aligned = abs(span - nearest) <= Fraction(int(expected['fps']), 1_000_000)
    minimum = nearest if aligned else math.floor(span)
    maximum = nearest if aligned else math.ceil(span)
    require(minimum <= frames <= maximum,
            'Output frame count differs: got %d, expected %d..%d' %
            (frames, minimum, maximum))
    materials = {item['id']: item for bucket in expected.get('materials', {}).values()
                 if isinstance(bucket, list) for item in bucket if isinstance(item, dict)}
    expected_audio = any(
        track.get('type') == 'audio' or
        (track.get('type') == 'video' and
         any(materials.get(segment.get('material_id'), {}).get('has_audio')
             for segment in track.get('segments', [])))
        for track in expected.get('tracks', []))
    require(len(audio) == (1 if expected_audio else 0), 'Output audio stream count differs')
    if audio:
        start = float(audio[0].get('start_time', 0))
        audio_duration = float(audio[0].get('duration') or duration)
        require(abs(start) <= .05 and abs(audio_duration - expected_seconds) <= .1,
                'Output audio timing differs')
    return value


def safe_command(command, job):
    root = str(job)
    return [str(item).replace(root, '<JOB>') for item in command]


def run(build, out, ffmpeg=None, ffprobe=None, font=None, crf=18, preset='medium', timeout=600):
    build, record, original = portable.verify_build(build)
    out = portable.new_work_directory(out)
    out.mkdir(parents=True)
    started = time.monotonic()
    evidence = {'schema': SCHEMA, 'backend': 'windows-ffmpeg', 'status': 'preparing',
                'native_editable_draft': False, 'source_build_unchanged': False,
                'full_decode_passed': False, 'network_allowed': False}
    try:
        ffmpeg = ffmpeg_tools.resolve_tool('ffmpeg', ffmpeg, 'JIANYING_FFMPEG')
        ffprobe = ffmpeg_tools.resolve_tool('ffprobe', ffprobe, 'JIANYING_FFPROBE')
        evidence.update(ffmpeg_name=ffmpeg.name, ffprobe_name=ffprobe.name,
                        ffmpeg_version=ffmpeg_tools.version(ffmpeg),
                        ffprobe_version=ffmpeg_tools.version(ffprobe))
        timeline, copied = stage_timeline(original, build, record, out)
        font_record = None
        has_text = any(track.get('type') == 'text' for track in timeline.get('tracks', []))
        if has_text:
            require(font is not None, 'Text rendering requires --font')
            font, font_record = verified_font(font, out)
        inputs, graph, video, audio, warnings = ffmpeg_graph.build(timeline, out, font)
        (out / 'filter_complex.txt').write_bytes(graph.encode('utf-8'))
        command = []
        for item in inputs:
            if item['still']:
                command += ['-loop', '1']
            command += ['-i', item['path']]
        command += ['-filter_complex_script', 'filter_complex.txt', '-map', video]
        if audio:
            command += ['-map', audio]
        command += ['-c:v', 'libx264', '-preset', preset, '-crf', str(crf),
                    '-pix_fmt', 'yuv420p', '-r', str(timeline['fps'])]
        if audio:
            command += ['-c:a', 'aac', '-b:a', '192k']
        command += ['-t', ffmpeg_graph.sec(timeline['duration']), '-movflags', '+faststart',
                    'render.mp4']
        evidence['ffmpeg_command'] = safe_command([ffmpeg.name, *command], out)
        _, code = ffmpeg_tools.run(ffmpeg, command, out / 'ffmpeg.stdout.log',
                                   out / 'ffmpeg.stderr.log', timeout, cwd=out)
        require(code == 0, 'FFmpeg render failed; inspect ffmpeg.stderr.log')
        output = out / 'render.mp4'
        media = probe(ffprobe, Path('render.mp4'), out, timeline, min(timeout, 120))
        decode = ['-v', 'error', '-xerror', '-i', 'render.mp4', '-f', 'null', '-']
        _, code = ffmpeg_tools.run(ffmpeg, decode, out / 'decode.stdout.log',
                                   out / 'decode.stderr.log', timeout, cwd=out)
        require(code == 0, 'Rendered MP4 did not fully decode')
        _, after, _ = portable.verify_build(build)
        require(after == record, 'Source build changed during export')
        evidence.update(status='encoded-and-decoded', output='render.mp4',
                        output_sha256=digest(output), output_bytes=output.stat().st_size,
                        media=media, staged_resources=copied, font=font_record, warnings=warnings,
                        full_decode_passed=True, source_build_unchanged=True,
                        elapsed_seconds=round(time.monotonic() - started, 3))
        write(out / 'result.json', evidence)
        return evidence
    except Exception as error:
        evidence.update(status='failed', error=str(error), partial_artifacts_retained=True,
                        elapsed_seconds=round(time.monotonic() - started, 3))
        write(out / 'result.json', evidence)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--ffmpeg'); parser.add_argument('--ffprobe'); parser.add_argument('--font')
    parser.add_argument('--crf', type=int, default=18); parser.add_argument('--preset', default='medium')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    try:
        result = run(args.build, args.out, args.ffmpeg, args.ffprobe, args.font,
                     args.crf, args.preset, args.timeout)
        print(json.dumps({key: result[key] for key in
              ('status', 'output', 'full_decode_passed', 'source_build_unchanged',
               'elapsed_seconds')}, ensure_ascii=False))
    except Exception as error:
        print('Windows FFmpeg export failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
