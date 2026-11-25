#!/usr/bin/env python3
"""Genera cartelera.json (solo películas) a partir de una guía XMLTV real.
Sin dependencias externas. TMDB es opcional (variable TMDB_API_KEY)."""
import gzip, json, os, re, sys, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EPG_URLS = [u.strip() for u in os.environ.get("EPG_URLS", ",".join([
    "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv_sincolor.xml.gz",
    "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiaiptv.xml",
    "https://www.tdtchannels.com/epg/TV.xml.gz",
])).split(",") if u.strip()]
CANON = {
    "la1": "La 1", "tve1": "La 1", "la2": "La 2", "tve2": "La 2", "antena3": "Antena 3",
    "cuatro": "Cuatro", "telecinco": "Telecinco", "tele5": "Telecinco", "lasexta": "laSexta",
    "paramount": "Paramount Network", "paramountnetwork": "Paramount Network", "bemad": "Be Mad",
    "13": "Trece", "13tv": "Trece", "trece": "Trece", "bom": "Bom Cine", "bomcine": "Bom Cine",
    "canalsur": "Canal Sur", "canalsurandalucia": "Canal Sur", "canalsur2": "ATV", "atv": "ATV",
    "andaluciatv": "ATV", "neox": "Neox", "nova": "Nova", "mega": "Mega", "fdf": "FDF",
    "energy": "Energy", "divinity": "Divinity", "dmax": "DMAX", "squirrel": "Squirrel",
    "ten": "Ten", "dkiss": "DKISS",
}


def canon(label_id, label_name):
    for c in (label_name, label_id):
        t = re.sub(r"\.(tv|es)$", "", c or "", flags=re.I)
        t = re.sub(r"[_\s]*(hd|uhd|4k|sd)$", "", t, flags=re.I)
        if norm(t) in CANON:
            return CANON[norm(t)]
    return re.sub(r"\.(tv|es)$", "", label_name or label_id or "", flags=re.I).replace("_", " ").strip()
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()
OMDB_KEY = os.environ.get("OMDB_API_KEY", "").strip()
TZ = ZoneInfo("Europe/Madrid")
MOVIE_WORDS = ("película", "pelicula", "cine", "movie", "film")
MIN_MINUTES = 70
OUT = os.environ.get("OUT_FILE", "cartelera.json")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cinetdt-granada/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse_ts(s):
    m = re.match(r"\s*(\d{14})\s*([+-]\d{4})?", s or "")
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    if m.group(2):
        off = timedelta(hours=int(m.group(2)[1:3]), minutes=int(m.group(2)[3:5]))
        dt = dt.replace(tzinfo=timezone(off if m.group(2)[0] == "+" else -off))
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def txt(el, tag):
    n = el.find(tag)
    return (n.text or "").strip() if n is not None and n.text else ""


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def tmdb(path, **p):
    p.update(api_key=TMDB_KEY, language="es-ES")
    return json.loads(get(f"https://api.themoviedb.org/3{path}?{urllib.parse.urlencode(p)}"))


_cache = {}
CACHE_FILE = os.environ.get("CACHE_FILE", "cache_omdb.json")
try:
    with open(CACHE_FILE, encoding="utf-8") as _f:
        _omdb = json.load(_f)
except Exception:
    _omdb = {}


def omdb_score(imdb_id, title, year):
    """Nota de la crítica (0-10): Metascore/10; si no hay, Rotten Tomatoes/10."""
    global OMDB_KEY
    key = imdb_id or f"{title}|{year}"
    if key in _omdb:
        return _omdb[key]
    score = None
    try:
        q = {"i": imdb_id} if imdb_id else ({"t": title, "y": year} if year else None)
        if q:
            q["apikey"] = OMDB_KEY
            d = json.loads(get("https://www.omdbapi.com/?" + urllib.parse.urlencode(q)))
            if d.get("Response") == "True" and (imdb_id or norm(d.get("Title")) == norm(title)):
                rt = next((r["Value"] for r in d.get("Ratings", []) if r["Source"] == "Rotten Tomatoes"), "")
                if str(d.get("Metascore", "")).isdigit():
                    score = round(int(d["Metascore"]) / 10, 1)
                elif rt.endswith("%"):
                    score = round(int(rt[:-1]) / 10, 1)
    except Exception as e:
        print("OMDb:", title, e)
        if "401" in str(e):
            OMDB_KEY = ""
            print("OMDb desactivado: clave no válida, no activada o límite diario alcanzado. Se sigue sin más notas de la crítica.")
        return None
    _omdb[key] = score
    return score


def enrich(it):
    """Completa con TMDB solo si el título coincide exactamente (sin adivinar)."""
    if len(it["title"]) > 120:
        return
    key = (it["title"], it["year"])
    if key not in _cache:
        data = None
        try:
            if not TMDB_KEY:
                raise LookupError()
            q = {"query": it["title"]}
            if it["year"]:
                q["year"] = it["year"]
            for r in tmdb("/search/movie", **q).get("results", [])[:3]:
                if norm(it["title"]) in (norm(r.get("title")), norm(r.get("original_title"))):
                    data = tmdb(f"/movie/{r['id']}", append_to_response="credits")
                    break
        except LookupError:
            pass
        except Exception as e:
            print("TMDB:", it["title"], e)
        _cache[key] = data
    d = _cache[key]
    if OMDB_KEY:
        s = omdb_score(d.get("imdb_id") if d else None, it["title"], it["year"])
        if s is not None:
            it["criticScore"] = s
    if not d:
        return
    crew = d.get("credits", {}).get("crew", [])
    cast = d.get("credits", {}).get("cast", [])
    it["originalTitle"] = d.get("original_title") or it["originalTitle"]
    it["year"] = it["year"] or int((d.get("release_date") or "0")[:4]) or None
    it["director"] = it["director"] or ", ".join(c["name"] for c in crew if c.get("job") == "Director")
    it["cast"] = it["cast"] or [c["name"] for c in cast[:4]]
    it["synopsis"] = it["synopsis"] or d.get("overview", "")
    if d.get("genres"):
        it["genre"] = d["genres"][0]["name"]
    if d.get("runtime"):
        it["duration"] = f"{d['runtime']} min"
    if d.get("vote_count", 0) >= 50:
        it["audienceScore"] = round(d["vote_average"], 1)
    if d.get("poster_path"):
        it["poster"] = "https://image.tmdb.org/t/p/w500" + d["poster_path"]


def main():
    today = datetime.now(TZ).date()
    days, cats, seen = {}, Counter(), set()
    target = set(CANON.values())
    present, longcats, used = set(), Counter(), []
    BIG = {"La 1", "Antena 3", "Cuatro", "Telecinco", "laSexta"}
    samples = {}
    for url in EPG_URLS:
        try:
            raw = get(url)
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            root = ET.fromstring(raw)
        except Exception as e:
            print("No se pudo leer", url, "->", e)
            continue
        names = {c.get("id"): txt(c, "display-name") for c in root.findall("channel")}
        n_before = sum(len(v) for v in days.values())
        for c in root.findall("channel"):
            present.add(canon(c.get("id"), names.get(c.get("id"))))
        for p in root.findall("programme"):
            a, b = parse_ts(p.get("start")), parse_ts(p.get("stop"))
            if not a or not b:
                continue
            minutes = int((b - a).total_seconds() // 60)
            categories = [(c.text or "").strip() for c in p.findall("category")]
            channel = canon(p.get("channel"), names.get(p.get("channel")))
            if channel in target and minutes >= 80:
                longcats.update(categories or ["(sin categoría)"])
            if channel not in target:
                continue
            cats.update(categories)
            title = txt(p, "title")
            low = [c.lower() for c in categories]
            cr = p.find("credits")
            has_dir = cr is not None and cr.find("director") is not None
            has_year = bool(re.match(r"\d{4}", txt(p, "date")))
            by_cat = any(w in c for c in low for w in MOVIE_WORDS) or title.lower().startswith("cine")
            by_meta = (not categories) and 85 <= minutes <= 240 and has_dir and has_year
            is_movie = by_cat or by_meta
            if (not is_movie and channel in BIG and minutes >= 85
                    and today <= a.date() <= today + timedelta(days=1)):
                lst = samples.setdefault(channel, [])
                if len(lst) < 10:
                    lst.append((a.strftime("%d %H:%M"), title[:45], categories[:2], minutes, has_dir, has_year))
            if not (is_movie and minutes >= MIN_MINUTES and title):
                continue
            day = a.date()
            if not (today - timedelta(days=1) <= day <= today + timedelta(days=7)):
                continue
            title = re.sub(r"^\s*cine[^:]{0,25}:\s*", "", title, flags=re.I)
            k = (channel, a.strftime("%Y%m%d%H"), norm(title))
            if k in seen:
                continue
            seen.add(k)
            credits = p.find("credits")
            generic = [c for c in categories if not any(w in c.lower() for w in MOVIE_WORDS)]
            year = re.match(r"\d{4}", txt(p, "date"))
            it = {
                "time": a.strftime("%H:%M"), "channel": channel, "title": title,
                "originalTitle": title, "year": int(year.group()) if year else None,
                "duration": f"{minutes} min", "genre": generic[0] if generic else "Cine",
                "director": ", ".join(d.text for d in credits.findall("director")) if credits is not None else "",
                "cast": [x.text for x in credits.findall("actor")][:4] if credits is not None else [],
                "criticScore": None, "audienceScore": None,
                "synopsis": txt(p, "desc"), "poster": "",
            }
            days.setdefault(day.isoformat(), []).append(it)
        used.append((url, sum(len(v) for v in days.values()) - n_before))
    print("Películas aportadas por cada guía:", used)
    print("Cadenas objetivo presentes en las guías:", sorted(present & target))
    print("Cadenas objetivo AUSENTES de las guías:", sorted(target - present))
    print("Categorías de programas largos (>=80 min) de las cadenas objetivo:", longcats.most_common(20))
    for ch, lst in samples.items():
        print("MUESTRA no detectadas", ch)
        for row in lst:
            print("   ", row)
    total = sum(len(v) for v in days.values())
    if total == 0:
        print("No se detectó ninguna película. Categorías vistas:", cats.most_common(25))
        sys.exit(1)
    for items in days.values():
        items.sort(key=lambda x: x["time"])
        if TMDB_KEY or OMDB_KEY:
            for it in items:
                enrich(it)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_omdb, f, ensure_ascii=False)
    out = {"generated": datetime.now(TZ).isoformat(timespec="minutes"), "source": EPG_URLS, "days": dict(sorted(days.items()))}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{total} películas en {len(days)} días. Canales:", sorted({i['channel'] for v in days.values() for i in v}))


main()
