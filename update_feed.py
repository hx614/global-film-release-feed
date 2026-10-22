#!/usr/bin/env python3
"""Build a rolling release feed from TMDB and TVmaze public APIs."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
from html import unescape
from html.parser import HTMLParser
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
TMDB = "https://api.themoviedb.org/3"
TVMAZE = "https://api.tvmaze.com"
IMAGE = "https://image.tmdb.org/t/p/w500"

# Release markets map to the app's region selector. More countries can be added
# without changing the consumer schema.
MARKETS = [
    ("CN", "中国大陆"), ("HK", "中国香港"), ("TW", "中国台湾"),
    ("US", "美国"), ("GB", "英国"), ("JP", "日本"),
    ("KR", "韩国"), ("IN", "印度"), ("TH", "泰国"), ("SG", "新加坡"),
]
COUNTRIES = ["CN", "HK", "TW", "US", "GB", "JP", "KR", "IN", "TH", "SG"]
RELEASE_TYPES = {3: "院线", 4: "数字发行", 5: "实体碟"}


class TextOnlyHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "li", "div"} and self.parts:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif tag in {"p", "li", "div"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = TextOnlyHTML()
    parser.feed(value)
    return " ".join(unescape("".join(parser.parts)).split())


def fetch_json(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, headers={"User-Agent": "GlobalFilmRelease/1.0", **(headers or {})})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code < 600:
                time.sleep(min(2 ** attempt, 16))
                continue
            raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"Too many retries for {url}")


def date_text(date: dt.date) -> str:
    return f"{date.year}年{date.month}月{date.day}日"


def area_for(country_codes: list[str], language: str = "") -> str:
    codes = set(country_codes)
    if "CN" in codes:
        return "大陆"
    if codes.intersection({"HK", "MO", "TW"}):
        return "港澳台"
    if "JP" in codes or language == "ja":
        return "日本"
    if "KR" in codes or language == "ko":
        return "韩国"
    if codes.intersection({"US", "GB", "CA", "AU", "NZ", "IE", "FR", "DE", "IT", "ES", "NL", "SE", "NO", "DK", "FI", "BE", "CH", "AT", "PT", "PL", "RU"}):
        return "欧美"
    if language in {"en", "fr", "de", "it", "es", "pt", "ru", "sv", "da", "no", "fi", "nl"}:
        return "欧美"
    return "其他地区"


def tmdb_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in value if char.isalnum())


def douban_match(titles: list[str], year: str = "", media_type: str = "movie") -> dict | None:
    """Resolve one exact Douban subject, retrying through a fetch proxy if needed."""
    wanted = {normalize_title(title) for title in titles if normalize_title(title)}
    original_titles = {normalize_title(title) for title in titles[1:] if normalize_title(title)}
    queries = list(dict.fromkeys(title.strip() for title in titles if title and title.strip()))
    for query in queries:
        query_string = urllib.parse.urlencode({"q": query})
        endpoints = [
            ("Douban", f"https://movie.douban.com/j/subject_suggest?{query_string}"),
            ("Douban proxy", f"https://r.jina.ai/http://movie.douban.com/j/subject_suggest?{query_string}"),
        ]
        for source, url in endpoints:
            request = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                "Referer": "https://movie.douban.com/",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    body = response.read().decode("utf-8-sig")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
                print(f"{source} lookup unavailable for {query!r}: {exc}")
                continue
            if source == "Douban proxy":
                marker = "Markdown Content:"
                if marker in body:
                    body = body.split(marker, 1)[1].strip()
            try:
                candidates = json.loads(body)
            except ValueError:
                print(f"{source} returned unparseable suggestions for {query!r}")
                continue
            if not isinstance(candidates, list):
                continue
            exact = []
            for candidate in candidates:
                if not isinstance(candidate, dict) or not candidate.get("id"):
                    continue
                candidate_titles = {
                    normalize_title(str(candidate.get("title") or "")),
                    normalize_title(str(candidate.get("sub_title") or "")),
                }
                if not wanted.intersection(candidate_titles):
                    continue
                candidate["_original_title_match"] = bool(original_titles.intersection(candidate_titles))
                candidate_type = str(candidate.get("type") or "").casefold()
                if candidate_type and media_type == "movie" and candidate_type not in {"movie", "film"}:
                    continue
                if candidate_type and media_type == "tv" and candidate_type not in {"tv", "tv show", "tv series", "series"}:
                    continue
                exact.append(candidate)
            if year:
                same_year = [item for item in exact if str(item.get("year") or "").isdigit() and abs(int(item["year"]) - int(year)) <= 1]
                if same_year:
                    exact = same_year
                elif exact and all(item.get("year") for item in exact) and not any(item.get("_original_title_match") for item in exact):
                    continue
            ids = {str(item["id"]) for item in exact}
            if len(ids) != 1:
                continue
            result = next(item for item in exact if str(item["id"]) in ids)
            subject_id = str(result["id"])
            return {
                "doubanId": subject_id,
                "doubanUrl": f"https://movie.douban.com/subject/{subject_id}/",
                "doubanPosterUrl": str(result.get("pic") or result.get("img") or ""),
            }
    return None


def load_existing_douban_mappings() -> tuple[dict[str, dict], dict[str, dict]]:
    """Reuse verified links so unchanged works do not hit Douban again every day."""
    try:
        with open("releases.json", encoding="utf-8") as source:
            old_films = json.load(source).get("films", {})
    except (OSError, ValueError, TypeError):
        return {}, {}
    movies: dict[str, dict] = {}
    shows: dict[str, dict] = {}
    for item_id, item in old_films.items():
        if not isinstance(item, dict) or not item.get("doubanId"):
            continue
        mapping = {key: item[key] for key in ("doubanId", "doubanUrl", "doubanPosterUrl") if item.get(key)}
        if item.get("tmdbId"):
            movies[str(item["tmdbId"])] = mapping
        elif item_id.startswith("tvmaze-"):
            shows[item_id.split("-", 2)[1]] = mapping
    return movies, shows


def get_movie_candidates(token: str, start: dt.date, end: dt.date) -> list[dict]:
    headers = tmdb_headers(token)
    candidates: dict[tuple[int, int], dict] = {}
    for market, market_name in MARKETS:
        for release_type, kind in RELEASE_TYPES.items():
            for page in (1, 2):
                query = urllib.parse.urlencode({
                    "region": market,
                    "release_date.gte": start.isoformat(),
                    "release_date.lte": end.isoformat(),
                    "with_release_type": release_type,
                    "sort_by": "popularity.desc",
                    "include_adult": "false",
                    "language": "zh-CN",
                    "page": page,
                })
                data = fetch_json(f"{TMDB}/discover/movie?{query}", headers)
                for movie in data.get("results", []):
                    date_string = movie.get("release_date")
                    if not date_string:
                        continue
                    key = (int(movie["id"]), release_type)
                    candidate = candidates.setdefault(key, {
                        **movie,
                        "releaseType": kind,
                        "releaseTypeId": release_type,
                        "releaseMarkets": {},
                    })
                    candidate["releaseMarkets"][market] = {
                        "market": market,
                        "region": market_name,
                        "dateISO": date_string,
                    }
                if page >= min(int(data.get("total_pages", 1)), 2):
                    break
    return list(candidates.values())


def movie_record(movie: dict, details: dict) -> tuple[dict, str, str]:
    original = plain_text(details.get("original_title") or movie.get("original_title") or movie.get("title") or "")
    title = plain_text(details.get("title") or movie.get("title") or original)
    overview = plain_text(details.get("overview") or movie.get("overview") or "")
    if not overview:
        overview = "简介暂缺，详情请查看 TMDB 片目页。"
    releases = sorted(movie.get("releaseMarkets", {}).values(), key=lambda release: (release["dateISO"], release["region"]))
    if not releases:
        releases = [{"market": movie.get("market", ""), "region": movie.get("marketName", ""), "dateISO": movie["dateISO"]}]
    today = dt.datetime.now(BEIJING).date()
    release_dates = [dt.date.fromisoformat(release["dateISO"]) for release in releases]
    date_value = next((date for date in release_dates if date == today), None)
    if date_value is None:
        date_value = next((date for date in release_dates if date > today), release_dates[-1])
    release_regions = list(dict.fromkeys(release["region"] for release in releases))
    area = area_for([c.get("iso_3166_1", "") for c in details.get("production_countries", [])], details.get("original_language", ""))
    media = {
        "title": title,
        "original": original,
        "platform": f"{movie['releaseType']}（{'、'.join(release_regions)}）",
        "date": date_text(date_value),
        "region": "、".join(release_regions),
        "releaseMarkets": [
            {"region": release["region"], "date": date_text(dt.date.fromisoformat(release["dateISO"]))}
            for release in releases
        ],
        "posterUrl": IMAGE + details["poster_path"] if details.get("poster_path") else "",
        "tmdbId": str(movie["id"]),
        "tmdbRating": f"{details['vote_average']:.1f}/10" if details.get("vote_count", 0) else "",
        "summary": overview,
    }
    imdb_id = details.get("imdb_id")
    if imdb_id:
        media["imdbId"] = imdb_id
    return media, area, f"https://www.themoviedb.org/movie/{movie['id']}"


def tvmaze_days(start: dt.date, end: dt.date) -> list[tuple[str, dt.date, dict]]:
    requests: list[tuple[str, dt.date]] = []
    current = start
    while current <= end:
        for country in COUNTRIES:
            requests.append((country, current))
        current += dt.timedelta(days=1)

    def get_schedule(request: tuple[str, dt.date]):
        country, date = request
        query = urllib.parse.urlencode({"date": date.isoformat(), "country": country})
        return country, fetch_json(f"{TVMAZE}/schedule/web?{query}")

    found: list[tuple[str, dt.date, dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for country, entries in pool.map(get_schedule, requests):
            for entry in entries:
                show = entry.get("show") or {}
                if show.get("type") not in {"Scripted", "Animation", "Documentary"}:
                    continue
                # TVmaze schedule dates are supplied in the service's local market.
                # Keep that date and label the market in the platform field.
                airdate = entry.get("airdate")
                if not airdate:
                    stamp = entry.get("airstamp")
                    if not stamp:
                        continue
                    airdate = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(BEIJING).date().isoformat()
                try:
                    air_date = dt.date.fromisoformat(airdate)
                except ValueError:
                    continue
                found.append((country, air_date, entry))
    return found


def build_tv_items(rows: list[tuple[str, dt.date, dict]], films: dict, areas: dict, kinds: dict, sources: dict, douban_by_show: dict[str, dict]):
    for country, air_date, episode in rows:
        show = episode.get("show") or {}
        show_id = show.get("id")
        if show_id is None:
            continue
        market_name = next((name for code, name in MARKETS if code == country), country)
        channel = show.get("webChannel") or show.get("network") or {}
        channel_name = channel.get("name") or "流媒体"
        season, number = episode.get("season"), episode.get("number")
        episode_suffix = f" S{season:02d}E{number:02d}" if season is not None and number is not None else ""
        item_id = f"tvmaze-{show_id}-{country}-{air_date.isoformat()}-{season or 0}-{number or 0}".replace(".", "-")
        title = show.get("name") or "未命名剧集"
        episode_name = episode.get("name") or ""
        summary = plain_text(episode.get("summary") or show.get("summary") or "简介暂缺，详情请查看 TVmaze 片目页。")
        image = (show.get("image") or {}).get("original") or (show.get("image") or {}).get("medium") or ""
        films[item_id] = {
            "title": plain_text(title),
            "original": plain_text(title),
            "platform": f"{channel_name}（{market_name}）",
            "date": date_text(air_date),
            "region": market_name,
            "posterUrl": image,
            "summary": (f"{plain_text(episode_name)}{episode_suffix}。" if episode_name else f"更新{episode_suffix}。") + summary,
        }
        if douban_by_show.get(str(show_id)):
            films[item_id].update(douban_by_show[str(show_id)])
        films[item_id]["tvmazeId"] = str(show_id)
        country_info = (channel.get("country") or {}).get("code") or country
        areas[item_id] = area_for([country_info], show.get("language", ""))
        kinds[item_id] = "电视剧"
        sources[item_id] = show.get("url") or f"https://www.tvmaze.com/shows/{show_id}"


def main() -> int:
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        print("TMDB_READ_ACCESS_TOKEN secret is required. No feed was generated.", file=sys.stderr)
        return 2

    today = dt.datetime.now(BEIJING).date()
    movie_start, movie_end = today - dt.timedelta(days=7), today + dt.timedelta(days=90)
    tv_end = today + dt.timedelta(days=30)
    films: dict[str, dict] = {}
    areas: dict[str, str] = {}
    kinds: dict[str, str] = {}
    sources: dict[str, str] = {}
    cross_sources: dict[str, str] = {}
    old_movie_links, old_show_links = load_existing_douban_mappings()

    movies = get_movie_candidates(token, movie_start, movie_end)
    print(f"TMDB candidate releases: {len(movies)}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        movie_ids = sorted({int(movie["id"]) for movie in movies})
        detail_rows = pool.map(lambda movie_id: (movie_id, fetch_json(f"{TMDB}/movie/{movie_id}?language=zh-CN", tmdb_headers(token))), movie_ids)
        details_by_id = dict(detail_rows)
    movie_douban: dict[str, dict] = dict(old_movie_links)
    missing_movie_ids = [movie_id for movie_id in movie_ids if str(movie_id) not in movie_douban]
    def lookup_movie(movie_id: int):
        detail = details_by_id[movie_id]
        title = plain_text(detail.get("title") or "")
        original = plain_text(detail.get("original_title") or title)
        release_year = str((detail.get("release_date") or "")[:4])
        return str(movie_id), douban_match([title, original], release_year, "movie")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for tmdb_id, match in pool.map(lookup_movie, missing_movie_ids):
            if match:
                movie_douban[tmdb_id] = match
    matched_movie_count = 0
    for movie in movies:
        item, area, source = movie_record(movie, details_by_id[int(movie["id"])])
        key = f"tmdb-{movie['id']}-{movie['releaseTypeId']}"
        if movie_douban.get(str(movie["id"])):
            item.update(movie_douban[str(movie["id"])])
            matched_movie_count += 1
        films[key] = item
        areas[key] = area
        kinds[key] = "电影"
        sources[key] = source

    # Episode schedules are refreshed daily; TMDB release calendar records are
    # retained for the full 90-day window.
    tv_start = today - dt.timedelta(days=7)
    rows = tvmaze_days(tv_start, tv_end)
    print(f"TVmaze scheduled episode releases: {len(rows)}")
    show_candidates: dict[str, tuple[str, str]] = {}
    for _, _, episode in rows:
        show = episode.get("show") or {}
        show_id = show.get("id")
        if show_id is not None and str(show_id) not in old_show_links:
            show_candidates[str(show_id)] = (plain_text(show.get("name") or ""), str((show.get("premiered") or "")[:4]))
    def lookup_show(entry: tuple[str, tuple[str, str]]):
        show_id, (title, year) = entry
        return show_id, douban_match([title], year, "tv")
    show_douban: dict[str, dict] = dict(old_show_links)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for show_id, match in pool.map(lookup_show, show_candidates.items()):
            if match:
                show_douban[show_id] = match
    build_tv_items(rows, films, areas, kinds, sources, show_douban)
    missing_links = [key for key, item in films.items() if not (item.get("doubanId") and item.get("doubanUrl") == f"https://movie.douban.com/subject/{item.get('doubanId')}/")]
    for key in missing_links:
        films.pop(key, None)
        areas.pop(key, None)
        kinds.pop(key, None)
        sources.pop(key, None)
        cross_sources.pop(key, None)
    linked_movie_count = sum(1 for key in films if key.startswith("tmdb-"))
    linked_show_count = sum(1 for key in films if key.startswith("tvmaze-"))
    print(f"Douban direct links: {linked_movie_count} films / {linked_show_count} TV shows; excluded {len(missing_links)} items without an exact subject")

    payload = {
        "updatedAt": dt.datetime.now(BEIJING).isoformat(timespec="seconds"),
        "completeSnapshot": True,
        "attribution": [
            {"name": "TMDB", "url": "https://www.themoviedb.org/", "text": "This product uses the TMDB API but is not endorsed or certified by TMDB."},
            {"name": "TVmaze", "url": "https://www.tvmaze.com/", "text": "TV schedule data provided by TVmaze."},
        ],
        "films": films,
        "filmAreas": areas,
        "mediaKinds": kinds,
        "contentSources": sources,
        "crossSources": cross_sources,
    }
    with open("releases.json", "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(f"Wrote {len(films)} entries to releases.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
