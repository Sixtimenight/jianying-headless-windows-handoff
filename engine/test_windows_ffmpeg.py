"""Portable Windows build/export integration and fail-closed checks."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
import ffmpeg_graph
import windows_export
import windows_portable as portable

TEST_WORK = ROOT / 'work/windows-ffmpeg-tests'
TEST_WORK.mkdir(parents=True, exist_ok=True)


class WindowsFfmpegSemanticsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required')
    def test_public_ig_export_retains_requested_audio_gain(self):
        job = Path(tempfile.mkdtemp(prefix='ig-audio-', dir=TEST_WORK))
        source = ROOT / 'docs/media/hypit-original-preview.mp4'
        plan = {'schema': 'jy14-headless-plan/v1', 'name': 'public-ig-audio-gain',
                'canvas': {'width': 540, 'height': 960, 'fps': 30},
                'tracks': [{'type': 'video', 'name': 'main', 'segments': [{
                    'source': str(source), 'start_us': 0, 'duration_us': 2_000_000,
                    'source_start_us': 0, 'source_duration_us': 2_000_000,
                    'speed': 1, 'volume': .5}]}]}
        plan_path = job / 'plan.json'
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        build = job / 'build'
        portable.build(plan_path, build)
        result = windows_export.run(build, job / 'export')
        self.assertTrue(result['full_decode_passed'])

        def mean_db(path):
            output = subprocess.run(
                ['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-t', '2',
                 '-vn', '-af', 'volumedetect', '-f', 'null', '-'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=120, check=True)
            matches = re.findall(r'mean_volume: ([+-]?[\d.]+) dB', output.stderr)
            self.assertTrue(matches, 'public IG audio must be measurable')
            return float(matches[-1])

        measured = mean_db(job / 'export/render.mp4') - mean_db(source)
        self.assertAlmostEqual(measured, 20 * math.log10(.5), delta=1.0)

    def test_public_ig_audio_gain_is_not_normalized_by_silence(self):
        source = ROOT / 'docs/media/hypit-original-preview.mp4'
        timeline = {
            'canvas_config': {'width': 540, 'height': 960}, 'fps': 30,
            'duration': 2_000_000,
            'materials': {'videos': [{'id': 'ig', 'path': str(source), 'has_audio': True}]},
            'tracks': [{'type': 'video', 'segments': [{
                'material_id': 'ig', 'source_timerange': {'start': 0, 'duration': 2_000_000},
                'target_timerange': {'start': 0, 'duration': 2_000_000},
                'volume': .5}]}],
        }
        _, graph, _, audio, _ = ffmpeg_graph.build(timeline, ROOT)
        self.assertEqual(audio, '[aout]')
        self.assertIn('volume=0.500000', graph)
        self.assertIn('amix=inputs=2:duration=longest:dropout_transition=0:normalize=0', graph)

    def test_aligned_export_rejects_one_missing_or_extra_frame(self):
        job = Path(tempfile.mkdtemp(prefix='probe-', dir=TEST_WORK))
        expected = {'canvas_config': {'width': 320, 'height': 240}, 'fps': 25,
                    'duration': 2_000_000, 'tracks': []}
        media = {'streams': [{'codec_type': 'video', 'codec_name': 'h264',
                              'pix_fmt': 'yuv420p', 'width': 320, 'height': 240,
                              'avg_frame_rate': '25/1', 'nb_read_frames': '50'}],
                 'format': {'duration': '2.0'}}
        for count in (49, 51):
            with self.subTest(count=count):
                media['streams'][0]['nb_read_frames'] = str(count)
                response = subprocess.CompletedProcess([], 0, json.dumps(media), '')
                with patch.object(windows_export.subprocess, 'run', return_value=response):
                    with self.assertRaisesRegex(ValueError, 'frame count'):
                        windows_export.probe('ffprobe', Path('render.mp4'), job, expected, 5)
        media['streams'][0]['nb_read_frames'] = '50'
        response = subprocess.CompletedProcess([], 0, json.dumps(media), '')
        with patch.object(windows_export.subprocess, 'run', return_value=response):
            windows_export.probe('ffprobe', Path('render.mp4'), job, expected, 5)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required')
class WindowsFfmpegTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='windows-ffmpeg-', dir=TEST_WORK))
        self.original_project_root = portable.ROOT
        portable.ROOT = self.temp / 'project'
        (portable.ROOT / 'work').mkdir(parents=True)
        self.root = portable.ROOT / 'work' / 'case with spaces'
        self.root.mkdir()
        self.source = self.root / '中文 source with spaces.mp4'
        self.silent = self.root / 'silent source.mp4'
        shutil.copyfile(ROOT / 'docs/media/hypit-original-preview.mp4', self.source)
        # A muted derivative of the same approved IG clip covers the gap case.
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-t', '1',
                        '-an', '-c:v', 'copy', '-y', str(self.silent)],
                       check=True)
        self.font = next((Path(item) for item in
                          (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf',
                           r'C:\Windows\Fonts\arial.ttf',
                           '/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
                          if Path(item).is_file()), None)
        self.plan = self.root / 'plan.json'
        value = {'schema': 'jy14-headless-plan/v1', 'name': 'portable-test',
                 'canvas': {'width': 320, 'height': 240, 'fps': 25},
                 'tracks': [
                    {'type': 'video', 'name': 'main', 'segments': [
                        {'source': str(self.source), 'start_us': 0, 'duration_us': 800_000,
                         'source_start_us': 0, 'source_duration_us': 800_000,
                         'speed': 1, 'volume': .5},
                        {'source': str(self.silent), 'start_us': 1_200_000,
                         'duration_us': 800_000, 'source_start_us': 0,
                         'source_duration_us': 800_000}]},
                    {'type': 'text', 'name': 'captions', 'segments': [
                        {'text': '第一条字幕', 'start_us': 100_000, 'duration_us': 700_000}]},
                    {'type': 'text', 'name': 'overlapping captions', 'segments': [
                        {'text': '重叠字幕', 'start_us': 500_000, 'duration_us': 700_000}]}
                 ]}
        self.plan.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        self.build = self.root / 'build'
        portable.build(self.plan, self.build)

    def record(self):
        return portable.read_json(self.build / 'build.json')

    def rewrite_record_for_timeline(self):
        record = self.record()
        timeline = self.build / 'render' / portable.TIMELINE_NAME
        record['timeline_sha256'] = portable.digest(timeline)
        record['files'] = portable.files_manifest(self.build / 'render')
        (self.build / 'build.json').write_bytes(portable.packed(record))

    def test_snapshot_binds_timeline_and_resources(self):
        portable.verify_build(self.build)
        timeline = self.build / 'render' / portable.TIMELINE_NAME
        raw = timeline.read_bytes()
        changed = raw.replace(b'"volume":0.5', b'"volume":0.6', 1)
        self.assertEqual(len(changed), len(raw))
        timeline.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'snapshot changed|timeline changed'):
            portable.verify_build(self.build)

    def test_external_media_path_is_rejected_even_with_updated_record(self):
        timeline_path = self.build / 'render' / portable.TIMELINE_NAME
        timeline = portable.read_json(timeline_path)
        timeline['materials']['videos'][0]['path'] = str(self.source)
        timeline_path.write_bytes(portable.packed(timeline))
        self.rewrite_record_for_timeline()
        with self.assertRaisesRegex(ValueError, 'Unsafe media dependency path'):
            portable.verify_build(self.build)

    def test_changed_and_missing_resources_are_rejected(self):
        _, record, timeline = portable.verify_build(self.build)
        relative = timeline['materials']['videos'][0]['path']
        path = self.build / 'render' / relative
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            portable.verify_build(self.build)
        path.rename(path.with_name(path.name + '.retained'))
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            portable.verify_build(self.build)

    def test_caption_graph_is_one_connected_chain(self):
        _, record, timeline = portable.verify_build(self.build)
        job = self.root / 'graph-job'
        job.mkdir()
        staged, _ = windows_export.stage_timeline(timeline, self.build, record, job)
        _, graph, video, _, _ = ffmpeg_graph.build(staged, job, self.font)
        self.assertIn('[vtext0]drawtext=', graph)
        self.assertEqual(video, '[vout]')
        self.assertIn('color=c=black', graph)
        self.assertIn('adelay=0:all=1', graph)

    def test_render_gap_captions_audio_and_full_decode(self):
        if self.font is None:
            self.skipTest('A local test font is required')
        filters = subprocess.run(['ffmpeg', '-hide_banner', '-filters'],
                                 capture_output=True, text=True, check=True)
        if ' drawtext ' not in filters.stdout:
            self.skipTest('The local FFmpeg build lacks drawtext')
        try:
            result = windows_export.run(self.build, self.root / 'export', font=self.font)
        except Exception as error:
            log = self.root / 'export' / 'ffmpeg.stderr.log'
            graph = self.root / 'export' / 'filter_complex.txt'
            self.fail(str(error) + '\n' + (log.read_text(encoding='utf-8', errors='replace')
                                           if log.is_file() else 'FFmpeg log is missing') + '\nGRAPH:\n' +
                      (graph.read_text(encoding='utf-8', errors='replace')
                       if graph.is_file() else 'Graph is missing'))
        self.assertEqual(result['schema'], 'jy14-ffmpeg-export/v2')
        self.assertTrue(result['full_decode_passed'])
        self.assertTrue(result['source_build_unchanged'])
        self.assertEqual(result['media']['streams'][0]['width'], 320)
        self.assertEqual(result['media']['streams'][0]['height'], 240)
        self.assertEqual(Path(result['output']).name, 'render.mp4')
        graph = (self.root / 'export' / 'filter_complex.txt').read_text(encoding='utf-8')
        self.assertIn('[vtext0]drawtext=', graph)
        self.assertIn('adelay=0:all=1', graph)

    def test_unsupported_native_track_is_rejected(self):
        plan = json.loads(self.plan.read_text(encoding='utf-8'))
        plan['tracks'].append({'type': 'effect', 'segments': [
            {'name': 'light-shake', 'start_us': 0, 'duration_us': 100_000}]})
        bad = self.root / 'bad-plan.json'
        bad.write_text(json.dumps(plan), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'supports only'):
            portable.build(bad, self.root / 'bad-build')

    def tearDown(self):
        portable.ROOT = self.original_project_root


if __name__ == '__main__':
    unittest.main()
