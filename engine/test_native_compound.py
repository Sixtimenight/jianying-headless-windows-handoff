"""Behavioral checks for captured editable compounds and isolated copy edits."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest

import jy14_headless as j
import native_compound as c
import native_edit as e
import native_export as export


class CompoundTests(unittest.TestCase):
    def setUp(self):
        self.original = j.read_json(CAPTURE / 'capture-native-compound/timeline.json')
        self.before = j.read_json(CAPTURE / 'capture-before-compound/timeline.json')
        self.record = j.read_json(BUILD / 'build.json')
        self.expected = j.read_json(BUILD / 'expected-timeline.json')
        self.target = Path(self.record['target'])

    def test_nested_copy_preserves_all_tracks_and_changes_requested_fields(self):
        e.verify_build(BUILD)
        child = self.expected['materials']['drafts'][0]['draft']
        self.assertEqual([len(t['segments']) for t in child['tracks']], [2, 1, 1, 1])
        self.assertEqual(json.loads(child['materials']['texts'][0]['content'])['text'], '复合片段内编辑 · 原稿不变')
        self.assertEqual(child['tracks'][1]['segments'][0]['clip']['transform'], {'x': -.25, 'y': .15})
        self.assertEqual(child['tracks'][3]['segments'][0]['volume'], .07)
        self.assertEqual(child['tracks'][1]['name'], '复合内部画中画')
        self.assertEqual(self.expected['duration'], 4_000_000)

    def test_unaccepted_compound_persistence_prevents_live_registration(self):
        with self.assertRaisesRegex(ValueError, 'Compound live registration is blocked'):
            c.require_publishable(self.expected)

    def test_regular_multitrack_registration_remains_available(self):
        c.require_publishable(self.before)

    def test_recursive_media_and_original_bytes_are_checked(self):
        self.assertEqual(len(self.record['media_dependencies']), 3)
        self.assertEqual(j.files_manifest(Path(self.record['source'])), self.record['source_files'])
        for row in self.record['media_dependencies']:
            self.assertTrue(Path(row['path']).is_relative_to(self.target / 'Resources'))

    def test_sidecar_matches_edited_embedded_child(self):
        checked = c.check_sidecars(self.expected, self.target, BUILD / 'draft', e.preserved)
        self.assertEqual(len(checked), 1)
        self.assertEqual(checked[0]['timeline_id'], self.expected['materials']['drafts'][0]['draft']['id'])

    def test_nested_edit_keeps_unknown_native_fields(self):
        doc = deepcopy(self.original)
        child = doc['materials']['drafts'][0]['draft']
        child['opaque_local_settings'] = {'preserve': ['native', 1.23]}
        operation = {'op': 'set_segment', 'timeline_id': child['id'],
                     'id': child['tracks'][3]['segments'][0]['id'], 'set': {'volume': .04}}
        e.apply_operations(doc, {}, [operation], self.target)
        self.assertEqual(child['opaque_local_settings'], {'preserve': ['native', 1.23]})
        self.assertEqual(child['tracks'][3]['segments'][0]['volume'], .04)

    def test_unknown_nested_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown or ambiguous'):
            e.apply_operations(deepcopy(self.original), {},
                               [{'op': 'rename_track', 'timeline_id': 'absent', 'id': 'x', 'name': 'x'}], self.target)

    def test_nested_duration_change_needs_parent_policy(self):
        doc = deepcopy(self.original)
        child = doc['materials']['drafts'][0]['draft']
        operation = {'op': 'duplicate_segment', 'timeline_id': child['id'],
                     'id': child['tracks'][0]['segments'][1]['id'], 'start_us': 4_000_000}
        with self.assertRaisesRegex(ValueError, 'parent-range policy'):
            e.apply_operations(doc, {}, [operation], self.target)

    def test_orphan_or_ambiguous_compound_reference_is_rejected(self):
        doc = deepcopy(self.original)
        owner_id = doc['materials']['drafts'][0]['id']
        doc['tracks'][0]['segments'][0]['extra_material_refs'].remove(owner_id)
        with self.assertRaisesRegex(ValueError, 'unambiguous clip owner'):
            c.validate(doc, e.basic_validation)

    def test_cycle_and_unsupported_compound_kind_are_rejected(self):
        doc = deepcopy(self.original)
        child = doc['materials']['drafts'][0]['draft']
        child['materials']['drafts'] = [deepcopy(doc['materials']['drafts'][0])]
        with self.assertRaisesRegex(ValueError, 'Repeated/cyclic'):
            list(c.graph(doc))
        doc = deepcopy(self.original)
        doc['materials']['drafts'][0]['combination_type'] = 'multicam'
        with self.assertRaisesRegex(ValueError, 'Only captured'):
            list(c.graph(doc))

    def test_compound_placeholder_and_source_ranges_are_validated(self):
        doc = deepcopy(self.original)
        doc['materials']['videos'][0]['duration'] += 1_000_000
        with self.assertRaisesRegex(ValueError, 'dimensions/duration'):
            c.validate(doc, e.basic_validation)
        doc = deepcopy(self.original)
        doc['tracks'][0]['segments'][0]['source_timerange']['start'] = 1
        doc['tracks'][0]['segments'][0]['source_timerange']['duration'] += 1
        with self.assertRaisesRegex(ValueError, 'source range or speed'):
            c.validate(doc, e.basic_validation)

    def test_sidecar_cannot_escape_its_own_draft_directory(self):
        owner = deepcopy(self.original['materials']['drafts'][0])
        owner['draft_file_path'] = '/unrelated/compound.json'
        with self.assertRaisesRegex(ValueError, 'draft-local'):
            c.paths(owner, self.target)
        owner['draft_file_path'] = j.DRAFT_PATH_TOKEN + '../outside.json'
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            c.paths(owner, self.target)

    def test_stale_sidecar_is_not_silently_accepted(self):
        folder = WORK / 'stale-sidecar'
        shutil.copytree(BUILD / 'draft', folder)
        owner = self.expected['materials']['drafts'][0]
        path = folder / 'subdraft' / owner['draft']['id'] / 'draft_content.json'
        sidecar = j.read_json(path)
        sidecar['materials']['drafts'][0]['draft']['tracks'][3]['segments'][0]['volume'] = .08
        e.write_owned(path, sidecar)
        with self.assertRaisesRegex(ValueError, 'numeric value'):
            c.check_sidecars(self.expected, self.target, folder, e.preserved)

    def test_nested_order_changes_are_rejected(self):
        actual = deepcopy(self.expected)
        actual['materials']['drafts'][0]['draft']['tracks'].reverse()
        with self.assertRaisesRegex(ValueError, 'track order/count'):
            c.check_order(self.expected, actual)

    def test_wrap_all_is_editable_and_retains_original_child_ids(self):
        doc = deepcopy(self.before)
        old_tracks = deepcopy(doc['tracks'])
        old_materials = deepcopy(doc['materials'])
        event = c.wrap_all(doc, '离线新复合片段', self.target)
        c.validate(doc, e.basic_validation)
        child = doc['materials']['drafts'][0]['draft']
        self.assertEqual(child['tracks'], old_tracks)
        self.assertEqual(child['materials'], old_materials)
        self.assertEqual(doc['materials']['videos'][0]['path'], '')
        self.assertEqual(doc['materials']['videos'][0]['extra_type_option'], 2)
        self.assertEqual(len(doc['tracks']), 1)
        self.assertEqual(event['created_timeline_id'], child['id'])
        self.assertNotEqual(child['id'], self.before['id'])

    def test_new_compound_sidecars_and_embedded_graph_match(self):
        doc = deepcopy(self.before)
        c.wrap_all(doc, '新建并核对配套文件', self.target)
        folder = WORK / 'new-compound'
        shutil.copytree(CAPTURE / 'capture-build-v1/draft', folder)
        c.write_sidecars(doc, self.target, folder, Path(self.record['source']), e.write_owned, e.rebase)
        checked = c.check_sidecars(doc, self.target, folder, e.preserved)
        self.assertEqual(len(checked), 1)

    def test_blueprint_has_no_user_media_or_device_identifiers(self):
        raw = c.BLUEPRINT.read_text()
        for forbidden in ('/Users/', 'device_id', 'hard_disk_id', 'mac_address', 'headless-media'):
            self.assertNotIn(forbidden, raw)
        proto = j.read_json(c.BLUEPRINT)
        self.assertEqual(proto['wrapper_materials']['drafts'][0]['draft'], {})
        self.assertEqual(proto['sidecar']['materials']['drafts'][0]['draft'], {})

    def test_native_empty_companion_recreation_is_bijective_and_reported(self):
        saved = j.read_json(CAPTURE / 'capture-edited-first-save/timeline.json')
        compared, changes = c.normalize_companion_ids(self.expected, saved)
        self.assertEqual(len(changes), 2)
        self.assertEqual({row['bucket'] for row in changes}, {'loudnesses', 'vocal_separations'})
        left = self.expected['materials']['drafts'][0]['draft']
        right = compared['materials']['drafts'][0]['draft']
        e.preserved(c.normalize_paths(left, self.target), c.normalize_paths(right, self.target))
        self.assertNotEqual(saved['materials']['drafts'][0]['draft']['tracks'][1]['segments'][0]['extra_material_refs'],
                            right['tracks'][1]['segments'][0]['extra_material_refs'])

    def test_changed_audio_companion_parameter_is_never_normalized_away(self):
        saved = j.read_json(CAPTURE / 'capture-edited-first-save/timeline.json')
        saved['materials']['drafts'][0]['draft']['materials']['loudnesses'][0]['gain'] = .5
        compared, changes = c.normalize_companion_ids(self.expected, saved)
        self.assertEqual(len(changes), 1)
        with self.assertRaises(ValueError):
            e.preserved(self.expected['materials']['drafts'][0]['draft'], compared['materials']['drafts'][0]['draft'])

    def test_top_level_companion_reassignment_is_not_implicitly_allowed(self):
        left = self.expected['materials']['drafts'][0]['draft']
        right = j.read_json(CAPTURE / 'capture-edited-first-save/timeline.json')['materials']['drafts'][0]['draft']
        compared, changes = c.normalize_companion_ids(left, right)
        self.assertEqual(changes, [])
        with self.assertRaises(ValueError):
            e.preserved(left, compared)

    def test_export_checks_child_capabilities_without_flattening(self):
        value = export.supported_features(self.expected)
        self.assertEqual(value['nested_timelines'], 1)
        bad = deepcopy(self.expected)
        bad['materials']['drafts'][0]['draft']['materials']['video_effects'] = [{'id': 'unverified-effect'}]
        with self.assertRaisesRegex(ValueError, 'Unverified native video effect identity: type'):
            export.supported_features(bad)

    def test_export_stages_recursive_media_and_sidecar_references(self):
        out = WORK / 'staged-compound'; out.mkdir()
        value, files = export.stage_timeline(self.expected, self.record, BUILD / 'draft', out)
        self.assertEqual(len(files), 6)
        self.assertEqual(len(value['tracks']), 1)
        child = value['materials']['drafts'][0]['draft']
        self.assertEqual(len(child['tracks']), 4)
        for bucket in ('videos', 'audios'):
            for node in child['materials'][bucket]:
                self.assertTrue(Path(node['path']).is_relative_to(out / 'Resources'))
                self.assertTrue(Path(node['path']).is_file())
        self.assertNotIn(str(self.target), json.dumps(value))
        sidecar = j.read_json(Path(value['materials']['drafts'][0]['draft_file_path']))
        self.assertNotIn(str(self.target), json.dumps(sidecar))
        self.assertEqual(self.expected, j.read_json(BUILD / 'expected-timeline.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    CAPTURE, BUILD, WORK = args.capture.resolve(), args.build.resolve(), args.work.resolve()
    WORK.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CompoundTests))
    j.write(WORK / 'result.json', {'tests': result.testsRun, 'passed': result.wasSuccessful(), 'live_written': False})
    raise SystemExit(not result.wasSuccessful())
