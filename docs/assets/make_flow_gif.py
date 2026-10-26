#!/usr/bin/env python3
"""Render docs/assets/flow.gif: the agentic flow, end to end.

Usage: python3 docs/assets/make_flow_gif.py   (needs Pillow and the IBM Plex Sans font)

Visual system ("less, but better"):
- Warm Braun white, graphite ink, one orange accent. Green and red only for results.
- IBM Plex Sans, light headlines, generous margins, one idea per scene.
- A precision scale at the bottom, like a Braun tuner dial, with an orange needle that travels through the
  stages of the flow, like a tachometer needle.
- Rendered at 2x and downsampled, so lines and type stay crisp.
"""
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("flow.gif")
W, H, SS = 960, 540, 2           # logical size and supersampling factor
FPS = 12
M = 72                           # outer margin

C = {
    "bg": (241, 239, 234),
    "card": (250, 249, 246),
    "hair": (207, 203, 194),
    "grey": (140, 136, 128),
    "ink": (29, 29, 27),
    "accent": (235, 106, 43),
    "ok": (62, 125, 82),
    "err": (192, 57, 43),
}

STAGES = ["DESIGN", "TESTS", "BUILD", "VERIFY", "REVIEW", "DOCS", "SHIP"]
SCALE_Y = 470
SCALE_X0, SCALE_X1 = M + 24, W - M - 24


def font_path(style):
    name = f"IBMPlexSans-{style}.ttf"
    for root in (Path("/usr/share/fonts"), Path("/Library/Fonts"), Path.home() / ".fonts",
                 Path.home() / ".local/share/fonts"):
        if root.exists():
            hit = next(root.rglob(name), None)
            if hit:
                return hit
    if shutil.which("fc-match"):
        out = subprocess.run(["fc-match", "-f", "%{file}", f"IBM Plex Sans:style={style}"], capture_output=True, text=True)
        if out.stdout.endswith(name):
            return Path(out.stdout)
    raise SystemExit("IBM Plex Sans not found. Install it (e.g. `apt install fonts-ibm-plex`) and run again.")


_FONTS = {}


def font(style, size):
    key = (style, size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(str(font_path(style)), size * SS)
    return _FONTS[key]


# ---------------- easing and helpers ----------------

def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(t):
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def span(t, a, b):
    return clamp((t - a) / (b - a)) if b > a else float(t >= a)


def rgb(x):
    return C[x] if isinstance(x, str) else x


def mix(c, base, a):
    c, base = rgb(c), rgb(base)
    return tuple(round(base[i] + (c[i] - base[i]) * clamp(a)) for i in range(3))


class Frame:
    def __init__(self):
        self.im = Image.new("RGB", (W * SS, H * SS), C["bg"])
        self.d = ImageDraw.Draw(self.im)

    def text(self, x, y, s, size=16, style="Regular", color="ink", a=1.0, base="bg", anchor="la", track=0):
        f = font(style, size)
        col = mix(color, base, a)
        if not track:
            self.d.text((x * SS, y * SS), s, font=f, fill=col, anchor=anchor)
            return
        cx = x * SS
        if anchor[0] == "m":
            cx -= self.width(s, size, style, track) * SS / 2
        for ch in s:
            self.d.text((cx, y * SS), ch, font=f, fill=col, anchor="l" + anchor[1])
            cx += self.d.textlength(ch, font=f) + track * SS

    def width(self, s, size=16, style="Regular", track=0):
        return self.d.textlength(s, font=font(style, size)) / SS + track * max(0, len(s) - 1)

    def line(self, x0, y0, x1, y1, color="hair", w=1, a=1.0, base="bg"):
        self.d.line((x0 * SS, y0 * SS, x1 * SS, y1 * SS), fill=mix(color, base, a), width=max(1, round(w * SS)))

    def rect(self, x0, y0, x1, y1, color="card", r=0, a=1.0, base="bg", outline=None, ow=1):
        fill = mix(color, base, a) if color else None
        out = mix(outline, base, a) if outline else None
        self.d.rounded_rectangle((x0 * SS, y0 * SS, x1 * SS, y1 * SS), radius=r * SS, fill=fill, outline=out,
                                 width=round(ow * SS))

    def circle(self, cx, cy, r, color="ink", a=1.0, base="bg", outline=None, ow=1):
        fill = mix(color, base, a) if color else None
        out = mix(outline, base, a) if outline else None
        self.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=fill, outline=out,
                       width=round(ow * SS))

    def check(self, cx, cy, r, color="ok", a=1.0, base="bg"):
        """Filled dot with a white tick, drawn progressively with a in [0, 1]."""
        if a <= 0:
            return
        self.circle(cx, cy, r, color, a=min(1, a * 2), base=base)
        k = clamp(a * 2 - 1)
        if k > 0:
            p = [(cx - r * 0.45, cy + r * 0.02), (cx - r * 0.1, cy + r * 0.38), (cx + r * 0.48, cy - r * 0.36)]
            seg1 = min(1, k * 2)
            pts = [p[0], (p[0][0] + (p[1][0] - p[0][0]) * seg1, p[0][1] + (p[1][1] - p[0][1]) * seg1)]
            if k > 0.5:
                s2 = (k - 0.5) * 2
                pts.append((p[1][0] + (p[2][0] - p[1][0]) * s2, p[1][1] + (p[2][1] - p[1][1]) * s2))
            self.d.line([(x * SS, y * SS) for x, y in pts], fill=C["card"], width=round(max(1.6, r / 4.5) * SS),
                        joint="curve")

    def cross(self, cx, cy, r, a=1.0):
        self.circle(cx, cy, r, "err", a=a)
        k = r * 0.38
        for dx in (-1, 1):
            self.line(cx - k, cy - k * dx, cx + k, cy + k * dx, "card", w=max(1.6, r / 4.5), a=a, base="err")

    def output(self):
        return self.im.resize((W, H), Image.LANCZOS)


# ---------------- persistent chrome: header and tuner scale ----------------

def stage_x(pos):
    """x of a (fractional) stage position; -1 is the rest position just before the first stage."""
    if pos < 0:
        return SCALE_X0 - 24 * (-pos)
    i = min(math.floor(pos), len(STAGES) - 2)
    x0 = SCALE_X0 + (SCALE_X1 - SCALE_X0) * i / (len(STAGES) - 1)
    x1 = SCALE_X0 + (SCALE_X1 - SCALE_X0) * (i + 1) / (len(STAGES) - 1)
    return x0 + (x1 - x0) * (pos - i)


def chrome(f, needle, index, total):
    f.text(M, 40, "AGENTIC FLOW", 11, "Medium", "grey", track=2.2)
    f.text(W - M, 40, f"{index:02d} / {total:02d}", 11, "Medium", "grey", anchor="ra")
    f.line(M, 64, W - M, 64)
    # Tuner scale: fine ticks, stage ticks and labels.
    f.line(SCALE_X0 - 24, SCALE_Y, SCALE_X1 + 24, SCALE_Y, "hair")
    steps = 72
    for k in range(steps + 1):
        x = SCALE_X0 - 24 + (SCALE_X1 - SCALE_X0 + 48) * k / steps
        f.line(x, SCALE_Y - 4, x, SCALE_Y, "hair")
    for i, name in enumerate(STAGES):
        x = stage_x(i)
        near = 1 - clamp(abs(needle - i))
        f.line(x, SCALE_Y - 10, x, SCALE_Y, "grey")
        f.text(x, SCALE_Y + 14, name, 10, "Medium", "ink" if near > 0.5 else "grey", anchor="ma", track=1.6)
    # Needle: thin and orange, with a small pivot dot, like an instrument pointer.
    nx = stage_x(needle)
    f.line(nx, SCALE_Y - 26, nx, SCALE_Y + 6, "accent", w=2)
    f.circle(nx, SCALE_Y - 26, 3, "accent")


def headline(f, t, title, sub=None, y=104):
    a = ease(span(t, 0.0, 0.18))
    f.text(M, y + (1 - a) * 8, title, 38, "Light", "ink", a=a)
    if sub:
        b = ease(span(t, 0.08, 0.26))
        f.text(M, y + 56 + (1 - b) * 6, sub, 16, "Regular", "grey", a=b)


# ---------------- scenes ----------------

def sc_title(f, t):
    a = ease(span(t, 0.05, 0.35))
    f.text(M, 168 + (1 - a) * 10, "agentic-flow", 64, "Light", "ink", a=a)
    f.rect(M, 262, M + 44 * ease(span(t, 0.25, 0.5)), 265, "accent")
    b = ease(span(t, 0.35, 0.6))
    f.text(M, 292, "From an idea to tested code, with a team of agents.", 20, "Regular", "ink", a=b)
    c = ease(span(t, 0.55, 0.8))
    f.text(M, 326, "Opus designs.  Sonnet builds.  You decide.", 16, "Regular", "grey", a=c)


def sc_design(f, t):
    headline(f, t, "Opus designs the contract.", "One document every agent works from: architecture.md")
    a = ease(span(t, 0.15, 0.3))
    x0, y0, x1, y1 = M, 200, M + 430, 420
    f.rect(x0, y0 + (1 - a) * 10, x1, y1 + (1 - a) * 10, "card", r=10, a=a, outline="hair")
    card = mix("card", "bg", a)
    f.text(x0 + 24, y0 + 22, "architecture.md", 13, "Medium", "grey", a=a, base=card)
    rows = [("§6", "Contracts", "exact signatures between modules"),
            ("§8", "Acceptance criteria", "AC-001 … AC-012, each with a “must not”"),
            ("§8", "Work packages", "4 packages, 2 waves, disjoint files"),
            ("§4b", "Existing code", "patterns to mirror, with file:line")]
    for k, (sec, name, note) in enumerate(rows):
        b = ease(span(t, 0.28 + k * 0.08, 0.4 + k * 0.08))
        y = y0 + 62 + k * 38
        f.text(x0 + 24, y, sec, 13, "Medium", "accent", a=b, base=card)
        f.text(x0 + 66, y, name, 15, "Medium", "ink", a=b, base=card)
        f.text(x0 + 66, y + 19, note, 12, "Regular", "grey", a=b, base=card)
    # Right column: independent review and the first human gate.
    c = ease(span(t, 0.62, 0.75))
    rx = M + 480
    f.check(rx + 12, 236, 12, a=span(t, 0.62, 0.8))
    f.text(rx + 36, 226, "Reviewed in a clean context", 15, "Medium", "ink", a=c)
    f.text(rx + 36, 248, "by a second Opus that never saw the interview", 12, "Regular", "grey", a=c)
    d = ease(span(t, 0.8, 0.95))
    f.rect(rx, 300, rx + 250, 340, None, r=20, a=d, outline="accent", ow=1.5)
    f.text(rx + 125, 320, "Gate 1 · you approve the design", 13, "Medium", "accent", a=d, anchor="mm")


def sc_tests(f, t):
    headline(f, t, "The exam comes before the code.",
             "A separate agent turns every criterion into a test. They must fail first.")
    acs = ["AC-001  shorten a valid URL", "AC-002  reject anything but http(s)", "AC-003  resolve a code",
           "AC-004  unknown code fails cleanly"]
    for k, s in enumerate(acs):
        a = ease(span(t, 0.2 + k * 0.08, 0.32 + k * 0.08))
        y = 226 + k * 40
        f.text(M + 30, y, s, 16, "Regular", "ink", a=a)
        f.line(M, y + 26, M + 430, y + 26, "hair", a=a)
        red = span(t, 0.6 + k * 0.04, 0.7 + k * 0.04)
        f.circle(M + 9, y + 10, 6, None, a=a, outline="hair", ow=1.5)
        if red:
            f.circle(M + 9, y + 10, 6 * ease(red), "err")
    b = ease(span(t, 0.72, 0.86))
    f.text(M + 520, 226, "Red", 44, "Light", "err", a=b)
    f.text(M + 520, 286, "No code exists yet.", 15, "Regular", "ink", a=b)
    f.text(M + 520, 308, "The tests are locked for implementers.", 15, "Regular", "grey", a=b)


def lane(f, y, label, progress, a=1.0, state=None):
    f.text(M, y - 8, label, 13, "Medium", "ink", a=a)
    x0, x1 = M + 90, M + 520
    f.line(x0, y, x1, y, "hair", a=a)
    if progress > 0:
        f.line(x0, y, x0 + (x1 - x0) * progress, y, "ink", w=4, a=a)
    if state == "done":
        f.check(x1 + 22, y, 9)


def sc_build(f, t):
    headline(f, t, "Sonnet builds, in parallel.", "One implementer per work package. Each one writes only its own files.")
    w1 = [("WP-01", 0.5), ("WP-02", 0.58), ("WP-03", 0.46)]
    for k, (name, end) in enumerate(w1):
        lane(f, 236 + k * 44, name, ease(span(t, 0.18, end)), state="done" if t > end + 0.02 else None)
    f.text(M + 600, 226, "Wave 1", 12, "Medium", "grey", a=ease(span(t, 0.18, 0.28)))
    # A write outside its zone, blocked by the hook.
    if 0.3 < t < 0.62:
        k = ease(span(t, 0.3, 0.38))
        fade = 1 - span(t, 0.56, 0.62)
        x = M + 90 + 430 * 0.32
        f.line(x, 283, x, 283 + 12 * k, "accent", w=1.5, a=fade)
        if t > 0.38:
            f.cross(x, 302, 6, a=fade)
            f.text(x + 14, 302, "Blocked by the hook: not WP-02’s file", 12, "Medium", "err", a=fade, anchor="lm")
    b = span(t, 0.66, 0.95)
    if b:
        lane(f, 372, "WP-04", ease(b), a=ease(span(t, 0.62, 0.7)), state="done" if t > 0.97 else None)
        f.text(M + 600, 362, "Wave 2", 12, "Medium", "grey", a=ease(span(t, 0.62, 0.7)))


def sc_verify(f, t):
    headline(f, t, "Verified against a baseline.",
             "What passed before must still pass. What was already broken is reported, not blamed.")
    phases = ["Build", "Types", "Lint", "Tests", "Acceptance"]
    for k, name in enumerate(phases):
        x = M + 18 + k * 110
        a = ease(span(t, 0.18 + k * 0.05, 0.26 + k * 0.05))
        f.circle(x, 262, 16, None, a=a, outline="hair", ow=1.5)
        f.check(x, 262, 16, a=span(t, 0.35 + k * 0.08, 0.5 + k * 0.08))
        f.text(x, 292, name, 13, "Medium", "ink", a=a, anchor="ma")
    n = int(round(12 * ease(span(t, 0.55, 0.85))))
    c = ease(span(t, 0.55, 0.65))
    f.text(M, 340, f"{n:>2} / 12", 44, "Light", "ok" if n == 12 else "ink", a=c)
    f.text(M + 160, 356, "acceptance criteria green", 15, "Regular", "grey", a=c)


def sc_review(f, t):
    headline(f, t, "Independent judges.",
             "A reviewer, an adversarial verifier and QA. Each defect goes back to its owner.")
    a = ease(span(t, 0.15, 0.28))
    x0, y0, x1, y1 = M, 200, M + 470, 330
    fixed = span(t, 0.78, 0.9)
    f.rect(x0, y0, x1, y1, "card", r=10, a=a, outline="hair")
    card = mix("card", "bg", a)
    tag = "ok" if fixed >= 1 else "accent"
    f.rect(x0 + 24, y0 + 22, x0 + 84, y0 + 44, None, r=11, a=a, base=card, outline=tag, ow=1.2)
    f.text(x0 + 54, y0 + 33, "Fixed" if fixed >= 1 else "High", 11, "Medium", tag, a=a, base=card, anchor="mm")
    f.text(x0 + 100, y0 + 24, "WP-02 · cli.py:71", 13, "Medium", "grey", a=a, base=card)
    f.text(x0 + 24, y0 + 62, "stats swallows storage errors and exits 0", 16, "Regular", "ink", a=a, base=card)
    f.text(x0 + 24, y0 + 88, "contract §6.2 says: “error: storage” and exit 3", 12, "Regular", "grey", a=a, base=card)
    steps = [("Reviewer", "found it", 0.3), ("Verifier", "could not refute it", 0.42),
             ("QA", "reproduced it with a test", 0.54), ("WP-02", "fix cycle 1 of 2", 0.66)]
    for k, (who, what, t0) in enumerate(steps):
        b = ease(span(t, t0, t0 + 0.1))
        y = 218 + k * 34
        x = M + 530
        f.check(x + 8, y + 9, 8, color="ok" if k < 3 or fixed >= 1 else "accent", a=span(t, t0, t0 + 0.12))
        f.text(x + 26, y, who, 14, "Medium", "ink", a=b)
        f.text(x + 104, y + 1, what, 13, "Regular", "grey", a=b)
    f.text(M, 360, "Second round: clean.", 15, "Medium", "ok", a=ease(span(t, 0.88, 0.98)))


def sc_docs(f, t):
    headline(f, t, "Documentation that stays true.",
             "Only once the code is accepted. Every claim checked against the code.")
    rows = [("Code map", "generated, no model involved"), ("README", "updated where behaviour changed"),
            ("CHANGELOG", "one entry for this cycle"), ("Links and paths", "all checked")]
    for k, (name, note) in enumerate(rows):
        a = ease(span(t, 0.2 + k * 0.1, 0.3 + k * 0.1))
        y = 222 + k * 40
        f.check(M + 9, y + 10, 9, a=span(t, 0.28 + k * 0.1, 0.42 + k * 0.1))
        f.text(M + 32, y, name, 16, "Medium", "ink", a=a)
        f.text(M + 220, y + 1, note, 15, "Regular", "grey", a=a)
        f.line(M, y + 28, M + 640, y + 28, "hair", a=a)


def sc_ship(f, t):
    a = ease(span(t, 0.0, 0.2))
    f.text(M, 120 + (1 - a) * 8, "Ready.", 64, "Light", "ink", a=a)
    stats = [("12", "acceptance criteria"), ("4", "work packages"), ("1", "fix cycle"), ("0", "foreign writes")]
    for k, (n, label) in enumerate(stats):
        b = ease(span(t, 0.18 + k * 0.07, 0.3 + k * 0.07))
        x = M + k * 190
        f.text(x, 236, n, 40, "Light", "ink", a=b)
        f.text(x, 292, label, 13, "Regular", "grey", a=b)
    c = ease(span(t, 0.5, 0.65))
    f.rect(M, 350, M + 290, 392, None, r=21, a=c, outline="accent", ow=1.5)
    f.text(M + 145, 371, "Gate 2 · you approve the commit", 13, "Medium", "accent", a=c, anchor="mm")


SCENES = [  # (scene, seconds, needle position it travels to)
    (sc_title, 4.0, -1),
    (sc_design, 6.5, 0),
    (sc_tests, 5.0, 1),
    (sc_build, 6.5, 2),
    (sc_verify, 4.5, 3),
    (sc_review, 7.0, 4),
    (sc_docs, 5.0, 5),
    (sc_ship, 5.5, 6),
]
FINAL_HOLD_MS = 3000


def build_palette():
    """Exact colours plus their anti-aliasing ramps over each background they are drawn on. An adaptive palette
    would shift the orange towards red; this one keeps every colour true and stable between frames."""
    pairs = [("ink", "bg"), ("grey", "bg"), ("hair", "bg"), ("accent", "bg"), ("ok", "bg"), ("err", "bg"),
             ("card", "bg"), ("ink", "card"), ("grey", "card"), ("hair", "card"), ("accent", "card"), ("ok", "card"),
             ("card", "ok"), ("card", "err")]
    colours = []
    for c, base in pairs:
        for k in range(18):
            rgb = mix(C[c], C[base], k / 17)
            if rgb not in colours:
                colours.append(rgb)
    colours = colours[:256]
    pal = Image.new("P", (1, 1))
    pal.putpalette([v for rgb in colours for v in rgb] + [0] * (768 - 3 * len(colours)))
    return pal


def render():
    frames, durations = [], []
    prev = -1
    for idx, (scene, secs, target) in enumerate(SCENES):
        n = max(2, int(secs * FPS))
        for i in range(n):
            t = i / (n - 1)
            f = Frame()
            chrome(f, prev + (target - prev) * ease(span(t, 0.0, 0.25)), idx + 1, len(SCENES))
            scene(f, t)
            frames.append(f.output())
            durations.append(1000 // FPS)
        prev = target
    durations[-1] = FINAL_HOLD_MS
    palette = build_palette()
    out, outd = [], []
    for fr, d in zip(frames, durations):
        q = fr.quantize(palette=palette, dither=Image.Dither.NONE)
        if out and q.tobytes() == out[-1].tobytes():
            outd[-1] += d
            continue
        out.append(q)
        outd.append(d)
    out[0].save(OUT, save_all=True, append_images=out[1:], duration=outd, loop=0, optimize=True, disposal=1)
    print(f"{OUT} · {len(out)} frames · {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    render()
