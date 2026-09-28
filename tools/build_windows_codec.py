#!/usr/bin/env python3
"""Build the Windows codec adapter against the user's installed Jianying DLL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'bridge' / 'jy-draftc-windows.cpp'
BUILD = ROOT / 'tools' / 'build'
OUTPUT = BUILD / 'jy-draftc-windows.exe'


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def run(argv: list[str], *, cwd: Path = ROOT,
        timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Compiler failed')[-6000:])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', help='C++17 MinGW compiler; defaults to g++ on PATH')
    parser.add_argument('--force', action='store_true', help='Replace the local bridge executable')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('The Windows native codec adapter can only be built on Windows')
    compiler = args.compiler or shutil.which('g++')
    if not compiler:
        raise RuntimeError('MinGW-w64 g++ is required; install it or pass --compiler')
    if not SOURCE.is_file():
        raise RuntimeError('The attributed jy-draftc adapter source is missing')
    BUILD.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists() and not args.force:
        raise RuntimeError('The bridge already exists. Pass --force to rebuild it.')

    # MinGW's argv/path conversion may not handle non-ASCII workspace paths.
    # Compile from an ASCII-only temporary directory, then copy the executable
    # back with Python's Unicode-aware filesystem APIs.
    with tempfile.TemporaryDirectory(prefix='jy14-codec-', dir=ROOT.anchor) as name:
        job = Path(name)
        source_copy = job / 'jy-draftc-windows.cpp'
        temporary = job / 'jy-draftc-windows.exe'
        shutil.copyfile(SOURCE, source_copy)
        command = [compiler, '-std=c++17', '-O2', '-municode', '-Wall', '-Wextra',
                   '-static', '-static-libgcc', '-static-libstdc++', str(source_copy),
                   '-o', str(temporary)]
        compiler_info = run([compiler, '--version'], timeout=30).stdout.splitlines()[0]
        result = run(command, cwd=job)
        report = {'schema': 'jy14-windows-codec-build/v1', 'status': 'built',
                  'source_sha256': digest(SOURCE), 'bridge_sha256': digest(temporary),
                  'compiler': compiler_info, 'command': command,
                  'stderr': result.stderr, 'official_jianying_library_copied': False,
                  'application_modified': False, 'network_called': False}
        os.replace(temporary, OUTPUT)
        (BUILD / 'codec-build-report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'bridge': str(OUTPUT),
                          'bridge_sha256': report['bridge_sha256'], 'compiler': compiler_info},
                         ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
