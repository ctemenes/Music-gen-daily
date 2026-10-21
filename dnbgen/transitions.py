"""DJ-style transitions between two rendered tracks.

Each track is split at bar lines: start = bars 0-8, main = bars 8-96, outro = bars 96-104.
A transition A->B is 8 bars that overlays A's outro on B's first 8 bars:
  - tempo glides bar by bar from A's BPM to B's BPM (both sides time-stretched in step, pitch unchanged)
  - A's beat is low-pass filtered down to a muffled kick, then fades out
  - B's pads rise in over the top
  - impact + downlifter at the start, riser and reverse crash into B's build
Played in order (start, main1, trans1-2, main2, ..., mainN, end) the segments are gapless.
"""
import shutil
import os
import subprocess
import tempfile
import numpy as np
import scipy.io.wavfile as wf
import scipy.signal as ss

SR = 44100
TB = 8                       # transition length in bars
MAIN = (8, 96)               # main body bars
XF = 256                     # crossfade at stretched-bar seams
rng = np.random.default_rng(2026)


def _has_rubberband():
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " rubberband " in out


HAS_RUBBERBAND = _has_rubberband()


def load(path):
    sr, x = wf.read(path)
    assert sr == SR
    return x.astype(np.float64) / 32767


def bs(bpm, k):
    """sample index of bar k"""
    return int(round(k * 240.0 / bpm * SR))


def stretch(x, out_len):
    """time-stretch stereo chunk x to out_len samples (+XF tail) without changing pitch"""
    need = out_len + XF
    if len(x) == need:
        return x.copy()
    ratio = len(x) / need
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.wav"), os.path.join(d, "b.wav")
        wf.write(a, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))
        af = (f"rubberband=tempo={ratio:.6f}:transients=crisp:detector=percussive" if HAS_RUBBERBAND
              else f"atempo={ratio:.6f}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a, "-af", af, b], check=True)
        _, y = wf.read(b)
    y = y.astype(np.float64) / 32767
    if len(y) < need:
        y = np.vstack([y, np.zeros((need - len(y), 2))])
    return y[:need]


def chunked_lowpass(x, cutoffs, block=512):
    """low-pass with a cutoff that changes every block (cutoffs: per-sample array)"""
    y = np.zeros_like(x)
    zi = np.zeros((1, 2, 2))
    sos0 = ss.butter(2, float(np.clip(cutoffs[0], 40, SR * 0.45)), "low", fs=SR, output="sos")
    for ch in range(2):  # start the filter settled on the first sample so the seam is click-free
        zi[:, :, ch] = ss.sosfilt_zi(sos0) * x[0, ch]
    for s in range(0, len(x), block):
        e = min(len(x), s + block)
        fc = float(np.clip(cutoffs[s], 40, SR * 0.45))
        sos = ss.butter(2, fc, "low", fs=SR, output="sos")
        for ch in range(2):
            y[s:e, ch], zi[:, :, ch] = ss.sosfilt(sos, x[s:e, ch], zi=zi[:, :, ch])
    return y


def norm(x, peak):
    return x * (peak / (np.abs(x).max() + 1e-9))


def fx_layer(n, bar_starts):
    t = np.arange(n) / SR
    out = np.zeros(n)
    # impact: sub drop + noise burst at transition start
    m = int(1.6 * SR)
    tt = t[:m]
    f = 35 + 60 * np.exp(-tt / .25)
    out[:m] += norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / .6), .45)
    burst = ss.sosfilt(ss.butter(2, 2500, "high", fs=SR, output="sos"), rng.standard_normal(m))
    out[:m] += norm(burst * np.exp(-tt / .35) * np.minimum(1, tt / .002), .12)
    # downlifter over first 2 bars: falling band of noise
    e2 = bar_starts[2]
    nz = rng.standard_normal(e2)
    dl = np.zeros(e2)
    chunks = 20
    for k in range(chunks):
        a, b = k * e2 // chunks, (k + 1) * e2 // chunks
        fc = 9000 * (0.05 ** (k / chunks))
        dl[a:b] = ss.sosfilt(ss.butter(2, [fc * .7, min(fc * 1.4, 20000)], "band", fs=SR, output="sos"), nz[a:b])
    out[:e2] += norm(dl * np.linspace(1, 0, e2) ** 1.5, .16)
    # riser over bars 4-8
    s4 = bar_starts[4]
    m = n - s4
    nz = rng.standard_normal(m)
    rs = np.zeros(m)
    for k in range(chunks):
        a, b = k * m // chunks, (k + 1) * m // chunks
        fc = 500 * (16 ** (k / chunks))
        rs[a:b] = ss.sosfilt(ss.butter(2, [fc * .7, min(fc * 1.4, 20000)], "band", fs=SR, output="sos"), nz[a:b])
    tail = np.clip((m - np.arange(m)) / (m / 8), 0, 1)  # hand over to the reverse crash in the last half bar
    out[s4:] += norm(rs * np.linspace(0, 1, m) ** 2 * tail, .2)
    # reverse crash over final 2 bars
    s6 = bar_starts[6]
    m = n - s6
    tt = np.arange(m) / SR
    crash = ss.sosfilt(ss.butter(2, 4500, "high", fs=SR, output="sos"), rng.standard_normal(m))
    out[s6:] += norm(crash * np.exp(-(tt[::-1]) / .5) * np.minimum(1, tt[::-1] / .003), .22)
    return out


def build_transition(ta, tb, A, B, seed=0):
    """A, B: stereo float arrays of the full outgoing / incoming tracks; ta, tb: their BPMs."""
    global rng
    rng = np.random.default_rng(seed)
    tempos = [ta + (tb - ta) * min(1, k / 6) for k in range(TB)]
    lens = []
    for k, T in enumerate(tempos):
        if k == 0:
            lens.append(bs(ta, 97) - bs(ta, 96))
        elif T == tb:
            lens.append(bs(tb, k + 1) - bs(tb, k))
        else:
            lens.append(int(round(240.0 / T * SR)))
    n = sum(lens)
    starts = np.concatenate([[0], np.cumsum(lens)])
    outA, outB = np.zeros((n + XF, 2)), np.zeros((n + XF, 2))
    for k in range(TB):
        for src, bpm, bar0, dst in ((A, ta, 96, outA), (B, tb, 0, outB)):
            a, b = bs(bpm, bar0 + k), bs(bpm, bar0 + k + 1)
            chunk = src[a:b + XF] if b + XF <= len(src) else np.vstack([src[a:], np.zeros((b + XF - len(src), 2))])
            y = stretch(chunk, lens[k])
            s = starts[k]
            if k > 0:  # crossfade the seam with the previous bar's tail
                ramp = np.linspace(0, 1, XF)[:, None]
                dst[s:s + XF] = dst[s:s + XF] * (1 - ramp) + y[:XF] * ramp
                dst[s + XF:s + lens[k] + XF] = y[XF:]
            else:
                dst[s:s + lens[k] + XF] = y
    outA, outB = outA[:n], outB[:n]
    # B side must end exactly on B's audio so the next segment joins cleanly
    # (last bars ran at ratio 1, so this is already true); A side: filter down then fade
    t_bar = np.interp(np.arange(n), starts, np.arange(TB + 1))
    cutoff = np.where(t_bar < 1, 18000, 18000 * (140 / 18000) ** np.clip((t_bar - 1) / 3, 0, 1))
    outA = chunked_lowpass(outA, cutoff)
    gainA = np.clip(1 - (t_bar - 5) / 2.5, 0, 1)[:, None]
    gainA = np.sin(gainA * np.pi / 2)
    fx = fx_layer(n, starts)
    mix = outA * gainA + outB + np.stack([fx, fx], 1) * np.array([1.0, .92])
    # safety: gently limit anything above the tracks' own peak level
    over = np.abs(mix) > .85
    mix[over] = np.sign(mix[over]) * (.85 + .1 * np.tanh((np.abs(mix[over]) - .85) / .1))
    return mix, tempos


