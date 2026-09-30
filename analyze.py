#!/usr/bin/env python3
"""Every number the page shows: data/citations_usc.csv -> data/bundle.json.

Per block (street + hundred block): where it is on the map, which tickets it gets
and when; per side, the street-sweeping schedule; for metered blocks, how often
an officer walks by in each half hour of each weekday. Plus the campus-wide
charts. See README.md for the method.
"""
import json
import math

from scipy.optimize import brentq

from citations import *

MIN_TIX = 20          # a block needs this many tickets in the window to get a panel
SWEEP_MIN = 8         # sweeping tickets on one side before we match it to a posted route
METER_MIN = 20        # meter tickets before we model a block's patrols
VISIT_GAP = 10        # minutes: meter tickets closer than this on one block = one officer visit
SEP_MIN, CONS_MIN = 4.0, 0.75  # metres / share: when odd and even addresses sit on clear sides
SLOT0, NSLOT = 8 * 60, 24      # half hours from 8:00 am to 8:00 pm (meter hours)

d = load()
END = end_date(d)
F = frame()
MPP = F["m_per_px"]
d["x"], d["y"] = to_px(d.lat.values, d.lon.values)
d["sweep"] = d.viol.isin(SWEEP)
code_viol = d.violation_code.fillna("") + "|" + d.viol   # name each code and description pair once
names_of = {cv: kind_label(*cv.split("|", 1)) for cv in code_viol.unique()}
d["kind"] = np.where(d.meter, "Meter expired", np.where(d.sweep, "Street cleaning", code_viol.map(names_of)))
START = END - WINDOW + pd.Timedelta(days=1)
w = d[(d.date >= START) & (d.date <= END)].copy()
hol = holidays(START, END)
if END > pd.Timestamp(TERMS[-1][1]):
    print(f"warning: USC term dates in citations.py stop at {TERMS[-1][1]}; add the next semester or meter stats lose class weeks")
print(f"{len(d):,} tickets; window {START.date()} to {END.date()}: {len(w):,}")


# ---------- block geometry: all years, for a steadier line ----------
geo = {}
a = d.dropna(subset=["street", "block", "lat"])
a = a[a.x.between(0, F["W"]) & a.y.between(0, F["H"])]
for (st, bl), g in a.groupby(["street", "block"]):
    if len(g) < 15:
        continue
    P = g[["x", "y"]].values
    keep = np.hypot(*(P - np.median(P, 0)).T) * MPP < 150
    g, P = g[keep], P[keep]
    if len(g) < 12:
        continue
    m = P.mean(0)
    v = np.linalg.svd(P - m, full_matrices=False)[2][0]
    t = (P - m) @ v
    lo, hi = np.quantile(t, [.03, .97])
    if (hi - lo) * MPP < 40:
        lo, hi = (lo + hi) / 2 - 20 / MPP, (lo + hi) / 2 + 20 / MPP
    nrm = np.array([-v[1], v[0]])
    off = (P - m) @ nrm * MPP
    e, o = off[g.side.values == "even"], off[g.side.values == "odd"]
    sgn, even_dir = 0, None
    if len(e) >= 8 and len(o) >= 8:
        sep = np.median(e) - np.median(o)
        cons = ((np.sign(sep) * e > 0).mean() + (np.sign(sep) * o < 0).mean()) / 2
        if abs(sep) >= SEP_MIN and cons >= CONS_MIN:
            sgn = int(np.sign(sep))
            even_dir = compass(*(nrm * sgn))
    p1, p2 = m + v * lo, m + v * hi
    geo[(st, bl)] = dict(seg=[round(float(z), 1) for z in (*p1, *p2)], nrm=[round(float(nrm[0]), 3), round(float(nrm[1]), 3)],
                         sgn=sgn, even=even_dir)

# ---------- per-block summary ----------
blocks, index = [], {}
for (st, bl), g in w.dropna(subset=["street", "block"]).groupby(["street", "block"]):
    if (st, bl) not in geo or len(g) < MIN_TIX:
        continue
    top = []
    for kind, h in g.groupby("kind"):
        wk = h[h.dow < 5]
        fines = h.fine[h.fine > 0]   # some handhelds write $0 on tickets that carry a fine
        top.append([kind, len(h), int(fines.median()) if len(fines) else 0,
                    int(h.dow.mode()[0]), usual_hours(h.mins), round(len(wk) / len(h), 2)])
    top.sort(key=lambda r: -r[1])
    index[(st, bl)] = len(blocks)
    blocks.append(dict(id=f"{int(bl)}-{st.replace(' ', '-')}", street=nice_street(st), block=int(bl), n=len(g),
                       dow=np.bincount(g.dow, minlength=7).tolist(), top=top[:6], sweep=[], sweep_n=int(g.sweep.sum()), meter=None, mspaces=0,
                       **geo[(st, bl)]))
print(len(blocks), "blocks with a panel;", sum(b["even"] is not None for b in blocks), "with a known even side")

# ---------- metered spaces per block, from LADOT's meter inventory ----------
# Tickets alone miss metered blocks whose meters never drew a ticket (3800 Figueroa, 2600 Vermont).
inv = pd.read_csv(METERS, dtype=str)
p = inv.blockface.map(parse_loc)
inv = inv.assign(num=p.str[0], street=p.str[1]).dropna(subset=["street"])
names = pd.concat([d.street.dropna(), inv.street])
inv["street"] = inv.street.map(dict(zip(names, canonical_streets(names))))
inv["block"] = inv.num.astype(int) // 100 * 100
for key, n in inv.groupby(["street", "block"]).size().items():
    if key in index:
        blocks[index[key]]["mspaces"] = int(n)
print(sum(b["mspaces"] > 0 for b in blocks), "blocks with metered spaces")

# ---------- street sweeping, per side ----------
# The posted day, weeks and time come from StreetsLA's route list. The tickets add which route day
# covers each side (the one most of that side's tickets sit inside, on their weekday) and when they come.
routes = load_routes()
sc = w[w.sweep].dropna(subset=["street", "block"]).reset_index(drop=True)
program_days = set(sc.date.unique())  # a weekday with no sweeping ticket anywhere nearby = program off
covers = np.array([inside(sc.lon, sc.lat, r["rings"]) for r in routes])  # route day x ticket
phase_hits = phase_all = off_time = 0
for (st, bl, side), x in sc.groupby(["street", "block", "side"]):
    if len(x) < SWEEP_MIN or (st, bl) not in index:
        continue
    dow = int(x.dow.mode()[0])
    x = x[x.dow == dow]
    votes = [covers[i, x.index].sum() if r["dow"] == dow else 0 for i, r in enumerate(routes)]
    if max(votes) == 0:
        print(f"warning: no posted route covers {bl} {st} ({side} side) on its sweeping weekday; no schedule shown")
        continue
    r = routes[int(np.argmax(votes))]
    nth = (x.date.dt.day - 1) // 7 + 1
    phase_hits += int(nth.isin([r["on"], r["on"] + 2]).sum())
    phase_all += len(x)
    posted = x.mins.between(r["s0"], r["s1"])
    off_time += int((~posted).sum())
    x = x[posted]
    cand = pd.date_range(START, END)
    cand = cand[(cand.dayofweek == dow) & ~cand.isin(list(hol)) & cand.isin(list(program_days))]
    on_days = cand[np.isin((cand.day - 1) // 7 + 1, [r["on"], r["on"] + 2])]
    tdays = set(x.date.unique())
    day = x.groupby("date").mins
    blocks[index[(st, bl)]]["sweep"].append(dict(
        side=side, route=r["route"], dow=dow, on=r["on"], s0=r["s0"], s1=r["s1"],
        rate=round(float(pd.Index(on_days).isin(tdays).mean()), 2), days=len(on_days),
        arr=sorted((day.min() - r["s0"]).tolist()), last=sorted((day.max() - r["s0"]).tolist()), n=len(x), tpd=round(len(x) / max(1, len(tdays)), 1)))
for b in blocks:
    b["sweep"].sort(key=lambda s: s["side"])
print("sweeping tickets on the posted weeks:", round(phase_hits / phase_all, 3))
print(f"sweeping tickets outside the posted time (left off the arrival strips): {off_time} of {phase_all}")

# ---------- meters, per block (class weeks) ----------
m = w[w.meter].dropna(subset=["street", "block"])
# Timestamps, like the holidays: pandas 1.3's unique() gives numpy datetime64 values, which never match one
mdays = pd.Index(sorted(set(m.date.drop_duplicates()) - hol))
mdays = mdays[mdays.dayofweek < 6]
term_days = mdays[in_term(mdays)]
ndow = [max(1, int((term_days.dayofweek == k).sum())) for k in range(6)]
for (st, bl), x in m.groupby(["street", "block"]):
    if len(x) < METER_MIN or (st, bl) not in index:
        continue
    xt = x[x.date.isin(term_days)].sort_values("ts")
    if len(xt) < 12:
        continue
    vid = ((xt.ts.diff().dt.total_seconds() / 60 > VISIT_GAP) | (xt.date != xt.date.shift())).cumsum()
    v = xt.groupby(vid).agg(date=("date", "first"), start=("mins", "min"), k=("mins", "size"))
    kbar = v.k.mean()
    # expired cars found per visit ~ Poisson(lam); visible visits are the zero-truncated ones
    lam = brentq(lambda l: l / (1 - math.exp(-l)) - kbar, 1e-6, 50) if kbar > 1.001 else 0.05
    c = min(1 / (1 - math.exp(-lam)), 4.0)
    v = v[(v.start >= SLOT0) & (v.start < SLOT0 + NSLOT * 30)]
    slot, dw = ((v.start - SLOT0) // 30).values.astype(int), v.date.dt.dayofweek.values
    rates = [[round(n / ndow[k], 4) for n in np.bincount(slot[dw == k], minlength=NSLOT)] for k in range(6)]
    blocks[index[(st, bl)]]["meter"] = dict(n=len(x), visits=len(v), kbar=round(kbar, 2), c=round(c, 2),
                                            spaces=int(x.meter_id.nunique()), r=rates)
print(sum(b["meter"] is not None for b in blocks), "metered blocks modelled")

# ---------- campus-wide: one block's Tuesdays ----------
one = sc[(sc.street == "28TH ST") & (sc.block == 600) & (sc.side == "even")]
cal = [[str(t.date()), int((t.day - 1) // 7 + 1), int((one.date == t).sum()), int(t in hol)]
       for t in pd.date_range(START, END, freq="W-TUE")]
lags = [a for b in blocks for s in b["sweep"] if s["n"] >= 20 for a in s["arr"]]
lag_hist = np.bincount(np.clip(np.array(lags) // 10, 0, 12), minlength=13).tolist()

# ---------- campus-wide: meter tickets per hour ----------
m["hr"] = m.mins // 60
heat = {}
for lab, dset in (("term", term_days), ("off", mdays.difference(term_days))):
    x = m[m.date.isin(dset)]
    cnt = x.groupby(["dow", "hr"]).size().unstack(fill_value=0).reindex(index=range(6), columns=range(8, 20), fill_value=0)
    nd = pd.Series(dset.dayofweek).value_counts().reindex(range(6), fill_value=1)
    heat[lab] = cnt.div(nd.values, axis=0).round(2).values.tolist()

# built: the day this ran, in LA, for the page's "Last updated" (the tickets themselves end a few days earlier)
bundle = dict(frame=dict(W=F["W"], H=F["H"], mpp=round(MPP, 3)), start=str(START.date()), end=str(END.date()),
              built=str(pd.Timestamp.now(tz="America/Los_Angeles").date()), total=len(d), window=len(w), phase=round(phase_hits / phase_all, 3),
              share=dict(sweep=round(float(w.sweep.mean()), 3), meter=round(float(w.meter.mean()), 3)),
              fines=dict(sweep=int(w[w.sweep].fine.median()), meter=int(m.fine.median())),
              rules=dict(sweep_min=SWEEP_MIN, min_tix=MIN_TIX),
              blocks=blocks, cal=cal, lag_hist=lag_hist, heat=heat)
out = DATA / "bundle.json"
out.write_text(json.dumps(bundle, separators=(",", ":")))
print(out, f"{out.stat().st_size // 1024} KB")
