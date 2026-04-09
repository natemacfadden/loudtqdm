"""
Audio engine for loudtqdm.

Generates square waves using only stdlib + CoreAudio (macOS) or aplay (Linux).
No pip dependencies.

Sound design: old-school PC speaker / ZX Spectrum tape loader.
- Continuous background tone sweeps from F_LOW to F_HIGH as progress advances
- Pure square waves, narrow duty cycle (thin, metallic, harsh)
- Low sample rate (11025 Hz) for extra digital grit
- No amplitude envelope — abrupt on/off, clicks are a feature
- Frequencies derived from the 8253 PIT formula: 1193180 / divisor

macOS: uses CoreAudio AudioQueue (ctypes) for gapless, zero-latency streaming.
Linux: falls back to aplay with a FIFO pipe.
"""

import array
import io
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

# ── Constants ─────────────────────────────────────────────────────────────────

SAMPLE_RATE    = 11025   # Hz — low rate for maximum grit
TONE_AMPLITUDE = 8000    # continuous tone sample values (out of 32767)
TONE_VOLUME    = 0.13    # AudioQueue gain: 0.0–1.0
JINGLE_AMP     = 10000   # jingle sample values
GAP_SEC        = 0.018   # silence between notes within a jingle burst
_SQUARE_DUTY   = 0.25    # square-wave duty cycle — narrow = thin/metallic

# CoreAudio buffer tuning (macOS path)
_CA_BUFFER_FRAMES  = 512   # ~46 ms per buffer
_CA_N_BUFFERS      = 3     # buffers in flight; 3 × 46 ms ≈ 138 ms latency
_KAQ_PARAM_VOLUME  = 1     # kAudioQueueParam_Volume

# ── PIT frequency formula: 1193180 / divisor ──────────────────────────────────

def _pit(divisor: int) -> float:
    return 1193180 / divisor

F_LOW  = _pit(19886)   # ~60 Hz
F_HIGH = _pit(6629)    # ~180 Hz

# ── Sample / WAV helpers ───────────────────────────────────────────────────────

def _square_samples(freq: float, duration: float, duty: float,
                    amplitude: int = TONE_AMPLITUDE) -> list:
    n = int(SAMPLE_RATE * duration)
    if freq == 0 or n == 0:
        return [0] * n
    period = SAMPLE_RATE / freq
    return [amplitude if (i % period) / period < duty else -amplitude
            for i in range(n)]


def _gap_samples() -> list:
    return [0] * int(SAMPLE_RATE * GAP_SEC)


def _build_wav(tone_specs: list, amplitude: int = TONE_AMPLITUDE) -> bytes:
    """tone_specs: [(freq, duration, duty), ...] → WAV bytes."""
    all_samples: list[int] = []
    for idx, (freq, dur, duty) in enumerate(tone_specs):
        if idx > 0:
            all_samples.extend(_gap_samples())
        all_samples.extend(_square_samples(freq, dur, duty, amplitude))
    raw = array.array("h", all_samples).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(raw)
    return buf.getvalue()


def _gen_square_chunk(phase: float, freq: float, n: int) -> tuple:
    """
    Generate n square-wave frames at the given frequency and phase offset.
    Returns (raw_bytes, updated_phase). Duty cycle uses module _SQUARE_DUTY.
    """
    period = SAMPLE_RATE / freq
    chunk  = [TONE_AMPLITUDE if ((phase + i) % period) / period < _SQUARE_DUTY
              else -TONE_AMPLITUDE
              for i in range(n)]
    return array.array("h", chunk).tobytes(), (phase + n) % period


# ── File-based playback (jingle fallback on Linux) ────────────────────────────

def _play_wav_async(wav_bytes: bytes, daemon: bool = True) -> threading.Thread:
    """Write WAV to a temp file and play it in a background thread."""

    def _run(path: str):
        try:
            if sys.platform == "darwin":
                subprocess.run(["afplay", path],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform.startswith("linux"):
                subprocess.run(["aplay", "-q", path],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        os.write(fd, wav_bytes)
    finally:
        os.close(fd)

    t = threading.Thread(target=_run, args=(path,), daemon=daemon)
    t.start()
    return t


# ── Jingles ───────────────────────────────────────────────────────────────────

def _build_jingle_samples() -> list:
    steps = [F_LOW + (F_HIGH - F_LOW) * t for t in (0.50, 0.65, 0.80, 1.0)]
    spec  = [(f, 0.09, _SQUARE_DUTY) for f in steps]
    spec.append((F_HIGH * 1.10, 0.65, 0.50))
    all_samples: list[int] = []
    for idx, (freq, dur, duty) in enumerate(spec):
        if idx > 0:
            all_samples.extend(_gap_samples())
        all_samples.extend(_square_samples(freq, dur, duty, JINGLE_AMP))
    return all_samples


_JINGLE_SAMPLES: list  = _build_jingle_samples()
_JINGLE_DUR: float     = len(_JINGLE_SAMPLES) / SAMPLE_RATE
_JINGLE_WAV: bytes     = _build_wav([], JINGLE_AMP)  # placeholder; overwritten below

# Build WAV from the pre-rendered samples directly
def _samples_to_wav(samples: list) -> bytes:
    raw = array.array("h", samples).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(raw)
    return buf.getvalue()

_JINGLE_WAV = _samples_to_wav(_JINGLE_SAMPLES)


# ── CoreAudio AudioQueue (macOS) ──────────────────────────────────────────────

def _ca_available() -> bool:
    return sys.platform == "darwin"


if _ca_available():
    import ctypes

    _AT = ctypes.CDLL(
        "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"
    )

    class _ASBD(ctypes.Structure):
        """AudioStreamBasicDescription"""
        _fields_ = [
            ("mSampleRate",          ctypes.c_double),
            ("mFormatID",            ctypes.c_uint32),
            ("mFormatFlags",         ctypes.c_uint32),
            ("mBytesPerPacket",      ctypes.c_uint32),
            ("mFramesPerPacket",     ctypes.c_uint32),
            ("mBytesPerFrame",       ctypes.c_uint32),
            ("mChannelsPerFrame",    ctypes.c_uint32),
            ("mBitsPerChannel",      ctypes.c_uint32),
            ("mReserved",            ctypes.c_uint32),
        ]

    class _AQBuffer(ctypes.Structure):
        """AudioQueueBuffer — layout matches the 64-bit macOS ABI."""
        _fields_ = [
            ("mAudioDataBytesCapacity",    ctypes.c_uint32),
            ("_pad0",                       ctypes.c_uint32),
            ("mAudioData",                 ctypes.c_void_p),
            ("mAudioDataByteSize",         ctypes.c_uint32),
            ("_pad1",                       ctypes.c_uint32),
            ("mUserData",                  ctypes.c_void_p),
            ("mPacketDescriptionCapacity", ctypes.c_uint32),
            ("_pad2",                       ctypes.c_uint32),
            ("mPacketDescriptions",        ctypes.c_void_p),
            ("mPacketDescriptionCount",    ctypes.c_uint32),
        ]

    _AQBufRef = ctypes.POINTER(_AQBuffer)

    _OutputCallback = ctypes.CFUNCTYPE(
        None,
        ctypes.c_void_p,
        ctypes.c_void_p,
        _AQBufRef,
    )

    _AT.AudioQueueNewOutput.restype  = ctypes.c_int32
    _AT.AudioQueueNewOutput.argtypes = [
        ctypes.POINTER(_ASBD), _OutputCallback,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
    ]
    _AT.AudioQueueAllocateBuffer.restype  = ctypes.c_int32
    _AT.AudioQueueAllocateBuffer.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_AQBufRef),
    ]
    _AT.AudioQueueEnqueueBuffer.restype  = ctypes.c_int32
    _AT.AudioQueueEnqueueBuffer.argtypes = [
        ctypes.c_void_p, _AQBufRef, ctypes.c_uint32, ctypes.c_void_p,
    ]
    _AT.AudioQueueSetParameter.restype  = ctypes.c_int32
    _AT.AudioQueueSetParameter.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_float,
    ]
    _AT.AudioQueueStart.restype    = ctypes.c_int32
    _AT.AudioQueueStart.argtypes   = [ctypes.c_void_p, ctypes.c_void_p]
    _AT.AudioQueueStop.restype     = ctypes.c_int32
    _AT.AudioQueueStop.argtypes    = [ctypes.c_void_p, ctypes.c_bool]
    _AT.AudioQueueDispose.restype  = ctypes.c_int32
    _AT.AudioQueueDispose.argtypes = [ctypes.c_void_p, ctypes.c_bool]

    def _make_asbd() -> "_ASBD":
        """Return a configured mono 16-bit signed-integer PCM ASBD."""
        asbd = _ASBD()
        asbd.mSampleRate       = float(SAMPLE_RATE)
        asbd.mFormatID         = 0x6C70636D   # 'lpcm'
        asbd.mFormatFlags      = 0x4 | 0x8   # signed int + packed
        asbd.mBytesPerPacket   = 2
        asbd.mFramesPerPacket  = 1
        asbd.mBytesPerFrame    = 2
        asbd.mChannelsPerFrame = 1
        asbd.mBitsPerChannel   = 16
        return asbd


# ── Jingle playback ───────────────────────────────────────────────────────────

def _play_jingle_ca() -> threading.Thread:
    """
    Play the jingle through CoreAudio — no subprocess, starts immediately.
    Runs in a non-daemon thread so Python waits for it on exit.
    """
    samples  = _JINGLE_SAMPLES
    n_bytes  = len(samples) * 2

    def _run():
        asbd  = _make_asbd()
        queue = ctypes.c_void_p()
        noop  = _OutputCallback(lambda ud, aq, buf: None)

        if _AT.AudioQueueNewOutput(
            ctypes.byref(asbd), noop, None, None, None, 0,
            ctypes.byref(queue),
        ):
            return

        buf_ref = _AQBufRef()
        _AT.AudioQueueAllocateBuffer(queue, n_bytes, ctypes.byref(buf_ref))
        ptr = ctypes.cast(buf_ref.contents.mAudioData,
                          ctypes.POINTER(ctypes.c_int16))
        for i, s in enumerate(samples):
            ptr[i] = s
        buf_ref.contents.mAudioDataByteSize = n_bytes
        _AT.AudioQueueEnqueueBuffer(queue, buf_ref, 0, None)
        _AT.AudioQueueStart(queue, None)

        time.sleep(_JINGLE_DUR + 0.1)
        _AT.AudioQueueStop(queue, True)
        _AT.AudioQueueDispose(queue, True)

    t = threading.Thread(target=_run, daemon=False)
    t.start()
    return t


def play_jingle() -> threading.Thread:
    """Fire completion jingle. CoreAudio on macOS, afplay on Linux."""
    if _ca_available():
        return _play_jingle_ca()
    return _play_wav_async(_JINGLE_WAV, daemon=False)


# ── ContinuousTone ────────────────────────────────────────────────────────────

_WOBBLE_MAX   = 10.0   # Hz — max excursion from center
_WOBBLE_STEP  = 1.2    # Hz — std dev of random nudge per buffer fill (~46 ms)
_WOBBLE_PULL  = 0.012  # mean-reversion strength per fill (correlation ~4 s)
_OFFSET_RANGE = 15.0   # Hz — fixed random offset baked in per run


class ContinuousTone:
    def __init__(self, reverse: bool = False):
        self._progress:   float = 0.0
        self._reverse     = reverse
        self._phase       = 0.0
        self._wobble      = 0.0
        self._freq_offset = random.uniform(-_OFFSET_RANGE, _OFFSET_RANGE)
        self._stop    = threading.Event()
        self._ready   = threading.Event()
        self._cb_ref  = None
        self._thread  = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        self._ready.wait()

    def update(self, progress: float):
        self._progress = max(0.0, min(1.0, progress))

    def stop(self):
        self._stop.set()

    def join(self, timeout: float = None):
        self._thread.join(timeout=timeout)

    def _freq(self) -> float:
        t = self._progress if not self._reverse else 1.0 - self._progress
        return max(20.0, F_LOW + (F_HIGH - F_LOW) * t
                   + self._freq_offset + self._wobble)

    def _step_wobble(self):
        self._wobble += -_WOBBLE_PULL * self._wobble + random.gauss(0, _WOBBLE_STEP)
        self._wobble  = max(-_WOBBLE_MAX, min(_WOBBLE_MAX, self._wobble))

    # ── CoreAudio path ────────────────────────────────────────────────────────

    def _ca_fill(self, queue, buf_ref):
        raw, self._phase = _gen_square_chunk(self._phase, self._freq(),
                                             _CA_BUFFER_FRAMES)
        ctypes.memmove(buf_ref.contents.mAudioData, raw, len(raw))
        buf_ref.contents.mAudioDataByteSize = len(raw)
        self._step_wobble()
        _AT.AudioQueueEnqueueBuffer(queue, buf_ref, 0, None)

    def _run_ca(self):
        asbd  = _make_asbd()
        queue = ctypes.c_void_p()

        def _callback(user_data, aq, buf_ref):
            if not self._stop.is_set():
                self._ca_fill(aq, buf_ref)

        cb = _OutputCallback(_callback)
        self._cb_ref = cb

        if _AT.AudioQueueNewOutput(
            ctypes.byref(asbd), cb, None, None, None, 0,
            ctypes.byref(queue),
        ):
            return

        buf_size = _CA_BUFFER_FRAMES * 2
        for _ in range(_CA_N_BUFFERS):
            buf_ref = _AQBufRef()
            _AT.AudioQueueAllocateBuffer(queue, buf_size, ctypes.byref(buf_ref))
            self._ca_fill(queue, buf_ref)

        _AT.AudioQueueSetParameter(queue, _KAQ_PARAM_VOLUME, TONE_VOLUME)
        _AT.AudioQueueStart(queue, None)
        self._ready.set()

        while not self._stop.is_set():
            time.sleep(0.02)

        _AT.AudioQueueStop(queue, True)
        _AT.AudioQueueDispose(queue, True)

    # ── Linux FIFO path ───────────────────────────────────────────────────────

    def _run_linux(self):
        import shutil
        tmpdir    = tempfile.mkdtemp()
        fifo_path = os.path.join(tmpdir, "tone.raw")
        os.mkfifo(fifo_path)
        cmd  = ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
                "-r", str(SAMPLE_RATE), "-c", "1", fifo_path]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        fifo = open(fifo_path, "wb", buffering=0)
        ph   = 0.0
        try:
            self._ready.set()
            while not self._stop.is_set():
                raw, ph = _gen_square_chunk(ph, self._freq(), 256)
                self._step_wobble()
                fifo.write(raw)
        except BrokenPipeError:
            pass
        finally:
            try:
                fifo.close()
            except OSError:
                pass
            proc.terminate()
            proc.wait()
            shutil.rmtree(tmpdir, ignore_errors=True)
            self._ready.set()

    def _run(self):
        try:
            if _ca_available():
                self._run_ca()
            else:
                self._run_linux()
        except Exception:
            pass
        finally:
            self._ready.set()
