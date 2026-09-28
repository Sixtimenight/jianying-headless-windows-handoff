#!/usr/bin/env python3
"""Content-bound ledger around the installed Volcano transcribe-only executor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

EXECUTOR = Path(os.environ.get('YICHEN_ASR_EXECUTOR', str(Path.home() / 'scripts/transcribe.py')))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def read_result(path):
    value = json.loads(Path(path).read_text())
    if not value.get('result', {}).get('text'):
        raise ValueError('ASR result does not contain completed text.')
    return value


def write_json(path, value, exclusive=False):
    with Path(path).open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def bound_result(source, ledger):
    key = digest(source)
    record = Path(ledger) / key / 'state.json'
    if not record.exists():
        return key, None
    state = json.loads(record.read_text())
    if state.get('source_sha256') != key:
        raise ValueError('Ledger/source hash mismatch.')
    if state.get('status') != 'completed':
        raise ValueError('Prior request is pending or ambiguous; inspect its state, do not resubmit.')
    result = record.parent / 'result.json'
    if digest(result) != state.get('result_sha256'):
        raise ValueError('Bound result changed; inspect before reuse.')
    read_result(result)
    return key, result


def adopt(source, cache, ledger, expected_hash):
    key, existing = bound_result(source, ledger)
    if key != expected_hash:
        raise ValueError('Explicit source hash does not match this media.')
    if existing:
        return {'status': 'reused', 'result': str(existing), 'api_called': False}
    result = read_result(cache)
    folder = Path(ledger).resolve() / key
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    output = folder / 'result.json'
    write_json(output, result, True)
    write_json(folder / 'state.json', {
        'status': 'completed', 'source_sha256': key, 'source': str(Path(source).resolve()),
        'result_sha256': digest(output), 'origin': 'existing cache with independently checked source hash',
        'api_called': False,
    }, True)
    return {'status': 'adopted', 'result': str(output), 'api_called': False}


def run(source, ledger):
    source = Path(source).resolve(strict=True)
    key, existing = bound_result(source, ledger)
    if existing:
        return {'status': 'reused', 'result': str(existing), 'api_called': False}
    cache = Path(str(source) + '.asr_cache.json')
    pending = Path(str(source) + '.asr_pending.json')
    if pending.exists():
        raise ValueError('Native pending-request file exists; inspect/resume it without new submission.')
    if cache.exists():
        raise ValueError('Unbound native cache exists; verify provenance and adopt it first.')
    # Load only the known local executor. Import has no network or media writes.
    executor = runpy.run_path(str(EXECUTOR))
    token = executor.get('ACCESS_TOKEN', '')
    app_id = executor.get('APP_ID', '')
    if not token:
        raise ValueError('Current ASR app has no credential; no request sent. Use environment or Keychain.')
    del executor
    folder = Path(ledger).resolve() / key
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    state = {'status': 'submitting', 'source_sha256': key, 'source': str(source),
             'app_id': app_id, 'created_at': int(time.time()), 'credential_persisted': False}
    write_json(folder / 'state.json', state, True)
    try:
        process = subprocess.run([sys.executable, str(EXECUTOR), str(source), '--transcribe-only'],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=7200)
        # Keep diagnostic output local and redact the resolved credential and common auth fields.
        log = process.stdout.replace(token, '[credential redacted]')
        (folder / 'executor.log').write_text(log)
        os.chmod(folder / 'executor.log', 0o600)
        token = ''
        result = read_result(cache)
        if digest(source) != key:
            raise ValueError('Media changed while transcription was running; result is not reusable.')
        output = folder / 'result.json'
        write_json(output, result, True)
        state.update(status='completed', result_sha256=digest(output), executor_exit=process.returncode)
        write_json(folder / 'state.json', state)
        return {'status': 'completed', 'result': str(output), 'api_called': True,
                'utterances': len(result['result'].get('utterances', []))}
    except BaseException:
        state['status'] = 'failed_or_ambiguous'
        write_json(folder / 'state.json', state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ['run', 'adopt']:
        p = sub.add_parser(command)
        p.add_argument('--source', required=True, type=Path)
        p.add_argument('--ledger', required=True, type=Path)
        if command == 'adopt':
            p.add_argument('--cache', required=True, type=Path)
            p.add_argument('--expect-source-sha', required=True)
    args = parser.parse_args()
    result = run(args.source, args.ledger) if args.command == 'run' else adopt(
        args.source, args.cache, args.ledger, args.expect_source_sha)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
