#!/usr/bin/env python3
"""Build the local Windows native export helper with MSVC (x64, /MD).

The helper calls the engine's C++ interfaces, which pass MSVC std::string,
std::shared_ptr and std::function; MinGW is not ABI compatible with them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'bridge' / 'jy-export-windows.cpp'
OUT_DIR = ROOT / 'tools' / 'build'
HELPER = OUT_DIR / 'jy-export-windows.exe'
REPORT = OUT_DIR / 'export-build-report.json'
VSWHERE = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / \
    'Microsoft Visual Studio' / 'Installer' / 'vswhere.exe'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def vcvars():
    if not VSWHERE.is_file():
        raise SystemExit('Visual Studio Build Tools (C++ workload) are required; vswhere.exe not found')
    root = subprocess.check_output([str(VSWHERE), '-latest', '-products', '*', '-requires',
                                    'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                    '-property', 'installationPath'], text=True).strip()
    script = Path(root) / 'VC' / 'Auxiliary' / 'Build' / 'vcvars64.bat'
    if not root or not script.is_file():
        raise SystemExit('MSVC x64 toolset not found; install the "Desktop development with C++" workload')
    return script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Rebuild even if the helper exists')
    args = parser.parse_args()
    if HELPER.exists() and REPORT.exists() and not args.force:
        report = json.loads(REPORT.read_text(encoding='utf-8'))
        if report.get('source_sha256') == digest(SOURCE) and report.get('helper_sha256') == digest(HELPER):
            print('The export helper already exists. Pass --force to rebuild it.')
            return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='jy-export-build-') as tmp:
        command = (f'"{vcvars()}" >nul && cl /nologo /std:c++17 /EHsc /O2 /MD /utf-8 /W3 '
                   f'"{SOURCE}" /Fe:"{HELPER}" /Fo:"{tmp}\\\\"')
        result = subprocess.run(command, shell=True, capture_output=True, text=True, errors='replace')
    if result.returncode != 0 or not HELPER.is_file():
        print(result.stdout, result.stderr)
        raise SystemExit('MSVC build of the export helper failed')
    version = subprocess.run(f'"{vcvars()}" >nul && cl 2>&1', shell=True, capture_output=True,
                             text=True, errors='replace').stdout.splitlines()
    report = {'schema': 'jy14-windows-export-build/v1', 'status': 'built',
              'source_sha256': digest(SOURCE), 'helper_sha256': digest(HELPER),
              'compiler': version[0].strip() if version else 'unknown'}
    REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
