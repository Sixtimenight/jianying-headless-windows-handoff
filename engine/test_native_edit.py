import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import jy14_headless as j
import native_edit as e


class CopyEditorTests(unittest.TestCase):
    def setUp(self):
        self.source = j.read_json(BUILD / 'source-timeline.json')
        self.plan = j.read_json(BUILD / 'plan.json')
        self.expected = j.read_json(BUILD / 'expected-timeline.json')
        self.record = j.read_json(BUILD / 'build.json')
        self.target = Path(self.record['target'])

    def test_full_nine_operation_copy_and_original_integrity(self):
        e.verify_build(BUILD)
        self.assertEqual(len(self.record['operations']), 9)
        self.assertEqual([len(t['segments']) for t in self.expected['tracks']], [3, 1, 3, 1, 1, 2])
        self.assertEqual(self.expected['tracks'][1]['name'], '修改后的画中画')
        self.assertEqual(j.files_manifest(Path(self.record['source'])), self.record['source_files'])

    def test_unknown_nonempty_fields_survive_operation(self):
        doc = deepcopy(self.source)
        doc['opaque_plugin_settings'] = {'vendor_value': [1, 'preserve', {'nested': True}]}
        doc['tracks'][1]['segments'][0]['private_native_setting'] = {'threshold': .8123}
        before = deepcopy(doc)
        op = self.plan['operations'][2]
        e.apply_operations(doc, {}, [op], self.target)
        self.assertEqual(doc['opaque_plugin_settings'], before['opaque_plugin_settings'])
        self.assertEqual(doc['tracks'][1]['segments'][0]['private_native_setting'], {'threshold': .8123})

    def test_volume_edit_preserves_native_padded_mp3_interval(self):
        segment = deepcopy(self.source['tracks'][5]['segments'][0])
        before = deepcopy(segment['source_timerange'])
        e.set_segment(deepcopy(self.source), segment, 'audio', {'volume': .2})
        self.assertEqual(segment['source_timerange'], before)
        self.assertEqual(segment['volume'], .2)

    def test_existing_padding_does_not_authorize_new_overshoot(self):
        segment = deepcopy(self.source['tracks'][5]['segments'][0])
        media = {'duration_us': 500000}
        self.assertEqual(e.validate_media_interval(segment, media, self.source), 20000)
        with self.assertRaisesRegex(ValueError, 'exceeds source'):
            e.validate_media_interval(segment, media, self.source, replacement=True)
        segment['source_timerange']['duration'] += 1
        with self.assertRaisesRegex(ValueError, 'exceeds source'):
            e.validate_media_interval(segment, media, self.source)

    def test_speed_material_is_copy_on_write(self):
        doc = deepcopy(self.source)
        original = self.source['tracks'][0]['segments'][1]
        modified = doc['tracks'][0]['segments'][1]
        old_index = e.material_index(self.source)
        old = next(old_index[r][1] for r in original['extra_material_refs'] if old_index[r][0] == 'speeds')
        doc['tracks'][0]['segments'][0]['extra_material_refs'].append(old['id'])
        e.set_segment(doc, modified, 'video', {'speed': 2, 'source_duration_us': 4000000})
        new_index = e.material_index(doc)
        new = next(new_index[r][1] for r in modified['extra_material_refs'] if new_index[r][0] == 'speeds')
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual((old['speed'], new['speed']), (1.5, 2))
        self.assertEqual(new_index[old['id']][1], old)

    def test_duplicate_materials_have_independent_node_identity(self):
        segments = self.expected['tracks'][5]['segments']
        refs = [{s['material_id'], *s['extra_material_refs']} for s in segments]
        self.assertFalse(refs[0] & refs[1])
        index = e.material_index(self.expected)
        self.assertEqual(index[segments[0]['material_id']][1]['local_material_id'],
                         index[segments[1]['material_id']][1]['local_material_id'])

    def test_inconsistent_speed_timing_is_rejected(self):
        segment = deepcopy(self.source['tracks'][0]['segments'][1])
        with self.assertRaisesRegex(ValueError, 'durations disagree'):
            e.set_segment(deepcopy(self.source), segment, 'video', {'speed': 2})

    def test_text_uses_utf16_length_and_preserves_style(self):
        item = next(iter(self.source['materials']['texts']))
        old = deepcopy(item)
        e.replace_text(item, '新标题 😀')
        content = json.loads(item['content'])
        self.assertEqual(content['text'], '新标题 😀')
        self.assertEqual(content['styles'][0]['range'], [0, 6])
        for a, b in zip(content['styles'], json.loads(old['content'])['styles']):
            self.assertEqual({k: v for k, v in a.items() if k != 'range'}, {k: v for k, v in b.items() if k != 'range'})
        content['styles'][0]['range'] = [0, 1]
        item['content'] = json.dumps(content)
        with self.assertRaisesRegex(ValueError, 'style-range'):
            e.replace_text(item, '不能静默破坏局部样式')

    def test_overlapping_duplicate_is_rejected(self):
        doc = deepcopy(self.source)
        op = {'op': 'duplicate_segment', 'id': doc['tracks'][1]['segments'][0]['id'], 'start_us': 1500000}
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            e.apply_operations(doc, {}, [op], self.target)

    def test_linked_transition_boundary_edits_are_rejected(self):
        for op in ('duplicate_segment', 'remove_segment'):
            doc = deepcopy(self.source)
            segment = doc['tracks'][0]['segments'][0]
            doc['materials'].setdefault('transitions', []).append({'id': 'test-transition', 'type': 'transition'})
            segment['extra_material_refs'].append('test-transition')
            operation = {'op': op, 'id': segment['id']}
            if op == 'duplicate_segment':
                operation['start_us'] = 6000000
            with self.assertRaisesRegex(ValueError, 'boundary adapter'):
                e.apply_operations(doc, {}, [operation], self.target)

    def test_existing_filter_settings_remain_intact(self):
        doc = deepcopy(self.source)
        material = {'id': 'test-filter', 'type': 'filter', 'value': .6, 'resource_id': 'existing-id',
                    'path': '/existing/authorized/cache', 'opaque_native_metadata': {'keep': 7}}
        doc['materials'].setdefault('effects', []).append(material)
        e.apply_operations(doc, {}, [{'op': 'set_filter_intensity', 'id': material['id'], 'set': {'value': .2}}], self.target)
        self.assertEqual(material['value'], .2)
        self.assertEqual(material['opaque_native_metadata'], {'keep': 7})
        self.assertEqual(material['resource_id'], 'existing-id')

    def test_unknown_operations_and_duplicate_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported edit'):
            e.apply_operations(deepcopy(self.source), {}, [{'op': 'flatten_video'}], self.target)
        doc = deepcopy(self.source)
        doc['tracks'][1]['segments'][0]['id'] = doc['tracks'][0]['segments'][0]['id']
        with self.assertRaisesRegex(ValueError, 'Duplicate segment'):
            e.basic_validation(doc)

    def test_new_segment_has_own_sidecar_registration(self):
        op = next(o for o in self.record['operations'] if o['op'] == 'duplicate_segment')
        kv = j.read_json(BUILD / 'draft/key_value.json')
        self.assertEqual(kv[op['created_segment_id']]['segmentId'], op['created_segment_id'])
        self.assertEqual(kv[op['id']]['segmentId'], op['id'])

    def test_post_build_tampering_is_rejected_without_live_write(self):
        target = WORK / 'tampered-build'
        shutil.copytree(BUILD, target)
        e.write_owned(target / 'draft/key_value.json', {'tampered': True})
        with self.assertRaisesRegex(ValueError, 'changed after build'):
            e.verify_build(target)

    def test_readback_detects_nonempty_field_loss(self):
        with self.assertRaisesRegex(ValueError, 'disappeared'):
            e.preserved({'plugin': {'important': .5}}, {'plugin': {}})
        e.preserved({'empty': '', 'off': False}, {})

    def test_native_rounding_is_bounded_and_audio_default_is_true(self):
        seen = []
        e.preserved({'target_timerange': {'start': 500000}}, {'target_timerange': {'start': 520000}},
                    '/tracks/a/segments/b', frame_tolerance=40000, quantized=seen)
        self.assertEqual(len(seen), 1)
        with self.assertRaisesRegex(ValueError, 'numeric value'):
            e.preserved({'target_timerange': {'start': 500000}}, {'target_timerange': {'start': 580000}},
                        '/tracks/a/segments/b', frame_tolerance=40000)
        e.preserved({'has_audio': True}, {}, '/materials/videos/id')
        with self.assertRaisesRegex(ValueError, 'audio capability'):
            e.preserved({'has_audio': False}, {}, '/materials/videos/id')

    def test_stale_source_plan_stops_before_copy(self):
        stale = deepcopy(self.plan)
        stale['source']['timeline_sha256'] = '0' * 64
        path = WORK / 'stale-plan.json'
        j.write(path, stale)
        destination = WORK / 'must-not-exist'
        # Test the precondition guard on its frozen capture, not a live editor
        # read. The production load_source still requires a closed editor.
        snapshot = (Path(self.plan['source']['draft_path']), self.record['source_files'], self.source,
                    {'draft_id': self.plan['source']['draft_id']}, {})
        with patch.object(e, 'load_source', return_value=snapshot) as loader:
            with self.assertRaisesRegex(ValueError, 'Stale source'):
                e.build(path, destination)
            loader.assert_called_once_with(self.plan['source']['draft_path'])
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    BUILD, WORK = args.build.resolve(), args.work.resolve()
    WORK.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CopyEditorTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(), 'live_written': False})
    raise SystemExit(not result.wasSuccessful())
