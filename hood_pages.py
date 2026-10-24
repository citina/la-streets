#!/usr/bin/env python3
"""LA Street Rules' neighborhood pages and the site's sitemap: docs/streets/neighborhoods/ and docs/sitemap.xml (not committed).

The map page shows a block at a # link (streets/#3600-S-VERMONT-AVE), and search engines read everything after the #
as the same page, so no block or neighborhood on the map can be found by a search. This writes a plain page for each
LA Times neighborhood: its posted sweeping schedules, what gets ticketed there, and every block's sweeping days, each
block linked into the map. Also a list of the neighborhoods, and a sitemap of every page for search engines.

It reads only what the map loads (docs/streets/data/ from the city-data release, and docs/streets/hoods.json) and
uses Python's standard library, so pages.yml runs it right after unpacking the release, on every deploy. The colors
come from the map page's own tokens. A block belongs to the neighborhood its middle falls in, as on the map's card.

    python3 hood_pages.py            # what pages.yml runs
    python3 hood_pages.py --data https://citina.github.io/la-streets/streets/data/ --only Koreatown,Venice
                                     # a local preview from the live site's data, a few neighborhoods only
"""
import argparse
import collections
import datetime
import json
import math
import re
import shutil
import urllib.request
from html import escape
from pathlib import Path

DOCS = Path(__file__).resolve().parent / "docs"
STREETS = DOCS / "streets"
OUT = STREETS / "neighborhoods"
SITE = "https://citina.github.io/la-streets/"
LOOKUP = '<a href="https://streets.lacity.gov/services/street-sweeping">streets.lacity.gov</a>'
ROUTES = "https://www.arcgis.com/home/item.html?id=0e16fa641a0846a3ae29bffb150314dc"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYLONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OPP = {"north": "south", "south": "north", "east": "west", "west": "east",
       "northeast": "southwest", "southwest": "northeast", "northwest": "southeast", "southeast": "northwest"}
SHOW_SCHED, SHOW_KINDS, SHOW_BUSY = 8, 6, 5
FEW = 20    # sweeping tickets under which the card says "Limited data" about a side's route day


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def reader(src):
    """index.json and cells from a folder, or (for a preview) from the live site."""
    if src.startswith(("http://", "https://")):
        base = src.rstrip("/") + "/"
        def get(name):
            with urllib.request.urlopen(base + name, timeout=60) as r:
                return json.load(r)
    else:
        def get(name):
            return json.loads((Path(src) / name).read_text())
    return get


# ---------- neighborhoods: rings in zoom-17 pixels, as the map page unpacks them ----------
def load_hoods():
    out = []
    for name, *rs in json.loads((STREETS / "hoods.json").read_text())["h"]:
        rings = []
        for f in rs:
            x, y = f[0], f[1]
            p = [(x, y)]
            for k in range(2, len(f), 2):
                x, y = x + f[k], y + f[k + 1]
                p.append((x, y))
            rings.append(p)
        xs, ys = [x for p in rings for x, _ in p], [y for p in rings for _, y in p]
        out.append(dict(name=name, slug=slug(name), rings=rings, box=(min(xs), min(ys), max(xs), max(ys)), blocks=[]))
    return out


def in_hood(h, x, y):
    """Even-odd over all its rings, like the page's inHood()."""
    x0, y0, x1, y1 = h["box"]
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    c = False
    for p in h["rings"]:
        j = len(p) - 1
        for i in range(len(p)):
            (xi, yi), (xj, yj) = p[i], p[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                c = not c
            j = i
    return c


def neighbors(hoods, size=40, min_cells=5):
    """Neighborhoods that share an edge: their outlines run within a grid square (40 m) of each other for 5 or more
    squares (about 200 m), so two that only touch at a corner aren't counted."""
    marks = []
    for h in hoods:
        s = set()
        for p in h["rings"]:
            for (ax, ay), (bx, by) in zip(p, p[1:] + p[:1]):
                n = max(1, int(math.hypot(bx - ax, by - ay) // (size / 2)))
                s.update((int((ax + (bx - ax) * i / n) // size), int((ay + (by - ay) * i / n) // size)) for i in range(n + 1))
        marks.append(s)
    owners = collections.defaultdict(set)
    for k, s in enumerate(marks):
        for c in s:
            owners[c].add(k)
    shared = collections.Counter()
    for k, s in enumerate(marks):
        for gx, gy in s:
            for o in {j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in owners.get((gx + dx, gy + dy), ())} - {k}:
                shared[(k, o)] += 1
    return {k: sorted((o for (a, o), c in shared.items() if a == k and c >= min_cells), key=lambda o: hoods[o]["name"])
            for k in range(len(hoods))}


# ---------- words, as on the map's card ----------
def fmt_t(m):
    h, mm = m // 60 % 24, m % 60
    return f"{h % 12 or 12}{f':{mm:02d}' if mm else ''} {'pm' if h >= 12 else 'am'}"


def fmt_range(a, b):
    s, e = fmt_t(a), fmt_t(b)
    return s[:-3] + "–" + e if s[-2:] == e[-2:] else s + "–" + e


def fmt_date(s):
    d = datetime.date.fromisoformat(s)
    return f"{d:%b} {d.day}, {d.year}"


def days_txt(dows, long=False):
    if len(dows) == 1:
        return (DAYLONG if long else DAYS)[dows[0]]
    if all(d == dows[i - 1] + 1 for i, d in enumerate(dows) if i):
        return DAYS[dows[0]] + "–" + DAYS[dows[-1]]
    return " & ".join(DAYS[d] for d in dows)


def sched(dows, s0, s1, on, long=False):
    """1st & 3rd Tue, 8–10 am; Every Mon–Fri, 1–4 am"""
    weeks = {1: "1st & 3rd ", 2: "2nd & 4th "}.get(on, "Every ")
    return f"{weeks}{days_txt(dows, long)}, {fmt_range(s0, s1)}"


def side_name(b, side):
    d = b.get("e")
    d = (d if side == "even" else OPP[d]) if d else side
    return f"{d.capitalize()} side"


def hund(h):
    return f"{h} block" if h else "1–99 block"


def plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def pct(c, n):
    return f"all {n:,}" if c == n else f"{round(100 * c / n)}%"


def block_sweeps(b):
    """The block's sweeping days for its row: per side from the posted routes, or the routes around it, or none."""
    groups = {}
    for s in b.get("w", []):
        groups.setdefault((tuple(s[1]), s[2], s[3], s[4]), []).append(s)
    if groups:
        rows = []
        for (dows, s0, s1, on), g in groups.items():
            who = "Both sides" if len(g) == 2 else side_name(b, g[0][0])
            few = ' <small>limited data</small>' if sum(x[7] for x in g) < FEW else ""
            rows.append(f'<b>{who}:</b> <span class="nw">{sched(dows, s0, s1, on)}</span>{few}')
        return "<br>".join(rows)
    if b.get("wr"):
        # an every-other-week route's days go one per side, so each is listed on its own, as the card's signs are
        parts = []
        for dows, s0, s1, on in b["wr"]:
            for d in (dows if on else [dows]):
                t = f'<span class="nw">{sched([d] if on else dows, s0, s1, on)}</span>'
                if t not in parts:
                    parts.append(t)
        return "<b>Routes around it, side not known:</b> " + " · ".join(parts)
    return "No sweeping schedule found"


def street_key(name):
    """Streets in A to Z order without their N/S/E/W, numbered streets first in number order."""
    base = re.sub(r"^[NSEW] ", "", name)
    m = re.match(r"(\d+)", base)
    return (0, int(m.group(1)), base, name) if m else (1, 0, base.lower(), name)


# ---------- the pages ----------
def page_css(tokens):
    return tokens + """
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
  body{margin:0;background:var(--plane);color:var(--ink);font-family:"Barlow",system-ui,-apple-system,"Segoe UI",sans-serif;
       font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased}
  main{max-width:1280px;margin:0 auto;padding:26px 20px 72px}
  a{color:var(--accent-ink)}
  .mast{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
        color:var(--ink-2);display:flex;justify-content:space-between;gap:8px 16px;flex-wrap:wrap}
  .mast a{color:inherit}
  .mast .site{white-space:nowrap}
  .crumbs{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);margin:26px 0 0}
  .crumbs a{color:inherit}
  h1{font-family:"Barlow Condensed","Barlow",system-ui,sans-serif;font-weight:700;font-size:clamp(40px,7vw,62px);
     line-height:.95;margin:10px 0 12px;text-wrap:balance}
  .lede{font-size:18.5px;line-height:1.5;color:var(--ink-2);max-width:var(--measure);margin:0}
  .go{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--ink);background:var(--ink);color:var(--plane);text-decoration:none;
      font:600 15px "Barlow",system-ui,sans-serif;padding:8px 14px;border-radius:8px;min-height:40px;margin:16px 0 0}
  h2{font-family:"Barlow Condensed","Barlow",system-ui,sans-serif;font-weight:700;font-size:34px;line-height:1.02;margin:8px 0 14px;text-wrap:balance;max-width:var(--measure)}
  h3{font-family:"Barlow Condensed","Barlow",system-ui,sans-serif;font-weight:700;font-size:22px;line-height:1.1;margin:26px 0 6px}
  p{max-width:var(--measure);margin:0 0 14px}
  .code{display:inline-flex;gap:10px;align-items:center;font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;
        font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2)}
  .code b{font-weight:600;color:var(--ink-2);border:1px solid var(--edge);border-radius:3px;padding:1px 6px;letter-spacing:.04em}
  section{margin-top:48px;padding-top:28px;border-top:1px solid var(--rule)}
  .note{font-size:14px;line-height:1.5;color:var(--ink-2);max-width:var(--measure);margin:0}
  .note b{color:var(--ink);font-weight:600}
  .note p{margin:0 0 6px;max-width:none}
  .note a{color:inherit}
  .note.end{margin-top:40px;max-width:none}
  .aside{max-width:var(--measure);border-left:3px solid var(--accent);padding:4px 0 4px 16px;margin:26px 0 0;font-size:16px;color:var(--ink-2)}
  .aside b{color:var(--ink)}
  small{font-size:14px;color:var(--ink-2);font-weight:400}
  .nw{white-space:nowrap}   /* a schedule never splits across lines */
  :focus-visible{outline:2px solid var(--accent);outline-offset:2px}

  /* the top: words beside a small map of the neighborhood, which opens the real one */
  .top{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:20px 40px;align-items:start;margin-top:32px}
  @media (max-width:900px){ .top{grid-template-columns:minmax(0,1fr)} .top .map{order:-1;max-width:520px} .map svg{max-height:240px} }
  .top h2{margin-top:0}
  .map{display:block;background:var(--map-bg);border:1px solid var(--rule);border-radius:12px;overflow:hidden;text-decoration:none;color:var(--ink-2)}
  .map:hover{border-color:var(--ink-3)}
  .map svg{display:block;width:100%;height:auto;max-height:420px}
  .map .hood{fill:var(--map-line);fill-opacity:.12;stroke:var(--map-sel);stroke-opacity:.55;stroke-width:1.5;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .map .blk{fill:none;stroke:var(--map-line);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .map .cap{display:block;font-size:14px;padding:8px 12px;border-top:1px solid var(--rule);background:var(--surface)}
  .map:hover .cap{color:var(--ink)}

  /* counted lists: a name, its count, and a bar */
  .bars{list-style:none;margin:16px 0 0;padding:0;max-width:var(--measure)}
  .bars li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 12px;align-items:baseline;padding:8px 0;border-top:1px solid var(--rule)}
  .bars .n{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:14px;text-align:right;white-space:nowrap}
  .bars .bar{grid-column:1/-1;height:6px;border-radius:3px;background:var(--accent)}
  .bars + .note{margin-top:8px}
  .busy{margin:12px 0 0;padding-left:22px;max-width:var(--measure)}
  .busy li{margin:0 0 8px}
  .busy span{display:block;font-size:15px;color:var(--ink-2)}

  /* every block, street by street */
  .blocks{width:100%;max-width:980px;border-collapse:collapse;font-size:15px}
  .blocks th,.blocks td{text-align:left;vertical-align:top;padding:8px 16px 8px 0;border-top:1px solid var(--rule)}
  .blocks th{font-weight:600;width:36%}
  .blocks th span{display:block;font-weight:400;font-size:14px;color:var(--ink-2)}
  @media (max-width:560px){ .blocks th,.blocks td{display:block;width:auto;padding-right:0} .blocks td{border-top:0;padding-top:0} }

  .hoodlist{columns:4 210px;column-gap:28px;list-style:none;margin:20px 0 0;padding:0}
  .hoodlist li{break-inside:avoid;padding:5px 0;border-top:1px solid var(--rule)}
  .hoodlist small{display:block}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0;padding:0;list-style:none}
  .chips a{display:inline-block;border:1px solid var(--edge);background:var(--surface);color:var(--ink);text-decoration:none;
           font:500 15px "Barlow",system-ui,sans-serif;padding:5px 12px;border-radius:999px}
  .chips a:hover{border-color:var(--ink-3)}
  .about{margin:12px 0 0;font-size:16px;line-height:1.5;color:var(--ink-2);max-width:var(--measure)}
"""


def shell(*, title, desc, path, css, built, crumbs, body, jsonld):
    """The parts every page shares: the head, the sister-site masthead, the end notes."""
    up = "../" * path.count("/")            # back to streets/ from streets/<path>
    url = SITE + "streets/" + path
    d = datetime.date.fromisoformat(built)
    crumb = " / ".join(f'<a href="{up}{h}">{escape(t)}</a>' if h is not None else escape(t) for t, h in crumbs)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="LA Street Rules">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{escape(desc)}">
<meta property="og:url" content="{url}">
<!-- Written by hood_pages.py from the weekly data on every deploy; not committed, so edit hood_pages.py instead. -->
<script type="application/ld+json">{json.dumps(jsonld, separators=(",", ":"))}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
{css}</style>

<main>
  <nav class="mast" aria-label="Sister sites"><span><a class="site" href="https://citina.github.io/curb-log/">Curb Log</a> · <a class="site" href="{up}../">USC Street Parking</a> · <a class="site" href="{up}">LA Street Rules</a> · <a class="site" href="https://citina.github.io/sf-streets/">SF Streets</a></span><span class="end"><span>Updated {d:%b} {d.day}, {d.year}</span> · <a href="#about">About</a></span></nav>
  <nav class="crumbs" aria-label="You are here">{crumb}</nav>
{body}
  <div class="note end" id="about">
    <p><b>Not a guide to parking illegally.</b> Past tickets don't predict the next one, so always read the posted signs.</p>
    <p>This page doesn't suggest parking anywhere, or at any time, the rules don't allow. It shows when and where LADOT has written tickets, so the rules on each block and how they're enforced are easier to understand. An officer can come by at any time.</p>
    <p class="about">Made by Citina Liang with Claude Code in less than a week. The page updates itself weekly. All sources are from official public data. <a href="https://www.linkedin.com/in/citina/">LinkedIn</a> · <a href="https://github.com/citina/la-streets">GitHub</a> · <a href="mailto:citina.liang@gmail.com">citina.liang@gmail.com</a></p>
  </div>
</main>
"""


def crumbs_ld(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": t, "item": u} for i, (t, u) in enumerate(items)]}


def simplify(pts, tol):
    """Douglas-Peucker: drop the points within tol of the line between the ones kept."""
    if len(pts) < 3:
        return pts
    keep, stack = {0, len(pts) - 1}, [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        n = math.hypot(dx, dy) or 1
        far, k = max((abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / n, i) for i in range(a + 1, b)) if b > a + 1 else (0, a)
        if far > tol:
            keep.add(k)
            stack += [(a, k), (k, b)]
    return [pts[i] for i in sorted(keep)]


def simplify_ring(p, tol):
    """A closed ring, as two open halves split at the point farthest from its first (one open line would start and
    end on the same point, and every point would sit "on" it)."""
    k = max(range(len(p)), key=lambda i: math.hypot(p[i][0] - p[0][0], p[i][1] - p[0][1]))
    return simplify(p[:k + 1], tol)[:-1] + simplify(p[k:] + p[:1], tol)[:-1]


def map_svg(h, blocks, cell):
    """The neighborhood's outline and its blocks' lines, in metres (zoom-17 pixels) from its top-left corner. The map
    is drawn at most ~500 px wide, so detail finer than about a third of a pixel is dropped (hillside streets curve a lot)."""
    x0, y0, x1, y1 = h["box"]
    pad = max(x1 - x0, y1 - y0) * .03
    ox, oy = x0 - pad, y0 - pad
    tol = max(x1 - x0, y1 - y0) / 1500
    rings = [q for p in h["rings"] if len(q := simplify_ring(p, tol)) >= 3]   # specks smaller than the tolerance go
    ring = "".join("M" + "L".join(f"{x - ox:.0f},{y - oy:.0f}" for x, y in q) + "Z" for q in rings)
    lines = []
    for b in blocks:
        cx, cy = b["_cell"]
        for ln in b["g"]:
            pts = simplify([(ln[i] + cx * cell - ox, ln[i + 1] + cy * cell - oy) for i in range(0, len(ln), 2)], tol)
            lines.append("M" + "L".join(f"{x:.0f},{y:.0f}" for x, y in pts))
    w, hgt = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    return (f'<svg viewBox="0 0 {w:.0f} {hgt:.0f}" aria-hidden="true"><path class="hood" fill-rule="evenodd" d="{ring}"/>'
            f'<path class="blk" d="{"".join(lines)}"/></svg>')


def hood_page(h, near, hoods, meta, css):
    name, blocks, names, kinds = h["name"], h["blocks"], meta["names"], meta["kinds"]
    e = escape(name)
    start, end = fmt_date(meta["start"]), fmt_date(meta["end"])
    sides = [s for b in blocks for s in b.get("w", [])]
    n_sides, twice = len(sides), sum(1 for s in sides if s[4])
    weekly = n_sides - twice
    around = sum(1 for b in blocks if not b.get("w") and b.get("wr"))
    none = sum(1 for b in blocks if not b.get("w") and not b.get("wr"))
    tickets = sum(b["n"] for b in blocks)

    # ---- the sweeping summary
    one_day = collections.Counter(s[1][0] for s in sides if len(s[1]) == 1)
    top = one_day.most_common(2)
    # "Most streets" counts every block: its sides' routes, or else the routes around it (Downtown has few matched sides)
    weeks = [{s[4] for s in b.get("w", [])} or {r[3] for r in b.get("wr", [])} for b in blocks]
    if sum(1 for w in weeks if w and 0 not in w) * 2 > len(blocks):
        most = "twice a month"
    elif sum(1 for w in weeks if 0 in w) * 2 > len(blocks):
        most = "every week"
    else:
        most = None
    when = ""
    if (len(blocks) - around - none) * 2 > len(blocks):   # sweep days only when most blocks have a matched side
        if top and top[0][1] > .6 * n_sides:
            when = f", on {DAYLONG[top[0][0]]}s"
        elif len(top) > 1 and top[0][1] + top[1][1] > .6 * n_sides:
            when = f", on {DAYLONG[top[0][0]]}s or {DAYLONG[top[1][0]]}s"
    h2 = f"Most streets in {e} are swept {most}{when}" if most else f"Street sweeping in {e}"
    para = []
    if n_sides:
        pair = "on the 1st &amp; 3rd or the 2nd &amp; 4th week of the month"
        how = (f"each swept twice a month: {pair}" if twice == n_sides else "each swept every week" if not twice
               else f"{twice:,} of them twice a month ({pair}) and {weekly:,} every week")
        para.append(f"Each side of a street can have its own sweeping day. In {e}, {plural(n_sides, 'block side')} "
                    f"{'is' if n_sides == 1 else 'are'} matched to one of StreetsLA's posted routes, {how}.")
        times = collections.Counter((s[2], s[3]) for s in sides).most_common(1)[0]
        days = [f"{DAYLONG[d]} ({pct(c, n_sides)})" for d, c in top]
        # a posted time only when it's common; otherwise the span they cover
        hours = (f"the most common posted time is {fmt_range(*times[0])} ({pct(times[1], n_sides)})" if times[1] >= .4 * n_sides
                 else f"posted times run between {fmt_t(min(s[2] for s in sides))} and {fmt_t(max(s[3] for s in sides))}")
        para.append((f"The most common sweep {'days are ' + ' and '.join(days) if len(days) > 1 else 'day is ' + days[0]}, and "
                     if days else "") + (hours if days else hours[0].upper() + hours[1:]) + ".")
    if none == len(blocks):
        para.append(f"The {plural(none, 'block')} with tickets here {'is' if none == 1 else 'are'}n't inside any of StreetsLA's posted sweeping routes, "
                    f"so no sweeping schedule is shown. Check a street at {LOOKUP}.")
    elif around or none:
        bits = []
        if around:
            bits.append(f"for {plural(around, 'more block' if n_sides else 'block')}, the routes around the block are known but not which side gets which day")
        if none:
            bits.append(f"{plural(none, 'block')} {'is' if none == 1 else 'are'}n't inside any posted route")
        t = ", and ".join(bits)
        para.append(t[0].upper() + t[1:] + ". Every block's schedule is listed below.")
    sc = collections.Counter((tuple(s[1]), s[2], s[3], s[4]) for s in sides).most_common()
    smax = sc[0][1] if sc else 1
    sched_list = "".join(f'<li><span>{sched(list(d), s0, s1, on, long=True)}</span><span class="n">{plural(c, "block side")}</span>'
                         f'<span class="bar" style="width:{max(1, 100 * c / smax):.1f}%"></span></li>'
                         for (d, s0, s1, on), c in sc[:SHOW_SCHED])
    rest = sc[SHOW_SCHED:]
    sched_more = (f'<p class="note">And {plural(len(rest), "other schedule")}, on {plural(sum(c for _, c in rest), "block side")}.</p>' if rest else "")

    # ---- tickets
    kc = collections.Counter()
    for b in blocks:
        for t in b["k"]:
            kc[kinds[t[0]]] += t[1]
    ks = kc.most_common(SHOW_KINDS)
    kmax = ks[0][1] if ks else 1
    kind_list = "".join(f'<li><span>{escape(k)}</span><span class="n">{c:,}</span><span class="bar" style="width:{max(1, 100 * c / kmax):.1f}%"></span></li>'
                        for k, c in ks)
    spaces, mblocks = sum(b.get("sp", 0) for b in blocks), sum(1 for b in blocks if b.get("sp"))
    meters = (f"LADOT's meter inventory lists {plural(spaces, 'metered space')} on {plural(mblocks, 'block')} here."
              if spaces else "No parking meters are listed in city records for these blocks.")

    def between(b):
        x = [escape(names[i]) for i in b["x"]]
        return f"between {x[0]} and {x[1]}" if len(x) == 2 else f"near {x[0]}" if x else ""

    busy = sorted(blocks, key=lambda b: (-b["n"], b["id"]))[:SHOW_BUSY]
    busy_list = "".join(f'<li><a href="../../#{b["id"]}">{escape(names[b["s"]])}, {hund(b["h"])}</a>'
                        f'<span>{between(b)[:1].upper() + between(b)[1:]}{" · " if b["x"] else ""}{b["n"]:,} tickets, most often {escape(kinds[b["k"][0][0]].lower())}</span></li>'
                        for b in busy)

    # ---- every block, street by street
    by_street = collections.defaultdict(list)
    for b in blocks:
        by_street[b["s"]].append(b)
    rows = []
    for s in sorted(by_street, key=lambda s: street_key(names[s])):
        trs = "".join(f'<tr><th scope="row"><a href="../../#{b["id"]}">{hund(b["h"])}</a>{f"<span>{between(b)}</span>" if b["x"] else ""}</th>'
                      f"<td>{block_sweeps(b)}</td></tr>"
                      for b in sorted(by_street[s], key=lambda b: (b["h"], b["id"])))
        rows.append(f'<h3>{escape(names[s])}</h3>\n<table class="blocks"><tbody>{trs}</tbody></table>')

    near_list = "".join(f'<li><a href="../{hoods[o]["slug"]}/">{escape(hoods[o]["name"])}</a></li>' for o in near)
    desc = (f"Street-sweeping days, weeks and posted times for {len(blocks):,} blocks in {name}, Los Angeles, from StreetsLA's posted routes, "
            f"and the parking tickets LADOT wrote there. Updated weekly.")
    body = f"""  <h1>Street sweeping in {e}</h1>
  <p class="lede">Posted sweeping days and weeks for {plural(len(blocks), 'block')} in {e}, and the parking tickets LADOT wrote there from {start} to {end}.</p>
  <a class="go" href="../../#{h['slug']}">Open {e} on the map →</a>

  <div class="top">
    <div>
      <div class="code"><b>Sweeping</b> {e}</div>
      <h2>{h2}</h2>
      {"".join(f"<p>{t}</p>" for t in para)}
      {f'<ul class="bars" aria-label="Posted sweeping schedules in {e}, by sides of a block">{sched_list}</ul>' if sched_list else ""}
      {sched_more}
      <div class="aside"><b>Street sweeping signs.</b> LADOT says parking is legal on a street's off weeks, even where the sign shows only the day, and said officers would enforce street sweeping only on days that sweeping happens (<a href="https://ladot.lacity.gov/dotnews/weekly-update-march-4-2021">LADOT, March 2021</a>). The weeks on this page are from <a href="{ROUTES}">StreetsLA's list</a> of posted routes; for the official schedule, look up your street at {LOOKUP}.</div>
    </div>
    <a class="map" href="../../#{h['slug']}">{map_svg(h, blocks, meta['cell'])}<span class="cap">{e}: each blue line is a block with tickets. Open the map →</span></a>
  </div>

  <section>
    <div class="code"><b>Tickets</b> {e}</div>
    <h2>What gets ticketed in {e}</h2>
    <p>{tickets:,} LADOT tickets were written on these blocks from {start} to {end}. The most common kinds:</p>
    <ul class="bars" aria-label="Most common tickets in {e}">{kind_list}</ul>
    <p class="note">{meters}</p>
    <h3>Blocks with the most tickets</h3>
    <ol class="busy">{busy_list}</ol>
  </section>

  <section>
    <div class="code"><b>Every block</b> {e}</div>
    <h2>Every block's sweeping days, street by street</h2>
    <p>Each block's posted sweeping schedule, per side of the street, from StreetsLA's routes. Tap a block to open it on the map, with its next sweep dates, meters and tickets.</p>
    <p class="note">“Limited data”: the side was matched to its route day from fewer than {FEW} sweeping tickets. “Side not known”: the block sits inside these posted routes, but too few tickets show which side gets which day. Signs often show only the day; check your street at {LOOKUP}.</p>
    {chr(10).join(rows)}
  </section>
{f'''
  <section>
    <div class="code"><b>Nearby</b> Neighborhoods</div>
    <h2>Next to {e}</h2>
    <ul class="chips">{near_list}</ul>
    <p class="note" style="margin-top:14px"><a href="../">All neighborhoods</a></p>
  </section>''' if near_list else ""}

  <p class="note" style="margin-top:40px"><b>Where this comes from.</b> LADOT parking citations (data.lacity.org) from {start} to {end}, matched to blocks by address; posted sweeping routes from <a href="{ROUTES}">StreetsLA</a>; neighborhood outlines from the <a href="https://www.arcgis.com/home/item.html?id=d6c55385a0e749519f238b77135eafac">Los Angeles Times</a> (CC BY 4.0). A block is in {e} when its middle is. More in <a href="../../#method">How this was measured</a>.</p>"""
    title = f"{name} street sweeping and parking tickets · LA Street Rules"
    path = f"neighborhoods/{h['slug']}/"
    ld = crumbs_ld([("LA Street Rules", SITE + "streets/"), ("Neighborhoods", SITE + "streets/neighborhoods/"), (name, SITE + "streets/" + path)])
    return shell(title=title, desc=desc, path=path, css=css, built=meta["built"],
                 crumbs=[("LA Street Rules", ""), ("Neighborhoods", "neighborhoods/"), (name, None)], body=body, jsonld=ld)


def list_page(hoods, meta, css):
    items = "".join(f'<li><a href="{h["slug"]}/">{escape(h["name"])}</a><small>{plural(len(h["blocks"]), "block")}</small></li>'
                    for h in hoods if h["blocks"])
    body = f"""  <h1>Street sweeping by neighborhood</h1>
  <p class="lede">Each neighborhood in the City of LA, with its blocks' posted sweeping days and weeks and what gets ticketed there, from LADOT's tickets of the past two years. Or <a href="../">open the map</a> and pick a block.</p>
  <ul class="hoodlist">{items}</ul>
  <p class="note" style="margin-top:28px">Neighborhood outlines from the <a href="https://www.arcgis.com/home/item.html?id=d6c55385a0e749519f238b77135eafac">Los Angeles Times</a> (CC BY 4.0).</p>"""
    desc = "Street-sweeping schedules and parking tickets for every neighborhood in the City of Los Angeles, block by block. Updated weekly."
    ld = crumbs_ld([("LA Street Rules", SITE + "streets/"), ("Neighborhoods", SITE + "streets/neighborhoods/")])
    return shell(title="Street sweeping by neighborhood in Los Angeles · LA Street Rules", desc=desc, path="neighborhoods/", css=css,
                 built=meta["built"], crumbs=[("LA Street Rules", ""), ("Neighborhoods", None)], body=body, jsonld=ld)


def sitemap(hoods, built):
    urls = [(SITE, None), (SITE + "streets/", built)]
    if built:
        urls.append((SITE + "streets/neighborhoods/", built))
        urls += [(SITE + f"streets/neighborhoods/{h['slug']}/", built) for h in hoods if h["blocks"]]
    body = "".join(f"<url><loc>{u}</loc>{f'<lastmod>{m}</lastmod>' if m else ''}</url>\n" for u, m in urls)
    (DOCS / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                      f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}</urlset>\n')
    return len(urls)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=str(STREETS / "data"), help="folder or URL with index.json and cells/")
    ap.add_argument("--only", help="comma-separated neighborhood names, for a quick preview")
    a = ap.parse_args()
    get = reader(a.data)
    hoods = load_hoods()
    try:
        meta = get("index.json")
    except (OSError, ValueError) as err:
        # no data (the city-data release is missing): the map says its data didn't load, and the sitemap lists the two pages
        print(f"::warning::no LA Street Rules data ({err}); no neighborhood pages this time")
        shutil.rmtree(OUT, ignore_errors=True)
        print(f"sitemap: {sitemap(hoods, None)} pages")
        return
    page = (STREETS / "index.html").read_text()
    tokens = re.search(r"<style>\n(.*?)\n\s*\*\{box-sizing", page, re.S)
    if not tokens:
        raise SystemExit("error: the map page's color tokens weren't found (the start of its <style>)")
    css = page_css(tokens.group(1))

    want = {n.strip() for n in a.only.split(",")} if a.only else None
    if want and want - {h["name"] for h in hoods}:
        raise SystemExit(f"error: no neighborhood called {', '.join(sorted(want - {h['name'] for h in hoods}))}")
    live = [h for h in hoods if not want or h["name"] in want]
    cell, M = meta["cell"], 400   # M: a block can sit in the next cell over from its middle

    # each cell's blocks go to the neighborhoods whose box overlaps the cell (a preview loads only those cells)
    for key in meta["cells"]:
        cx, cy = map(int, key.split("_"))
        x0, y0 = cx * cell, cy * cell
        cand = [h for h in live if h["box"][0] <= x0 + cell + M and h["box"][2] >= x0 - M and h["box"][1] <= y0 + cell + M and h["box"][3] >= y0 - M]
        if not cand:
            continue
        for b in get(f"cells/{key}.json"):
            xs = [v for ln in b["g"] for v in ln[0::2]]
            ys = [v for ln in b["g"] for v in ln[1::2]]
            x, y = x0 + (min(xs) + max(xs)) / 2, y0 + (min(ys) + max(ys)) / 2   # the middle of its box, like the page's blockCentre()
            for h in cand:   # the first that holds it, in hoods.json's order, like the page's HOODS.find()
                if in_hood(h, x, y):
                    b["_cell"] = (cx, cy)
                    h["blocks"].append(b)
                    break

    near = neighbors(hoods)
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    sizes = []
    for k, h in enumerate(hoods):
        if h in live and h["blocks"]:
            (OUT / h["slug"]).mkdir()
            txt = hood_page(h, near[k], hoods, meta, css)
            (OUT / h["slug"] / "index.html").write_text(txt)
            sizes.append((len(txt.encode()), h["name"], len(h["blocks"])))
    (OUT / "index.html").write_text(list_page(hoods, meta, css))
    n = sitemap(hoods, meta["built"])
    sizes.sort()
    print(f"{len(sizes)} neighborhood pages, {sum(len(h['blocks']) for h in hoods):,} blocks; "
          f"smallest {sizes[0][0] / 1024:.0f} KB ({sizes[0][1]}), largest {sizes[-1][0] / 1024:.0f} KB ({sizes[-1][1]}, {sizes[-1][2]:,} blocks); "
          f"sitemap: {n} pages")


if __name__ == "__main__":
    main()
