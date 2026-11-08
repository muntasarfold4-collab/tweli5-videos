#!/usr/bin/env python3
"""Actual News renderer (3-story roundup in the studio look).

Usage:
  python3 actualnews.py job.json OUT.mp4            # full render (video + voice + sting/whooshes)
  python3 actualnews.py job.json OUT.mp4 --stills 3,20,35   # check stills only (still_<t>.png)
  python3 actualnews.py job.json OUT.mp4 --cover 4.0        # also save OUT_cover.png from that time

job.json:
{
  "voice": "voice.wav",                 # Ed's voiceover (mono 48 kHz wav)
  "words": "words.json",                # ElevenLabs Scribe words list ([{"text","start","end"}...]);
                                        # inline [tags] are ignored automatically
  "stories": [                          # 1 (breaking extra) to ~8, in order; stories after the
                                        # first need "start_phrase" (first words Ed says for it)
    {"image": "img1.png", "focus": 0.55, "tab": "BREAKING NEWS",
     "headline": "INTERNET SOLVES CRASH IN 0.2 SECONDS",
     "ticker": "NTSB: cause not yet determined   ■   ...   ■   "},
    {"images": [{"image": "a.jpg", "focus": 0.4}, {"image": "b.jpg", "focus": 0.6}], ...},  # several pics, equal turns
    {"image": "img2.png", "focus": 0.5, "tab": "ALSO IN THE NEWS", ...},
    {"image": "img3.png", "focus": 0.5, "tab": "AND FINALLY", ...}
  ],
  "story_starts": [0, 19.3, 33.55],     # optional; auto-detected from "Also in the news" / "And finally"
  "poses": [[0,0],[1.15,3],...],        # optional; auto from sentences if missing
  "bleep": ["word", ...]                # optional extra words to censor; common swears are bleeped
                                        # automatically (voice muted + 1 kHz beep, caption "F***")
}
Poses: 0 clasped hands, 1 glasses pull-down, 2 shrug, 3 point at screen, 4 peace sign.
Assets (plate.png, pose0-4.png, sting.mp3, whoosh.mp3) are read from ./assets next to this file.
"""
import json, math, os, re, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

HERE = os.path.dirname(os.path.abspath(__file__))
A = lambda f: os.path.join(HERE, 'assets', f)
W, H, FPS = 1080, 1920, 30
FB = '/usr/share/fonts/opentype/inter/InterDisplay-ExtraBold.otf'
FBL = '/usr/share/fonts/opentype/inter/InterDisplay-Black.otf'
FBI = '/usr/share/fonts/opentype/inter/InterDisplay-ExtraBoldItalic.otf'
FM = '/usr/share/fonts/opentype/inter/Inter-SemiBold.otf'
RED = (200, 18, 28)
YEL = (255, 214, 0)
DESK_Y = 1309
SCR = (582, 374, 1080, 982)
SW, SH = SCR[2] - SCR[0], SCR[3] - SCR[1]

args = sys.argv[1:]
JOB, OUT = args[0], args[1]
STILLS = COVER = None
if '--stills' in args: STILLS = [float(x) for x in args[args.index('--stills') + 1].split(',')]
if '--cover' in args: COVER = float(args[args.index('--cover') + 1])
jobdir = os.path.dirname(os.path.abspath(JOB))
job = json.load(open(JOB))
# "show": "news" (default, red tabs) or "culture" (internet-culture show, purple tabs)
TABC = (124, 44, 255) if job.get('show') == 'culture' else (200, 18, 28)
P = lambda f: f if os.path.isabs(f) else os.path.join(jobdir, f)

# ---------- words (strip [tags]) ----------
raw = json.load(open(P(job['words'])))
if isinstance(raw, dict): raw = raw.get('words', raw)
words, intag = [], False
for w in raw:
    t = w['text'].strip()
    if not t: continue
    if t.startswith('['): intag = True
    if intag:
        if t.endswith(']'): intag = False
        continue
    words.append(dict(text=t, start=float(w['start']), end=float(w['end'])))
# ---------- bleeps (swears: mute voice, beep, starred caption) ----------
SWEARS = {'fuck', 'fucking', 'fucked', 'fucker', 'motherfucker', 'shit', 'shitty', 'bullshit', 'bitch',
          'bitches', 'asshole', 'arsehole', 'bastard', 'prick', 'wanker', 'twat', 'bollocks',
          'pissed', 'goddamn'} | {x.lower() for x in job.get('bleep', [])}
BLEEPS = []
for w in words:
    core = re.sub(r'[^a-z]', '', w['text'].lower())
    if core and core in SWEARS:
        BLEEPS.append((max(w['start'] - 0.03, 0), w['end'] + 0.03))
        m = re.match(r'^([^A-Za-z]*)([A-Za-z])([A-Za-z]*)(.*)$', w['text'])
        if m: w['text'] = m.group(1) + m.group(2).upper() + '*' * max(len(m.group(3)), 2) + m.group(4)
VEND = words[-1]['end']
DUR = round(VEND + 1.4, 2)

def norm(s): return re.sub(r'[^a-z]', '', s.lower())

# ---------- story starts ----------
# Any number of stories (1 for a breaking extra, ~6 for the daily episode).
# Each story after the first gives "start_phrase": the first 1-3 words Ed says for it
# (e.g. "Meanwhile", "Over in Hollywood", "And finally"); the renderer finds them in order.
NS = len(job['stories'])
if 'story_starts' in job:
    starts = job['story_starts']
else:
    starts = [0.0]
    wi = 0
    for st in job['stories'][1:]:
        ph = [norm(x) for x in st.get('start_phrase', '').split() if norm(x)]
        found = None
        if ph:
            for i in range(wi, len(words) - len(ph) + 1):
                if all(norm(words[i + k]['text']) == ph[k] for k in range(len(ph))):
                    found = i; break
        if found is None:  # legacy roundup phrases
            for i in range(wi, len(words) - 1):
                a, b = norm(words[i]['text']), norm(words[i + 1]['text'])
                if (a, b) in (('also', 'in'), ('and', 'finally')) and words[i]['start'] > starts[-1] + 1:
                    found = i; break
        if found is None:
            raise SystemExit(f"could not find start_phrase {st.get('start_phrase')!r} in the transcript")
        starts.append(words[found]['start'] - 0.05)
        wi = found + 1
WHOOSH = starts[1:]

# ---------- sentences -> poses ----------
sents, cur = [], []
for w in words:
    cur.append(w)
    if re.search(r'[.?!]$', w['text']):
        sents.append(cur); cur = []
if cur: sents.append(cur)
def story_of(t):
    return max(i for i, x in enumerate(starts) if t >= x - 0.3)
if 'poses' in job:
    TL = [tuple(x) for x in job['poses']]
else:
    TL = [(0.0, 0)]
    by = {}
    for s in sents: by.setdefault(story_of(s[0]['start']), []).append(s)
    for si in sorted(by):
        ss = by[si]
        for m, s in enumerate(ss):
            st = max(s[0]['start'] - 0.05, 0.0)
            txt = s[-1]['text']
            if m == 0: p = 3
            elif m == len(ss) - 1: p = 2 if txt.endswith('?') else 0
            else: p = 2 if (m - 1) % 2 == 0 else 1
            TL.append((st, p))
    # short stories (one sentence) get a mid-story reaction so the anchor doesn't just point
    react = [2, 1, 0]; r = 0
    bounds = list(starts) + [VEND]
    for k in range(len(starts)):
        inside = [x for x in TL if bounds[k] - 0.1 <= x[0] < bounds[k + 1] - 0.1]
        if len(inside) <= 1 and bounds[k + 1] - bounds[k] > 2.5:
            TL.append((bounds[k] + 0.6 * (bounds[k + 1] - bounds[k]), react[r % 3])); r += 1
    TL.append((VEND + 0.2, 4))
    TL = sorted(TL)

# ---------- caption chunks ----------
chunks, cur = [], []
for w in words:
    cur.append(w)
    txt = ' '.join(x['text'] for x in cur)
    if len(cur) >= 3 or len(txt) >= 18 or re.search(r'[.,?!:;]$', w['text']):
        chunks.append(cur); cur = []
if cur: chunks.append(cur)
for i, c in enumerate(chunks):
    c_end = chunks[i + 1][0]['start'] if i + 1 < len(chunks) else DUR
    for w in c: w['chunk_end'] = min(c_end, c[-1]['end'] + 0.6)

# ---------- assets ----------
def ctext(txt, path, size, fill, squeeze=0.86, stroke=0, sfill=(0, 0, 0)):
    f = ImageFont.truetype(path, size)
    b = f.getbbox(txt, stroke_width=stroke)
    im = Image.new('RGBA', (b[2] - b[0] + 8, b[3] - b[1] + 8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - b[0], 4 - b[1]), txt, font=f, fill=fill, stroke_width=stroke, stroke_fill=sfill)
    return im.resize((max(1, round(im.width * squeeze)), im.height), Image.LANCZOS) if squeeze != 1 else im

def fit(im, maxw):
    return im.resize((maxw, round(im.height * maxw / im.width)), Image.LANCZOS) if im.width > maxw else im

plate = Image.open(A('plate.png')).convert('RGB')
S = []
for i, st in enumerate(job['stories']):
    def crop_to_screen(path, focus):
        src = Image.open(P(path)).convert('RGB')
        ar = SW / SH
        if src.width / src.height > ar:
            ch = src.height; cw = round(ch * ar)
            x0 = int(min(max(focus * src.width - cw / 2, 0), src.width - cw))
            return src.crop((x0, 0, x0 + cw, ch))
        cw = src.width; ch = round(cw / ar); y0 = (src.height - ch) // 2
        return src.crop((0, y0, cw, y0 + ch))
    # one picture ("image"/"focus") or several ("images": [{"image","focus"}...]) shown in equal turns
    pics = st.get('images') or [{'image': st['image'], 'focus': st.get('focus', 0.5)}]
    crops = [crop_to_screen(p_['image'], p_.get('focus', 0.5)) for p_ in pics]
    S.append(dict(t=starts[i], img=crops[0], imgs=crops, tab=ctext(st['tab'], FB, 46, (255, 255, 255), 0.9),
                  hl=fit(ctext(st['headline'].upper(), FB, 66, (12, 12, 16)), 730),
                  tk=ctext(st['ticker'] * 3, FM, 30, (236, 238, 244), 0.92)))

K = 0.8
poses = [Image.open(A(f'pose{k}.png')) for k in range(5)]
poses = [p.resize((round(p.width * K), round(p.height * K)), Image.LANCZOS) for p in poses]
PX, PY = 464, 640
P_OFF = (560 * K, 27 * K)
live_img = ctext('LIVE', FBI, 44, (255, 255, 255), 0.95)
an_big = ctext('AN', FB, 86, (255, 255, 255), 0.9)
an_s1 = ctext('ACTUAL', FB, 26, (255, 255, 255), 0.95)
an_s2 = ctext('NEWS', FB, 26, (255, 255, 255), 0.95)
CAP = {}
def capword(t, hot):
    key = (t, hot)
    if key not in CAP:
        CAP[key] = ctext(t.upper(), FBL, 78, YEL if hot else (255, 255, 255), 1, stroke=9)
    return CAP[key]

def eio(x): x = min(max(x, 0), 1); return x * x * (3 - 2 * x)
def spring(x, k=14, d=9):
    return 0.0 if x <= 0 else 1 - math.exp(-d * x) * math.cos(k * x)

def story_at(t):
    i = 0
    for j, s in enumerate(S):
        if t >= s['t']: i = j
    return i, t - S[i]['t']

def pose_at(t):
    cur, ts = TL[0][1], 0
    for s, p in TL:
        if t >= s: cur, ts = p, s
    return cur, t - ts

def screen(t):
    i, dt = story_at(t)
    s = S[i]
    nxt = S[i + 1]['t'] if i + 1 < len(S) else DUR
    span = max(nxt - s['t'], 1)
    k = min(int(dt / span * len(s['imgs'])), len(s['imgs']) - 1)
    seg = span / len(s['imgs']); sdt = dt - k * seg
    z = 1.0 + 0.07 * min(sdt / seg, 1)
    src = s['imgs'][k]; w, h = src.width / z, src.height / z
    cx, cy = src.width / 2, src.height / 2
    im = src.transform((SW, SH), Image.EXTENT, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), Image.BICUBIC)
    im = ImageEnhance.Brightness(im).enhance(0.95 + 0.03 * math.sin(t * 7.3) * math.sin(t * 3.1))
    gl = Image.new('L', (SW, SH), 0)
    ImageDraw.Draw(gl).polygon([(0, 0), (SW * 0.45, 0), (SW * 0.15, SH), (0, SH)], fill=20)
    im = Image.composite(Image.new('RGB', (SW, SH), (255, 255, 255)), im, gl.filter(ImageFilter.GaussianBlur(30)))
    if (i > 0 or k > 0) and sdt < 0.22:
        im = Image.blend(im, Image.new('RGB', (SW, SH), (255, 255, 255)), 0.85 * (1 - sdt / 0.22))
    return im

def world(t):
    im = plate.copy()
    im.paste(screen(t), SCR[:2])
    p, dt = pose_at(t)
    a = poses[p]
    act = t > 0.1 and dt < 0.6
    pop = math.exp(-9 * dt) * math.cos(20 * dt) if act else 0
    sy, sx = 1 - 0.07 * pop, 1 + 0.04 * pop
    bob = 3 * math.sin(2 * math.pi * t / 2.5)
    aw, ah = round(a.width * sx), round(a.height * sy)
    a2 = a.resize((aw, ah), Image.BILINEAR) if (aw, ah) != a.size else a
    x = round(PX - P_OFF[0] * sx)
    y = round(DESK_Y - (DESK_Y - (PY - P_OFF[1])) * sy + bob)
    layer = Image.new('RGBA', im.size, (0, 0, 0, 0)); layer.paste(a2, (x, y), a2)
    sh_ = layer.split()[3].point(lambda v: v * 0.45).filter(ImageFilter.GaussianBlur(14))
    sdw = Image.new('L', im.size, 0); sdw.paste(sh_.crop((0, 0, W - 12, DESK_Y - 8)), (12, 8))
    im = Image.composite(Image.new('RGB', im.size, (0, 0, 0)), im, sdw)
    al = np.array(layer.split()[3]); al[DESK_Y:] = 0; layer.putalpha(Image.fromarray(al))
    im.paste(layer, (0, 0), layer)
    band = 230
    refl = layer.crop((0, DESK_Y - band, W, DESK_Y)).transpose(Image.FLIP_TOP_BOTTOM).filter(ImageFilter.GaussianBlur(3))
    ra = np.array(refl.split()[3]).astype(float) * np.linspace(0.22, 0, band)[:, None]
    refl.putalpha(Image.fromarray(ra.astype(np.uint8)))
    im.paste(refl, (0, DESK_Y + 2), refl)
    return im

def camera(im, t):
    i, dt = story_at(t)
    z = 1.012 + 0.04 * eio(t / DUR) + (0.015 * math.exp(-3 * dt) if i > 0 else 0)
    p, pdt = pose_at(t)
    cx = 540 + (14 * eio(pdt / 0.8) if p == 3 else 0) + 6 * math.sin(t * 0.63)
    cy = 952 + 4 * math.sin(t * 0.41)
    w, h = W / z, H / z
    return im.transform((W, H), Image.EXTENT, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), Image.BICUBIC)

def captions(im, t):
    active = [w for w in words if w['start'] <= t < w['chunk_end']]
    if not active: return im
    # the chunk containing these words
    for c in chunks:
        if active[0] in c: chunk = c; break
    shown = [w for w in chunk if w['start'] <= t]
    imgs, gap = [], 18
    for w in shown:
        hot = w['start'] <= t < max(w['end'], w['start'] + 0.15) and w is shown[-1]
        imgs.append((w, capword(w['text'], hot)))
    total = sum(i.width for _, i in imgs) + gap * (len(imgs) - 1)
    scale = min(1.0, 980 / total) if total else 1
    x = (W - total * scale) / 2
    yc = 1350
    for w, ci in imgs:
        age = t - w['start']
        s = scale * (0.82 + 0.18 * spring(age, 22, 12))
        ci2 = ci.resize((max(1, round(ci.width * s)), max(1, round(ci.height * s))), Image.BILINEAR)
        im.paste(ci2, (round(x + (ci.width * scale - ci2.width) / 2), round(yc - ci2.height / 2)), ci2)
        x += ci.width * scale + gap * scale
    return im

def ui(im, t):
    d = ImageDraw.Draw(im)
    if t > 0.2:
        k = spring(t - 0.2); x = round(-200 + 260 * k)
        d.rectangle((x, 286, x + 150, 344), fill=RED)
        d.ellipse((x + 14, 306, x + 32, 324), fill=(255, 255, 255) if int(t * 2) % 2 == 0 else (120, 10, 15))
        im.paste(live_img, (x + 40, 288), live_img)
    L, R = 40, 800
    i, dt = story_at(t)
    s = S[i]
    if t > 0.35:
        k = spring(dt - 0.35) if i == 0 else spring(dt + 0.02, 18, 10)
        tw = s['tab'].width + 44
        x = round(-tw - 40 + (L - 8 + tw + 40) * k)
        d.rectangle((x, 1424, x + tw, 1488), fill=TABC)
        im.paste(s['tab'], (x + 22, 1431), s['tab'])
    if t > 0.55:
        k = eio((t - 0.55) / 0.35)
        d.rectangle((L, 1488, round(L + (R - L) * k), 1592), fill=(250, 250, 250))
        d.rectangle((L, 1488, L + 8, 1592), fill=TABC)
        r = eio((dt - (0.8 if i == 0 else 0.15)) / 0.55)
        cw = round(s['hl'].width * r)
        if cw > 0:
            c = s['hl'].crop((0, 0, cw, s['hl'].height))
            im.paste(c, (L + 26, 1540 - s['hl'].height // 2), c)
    if t > 0.75:
        k = eio((t - 0.75) / 0.3)
        d.rectangle((L, 1592, L + round((R - L) * k), 1644), fill=(14, 16, 26))
        if k >= 1:
            tk = s['tk']
            off = round(max(dt - 0.3, 0) * 120) % (tk.width // 3)
            c = tk.crop((off, 0, off + (R - L - 30), tk.height))
            im.paste(c, (L + 16, 1600), c)
    if t > 0.9:
        k = max(spring(t - 0.9, 16, 8), 0.01)
        bw, bh = 128, 166
        box = Image.new('RGBA', (bw, bh), RED + (255,))
        box.paste(an_big, ((bw - an_big.width) // 2, 8), an_big)
        box.paste(an_s1, ((bw - an_s1.width) // 2, 104), an_s1)
        box.paste(an_s2, ((bw - an_s2.width) // 2, 132), an_s2)
        b2 = box.resize((max(1, round(bw * k)), max(1, round(bh * k))), Image.BILINEAR)
        im.paste(b2, (R + 12 + bw // 2 - b2.width // 2, 1478 + bh // 2 - b2.height // 2), b2)
    return captions(im, t)

def frame(t): return ui(camera(world(t), t), t)

print('story starts', [round(x, 2) for x in starts], 'dur', DUR)
print('poses', [(round(a, 2), b) for a, b in TL])
if STILLS:
    for t in STILLS: frame(t).save(os.path.join(os.path.dirname(os.path.abspath(OUT)), f'still_{t:.1f}.png'))
    sys.exit()
if COVER is not None:
    frame(COVER).save(OUT.rsplit('.', 1)[0] + '_cover.png')

silent = OUT.rsplit('.', 1)[0] + '_silent.mp4'
ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                       '-c:v', 'libx264', '-preset', 'slow', '-crf', '12', '-tune', 'animation', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', silent],
                      stdin=subprocess.PIPE)
for n in range(int(DUR * FPS)):
    ff.stdin.write(frame(n / FPS).tobytes())
ff.stdin.close(); ff.wait()

# audio: voice + sting at 0 (ducked) + whoosh at each story change
inputs = ['-i', silent, '-i', P(job['voice']), '-i', A('sting.mp3')]
mute = ''.join(f",volume=enable='between(t,{a:.3f},{b:.3f})':volume=0" for a, b in BLEEPS)
fl = [f'[1:a]aresample=48000{mute},apad[v]', '[2:a]aresample=48000,volume=0.22,afade=t=out:st=1.2:d=0.8[s]']
mix = ['[v]', '[s]']
for k, wt in enumerate(WHOOSH):
    inputs += ['-i', A('whoosh.mp3')]
    ms = max(int((wt - 0.12) * 1000), 0)
    fl.append(f'[{3 + k}:a]aresample=48000,volume=0.45,adelay={ms}|{ms}[w{k}]')
    mix.append(f'[w{k}]')
if BLEEPS:
    on = '+'.join(f'between(t,{a:.3f},{b:.3f})' for a, b in BLEEPS)
    fl.append(f"sine=f=1000:sample_rate=48000:d={DUR},volume='0.8*gt({on},0)':eval=frame[bp]")
    mix.append('[bp]')
fl.append(''.join(mix) + f'amix=inputs={len(mix)}:normalize=0:duration=first,atrim=0:{DUR},alimiter=limit=0.95[a]')
subprocess.run(['ffmpeg', '-y', '-loglevel', 'error'] + inputs + ['-filter_complex', ';'.join(fl), '-map', '0:v', '-map', '[a]',
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', OUT], check=True)
os.remove(silent)
print('done', OUT, DUR)
