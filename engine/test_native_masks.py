"""Mask integrity and source isolation; no live draft or cache mutations."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import jy14_headless as j
import native_motion as motion
import native_resources as resources


class NativeMaskTests(unittest.TestCase):
    def setUp(self):
        self.plan = j.read_json(FIXTURE / 'mask-plan.json')

    def test_all_six_masks_accept_and_copy_captured_native_resources(self):
        record = j.verify_build(FIXTURE / 'mask-build-v1')
        self.assertEqual(len(record['native_resources']), 6)
        self.assertEqual(record['duration_us'], 12000000)
        self.assertTrue(all(not r['redistribution_authorized'] for r in record['native_resources']))
        self.assertEqual(len(record['assets']), 1)

    def test_unverified_shape_and_fields_are_rejected(self):
        for shape in ('text', 'custom', '', None):
            spec = deepcopy(self.plan['tracks'][0]['segments'][0])
            spec['mask']['shape'] = shape
            with self.assertRaisesRegex(ValueError, 'Unsupported geometric'):
                motion.validate(spec, 'video')
        self.plan['tracks'][0]['segments'][0]['mask']['url'] = 'https://example.invalid'
        with self.assertRaisesRegex(ValueError, 'Unsupported mask fields'):
            j.validate_plan(self.plan)

    def test_nonfinite_values_and_nonboolean_inversion_are_rejected(self):
        for field, value in [('width', 0), ('feather', float('nan')), ('height', True), ('invert', 1)]:
            spec = deepcopy(self.plan['tracks'][0]['segments'][0])
            spec['mask'][field] = value
            with self.assertRaises(ValueError):
                motion.validate(spec, 'video')

    def test_mask_must_be_visual_and_rounded_corners_rectangular(self):
        spec = self.plan['tracks'][0]['segments'][0]
        with self.assertRaisesRegex(ValueError, 'video track'):
            motion.validate(spec, 'audio')
        spec['mask']['round_corner'] = .2
        with self.assertRaisesRegex(ValueError, 'rectangle'):
            motion.validate(spec, 'video')

    def test_missing_category_or_changed_resource_binding_is_detected(self):
        spec = self.plan['tracks'][0]['segments'][0]
        segment, materials = {}, {}
        motion.apply(segment, materials, spec, 'video', WORK / 'draft')
        node = materials['common_mask'][0]
        index = {node['id']: ('common_mask', node)}
        motion.verify(segment, index, spec, 'video', 0)
        for field in ('category', 'resource_id', 'resource_type'):
            original = node[field]
            node[field] = ''
            with self.assertRaisesRegex(ValueError, 'resource identity'):
                motion.verify(segment, index, spec, 'video', 0)
            node[field] = original
        segment['enable_video_mask'] = False
        with self.assertRaisesRegex(ValueError, 'Mask disabled'):
            motion.verify(segment, index, spec, 'video', 0)

    def test_native_default_omission_preserves_mask_configuration(self):
        spec = self.plan['tracks'][0]['segments'][0]
        segment, materials = {}, {}
        motion.apply(segment, materials, spec, 'video', WORK / 'draft')
        node = materials['common_mask'][0]
        node['config'] = {'width': .28, 'height': .5}
        motion.verify(segment, {node['id']: ('common_mask', node)}, spec, 'video', 0)
        node['config']['width'] = .3
        with self.assertRaisesRegex(ValueError, 'parameter changed'):
            motion.verify(segment, {node['id']: ('common_mask', node)}, spec, 'video', 0)

    def test_wrong_runtime_refused_without_copying(self):
        destination = WORK / 'wrong-runtime'
        with self.assertRaisesRegex(ValueError, 'runtime profile'):
            resources.prepare(self.plan, destination, {'runtime_profile': 'jy14-headless-macos-11.4.0'})
        self.assertFalse(destination.exists())

    def test_modified_resource_refused_and_original_cache_untouched(self):
        entry = resources.definition('mask/circle')
        dest = WORK / 'tampered-resource'
        shutil.copytree(entry['source'], dest)
        original = resources.tree_manifest(entry['source'])
        path = dest / next(iter(entry['files']))
        path.write_bytes(path.read_bytes() + b'changed')
        fake = deepcopy(entry)
        fake['source'] = str(dest)
        with patch.object(resources, 'definition', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'bytes differ'):
                resources.prepare(self.plan, WORK / 'must-not-copy', {'runtime_profile': resources.catalog()['runtime_profile']})
        self.assertEqual(resources.tree_manifest(entry['source']), original)
        self.assertFalse((WORK / 'must-not-copy').exists())

    def test_missing_resource_and_symlink_fail_closed(self):
        entry = resources.definition('mask/circle')
        entry['source'] = str(WORK / 'missing-source')
        with patch.object(resources, 'definition', return_value=entry):
            with self.assertRaises(FileNotFoundError):
                resources.prepare(self.plan, WORK / 'must-not-create', {'runtime_profile': resources.catalog()['runtime_profile']})
        folder = WORK / 'symlink-resource'
        folder.mkdir()
        (folder / 'external').symlink_to(FIXTURE / 'mask-plan.json')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            resources.tree_manifest(folder)

    def test_copied_resources_do_not_need_cache_for_readback(self):
        record = j.read_json(FIXTURE / 'mask-build-v1/build.json')
        original = resources.definition
        def absent_source(key):
            value = original(key)
            value['source'] = str(WORK / 'not-present')
            return value
        with patch.object(resources, 'definition', side_effect=absent_source):
            resources.verify_files(record['native_resources'], FIXTURE / 'mask-build-v1/draft', self.plan)

    def test_native_cache_rebinding_requires_explicit_mode_and_exact_bytes(self):
        entry = resources.definition('mask/circle')
        target = WORK / 'draft'
        with self.assertRaisesRegex(ValueError, 'path changed'):
            resources.verify_binding('mask/circle', entry['source'], target, j.native_media_path)
        got = resources.verify_binding('mask/circle', entry['source'], target, j.native_media_path, True)
        self.assertEqual(got['location'], 'native-cache')
        self.assertTrue(got['bytes_verified'])
        with self.assertRaisesRegex(ValueError, 'path changed'):
            resources.verify_binding('mask/circle', str(WORK / 'other-copy'), target, j.native_media_path, True)
        with patch.object(resources, 'tree_manifest', return_value={}):
            with self.assertRaisesRegex(ValueError, 'bytes changed'):
                resources.verify_binding('mask/circle', entry['source'], target, j.native_media_path, True)

    def test_line_mask_rejects_ineffective_dimension_controls(self):
        spec = self.plan['tracks'][0]['segments'][2]
        for field in ('width', 'height'):
            with self.assertRaisesRegex(ValueError, 'half-plane'):
                motion.validate(dict(spec, mask=dict(spec['mask'], **{field: .5})), 'video')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    FIXTURE, WORK = args.fixture.resolve(), args.work.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeMaskTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(),
                                 'live_written': False, 'cache_changed': False})
    raise SystemExit(not result.wasSuccessful())
