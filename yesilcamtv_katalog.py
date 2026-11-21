#!/usr/bin/env python3
import argparse, json, os, re, sys, time, html
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, quote
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36"
ADDON_ID = "tr.yesilcamtv.katalog"
CATALOG_ID = "yesilcamtv"
PAGE_SIZE = 100
MIN_TUR_FILM = 3      # bundan az filmi olan tur katalog olmaz
GURULTU_ORANI = 0.95  # filmlerin %95'inde gorunen "tur" menu gurultusudur, atlanir

_TR = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def slugify(t):
    t = t.translate(_TR).lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "diger"


def yil_sirasi(m):
    y = m.get("releaseInfo") or ""
    return (-(int(y) if y.isdigit() else 0), (m.get("name") or "").lower())


def fetch(url, tries=4):
    err = None
    for i in range(tries):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept-Language": "tr"})
            with urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except (URLError, TimeoutError) as e:
            err = e
        time.sleep(1.5 * (i + 1))
    print(f"  ! alinamadi: {url} ({err})", file=sys.stderr)
    return None


FILM_LINK = re.compile(r'href=["\']((?:https?://[^"\']+)?/[^"\'/#?]+-izle[^"\'/#?]*/)["\']')


def crawl_listing(base):
    links, seen, page = [], set(), 1
    while True:
        url = base + ("/" if page == 1 else f"/page/{page}/")
        body = fetch(url)
        if not body:
            break
        new = 0
        for m in FILM_LINK.finditer(body):
            u = urljoin(base + "/", m.group(1))
            if u not in seen:
                seen.add(u)
                links.append(u)
                new += 1
        print(f"liste sayfa {page}: +{new} (toplam {len(links)})")
        if new == 0:
            break
        page += 1
        time.sleep(0.4)
    return links


def meta_tag(body, prop):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(prop), body)
    return html.unescape(m.group(1)).strip() if m else None


def clean_title(t):
    t = html.unescape(t or "").strip()
    t = re.sub(r"\s*\|\s*YESILCAM TV\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", t)
    t = re.sub(r"\s+(?:full\s+)?(?:hd\s+)?izle\s*$", "", t, flags=re.I)
    return t.strip(" -–|")


def parse_film(url, body):
    vid = re.search(r"Video name\s*:\s*([^\[<|]*?)\s*\[(\d+)\]", body)
    tmdb = vid.group(2) if vid else None
    title = clean_title(vid.group(1) if vid else meta_tag(body, "og:title"))

    poster = meta_tag(body, "og:image")
    if not poster:
        m = re.search(r'<img[^>]+src=["\']([^"\']+/wp-content/uploads/[^"\']+-180x270\.\w+)["\']', body)
        poster = m.group(1) if m else None
    if poster:
        poster = re.sub(r"-\d+x\d+(?=\.\w+$)", "", poster)

    year = re.search(r"Yap[ıi]m Y[ıi]l[ıi]:.{0,300}?/yil/(\d{4})/", body, re.S)
    imdb = re.search(r"IMDb:\s*([\d.]+)", body)
    genres = [html.unescape(g) for g in re.findall(r'/category/[^"\']+["\']\s+title=["\']([^"\']+)["\']', body)]
    desc = meta_tag(body, "og:description") or meta_tag(body, "description")
    return {"url": url, "name": title, "tmdb": tmdb, "poster": poster,
            "year": year.group(1) if year else None,
            "imdbRating": imdb.group(1) if imdb else None,
            "genres": genres, "description": desc}


def tmdb_get(base, key, path, **params):
    q = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    body = fetch(f"{base}{path}?api_key={key}&{q}")
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def search_tmdb(film, key, base):
    title, year = film["name"], film["year"]
    if not title or not year:
        return None
    y = int(year)
    seen = {}
    for lang in ("tr-TR", "en-US"):
        for extra in ({"year": year}, {}):
            d = tmdb_get(base, key, "/search/movie", query=title, language=lang,
                         include_adult="false", **extra)
            for r in (d or {}).get("results", []):
                seen.setdefault(r["id"], r)
            ok = [r for r in seen.values()
                  if (r.get("release_date") or "")[:4].isdigit()
                  and abs(int(r["release_date"][:4]) - y) <= 1]
            if ok:
                ok.sort(key=lambda r: (r.get("original_language") != "tr",
                                       abs(int(r["release_date"][:4]) - y),
                                       -(r.get("popularity") or 0)))
                return str(ok[0]["id"])
    return None


def tmdb_to_imdb(tmdb_id, key, base):
    d = tmdb_get(base, key, f"/movie/{tmdb_id}/external_ids")
    return (d or {}).get("imdb_id") or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://yesilcamtv.com.tr")
    ap.add_argument("--out", default="docs")
    ap.add_argument("--tmdb-key", default=os.environ.get("TMDB_KEY"))
    ap.add_argument("--tmdb-base", default="https://api.themoviedb.org/3")
    ap.add_argument("--imdb-ids", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    key = (a.tmdb_key or "").strip() or None

    cache_path, tmdb_path = "cache.json", "tmdb_cache.json"
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    tcache = json.load(open(tmdb_path, encoding="utf-8")) if os.path.exists(tmdb_path) else {}
    for f in cache.values():
        f["name"] = clean_title(f.get("name"))

    links = crawl_listing(base)
    todo = [u for u in links if u not in cache]
    print(f"{len(links)} film bulundu, {len(todo)} tanesi cekilecek")

    def work(u):
        body = fetch(u)
        return u, (parse_film(u, body) if body else None)

    with ThreadPoolExecutor(a.workers) as ex:
        for i, (u, film) in enumerate(ex.map(work, todo), 1):
            if film:
                cache[u] = film
            if i % 50 == 0:
                json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
                print(f"  film {i}/{len(todo)}")
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)

    need = [cache[u] for u in links if cache.get(u) and not cache[u]["tmdb"]
            and cache[u]["url"] not in tcache]
    if need and key:
        print(f"{len(need)} film icin TMDB'de arama yapiliyor")

        def look(f):
            return f["url"], search_tmdb(f, key, a.tmdb_base)

        with ThreadPoolExecutor(a.workers) as ex:
            for i, (u, tid) in enumerate(ex.map(look, need), 1):
                tcache[u] = tid
                if i % 100 == 0:
                    json.dump(tcache, open(tmdb_path, "w", encoding="utf-8"), ensure_ascii=False)
                    print(f"  arama {i}/{len(need)}")
        json.dump(tcache, open(tmdb_path, "w", encoding="utf-8"), ensure_ascii=False)
    elif need:
        print("UYARI: TMDB anahtari yok, ID'siz filmler atlanacak (TMDB_KEY ekleyin)")

    metas, skipped = [], []
    for u in links:
        f = cache.get(u)
        if not f:
            skipped.append((u, "sayfa alinamadi")); continue
        tid = f["tmdb"] or tcache.get(u)
        if not tid:
            skipped.append((u, "TMDB'de eslesme yok" if key else "sayfada TMDB ID yok ve anahtar yok")); continue
        if not f["poster"]:
            skipped.append((u, "sitede poster yok")); continue
        mid = f"tmdb:{tid}"
        if key and a.imdb_ids:
            mid = tmdb_to_imdb(tid, key, a.tmdb_base) or mid
        m = {"id": mid, "type": "movie", "name": f["name"], "poster": f["poster"],
             "posterShape": "poster"}
        if f["year"]: m["releaseInfo"] = f["year"]
        if f["imdbRating"]: m["imdbRating"] = f["imdbRating"]
        if f["genres"]: m["genres"] = f["genres"]
        if f["description"]: m["description"] = f["description"]
        metas.append(m)

    seen, uniq = set(), []
    for m in metas:
        if m["id"] not in seen:
            seen.add(m["id"]); uniq.append(m)
    metas = uniq

    # yeniden eskiye (yil azalan)
    metas.sort(key=yil_sirasi)

    # turleri say, menu gurultusunu ele
    sayac = {}
    for m in metas:
        for g in set(m.get("genres") or []):
            sayac[g] = sayac.get(g, 0) + 1
    toplam = len(metas) or 1
    turler = [(g, n) for g, n in sayac.items()
              if n >= MIN_TUR_FILM and n / toplam < GURULTU_ORANI]
    turler.sort(key=lambda x: -x[1])
    print("\nTur dagilimi:")
    for g, n in sorted(sayac.items(), key=lambda x: -x[1]):
        durum = "ATLANDI" if (g, n) not in turler else "katalog"
        print(f"  {g}: {n} ({durum})")

    cat_dir = os.path.join(a.out, "catalog", "movie")
    os.makedirs(cat_dir, exist_ok=True)

    def yaz_katalog(cat_id, items):
        pages = [items[i:i + PAGE_SIZE] for i in range(0, len(items), PAGE_SIZE)] or [[]]
        for n, chunk in enumerate(pages):
            if n == 0:
                path = os.path.join(cat_dir, f"{cat_id}.json")
            else:
                os.makedirs(os.path.join(cat_dir, cat_id), exist_ok=True)
                path = os.path.join(cat_dir, cat_id, f"skip={n * PAGE_SIZE}.json")
            json.dump({"metas": chunk}, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    catalogs = [{"type": "movie", "id": CATALOG_ID, "name": "Yeşilçam.tv"}]
    yaz_katalog(CATALOG_ID, metas)
    for g, n in turler:
        cid = f"{CATALOG_ID}-{slugify(g)}"
        yaz_katalog(cid, [m for m in metas if g in (m.get("genres") or [])])
        catalogs.append({"type": "movie", "id": cid, "name": g})

    manifest = {
        "id": ADDON_ID, "version": "1.2.0", "name": "Yeşilçam.tv",
        "description": "yesilcamtv.com.tr sitesindeki tüm filmler (kapaklar sitenin kendisinden)",
        "resources": ["catalog"], "types": ["movie"], "idPrefixes": ["tmdb:", "tt"],
        "catalogs": catalogs,
    }
    json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    with open("atlananlar.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{u}\t{why}" for u, why in skipped))
    print(f"\nBitti: {len(metas)} film kataloga yazildi, {len(skipped)} atlandi")


if __name__ == "__main__":
    main()
