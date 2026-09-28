#!/usr/bin/env python3
"""Generate a local motion fixture or measure a completed native export.

Only Python's standard library, ffmpeg and ffprobe are required. This tool never
calls the native engine, registers a draft, or makes a network request.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FPS = 30
FRAME_COUNT = 60
DURATION_US = 2_000_000
WIDTH, HEIGHT = 640, 360
CYAN = {"red_max": 80, "green_min": 150, "blue_min": 150}
Y_TOLERANCE_PX = 1.0
TIME_TOLERANCE_SECONDS = 0.00001


def binary(name):
    path = shutil.which(name)
    if not path:
        raise ValueError(f"Required executable is not on PATH: {name}")
    return path


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def version(program):
    return run([program, "-version"]).splitlines()[0]


def write_json(path, value):
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def probe(video, ffprobe):
    command = [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
               "-show_frames", "-show_streams", "-show_entries",
               "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,duration,nb_read_frames:"
               "frame=best_effort_timestamp_time,pkt_duration_time,width,height",
               "-of", "json", str(video)]
    return json.loads(run(command)), command


def generate(out):
    ffmpeg, ffprobe = binary("ffmpeg"), binary("ffprobe")
    out.mkdir(parents=True, exist_ok=False)
    source = out / "cyan-source.mp4"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
               "-f", "lavfi", "-i", "color=c=cyan:s=128x72:r=30:d=2",
               "-frames:v", str(FRAME_COUNT), "-an", "-c:v", "libx264",
               "-preset", "veryslow", "-crf", "12", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(source)]
    run(command)
    metadata, probe_command = probe(source, ffprobe)
    stream = metadata["streams"][0]
    if (stream["codec_name"], stream["width"], stream["height"],
            int(stream["nb_read_frames"]), Fraction(stream["avg_frame_rate"])) != (
            "h264", 128, 72, FRAME_COUNT, FPS):
        raise ValueError("Generated source does not match the fixed fixture specification")
    plan = {
        "schema": "jy14-headless-plan/v1",
        "name": "position115-a-x-only",
        "canvas": {"width": WIDTH, "height": HEIGHT, "fps": FPS},
        "tracks": [{"type": "video", "name": "cyan-motion", "segments": [{
            "source": str(source), "start_us": 0, "duration_us": DURATION_US,
            "source_start_us": 0, "source_duration_us": DURATION_US,
            "speed": 1, "volume": 0, "scale": 0.2, "x": -0.7, "y": 0.4,
            "keyframes": {"x": [{"at_us": 0, "value": -0.7},
                                {"at_us": DURATION_US, "value": 0.7}]},
        }]}],
    }
    control = deepcopy(plan)
    control["name"] = "position115-b-explicit-y"
    control["tracks"][0]["segments"][0]["keyframes"]["y"] = [
        {"at_us": 0, "value": 0.4}, {"at_us": DURATION_US, "value": 0.4}]
    write_json(out / "a-x-only.plan.json", plan)
    write_json(out / "b-explicit-y.plan.json", control)
    portable_plan = deepcopy(plan)
    portable_plan["tracks"][0]["segments"][0]["source"] = source.name
    manifest = {
        "schema": "jy115-position-fixture/v1",
        "source": {"path_relative_to_manifest": source.name, "sha256": digest(source),
                   "width": 128, "height": 72, "fps": FPS, "frames": FRAME_COUNT,
                   "duration_us": DURATION_US, "color": "cyan", "codec": "h264"},
        "portable_a_spec": portable_plan,
        "control_change": {"name": control["name"], "constant_y_keyframes":
                           control["tracks"][0]["segments"][0]["keyframes"]["y"]},
        "background": "Uncovered native canvas; expected black; no extra background track",
        "expected_export": {"width": WIDTH, "height": HEIGHT, "fps": FPS,
                            "frames": FRAME_COUNT, "duration_us": DURATION_US},
        "measurement": {"cyan_rgb_threshold_inclusive": CYAN,
                        "y_span_max_px": Y_TOLERANCE_PX,
                        "x_rule": "Every center delta >= 0; total motion > 1 pixel",
                        "time_tolerance_seconds": TIME_TOLERANCE_SECONDS},
        "generation_command": command, "probe_command": probe_command,
        "source_probe": metadata,
        "tools": {"ffmpeg": version(ffmpeg), "ffprobe": version(ffprobe)},
        "native_called": False, "draft_registered": False, "network_called": False,
    }
    write_json(out / "fixture.json", manifest)
    return {"status": "generated", "out": str(out), "source_sha256": digest(source),
            "plans": ["a-x-only.plan.json", "b-explicit-y.plan.json"]}


def cyan_box(raw, width, height):
    x_min, y_min, x_max, y_max, count = width, height, -1, -1, 0
    for index, (red, green, blue) in enumerate(zip(raw[0::3], raw[1::3], raw[2::3])):
        if red <= CYAN["red_max"] and green >= CYAN["green_min"] and blue >= CYAN["blue_min"]:
            y, x = divmod(index, width)
            x_min, x_max = min(x_min, x), max(x_max, x)
            y_min, y_max = min(y_min, y), max(y_max, y)
            count += 1
    if not count:
        return {"cyan_pixels": 0, "bbox": None, "center_x": None, "center_y": None}
    return {"cyan_pixels": count, "bbox": [x_min, y_min, x_max, y_max],
            "center_x": (x_min + x_max) / 2, "center_y": (y_min + y_max) / 2}


def read_frame(pipe, size):
    chunks, remaining = [], size
    while remaining:
        chunk = pipe.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def decode_boxes(video, ffmpeg, width, height):
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
               "-noautorotate", "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
               "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    frames, partial_bytes = [], 0
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as process:
            while True:
                raw = read_frame(process.stdout, width * height * 3)
                if not raw:
                    break
                if len(raw) != width * height * 3:
                    partial_bytes = len(raw)
                    break
                frames.append({"index": len(frames), **cyan_box(raw, width, height)})
            code = process.wait()
        errors.seek(0)
        stderr = errors.read().decode("utf-8", errors="replace")
    return frames, {"command": command, "returncode": code, "stderr": stderr,
                    "partial_frame_bytes": partial_bytes}


def spread(values):
    if not values:
        return None
    return {"min": min(values), "max": max(values), "span": max(values) - min(values),
            "mean": sum(values) / len(values)}


def frame_rate_checks(stream):
    # Native exports quantize total duration to microseconds. Derive the only
    # allowed average-FPS interval from the existing duration tolerance; nominal
    # FPS remains exact. Fraction avoids adding an unrelated float epsilon.
    duration = Fraction(DURATION_US, 1_000_000)
    tolerance = Fraction(str(TIME_TOLERANCE_SECONDS))
    average = Fraction(stream.get("avg_frame_rate", "0/1"))
    lower = FRAME_COUNT / (duration + tolerance)
    upper = FRAME_COUNT / (duration - tolerance)
    return {
        "nominal_fps_30": Fraction(stream.get("r_frame_rate", "0/1")) == FPS,
        "average_fps_matches_duration_within_time_tolerance": lower <= average <= upper,
    }


def measure(video):
    ffmpeg, ffprobe = binary("ffmpeg"), binary("ffprobe")
    if not video.is_file():
        raise ValueError("Input video must be an existing regular file")
    metadata, probe_command = probe(video, ffprobe)
    if len(metadata.get("streams", [])) != 1:
        raise ValueError("ffprobe did not identify the first video stream")
    stream = metadata["streams"][0]
    width, height = stream["width"], stream["height"]
    if not (0 < width <= 8192 and 0 < height <= 8192):
        raise ValueError("Unsupported decoded frame dimensions")
    frames, decoding = decode_boxes(video, ffmpeg, width, height)
    probe_frames = metadata.get("frames", [])
    for frame, native_frame in zip(frames, probe_frames):
        frame["timestamp_seconds"] = native_frame.get("best_effort_timestamp_time")
    missing = [frame["index"] for frame in frames if not frame["cyan_pixels"]]
    xs = [frame["center_x"] for frame in frames if frame["cyan_pixels"]]
    ys = [frame["center_y"] for frame in frames if frame["cyan_pixels"]]
    all_colored = bool(frames) and not missing
    x_deltas = [current - previous for previous, current in zip(xs, xs[1:])] if all_colored else []
    y_deltas = [current - previous for previous, current in zip(ys, ys[1:])] if all_colored else []
    even, odd = (spread(ys[0::2]), spread(ys[1::2])) if all_colored else (None, None)
    two_levels = bool(len(ys) >= 4 and all_colored and
                      even["span"] <= Y_TOLERANCE_PX and odd["span"] <= Y_TOLERANCE_PX and
                      abs(even["mean"] - odd["mean"]) > Y_TOLERANCE_PX)
    reversals = sum(first * second < 0 and abs(first) > Y_TOLERANCE_PX and
                    abs(second) > Y_TOLERANCE_PX for first, second in zip(y_deltas, y_deltas[1:]))
    timestamps = [frame.get("best_effort_timestamp_time") for frame in probe_frames]
    timing_ok = (len(timestamps) == FRAME_COUNT and all(value is not None for value in timestamps) and
                 all(abs(float(value) - index / FPS) <= TIME_TOLERANCE_SECONDS
                     for index, value in enumerate(timestamps)))
    y_stats = spread(ys)
    checks = {
        "decode_completed_without_errors": decoding["returncode"] == 0 and not decoding["stderr"]
                                           and decoding["partial_frame_bytes"] == 0,
        "dimensions_640x360": (width, height) == (WIDTH, HEIGHT),
        **frame_rate_checks(stream),
        "ffprobe_read_60_frames": int(stream.get("nb_read_frames", -1)) == FRAME_COUNT,
        "decoded_exactly_60_frames": len(frames) == FRAME_COUNT,
        "frame_dimensions_consistent": all((frame.get("width"), frame.get("height")) == (width, height)
                                           for frame in probe_frames),
        "timestamps_match_30fps_from_zero": timing_ok,
        "cyan_present_in_every_frame": all_colored,
        "x_monotonic_nondecreasing": bool(x_deltas) and min(x_deltas) >= 0,
        "x_moves_more_than_one_pixel": bool(xs) and xs[-1] - xs[0] > 1,
        "y_span_at_most_one_pixel": all_colored and y_stats["span"] <= Y_TOLERANCE_PX,
    }
    status = "stable" if all(checks.values()) else "failed"
    failures = [key for key, passed in checks.items() if not passed]
    return {
        "schema": "jy115-position-measurement/v1", "status": status,
        "video": str(video), "video_sha256": digest(video), "video_bytes": video.stat().st_size,
        "checks": checks, "failed_checks": failures,
        "thresholds": {"cyan_rgb_threshold_inclusive": CYAN, "y_span_max_px": Y_TOLERANCE_PX,
                       "x_negative_delta_allowed_px": 0, "time_tolerance_seconds": TIME_TOLERANCE_SECONDS,
                       "average_fps_rule": "60 / (2 + time_tolerance) <= avg_fps <= 60 / (2 - time_tolerance)"},
        "decoded_frames": len(frames), "frames_without_cyan": missing,
        "x": {"centers": spread(xs), "step_deltas": spread(x_deltas)},
        "y": {"centers": y_stats, "even_frame_centers": even, "odd_frame_centers": odd,
              "two_level_alternation": two_levels, "alternating_step_reversals": reversals,
              "step_reversal_opportunities": max(0, len(y_deltas) - 1)},
        "frames": frames, "ffprobe": metadata, "ffprobe_command": probe_command,
        "decode": decoding, "tools": {"ffmpeg": version(ffmpeg), "ffprobe": version(ffprobe)},
        "scope": "Pixel and timing measurement of the supplied export; no native fix certification",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generator = commands.add_parser("generate", help="Create fresh local source, A/B plans and manifest")
    generator.add_argument("--out", type=Path, required=True, help="New directory; must not exist")
    measurement = commands.add_parser("measure", help="Decode all frames and write fixed-threshold measurements")
    measurement.add_argument("--video", type=Path, required=True)
    measurement.add_argument("--out", type=Path, required=True, help="New JSON file; parent directory must exist")
    args = parser.parse_args()
    out = args.out.expanduser().resolve()
    if out.exists():
        parser.error("--out must not already exist")
    try:
        if args.command == "generate":
            print(json.dumps(generate(out), ensure_ascii=False))
            return 0
        if not out.parent.is_dir():
            parser.error("The parent directory of --out must already exist")
        try:
            result = measure(args.video.expanduser().resolve())
        except (OSError, ValueError, KeyError, ZeroDivisionError, subprocess.CalledProcessError) as error:
            result = {"schema": "jy115-position-measurement/v1", "status": "error", "error": str(error)}
            if isinstance(error, subprocess.CalledProcessError):
                result["stderr"] = error.stderr
        write_json(out, result)
        print(json.dumps({"status": result["status"], "report": str(out),
                          "failed_checks": result.get("failed_checks", []),
                          "error": result.get("error")}, ensure_ascii=False))
        return {"stable": 0, "failed": 1, "error": 2}[result["status"]]
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
