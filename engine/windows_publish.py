"""Atomic new-draft registration for Windows Jianying's local home index."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import uuid

import headless_runtime as nd
import jy14_headless as j


def _write_new(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data if isinstance(data, bytes) else nd.packed(data)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def publish(out, audit, *, resume=False, verify_build_fn=None, verify_live_fn=None):
    verify_build_fn = verify_build_fn or j.verify_build
    verify_live_fn = verify_live_fn or j.verify_live
    out = Path(out).resolve(strict=True)
    record = verify_build_fn(out)
    h = nd.helper()
    runtime = h._validate_runtime_environment()
    j.require(record.get('runtime_profile') == runtime['runtime_profile'],
              'Build runtime differs; create a fresh isolated build with the current profile')
    h._ensure_editor_closed(True)
    target = Path(record['target'])
    j.require(target.parent == nd.DRAFT_ROOT and not target.is_symlink(),
              'Invalid new-draft target')
    j.require(target.exists() == resume,
              'Use resume-publish only for an existing unchanged build; publish requires a new target')
    if resume:
        j.require(target.is_dir() and j.files_manifest(target) == record['files'],
                  'Resume refused: target differs from the exact unpublished build')
    audit = nd.fresh_directory(audit)
    lock, _ = h._acquire_directory_transaction_lock(nd.DRAFT_ROOT, 'headless draft root')
    index_lock = None
    phase = 'locked'
    temporary = stage = None
    try:
        # User Data is often a junction (isolated installs, moved profiles). Creating
        # files through the junction with CREATE_NEW can fail with EEXIST, so the lock
        # and the temporary index are written on the resolved directory; the logical
        # path is still what the index's root_path must match.
        index_dir = nd.INDEX_ROOT.resolve(strict=True)
        if not j.paths_equal(index_dir, nd.DRAFT_ROOT.resolve(strict=True)):
            index_lock, _ = h._acquire_directory_transaction_lock(index_dir, 'home index root')
        root = h._snapshot_file(index_dir / 'root_meta_info.json', 'home index')
        original = h._parse_strict_json(root.content, 'home index')
        j.require(j.paths_equal(original.get('root_path', ''), nd.INDEX_ROOT),
                  'Home index root path mismatch')
        j.require(isinstance(original.get('all_draft_store'), list), 'Home index draft list is invalid')
        entries = [entry for entry in original['all_draft_store']
                   if j.paths_equal(entry.get('draft_fold_path', ''), target)
                   or entry.get('draft_id') == record['draft_id']]
        if entries:
            j.require(resume and len(entries) == 1
                      and entries[0].get('draft_id') == record['draft_id']
                      and j.paths_equal(entries[0].get('draft_fold_path', ''), target),
                      'Draft registration conflicts')
            result = dict(verify_live_fn(out), status='already_registered',
                          index_written=False, audit=str(audit))
            _write_new(audit / 'result.json', result)
            return result

        meta = h._decrypt_metadata_in_memory(out / 'draft' / 'draft_meta_info.json')
        updated = deepcopy(original)
        updated['all_draft_store'].insert(0, j.index_entry(meta, target))
        updated['draft_ids'] = j.integer(original.get('draft_ids'), 'draft_ids') + 1
        payload = nd.packed(updated)
        temporary = root.path.parent / ('.root_meta_info.headless-' + uuid.uuid4().hex + '.tmp')
        _write_new(temporary, payload)
        phase = 'index_prepared'
        _write_new(audit / 'prepared.json', {'temporary_index': str(temporary),
                    'target': str(target), 'index_sha256': nd.digest(temporary),
                    'original_index_sha256': root.sha256, 'resumed': resume})

        if not resume:
            stage = nd.DRAFT_ROOT / ('.jy14-headless-' + uuid.uuid4().hex)
            shutil.copytree(out / 'draft', stage, copy_function=shutil.copy2)
            j.require(j.files_manifest(stage) == record['files'], 'Staged file copy differs')
            h._ensure_editor_closed(True)
            h._revalidate_snapshot(root, 'before registering new draft')
            os.rename(stage, target)
        phase = 'draft_placed'
        _write_new(audit / 'published.json', {'target': str(target),
                    'draft_id': record['draft_id'], 'registered': False, 'resumed': resume})
        h._ensure_editor_closed(True)
        h._revalidate_snapshot(root, 'immediately before home index replacement')
        j.require(j.files_manifest(target) == record['files'],
                  'Published draft changed before registration')
        j.require(temporary.read_bytes() == payload,
                  'Prepared home index changed before commit')
        os.replace(temporary, root.path)
        phase = 'index_replaced'
        after = j.read_json(root.path)
        j.require(after == updated and after['all_draft_store'][1:] ==
                  original['all_draft_store'], 'Unrelated home entries changed')
        result = verify_live_fn(out)
        result.update(status='created', audit=str(audit), ui_preparation_used=False,
                      bookmarks_written=False, original_index_sha256=root.sha256,
                      current_index_sha256=nd.digest(root.path),
                      index_acl='inherited by same-directory temporary file; not independently audited')
        _write_new(audit / 'result.json', result)
        return result
    except Exception as error:
        recovery = ('inspect-index-and-run-verify' if phase == 'index_replaced' else
                    'resume-publish-after-fixing-cause' if phase == 'draft_placed' or resume else
                    'publish-after-fixing-cause')
        failure = {'status': 'failed', 'phase': phase, 'resumed': resume,
                   'index_replaced': phase == 'index_replaced', 'target': str(target),
                   'temporary_index': str(temporary) if temporary else None,
                   'staged_draft': str(stage) if stage else None,
                   'recovery': recovery, 'error': str(error), 'files_deleted': False}
        try:
            _write_new(audit / 'failure.json', failure)
        except OSError:
            pass
        raise RuntimeError('Windows draft registration stopped at ' + phase + ': ' + str(error)
                           + '. Audit retained at ' + str(audit) + '; next: ' + recovery) from error
    finally:
        if index_lock is not None:
            h._release_directory_transaction_lock(index_lock)
        h._release_directory_transaction_lock(lock)
