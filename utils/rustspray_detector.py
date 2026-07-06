#!/usr/bin/env python
"""Wraps the Rust-Spray binary as a high-performance detection inner loop.

Rust-Spray (github.com/cropcrusaders/Rust-Spray) performs SIMD-accelerated
colour-based weed detection (ExG / green-ratio / chroma) and can drive the
solenoid GPIO pins itself. OWL remains the outer shell: it owns frame capture
(picamera2), scheduling, logging, the dashboard and config management, and
streams frames to the Rust-Spray subprocess over stdin.

IPC protocol v1 (see INTEGRATION.md in the Rust-Spray repository):
  stdin  <- [u32 width LE][u32 height LE][width*height*3 bytes RGB24]
  stdout -> {"v":1,"frame":N,"ts_us":T,"lanes":[bool,...],"latency_us":L}\n

If the subprocess dies or times out it is restarted up to ``max_restarts``
times; after that ``inference()`` raises RuntimeError so the caller (owl.py)
can fall back to the Python ExG detector.

GPIO ownership: unless ``mock_gpio`` is set, Rust-Spray applies the lane
states to its own (BCM-numbered) pins *before* each response is written —
the JSON is a report of what was actuated, not a request. owl.py therefore
suppresses its own relay actuation when this detector owns the GPIO; with
``mock_gpio = True`` (OWL's shipped default) Rust-Spray only detects and
OWL's relay controller keeps exclusive control of the pins.
"""
import atexit
import json
import logging
import queue
import struct
import subprocess
import threading
from collections import deque

import numpy as np

try:
    import cv2
except ImportError:  # standalone use outside OWL — numpy fallback below
    cv2 = None

logger = logging.getLogger(__name__)


class RustSprayIPCError(Exception):
    """Transient IPC failure (timeout, dead subprocess, bad response).

    Handled internally by restarting the subprocess — never escapes
    ``inference()``. Permanent failures raise RuntimeError instead.
    """


class RustSprayDetector:
    """Duck-typed OWL detector backed by the Rust-Spray subprocess.

    Satisfies the same interface as GreenOnBrown: ``inference(image, ...)``
    returning ``(contours, boxes, weed_centres, image_out)``. Rust-Spray
    reports per-lane spray decisions rather than pixel contours, so each
    active lane is converted to one synthetic detection: a box spanning the
    lane and a centre anchored at the bottom of the frame. Bottom-anchoring
    guarantees the centre passes owl.py's actuation-zone gate
    (``centre[1] >= actuation_y_thresh``), because Rust-Spray has already
    decided that lane should fire.
    """

    PROTOCOL_VERSION = 1
    STARTUP_TIMEOUT_S = 5.0
    FRAME_TIMEOUT_S = 0.10   # 100 ms — safe at up to 30 km/h
    MAX_RESTARTS = 3

    def __init__(self, binary_path: str, config_path: str,
                 num_lanes: int = 4, mock_gpio: bool = False,
                 frame_timeout_s: float = None, max_restarts: int = None,
                 startup_timeout_s: float = None):
        self.binary_path = str(binary_path)
        self.config_path = str(config_path)
        self.num_lanes = int(num_lanes)
        self.mock_gpio = bool(mock_gpio)
        self.frame_timeout_s = self.FRAME_TIMEOUT_S if frame_timeout_s is None else float(frame_timeout_s)
        self.max_restarts = self.MAX_RESTARTS if max_restarts is None else int(max_restarts)
        self.startup_timeout_s = self.STARTUP_TIMEOUT_S if startup_timeout_s is None else float(startup_timeout_s)

        self.backend_version = None
        self.last_lane_states = [False] * self.num_lanes
        self.last_latency_us = None

        self._process = None
        self._stdout_queue = None
        self._stderr_tail = deque(maxlen=20)
        self._lane_count_warned = False
        self._restart_count = 0
        self._frames_since_start = 0
        self._closed = False
        self._lock = threading.RLock()

        self._verify_protocol_version()
        self._start_process()
        # Belt-and-braces: never leave an orphaned subprocess holding GPIO
        atexit.register(self.close)

    # ------------------------------------------------------------------ #
    # OWL detector interface
    # ------------------------------------------------------------------ #

    def inference(self, image, show_display=False, label='WEED', **kwargs):
        """Drop-in replacement for GreenOnBrown.inference().

        Accepts (and ignores) the GreenOnBrown threshold kwargs — colour
        thresholds live in Rust-Spray's own TOML config.

        Returns (contours, boxes, weed_centres, image_out). Contours are
        always empty; boxes/centres are synthesised per active lane.
        """
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError(
                f"Expected an HxWx3 uint8 BGR frame, got shape {image.shape} "
                f"dtype {image.dtype}")

        height, width = image.shape[:2]
        # OWL frames are BGR (OpenCV); protocol v1 wants RGB24, R first.
        # cv2.cvtColor is ~4x faster than a numpy negative-stride copy —
        # at 1456x1088 this is the difference between ~15 ms and ~4 ms.
        if cv2 is not None:
            frame_rgb24 = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).tobytes()
        else:
            frame_rgb24 = np.ascontiguousarray(image[:, :, ::-1]).tobytes()

        response = self._send_frame_with_recovery(frame_rgb24, width, height)

        reported = response.get('lanes', [])
        if len(reported) != self.num_lanes and not self._lane_count_warned:
            self._lane_count_warned = True
            logger.warning(
                f"Rust-Spray reports {len(reported)} lanes but OWL expects "
                f"{self.num_lanes} (relay_num) — check [lanes] count in "
                f"{self.config_path}. Extra lanes are ignored, missing lanes "
                f"stay off.")
        lanes = [bool(state) for state in reported][:self.num_lanes]
        lanes += [False] * (self.num_lanes - len(lanes))
        self.last_lane_states = lanes
        self.last_latency_us = response.get('latency_us')

        boxes, weed_centres = self._lanes_to_detections(lanes, width, height)

        image_out = image
        if show_display and boxes:
            image_out = self._annotate(image, boxes, label)

        return [], boxes, weed_centres, image_out

    def detect(self, frame: np.ndarray, confidence: float = 0.5,
               filter_id: int = 0, **kwargs):
        """Convenience API per the integration spec.

        Returns (boxes, annotated_frame, lane_states).
        """
        _, boxes, _, annotated = self.inference(frame, show_display=True, **kwargs)
        return boxes, annotated, list(self.last_lane_states)

    def close(self) -> None:
        """Terminate the subprocess cleanly. Idempotent."""
        with self._lock:
            self._closed = True
            self._terminate_process()
        atexit.unregister(self.close)

    # ------------------------------------------------------------------ #
    # Subprocess lifecycle
    # ------------------------------------------------------------------ #

    def _verify_protocol_version(self) -> None:
        """Startup handshake: run `rustspray --output-version` and check
        the IPC protocol version before sending any frames."""
        try:
            result = subprocess.run(
                [self.binary_path, '--output-version'],
                capture_output=True, text=True, timeout=self.startup_timeout_s,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Rust-Spray binary not found: {self.binary_path}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Rust-Spray did not answer --output-version within "
                f"{self.startup_timeout_s}s: {self.binary_path}")
        except OSError as e:
            raise RuntimeError(f"Cannot execute Rust-Spray binary {self.binary_path}: {e}")

        try:
            info = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(
                f"Rust-Spray --output-version returned invalid JSON "
                f"(exit code {result.returncode}): {result.stdout!r}")

        protocol = info.get('ipc_protocol')
        if protocol != self.PROTOCOL_VERSION:
            raise RuntimeError(
                f"Rust-Spray IPC protocol mismatch: binary speaks v{protocol}, "
                f"this detector requires v{self.PROTOCOL_VERSION}. "
                f"Update Rust-Spray or OWL so the versions match.")

        self.backend_version = info.get('rustspray_version', 'unknown')
        logger.info(f"Rust-Spray {self.backend_version} (IPC protocol v{protocol}) at {self.binary_path}")

    def _start_process(self) -> None:
        """Spawn the rustspray subprocess. Called at init and on restart."""
        with self._lock:
            if self._closed:
                raise RuntimeError("RustSprayDetector is closed")

            cmd = [self.binary_path, '--ipc-mode', '--config', self.config_path]
            if self.mock_gpio:
                cmd.append('--mock-gpio')

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._stdout_queue = queue.Queue()
            self._frames_since_start = 0

            threading.Thread(
                target=self._read_stdout,
                args=(self._process, self._stdout_queue),
                daemon=True, name='rustspray-stdout').start()
            threading.Thread(
                target=self._read_stderr,
                args=(self._process,),
                daemon=True, name='rustspray-stderr').start()

            logger.info(f"Started Rust-Spray subprocess (pid {self._process.pid}): {' '.join(cmd)}")

    @staticmethod
    def _read_stdout(process, out_queue) -> None:
        """Daemon reader thread: enforces FRAME_TIMEOUT_S without blocking
        OWL's main loop (the loop waits on the queue with a timeout instead
        of blocking on the pipe). Puts None on EOF so a dead subprocess is
        detected immediately rather than after a full timeout."""
        try:
            for line in iter(process.stdout.readline, b''):
                out_queue.put(line)
        except (ValueError, OSError):
            pass  # pipe closed during shutdown
        out_queue.put(None)

    def _read_stderr(self, process) -> None:
        try:
            for raw in iter(process.stderr.readline, b''):
                line = raw.decode('utf-8', errors='replace').rstrip()
                if line:
                    self._stderr_tail.append(line)
                    logger.debug(f"[rustspray] {line}")
        except (ValueError, OSError):
            pass

    def _kill_process(self) -> None:
        """Kill immediately — used on the restart path where the subprocess
        is already broken or hung; waiting for a graceful EOF exit would
        stall recovery well past the per-frame budget. Rust-Spray drives all
        lanes off on every exit path, so this is safe."""
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.kill()
            process.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, OSError):
            pass

    def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            # closing stdin signals EOF — rustspray exits cleanly in IPC mode
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.terminate()
                process.wait(timeout=0.5)
            except (subprocess.TimeoutExpired, OSError):
                process.kill()
                try:
                    process.wait(timeout=0.5)
                except (subprocess.TimeoutExpired, OSError):
                    pass

    def _health_check(self) -> bool:
        """Return True if subprocess is alive and IPC protocol version matched."""
        with self._lock:
            return (not self._closed
                    and self.backend_version is not None
                    and self._process is not None
                    and self._process.poll() is None)

    # ------------------------------------------------------------------ #
    # IPC
    # ------------------------------------------------------------------ #

    def _send_frame(self, frame_rgb24: bytes, width: int, height: int) -> dict:
        """Write one frame to stdin, read the JSON response from stdout."""
        process = self._process
        if process is None or process.poll() is not None:
            raise RustSprayIPCError(self._death_message(process))

        header = struct.pack('<II', width, height)
        try:
            # header + pixels in a single write per the IPC contract
            process.stdin.write(header + frame_rgb24)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RustSprayIPCError(f"stdin write failed: {e}. {self._death_message(process)}")

        # Allow the (re)started binary time to initialise before the first
        # frame; steady-state frames get the strict per-frame budget.
        timeout = self.startup_timeout_s if self._frames_since_start == 0 else self.frame_timeout_s
        try:
            line = self._stdout_queue.get(timeout=timeout)
        except queue.Empty:
            raise RustSprayIPCError(
                f"no response within {timeout * 1000:.0f} ms "
                f"(frame {self._frames_since_start} since start)")
        if line is None:
            raise RustSprayIPCError(self._death_message(process))

        try:
            response = json.loads(line)
        except ValueError:
            raise RustSprayIPCError(f"invalid JSON response: {line!r}")

        version = response.get('v')
        if version != self.PROTOCOL_VERSION:
            raise RustSprayIPCError(
                f"response protocol v{version} != expected v{self.PROTOCOL_VERSION}")

        self._frames_since_start += 1
        return response

    def _send_frame_with_recovery(self, frame_rgb24: bytes, width: int, height: int) -> dict:
        """Send a frame, restarting the subprocess on failure.

        Raises RuntimeError once max_restarts is exhausted so owl.py can
        fall back to the Python ExG detector.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("RustSprayDetector is closed")
            while True:
                try:
                    return self._send_frame(frame_rgb24, width, height)
                except RustSprayIPCError as e:
                    if self._restart_count >= self.max_restarts:
                        self._kill_process()
                        raise RuntimeError(
                            f"Rust-Spray backend failed after {self._restart_count} "
                            f"restart(s): {e}")
                    self._restart_count += 1
                    logger.error(
                        f"Rust-Spray IPC failure: {e} — restarting subprocess "
                        f"({self._restart_count}/{self.max_restarts})")
                    self._kill_process()
                    self._start_process()

    def _death_message(self, process) -> str:
        exit_code = process.poll() if process is not None else None
        message = f"subprocess not running (exit code {exit_code})"
        if self._stderr_tail:
            message += f"; stderr tail: {' | '.join(list(self._stderr_tail)[-5:])}"
        return message

    # ------------------------------------------------------------------ #
    # Lane state -> OWL detections
    # ------------------------------------------------------------------ #

    def _lanes_to_detections(self, lanes, width, height):
        """Convert per-lane booleans into synthetic boxes/centres.

        Uses Rust-Spray's exact lane geometry (src/lanes.rs): base width
        ``width // num_lanes`` with the first ``width % num_lanes`` lanes one
        pixel wider. Each centre sits mid-strip, so owl.py's relay mapping
        (relay_id = int(centre_x / lane_width)) fires exactly that lane.
        """
        boxes = []
        weed_centres = []
        base_width, remainder = divmod(width, self.num_lanes)
        x = 0
        for i, active in enumerate(lanes):
            lane_width = base_width + (1 if i < remainder else 0)
            if active:
                boxes.append([x, 0, max(1, lane_width), height])
                weed_centres.append([x + lane_width // 2, height - 1])
            x += lane_width
        return boxes, weed_centres

    @staticmethod
    def _annotate(image, boxes, label):
        if cv2 is None:
            return image
        image_out = image.copy()
        for x, y, w, h in boxes:
            cv2.rectangle(image_out, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.putText(image_out, label, (x + 5, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 2)
        return image_out
