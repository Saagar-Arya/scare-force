"""Make a clip last one scare, ending at a natural break near the scare's length.

    r = render(samples, rate, seconds=5.0, window_s=0.5, fade_s=0.5)
    r.samples    the clip (repeated if it is shorter than the scare), ended cleanly; exactly
                 r.audible_s long, so aplay finishes by itself when the sound does
    r.audible_s  the scare's length for this clip, in [seconds - window_s, seconds + window_s]
    r.plays      how many times the clip starts (1 when it covers the scare by itself)
    r.ending     "break" (stopped in a quiet moment), "fade" (faded out) or "end" (whole_fit:
                 the clip already lasts seconds +/- window_s, so it plays whole)

Nov 10 options (scare/sound.py turns them on): whole_fit=True plays a clip that already fits the
window whole instead of cutting it at a pause; pause_s=(lo, hi) puts a random lo..hi s of silence
between repeated copies, so a short call sounds like an animal calling again, not a loop.

The sound decides when a scare ends: scare.deterrent ends the light and the arms at the same
moment, so all three start and stop together.

A gap is a stretch at least MIN_GAP_S long whose 10 ms envelope is GAP_DB below the clip's loud
level (its 95th-percentile envelope): the pause between barks, words, calls or siren yelps.

Repeats: a clip shorter than (seconds - window_s) plays again straight after itself. A clip
that ends quietly repeats whole, keeping its own rhythm. A clip the build cut off mid-sound is
repeated only up to its last gap in its second half (if it has one), so the join falls in a
pause. Each copy starts abruptly, as the build made it; the copy before fades out over 10 ms
(50 ms if it ends loud, i.e. a clip with no usable gap).

Where it ends, in order of preference, always within seconds +/- window_s:
  1. break: the quiet moment (a gap, or a copy's quiet end) nearest to `seconds`. The sound
     stops there with a 10 ms fade, so no bark, word or shout is cut through.
  2. fade: no quiet moment, but a copy ends loud in the window (a clip the build cut
     mid-sound): fade that copy out over fade_s, ending where it ends.
  3. fade: neither (the continuous sirens, a bark over street noise): fade out over fade_s,
     ending exactly at `seconds`.
window_s = 0 always ends exactly at `seconds` (a fade unless a pause falls right there).

Levels are unchanged (no gain, only fades towards silence), so every clip still plays at the
library's reference level (D-21, TR4 Test 4).

numpy only (already on the Pi for the detector); WAV in and out with the stdlib wave module.
"""

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOP_S = 0.010        # envelope step
GAP_DB = 25.0        # a gap is this far below the clip's loud level (its 95th-percentile envelope)
MIN_GAP_S = 0.030    # and lasts at least this long
CLICK_FADE_S = 0.010  # fade at a cut in a quiet moment, and at a quiet join
REPEAT_TAIL = 1 / 2  # a repeated copy is trimmed only at a gap in its second half
JOIN_FADE_S = 0.050  # fade at a join where the copy ends loud (the build cut it mid-sound)


@dataclass
class Rendered:
    samples: np.ndarray  # int16, (frames,) or (frames, channels)
    rate: int
    audible_s: float
    plays: int
    ending: str          # "break" | "fade"


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: only 16-bit PCM WAVs are supported")
        ch, rate = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return (x.reshape(-1, ch) if ch > 1 else x), rate


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    ch = 1 if samples.ndim == 1 else samples.shape[1]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(samples, dtype="<i2").tobytes())


def gaps(x: np.ndarray, rate: int) -> list[tuple[int, int]]:
    """Quiet gaps in a clip as (start, end) frame indices, in order."""
    mono = x.astype(np.float64)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    hop = max(1, round(HOP_S * rate))
    n = len(mono) // hop
    if n == 0:
        return []
    rms = np.sqrt((mono[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)
    db = 20 * np.log10(rms)
    quiet = db < np.percentile(db, 95) - GAP_DB
    min_hops = max(1, round(MIN_GAP_S / HOP_S))
    out, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= min_hops:
                out.append((i * hop, j * hop))
            i = j
        else:
            i += 1
    return out


def _fade_out(x: np.ndarray, end: int, frames: int) -> None:
    """Linear fade to silence over the `frames` before `end` (in place, float array)."""
    frames = min(frames, end)
    if frames <= 0:
        return
    ramp = np.linspace(1.0, 0.0, frames, endpoint=False)
    if x.ndim > 1:
        ramp = ramp[:, None]
    x[end - frames:end] *= ramp


def render(samples: np.ndarray, rate: int, seconds: float, window_s: float = 0.5,
           fade_s: float = 0.5, pause_s=(0.0, 0.0), whole_fit: bool = False,
           rng=None) -> Rendered:
    if seconds <= 0:
        raise ValueError("seconds must be > 0")
    if not 0 <= window_s < seconds or fade_s <= 0:
        raise ValueError("window_s must be in [0, seconds) and fade_s > 0")
    target = round(seconds * rate)
    lo, hi = target - round(window_s * rate), target + round(window_s * rate)
    click = max(1, round(CLICK_FADE_S * rate))
    src = samples.astype(np.float64)
    clip_gaps = gaps(samples, rate)
    hop = max(1, round(HOP_S * rate))

    # 0. Nov 10: a clip that already lasts seconds +/- window_s plays whole, ending with itself.
    if whole_fit and lo <= len(src) <= hi:
        out = src.copy()
        _fade_out(out, len(out), click)
        pcm = np.clip(np.round(out), -32768, 32767).astype(np.int16)
        return Rendered(pcm, rate, len(out) / rate, 1, "end")

    # 1. What one copy is, if the clip has to repeat.
    repeat = len(src) < lo
    pause_lo, pause_hi = (float(v) for v in pause_s)
    pauses = repeat and pause_hi > 0  # Nov 10: a short, varied silence between copies
    rng = rng if rng is not None else np.random.default_rng()
    body = len(src)
    quiet_end = bool(clip_gaps) and clip_gaps[-1][1] >= len(src) - hop  # ends in a pause
    if repeat and not quiet_end:
        tail = [g for g in clip_gaps if g[0] >= len(src) * (1 - REPEAT_TAIL)]
        if tail:
            body, quiet_end = tail[-1][0] + click, True
    join_fade = click if quiet_end else round(JOIN_FADE_S * rate)

    # 2. Lay copies end to end up to the window's far edge (one copy if it already covers it).
    out = np.zeros((hi,) + src.shape[1:], dtype=np.float64)
    copies, pos = [], 0  # (start, frames)
    while pos < hi and (repeat or not copies):
        n = min(body, hi - pos)
        seg = src[:n].copy()
        if repeat and pos + n < hi:  # a join
            _fade_out(seg, n, join_fade)
        out[pos:pos + n] = seg
        copies.append((pos, n))
        pos += n
        if pauses and pos < hi:
            pos += round(rng.uniform(pause_lo, pause_hi) * rate)

    # 3. Where to stop: the candidate nearest `seconds`.
    quiet = [s + a + click for s, n in copies for a, _ in clip_gaps if a < n]
    ends = [s + n for s, n in copies if n == body]  # copies that play to their end
    if quiet_end or pauses:  # a copy followed by a pause ends in silence
        quiet += ends
    quiet = [q for q in quiet if lo <= q <= hi]
    loud_ends = [e for e in ends if lo <= e <= hi] if not quiet_end else []
    nearest = lambda xs: min(xs, key=lambda x: (abs(x - target), x))
    if quiet:
        cut, fade, ending = nearest(quiet), click, "break"
    elif loud_ends:
        cut, fade, ending = nearest(loud_ends), round(fade_s * rate), "fade"
    else:
        cut, fade, ending = min(target, pos), round(fade_s * rate), "fade"
    out = out[:cut]
    _fade_out(out, cut, fade)
    plays = sum(1 for s, _ in copies if s < cut - click)
    pcm = np.clip(np.round(out), -32768, 32767).astype(np.int16)
    return Rendered(pcm, rate, cut / rate, plays, ending)
