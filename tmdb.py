import requests

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

def get_tmdb_key():
    try:
        import json
        with open("config.json", "r") as f:
            config = json.load(f)
        return config.get("tmdb", {}).get("api_key", "")
    except:
        return ""

def search_tmdb(query):
    api_key = get_tmdb_key()
    if not api_key:
        return None

    url = f"{TMDB_BASE}/search/multi"
    params = {
        "api_key": api_key,
        "query": query,
        "include_adult": False
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        # Return best match — first result
        return results[0]
    except:
        return None

def get_movie_details(tmdb_id):
    api_key = get_tmdb_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            f"{TMDB_BASE}/movie/{tmdb_id}",
            params={"api_key": api_key},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except:
        return None

def get_tv_details(tmdb_id):
    api_key = get_tmdb_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            f"{TMDB_BASE}/tv/{tmdb_id}",
            params={"api_key": api_key},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except:
        return None

def get_season_episodes(tmdb_id, season_number):
    api_key = get_tmdb_key()
    if not api_key:
        return []
    try:
        response = requests.get(
            f"{TMDB_BASE}/tv/{tmdb_id}/season/{season_number}",
            params={"api_key": api_key},
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("episodes", [])
    except:
        return []

def get_collection(collection_id):
    api_key = get_tmdb_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            f"{TMDB_BASE}/collection/{collection_id}",
            params={"api_key": api_key},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except:
        return None

def get_poster_url(poster_path):
    if not poster_path:
        return None
    return f"{TMDB_IMAGE_BASE}{poster_path}"

def detect_media_type(tmdb_result):
    if not tmdb_result:
        return "unknown"
    return tmdb_result.get("media_type", "unknown")

def get_media_info(query):
    result = search_tmdb(query)
    if not result:
        return {
            "type": "unknown",
            "title": query,
            "year": None,
            "poster": None,
            "tmdb_id": None,
            "overview": None
        }

    media_type = detect_media_type(result)
    poster = get_poster_url(result.get("poster_path"))

    if media_type == "movie":
        title = result.get("title", query)
        year = result.get("release_date", "")[:4] or None
    elif media_type == "tv":
        title = result.get("name", query)
        year = result.get("first_air_date", "")[:4] or None
    else:
        title = result.get("title") or result.get("name") or query
        year = None

    return {
        "type": media_type,
        "title": title,
        "year": year,
        "poster": poster,
        "tmdb_id": result.get("id"),
        "overview": result.get("overview")
    }

def get_all_episodes(tmdb_id):
    details = get_tv_details(tmdb_id)
    if not details:
        return {}

    seasons = {}
    for season in details.get("seasons", []):
        season_num = season.get("season_number", 0)
        if season_num == 0:
            continue
        episodes = get_season_episodes(tmdb_id, season_num)
        seasons[season_num] = [
            {
                "episode_number": ep.get("episode_number"),
                "name": ep.get("name"),
                "air_date": ep.get("air_date"),
                "overview": ep.get("overview"),
                "still_path": get_poster_url(ep.get("still_path"))
            }
            for ep in episodes
        ]

    return seasons