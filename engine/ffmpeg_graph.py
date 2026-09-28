"""Translate a verified portable timeline into one connected FFmpeg graph."""
from pathlib import Path

MICROS = 1_000_000


def sec(value):
    return f'{value / MICROS:.6f}'


def escape_path(value, work):
    path = Path(value).resolve()
    try:
        path = path.relative_to(Path(work).resolve())
    except ValueError:
        pass
    return path.as_posix().replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")


def color(value, fallback):
    value = value if isinstance(value, str) and len(value) == 7 and value.startswith('#') else fallback
    return '0x' + value[1:]


def atempo_chain(speed):
    speed = float(speed)
    parts = []
    while speed > 2:
        parts.append('atempo=2')
        speed /= 2
    while speed < .5:
        parts.append('atempo=0.5')
        speed /= .5
    parts.append(f'atempo={speed:.6f}')
    return ','.join(parts)


def material_index(timeline):
    result = {}
    for values in timeline.get('materials', {}).values():
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict) and value.get('id'):
                    result[value['id']] = value
    return result


def build(timeline, work, font=None):
    """Return inputs, graph, final video/audio labels and warnings."""
    work = Path(work)
    canvas = timeline['canvas_config']
    width, height, fps = int(canvas['width']), int(canvas['height']), int(timeline['fps'])
    total = int(timeline['duration'])
    materials = material_index(timeline)
    inputs, filters, audio_parts, warnings = [], [], [], []

    def add_input(material):
        path = Path(material['path'])
        checked = path if path.is_absolute() else work / path
        if not checked.is_file() or checked.is_symlink():
            raise ValueError('Missing staged material: ' + str(path))
        inputs.append({'path': str(path), 'index': len(inputs),
                       'still': path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif'}})
        return len(inputs) - 1

    filters.append(f'color=c=black:s={width}x{height}:r={fps}:d={sec(total)}[vcanvas]')
    current_video = '[vcanvas]'
    video_tracks = [track for track in timeline.get('tracks', []) if track.get('type') == 'video']
    for ti, track in enumerate(video_tracks):
        for si, segment in enumerate(track.get('segments', [])):
            material = materials[segment['material_id']]
            index = add_input(material)
            source = segment['source_timerange']
            target = segment['target_timerange']
            start, duration = int(target['start']), int(target['duration'])
            speed = float(segment.get('speed', 1))
            raw = f'vr{ti}_{si}'
            placed = f'vp{ti}_{si}'
            chain = (f'[{index}:v]trim=start={sec(source["start"])}:duration={sec(source["duration"])},'
                     f'setpts=(PTS-STARTPTS)/{speed:.6f}')
            if ti == 0:
                chain += (f',scale={width}:{height}:force_original_aspect_ratio=decrease,'
                          f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1')
                x, y = '0', '0'
            else:
                scale = float(segment.get('scale', 1))
                chain += f',scale=iw*{scale:.6f}:ih*{scale:.6f},setsar=1'
                x = f'(W-w)/2+({float(segment.get("x", 0)):.6f})*W/2'
                y = f'(H-h)/2-({float(segment.get("y", 0)):.6f})*H/2'
            chain += f',setpts=PTS-STARTPTS+{sec(start)}/TB[{raw}]'
            filters.append(chain)
            filters.append(f"{current_video}[{raw}]overlay=x='{x}':y='{y}':eof_action=pass:"
                           f"shortest=0:enable='between(t,{sec(start)},{sec(start + duration)})'[{placed}]")
            current_video = f'[{placed}]'

            if material.get('has_audio'):
                label = f'av{ti}_{si}'
                audio = (f'[{index}:a]atrim=start={sec(source["start"])}:duration={sec(source["duration"])},'
                         f'asetpts=PTS-STARTPTS,{atempo_chain(speed)},'
                         f'volume={float(segment.get("volume", 1)):.6f},'
                         f'apad,atrim=duration={sec(duration)},adelay={round(start / 1000)}:all=1[{label}]')
                filters.append(audio)
                audio_parts.append(f'[{label}]')

    for ti, track in enumerate(timeline.get('tracks', [])):
        if track.get('type') != 'audio':
            continue
        for si, segment in enumerate(track.get('segments', [])):
            material = materials[segment['material_id']]
            index = add_input(material)
            source, target = segment['source_timerange'], segment['target_timerange']
            start, duration = int(target['start']), int(target['duration'])
            label = f'aa{ti}_{si}'
            filters.append(
                f'[{index}:a]atrim=start={sec(source["start"])}:duration={sec(source["duration"])},'
                f'asetpts=PTS-STARTPTS,{atempo_chain(segment.get("speed", 1))},'
                f'volume={float(segment.get("volume", 1)):.6f},'
                f'apad,atrim=duration={sec(duration)},adelay={round(start / 1000)}:all=1[{label}]')
            audio_parts.append(f'[{label}]')

    text_count = 0
    text_tracks = [track for track in timeline.get('tracks', []) if track.get('type') == 'text']
    if text_tracks and not font:
        raise ValueError('Text rendering requires an explicitly verified --font file')
    for track in text_tracks:
        for segment in track.get('segments', []):
            material = materials[segment['material_id']]
            target = segment['target_timerange']
            start, duration = int(target['start']), int(target['duration'])
            text_file = work / 'captions' / f'{text_count:04d}.txt'
            text_file.parent.mkdir(parents=True, exist_ok=True)
            text_file.write_bytes(material['text'].encode('utf-8'))
            output = f'vtext{text_count}'
            size = max(12, round(float(material.get('font_size', 6)) * height / 100))
            x = f'(w-text_w)/2+({float(segment.get("x", 0)):.6f})*w/2'
            y = f'(h-text_h)/2-({float(segment.get("y", -.78)):.6f})*h/2'
            border = max(0, round(float(material.get('border_width', .05)) * size))
            filters.append(
                f"{current_video}drawtext=fontfile='{escape_path(font, work)}':"
                f"textfile='{escape_path(text_file, work)}':fontsize={size}:"
                f"fontcolor={color(material.get('color'), '#FFFFFF')}:"
                f"borderw={border}:bordercolor={color(material.get('border_color'), '#000000')}:"
                f"x={x}:y={y}:enable='between(t,{sec(start)},{sec(start + duration)})'[{output}]")
            current_video = f'[{output}]'
            text_count += 1

    audio_label = None
    if audio_parts:
        filters.append(f'anullsrc=r=48000:cl=stereo:d={sec(total)}[asilence]')
        filters.append('[asilence]' + ''.join(audio_parts) +
                       f'amix=inputs={len(audio_parts) + 1}:duration=longest:'
                       'dropout_transition=0:normalize=0,'
                       f'atrim=duration={sec(total)}[aout]')
        audio_label = '[aout]'
    filters.append(f'{current_video}trim=duration={sec(total)},setpts=PTS-STARTPTS[vout]')
    return inputs, ';'.join(filters), '[vout]', audio_label, warnings
