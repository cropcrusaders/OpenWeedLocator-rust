#!/usr/bin/env python
"""
Benchmark: detection lag — OWL Python detectors vs the Rust-Spray backend.

Measures per-frame detection latency (frame in -> lane/actuation decision out)
for OWL's original Python green-on-brown detectors (GreenOnBrown.inference)
and for the Rust-Spray subprocess backend (RustSprayDetector.inference,
IPC protocol v1). The Rust-Spray number is end-to-end as OWL experiences it:
BGR->RGB conversion + pipe transfer + SIMD detection + lane reduction + JSON
response. The binary's internally reported latency_us is shown separately so
the pipe overhead is visible.

Frames are synthetic but realistic: textured brown soil with randomly placed
green weed blobs (~2% coverage), identical for every detector.

Run:  python benchmarks/bench_rustspray_lag.py [--binary /path/to/rustspray]
                                               [--config /path/to/config.toml]
                                               [--frames 100] [--markdown]

The Rust-Spray rows are skipped (with a note) if no binary is found via
--binary, $RUSTSPRAY_BIN, or /usr/local/bin/rustspray.
"""
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.greenonbrown import GreenOnBrown  # noqa: E402

RESOLUTIONS = [(416, 320), (640, 480), (1280, 720), (1456, 1088)]
PYTHON_ALGORITHMS = ['exg', 'exhsv']
WARMUP = 10

# GreenOnBrown thresholds — OWL's GENERAL_CONFIG.ini defaults
GOB_KWARGS = dict(
    exg_min=25, exg_max=200, hue_min=39, hue_max=83,
    saturation_min=50, saturation_max=220,
    brightness_min=60, brightness_max=190,
    min_detection_area=10, invert_hue=False, show_display=False,
)


def make_field_frame(width, height, seed=42, num_weeds=12):
    """Textured brown soil with green weed blobs (~2% coverage), BGR uint8."""
    rng = np.random.RandomState(seed)
    noise = rng.randint(-18, 18, (height, width, 3)).astype(np.int16)
    frame = np.empty((height, width, 3), dtype=np.int16)
    frame[:] = (50, 90, 120)  # BGR soil
    frame = np.clip(frame + noise, 0, 255).astype(np.uint8)

    blob_r = max(4, int(0.02 * width))
    for _ in range(num_weeds):
        cx = rng.randint(blob_r, width - blob_r)
        cy = rng.randint(blob_r, height - blob_r)
        axes = (rng.randint(blob_r // 2, blob_r), rng.randint(blob_r // 2, blob_r))
        cv2.ellipse(frame, (cx, cy), axes, rng.randint(0, 180), 0, 360,
                    (40, 200, 40), -1)  # BGR green
    return frame


def measure(fn, frames):
    """Run fn over frames, return per-frame wall latency in ms."""
    for frame in frames[:WARMUP]:
        fn(frame)
    samples = []
    for frame in frames:
        start = time.perf_counter()
        fn(frame)
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def stats(samples):
    ordered = sorted(samples)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    return statistics.mean(samples), statistics.median(samples), p95


def find_binary(cli_path):
    for candidate in [cli_path, os.environ.get('RUSTSPRAY_BIN'), '/usr/local/bin/rustspray']:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary', default=None, help='path to the rustspray binary')
    ap.add_argument('--config', default=None, help='path to a rustspray TOML config')
    ap.add_argument('--frames', type=int, default=100, help='measured frames per case')
    ap.add_argument('--markdown', action='store_true', help='print a markdown table')
    args = ap.parse_args()

    binary = find_binary(args.binary)
    rust_detector_cls = None
    if binary:
        from utils.rustspray_detector import RustSprayDetector
        rust_detector_cls = RustSprayDetector
        config = args.config
        if config is None:
            config = str(PROJECT_ROOT / 'benchmarks' / '_rustspray_bench.toml')
            Path(config).write_text(
                '[lanes]\ncount = 4\non_threshold = 0.005\noff_threshold = 0.002\n'
                '[gpio]\nmock = true\n')
    else:
        print('NOTE: rustspray binary not found — Rust-Spray rows skipped.\n'
              '      Pass --binary or set $RUSTSPRAY_BIN to include them.\n')

    rows = []
    for width, height in RESOLUTIONS:
        frames = [make_field_frame(width, height, seed=s) for s in range(args.frames)]

        for algorithm in PYTHON_ALGORITHMS:
            detector = GreenOnBrown(algorithm=algorithm)
            samples = measure(
                lambda f, d=detector, a=algorithm: d.inference(f, algorithm=a, **GOB_KWARGS),
                frames)
            rows.append((f'{width}x{height}', f'python {algorithm}', *stats(samples), None))

        if rust_detector_cls:
            detector = rust_detector_cls(
                binary_path=binary, config_path=config,
                num_lanes=4, mock_gpio=True, frame_timeout_s=2.0)
            internal_us = []

            def rust_call(frame, d=detector, acc=internal_us):
                d.inference(frame)
                acc.append(d.last_latency_us)

            samples = measure(rust_call, frames)
            internal_ms = statistics.mean(internal_us[WARMUP:]) / 1000.0
            rows.append((f'{width}x{height}', 'rustspray (end-to-end IPC)',
                         *stats(samples), internal_ms))
            detector.close()

    header = ('Resolution', 'Detector', 'mean ms', 'median ms', 'p95 ms', 'rust-internal ms')
    if args.markdown:
        print('| ' + ' | '.join(header) + ' |')
        print('|' + '---|' * len(header))
        for res, name, mean, median, p95, internal in rows:
            internal_s = f'{internal:.2f}' if internal is not None else '—'
            print(f'| {res} | {name} | {mean:.2f} | {median:.2f} | {p95:.2f} | {internal_s} |')
    else:
        print(f"{header[0]:<12} {header[1]:<28} {'mean':>8} {'median':>8} {'p95':>8} {'internal':>9}")
        for res, name, mean, median, p95, internal in rows:
            internal_s = f'{internal:8.2f}' if internal is not None else '       —'
            print(f'{res:<12} {name:<28} {mean:8.2f} {median:8.2f} {p95:8.2f} {internal_s}')

    print(f'\n{args.frames} frames per case after {WARMUP} warmup frames; '
          'wall-clock per-frame latency, frame in -> actuation decision out.')


if __name__ == '__main__':
    main()
