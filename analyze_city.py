#!/usr/bin/env python3
"""Every number LA Street Rules shows: data/city/ -> docs/streets/data/ (not committed).

Tickets are matched to the city's street centerlines by address: the street name, direction
and suffix pick the street, the house number picks the segment whose address range holds it,
and the ticket's own coordinates settle streets whose names repeat across the city. A block is
one street's hundred block; its line is cut from the centerline segments by address.

Per block the page gets the same things the USC page shows: the sweeping schedule per side,
from StreetsLA's posted routes (tickets only decide which side gets which route day); for metered blocks, officer visits and the chance of a ticket if unpaid;
and every kind of ticket written there. The blocks are split into map cells of about 1 km, so the
page only loads the few cells it is showing. The weekly workflow publishes docs/streets/data/
as a release asset instead of committing it.
"""
import collections
import glob
import json
import math
import re
import shutil

from scipy.optimize import brentq

from citations import *

CITY = DATA / "city"
OUT = DOCS / "streets" / "data"
CELL = 1024           # map cell edge in zoom-17 Web Mercator pixels (1 px is about 1 m in LA)
SWEEP_MIN = 8         # sweeping tickets on one side before we match it to a posted route
METER_MIN = 20        # meter tickets before we model a block's patrols
KIND_MIN = 50         # tickets before a kind gets its own dot chart on a block (its top kind always has one)
VISIT_GAP = 10        # minutes: meter tickets closer than this on one block = one officer visit
SLOT0, NSLOT = 8 * 60, 24   # half hours from 8:00 am to 8:00 pm (meter hours)
FAR = 600             # metres: a match this far from the ticket's own coordinates is treated as wrong
SPLIT = 800           # metres: parts of one street + hundred block this far apart are separate places

# ticket spellings -> the centerline's suffixes and directions
CL_SUFFIX = {"AV": "AVE", "AVENUE": "AVE", "STR": "ST", "STREET": "ST", "BL": "BLVD", "BLV": "BLVD", "BOULEVARD": "BLVD",
             "DRIVE": "DR", "PLACE": "PL", "RD": "ROAD", "WY": "WAY", "LN": "LANE", "COURT": "CT", "TERR": "TER",
             "TERRACE": "TER", "CIRCLE": "CIR", "HIGHWAY": "HWY", "TRL": "TR", "TRAIL": "TR", "PARKWAY": "PKWY",
             "PLZ": "PZ", "PLAZA": "PZ"}
CL_DIRS = {"N": "N", "S": "S", "E": "E", "W": "W", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
NICE_SUFFIX = {"ROAD": "Rd", "LANE": "Ln", "TR": "Trl", "PZ": "Plaza", "CK": "Creek"}
NICE_SFXDIR = {"(S/R)": "(south roadway)", "(N/R)": "(north roadway)"}   # Exposition Blvd's two roadways


def z17(lon, lat):
    """Zoom-17 Web Mercator pixels, the same grid as OpenStreetMap's tiles."""
    n = 2 ** 17 * 256
    r = np.radians(np.asarray(lat, dtype=float))
    with np.errstate(invalid="ignore"):   # missing coordinates stay NaN
        return (np.asarray(lon, dtype=float) + 180) / 360 * n, (1 - np.log(np.tan(r) + 1 / np.cos(r)) / math.pi) / 2 * n


def lonlat(x, y):
    """Zoom-17 Web Mercator pixels back to longitude and latitude."""
    n = 2 ** 17 * 256
    return np.asarray(x) / n * 360 - 180, np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * np.asarray(y) / n))))


PX_M = 156543.03392 * math.cos(math.radians(34.05)) / 2 ** 17   # metres per zoom-17 pixel in LA

# ---------- street centerlines ----------
feats = json.loads((CITY / "centerlines.json").read_text())["features"]
segs = []
for f in feats:
    p, g = f["properties"], f["geometry"]
    if not g or not (p.get("STNAME") or "").strip():
        continue
    parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    pts = np.array([c for ln in parts for c in ln])
    x, y = z17(pts[:, 0], pts[:, 1])
    rl = (p["ADLF"] or 0, p["ADLT"] or 0)
    rr = (p["ADRF"] or 0, p["ADRT"] or 0)
    nums = [v for v in (*rl, *rr) if v]
    segs.append(dict(
        key=((p["TDIR"] or "").strip(), p["STNAME"].strip().upper(), (p["STSFX"] or "").strip().upper(), (p["SFXDIR"] or "").strip().upper()),
        l=rl, r=rr, lo=min(nums) if nums else 0, hi=max(nums) if nums else 0,
        a=p["INT_ID_FROM"], b=p["INT_ID_TO"], zip=p["ZIP_L"] or p["ZIP_R"] or 0, xy=np.c_[x, y]))
MID = np.array([s["xy"].mean(0) for s in segs])
KEYS = sorted({s["key"] for s in segs})
KEY_IX = {k: i for i, k in enumerate(KEYS)}
SID = np.array([KEY_IX[s["key"]] for s in segs])
by_name = collections.defaultdict(list)
for i, s in enumerate(segs):
    by_name[s["key"][1]].append(i)
SUFFIXES_CL = {s["key"][2] for s in segs if s["key"][2]}


def street_name(key):
    tdir, name, sfx, sdir = key
    sfx = NICE_SUFFIX.get(sfx, sfx.title())
    return " ".join(t for t in (tdir, nice_street(name), sfx, NICE_SFXDIR.get(sdir, sdir.title())) if t)


def parse_city(loc):
    """'3601 VERMONT AV S' -> (3601, 'VERMONT', 'AVE', 'S'); intersections and blanks -> None."""
    m = re.match(r"^\s*(\d{1,6})[A-Z]?\s+(.+)$", loc) if isinstance(loc, str) else None
    if not m:
        return None
    toks = [t for t in re.split(r"[\s.]+", m.group(2).upper()) if t]
    d = sfx = None
    if len(toks) > 1 and toks[0] in CL_DIRS:
        d = CL_DIRS[toks.pop(0)]
    if len(toks) > 1 and toks[-1] in CL_DIRS:
        d = CL_DIRS[toks.pop()]
    if len(toks) > 1 and CL_SUFFIX.get(toks[-1], toks[-1]) in SUFFIXES_CL:
        sfx = CL_SUFFIX.get(toks[-1], toks[-1])
        toks.pop()
    name = " ".join(toks)
    name = re.sub(r"^AVE? (\d+)$", r"AVENUE \1", name)
    name = re.sub(r"^SAINT ", "ST ", re.sub(r"^MOUNT ", "MT ", name))
    return int(m.group(1)), name, sfx, d


def in_range(n, rng):
    a, b = rng
    return bool(a or b) and min(a, b) <= n <= max(a, b) and n % 2 in (a % 2, b % 2)


def match(num, name, sfx, d, x, y):
    """Index of the centerline segment for an address, or None; x, y are the ticket's own zoom-17 pixels."""
    c = by_name.get(name)
    if not c:
        return None
    c = [i for i in c if segs[i]["key"][2] == sfx] or c if sfx else c
    c = [i for i in c if d in (segs[i]["key"][0], segs[i]["key"][3][:1])] or c if d else c
    hit = [i for i in c if in_range(num, segs[i]["l"]) or in_range(num, segs[i]["r"])]
    if not hit:  # the number falls in a gap between ranges: the nearest segment of this street within 100 numbers
        hit = [i for i in c if segs[i]["lo"] and segs[i]["lo"] - 100 <= num <= segs[i]["hi"] + 100]
    if not hit:
        return None
    dist = np.hypot(MID[hit, 0] - x, MID[hit, 1] - y) * PX_M if np.isfinite(x) else np.zeros(len(hit))
    k = int(np.argmin(dist))
    return hit[k] if dist[k] <= FAR else None


# ---------- tickets in the window ----------
cols = ["issue_date", "issue_time", "location", "violation_code", "violation_description", "fine_amount", "loc_lat", "loc_long"]
d = pd.concat([pd.read_csv(f, dtype=str, usecols=cols) for f in sorted(glob.glob(str(CITY / "tickets" / "*.csv")))], ignore_index=True)
d["date"] = pd.to_datetime(d.issue_date.str[:10])
# a month that came back thin means a broken download: stop rather than publish a hole (the last month is still filling in)
per_month = d.groupby(d.date.dt.to_period("M")).size().iloc[:-1]
thin = per_month[per_month < 0.6 * per_month.median()]
if len(thin):
    raise SystemExit("error: too few tickets in " + ", ".join(f"{m} ({n:,})" for m, n in thin.items()) + "; rerun fetch_city.py")
per_day = d.groupby("date").size()
END = per_day[per_day >= 1000].index.max()   # the feed has a few stray future dates
START = END - WINDOW + pd.Timedelta(days=1)
d = d[(d.date >= START) & (d.date <= END)]
total = len(d)
t = pd.to_numeric(d.issue_time, errors="coerce")
d = d[t.notna() & (t % 100 < 60) & (t < 2400)].copy()
t = t.loc[d.index].astype(int)
d["mins"] = (t // 100) * 60 + t % 100
d["dow"] = d.date.dt.dayofweek
print(f"{total:,} tickets from {START.date()} to {END.date()}")

# match each distinct address once
u = d.groupby("location").agg(lat=("loc_lat", "first"), lon=("loc_long", "first")).reset_index()
ux, uy = z17(pd.to_numeric(u.lon, errors="coerce"), pd.to_numeric(u.lat, errors="coerce"))
seg_of, num_of = {}, {}
for loc, x, y in zip(u.location, ux, uy):
    p = parse_city(loc)
    if p and (i := match(*p, x, y)) is not None:
        seg_of[loc], num_of[loc] = i, p[0]
d["seg"] = d.location.map(seg_of)
d = d[d.seg.notna()].copy()
d["seg"] = d.seg.astype(int)
d["num"] = d.location.map(num_of).astype(int)
print(f"{len(d):,} tickets ({len(d) / total:.1%}) matched to a street by address")

# ---------- blocks: street + hundred, split where the same name repeats in another part of the city ----------
d["street"] = SID[d.seg.values]
d["hund"] = d.num // 100 * 100
parts = {}   # (street, hundred) -> [[centre xy, [segment ids]], ...]
for (st, h), ss in d.groupby(["street", "hund"]).seg.unique().items():
    groups = []
    for i in ss:
        for gr in groups:
            if np.hypot(*(gr[0] - MID[i])) * PX_M < SPLIT:
                gr[1].append(i)
                break
        else:
            groups.append([MID[i], [i]])
    parts[(st, h)] = groups
part_of = {(st, h, i): k for (st, h), groups in parts.items() for k, gr in enumerate(groups) for i in gr[1]}
blk = [(st, h, part_of[(st, h, i)]) for st, h, i in zip(d.street, d.hund, d.seg)]
blocks = sorted(set(blk), key=lambda b: (street_name(KEYS[b[0]]), b[1], b[2]))
bix = {b: k for k, b in enumerate(blocks)}
d["b"] = [bix[b] for b in blk]
by_street = collections.defaultdict(list)
for i, k in enumerate(SID):
    by_street[k].append(i)
print(f"{len(blocks):,} blocks, {sum(1 for b in blocks if b[2]):,} of them a second place with the same street name and hundred")


def cut(s, h):
    """The piece of a segment's line whose addresses fall in hundred block h."""
    f = np.mean([v for v in (s["l"][0], s["r"][0]) if v] or [0])
    t = np.mean([v for v in (s["l"][1], s["r"][1]) if v] or [0])
    xy = s["xy"]
    if f == t or (h <= s["lo"] and s["hi"] < h + 100):
        return xy
    p0, p1 = sorted(np.clip([(h - f) / (t - f), (h + 100 - f) / (t - f)], 0, 1))
    seg_len = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.r_[0, np.cumsum(seg_len)]
    at = lambda q: np.array([np.interp(q * cum[-1], cum, xy[:, 0]), np.interp(q * cum[-1], cum, xy[:, 1])])
    inner = xy[(cum > p0 * cum[-1]) & (cum < p1 * cum[-1])]
    return np.vstack([at(p0), inner, at(p1)])


# names at each intersection, for "between W 37th St and W 38th St"
at_int = collections.defaultdict(collections.Counter)
for s in segs:
    for n in (s["a"], s["b"]):
        at_int[n][s["key"]] += 1


def cross(st, n):
    """The busiest other street at intersection n (not this street's own continuation, like N and S Vermont)."""
    other = [(c, k) for k, c in at_int[n].items() if k[1] != KEYS[st][1]]
    return max(other)[1] if other else None


names, name_ix = [], {}
def nix(key):
    key = KEYS[key] if isinstance(key, (int, np.integer)) else key
    if key not in name_ix:
        name_ix[key] = len(names)
        names.append(street_name(key))
    return name_ix[key]


geo = []
for st, h, k in blocks:
    centre, matched = parts[(st, h)][k]
    own = [i for i in by_street[st] if segs[i]["lo"] <= h + 99 and segs[i]["hi"] >= h and np.hypot(*(MID[i] - centre)) * PX_M < SPLIT]
    own = own or matched
    lines = [cut(segs[i], h) for i in own]
    # cross streets: chain ends whose address sits in (or up to 50 numbers past) this hundred, low end first
    ends = collections.Counter(n for i in own for n in (segs[i]["a"], segs[i]["b"]))
    tips = []
    for i in own:
        s = segs[i]
        for n, addr in ((s["a"], np.mean([v for v in (s["l"][0], s["r"][0]) if v] or [-1])), (s["b"], np.mean([v for v in (s["l"][1], s["r"][1]) if v] or [-1]))):
            if ends[n] == 1 and h - 50 <= addr < h + 150 and (c := cross(st, n)):
                tips.append((addr, nix(c)))
    tips.sort()
    between = [tips[0][1], tips[-1][1]] if len(tips) >= 2 and tips[0][1] != tips[-1][1] else [tips[0][1]] if tips else []
    # which compass side the even numbers are on, weighted by length
    votes = collections.Counter()
    for i in own:
        s = segs[i]
        dx, dy = s["xy"][-1] - s["xy"][0]
        even_left = s["l"][0] % 2 == 0 if s["l"][0] else s["r"][0] % 2 == 1 if s["r"][0] else None
        if even_left is not None and (dx or dy):
            votes[compass(dy, -dx) if even_left else compass(-dy, dx)] += math.hypot(dx, dy)
    allp = np.vstack(lines)
    geo.append(dict(lines=lines, between=between, even=votes.most_common(1)[0][0] if votes else "",
                    cell=tuple((allp.mean(0) // CELL).astype(int)), zip=int(collections.Counter(segs[i]["zip"] for i in own).most_common(1)[0][0])))

# ---------- what gets ticketed, per block ----------
viol = d.violation_description.fillna("?").str.strip()
d["meter"] = d.violation_code.fillna("").str.replace(".", "", regex=False).str.startswith(METER)
d["sweep"] = viol.isin(SWEEP)
code_viol = d.violation_code.fillna("") + "|" + viol   # name each code and description pair once
names_of = {cv: kind_label(*cv.split("|", 1)) for cv in code_viol.unique()}
d["kind"] = np.where(d.meter, "Meter expired", np.where(d.sweep, "Street cleaning", code_viol.map(names_of)))
fine = pd.to_numeric(d.fine_amount, errors="coerce")
d["fine"] = fine.where(fine > 0)   # some handhelds write $0 on tickets that carry a fine
d["wk"] = d.dow < 5
kinds, kind_ix = [], {}
g = d.groupby(["b", "kind"])
top = pd.DataFrame({"n": g.size(), "fine": g.fine.median(), "wk": g.wk.mean()}).reset_index()
top = top.merge(d.groupby(["b", "kind", "dow"]).size().reset_index(name="c")
              .sort_values(["c", "dow"], ascending=[False, True], kind="mergesort").drop_duplicates(["b", "kind"])[["b", "kind", "dow"]], on=["b", "kind"])
top = top.sort_values(["b", "n"], ascending=[True, False])
print(f"{d.kind.nunique()} kinds of ticket; up to {top.groupby('b').size().max()} on one block")
hours = d.assign(hr=d.mins // 60).groupby(["b", "kind", "hr"]).size()
tops = collections.defaultdict(list)
for b, kind, n, fine, wk, dow in top[["b", "kind", "n", "fine", "wk", "dow"]].itertuples(index=False):
    if kind not in kind_ix:
        kind_ix[kind] = len(kinds)
        kinds.append(kind)
    hc = hours.loc[(b, kind)]
    tops[b].append([kind_ix[kind], int(n), int(fine) if fine == fine else 0, int(dow),
                    usual_hours(np.repeat(hc.index.values * 60, hc.values)), round(float(wk), 2)])

# For the card: the code LADOT writes a kind under most often, and a plain line about the rule
src = (d.groupby(["kind", d.violation_code.fillna("?"), viol]).size().reset_index(name="c")
       .sort_values("c", ascending=False))
CODE_CAP, DESC_CAP = 1, 0   # the card shows only the commonest code; the rest are counted, not listed
kind_src = []
for k in kinds:
    x = src[src.kind == k]
    cs = list(dict.fromkeys(x.violation_code))
    ds = list(dict.fromkeys(x.violation_description))
    kind_src.append([cs[:CODE_CAP], ds[:DESC_CAP], len(cs), len(ds)])
kind_note = [KIND_NOTES.get(k, "") for k in kinds]

# each kind's tickets by the half hour they were written (midnight = 0), for the dot charts: the block's
# most ticketed kind, plus any kind with KIND_MIN tickets there
want = pd.concat([top.drop_duplicates("b")[["b", "kind"]], top[top.n >= KIND_MIN][["b", "kind"]]]).drop_duplicates()
hh = (d[["b", "kind", "mins"]].merge(want, on=["b", "kind"])
      .assign(hf=lambda x: x.mins // 30).groupby(["b", "kind", "hf"]).size())
charts = collections.defaultdict(lambda: collections.defaultdict(dict))
for (b, kind, hf), c in hh.items():
    charts[b][kind][hf] = int(c)
print(f"{len(want):,} dot charts on {want.b.nunique():,} blocks")

# ---------- street sweeping, per side: the same rule as the USC page ----------
# The posted day, weeks and time come from StreetsLA's routes. Each route covers an area and is swept on two
# days, one per side of the street, and the list doesn't say which side is which, so a side gets the route
# day on its most common sweeping-ticket weekday, among the routes those tickets sit inside.
hol = holidays(START, END)
routes = load_routes(CITY / "sweep_routes.geojson", weekly=True)
sc = d[d.sweep]
program = set(sc.date.unique())   # a weekday with no sweeping ticket anywhere in the city = program off
days = pd.date_range(START, END)
days = days[~days.isin(list(hol)) & days.isin(list(program))]
nth_all = (days.day - 1) // 7 + 1
posted_days = {}                  # (weekdays, weeks) -> the dates that route day was on, as Timestamps
sc = sc.assign(side=np.where(sc.num % 2 == 0, "even", "odd"),
               lon=pd.to_numeric(sc.loc_long, errors="coerce"), lat=pd.to_numeric(sc.loc_lat, errors="coerce"))
size = sc.groupby(["b", "side"]).size()
sc = sc.set_index(["b", "side"]).loc[size[size >= SWEEP_MIN].index].reset_index()
mode = (sc.groupby(["b", "side", "dow"]).size().reset_index(name="c")
        .sort_values(["c", "dow"], ascending=[False, True], kind="mergesort").drop_duplicates(["b", "side"]))
sc = sc.merge(mode[["b", "side", "dow"]].rename(columns={"dow": "mode"}), on=["b", "side"])
sc["g"] = sc.b * 2 + (sc.side == "odd")
# votes: for each route day, how many of each side's tickets on its most common weekday sit inside it
best = {}                         # side -> (tickets inside, route index); the first route wins a tie, as on the USC page
on_mode = sc[sc.dow == sc["mode"]]
for dow, x in on_mode.groupby("dow"):
    lon, lat, g = x.lon.values, x.lat.values, x.g.values
    for i, r in enumerate(routes):
        if r["dow"] != dow:
            continue
        hit = inside(lon, lat, r["rings"])
        for side, c in zip(*np.unique(g[hit], return_counts=True)):
            if c > best.get(side, (0,))[0]:
                best[side] = (c, i)
sweeps = collections.defaultdict(list)
unrouted = collections.Counter()  # block -> sides with enough tickets but no posted route around them
phase_hits = phase_all = off_time = in_route = 0
# Where the tickets on a side's sweep weekday fall: in its posted weeks, in the other pair, or on a 5th week, when no
# every-other-week route is swept. Per category: [tickets, days with tickets, days], holidays left out.
alldays = pd.date_range(START, END)
alldays = alldays[~alldays.isin(list(hol))]
weeks_n = {"posted": [0, 0, 0], "other": [0, 0, 0], "fifth": [0, 0, 0]}
year2 = START + pd.DateOffset(years=1)
off_first = 0                     # tickets outside the posted weeks in the window's first year (routes that changed?)
example = None                    # the side with the most sweeping tickets on a one-day, every-other-week route; its
                                  # calendar counts like its card: on the sweep weekday, in the posted hours
for (b, side), x in sc.groupby(["b", "side"]):
    n_all = len(x)
    key = b * 2 + (side == "odd")
    if key not in best:
        unrouted[b] += 1
        continue
    r = routes[best[key][1]]
    x = x[x.dow.isin(r["dows"])]
    if r["on"]:   # every-week routes can't miss a week, so they stay out of the week-pair check
        nth = (x.date.dt.day - 1) // 7 + 1
        phase_hits += int(nth.isin([r["on"], r["on"] + 2]).sum())
        phase_all += len(x)
    posted = x.mins.between(r["s0"], r["s1"])
    off_time += int((~posted).sum())
    in_route += len(x)
    x = x[posted]
    # how many of those fell in the posted weeks: signs often show only the weekday, and the card says how
    # many of the tickets on that weekday came in the swept weeks (every-week routes: all of them)
    in_weeks = int(((x.date.dt.day - 1) // 7 + 1).isin([r["on"], r["on"] + 2]).sum()) if r["on"] else len(x)
    if r["on"]:
        pair = [r["on"], r["on"] + 2]
        wd = alldays[np.isin(alldays.dayofweek, r["dows"])]
        wn = (wd.day - 1) // 7 + 1
        cat_days = {"posted": set(wd[np.isin(wn, pair)]), "fifth": set(wd[wn == 5])}
        cat_days["other"] = set(wd) - cat_days["posted"] - cat_days["fifth"]
        tn = ((x.date.dt.day - 1) // 7 + 1).values
        tcat = np.where(np.isin(tn, pair), "posted", np.where(tn == 5, "fifth", "other"))
        for c, dset in cat_days.items():
            sel = x[tcat == c]
            weeks_n[c][0] += len(sel)
            weeks_n[c][1] += len(set(sel.date) & dset)
            weeks_n[c][2] += len(dset)
        off_first += int(((tcat != "posted") & (x.date < year2).values).sum())
        if len(r["dows"]) == 1 and (example is None or n_all > example[0]):
            example = (n_all, b, side, r, x)
    pk = (tuple(r["dows"]), r["on"])
    if pk not in posted_days:
        weeks = [1, 2, 3, 4, 5] if r["on"] == 0 else [r["on"], r["on"] + 2]
        posted_days[pk] = set(days[np.isin(days.dayofweek, r["dows"]) & np.isin(nth_all, weeks)])
    cd, tdays = posted_days[pk], set(x.date)   # both Timestamps; numpy datetime64 values would never match
    sweeps[b].append([side, r["dows"], r["s0"], r["s1"], r["on"], round(len(tdays & cd) / max(1, len(cd)), 2), len(cd), len(x), len(tdays), in_weeks])
print(f"{sum(len(v) for v in sweeps.values()):,} block sides matched to a posted sweeping route;",
      f"{sum(unrouted.values()):,} sides with enough tickets but no posted route around them")
print(f"{phase_hits / phase_all:.1%} of their sweeping tickets on the posted weeks;",
      f"{off_time:,} of {in_route:,} outside the posted time (not counted)")
wp, wo, wf = weeks_n["posted"], weeks_n["other"], weeks_n["fifth"]
print(f"tickets on a side's sweep weekday: {wp[0]:,} in its posted weeks, {wo[0]:,} in the other pair, {wf[0]:,} on 5th weeks",
      f"({off_first:,} of the {wo[0] + wf[0]:,} outside the posted weeks in the window's first year);",
      f"days with tickets: {wp[1] / max(1, wp[2]):.1%} of posted, {wo[1] / max(1, wo[2]):.2%} of other-pair,",
      f"{wf[1] / max(1, wf[2]):.2%} of 5th-week days")

# The page's calendar: every one of that side's sweep weekdays over the window, as [date, nth of the month,
# its sweeping tickets that day, holiday]; the block's name and sides are added when the cells are written.
cal = None
if example:
    _, cb, cside, cr, ct = example
    per_day = ct.groupby("date").size()
    wdays = pd.date_range(START, END)
    wdays = wdays[wdays.dayofweek == cr["dows"][0]]
    cal = dict(block=int(cb), side=cside, dow=int(cr["dows"][0]), on=int(cr["on"]), s0=int(cr["s0"]), s1=int(cr["s1"]),
               n=len(ct), rows=[[str(t.date()), int((t.day - 1) // 7 + 1), int(per_day.get(t, 0)), int(t in hol)] for t in wdays])

# A block with no side matched to a route still gets the posted route days around it, so the card can say
# which days the routes give it (without which side is which) or that no route covers it. The test points
# sit 6 m off each side of the middle of each piece of the block's line: streets often form a route's edge,
# with another route across, and a block can run past a route's end.
OFF = 6 / PX_M
px, py, owner = [], [], []
for k in range(len(blocks)):
    if sweeps.get(k):
        continue
    for ln in geo[k]["lines"]:
        seg = np.hypot(*np.diff(ln, axis=0).T)
        if not seg.sum():
            continue
        cum = np.r_[0, np.cumsum(seg)]
        j = min(int(np.searchsorted(cum, cum[-1] / 2, side="right")) - 1, len(seg) - 1)
        mid = ln[j] + (cum[-1] / 2 - cum[j]) / seg[j] * (ln[j + 1] - ln[j])
        ux, uy = (ln[j + 1] - ln[j]) / seg[j]
        for sgn in (1, -1):
            px.append(mid[0] - sgn * uy * OFF)
            py.append(mid[1] + sgn * ux * OFF)
            owner.append(k)
plon, plat = lonlat(px, py)
owner = np.array(owner, int)
areas = collections.defaultdict(list)   # a Monday-to-Friday route is five route days over one area: test it once
for r in routes:
    areas[b"".join(q.tobytes() for q in r["rings"])].append(r)
around = collections.defaultdict(set)   # block -> {(weekdays, start, end, weeks)}
for rs in areas.values():
    for k in set(owner[inside(plon, plat, rs[0]["rings"])].tolist()):
        around[k].update((tuple(r["dows"]), r["s0"], r["s1"], r["on"]) for r in rs)
print(f"{len(around):,} of {len(set(owner.tolist())):,} blocks without a matched side sit in a posted route")

# ---------- meters: officer visits per half hour, Monday to Saturday ----------
m = d[d.meter].sort_values(["b", "date", "mins"])
cnt = m.groupby("b").size()
m = m[m.b.isin(cnt[cnt >= METER_MIN].index)]
# Timestamps, like the holidays: pandas 1.3's unique() gives numpy datetime64 values, which never match one
mdays = pd.Index(sorted(set(m.date.drop_duplicates()) - hol))
mdays = mdays[mdays.dayofweek < 6]
ndow = np.array([max(1, int((mdays.dayofweek == k).sum())) for k in range(6)])
m = m[m.date.isin(mdays)]
new = (m.b != m.b.shift()) | (m.date != m.date.shift()) | (m.mins - m.mins.shift() > VISIT_GAP)
m = m.assign(visit=new.cumsum())
v = m.groupby("visit").agg(b=("b", "first"), dow=("dow", "first"), start=("mins", "min"), k=("mins", "size"))
meters = {}
for b, x in v.groupby("b"):
    if len(x) < 8:
        continue
    kbar = x.k.mean()
    # expired cars found per visit ~ Poisson(lam); visible visits are the zero-truncated ones
    lam = brentq(lambda l: l / (1 - math.exp(-l)) - kbar, 1e-6, 50) if kbar > 1.001 else 0.05
    c = min(1 / (1 - math.exp(-lam)), 4.0)
    x = x[(x.start >= SLOT0) & (x.start < SLOT0 + NSLOT * 30)]
    r = np.zeros((6, NSLOT))
    np.add.at(r, (x.dow.values, ((x.start - SLOT0) // 30).values.astype(int)), 1)
    r /= ndow[:, None]
    wkday = r[:5].mean(0)
    hi = int(np.argmax([wkday[j:j + 4].sum() for j in range(NSLOT - 3)]))
    meters[b] = [round(float(c * r[:5].sum() / 5), 1), hi, [int(round(100 * (1 - math.exp(-c * r[k].sum())))) for k in range(6)]]
print(f"{len(meters):,} metered blocks modelled")

# metered spaces from LADOT's inventory, matched the same way
inv = pd.read_csv(CITY / "meters.csv", dtype=str)
ll = inv.latlng.str.extract(r"\(([-\d.]+),\s*([-\d.]+)\)").astype(float)
ix, iy = z17(ll[1], ll[0])
spaces = collections.Counter()
seg_blocks = collections.defaultdict(list)
for k, b in enumerate(blocks):
    seg_blocks[(b[0], b[1])].append(k)
for bf, x, y in zip(inv.blockface, ix, iy):
    p = parse_city(bf)
    if p and (i := match(*p, x, y)) is not None:
        ks = seg_blocks.get((SID[i], p[0] // 100 * 100))
        if ks:
            spaces[min(ks, key=lambda k: np.hypot(*(parts[(blocks[k][0], blocks[k][1])][blocks[k][2]][0] - MID[i])))] += 1
print(f"{sum(spaces.values()):,} of {len(inv):,} metered spaces placed on a block")

# ---------- write cells and the index ----------
def slug(s):
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")


shutil.rmtree(OUT, ignore_errors=True)
(OUT / "cells").mkdir(parents=True)
n_by_block = d.groupby("b").size()
dup = collections.Counter((b[0], b[1]) for b in blocks)
cells = collections.defaultdict(list)
index, used = [], set()
for k, (st, h, part) in enumerate(blocks):
    gm = geo[k]
    cx, cy = gm["cell"]
    suffix = str(gm["zip"]) if dup[(st, h)] > 1 else ""
    if (st, h, suffix) in used:
        suffix += f"-{part}"
    used.add((st, h, suffix))
    bid = f"{h}-{slug(street_name(KEYS[st]))}" + (f"-{suffix}" if suffix else "")
    o = np.array([cx * CELL, cy * CELL])
    rec = dict(id=bid, s=nix(st), h=int(h), g=[np.round(ln - o).astype(int).ravel().tolist() for ln in gm["lines"]],
               x=gm["between"], e=gm["even"], n=int(n_by_block[k]), k=tops[k])
    if sweeps.get(k):
        rec["w"] = sweeps[k]
    if unrouted.get(k):
        rec["wu"] = unrouted[k]
    if around.get(k):
        rec["wr"] = sorted([list(dows), s0, s1, on] for dows, s0, s1, on in around[k])   # route days around it
    if meters.get(k):
        rec["m"] = meters[k]
    # per kind: [kind, first half hour, counts...] in the same order as the kinds above
    rec["hh"] = [[t[0], min(bins)] + [bins.get(j, 0) for j in range(min(bins), max(bins) + 1)]
                 for t in tops[k] if (bins := charts[k].get(kinds[t[0]]))]
    if spaces.get(k):
        rec["sp"] = spaces[k]
    cells[(cx, cy)].append(rec)
    index.append((rec["s"], int(h), f"{cx}_{cy}", suffix))
    if cal and k == cal["block"]:
        cal.update(id=bid, s=rec["s"], h=int(h), x=rec["x"], e=rec["e"])
sizes = []
for (cx, cy), recs in cells.items():
    body = json.dumps(recs, separators=(",", ":"))
    (OUT / "cells" / f"{cx}_{cy}.json").write_text(body)
    sizes.append(len(body))
# index.json loads with the page: dates, counts, street names and ticket kinds (cards refer to them by number), and
# which cells exist. streets.json loads when someone searches: for each street name, its blocks as
# [hundred, cell number] or [hundred, cell number, id suffix] where the street + hundred repeats elsewhere.
cell_keys = sorted(f"{cx}_{cy}" for cx, cy in cells)
cell_ix = {k: i for i, k in enumerate(cell_keys)}
streets = [[] for _ in names]
for s_ix, h, key, suffix in index:
    streets[s_ix].append([h, cell_ix[key], suffix] if suffix else [h, cell_ix[key]])
# built: the day this ran, in LA, for the page's "Last updated" (the tickets themselves end a few days earlier)
meta = dict(start=str(START.date()), end=str(END.date()), built=str(pd.Timestamp.now(tz="America/Los_Angeles").date()), total=total, matched=len(d), phase=round(phase_hits / phase_all, 3),
            cell=CELL, sweep_min=SWEEP_MIN, routes_around=True, chart_min=KIND_MIN,
            names=names, kinds=kinds, kind_src=kind_src, kind_note=kind_note, cells=cell_keys,
            weeks=dict(posted=wp, other=wo, fifth=wf, off_first_year=off_first))
if cal:
    del cal["block"]
    meta["cal"] = cal
(OUT / "index.json").write_text(json.dumps(meta, separators=(",", ":")))
(OUT / "streets.json").write_text(json.dumps(streets, separators=(",", ":")))
sizes = np.array(sizes)
print(f"{len(cells)} cells, {sizes.sum() / 2**20:.1f} MB; median {np.median(sizes) / 1024:.0f} KB, largest {sizes.max() / 1024:.0f} KB;",
      f"index.json {(OUT / 'index.json').stat().st_size / 1024:.0f} KB, streets.json {(OUT / 'streets.json').stat().st_size / 1024:.0f} KB")
