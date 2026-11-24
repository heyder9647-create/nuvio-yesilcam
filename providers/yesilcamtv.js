var TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c";
var BASE = "https://yesilcamtv.com.tr";
var INDEX_URL = "https://heyder9647-create.github.io/nuvio-yesilcam/index.json";
var UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36";
var REQ_TIMEOUT = 8000;

function norm(s) {
  return (s || "").toLocaleLowerCase("tr-TR")
    .replace(/ç/g, "c").replace(/ğ/g, "g").replace(/ı/g, "i")
    .replace(/ö/g, "o").replace(/ş/g, "s").replace(/ü/g, "u")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/&[^;]+;/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
}

function withTimeout(p, ms) {
  return new Promise(function (resolve, reject) {
    var done = false;
    var t = (typeof setTimeout === "function")
      ? setTimeout(function () {
          if (!done) { done = true; reject(new Error("timeout")); }
        }, ms)
      : null;
    function clear() {
      if (t && typeof clearTimeout === "function") clearTimeout(t);
    }
    p.then(function (v) {
      if (!done) { done = true; clear(); resolve(v); }
    }, function (e) {
      if (!done) { done = true; clear(); reject(e); }
    });
  });
}

function getJson(url, ms) {
  return withTimeout(
    fetch(url, { headers: { "User-Agent": UA } }).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    }),
    ms || REQ_TIMEOUT
  );
}

function getText(url) {
  return withTimeout(
    fetch(url, { headers: { "User-Agent": UA, "Referer": BASE + "/" } }).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.text();
    }),
    REQ_TIMEOUT
  );
}

// ---- Indeks: TMDB ID -> Rumble ID (katalog scripti uretir) ----
// Bir kez cekilir, bellekte tutulur. Cekilemezse null doner ve sonraki istekte tekrar denenir.
var indexPromise = null;
function loadIndex() {
  if (!indexPromise) {
    indexPromise = getJson(INDEX_URL, 6000).catch(function () {
      indexPromise = null;
      return null;
    });
  }
  return indexPromise;
}
// Modul yuklenir yuklenmez isitma turu: ilk filmde bekleme olmasin.
try { loadIndex(); } catch (e) {}

function rumbleId(html) {
  var m = (html || "").match(/rumble\.com\\?\/embed\\?\/([a-z0-9]+)/i);
  return m ? m[1] : null;
}

function rumbleStreams(vid, label) {
  return getText("https://rumble.com/embed/" + vid + "/").then(function (html) {
    var out = [];
    var hls = html.match(/"hls":\{"auto":\{"url":"([^"]+)"/);
    if (hls) {
      out.push({
        name: "Yeşilçam TV",
        title: label + " - Türkçe - HLS",
        url: hls[1].replace(/\\\//g, "/"),
        quality: "Auto",
        headers: { "Referer": "https://rumble.com/", "User-Agent": UA }
      });
    }
    return out;
  });
}

function fromEntry(e) {
  var label = e.n || "Yeşilçam";
  if (e.r) return rumbleStreams(e.r, label);
  if (e.u) {
    return getText(e.u).then(function (h) {
      var v = rumbleId(h);
      return v ? rumbleStreams(v, label) : [];
    });
  }
  return Promise.resolve([]);
}

// ---- Yedek: indeks hic cekilemezse eski yontem (site aramasi) ----
function tmdbTitles(id) {
  var u = "https://api.themoviedb.org/3/movie/" + id + "?api_key=" + TMDB_API_KEY + "&language=tr-TR";
  return getJson(u).then(function (d) {
    var out = [];
    [d.title, d.original_title].forEach(function (t) {
      if (t && out.indexOf(t) === -1) out.push(t);
    });
    return out;
  });
}

function searchSite(q) {
  var u = BASE + "/wp-json/wp/v2/posts?per_page=5&_fields=link,title,content&search=" + encodeURIComponent(q);
  return getJson(u).catch(function () { return []; });
}

function searchAll(titles) {
  return Promise.all(titles.map(searchSite)).then(function (lists) {
    var seen = {};
    var all = [];
    lists.forEach(function (list) {
      (list || []).forEach(function (p) {
        if (p && p.link && !seen[p.link]) { seen[p.link] = true; all.push(p); }
      });
    });
    return all;
  });
}

function pickPost(posts, titles) {
  var want = titles.map(norm);
  var exact = posts.filter(function (p) { return want.indexOf(norm(p.title.rendered)) !== -1; });
  if (exact.length) return exact[0];
  var part = posts.filter(function (p) {
    var t = norm(p.title.rendered);
    return want.some(function (w) { return w && (t.indexOf(w) !== -1 || w.indexOf(t) !== -1); });
  });
  return part[0] || null;
}

function legacy(tmdbId) {
  return tmdbTitles(tmdbId).then(function (titles) {
    if (!titles.length) return [];
    return searchAll(titles).then(function (posts) {
      var post = pickPost(posts, titles);
      if (!post) return [];
      var label = post.title.rendered;
      var vid = rumbleId(post.content && post.content.rendered);
      if (vid) return rumbleStreams(vid, label);
      return getText(post.link).then(function (h) {
        var v = rumbleId(h);
        return v ? rumbleStreams(v, label) : [];
      });
    });
  });
}

function getStreams(tmdbId, mediaType, season, episode) {
  if (mediaType !== "movie") return Promise.resolve([]);
  return loadIndex()
    .then(function (idx) {
      // Indeks yoksa (cekilemedi) eski yonteme dus.
      if (!idx) return legacy(tmdbId);
      // Indeks var ama film yoksa: sitede yok, bosuna arama yapma.
      var e = idx[String(tmdbId)];
      return e ? fromEntry(e) : [];
    })
    .catch(function () { return []; });
}

module.exports = { getStreams: getStreams };
