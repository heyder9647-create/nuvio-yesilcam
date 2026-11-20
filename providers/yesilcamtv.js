var TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c";
var BASE = "https://yesilcamtv.com.tr";
var UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36";

function norm(s) {
  return (s || "").toLocaleLowerCase("tr-TR")
    .replace(/ç/g, "c").replace(/ğ/g, "g").replace(/ı/g, "i")
    .replace(/ö/g, "o").replace(/ş/g, "s").replace(/ü/g, "u")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/&[^;]+;/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
}

function getJson(url) {
  return fetch(url, { headers: { "User-Agent": UA } }).then(function (r) { return r.json(); });
}
function getText(url) {
  return fetch(url, { headers: { "User-Agent": UA, "Referer": BASE + "/" } }).then(function (r) { return r.text(); });
}

function tmdbInfo(id) {
  var u = "https://api.themoviedb.org/3/movie/" + id + "?api_key=" + TMDB_API_KEY + "&language=tr-TR";
  return getJson(u).then(function (d) {
    return { titles: [d.title, d.original_title].filter(Boolean) };
  });
}

function searchSite(q) {
  var u = BASE + "/wp-json/wp/v2/posts?per_page=10&_fields=link,title,content&search=" + encodeURIComponent(q);
  return getJson(u).catch(function () { return []; });
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

function rumbleId(html) {
  var m = (html || "").match(/rumble\.com\/embed\/([a-z0-9]+)/i);
  return m ? m[1] : null;
}

function rumbleStreams(vid, label) {
  return getText("https://rumble.com/embed/" + vid + "/").then(function (html) {
    var out = [];
    var hls = html.match(/"hls":\{"auto":\{"url":"([^"]+)"/);
    if (hls) {
      out.push({
        name: "Yeşilçam TV",
        title: label + " - Otomatik (HLS)",
        url: hls[1].replace(/\\\//g, "/"),
        quality: "Auto",
        headers: { "Referer": "https://rumble.com/", "User-Agent": UA }
      });
    }
    return out;
  });
}

function getStreams(tmdbId, mediaType, season, episode) {
  if (mediaType !== "movie") return Promise.resolve([]);
  return tmdbInfo(tmdbId).then(function (info) {
    var titles = info.titles;
    function tryNext(i) {
      if (i >= titles.length) return Promise.resolve(null);
      return searchSite(titles[i]).then(function (posts) {
        var p = pickPost(posts || [], titles);
        return p || tryNext(i + 1);
      });
    }
    return tryNext(0).then(function (post) {
      if (!post) return [];
      var vid = rumbleId(post.content && post.content.rendered);
      if (vid) return rumbleStreams(vid, post.title.rendered);
      return getText(post.link).then(function (h) {
        var v = rumbleId(h);
        return v ? rumbleStreams(v, post.title.rendered) : [];
      });
    });
  }).catch(function (e) { console.log("[YesilcamTV] " + e.message); return []; });
}

module.exports = { getStreams: getStreams };
