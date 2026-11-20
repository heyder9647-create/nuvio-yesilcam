#!/usr/bin/env python3
import argparse, json, os, re, sys, time, html
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36"
ADDON_ID = "tr.yesilcamtv.katalog"
CATALOG_ID = "yesilcamtv"
PAGE_SIZE = 100


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


def parse_film(url, body):
    vid = re.search(r"Video name\s*:\s*([^\[<|]*?)\s*\[(\d+)\]", body)
    title = vid.group(1).strip() if vid else None
    tmdb = vid.group(2) if vid else None
    og_title = meta_tag(body, "og:title")
    if og_title:
        og_title = re.sub(r"\s*\|\s*YESILCAM TV\s*$", "", og_title, flags=re.I)
    title = html.unescape(title or og_title or "").strip()

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

    return {
        "url": url, "name": title, "tmdb": tmdb, "poster": poster,
        "year": year.group(1) if year else None,
        "imdbRating": imdb.group(1) if imdb else None,
        "genres": genres, "description": desc,
    }


def tmdb_to_imdb(tmdb_id, key):
    body = fetch(f"https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids?api_key={key}")
    if not body:
        return None
    try:
        return json.loads(body).get("imdb_id") or None
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://yesilcamtv.com.tr")
    ap.add_argument("--out", default="docs")
    ap.add_argument("--tmdb-key", default=None)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    base = a.base.rstrip("/")

    cache_path = "cache.json"
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}

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

    metas, skipped = [], []
    for u in links:
        f = cache.get(u)
        if not f:
            skipped.append((u, "sayfa alinamadi")); continue
        if not f["tmdb"]:
            skipped.append((u, "sayfada TMDB ID yok")); continue
        if not f["poster"]:
            skipped.append((u, "sitede poster yok")); continue
        mid = f"tmdb:{f['tmdb']}"
        if a.tmdb_key:
            mid = tmdb_to_imdb(f["tmdb"], a.tmdb_key) or mid
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

    cat_dir = os.path.join(a.out, "catalog", "movie")
    os.makedirs(cat_dir, exist_ok=True)
    pages = [metas[i:i + PAGE_SIZE] for i in range(0, len(metas), PAGE_SIZE)] or [[]]
    for n, chunk in enumerate(pages):
        if n == 0:
            path = os.path.join(cat_dir, f"{CATALOG_ID}.json")
        else:
            os.makedirs(os.path.join(cat_dir, CATALOG_ID), exist_ok=True)
            path = os.path.join(cat_dir, CATALOG_ID, f"skip={n * PAGE_SIZE}.json")
        json.dump({"metas": chunk}, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    manifest = {
        "id": ADDON_ID, "version": "1.0.0", "name": "Yeşilçam.tv",
        "description": "yesilcamtv.com.tr sitesindeki tüm filmler (kapaklar sitenin kendisinden)",
        "resources": ["catalog"], "types": ["movie"], "idPrefixes": ["tmdb:", "tt"],
        "catalogs": [{"type": "movie", "id": CATALOG_ID, "name": "Yeşilçam.tv"}],
    }
    json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    if skipped:
        with open("atlananlar.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"{u}\t{why}" for u, why in skipped))
    print(f"\nBitti: {len(metas)} film kataloga yazildi, {len(skipped)} atlandi")


if __name__ == "__main__":
    main()
