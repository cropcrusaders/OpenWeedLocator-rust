"""Integration tests for the Rust-Spray detector backend.

The real Rust-Spray binary is built from github.com/cropcrusaders/Rust-Spray
and isn't available in this repo's CI, so these tests exercise
RustSprayDetector against a stdlib-only fake binary that implements IPC
protocol v1 exactly as documented in Rust-Spray's INTEGRATION.md:

  stdin  <- [u32 width LE][u32 height LE][width*height*3 bytes RGB24]
  stdout -> {"v":1,"frame":N,"ts_us":T,"lanes":[bool,...],"latency_us":L}\n

This doubles as an executable specification of the protocol: if either side
changes the contract, these tests break.
"""

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.rustspray_detector import RustSprayDetector

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name == 'nt', reason='fake binary relies on a shebang (POSIX only)'),
]

NUM_LANES = 4
WIDTH, HEIGHT = 64, 48
LANE_WIDTH = WIDTH // NUM_LANES

# ExG = 2G - R - B. Brown soil: 2*90 - 120 - 50 = 10 (below threshold).
# Green weed: 2*200 - 40 - 40 = 320 (well above threshold).
BROWN_BGR = (50, 90, 120)
GREEN_BGR = (40, 200, 40)

FAKE_RUSTSPRAY = r'''#!/usr/bin/env python3
"""Stand-in for the Rust-Spray binary implementing IPC protocol v1.

Behaviour toggles (env vars, set by tests):
  FAKE_RUSTSPRAY_PROTOCOL   -- protocol version to report and emit (default 1)
  FAKE_RUSTSPRAY_HANG_AFTER -- frame index at which to stop responding
  FAKE_RUSTSPRAY_DIE_AFTER  -- frame index at which to exit(1) without responding
"""
import json
import os
import struct
import sys
import time

PROTOCOL = int(os.environ.get('FAKE_RUSTSPRAY_PROTOCOL', '1'))
HANG_AFTER = os.environ.get('FAKE_RUSTSPRAY_HANG_AFTER')
DIE_AFTER = os.environ.get('FAKE_RUSTSPRAY_DIE_AFTER')
NUM_LANES = 4
EXG_THRESHOLD = 20

if '--output-version' in sys.argv:
    print(json.dumps({'rustspray_version': '0.3.0-fake', 'ipc_protocol': PROTOCOL}))
    sys.exit(0)

if '--ipc-mode' not in sys.argv:
    sys.exit(2)

mock_gpio = '--mock-gpio' in sys.argv


def read_exact(stream, n):
    data = b''
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


frame_num = 0
prev_lanes = [False] * NUM_LANES
while True:
    header = read_exact(sys.stdin.buffer, 8)
    if header is None:
        sys.exit(0)  # clean shutdown on stdin EOF
    width, height = struct.unpack('<II', header)
    pixels = read_exact(sys.stdin.buffer, width * height * 3)
    if pixels is None:
        sys.exit(1)

    if DIE_AFTER is not None and frame_num >= int(DIE_AFTER):
        sys.exit(1)
    if HANG_AFTER is not None and frame_num >= int(HANG_AFTER):
        time.sleep(3600)

    start = time.time()
    lane_width = width // NUM_LANES
    lanes = []
    for lane in range(NUM_LANES):
        acc = 0
        count = 0
        for y in range(0, height, 4):
            row = y * width * 3
            for x in range(lane * lane_width, (lane + 1) * lane_width, 4):
                i = row + x * 3
                r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
                acc += 2 * g - r - b
                count += 1
        lanes.append(acc / max(count, 1) > EXG_THRESHOLD)

    if mock_gpio:
        for lane, (prev, cur) in enumerate(zip(prev_lanes, lanes)):
            if prev != cur:
                state = 'ON' if cur else 'OFF'
                print('[MOCK GPIO] lane=%d state=%s' % (lane, state),
                      file=sys.stderr, flush=True)
    prev_lanes = lanes

    response = {
        'v': PROTOCOL,
        'frame': frame_num,
        'ts_us': int(time.time() * 1e6),
        'lanes': lanes,
        'latency_us': int((time.time() - start) * 1e6),
    }
    sys.stdout.write(json.dumps(response) + '\n')
    sys.stdout.flush()
    frame_num += 1
'''


def make_frame(green_lanes=(), width=WIDTH, height=HEIGHT):
    """Synthetic BGR frame: brown soil with whole lanes painted green."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = BROWN_BGR
    lane_width = width // NUM_LANES
    for lane in green_lanes:
        frame[:, lane * lane_width:(lane + 1) * lane_width] = GREEN_BGR
    return frame


@pytest.fixture
def fake_binary(tmp_path):
    binary = tmp_path / 'rustspray'
    binary.write_text(FAKE_RUSTSPRAY)
    binary.chmod(0o755)
    config = tmp_path / 'config.toml'
    config.write_text('# fake Rust-Spray config\n')
    return binary, config


@pytest.fixture
def detector_factory(fake_binary):
    """Create detectors against the fake binary; close them all on teardown."""
    created = []

    def factory(**kwargs):
        binary, config = fake_binary
        kwargs.setdefault('frame_timeout_s', 0.5)
        detector = RustSprayDetector(
            binary_path=str(binary),
            config_path=str(config),
            num_lanes=NUM_LANES,
            mock_gpio=True,
            **kwargs,
        )
        created.append(detector)
        return detector

    yield factory
    for detector in created:
        detector.close()


class TestStartupHandshake:
    def test_version_handshake_succeeds(self, detector_factory):
        detector = detector_factory()
        assert detector.backend_version == '0.3.0-fake'
        assert detector._health_check()

    def test_protocol_version_mismatch_raises(self, fake_binary, monkeypatch):
        monkeypatch.setenv('FAKE_RUSTSPRAY_PROTOCOL', '2')
        binary, config = fake_binary
        with pytest.raises(RuntimeError, match='protocol'):
            RustSprayDetector(str(binary), str(config), num_lanes=NUM_LANES)

    def test_missing_binary_raises(self, fake_binary):
        _, config = fake_binary
        with pytest.raises(RuntimeError, match='not found'):
            RustSprayDetector('/nonexistent/rustspray', str(config), num_lanes=NUM_LANES)


class TestDetection:
    def test_no_weed_gives_no_detections(self, detector_factory):
        detector = detector_factory()
        cnts, boxes, weed_centres, image_out = detector.inference(make_frame())
        assert cnts == []
        assert boxes == []
        assert weed_centres == []
        assert detector.last_lane_states == [False] * NUM_LANES

    def test_single_green_lane_maps_to_correct_relay(self, detector_factory):
        detector = detector_factory()
        _, boxes, weed_centres, _ = detector.inference(make_frame(green_lanes=(2,)))

        assert detector.last_lane_states == [False, False, True, False]
        assert len(boxes) == 1 and len(weed_centres) == 1

        # Centre must map back to relay 2 via owl.py's mapping:
        # relay_id = int(centre_x / lane_width), gated on centre_y >= threshold
        centre_x, centre_y = weed_centres[0]
        assert int(centre_x / LANE_WIDTH) == 2
        assert centre_y == HEIGHT - 1  # bottom-anchored: always inside actuation zone

        x, y, w, h = boxes[0]
        assert x == 2 * LANE_WIDTH and w == LANE_WIDTH and h == HEIGHT

    def test_all_lanes_green(self, detector_factory):
        detector = detector_factory()
        _, boxes, weed_centres, _ = detector.inference(make_frame(green_lanes=range(NUM_LANES)))
        assert detector.last_lane_states == [True] * NUM_LANES
        relay_ids = sorted(int(cx / LANE_WIDTH) for cx, _ in weed_centres)
        assert relay_ids == list(range(NUM_LANES))

    def test_response_metadata_recorded(self, detector_factory):
        detector = detector_factory()
        detector.inference(make_frame(green_lanes=(0,)))
        assert detector.last_latency_us is not None and detector.last_latency_us >= 0

    def test_ignores_greenonbrown_kwargs(self, detector_factory):
        """owl.py's GoB call signature must be accepted (thresholds live in
        Rust-Spray's TOML, so the kwargs are ignored)."""
        detector = detector_factory()
        _, boxes, _, _ = detector.inference(
            make_frame(green_lanes=(1,)),
            exg_min=25, exg_max=200, hue_min=39, hue_max=83,
            saturation_min=50, saturation_max=220,
            brightness_min=60, brightness_max=190,
            show_display=False, algorithm='rustspray',
            min_detection_area=10, invert_hue=False, label='WEED')
        assert len(boxes) == 1

    def test_detect_convenience_api(self, detector_factory):
        detector = detector_factory()
        boxes, annotated, lane_states = detector.detect(make_frame(green_lanes=(3,)))
        assert lane_states == [False, False, False, True]
        assert len(boxes) == 1
        assert annotated.shape == (HEIGHT, WIDTH, 3)

    def test_mock_gpio_logs_state_changes(self, detector_factory):
        detector = detector_factory()
        detector.inference(make_frame(green_lanes=(1,)))
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any('[MOCK GPIO] lane=1 state=ON' in line for line in detector._stderr_tail):
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"No mock GPIO line seen on stderr: {list(detector._stderr_tail)}")


class TestRestartAndFallback:
    def test_recovers_when_subprocess_hangs(self, detector_factory, monkeypatch):
        monkeypatch.setenv('FAKE_RUSTSPRAY_HANG_AFTER', '2')
        detector = detector_factory()

        detector.inference(make_frame())                # frame 0 — ok
        detector.inference(make_frame())                # frame 1 — ok
        # frame 2 hangs -> timeout -> transparent restart -> resent frame succeeds
        _, boxes, _, _ = detector.inference(make_frame(green_lanes=(0,)))
        assert detector.last_lane_states[0] is True
        assert detector._restart_count == 1

    def test_recovers_when_subprocess_dies_mid_run(self, detector_factory, monkeypatch):
        monkeypatch.setenv('FAKE_RUSTSPRAY_DIE_AFTER', '1')
        detector = detector_factory()

        detector.inference(make_frame())                # frame 0 — ok
        # frame 1: subprocess exits(1) after reading the frame, before replying
        _, _, weed_centres, _ = detector.inference(make_frame(green_lanes=(2,)))
        assert len(weed_centres) == 1
        assert detector._restart_count == 1

    def test_recovers_when_killed_externally(self, detector_factory):
        detector = detector_factory()
        detector.inference(make_frame())

        detector._process.kill()
        detector._process.wait()

        _, _, weed_centres, _ = detector.inference(make_frame(green_lanes=(1,)))
        assert len(weed_centres) == 1
        assert detector._restart_count == 1

    def test_raises_runtime_error_after_max_restarts(self, detector_factory, monkeypatch):
        """After max_restarts the detector must raise RuntimeError so owl.py
        falls back to the Python ExG detector."""
        monkeypatch.setenv('FAKE_RUSTSPRAY_DIE_AFTER', '0')  # dies on every frame
        detector = detector_factory(max_restarts=2)

        with pytest.raises(RuntimeError, match='after 2 restart'):
            detector.inference(make_frame())


class TestShutdown:
    def test_close_terminates_subprocess(self, detector_factory):
        detector = detector_factory()
        process = detector._process
        detector.close()

        assert process.poll() is not None
        assert detector._process is None
        assert not detector._health_check()

    def test_close_is_idempotent(self, detector_factory):
        detector = detector_factory()
        detector.close()
        detector.close()

    def test_inference_after_close_raises(self, detector_factory):
        detector = detector_factory()
        detector.close()
        with pytest.raises(RuntimeError, match='closed'):
            detector.inference(make_frame())
