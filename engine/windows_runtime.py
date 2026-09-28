"""Windows runtime adapter for the local Jianying installation.

The official application and videoeditor.dll stay installed and user-owned.
Only the two metadata files in a newly built private draft are sent to the
local DLL through the separately attributed jy-draftc bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CODEC = PROJECT_ROOT / 'tools' / 'build' / 'jy-draftc-windows.exe'
CODEC_REPORT = CODEC.parent / 'codec-build-report.json'
CODEC_SOURCE = PROJECT_ROOT / 'bridge' / 'jy-draftc-windows.cpp'
EXPECTED_CODEC_SOURCE_SHA256 = 'ab173af42cec074c504db8ec0a51c54531265aa64ed6d32a1f0c973cdafe199e'
EXPECTED_APP_VERSION = '11.5.0.14471'
EXPECTED_DLL_SHA256 = '37cadef37daff82e2ecdcd65080b9cbd7f6f9436f5e56c1ffb90c52c408eb7fb'
WINDOWS_PROFILE = 'jy14-headless-windows-' + EXPECTED_APP_VERSION
MANIFEST_SHA = '2fea820b26d503940526c345ce8e9bd87c25f0c1c8b1c4a02aa8edba317e33dd'
MICROS = 1_000_000
MAX_METADATA_BYTES = 16 * 1024 * 1024


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def packed(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _loads(raw: bytes):
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda token: (_ for _ in ()).throw(
                          ValueError('Invalid JSON number: ' + token)))


def _version_tuple(value: str):
    return tuple(int(part) for part in re.findall(r'\d+', value))


def _registry_install_root() -> Path | None:
    if os.name != 'nt':
        return None
    import winreg
    keys = (
        r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
    )
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in keys:
            try:
                parent = winreg.OpenKey(hive, key_path)
            except OSError:
                continue
            with parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        child_name = winreg.EnumKey(parent, index)
                        child = winreg.OpenKey(parent, child_name)
                    except OSError:
                        continue
                    with child:
                        try:
                            name = winreg.QueryValueEx(child, 'DisplayName')[0]
                            command = winreg.QueryValueEx(child, 'UninstallString')[0]
                        except OSError:
                            continue
                    if name != '剪映专业版':
                        continue
                    match = re.match(r'^\s*"?(.+?\.exe)"?(?:\s|$)', command,
                                     flags=re.IGNORECASE)
                    if match:
                        uninstaller = Path(match.group(1))
                        if uninstaller.is_absolute():
                            return uninstaller.parent
    return None


def discover_install_dir() -> Path:
    configured = os.environ.get('JY_INSTALL_DIR', '').strip().strip('"')
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    root = _registry_install_root()
    if root:
        candidates.extend(sorted((p for p in root.iterdir() if p.is_dir()),
                                 key=lambda p: _version_tuple(p.name), reverse=True))
    for candidate in candidates:
        if (candidate / 'videoeditor.dll').is_file():
            return candidate.resolve(strict=True)
    raise FileNotFoundError('Could not locate a Jianying version directory containing videoeditor.dll. '
                            'Set JY_INSTALL_DIR to that exact directory.')


def _user_data() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise RuntimeError('LOCALAPPDATA is unavailable')
    return Path(local) / 'JianyingPro' / 'User Data'


def index_root() -> Path:
    # root_meta_info.json always stays here, even when drafts are saved elsewhere.
    # Keep the logical LocalAppData path. The directory may be a junction to
    # another volume; Jianying's home index stores the logical path spelling.
    return Path(os.path.abspath(_user_data() / 'Projects' / 'com.lveditor.draft'))


def _custom_draft_root() -> Path | None:
    setting = _user_data() / 'Config' / 'globalSetting'
    try:
        text = setting.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r'^currentCustomDraftPath=(.+?)\s*$', text, flags=re.MULTILINE)
    if not match:
        return None
    # QSettings escapes backslashes in INI values.
    value = match.group(1).strip().strip('"').replace('\\\\', '\\')
    return Path(os.path.abspath(value)) if value else None


def draft_root() -> Path:
    """Folder where Jianying places new drafts (the "草稿位置" setting)."""
    override = os.environ.get('JY_DRAFT_ROOT', '').strip().strip('"')
    if override:
        return Path(os.path.abspath(override))
    return _custom_draft_root() or index_root()


INDEX_ROOT = index_root()
DRAFT_ROOT = draft_root()
# Windows Jianying stores the active timeline as draft_content.json (macOS: draft_info.json).
TIMELINE_FILE = 'draft_content.json'


def doctor() -> dict:
    if os.name != 'nt':
        raise RuntimeError('The native draft backend requires Windows')
    install = discover_install_dir()
    dll = install / 'videoeditor.dll'
    version = install.name
    if version != EXPECTED_APP_VERSION:
        raise RuntimeError('No reviewed Windows runtime profile for Jianying ' + version +
                           '; this local port is pinned to ' + EXPECTED_APP_VERSION)
    fingerprint = digest(dll)
    if fingerprint != EXPECTED_DLL_SHA256:
        raise RuntimeError('The installed videoeditor.dll differs from the reviewed local profile; '
                           'native draft writes are disabled')
    if not CODEC.is_file() or CODEC.is_symlink():
        raise RuntimeError('Windows codec bridge is missing; build it with '
                           'python tools/build_windows_codec.py')
    if not CODEC_REPORT.is_file() or CODEC_REPORT.is_symlink():
        raise RuntimeError('Windows codec build report is missing; rebuild the local bridge')
    report = json.loads(CODEC_REPORT.read_text(encoding='utf-8'))
    if (report.get('schema') != 'jy14-windows-codec-build/v1'
            or report.get('status') != 'built'
            or report.get('source_sha256') != EXPECTED_CODEC_SOURCE_SHA256
            or report.get('source_sha256') != digest(CODEC_SOURCE)
            or report.get('bridge_sha256') != digest(CODEC)
            or report.get('official_jianying_library_copied') is not False
            or report.get('application_modified') is not False):
        raise RuntimeError('Windows codec executable or build report differs from the reviewed source')
    if not (INDEX_ROOT / 'root_meta_info.json').is_file():
        raise RuntimeError('Jianying local draft index is unavailable at ' + str(INDEX_ROOT))
    if not DRAFT_ROOT.is_dir():
        raise RuntimeError('Jianying draft folder is unavailable at ' + str(DRAFT_ROOT))
    return {'status': 'ok', 'backend': 'windows-native-draft',
            'app_version': '.'.join(version.split('.')[:3]), 'app_build': version,
            'runtime_profile': WINDOWS_PROFILE, 'videoeditor_dll_sha256': fingerprint,
            'codec_executable_sha256': report['bridge_sha256'], 'draft_root': str(DRAFT_ROOT), 'index_root': str(INDEX_ROOT),
            'library_copied': False, 'application_modified': False,
            'network_called': False, 'native_export_supported': False}


def validate_runtime():
    return doctor()


def validate_plan_scope(plan: dict) -> None:
    """Reject mac-captured resources/effects until Windows-native parity is proven."""
    for track in plan.get('tracks', []):
        if track.get('type') not in {'video', 'audio', 'text'}:
            raise ValueError('Windows native draft port currently supports video, audio and text tracks only')
        for segment in track.get('segments', []):
            unsupported = {'mask', 'transition_out', 'text_effect', 'keyframes'} & set(segment)
            if unsupported:
                raise ValueError('Windows native draft port does not yet verify: ' +
                                 ', '.join(sorted(unsupported)))
    return None


@dataclass(frozen=True)
class Snapshot:
    path: Path
    content: bytes
    sha256: str
    mode: int
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    parent_device: int
    parent_inode: int


def _snapshot_file(path: Path, label: str):
    path = Path(os.path.abspath(os.fspath(path)))
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(label + ' must be a regular local file within the size limit')
    path = path.resolve(strict=True)
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise RuntimeError(label + ' exceeds the size limit')
    parent = path.parent.stat()
    before = path.stat()
    content = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino) or len(content) != after.st_size:
        raise RuntimeError(label + ' changed while reading')
    return Snapshot(path, content, hashlib.sha256(content).hexdigest(), before.st_mode,
                    before.st_size, before.st_dev, before.st_ino, before.st_mtime_ns,
                    before.st_ctime_ns, parent.st_dev, parent.st_ino)


def _revalidate_snapshot(snapshot: Snapshot, phase: str):
    current = _snapshot_file(snapshot.path, phase)
    if (current.sha256, current.size, current.mtime_ns, current.inode,
            current.parent_device, current.parent_inode) != (
            snapshot.sha256, snapshot.size, snapshot.mtime_ns, snapshot.inode,
            snapshot.parent_device, snapshot.parent_inode):
        raise RuntimeError('File changed ' + phase)


def _codec_env():
    env = os.environ.copy()
    env['JY_INSTALL_DIR'] = str(discover_install_dir())
    return env


def _codec(command: str, source: Path, destination: Path) -> None:
    result = subprocess.run([str(CODEC), command, str(source), str(destination)],
                            cwd=PROJECT_ROOT, env=_codec_env(), capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=120)
    if result.returncode:
        raise RuntimeError('Local Jianying metadata codec failed: ' +
                           (result.stderr or result.stdout)[-3000:])


def _temporary_codec_files():
    (PROJECT_ROOT / 'work').mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix='windows-codec-', dir=PROJECT_ROOT / 'work')


def _decrypt_metadata_in_memory(path: Path):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 512 * 1024 * 1024:
        raise RuntimeError('Encrypted metadata exceeds the size limit')
    # Pre-6.0 drafts, and drafts converted for 5.9-era tools, store plain JSON.
    with path.open('rb') as stream:
        head = stream.read(1)
    if head == b'{':
        raw = path.read_bytes()
        if len(raw) > MAX_METADATA_BYTES:
            raise RuntimeError('Plain metadata exceeds the size limit')
        return _loads(raw)
    with _temporary_codec_files() as directory:
        plain = Path(directory) / 'metadata.json'
        _codec('--dec', path, plain)
        raw = plain.read_bytes()
        if not raw or len(raw) > MAX_METADATA_BYTES:
            raise RuntimeError('Decrypted metadata is empty or exceeds the size limit')
        return _loads(raw)


def _encrypt_metadata_from_memory(content: bytes, destination: Path):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Refusing to overwrite encrypted metadata: ' + str(destination))
    if not isinstance(content, bytes) or not content or len(content) > MAX_METADATA_BYTES:
        raise RuntimeError('Plain metadata is empty or exceeds the size limit')
    with _temporary_codec_files() as directory:
        plain = Path(directory) / 'metadata.json'
        encrypted = Path(directory) / 'metadata.enc'
        plain.write_bytes(content)
        _codec('--enc', plain, encrypted)
        decoded = _decrypt_metadata_in_memory(encrypted)
        if packed(decoded) != packed(_loads(content)):
            raise RuntimeError('Local encryption round-trip changed the metadata')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(encrypted.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())


def _parse_strict_json(content: bytes, label: str = 'JSON'):
    try:
        return _loads(content)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError(label + ' is not strict JSON: ' + str(error)) from error


def _ensure_editor_closed(required=True):
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=20)
    if result.returncode:
        raise RuntimeError('Could not confirm whether Jianying is closed')
    running = any(name in result.stdout.lower() for name in
                  ('jianyingpro.exe', 'videoeditor.exe'))
    if running and required:
        raise RuntimeError('Save your work and fully exit Jianying before registering a draft')
    return not running


def _acquire_directory_transaction_lock(path: Path, label: str):
    lock_path = Path(path).resolve(strict=True) / '.jy14-headless-windows.lock'
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError(label + ' is locked by another operation or a retained recovery record') from error
    with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
        stream.write(str(os.getpid()) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return lock_path, {'path': str(path.resolve()), 'lock': str(lock_path)}


def _release_directory_transaction_lock(lock):
    Path(lock).unlink(missing_ok=True)


def _adapt_blueprint(blueprint: dict) -> dict:
    profile = doctor()
    app_version = profile['app_version']
    os_version = platform.version()
    timeline = blueprint['timeline']
    timeline['new_version'] = '187.0.0'
    for field in ('platform', 'last_modified_platform'):
        timeline[field] = dict(timeline.get(field, {}), os='windows',
                               os_version=os_version, app_id=3704,
                               app_version=app_version, app_source='lv')
    legacy = blueprint.get('legacy', {})
    for field in ('platform', 'last_modified_platform'):
        if field in legacy:
            legacy[field] = dict(legacy[field], os='windows', os_version=os_version,
                                 app_id=3704, app_version=app_version, app_source='lv')
    font = discover_install_dir() / 'Resources' / 'Font' / 'SystemFont' / 'zh-hans.ttf'
    text = blueprint.get('text', {})
    for _, material in text.get('materials', []):
        if material.get('font_path'):
            material['font_path'] = str(font)
        try:
            content = json.loads(material.get('content', '{}'))
        except (ValueError, TypeError):
            continue
        for style in content.get('styles', []):
            if isinstance(style.get('font'), dict) and style['font'].get('path'):
                style['font']['path'] = str(font)
        material['content'] = json.dumps(content, ensure_ascii=False,
                                         separators=(',', ':'))
    return blueprint


def helper():
    profile = doctor()
    return SimpleNamespace(
        _decrypt_metadata_in_memory=_decrypt_metadata_in_memory,
        _encrypt_metadata_from_memory=_encrypt_metadata_from_memory,
        _ensure_editor_closed=_ensure_editor_closed,
        _snapshot_file=_snapshot_file,
        _parse_strict_json=_parse_strict_json,
        _revalidate_snapshot=_revalidate_snapshot,
        _acquire_directory_transaction_lock=_acquire_directory_transaction_lock,
        _release_directory_transaction_lock=_release_directory_transaction_lock,
        _validate_runtime_environment=validate_runtime,
        validate_plan_scope=validate_plan_scope,
        runtime_profile=profile['runtime_profile'],
        adapt_blueprint=_adapt_blueprint)
