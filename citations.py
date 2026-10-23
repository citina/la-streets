"""Shared loading, geometry and calendar helpers for the USC and city-wide ticket analyses."""
import collections
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
CSV = DATA / "citations_usc.csv"
METERS = DATA / "meters_usc.csv"
ROUTES = DATA / "sweep_routes.geojson"
CENTERLINES = DATA / "centerlines_usc.json"

# The study area: a 2.5 x 2.5 km box around USC's University Park campus
BOX = dict(s=34.012, n=34.035, w=-118.300, e=-118.272)
# The street centerlines reach a little past it (about 300 m), for streets that run along its edges
CL_BOX = dict(s=BOX["s"] - .003, n=BOX["n"] + .003, w=BOX["w"] - .003, e=BOX["e"] + .003)
ZOOM, TILE = 16, 256

# Patterns use the two years up to the newest ticket, so they describe how enforcement works now
WINDOW = pd.DateOffset(years=2)

# USC class periods (from the academic calendar), spring break removed. Add each new
# semester once USC posts it; analyze.py warns when the newest ticket is past the last one.
TERMS = [("2024-08-26", "2024-12-06"), ("2025-01-13", "2025-03-14"), ("2025-03-24", "2025-05-02"),
         ("2025-08-25", "2025-12-05"), ("2026-01-12", "2026-03-13"), ("2026-03-23", "2026-05-01"),
         ("2026-08-24", "2026-12-04"), ("2027-01-11", "2027-03-12"), ("2027-03-22", "2027-04-30"),
         ("2027-08-23", "2027-12-03")]

SUFFIX = {"AV": "AVE", "AVENUE": "AVE", "BL": "BLVD", "BLV": "BLVD", "BOULEVARD": "BLVD",
          "STREET": "ST", "PLACE": "PL", "DRIVE": "DR", "WY": "WAY"}
SUFFIXES = {"ST", "AVE", "BLVD", "PL", "DR", "WAY", "CT", "LN", "RD", "TER", "WALK", "PARK", "SQ"}
DIRS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST", "REAR", "OF"}
ADDR = re.compile(r"(\d{2,5})\s+(.+)")
METER = "8813B"  # code 88.13B; some handhelds write it without dots (8813B+)


# Ticket descriptions in plain words, for codes CODE_KINDS doesn't list; anything else is shown capitalized as LADOT wrote it
LABELS = {
    "?": "Not recorded", "NO PARK/STREET CLEAN": "Street cleaning", "RED ZONE": "Red zone", "NO STOP/STANDING": "No stopping",
    "NO STOP/STAND": "No stopping", "STOP/STAND PROHIBIT": "No stopping", "DISPLAY OF TABS": "Expired tabs",
    "NO PARKING": "No parking", "DISPLAY OF PLATES": "Missing plates", "BLOCKING DRIVEWAY": "Blocking driveway",
    "18 IN. CURB/2 WAY": "Too far from curb", "FIRE HYDRANT": "Fire hydrant", "DOUBLE PARKING": "Double parking",
    "STANDNG IN ALLEY": "Standing in alley", "STANDING IN ALLEY": "Standing in alley",
    "PARKED OVER TIME LIMIT": "Over time limit", "PARKED ON SIDEWALK": "On sidewalk",
    "YELLOW ZONE": "Loading zone", "PARKED IN BUS ZONE": "Bus zone", "PK IN BUS ZONE": "Bus zone",
    "NO STOP/STAND AM": "No stopping, AM rush", "NO STOP/STAND PM": "No stopping, PM rush",
    "NO EVIDENCE OF REG": "No registration", "CARSHARE PARKING": "Car-share space", "WHITE ZONE": "Passenger zone",
    "PREFERENTIAL PARKING": "Permit district", "PREF PARKING": "Permit district", "COMM VEH OVER TIME LIMIT": "Commercial over limit",
    "EXCEED 72HRS-ST": "Parked over 72 hours", "OVERNIGHT PARKING": "Overnight parking", "HANDICAP/NO PLACARD": "Disabled space",
    "PARKED IN CROSSWALK": "In crosswalk", "WITHIN 15FT OF HYDRANT": "Fire hydrant", "LOADING ZONE": "Loading zone",
    "8069B NO PARK ST CLN": "Street cleaning", "8056E4 RED ZONE": "Red zone", "8069A NO STOP/STAND": "No stopping",
}
# Street cleaning is code 80.69BS; a few handhelds write it as 8069BS with its own description
SWEEP = {"NO PARK/STREET CLEAN", "8069B NO PARK ST CLN"}

# The handhelds spell one rule many ways ("STANDNG IN ALLEY", "OVNIGHT PRK W/OUT PE", "8813B METER
# EXPIRED"), so both pages name a ticket by its code, with dots, brackets and the repeat-offence marks
# (+ - # *) taken off; LABELS only covers codes missing here. Sweeping and meter tickets are told apart
# by SWEEP and METER, not by this table.
CODE_KINDS = {
    "?": "Not recorded", "NOVIOL": "Not recorded",
    "8056E4": "Red zone", "8936": "Red zone", "8058L": "Permit district", "80581": "Car-share space",
    "5200": "Missing plates", "5200A": "Missing plates", "5200B": "Missing plates", "5202": "Missing plates",
    "5201": "Plate position", "5201F": "Plate cover", "4464": "Altered plate",
    "5204": "Expired tabs", "5204A": "Expired tabs",
    "4000": "Expired registration", "4000A": "Expired registration", "4000A1": "Expired registration",
    "4454A": "No registration card", "4462B": "Wrong registration",
    "22500M": "Bus lane", "22500I": "Bus zone", "803611": "Tour bus zone", "803611D1": "Tour bus zone",
    "803611D2": "Tour bus zone", "803611D3": "Tour bus zone",
    "8069B": "No parking", "1564260": "No parking", "89391B": "No parking",
    "8069A": "No stopping", "8069AA": "No stopping", "8069AP": "No stopping", "89391A": "No stopping",
    "8070": "Anti-gridlock zone", "22500H": "Double parking", "1564250": "Double parking",
    "8061": "Standing in alley", "8069C": "Over time limit", "89391C": "Over time limit",
    "80692": "Commercial over limit", "22514": "Fire hydrant", "22500D": "Fire station entrance",
    "225001": "Fire lane", "8072": "Red flag day", "22500E": "Blocking driveway", "80551": "Emergency driveway",
    "22502": "Too far from curb", "22502A": "Too far from curb", "22502E": "Too far from curb",
    "8049": "Too far from curb", "8942": "Too far from curb", "8051": "Wrong side of street", "8051A": "Wrong side of street",
    "8073": "Angle parked",
    "8056E1": "Passenger zone", "8939": "Passenger zone", "8056E2": "Loading zone", "8938": "Loading zone", "8709D": "Loading zone",
    "17104H": "Loading zone", "8056E3": "Green zone", "8056E2Z": "Zero-emission zone", "80661D": "Taxi zone",
    "80732": "Parked over 72 hours", "80731": "Stored on the street", "8054": "Overnight parking", "8054H1": "Overnight parking",
    "8711": "RV overnight", "80694": "Oversized vehicle", "22507A": "Oversized vehicle", "8069D": "Vehicle over 6 ft tall",
    "80691": "Trailer", "80691A": "Trailer", "80691C": "Trailer", "80691D": "Trailer",
    "22500F": "On sidewalk", "8053": "On the parkway strip", "22500B": "In crosswalk", "8055A3": "Near crosswalk",
    "22500N4": "Near crosswalk (warning)", "22500A": "In intersection", "22526": "In intersection",
    "22500C": "Transit safety zone", "22500K": "On bridge", "22500G": "Blocking excavation", "8709A": "Railroad track",
    "22521": "Railroad track", "21211B": "Bike lane", "21210": "Bicycle parking",
    "8813A": "Meter expired", "8861": "Meter misuse", "8863A": "Lot meter expired", "8863B": "Lot meter expired",
    "8803": "Outside the marked space", "8803A": "Outside the marked space", "8853": "Outside the marked space",
    "8940A": "Outside the marked space", "8940B": "Outside the marked space", "8709K": "Outside the marked space",
    "8864": "City parking lot", "8864A": "City parking lot", "8864A1": "City parking lot", "8866": "EV charging space",
    "225078": "Disabled space", "225078A": "Disabled space", "225078B": "Disabled space", "225078C": "Disabled space",
    "225078C1": "Disabled space", "225078C2": "Disabled space", "22500L": "Blocking a curb ramp", "22522": "Blocking a curb ramp",
    "2251156B": "Placard misuse", "2251157": "Placard misuse", "2251157A": "Placard misuse",
    "2251157B": "Placard misuse", "2251157C": "Placard misuse",
    "80714": "Private property", "17104C": "Private property", "22658": "Private property", "80713": "Parked on a front yard",
    "21113": "On public grounds", "21113A": "On public grounds", "8603": "In a city park", "8606": "In a city park",
    "6344K2": "In a city park", "6344K7": "In a city park", "6344K8": "In a city park",
    "8709B": "Posted no-parking area", "8706B": "Posted no-parking area", "1520070": "Against posted signs", "21461A": "Against posted signs",
    "572521D": "Fire road", "572521E": "Fire road", "80751": "Car alarm", "8755": "For-sale sign",
    "8753": "Mobile billboard", "8754": "Advertising on vehicle", "8501": "Repairing vehicle on street",
    "8074": "Cleaning vehicle on street", "22517": "Door left open", "22515": "Engine left running",
    "22523A": "Abandoned vehicle", "22523B": "Abandoned vehicle", "26710": "Windshield", "27465B": "Bald tires",
    "22513": "Tow truck", "8940": "Airport parking area", "22504A": "Unincorporated area", "22511": "Veterans exemption", "5025D": "Other",
}


# One plain line for the names that don't explain themselves, shown when a reader opens a kind on the
# city page. Names not listed here say enough on their own ("Blocking driveway", "Double parking").
KIND_NOTES = {
    "Against posted signs": "Parked against what the sign at that spot says.",
    "Airport parking area": "A parking area at LAX, parked against its rules.",
    "Angle parked": "Parked at an angle where parking has to be parallel to the curb.",
    "Anti-gridlock zone": "A stretch where stopping is banned at rush hour to keep traffic moving.",
    "Bike lane": "Stopped in a marked bike lane or bike path.",
    "Blocking a curb ramp": "Blocking the sloped part of a sidewalk that wheelchairs use.",
    "Blocking excavation": "Blocking street work or an excavation.",
    "Bus lane": "In a lane kept for buses.",
    "Bus zone": "In the curb space kept clear for buses at a stop.",
    "Car-share space": "In a space marked for a car-share vehicle.",
    "City parking lot": "A city-owned off-street lot, parked against its signs or over its time limit.",
    "Commercial over limit": "A commercial vehicle parked longer than the limit for one.",
    "Disabled space": "In a disabled space, or without a valid placard shown.",
    "EV charging space": "In a space kept for electric vehicles that are charging.",
    "Emergency driveway": "Within 15 feet of an emergency driveway.",
    "Expired registration": "The vehicle's registration was out of date.",
    "Expired tabs": "The month or year sticker on the plate was out of date.",
    "Fire lane": "In a marked fire lane.",
    "Fire road": "On a mountain fire road without a permit.",
    "Fire station entrance": "Within 15 feet of a fire station driveway.",
    "For-sale sign": "A vehicle parked on the street with a for-sale sign.",
    "Green zone": "At a green curb, which has a short time limit.",
    "In a city park": "Inside a city park: where parking isn't allowed, outside a marked stall, or after posted hours.",
    "In crosswalk": "Stopped in a crosswalk.",
    "In intersection": "Stopped in an intersection or blocking it.",
    "Loading zone": "At a yellow curb, which is for loading during the posted hours.",
    "Lot meter expired": "An expired meter in an off-street city lot.",
    "Meter expired": "The meter had run out, or wasn't paid.",
    "Meter misuse": "Using a meter in a way it isn't meant for.",
    "Missing plates": "No plate shown, or the plate not displayed as required.",
    "Mobile billboard": "A trailer or vehicle used as a moving advertisement.",
    "Near crosswalk": "Stopped too close to a crosswalk.",
    "Near crosswalk (warning)": "Parked too close to a crosswalk. These are written as warnings, usually with no fine.",
    "No stopping": "Stopped where even a brief stop isn't allowed, including rush-hour lanes.",
    "Not recorded": "The rule wasn't recorded on the ticket in LADOT's data.",
    "On bridge": "Stopped on a bridge or in a tunnel.",
    "On public grounds": "On public grounds, such as land around a civic building.",
    "On the parkway strip": "On the strip between the sidewalk and the curb.",
    "Other": "The code on the ticket doesn't match any rule this page knows.",
    "Outside the marked space": "Outside the painted lines of a space, or across two spaces.",
    "Over time limit": "Parked longer than the posted time limit.",
    "Overnight parking": "Parked overnight where that needs a permit.",
    "Oversized vehicle": "A vehicle over the size limit where oversized vehicles are banned.",
    "Parked on a front yard": "Parked on the front yard of a home.",
    "Parked over 72 hours": "Left in the same spot on the street for more than 72 hours.",
    "Passenger zone": "At a white curb, which is for dropping off and picking up people.",
    "Permit district": "In a permit-only district without a permit shown.",
    "Placard misuse": "Using someone else's disabled placard, or an altered one.",
    "Plate cover": "A cover over the licence plate.",
    "Plate position": "The plate isn't where it has to be on the vehicle.",
    "Posted no-parking area": "In an area posted as no parking.",
    "Private property": "Parked on private property without permission.",
    "RV overnight": "An RV parked overnight where that isn't allowed.",
    "Railroad track": "Stopped too close to a railroad track.",
    "Red flag day": "Parked on a hillside route on a red flag day, when fire risk is high.",
    "Red zone": "At a red curb, where stopping isn't allowed at any time.",
    "Standing in alley": "Stopped in an alley.",
    "Stored on the street": "Leaving a vehicle parked as storage rather than using it.",
    "Street cleaning": "Parked during the posted street-sweeping hours.",
    "Taxi zone": "In a space kept for taxis.",
    "Too far from curb": "More than 18 inches from the curb, or not parallel to it.",
    "Tour bus zone": "In a space kept for tour buses.",
    "Trailer": "A trailer parked on the street, or left unhitched.",
    "Transit safety zone": "In the marked strip of roadway for people getting on and off transit.",
    "Vehicle over 6 ft tall": "A vehicle or load over 6 feet tall, where that isn't allowed.",
    "Zero-emission zone": "A curb space kept for zero-emission vehicles.",
}


def kind_label(code, desc):
    """The plain name for a ticket from its code, falling back to LABELS and then LADOT's own words."""
    k = re.sub(r"[.()\s]", "", code.upper()).rstrip("+-#*") if isinstance(code, str) else "?"
    return CODE_KINDS.get(k) or LABELS.get(desc) or desc.capitalize()


def parse_loc(s):
    """'3601 VERMONT AV S' -> (3601, 'VERMONT AVE'); intersections and blanks -> (None, None).

    East addresses keep an 'E ' prefix: 101 E 35th St is across Main St from 101 W 35th St,
    on a different sweeping route.
    """
    if not isinstance(s, str):
        return None, None
    m = ADDR.search(s.upper())
    if not m:
        return None, None
    raw = re.split(r"[\s.]+", m.group(2))
    toks = [SUFFIX.get(t, t) for t in raw if t and t not in DIRS]
    east = "E " if {"E", "EAST"} & set(raw) else ""
    return (int(m.group(1)), east + " ".join(toks)) if toks else (None, None)


def canonical_streets(streets):
    """Fold spelling variants into the common form: 'FIGUEROA' -> 'FIGUEROA ST', '32' -> '32ND ST'."""
    counts = streets.value_counts()
    full = [s for s in counts.index if s.split()[-1] in SUFFIXES]
    fix = {}
    for s in counts.index:
        if s.split()[-1] in SUFFIXES:
            continue
        if re.fullmatch(r"\d+", s):
            cands = [f for f in full if re.match(rf"^{s}(ST|ND|RD|TH) ", f)]
        else:
            cands = [f for f in full if f.startswith(s + " ") and len(f.split()) == len(s.split()) + 1]
        if cands:
            fix[s] = max(cands, key=lambda f: counts[f])
    return streets.map(lambda s: fix.get(s, s))


def nice_street(s):
    t = s.title()
    t = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), t)
    return re.sub(r"\bMc(\w)", lambda m: "Mc" + m.group(1).upper(), t)


def load(path=CSV):
    d = pd.read_csv(path, dtype=str)
    d["date"] = pd.to_datetime(d.issue_date.str[:10])
    d = d[(d.date >= "2014-01-01") & (d.date <= pd.Timestamp.today().normalize())].copy()
    t = pd.to_numeric(d.issue_time, errors="coerce")
    d = d[t.notna() & (t % 100 < 60) & (t < 2400)].copy()
    t = t.loc[d.index].astype(int)
    d["mins"] = (t // 100) * 60 + t % 100
    d["ts"] = d.date + pd.to_timedelta(d.mins, unit="m")
    d["dow"] = d.date.dt.dayofweek
    parsed = d.location.map(parse_loc)
    d["num"] = parsed.str[0]
    d["street"] = parsed.str[1]
    ok = d.street.notna()
    d.loc[ok, "street"] = canonical_streets(d.loc[ok, "street"])
    d["block"] = (d.num // 100 * 100).astype("Int64")
    d["side"] = np.where(d.num % 2 == 0, "even", "odd")
    d["lat"] = pd.to_numeric(d.loc_lat, errors="coerce")
    d["lon"] = pd.to_numeric(d.loc_long, errors="coerce")
    d["tno"] = pd.to_numeric(d.ticket_number, errors="coerce")
    d["fine"] = pd.to_numeric(d.fine_amount, errors="coerce")
    d["viol"] = d.violation_description.fillna("?").str.strip()
    d["meter"] = d.violation_code.fillna("").str.replace(".", "", regex=False).str.startswith(METER)
    return d


def end_date(d):
    """Last day with a normal day's worth of tickets (the feed has a few stray future dates)."""
    per_day = d.groupby("date").size()
    return per_day[per_day >= 20].index.max()


def holidays(start, end):
    h = set(USFederalHolidayCalendar().holidays(start, end))
    for y in range(start.year, end.year + 1):
        tg = pd.Timestamp(f"{y}-11-01") + pd.offsets.WeekOfMonth(week=3, weekday=3)
        h |= {tg, tg + pd.Timedelta(days=1), pd.Timestamp(f"{y}-03-31"), pd.Timestamp(f"{y}-12-24")}
    return h


def in_term(dates):
    dates = pd.Series(dates)
    s = pd.Series(False, index=dates.index)
    for a, b in TERMS:
        s |= (dates >= a) & (dates <= b)
    return s.values


# ---- StreetsLA's posted sweeping routes ----
DAYNUM = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}


def clock(s):
    """'6:30 am' -> 390 minutes after midnight."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d\d))?\s*([ap])m", s.strip().lower())
    return (int(m[1]) % 12 + 12 * (m[3] == "p")) * 60 + int(m[2] or 0)


def load_routes(path=ROUTES, weekly=False):
    """One dict per route day: route, dow, on (1 = 1st & 3rd weeks, 2 = 2nd & 4th), posted
    window s0-s1 in minutes, and the polygon rings (lon, lat) of the area it covers.

    weekly=True (the city-wide page) also reads the few routes swept every week (on = 0): Skid Row,
    and Downtown's "Monday to Friday" routes, which become one route day per weekday with `dows`
    listing all five. Routes with no fixed time, like "As Available", are left out with a warning."""
    out, skipped = [], []
    for f in json.loads(path.read_text())["features"]:
        p, geom = f["properties"], f["geometry"]
        on = {"1 & 3": 1, "2 & 4": 2, **({"Weekly": 0} if weekly else {})}.get(p["Weeks"])
        span, times = p["Posted_Day"].split(" to "), p["Posted_Time"].split("-")
        if on is None or len(times) != 2 or any(d not in DAYNUM for d in span) or (len(span) > 1 and not weekly):
            skipped.append(f'{p["Route"]} ({p["Posted_Day"]}, {p["Weeks"]}, {p["Posted_Time"]})')
            continue
        parts = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        rings = [np.asarray(r, float) for q in parts for r in q]
        dows = list(range(DAYNUM[span[0]], DAYNUM[span[-1]] + 1))
        for dow in dows:
            out.append(dict(route=p["Route"].split()[0], dow=dow, dows=dows, on=on, s0=clock(times[0]), s1=clock(times[1]), rings=rings))
    if skipped:
        print(f"warning: {len(skipped)} posted sweeping route days left out: " + "; ".join(skipped))
    return out


def inside(lon, lat, rings):
    """Which points fall inside a polygon. Even-odd rule over every ring, so holes and
    multi-part areas need no special case; points outside a ring's bounding box can't flip it."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    hit = np.zeros(len(lon), bool)
    for r in rings:
        lo, hi = r.min(0), r.max(0)
        k = np.flatnonzero((lon >= lo[0]) & (lon <= hi[0]) & (lat >= lo[1]) & (lat <= hi[1]))
        if not len(k):
            continue
        x, y = lon[k], lat[k]
        for (x1, y1), (x2, y2) in zip(r, np.roll(r, -1, axis=0)):
            if y1 != y2:
                hit[k] ^= ((y1 > y) != (y2 > y)) & (x < x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    return hit


# ---- Web Mercator pixels on the basemap (same projection as the OSM tiles) ----
def deg2num(lat, lon, z=ZOOM):
    n = 2 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lr = np.radians(np.asarray(lat))
    y = (1.0 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2.0 * n
    return x, y


def frame():
    x0, y0 = deg2num(BOX["n"], BOX["w"])
    x1, y1 = deg2num(BOX["s"], BOX["e"])
    return dict(x0=float(x0) * TILE, y0=float(y0) * TILE,
                W=int(round((float(x1) - float(x0)) * TILE)), H=int(round((float(y1) - float(y0)) * TILE)),
                m_per_px=156543.03392 * math.cos(math.radians((BOX["n"] + BOX["s"]) / 2)) / 2 ** ZOOM)


def to_px(lat, lon):
    f = frame()
    x, y = deg2num(lat, lon)
    return x * TILE - f["x0"], y * TILE - f["y0"]


# ---- City of Los Angeles street centerlines (LA GeoHub), with the address range on each side ----
# ticket spellings -> the centerline's suffixes and directions
CL_SUFFIX = {"AV": "AVE", "AVENUE": "AVE", "STR": "ST", "STREET": "ST", "BL": "BLVD", "BLV": "BLVD", "BOULEVARD": "BLVD",
             "DRIVE": "DR", "PLACE": "PL", "RD": "ROAD", "WY": "WAY", "LN": "LANE", "COURT": "CT", "TERR": "TER",
             "TERRACE": "TER", "CIRCLE": "CIR", "HIGHWAY": "HWY", "TRL": "TR", "TRAIL": "TR", "PARKWAY": "PKWY",
             "PLZ": "PZ", "PLAZA": "PZ"}
CL_DIRS = {"N": "N", "S": "S", "E": "E", "W": "W", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
FAR = 600             # metres: a match this far from the ticket's own coordinates is treated as wrong
SPLIT = 800           # metres: parts of one street + hundred block this far apart are separate places
# Where the centerlines have the address ranges on the wrong sides, checked by hand against LA County's parcel
# addresses: the street, the house numbers, and the side the even numbers are really on. It names the side
# rather than swapping the ranges, so it stays right if the city corrects them.
EVEN_FIX = [(("W", "37TH", "PL", ""), 100, 299, "south")]   # 112, 146, 164, 226 and 240 W 37th Pl sit south of it


def z17(lon, lat):
    """Zoom-17 Web Mercator pixels, the same grid as OpenStreetMap's tiles."""
    n = 2 ** 17 * 256
    r = np.radians(np.asarray(lat, dtype=float))
    with np.errstate(invalid="ignore"):   # missing coordinates stay NaN
        return (np.asarray(lon, dtype=float) + 180) / 360 * n, (1 - np.log(np.tan(r) + 1 / np.cos(r)) / math.pi) / 2 * n


PX_M = 156543.03392 * math.cos(math.radians(34.05)) / 2 ** 17   # metres per zoom-17 pixel in LA


class Centerlines:
    """The street centerlines as fetch_city.py (the whole city) or fetch_citations.py (the USC box) saves
    them: one segment per piece of street between two intersections, with its address range on each side.
    Ticket addresses are matched to them by street name, direction, suffix and house number; the ticket's
    own coordinates settle names that repeat across the city. box (BOX's s, n, w, e) keeps only the
    segments that reach into it."""

    def __init__(self, path, box=None):
        self.segs = []
        for f in json.loads(path.read_text())["features"]:
            p, g = f["properties"], f["geometry"]
            if not g or not (p.get("STNAME") or "").strip():
                continue
            parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            pts = np.array([c for ln in parts for c in ln])
            if box and not (pts[:, 0].max() >= box["w"] and pts[:, 0].min() <= box["e"] and pts[:, 1].max() >= box["s"] and pts[:, 1].min() <= box["n"]):
                continue
            x, y = z17(pts[:, 0], pts[:, 1])
            rl = (p["ADLF"] or 0, p["ADLT"] or 0)
            rr = (p["ADRF"] or 0, p["ADRT"] or 0)
            nums = [v for v in (*rl, *rr) if v]
            self.segs.append(dict(
                key=((p["TDIR"] or "").strip(), p["STNAME"].strip().upper(), (p["STSFX"] or "").strip().upper(), (p["SFXDIR"] or "").strip().upper()),
                l=rl, r=rr, lo=min(nums) if nums else 0, hi=max(nums) if nums else 0,
                a=p["INT_ID_FROM"], b=p["INT_ID_TO"], zip=p["ZIP_L"] or p["ZIP_R"] or 0, xy=np.c_[x, y]))
        self.mid = np.array([s["xy"].mean(0) for s in self.segs])
        self.keys = sorted({s["key"] for s in self.segs})   # one per street: (direction, name, suffix, suffix direction)
        key_ix = {k: i for i, k in enumerate(self.keys)}
        self.sid = np.array([key_ix[s["key"]] for s in self.segs])
        self.by_name = collections.defaultdict(list)
        self.by_street = collections.defaultdict(list)
        for i, s in enumerate(self.segs):
            self.by_name[s["key"][1]].append(i)
            self.by_street[self.sid[i]].append(i)
        self.suffixes = {s["key"][2] for s in self.segs if s["key"][2]}
        for key, lo, hi, side in EVEN_FIX:   # a pixel vector toward the side the even numbers are really on
            a = math.radians(45 * COMPASS.index(side))
            for i in self.by_street[key_ix[key]] if key in key_ix else []:
                if lo <= self.segs[i]["lo"] and self.segs[i]["hi"] <= hi:
                    self.segs[i]["even_to"] = (math.cos(a), -math.sin(a))

    def parse(self, loc):
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
        if len(toks) > 1 and CL_SUFFIX.get(toks[-1], toks[-1]) in self.suffixes:
            sfx = CL_SUFFIX.get(toks[-1], toks[-1])
            toks.pop()
        name = " ".join(toks)
        name = re.sub(r"^AVE? (\d+)$", r"AVENUE \1", name)
        name = re.sub(r"^SAINT ", "ST ", re.sub(r"^MOUNT ", "MT ", name))
        return int(m.group(1)), name, sfx, d

    def match(self, num, name, sfx, d, x, y):
        """Index of the segment for an address, or None; x, y are the ticket's own zoom-17 pixels."""
        c = self.by_name.get(name)
        if not c:
            return None
        c = [i for i in c if self.segs[i]["key"][2] == sfx] or c if sfx else c
        c = [i for i in c if d in (self.segs[i]["key"][0], self.segs[i]["key"][3][:1])] or c if d else c
        hit = [i for i in c if in_range(num, self.segs[i]["l"]) or in_range(num, self.segs[i]["r"])]
        if not hit:  # the number falls in a gap between ranges: the nearest segment of this street within 100 numbers
            hit = [i for i in c if self.segs[i]["lo"] and self.segs[i]["lo"] - 100 <= num <= self.segs[i]["hi"] + 100]
        if not hit:
            return None
        dist = np.hypot(self.mid[hit, 0] - x, self.mid[hit, 1] - y) * PX_M if np.isfinite(x) else np.zeros(len(hit))
        k = int(np.argmin(dist))
        return hit[k] if dist[k] <= FAR else None

    def block(self, st, h, centre):
        """The segments of street st (an index into keys) whose addresses reach into hundred block h, within
        SPLIT of centre (zoom-17 pixels)."""
        return [i for i in self.by_street[st] if self.segs[i]["lo"] <= h + 99 and self.segs[i]["hi"] >= h
                and np.hypot(*(self.mid[i] - centre)) * PX_M < SPLIT]

    def even_side(self, ids):
        """Which compass side of the street the even house numbers are on, weighted by length: a compass word,
        or "" when the ranges don't say, and the summed pixel vector pointing to that side."""
        votes, vec = collections.Counter(), np.zeros(2)
        for i in ids:
            s = self.segs[i]
            dx, dy = s["xy"][-1] - s["xy"][0]
            even_left = s["l"][0] % 2 == 0 if s["l"][0] else s["r"][0] % 2 == 1 if s["r"][0] else None
            if even_left is not None and (dx or dy):
                v = (dy, -dx) if even_left else (-dy, dx)
                if "even_to" in s and np.dot(v, s["even_to"]) < 0:   # ranges on the wrong sides (EVEN_FIX)
                    v = (-v[0], -v[1])
                votes[compass(*v)] += math.hypot(dx, dy)
                vec += v
        return (votes.most_common(1)[0][0] if votes else ""), vec


def in_range(n, rng):
    a, b = rng
    return bool(a or b) and min(a, b) <= n <= max(a, b) and n % 2 in (a % 2, b % 2)


# ---- shared by the USC and city-wide analyses ----
COMPASS = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]   # 45° apart


def compass(vx, vy):
    """Direction of a pixel-space vector (+x east, +y south) as one of 8 compass words."""
    ang = (math.degrees(math.atan2(-vy, vx)) + 360) % 360
    return COMPASS[int((ang + 22.5) // 45) % 8]


def usual_hours(mins, share=.5):
    """Clock-hour ranges holding the busiest `share` of tickets, as [[start, end], ...] in minutes.

    One quartile range misleads when tickets come at two times of day (fire hydrant:
    1-4 am and midday gives "3:34 am-1:58 pm"). Instead take the fewest hours that
    cover `share`, join runs split by one quiet hour, and keep the two biggest runs.
    """
    c = np.bincount(np.asarray(mins, dtype=int) // 60 % 24, minlength=24)
    pick = np.zeros(24, bool)
    for hr in np.argsort(-c, kind="stable"):
        if c[pick].sum() >= share * c.sum():
            break
        pick[hr] = True
    pick |= np.roll(pick, 1) & np.roll(pick, -1)
    if pick.all():
        return [[0, 1440]]
    s0 = int(np.argmin(pick))  # an unpicked hour, so no run wraps past the scan start
    runs, a = [], None
    for i in range(s0, s0 + 25):
        if i < s0 + 24 and pick[i % 24]:
            a = i if a is None else a
        elif a is not None:
            runs.append((int(c[[j % 24 for j in range(a, i)]].sum()), a % 24, i - a))
            a = None
    runs = sorted(sorted(runs, reverse=True)[:2], key=lambda r: r[1])
    return [[h * 60, (h + n) * 60] for _, h, n in runs]
