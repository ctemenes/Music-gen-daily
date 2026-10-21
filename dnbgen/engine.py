"""dnbgen: a small synthesized drum & bass track generator.

Each track is described by a config dict (see tracks.py). The engine builds a
48-bar arrangement: intro (8) / drop A (16) / breakdown (8) / drop B (12) / outro (4),
renders it to a stereo WAV + MP3, and exports a multi-track MIDI file.

All sounds are synthesized from scratch, so the output is fully original and
owned by whoever runs it.
"""
import subprocess
import numpy as np
import scipy.signal as ss
import scipy.io.wavfile as wf
from scipy.signal import fftconvolve
import mido

SR = 44100

SCALES = {
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
}

DRUM_PATTERNS = {
    # k = kick, s = snare (low values are ghost notes), o = open hat
    "twostep": dict(k=[(0, 1), (10, .9)], s=[(4, 1), (12, 1), (7, .22), (9, .18), (15, .2)],
                    o=[(6, .3)], hat="8", ride=False),
    "rolling": dict(k=[(0, 1), (10, .9)], s=[(4, 1), (12, 1), (2, .12), (7, .2), (9, .16), (14, .15)],
                    o=[], hat="16", ride=True),
    "amen": dict(k=[(0, 1), (2, .9), (10, 1), (11, .8)], s=[(4, 1), (7, .5), (9, .45), (12, 1), (15, .5)],
                 o=[], hat="8", ride=True),
    "techstep": dict(k=[(0, 1), (10, 1)], s=[(4, 1), (12, 1)], o=[(14, .25)], hat="16t", ride=False),
    "jungle": dict(k=[(0, 1), (2, .8), (10, 1)],
                   s=[(4, 1), (6, .3), (7, .55), (9, .5), (12, 1), (13, .35), (15, .6)],
                   o=[], hat="8", ride=True),
}

BASS_PATTERNS = {
    "two": [(0, 8), (10, 4), (14, 2)],
    "long": [(0, 14)],
    "roll": [(0, 3), (3, 3), (6, 4), (10, 6)],
    "stab": [(0, 2), (3, 1), (6, 2), (10, 2), (13, 1)],
    "tech": [(0, 6), (6, 2), (10, 3), (14, 2)],
}

CHOP_ORDERS = [[0, 1, 2, 3], [0, 1, 0, 3], [0, 1, 2, 1], [0, 3, 2, 3], [2, 1, 0, 3], [0, 1, 3, 3]]
FORM = [("intro", 16), ("dropA", 32), ("break", 16), ("dropB", 32), ("outro", 8)]


def mtof(m):
    return 440.0 * 2 ** ((np.asarray(m, dtype=float) - 69) / 12)


def filt(x, kind, fc, order=2):
    return ss.sosfilt(ss.butter(order, fc, kind, fs=SR, output="sos"), x)


def bandpass(x, lo, hi, order=2):
    return ss.sosfilt(ss.butter(order, [lo, hi], "band", fs=SR, output="sos"), x)


class Track:
    def __init__(self, cfg):
        self.c = cfg
        self.rng = np.random.default_rng(cfg["seed"])
        self.bpm = cfg["bpm"]
        self.beat = 60 / self.bpm
        self.s16 = self.beat / 4
        self.bar = self.beat * 4
        self.sections = []
        self.sec_len = dict(FORM)
        for name, n in FORM:
            self.sections += [(name, i) for i in range(n)]
        self.nbars = len(self.sections)
        self.L = int(SR * self.bar * self.nbars) + SR * 4
        self.t = np.arange(self.L) / SR
        self.bus = {k: np.zeros(self.L) for k in ("drums", "bass", "pad", "lead", "fx")}
        self.midi = {k: [] for k in ("Drums", "Bass", "Pad", "Lead")}
        self.kicks = []
        self.scale = SCALES[cfg.get("scale", "minor")]
        self.key = cfg["key"]
        self.drum_kit = self._make_kit()

    # ---------- helpers ----------
    def add(self, bus, sig, start):
        i = int(start * SR)
        if i < 0 or i >= self.L:
            return
        e = min(self.L, i + len(sig))
        self.bus[bus][i:e] += sig[: e - i]

    def deg(self, d, base):
        return base + self.scale[d % 7] + 12 * (d // 7)

    def chord_degree(self, bar):
        return self.c["prog"][(bar // 2) % len(self.c["prog"])]

    def voicing(self, bar):
        d = self.chord_degree(bar)
        base = self.key + 12
        n = 4 if self.c.get("sevenths") else 3
        notes = [self.deg(d + 2 * i, base) for i in range(n)]
        while notes[0] > 66:
            notes = [m - 12 for m in notes]
        while notes[0] < 55:
            notes = [m + 12 for m in notes]
        return notes

    def bass_root(self, bar):
        base = self.key - 12
        while base > 40:
            base -= 12
        while base < 33:
            base += 12
        r = self.deg(self.chord_degree(bar), base)
        if r > base + 7:
            r -= 12
        return r

    def step_time(self, bar, step):
        swing = self.c.get("swing", 0.011)
        s = bar * self.bar + step * self.s16
        if float(step).is_integer() and int(step) % 2 == 1:
            s += swing
        return s

    def note(self, track, start, dur, m, vel, ch):
        self.midi[track].append((start, dur, int(m), int(np.clip(vel, 1, 127)), ch))

    # ---------- drum kit ----------
    def _make_kit(self):
        r = self.rng
        kick_style = self.c.get("kick", "punch")
        snare_style = self.c.get("snare", "crisp")

        def kick():
            n = int(0.4 * SR); t = np.arange(n) / SR
            start, end, dec, drive = {"punch": (160, 48, .13, 1.6), "deep": (130, 42, .2, 1.3),
                                      "hard": (190, 52, .11, 2.6)}[kick_style]
            f = end + (start - end) * np.exp(-t / 0.03)
            body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / dec)
            click = filt(r.standard_normal(n), "high", 3000) * np.exp(-t / 0.003) * .35
            return np.tanh((body + click) * drive) / np.tanh(drive)

        def snare():
            n = int(0.3 * SR); t = np.arange(n) / SR
            tone_f, ndec, drive, lo = {"crisp": (185, .075, 1.4, 1100), "hard": (220, .09, 3.0, 900),
                                       "soft": (170, .06, 1.1, 1500)}[snare_style]
            tone = np.sin(2 * np.pi * tone_f * t * (1 + .3 * np.exp(-t / .01))) * np.exp(-t / .045) * .7
            nz = bandpass(r.standard_normal(n), lo, 9500) * np.exp(-t / ndec)
            return np.tanh((tone + nz) * drive) / np.tanh(drive)

        def hat(open_=False):
            n = int((.22 if open_ else .05) * SR); t = np.arange(n) / SR
            metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (3140, 4410, 5230, 6870)) / 4
            nz = filt(r.standard_normal(n) * .7 + metal * .5, "high", 7000)
            return nz * np.exp(-t / (.08 if open_ else .013))

        def ride():
            n = int(.6 * SR); t = np.arange(n) / SR
            metal = sum(np.sin(2 * np.pi * f * t) for f in (2950, 3710, 4890, 6120, 7430)) / 5
            nz = filt(r.standard_normal(n), "high", 5000) * .5
            return filt(metal + nz, "high", 2500) * np.exp(-t / .25) * .6

        kit = {"k": [kick() for _ in range(2)], "s": [snare() for _ in range(4)],
               "h": [hat() for _ in range(4)], "o": [hat(True) for _ in range(2)],
               "r": [ride() for _ in range(2)]}
        for k in kit:
            kit[k] = [x / (np.abs(x).max() + 1e-9) for x in kit[k]]
        return kit

    # ---------- drums ----------
    def drum_hits(self, bar, mode, sec_bar):
        P = DRUM_PATTERNS[self.c["drums"]]
        if mode == "half":
            hits = [(0, "k", 1), (8, "s", .9), (11, "k", .5)] + [(s, "h", .3) for s in range(0, 16, 2)]
            return hits
        if mode == "hats":
            return [(s, "h", .4 if s % 4 == 2 else .25) for s in range(0, 16, 2)] + [(0, "k", .6)]
        hits = [(s, "k", v) for s, v in P["k"]] + [(s, "s", v) for s, v in P["s"]] + [(s, "o", v) for s, v in P["o"]]
        if P["hat"] == "8":
            hits += [(s, "h", .42 if s % 4 == 2 else .25) for s in range(0, 16, 2)]
        elif P["hat"] == "16":
            hits += [(s, "h", .32 if s % 2 == 0 else .14) for s in range(16)]
        else:
            hits += [(s, "h", .38 if s % 4 == 2 else .2) for s in range(16)]
        if P["ride"]:
            hits += [(s, "r", .28) for s in (2, 6, 10, 14)]
        chop = self.c.get("chop", False) and mode in ("full", "full2")
        if mode == "full2" and self.c.get("chop_b", True):
            chop = True
        if chop and sec_bar % 2 == 1:
            order = CHOP_ORDERS[self.rng.integers(len(CHOP_ORDERS))]
            new = []
            for j, src in enumerate(order):
                new += [(s - src * 4 + j * 4, k, v) for s, k, v in hits if src * 4 <= s < src * 4 + 4]
            hits = new
        if sec_bar % 8 == 7 or mode == "fill":
            hits = [h for h in hits if not (h[0] >= 12 and h[1] in "ks")]
            if self.c["drums"] in ("jungle", "amen"):
                hits += [(12 + i * .5, "s", .35 + .08 * i) for i in range(8)]
            else:
                hits += [(12, "s", .7), (13, "s", .55), (14, "s", .8), (15, "s", 1.0)]
        return hits

    def render_drums(self):
        filtered = []
        for bar, (sec, sb) in enumerate(self.sections):
            if sec == "intro":
                build = self.sec_len["intro"] - 8
                mode = "none" if sb < build else "full"
                if sb < build and self.c.get("intro_hats") and sb >= build // 2:
                    mode = "hats"
                if sb >= build:
                    filtered.append(bar)
            elif sec == "dropA":
                mode = "full"
            elif sec == "break":
                mode = self.c.get("break_drums", "none")
                if mode == "half" and sb % 8 == 7:
                    mode = "fill"
            elif sec == "dropB":
                mode = "full2"
            else:
                mode = "full"
            if mode == "none":
                continue
            for step, inst, v in self.drum_hits(bar, mode, sb):
                v *= self.rng.uniform(.88, 1.05)
                st = self.step_time(bar, step)
                kit = self.drum_kit[inst]
                self.add("drums", kit[self.rng.integers(len(kit))] * v * {"k": 1, "s": .85, "h": .6, "o": .5, "r": .4}[inst], st)
                if inst == "k":
                    self.kicks.append(st)
                self.note("Drums", st, self.s16 * .9, {"k": 36, "s": 38, "h": 42, "o": 46, "r": 51}[inst], v * 110, 9)
        if filtered:
            a, b = int(filtered[0] * self.bar * SR), int((filtered[-1] + 1) * self.bar * SR)
            self.bus["drums"][a:b] = filt(self.bus["drums"][a:b], "high", 600) * np.linspace(.35, 1, b - a)
        # room
        n = int(.7 * SR)
        ir = filt(self.rng.standard_normal(n), "low", 6000) * np.exp(-np.arange(n) / SR / .15)
        ir /= np.sqrt((ir ** 2).sum()) * 5
        self.bus["drums"] += fftconvolve(self.bus["drums"], ir)[: self.L] * self.c.get("room", .5)

    # ---------- bass ----------
    def render_bass(self):
        btype = self.c["bass"]
        freq = np.full(self.L, mtof(self.bass_root(0)))
        gate = np.zeros(self.L)
        nenv = np.zeros(self.L)
        any_note = False
        for bar, (sec, sb) in enumerate(self.sections):
            if sec in ("dropA", "dropB"):
                name = self.c["bass_pat"]
                if sec == "dropB" and (sb // 8) % 2 == 1:
                    name = self.c.get("bass_pat2", name)
                pat = BASS_PATTERNS[name]
            elif sec == "break" and self.c.get("break_bass") and sb >= self.sec_len["break"] - 8:
                pat = BASS_PATTERNS["long"]
            else:
                continue
            root = self.bass_root(bar)
            for k, (st, ln) in enumerate(pat):
                m = root
                if k > 0 and self.rng.random() < self.c.get("oct_prob", .15):
                    m += 12
                s = self.step_time(bar, st)
                i, e = int(s * SR), min(self.L, int((s + ln * self.s16) * SR))
                freq[i:] = mtof(m)
                f = int(.004 * SR)
                g = np.ones(e - i); g[:f] = np.linspace(0, 1, f); g[-f:] = np.linspace(1, 0, f)
                gate[i:e] = g
                nenv[i:e] = np.exp(-np.arange(e - i) / SR / self.c.get("bass_decay", .18))
                self.note("Bass", s, ln * self.s16, m, 100, 0)
                any_note = True
        if not any_note:
            return
        if btype != "stab":
            lf = ss.lfilter([.002], [1, -.998], np.log(freq), zi=[np.log(freq[0]) * .998])[0]
            freq = np.exp(lf)
        t = self.t
        subf = np.where(freq / 2 >= 34, freq / 2, freq)
        sub = np.sin(2 * np.pi * np.cumsum(subf) / SR)
        rate = self.bpm / 60 / self.c.get("wob_beats", 4)
        lfo = .5 + .5 * np.sin(2 * np.pi * t * rate)
        if btype == "reese":
            p1, p2 = np.cumsum(freq * 1.0035) / SR, np.cumsum(freq / 1.0035) / SR
            saws = (2 * (p1 % 1) - 1) + (2 * (p2 % 1) - 1)
            body = filt(saws, "low", 260) * (1 - lfo) + filt(saws, "low", 1500) * lfo
            body = np.tanh(body * 1.8)
            out = body * .55 + sub * .38
        elif btype == "sub":
            s1 = np.sin(2 * np.pi * np.cumsum(freq) / SR)
            out = filt(s1 + .18 * np.tanh(3 * s1), "low", 300) * .8 + sub * .25
        elif btype == "growl":
            pc = np.cumsum(freq) / SR
            mod = np.sin(2 * np.pi * pc * self.c.get("fm_ratio", 2.0))
            idx = 1.0 + 3.5 * lfo
            y = np.sin(2 * np.pi * pc + idx * mod)
            y = filt(np.tanh(2.2 * y), "low", 2600)
            out = y * .45 + sub * .45
        else:  # stab
            p = np.cumsum(freq) / SR
            raw = (2 * (p % 1) - 1) + .5 * np.sign(np.sin(2 * np.pi * p))
            bright = filt(raw, "low", 2400); dark = filt(raw, "low", 300)
            y = dark * (1 - nenv) + bright * nenv
            out = np.tanh(y * 1.6) * .6 * nenv + sub * .45 * np.maximum(nenv, .3)
        self.bus["bass"] += filt(out, "high", 32) * gate

    # ---------- pads ----------
    def render_pad(self):
        ptype = self.c["pad"]
        for bar in range(0, self.nbars, 2):
            notes = self.voicing(bar)
            dur = 2 * self.bar
            st = bar * self.bar
            if ptype == "rhodes":
                for step in (0, 10, 22, 27):
                    s = self.step_time(bar, step)
                    n = int(1.6 * SR); t = np.arange(n) / SR
                    v = .9 if step == 0 else .55
                    sig = np.zeros(n)
                    for m in notes:
                        f = mtof(m)
                        sig += np.sin(2 * np.pi * f * t + 1.3 * np.exp(-t / .25) * np.sin(2 * np.pi * f * t)) * np.exp(-t / .7)
                    sig *= (1 + .15 * np.sin(2 * np.pi * 4.5 * t)) * v / len(notes) * 1.6
                    self.add("pad", sig, s)
                for m in notes:
                    self.note("Pad", st, dur, m, 80, 1)
                continue
            n = int((dur + 1.5) * SR); t = np.arange(n) / SR
            att, cut = {"warm": (.5, 1700), "dark": (.8, 1100), "strings": (1.0, 2400)}[ptype]
            env = np.minimum(1, t / att) * np.where(t < dur, 1, np.exp(-(t - dur) / .45))
            sig = np.zeros(n)
            vib = np.ones(n)
            if ptype == "strings":
                vib = vib + .004 * np.sin(2 * np.pi * 5 * t) * np.minimum(1, t / 1.5)
            for m in notes:
                for d in (-.08, 0, .08):
                    p = np.cumsum(mtof(m) * 2 ** (d / 12) * vib) / SR + self.rng.random()
                    sig += 2 * (p % 1) - 1
            sig = filt(sig, "low", cut) / (3 * len(notes))
            if ptype == "dark":
                sig += bandpass(self.rng.standard_normal(n), 250, 1100) * .08
            self.add("pad", sig * env * 1.4, st)
            for m in notes:
                self.note("Pad", st, dur, m, 80, 1)

    # ---------- leads ----------
    def make_motif(self):
        r = self.rng
        cands = [0, 3, 6, 8, 10, 12, 14, 16, 19, 22, 24, 26, 28]
        n = int(r.integers(5, 8))
        on = sorted(set([0] + list(r.choice(cands, n, replace=False))))
        idx, cur = [], int(r.integers(1, 4))
        for _ in on:
            idx.append(cur)
            cur = int(np.clip(cur + r.choice([-2, -1, 1, 1, 2]), 0, 6))
        durs = [min(8, (on[i + 1] if i + 1 < len(on) else 32) - on[i]) for i in range(len(on))]
        return list(zip(on, idx, durs))

    def lead_bars(self):
        out = []
        for bar, (sec, sb) in enumerate(self.sections):
            if sec in ("break", "dropB") or (sec == "dropA" and sb >= 16 and self.c.get("lead_in_A", True)):
                out.append((bar, sec == "dropB" and sb >= 16))
        return out

    def render_lead(self):
        ltype = self.c["lead"]
        if ltype == "none":
            return
        bars = self.lead_bars()
        motif_a = self.make_motif()
        motif_b = self.make_motif()
        for bar, late in bars:
            motif = motif_b if late else motif_a
            notes = self.voicing(bar)
            ext = notes + [m + 12 for m in notes]
            if ltype == "arp":
                pat = [0, 2, 1, 2, 0, 2, 1, 3, 0, 2, 1, 2, 3, 2, 1, 0]
                for step in range(16):
                    if step % 4 == 3 and bar % 2 == 0:
                        continue
                    m = ext[pat[step] % len(ext)] + 12
                    n = int(.16 * SR); t = np.arange(n) / SR
                    sig = filt(np.tanh(3 * np.sin(2 * np.pi * mtof(m) * t)) * np.exp(-t / .07), "low", 3500) * .45
                    s = self.step_time(bar, step)
                    self.add("lead", sig, s)
                    self.note("Lead", s, self.s16 * .8, m, 90, 2)
            elif ltype == "bell":
                if bar % 2:
                    continue
                for on, ix, du in motif:
                    m = ext[ix % len(ext)] + 12
                    if bar % 8 == 6 and on == motif[-1][0]:
                        m = ext[0] + 12  # resolve phrase on the root
                    n = int(1.8 * SR); t = np.arange(n) / SR
                    f = mtof(m)
                    sig = np.sin(2 * np.pi * f * t + 2.2 * np.exp(-t / .35) * np.sin(2 * np.pi * f * 3.5 * t)) * np.exp(-t / .6) * .45
                    s = self.step_time(bar, on)
                    self.add("lead", sig, s)
                    self.note("Lead", s, du * self.s16, m, 95, 2)
            elif ltype == "stab":
                rhythm = [[0, 6, 10], [3, 6, 11, 14], [0, 3, 10]][bar % 3]
                for step in rhythm:
                    n = int(.3 * SR); t = np.arange(n) / SR
                    sig = np.zeros(n)
                    for m in notes:
                        p = mtof(m + 12) * t
                        sig += (2 * (p % 1) - 1) + (2 * ((p * 1.006) % 1) - 1)
                    sig = filt(np.tanh(sig * .8), "high", 350) * np.exp(-t / .14) * .38
                    s = self.step_time(bar, step)
                    self.add("lead", sig, s)
                    for m in notes:
                        self.note("Lead", s, self.s16 * 2, m + 12, 95, 2)
            elif ltype == "vox":
                if bar % 2:
                    continue
                m = notes[-1] + (12 if notes[-1] < 64 else 0)
                dur = 2 * self.bar
                n = int((dur + 1.2) * SR); t = np.arange(n) / SR
                f = mtof(m) * (1 + .006 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1, t / .8))
                p = np.cumsum(f) / SR
                src = 2 * (p % 1) - 1
                vowel = sum(bandpass(src, fc * .9, fc * 1.1) * g for fc, g in ((700, 1), (1150, .7), (2700, .3)))
                env = np.minimum(1, t / .35) * np.where(t < dur, 1, np.exp(-(t - dur) / .5))
                self.add("lead", vowel * env * 1.1, bar * self.bar)
                self.note("Lead", bar * self.bar, dur, m, 85, 2)

    # ---------- fx ----------
    def render_fx(self):
        starts = [b for b, (sec, sb) in enumerate(self.sections) if sb == 0 and sec in ("dropA", "dropB")]
        for b in starts:
            # 4-bar riser
            n = int(4 * self.bar * SR)
            nz = self.rng.standard_normal(n)
            out = np.zeros(n)
            chunks = 24
            for k in range(chunks):
                a, e = k * n // chunks, (k + 1) * n // chunks
                fc = 400 * (20 ** (k / chunks))
                out[a:e] = bandpass(nz[a:e], fc * .7, min(fc * 1.4, 18000))
            out *= np.linspace(0, 1, n) ** 2 * .35
            self.add("fx", out, (b - 4) * self.bar)
            # crash
            n = int(2.0 * SR); t = np.arange(n) / SR
            crash = filt(self.rng.standard_normal(n), "high", 4500) * np.exp(-t / .7) * .5
            self.add("fx", crash, b * self.bar)
        if self.c.get("atmos"):
            nz = filt(self.rng.standard_normal(self.L), "low", 2500) * .015
            crack = (self.rng.random(self.L) > .9997) * self.rng.standard_normal(self.L) * .25
            self.bus["fx"] += nz + filt(crack, "high", 1500)

    # ---------- mix ----------
    def mix(self):
        L = self.L
        duck = np.ones(L)
        depth = self.c.get("duck", .55)
        for kt in self.kicks:
            i = int(kt * SR); n = int(.25 * SR)
            seg = 1 - depth * np.exp(-np.arange(n) / SR / .07)
            e = min(L, i + n)
            duck[i:e] = np.minimum(duck[i:e], seg[: e - i])
        # pad fade-in over first 2 bars, fade out in outro
        bars_total = self.nbars
        fade = np.ones(L)
        fi = int(2 * self.bar * SR); fade[:fi] = np.linspace(0, 1, fi)
        fo_s, fo_e = int((bars_total - self.sec_len["outro"]) * self.bar * SR), int(bars_total * self.bar * SR) + SR * 2
        fade[fo_s:fo_e] = np.linspace(1, 0, fo_e - fo_s); fade[fo_e:] = 0
        g = self.c.get("gains", {})
        drums = self.bus["drums"] * g.get("drums", .85)
        bass = self.bus["bass"] * duck * g.get("bass", .6)
        pad = self.bus["pad"] * fade * (.6 + .4 * duck) * g.get("pad", .42)
        lead = self.bus["lead"] * fade * (.7 + .3 * duck) * g.get("lead", .32)
        # dotted 8th delay on lead
        dly = int(3 * self.s16 * SR)
        d = np.zeros(L)
        for k in range(1, 5):
            d[dly * k:] += lead[: L - dly * k] * (.42 ** k)
        lead_wet = filt(d, "high", 300)
        n = int(self.c.get("verb_len", 2.6) * SR)
        ir = filt(self.rng.standard_normal(n), "low", 5000) * np.exp(-np.arange(n) / SR / (self.c.get("verb_len", 2.6) / 3.7))
        ir /= np.sqrt((ir ** 2).sum()) * 6
        space = fftconvolve(pad * .8 + lead * .7 + lead_wet * .5, ir)[:L] * self.c.get("verb", .5)
        fx = self.bus["fx"] * g.get("fx", .5)
        mono = drums + bass + pad + lead + lead_wet * .6 + space + fx
        mono = filt(mono, "high", 28)
        side = (lead_wet * .35 + space * .5 + fx * .2)
        Lch = mono + np.roll(side, 331) * .5
        Rch = mono - np.roll(side, 331) * .3
        st = np.stack([Lch, Rch], 1)
        end = int((self.nbars * self.bar + 2.5) * SR)
        st = st[:end]
        # loudness: drop A RMS to -12 dBFS, then soft clip
        dropA0 = [b for b, (sec, sb) in enumerate(self.sections) if sec == "dropA"][0]
        a, b = int(dropA0 * self.bar * SR), int((dropA0 + 16) * self.bar * SR)
        rms = np.sqrt((st[a:b] ** 2).mean())
        st *= 10 ** (-12 / 20) / rms
        st = np.tanh(st * 1.1) / 1.1
        pk = np.abs(st).max()
        if pk > .89:
            st *= .89 / pk
        st[-int(.5 * SR):] *= np.linspace(1, 0, int(.5 * SR))[:, None]
        return st

    def render(self, out_base, mp3=True, midi=True):
        self.render_drums()
        self.render_bass()
        self.render_pad()
        self.render_lead()
        self.render_fx()
        st = self.mix()
        wf.write(out_base + ".wav", SR, (st * 32767).astype(np.int16))
        if mp3:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", out_base + ".wav",
                            "-b:a", "192k", "-metadata", f"title={self.c['name']}",
                            "-metadata", "artist=Digital Aura DnB Generator", out_base + ".mp3"], check=True)
        if midi:
            self.write_midi(out_base + ".mid")
        return st

    def write_midi(self, path):
        TPB = 480
        mid = mido.MidiFile(ticks_per_beat=TPB)
        meta = mido.MidiTrack(); mid.tracks.append(meta)
        meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(self.bpm)))
        meta.append(mido.MetaMessage("track_name", name=self.c["name"]))
        for name, evs in self.midi.items():
            if not evs:
                continue
            tr = mido.MidiTrack(); mid.tracks.append(tr)
            tr.append(mido.MetaMessage("track_name", name=name))
            ab = []
            for st, dur, m, vel, ch in evs:
                on = round(st / self.beat * TPB); off = max(on + 1, round((st + dur) / self.beat * TPB))
                ab.append((on, 1, mido.Message("note_on", note=m, velocity=vel, channel=ch)))
                ab.append((off, 0, mido.Message("note_off", note=m, velocity=0, channel=ch)))
            ab.sort(key=lambda x: (x[0], x[1]))
            last = 0
            for tk, _, msg in ab:
                tr.append(msg.copy(time=tk - last)); last = tk
        mid.save(path)
