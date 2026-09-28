"""Observed native custom-curve average recomputation, with strict controls.

The numeric example comes from a synthetic 11.5 GUI copy/save. No app, codec,
account identifiers or private draft files are required by these tests.
"""
from copy import deepcopy
import unittest

from test_native_defaults_115 import PROFILE, edit, saved_copy_fixture


def captured_shape():
    return {
        'id': 'curve-timeline', 'version': 360000, 'new_version': '187.0.0',
        'duration': 2066666, 'canvas_config': {'width': 640, 'height': 360},
        'last_modified_platform': {'app_version': '11.5.0'},
        'materials': {
            'videos': [{'id': 'video', 'type': 'video'}],
            'speeds': [{
                'id': 'curve-speed', 'type': 'speed', 'mode': 1,
                'speed': 1.4488572870241114,
                'curve_speed': {'id': '6768730851543880206', 'name': '自定义',
                                'speed_points': [
                                    {'x': 0.0, 'y': 1.0}, {'x': 0.25, 'y': 1.0},
                                    {'x': 0.49822416759672616, 'y': 4.257648468017578},
                                    {'x': 0.75, 'y': 1.0}, {'x': 1.0, 'y': 1.0}]},
            }],
        },
        'tracks': [{'id': 'video-track', 'type': 'video', 'segments': [{
            'id': 'curve-segment', 'material_id': 'video',
            'extra_material_refs': ['curve-speed'], 'speed': 1.4488572870241114,
            'source_timerange': {'duration': 3000000},
            'target_timerange': {'duration': 2066666},
        }]}],
    }


def owners(doc):
    return doc['materials']['speeds'][0], doc['tracks'][0]['segments'][0]


def saved_pair():
    before = captured_shape()
    after = deepcopy(before)
    for owner in owners(after):
        owner['speed'] = 3000000 / 2066666
    return before, after


class NativeCurveAverageTests(unittest.TestCase):
    def test_observed_recomputation_passes_full_entry_without_rewriting_evidence(self):
        before, after = saved_pair()
        with saved_copy_fixture(before, after) as (out, target, source):
            files = edit.j.files_manifest(out.parent)
            result = edit.verify_live(out)
            self.assertEqual(result['status'], 'verified')
            changes = result['native_curve_speed_recomputations']
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]['native_speed'], 3000000 / 2066666)
            self.assertGreater(changes[0]['frame_rounding_us'], 0)
            self.assertLess(changes[0]['frame_rounding_us'], 1_000_000 / 30)
            self.assertEqual(edit.j.files_manifest(out.parent), files)

    def test_normalization_is_a_copy_and_generic_comparison_stays_strict(self):
        before, after = saved_pair()
        originals = deepcopy((before, after))
        compared, changes = edit.normalize_saved_curve_averages(before, after, PROFILE)
        self.assertEqual((before, after), originals)
        self.assertEqual(len(changes), 1)
        edit.compare_saved_timeline(before, compared, PROFILE)
        with self.assertRaises(ValueError):
            edit.compare_saved_timeline(before, after, PROFILE)
        reversed_copy, changes = edit.normalize_saved_curve_averages(after, before, PROFILE)
        self.assertEqual(changes, [])
        self.assertEqual(reversed_copy, before)

    def test_zero_start_omission_combines_with_recomputation_in_full_entry(self):
        for field in ('source_timerange', 'target_timerange'):
            for side in ('expected', 'actual'):
                with self.subTest(field=field, explicit_zero_side=side):
                    before, after = saved_pair()
                    document = before if side == 'expected' else after
                    owners(document)[1][field]['start'] = 0
                    originals = deepcopy((before, after))
                    with saved_copy_fixture(before, after) as (out, _, __):
                        files = edit.j.files_manifest(out.parent)
                        result = edit.verify_live(out)
                        self.assertEqual(result['status'], 'verified')
                        self.assertEqual(len(result['native_curve_speed_recomputations']), 1)
                        self.assertTrue(result['four_mirrors_equal'])
                        self.assertTrue(result['source_files_unchanged'])
                        self.assertEqual(edit.j.files_manifest(out.parent), files)
                    self.assertEqual((before, after), originals)
                    reversed_copy, changes = edit.normalize_saved_curve_averages(after, before, PROFILE)
                    self.assertEqual(changes, [])
                    self.assertEqual(reversed_copy, before)

    def test_range_normalization_keeps_nonzero_timing_types_and_other_fields_strict(self):
        changes = [('start', value) for value in (1, -1, False, True, 0.0, None, '0')]
        changes += [('duration', value) for value in (True, None, '3000000')]
        changes += [('new_setting', value) for value in ('changed', None)]
        for field in ('source_timerange', 'target_timerange'):
            for side in ('expected', 'actual'):
                for key, value in changes + [('duration', 'same_as_float')]:
                    with self.subTest(field=field, side=side, key=key, value=value):
                        before, after = saved_pair()
                        document = before if side == 'expected' else after
                        timerange = owners(document)[1][field]
                        timerange[key] = float(timerange[key]) if value == 'same_as_float' else value
                        originals = deepcopy((before, after))
                        compared, normalized = edit.normalize_saved_curve_averages(before, after, PROFILE)
                        self.assertEqual(normalized, [])
                        self.assertEqual(compared, after)
                        self.assertEqual((before, after), originals)
                        with saved_copy_fixture(before, after) as (out, _, __):
                            with self.assertRaises(ValueError):
                                edit.verify_live(out)

    def test_identical_nonzero_starts_still_allow_recomputation(self):
        before, after = saved_pair()
        for document in (before, after):
            owners(document)[1]['source_timerange']['start'] = 1000000
            owners(document)[1]['target_timerange']['start'] = 333333
            document['duration'] += 333333
        with saved_copy_fixture(before, after) as (out, _, __):
            result = edit.verify_live(out)
            self.assertEqual(result['status'], 'verified')
            self.assertEqual(len(result['native_curve_speed_recomputations']), 1)

    def test_runtime_and_schema_boundaries(self):
        for profile in ('jy14-headless-macos-11.4.2', 'unknown', None):
            before, after = saved_pair()
            self.assertEqual(edit.normalize_saved_curve_averages(before, after, profile)[1], [])
        for field, value in (('new_version', '185.0.0'), ('fps', 60),
                             ('last_modified_platform', {'app_version': '11.4.2'})):
            with self.subTest(field=field):
                before, after = saved_pair()
                before[field] = after[field] = value
                self.assertEqual(edit.normalize_saved_curve_averages(before, after, PROFILE)[1], [])

    def test_changes_to_curve_references_timing_or_only_one_speed_still_fail(self):
        for change in ('point', 'mode', 'material_speed', 'segment_speed', 'reference',
                       'source_start', 'source_duration', 'target_duration', 'unknown_field',
                       'boolean_mode', 'boolean_point'):
            with self.subTest(change=change):
                before, after = saved_pair()
                material, segment = owners(after)
                if change == 'point':
                    material['curve_speed']['speed_points'][2]['y'] += 0.1
                elif change in ('mode', 'boolean_mode'):
                    material['mode'] = 2 if change == 'mode' else True
                elif change in ('material_speed', 'segment_speed'):
                    (material if change == 'material_speed' else segment)['speed'] = 1.5
                elif change == 'reference':
                    segment['extra_material_refs'] = []
                elif change == 'source_start':
                    segment['source_timerange']['start'] = 1
                elif change in ('source_duration', 'target_duration'):
                    segment[change.split('_')[0] + '_timerange']['duration'] += 1
                elif change == 'unknown_field':
                    material['unexpected'] = 'changed'
                else:
                    material['curve_speed']['speed_points'][0]['y'] = True
                with saved_copy_fixture(before, after) as (out, _, __):
                    with self.assertRaises(ValueError):
                        edit.verify_live(out)

    def test_unobserved_curve_shapes_and_shared_owners_are_not_normalized(self):
        for shape in ('flat', 'empty', 'preset', 'audio', 'shared', 'nonaligned', 'far_rounding'):
            with self.subTest(shape=shape):
                before, after = saved_pair()
                for doc in (before, after):
                    material, segment = owners(doc)
                    if shape == 'flat':
                        for point in material['curve_speed']['speed_points']:
                            point['y'] = 1.0
                    elif shape == 'empty':
                        material['curve_speed']['speed_points'] = []
                    elif shape == 'preset':
                        material['curve_speed']['id'] = 'unobserved-preset'
                    elif shape == 'audio':
                        doc['tracks'][0]['type'] = 'audio'
                    elif shape == 'shared':
                        other = deepcopy(segment)
                        other['id'] = 'second-owner'
                        doc['tracks'][0]['segments'].append(other)
                    elif shape == 'nonaligned':
                        segment['target_timerange']['duration'] = 2066000
                    elif shape == 'far_rounding' and doc is before:
                        material['speed'] = segment['speed'] = 1.2
                if shape == 'nonaligned':
                    for owner in owners(after):
                        owner['speed'] = 3000000 / 2066000
                self.assertEqual(edit.normalize_saved_curve_averages(before, after, PROFILE)[1], [])

    def test_absent_unit_or_nondefault_values_cannot_enter_recomputation(self):
        for value in (None, True, 1, 1.5):
            for index in (0, 1):
                with self.subTest(value=value, owner=index):
                    before, after = saved_pair()
                    owner = owners(after)[index]
                    if value is None:
                        del owner['speed']
                    else:
                        owner['speed'] = value
                    with saved_copy_fixture(before, after) as (out, _, __):
                        with self.assertRaises(ValueError):
                            edit.verify_live(out)


if __name__ == '__main__':
    unittest.main()
