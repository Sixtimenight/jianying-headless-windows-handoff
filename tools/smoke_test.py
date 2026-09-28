#!/usr/bin/env python3
"""Generate synthetic fixtures and verify a build; opt in to native export.

Never registers a draft, touches editor UI, reads accounts or deletes fixtures.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true', help='also test the installed native MP4 renderer')
    args = parser.parse_args()
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='package-smoke-', dir=work))

    def run(name, command, timeout=180):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        (job / (name + '.stdout.log')).write_text(result.stdout)
        (job / (name + '.stderr.log')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(name + ' failed; retained audit: ' + str(job))
        return result.stdout

    run('doctor', [sys.executable, str(ENTRY), 'doctor'])
    source = job / 'synthetic.mp4'
    run('fixture', ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                   'testsrc2=size=640x360:rate=30:duration=3', '-f', 'lavfi', '-i',
                   'sine=frequency=440:sample_rate=48000:duration=3', '-c:v', 'libx264',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)])
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = {'schema': 'jy14-headless-plan/v1', 'name': 'package-smoke-' + job.name,
            'canvas': {'width': 640, 'height': 360, 'fps': 30}, 'tracks': [
                {'type': 'video', 'name': 'Synthetic main', 'segments': [
                    {'source': str(source), 'start_us': 0, 'duration_us': 1000000, 'volume': .3},
                    {'source': str(source), 'start_us': 1000000, 'duration_us': 1000000,
                     'source_start_us': 1000000, 'source_duration_us': 1500000, 'speed': 1.5, 'volume': .3}]},
                {'type': 'video', 'name': 'Picture in picture', 'segments': [
                    {'source': str(source), 'start_us': 0, 'duration_us': 2000000,
                     'scale': .3, 'x': .45, 'y': .4, 'rotation': 12, 'volume': 0}]},
                {'type': 'text', 'name': 'Subtitle', 'segments': [
                    {'text': '私有源码打包测试', 'start_us': 0, 'duration_us': 2000000, 'size': 7}]}]}
    plan_path = job / 'plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    build = job / 'build'
    run('build', [sys.executable, str(ENTRY), 'build', '--plan', str(plan_path), '--out', str(build)])
    run('verify-build', [sys.executable, str(ENTRY), 'verify-build', '--build', str(build)])
    result = {'status': 'synthetic-build-verified', 'draft_registered': False, 'user_media_used': False,
              'asr_called': False, 'native_ui_checked': False, 'native_export_run': args.export}
    if args.export:
        output = job / 'export'
        run('export', [sys.executable, str(ENTRY), 'export', '--build', str(build), '--out', str(output)], timeout=660)
        exported = json.loads((output / 'result.json').read_bytes())
        result.update(status='synthetic-build-and-export-verified', export_status=exported['status'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
        raise RuntimeError('Synthetic source changed; retained audit: ' + str(job))
    result['source_unchanged'] = True
    (job / 'smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(dict(result, audit_directory=str(job)), ensure_ascii=False))


if __name__ == '__main__':
    main()
