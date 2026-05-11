import asyncio
import shutil
import re
import base64
from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
import json
import os
import string
from datetime import date, datetime
from typing import List
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from parser import parse_torrent_title
from profiles import (
    score_torrent, get_best_match, load_profiles, save_profiles,
    get_default_profile, get_profile_by_name, matches_profile,
    ALL_RESOLUTIONS, ALL_HDR, ALL_AUDIO, ALL_SOURCES, DEFAULT_PROFILE
)

app = FastAPI()
scheduler = AsyncIOScheduler()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- In-memory cache ---
import time as _time

_cache: dict = {}

def cache_get(key):
    entry = _cache.get(key)
    if not entry:
        return None
    value, expires = entry
    if _time.monotonic() > expires:
        del _cache[key]
        return None
    return value

def cache_set(key, value, ttl=300):
    _cache[key] = (value, _time.monotonic() + ttl)

def cache_clear(prefix=None):
    if prefix is None:
        _cache.clear()
    else:
        for k in list(_cache.keys()):
            if k.startswith(prefix):
                del _cache[k]

CONFIG_FILE = "config.json"
DUAL_EP_FILE = "dual_episodes.json"
WATCHLIST_FILE = "watchlist.json"

def config_exists():
    return os.path.exists(CONFIG_FILE)

def load_config():
    if not config_exists():
        return None
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_dual_ep():
    if not os.path.exists(DUAL_EP_FILE):
        return {}
    with open(DUAL_EP_FILE) as f:
        return json.load(f)

def save_dual_ep(data):
    with open(DUAL_EP_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE) as f:
        return json.load(f)

def save_watchlist(data):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- TMDB ---
def tmdb_key():
    config = load_config()
    return config.get("tmdb", {}).get("api_key", "") if config else ""

def tmdb_get(path, params=None, ttl=86400):
    cache_key = f"tmdb:{path}:{params}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    key = tmdb_key()
    if not key:
        return None
    url = f"https://api.themoviedb.org/3{path}"
    p = {"api_key": key}
    if params:
        p.update(params)
    try:
        r = requests.get(url, params=p, timeout=10)
        r.raise_for_status()
        data = r.json()
        cache_set(cache_key, data, ttl)
        return data
    except Exception:
        return None

def tmdb_poster(path):
    if not path:
        return None
    return f"https://image.tmdb.org/t/p/w500{path}"

async def tmdb_get_async(path, params=None):
    return await asyncio.to_thread(tmdb_get, path, params)

# --- Media Server ---
def get_server_config():
    config = load_config()
    if not config:
        return {}
    if "media_server" in config:
        return config["media_server"]
    if "jellyfin" in config:
        return {
            "type": "jellyfin",
            "url": config["jellyfin"].get("url", ""),
            "api_key": config["jellyfin"].get("api_key", "")
        }
    return {}

def get_server_type():
    return get_server_config().get("type", "jellyfin")

def media_server_request(path, params=None):
    sc = get_server_config()
    if not sc:
        return None
    server_type = sc.get("type", "jellyfin")
    base_url = sc.get("url", "")
    token = sc.get("api_key", "")
    if not base_url or not token:
        return None
    try:
        if server_type == "plex":
            r = requests.get(f"{base_url}{path}", headers={"X-Plex-Token": token, "Accept": "application/json"}, params=params or {}, timeout=15)
        else:
            r = requests.get(f"{base_url}{path}", headers={"X-Emby-Token": token}, params=params or {}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Media server error: {e}")
        return None

def media_server_post(path, params=None, body=None):
    sc = get_server_config()
    if not sc:
        return None
    server_type = sc.get("type", "jellyfin")
    base_url = sc.get("url", "")
    token = sc.get("api_key", "")
    if not base_url or not token:
        return None
    try:
        if server_type == "plex":
            requests.post(f"{base_url}{path}", headers={"X-Plex-Token": token, "Accept": "application/json"}, params=params or {}, json=body, timeout=10)
        else:
            requests.post(f"{base_url}{path}", headers={"X-Emby-Token": token}, params=params or {}, json=body, timeout=10)
    except Exception:
        pass

def get_user_id():
    if get_server_type() == "plex":
        return "plex"
    data = media_server_request("/Users")
    if data and len(data) > 0:
        return data[0]["Id"]
    return None

def get_poster_url(item_id, has_primary):
    if not has_primary:
        return None
    config = load_config() or {}
    sc = config.get("media_server", {})
    server_type = sc.get("type", "jellyfin")
    base_url = sc.get("url", "")
    token = sc.get("api_key", "")
    if server_type == "plex":
        return f"{base_url}/photo/:/transcode?url=/library/metadata/{item_id}/thumb&width=400&X-Plex-Token={token}"
    return f"{base_url}/Items/{item_id}/Images/Primary?api_key={token}&maxHeight=400"

def get_library_items(user_id, include_types="Series,Movie"):
    cache_key = f"library:{user_id}:{include_types}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    server_type = get_server_type()
    if server_type == "plex":
        data = media_server_request("/library/sections")
        if not data:
            return []
        items = []
        for section in data.get("MediaContainer", {}).get("Directory", []):
            section_type = section.get("type")
            if section_type not in ("show", "movie"):
                continue
            key = section.get("key")
            section_data = media_server_request(f"/library/sections/{key}/all")
            if not section_data:
                continue
            for item in section_data.get("MediaContainer", {}).get("Metadata", []):
                rating_key = str(item.get("ratingKey", ""))
                tmdb_id = None
                for g in item.get("Guid", []):
                    gid = g.get("id", "")
                    if gid.startswith("tmdb://"):
                        tmdb_id = gid.replace("tmdb://", "")
                items.append({
                    "Id": rating_key,
                    "Name": item.get("title", ""),
                    "Type": "Series" if section_type == "show" else "Movie",
                    "ProductionYear": item.get("year"),
                    "ProviderIds": {"Tmdb": tmdb_id} if tmdb_id else {},
                    "_has_primary": True
                })
        cache_set(cache_key, items, 300)
        return items
    else:
        data = media_server_request(f"/Users/{user_id}/Items", params={
            "Recursive": "true",
            "IncludeItemTypes": include_types,
            "Fields": "ProviderIds,ProductionYear,ImageTags",
            "SortBy": "SortName",
            "SortOrder": "Ascending"
        })
        result = data.get("Items", []) if data else []
        cache_set(cache_key, result, 300)
        return result

def get_item(user_id, item_id, fields="ProviderIds"):
    if get_server_type() == "plex":
        data = media_server_request(f"/library/metadata/{item_id}")
        if not data:
            return None
        items = data.get("MediaContainer", {}).get("Metadata", [])
        if not items:
            return None
        item = items[0]
        tmdb_id = None
        for g in item.get("Guid", []):
            gid = g.get("id", "")
            if gid.startswith("tmdb://"):
                tmdb_id = gid.replace("tmdb://", "")
        return {
            "Id": str(item.get("ratingKey", "")),
            "Name": item.get("title", ""),
            "ProviderIds": {"Tmdb": tmdb_id} if tmdb_id else {},
            "MediaSources": [],
            "ImageTags": {"Primary": True}
        }
    return media_server_request(f"/Users/{user_id}/Items/{item_id}", params={"Fields": fields})

def get_episodes(show_id):
    if get_server_type() == "plex":
        data = media_server_request(f"/library/metadata/{show_id}/allLeaves")
        if not data:
            return []
        return [{
            "ParentIndexNumber": ep.get("parentIndex"),
            "IndexNumber": ep.get("index"),
            "IndexNumberEnd": None,
            "Id": str(ep.get("ratingKey", "")),
            "MediaSources": []
        } for ep in data.get("MediaContainer", {}).get("Metadata", [])]
    else:
        data = media_server_request(f"/Shows/{show_id}/Episodes", params={"Fields": "MediaSources"})
        return data.get("Items", []) if data else []

def get_movie_tmdb_map():
    user_id = get_user_id()
    if not user_id:
        return {}
    items = get_library_items(user_id, include_types="Movie")
    return {str(item.get("ProviderIds", {}).get("Tmdb", "")): item["Id"]
            for item in items if item.get("ProviderIds", {}).get("Tmdb")}

# --- Download Path Builder ---
def build_download_path(media_type: str, title: str, year: str = None, season: int = None) -> str:
    config = load_config() or {}
    media_config = config.get("media", {})
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title).strip()

    if media_type == "movie":
        folders = media_config.get("movie_folders", [])
        base = folders[0] if folders else media_config.get("download_folder", "/downloads")
        folder_name = f"{safe_title} ({year})" if year else safe_title
        return os.path.join(base, folder_name)
    elif media_type == "tv":
        folders = media_config.get("tv_folders", [])
        base = folders[0] if folders else media_config.get("download_folder", "/downloads")
        show_folder = os.path.join(base, safe_title)
        if season is not None:
            return os.path.join(show_folder, f"Season {int(season):02d}")
        return show_folder
    else:
        return media_config.get("download_folder", "/downloads")

# --- Prowlarr ---
def search_prowlarr(query, tmdb_id=None, media_type=None):
    config = load_config()
    if not config:
        return []
    url = f"{config['prowlarr']['url']}/api/v1/search"
    api_key = config['prowlarr']['api_key']
    seen = set()
    all_results = []

    def collect(params):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                return
            for result in data:
                title = result.get("title", "")
                if title and title not in seen:
                    seen.add(title)
                    all_results.append(result)
        except requests.exceptions.RequestException as e:
            print(f"Prowlarr error: {e}")

    # Always search by title — works with all indexers
    # Include year in query for movies to narrow results
    collect({"query": query, "apikey": api_key, "type": "search"})
    return all_results

# --- qBittorrent ---
def add_to_qbit(download_url, save_path):
    config = load_config()
    if not config:
        return False, "No config"
    qbit_url = f"{config['qbittorrent']['url']}/api/v2"
    session = requests.Session()
    try:
        login = session.post(f"{qbit_url}/auth/login", data={
            "username": config['qbittorrent']['username'],
            "password": config['qbittorrent']['password']
        }, timeout=10)
        if login.text.strip() != "Ok.":
            return False, f"qBit login failed: {login.text.strip()}"
        os.makedirs(save_path, exist_ok=True)
        add = session.post(f"{qbit_url}/torrents/add", data={
            "urls": download_url,
            "savepath": save_path
        }, timeout=15)
        if add.text.strip() == "Ok.":
            return True, "OK"
        return False, f"qBit rejected: {add.text.strip()}"
    except requests.exceptions.RequestException as e:
        print(f"qBittorrent error: {e}")
        return False, str(e)

# --- OpenSubtitles ---
_opensub_token = None

def opensub_login():
    global _opensub_token
    config = load_config()
    if not config:
        return None
    os_config = config.get("opensubtitles", {})
    api_key = os_config.get("api_key", "")
    username = os_config.get("username", "")
    password = os_config.get("password", "")
    if not api_key or not username or not password:
        return None
    try:
        r = requests.post(
            "https://api.opensubtitles.com/api/v1/login",
            json={"username": username, "password": password},
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            timeout=10
        )
        r.raise_for_status()
        _opensub_token = r.json().get("token")
        return _opensub_token
    except Exception as e:
        print(f"OpenSubtitles login error: {e}")
        return None

def opensub_search(query, languages="en", season=None, episode=None, imdb_id=None, tmdb_id=None):
    config = load_config()
    if not config:
        return []
    api_key = config.get("opensubtitles", {}).get("api_key", "")
    if not api_key:
        return []
    token = _opensub_token or opensub_login()
    if not token:
        return []
    params = {"languages": languages, "order_by": "download_count"}
    if imdb_id:
        params["imdb_id"] = imdb_id
    elif tmdb_id:
        params["tmdb_id"] = tmdb_id
    else:
        params["query"] = query
    if season:
        params["season_number"] = season
    if episode:
        params["episode_number"] = episode
    try:
        r = requests.get(
            "https://api.opensubtitles.com/api/v1/subtitles",
            headers={"Api-Key": api_key, "Authorization": f"Bearer {token}"},
            params=params,
            timeout=10
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"OpenSubtitles search error: {e}")
        return []

def opensub_download(file_id):
    config = load_config()
    if not config:
        return None
    api_key = config.get("opensubtitles", {}).get("api_key", "")
    token = _opensub_token or opensub_login()
    if not token or not api_key:
        return None
    try:
        r = requests.post(
            "https://api.opensubtitles.com/api/v1/download",
            json={"file_id": file_id},
            headers={"Api-Key": api_key, "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10
        )
        r.raise_for_status()
        return r.json().get("link")
    except Exception as e:
        print(f"OpenSubtitles download error: {e}")
        return None

# --- File Browser ---
@app.get("/browse")
async def browse(path: str = None):
    if path is None:
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append({"name": drive, "path": drive, "type": "drive"})
        return {"current": None, "parent": None, "items": drives}
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return {"error": "Path not found"}
    items = []
    try:
        for item in sorted(os.listdir(path)):
            full_path = os.path.join(path, item)
            if os.path.isdir(full_path):
                items.append({"name": item, "path": full_path, "type": "folder"})
    except PermissionError:
        pass
    parent = str(os.path.dirname(path)) if path != os.path.dirname(path) else None
    return {"current": path, "parent": parent, "items": items}

# --- Home ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = None):
    if not config_exists():
        return RedirectResponse(url="/setup")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "profile": get_default_profile(),
        "profiles": load_profiles(),
        "all_resolutions": ALL_RESOLUTIONS,
        "all_hdr": ALL_HDR,
        "all_audio": ALL_AUDIO,
        "all_sources": ALL_SOURCES,
        "prefill_query": q or ""
    })

# --- Library ---
@app.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    return templates.TemplateResponse("library.html", {"request": request})

@app.get("/api/library")
async def get_library():
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server. Check your settings."}
    items_raw = await asyncio.to_thread(get_library_items, user_id)
    if not items_raw:
        return {"error": "Failed to fetch library"}
    items = []
    for item in items_raw:
        has_primary = bool(item.get("ImageTags", {}).get("Primary")) or item.get("_has_primary", False)
        items.append({
            "id": item["Id"],
            "title": item.get("Name", ""),
            "year": item.get("ProductionYear"),
            "type": "tv" if item.get("Type") == "Series" else "movie",
            "poster": get_poster_url(item["Id"], has_primary)
        })
    return {"items": items}

# --- Collections ---
@app.get("/collections", response_class=HTMLResponse)
async def collections_page(request: Request):
    return templates.TemplateResponse("collections.html", {"request": request})

@app.get("/api/collections")
async def get_collections():
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server"}
    items_raw = await asyncio.to_thread(get_library_items, user_id, "Movie")
    if not items_raw:
        return {"error": "Failed to fetch library"}
    movies = []
    for item in items_raw:
        tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
        if not tmdb_id:
            continue
        has_primary = bool(item.get("ImageTags", {}).get("Primary")) or item.get("_has_primary", False)
        movies.append({
            "jellyfin_id": item["Id"],
            "title": item.get("Name", ""),
            "year": item.get("ProductionYear"),
            "tmdb_id": tmdb_id,
            "poster": get_poster_url(item["Id"], has_primary)
        })

    async def fetch_tmdb(movie):
        data = await tmdb_get_async(f"/movie/{movie['tmdb_id']}")
        return movie, data

    results = await asyncio.gather(*[fetch_tmdb(m) for m in movies])
    collections = {}
    for movie, tmdb_data in results:
        if not tmdb_data:
            continue
        col = tmdb_data.get("belongs_to_collection")
        if not col:
            continue
        cid = col["id"]
        if cid not in collections:
            collections[cid] = {
                "id": cid,
                "name": col["name"],
                "poster": tmdb_poster(col.get("poster_path")),
                "movies": []
            }
        collections[cid]["movies"].append({
            "jellyfin_id": movie["jellyfin_id"],
            "title": movie["title"],
            "year": movie["year"],
            "poster": movie["poster"]
        })
    for c in collections.values():
        c["movies"].sort(key=lambda x: x["year"] or 0)
    return {"collections": sorted(collections.values(), key=lambda x: x["name"])}

@app.get("/collection/{collection_id}", response_class=HTMLResponse)
async def collection_page(request: Request, collection_id: int):
    return templates.TemplateResponse("collection.html", {"request": request, "collection_id": collection_id})

@app.get("/api/collection/{collection_id}")
async def get_collection(collection_id: int):
    col_data = tmdb_get(f"/collection/{collection_id}")
    if not col_data:
        return {"error": "Collection not found on TMDB"}
    tmdb_map = await asyncio.to_thread(get_movie_tmdb_map)
    movies = []
    for part in col_data.get("parts", []):
        tmdb_id = str(part["id"])
        movies.append({
            "tmdb_id": tmdb_id,
            "title": part.get("title", ""),
            "year": (part.get("release_date") or "")[:4],
            "poster": tmdb_poster(part.get("poster_path")),
            "jellyfin_id": tmdb_map.get(tmdb_id),
            "in_library": tmdb_id in tmdb_map
        })
    movies.sort(key=lambda x: x["year"] or "")
    return {
        "name": col_data.get("name", ""),
        "overview": col_data.get("overview", ""),
        "poster": tmdb_poster(col_data.get("poster_path")),
        "movies": movies
    }

# --- Show ---
@app.get("/show/{item_id}", response_class=HTMLResponse)
async def show_page(request: Request, item_id: str):
    return templates.TemplateResponse("show.html", {"request": request, "jellyfin_id": item_id})

@app.get("/api/show/{item_id}")
async def get_show(item_id: str):
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server"}
    item_data = await asyncio.to_thread(get_item, user_id, item_id, "ProviderIds,ImageTags")
    if not item_data:
        return {"error": "Show not found"}
    tmdb_id = item_data.get("ProviderIds", {}).get("Tmdb")
    if not tmdb_id:
        return {"error": "No TMDB ID linked. Refresh metadata and try again."}
    has_primary = bool(item_data.get("ImageTags", {}).get("Primary")) or item_data.get("_has_primary", False)
    poster = get_poster_url(item_id, has_primary)
    data = tmdb_get(f"/tv/{tmdb_id}")
    if not data:
        return {"error": "Failed to fetch show data from TMDB. Check your TMDB API key."}
    seasons = {}
    for season in data.get("seasons", []):
        season_num = season["season_number"]
        if season_num == 0:
            continue
        season_data = tmdb_get(f"/tv/{tmdb_id}/season/{season_num}")
        if season_data:
            seasons[season_num] = [
                {"episode_number": ep["episode_number"], "name": ep["name"], "id": ep.get("id")}
                for ep in season_data.get("episodes", [])
            ]
    return {
        "title": data.get("name"),
        "year": (data.get("first_air_date") or "")[:4],
        "overview": data.get("overview"),
        "poster": poster or tmdb_poster(data.get("poster_path")),
        "tmdb_id": tmdb_id,
        "seasons": seasons
    }

@app.get("/api/missing/{item_id}")
async def get_missing(item_id: str):
    user_id = get_user_id()
    if not user_id:
        return {"missing": []}
    eps_raw = await asyncio.to_thread(get_episodes, item_id)
    dual_data = load_dual_ep().get(item_id, {"show": False, "seasons": {}})
    have = set()
    for ep in eps_raw:
        s = ep.get("ParentIndexNumber")
        e = ep.get("IndexNumber")
        e_end = ep.get("IndexNumberEnd")
        if s and e:
            season_dual = dual_data.get("seasons", {}).get(str(s), dual_data.get("show", False))
            if e_end and e_end > e:
                for i in range(e, e_end + 1):
                    have.add(f"S{str(s).zfill(2)}E{str(i).zfill(2)}")
            elif season_dual:
                tmdb_e1 = (e - 1) * 2 + 1
                tmdb_e2 = tmdb_e1 + 1
                have.add(f"S{str(s).zfill(2)}E{str(tmdb_e1).zfill(2)}")
                have.add(f"S{str(s).zfill(2)}E{str(tmdb_e2).zfill(2)}")
            else:
                have.add(f"S{str(s).zfill(2)}E{str(e).zfill(2)}")
    item_data = await asyncio.to_thread(get_item, user_id, item_id, "ProviderIds")
    if not item_data:
        return {"missing": [], "have": list(have)}
    tmdb_id = item_data.get("ProviderIds", {}).get("Tmdb")
    if not tmdb_id:
        return {"missing": [], "have": list(have)}
    show_data = tmdb_get(f"/tv/{tmdb_id}")
    if not show_data:
        return {"missing": [], "have": list(have)}
    today = date.today().isoformat()
    all_episodes = set()
    for season in show_data.get("seasons", []):
        season_num = season["season_number"]
        if season_num == 0:
            continue
        season_data = tmdb_get(f"/tv/{tmdb_id}/season/{season_num}")
        if not season_data:
            continue
        for ep in season_data.get("episodes", []):
            air_date = ep.get("air_date") or ""
            if air_date and air_date <= today:
                key = f"S{str(season_num).zfill(2)}E{str(ep['episode_number']).zfill(2)}"
                all_episodes.add(key)
    return {"missing": sorted(all_episodes - have), "have": list(have)}

@app.get("/api/show/{item_id}/episodes")
async def get_show_episodes(item_id: str):
    if get_server_type() == "plex":
        data = media_server_request(f"/library/metadata/{item_id}/allLeaves")
        if not data:
            return {"episodes": []}
        return {"episodes": [{
            "id": str(ep.get("ratingKey", "")),
            "season": ep.get("parentIndex"),
            "episode": ep.get("index"),
            "title": ep.get("title", "")
        } for ep in data.get("MediaContainer", {}).get("Metadata", [])]}
    else:
        data = media_server_request(f"/Shows/{item_id}/Episodes")
        if not data:
            return {"episodes": []}
        return {"episodes": [{
            "id": ep["Id"],
            "season": ep.get("ParentIndexNumber"),
            "episode": ep.get("IndexNumber"),
            "title": ep.get("Name", "")
        } for ep in data.get("Items", [])]}

@app.get("/api/show/{item_id}/seasons")
async def get_show_seasons(item_id: str):
    if get_server_type() == "plex":
        data = media_server_request(f"/library/metadata/{item_id}/children")
        if not data:
            return {"seasons": []}
        return {"seasons": [{
            "id": str(s.get("ratingKey", "")),
            "index": s.get("index"),
            "title": s.get("title", "")
        } for s in data.get("MediaContainer", {}).get("Metadata", [])]}
    else:
        data = media_server_request(f"/Shows/{item_id}/Seasons")
        if not data:
            return {"seasons": []}
        return {"seasons": [{
            "id": s["Id"],
            "index": s.get("IndexNumber"),
            "title": s.get("Name", "")
        } for s in data.get("Items", [])]}

# --- Image Upload ---
@app.post("/api/images/upload/{item_id}")
async def upload_image(item_id: str, image_type: str = Form("Primary"), file: UploadFile = File(...)):
    sc = get_server_config()
    if not sc:
        return {"error": "Media server not configured"}
    server_type = sc.get("type", "jellyfin")
    base_url = sc.get("url", "")
    token = sc.get("api_key", "")
    if not base_url or not token:
        return {"error": "Media server URL or token missing"}
    img_data = await file.read()
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, file.content_type or "image/jpeg")
    try:
        if server_type == "plex":
            r = requests.post(f"{base_url}/library/metadata/{item_id}/posters", headers={"X-Plex-Token": token}, data=img_data, timeout=30)
        else:
            img_b64 = base64.b64encode(img_data).decode("utf-8")
            r = requests.post(f"{base_url}/Items/{item_id}/Images/{image_type}", params={"api_key": token}, headers={"Content-Type": content_type}, data=img_b64, timeout=30)
        r.raise_for_status()
        return {"status": f"{image_type} updated successfully"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/images/upload/episode/{episode_id}")
async def upload_episode_image(episode_id: str, file: UploadFile = File(...)):
    sc = get_server_config()
    if not sc:
        return {"error": "Media server not configured"}
    server_type = sc.get("type", "jellyfin")
    base_url = sc.get("url", "")
    token = sc.get("api_key", "")
    if not base_url or not token:
        return {"error": "Media server URL or token missing"}
    img_data = await file.read()
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, file.content_type or "image/jpeg")
    try:
        if server_type == "plex":
            r = requests.post(f"{base_url}/library/metadata/{episode_id}/posters", headers={"X-Plex-Token": token}, data=img_data, timeout=30)
        else:
            img_b64 = base64.b64encode(img_data).decode("utf-8")
            r = requests.post(f"{base_url}/Items/{episode_id}/Images/Thumb", params={"api_key": token}, headers={"Content-Type": content_type}, data=img_b64, timeout=30)
        r.raise_for_status()
        return {"status": "Title card updated successfully"}
    except Exception as e:
        return {"error": str(e)}

# --- File Replacement ---
@app.post("/api/replace/movie/{item_id}")
async def replace_movie_file(item_id: str, file: UploadFile = File(...)):
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server"}
    item_data = await asyncio.to_thread(get_item, user_id, item_id, "MediaSources")
    if not item_data:
        return {"error": "Movie not found"}
    sources = item_data.get("MediaSources", [])
    if not sources or not sources[0].get("Path"):
        return {"error": "No file path found for this movie"}
    existing_path = sources[0]["Path"]
    try:
        with open(existing_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        return {"error": f"Failed to write file: {str(e)}"}
    media_server_post(f"/Items/{item_id}/Refresh", params={"MetadataRefreshMode": "Default", "ImageRefreshMode": "Default"})
    return {"status": f"Replaced successfully: {os.path.basename(existing_path)}"}

@app.post("/api/replace/episodes/{item_id}")
async def replace_episode_files(item_id: str, files: List[UploadFile] = File(...), meta: str = Form(...)):
    eps_raw = await asyncio.to_thread(get_episodes, item_id)
    episode_map = {}
    for ep in eps_raw:
        s = ep.get("ParentIndexNumber")
        e = ep.get("IndexNumber")
        if not s or not e:
            continue
        key = f"S{str(s).zfill(2)}E{str(e).zfill(2)}"
        sources = ep.get("MediaSources", [])
        if sources and sources[0].get("Path"):
            episode_map[key] = {"path": sources[0]["Path"], "id": ep["Id"]}
    try:
        meta_list = json.loads(meta)
    except Exception:
        return {"error": "Invalid meta"}
    results = []
    for i, file in enumerate(files):
        m = meta_list[i] if i < len(meta_list) else {}
        code = m.get("code", "").strip().upper()
        dual = m.get("dual", False)
        if not code:
            match = re.search(r'[Ss](\d+)[Ee](\d+)', file.filename)
            if match:
                code = f"S{match.group(1).zfill(2)}E{match.group(2).zfill(2)}"
        if not code:
            results.append({"file": file.filename, "status": "No episode code"})
            continue
        ep = episode_map.get(code)
        if not ep:
            results.append({"file": file.filename, "status": f"No match for {code}"})
            continue
        try:
            with open(ep["path"], "wb") as f:
                shutil.copyfileobj(file.file, f)
            if dual:
                s = code[1:3]
                e = int(code[4:6])
                next_code = f"S{s}E{str(e + 1).zfill(2)}"
                next_ep = episode_map.get(next_code)
                if next_ep:
                    shutil.copy2(ep["path"], next_ep["path"])
                    media_server_post(f"/Items/{next_ep['id']}/Refresh", params={"MetadataRefreshMode": "Default"})
            media_server_post(f"/Items/{ep['id']}/Refresh", params={"MetadataRefreshMode": "Default"})
            label = f"{code}+{str(int(code[4:6]) + 1).zfill(2)}" if dual else code
            results.append({"file": file.filename, "status": f"✓ Replaced {label}"})
        except Exception as e:
            results.append({"file": file.filename, "status": f"Failed: {str(e)}"})
    return {"results": results}

# --- Movie ---
@app.get("/movie/{item_id}", response_class=HTMLResponse)
async def movie_page(request: Request, item_id: str):
    return templates.TemplateResponse("movie.html", {"request": request, "jellyfin_id": item_id})

@app.get("/api/movie/{item_id}")
async def get_movie(item_id: str):
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server"}
    item_data = await asyncio.to_thread(get_item, user_id, item_id, "ProviderIds,MediaSources,ProductionYear,Genres")
    if not item_data:
        return {"error": "Movie not found"}
    has_primary = bool(item_data.get("ImageTags", {}).get("Primary")) or item_data.get("_has_primary", False)
    poster = get_poster_url(item_id, has_primary)
    file_info = {}
    sources = item_data.get("MediaSources", [])
    if sources:
        src = sources[0]
        streams = src.get("MediaStreams", [])
        video = next((s for s in streams if s.get("Type") == "Video"), None)
        audio = next((s for s in streams if s.get("Type") == "Audio"), None)
        size_bytes = src.get("Size", 0)
        file_info = {
            "path": src.get("Path", ""),
            "size": f"{round(size_bytes / 1073741824, 2)} GB" if size_bytes else "Unknown",
            "video_codec": video.get("Codec", "").upper() if video else "Unknown",
            "resolution": f"{video.get('Width')}x{video.get('Height')}" if video else "Unknown",
            "audio_codec": audio.get("Codec", "").upper() if audio else "Unknown",
            "audio_channels": audio.get("Channels", "") if audio else ""
        }
    tmdb_id = item_data.get("ProviderIds", {}).get("Tmdb")
    tmdb_data = {}
    collection = None
    if tmdb_id:
        data = tmdb_get(f"/movie/{tmdb_id}")
        if data:
            tmdb_data = {
                "title": data.get("title", item_data.get("Name", "")),
                "year": (data.get("release_date") or "")[:4],
                "overview": data.get("overview", ""),
                "genres": [g["name"] for g in data.get("genres", [])],
                "runtime": data.get("runtime"),
                "rating": data.get("vote_average"),
                "status": data.get("status", ""),
                "tmdb_id": tmdb_id
            }
            col = data.get("belongs_to_collection")
            if col:
                collection = {"id": col["id"], "name": col["name"]}
    if not tmdb_data:
        tmdb_data = {
            "title": item_data.get("Name", ""),
            "year": item_data.get("ProductionYear", ""),
            "overview": item_data.get("Overview", ""),
            "genres": item_data.get("Genres", []),
            "runtime": None,
            "rating": None,
            "status": "",
            "tmdb_id": tmdb_id
        }
    return {"poster": poster, "file": file_info, "collection": collection, **tmdb_data}

# --- Subtitles ---
@app.get("/subtitles", response_class=HTMLResponse)
async def subtitles_page(request: Request):
    return templates.TemplateResponse("subtitles.html", {"request": request})

@app.get("/api/subtitles/search")
async def search_subtitles(query: str = "", tmdb_id: str = None, imdb_id: str = None, season: int = None, episode: int = None, languages: str = "en"):
    results = await asyncio.to_thread(opensub_search, query, languages, season, episode, imdb_id, tmdb_id)
    subs = []
    for r in results[:20]:
        attrs = r.get("attributes", {})
        files = attrs.get("files", [])
        file_id = files[0].get("file_id") if files else None
        subs.append({
            "file_id": file_id,
            "release": attrs.get("release", ""),
            "language": attrs.get("language", ""),
            "downloads": attrs.get("download_count", 0),
            "rating": attrs.get("ratings", 0),
            "fps": attrs.get("fps", ""),
            "hearing_impaired": attrs.get("hearing_impaired", False)
        })
    return {"results": subs}

@app.post("/api/subtitles/download")
async def download_subtitle(request: Request):
    data = await request.json()
    file_id = data.get("file_id")
    save_path = data.get("save_path", "")
    if not file_id:
        return {"error": "No file ID provided"}
    link = await asyncio.to_thread(opensub_download, file_id)
    if not link:
        return {"error": "Failed to get download link"}
    try:
        r = requests.get(link, timeout=30)
        r.raise_for_status()
        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(r.content)
            return {"status": f"Saved to {save_path}"}
        return {"status": "Downloaded", "link": link}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/subtitles/scan")
async def scan_missing_subtitles(languages: str = "en"):
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server"}
    items_raw = await asyncio.to_thread(get_library_items, user_id, "Movie,Series")
    return {"items": [{
        "id": item["Id"],
        "title": item.get("Name", ""),
        "type": "tv" if item.get("Type") == "Series" else "movie",
        "tmdb_id": item.get("ProviderIds", {}).get("Tmdb")
    } for item in items_raw]}

# --- Dual Episode ---
@app.get("/api/dual/{item_id}")
async def get_dual_ep(item_id: str):
    data = load_dual_ep()
    return data.get(item_id, {"show": False, "seasons": {}})

@app.post("/api/dual/{item_id}")
async def set_dual_ep(item_id: str, request: Request):
    body = await request.json()
    data = load_dual_ep()
    data[item_id] = body
    save_dual_ep(data)
    return {"status": "saved"}

# --- Monitor / Watchlist ---
@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request):
    return templates.TemplateResponse("monitor.html", {"request": request})

@app.get("/api/monitor")
async def get_monitor():
    watchlist = load_watchlist()
    if not watchlist:
        return {"items": []}

    today = date.today()

    async def enrich(entry):
        tmdb_id = entry["tmdb_id"]
        media_type = entry["type"]

        if media_type == "movie":
            data = await tmdb_get_async(f"/movie/{tmdb_id}")
            if not data:
                return None
            release_date = data.get("release_date") or ""
            status = data.get("status", "")
            if release_date:
                try:
                    rd = datetime.strptime(release_date, "%Y-%m-%d").date()
                    days_left = (rd - today).days
                    if days_left <= 0:
                        countdown = "Released"
                        countdown_class = "released"
                    else:
                        countdown = f"In {days_left}d"
                        countdown_class = "upcoming"
                except ValueError:
                    countdown = status or "Unknown"
                    countdown_class = "unknown"
            else:
                countdown = status or "Unknown"
                countdown_class = "unknown"
            return {
                "tmdb_id": tmdb_id,
                "type": "movie",
                "title": data.get("title", entry.get("title", "")),
                "poster": tmdb_poster(data.get("poster_path")),
                "release_date": release_date,
                "status": status,
                "countdown": countdown,
                "countdown_class": countdown_class,
                "overview": data.get("overview", ""),
                "rating": data.get("vote_average"),
            }
        else:
            data = await tmdb_get_async(f"/tv/{tmdb_id}", ttl=3600)
            if not data:
                return None
            next_ep = data.get("next_episode_to_air")
            show_status = data.get("status", "")

            if next_ep:
                air_date = next_ep.get("air_date") or ""
                ep_label = f"S{str(next_ep.get('season_number', 0)).zfill(2)}E{str(next_ep.get('episode_number', 0)).zfill(2)}"
                if air_date:
                    try:
                        ad = datetime.strptime(air_date, "%Y-%m-%d").date()
                        days_left = (ad - today).days
                        if days_left <= 0:
                            countdown = f"{ep_label} Today"
                            countdown_class = "released"
                        elif days_left == 1:
                            countdown = f"{ep_label} Tomorrow"
                            countdown_class = "upcoming"
                        else:
                            countdown = f"{ep_label} in {days_left}d"
                            countdown_class = "upcoming"
                    except ValueError:
                        countdown = ep_label
                        countdown_class = "upcoming"
                else:
                    countdown = ep_label
                    countdown_class = "upcoming"
            elif show_status in ("Ended", "Canceled"):
                countdown = show_status
                countdown_class = "ended"
            else:
                countdown = "No air date"
                countdown_class = "unknown"

            return {
                "tmdb_id": tmdb_id,
                "type": "tv",
                "title": data.get("name", entry.get("title", "")),
                "poster": tmdb_poster(data.get("poster_path")),
                "status": show_status,
                "next_episode": next_ep,
                "countdown": countdown,
                "countdown_class": countdown_class,
                "overview": data.get("overview", ""),
                "rating": data.get("vote_average"),
                "networks": [n.get("name") for n in data.get("networks", [])],
            }

    results = await asyncio.gather(*[enrich(e) for e in watchlist])
    return {"items": [r for r in results if r is not None]}

@app.post("/api/monitor/add")
async def monitor_add(request: Request):
    body = await request.json()
    tmdb_id = str(body.get("tmdb_id", ""))
    media_type = body.get("type", "")
    monitor_type = body.get("monitor_type", "all")
    storage_path = body.get("storage_path", "")
    profile_name = body.get("profile_name", "")
    if not tmdb_id or media_type not in ("movie", "tv"):
        return {"error": "tmdb_id and type required"}
    watchlist = load_watchlist()
    existing = next((e for e in watchlist if str(e["tmdb_id"]) == tmdb_id and e["type"] == media_type), None)
    if existing:
        existing["monitor_type"] = monitor_type
        if storage_path:
            existing["storage_path"] = storage_path
        if profile_name:
            existing["profile_name"] = profile_name
    else:
        watchlist.append({
            "tmdb_id": tmdb_id,
            "type": media_type,
            "title": body.get("title", ""),
            "added": date.today().isoformat(),
            "monitor_type": monitor_type,
            "storage_path": storage_path,
            "profile_name": profile_name
        })
    save_watchlist(watchlist)
    return {"status": "saved"}

@app.post("/api/monitor/remove")
async def monitor_remove(request: Request):
    body = await request.json()
    tmdb_id = str(body.get("tmdb_id", ""))
    media_type = body.get("type", "")
    watchlist = [e for e in load_watchlist() if not (str(e["tmdb_id"]) == tmdb_id and e["type"] == media_type)]
    save_watchlist(watchlist)
    return {"status": "removed"}

@app.get("/api/monitor/check")
async def monitor_check(tmdb_id: str, type: str):
    watchlist = load_watchlist()
    entry = next((e for e in watchlist if str(e["tmdb_id"]) == str(tmdb_id) and e["type"] == type), None)
    if not entry:
        return {"monitored": False, "monitor_type": None}
    return {"monitored": True, "monitor_type": entry.get("monitor_type", "all")}

# --- Monitor Runner ---

def title_matches(torrent_title, expected_title, year=None, media_type=None):
    def norm(s):
        s = s.lower()
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        s = re.sub(r" +", " ", s).strip()
        return s
    raw = torrent_title
    t = norm(torrent_title)
    e = norm(expected_title)
    # Must start with expected title
    if not (t + ' ').startswith(e + ' '):
        return False
    # Reject TV patterns when searching for a movie
    if media_type == "movie":
        if re.search(r'[Ss]\d{1,2}[Ee]\d{1,2}', raw):
            return False
        if re.search(r'\b[Ss]\d{1,2}\b', raw):
            return False
        if re.search(r'\bseason\b', t):
            return False
        if re.search(r'\bepisode\b', t):
            return False
        if re.search(r'\bcomplete\b', t):
            return False
    # Reject if a different year appears in the torrent title
    if year:
        for y in re.findall(r'\b(?:19|20)\d{2}\b', raw):
            if y != str(year):
                return False
    return True

MONITOR_LOG_FILE = "monitor_log.json"

def load_monitor_log():
    if not os.path.exists(MONITOR_LOG_FILE):
        return []
    with open(MONITOR_LOG_FILE) as f:
        return json.load(f)

def save_monitor_log(data):
    with open(MONITOR_LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def append_monitor_log(entry):
    log = load_monitor_log()
    log.insert(0, entry)
    save_monitor_log(log[:200])  # keep last 200 entries

async def run_monitor_cycle():
    watchlist = load_watchlist()
    if not watchlist:
        return {"ran": 0, "results": []}

    results = []
    user_id = await asyncio.to_thread(get_user_id)

    for entry in watchlist:
        tmdb_id = str(entry["tmdb_id"])
        media_type = entry["type"]
        title = entry.get("title", "")
        profile_name = entry.get("profile_name", "")
        storage_path = entry.get("storage_path", "")

        profile = get_profile_by_name(profile_name) if profile_name else get_default_profile()

        if media_type == "movie":
            # Check if already in library
            tmdb_map = await asyncio.to_thread(get_movie_tmdb_map)
            if tmdb_id in tmdb_map:
                results.append({"title": title, "type": "movie", "status": "Already in library", "skipped": True})
                continue

            # Fetch year from TMDB first so title_matches can use it
            tmdb_data = await tmdb_get_async(f"/movie/{tmdb_id}")
            year = (tmdb_data.get("release_date") or "")[:4] if tmdb_data else ""

            # Search and download
            clean_title = re.sub(r'[^\w\s]', ' ', title).strip()
            search_query = f"{clean_title} {year}" if year else clean_title
            raw = await asyncio.to_thread(search_prowlarr, search_query)
            print(f"[MONITOR] {title}: Prowlarr returned {len(raw)} results")
            scored = []
            for r in raw:
                t = r.get("title", "")
                if not t:
                    continue
                passed = title_matches(t, title, year, media_type="movie")
                print(f"[MONITOR]   {'PASS' if passed else 'SKIP'} title_matches: {t}")
                if not passed:
                    continue
                parsed = parse_torrent_title(t)
                profile_ok = matches_profile(parsed, profile)
                print(f"[MONITOR]   {'PASS' if profile_ok else 'SKIP'} profile: {parsed}")
                if profile_ok:
                    scored.append((score_torrent(parsed), r))
            if not scored:
                results.append({"title": title, "type": "movie", "status": "No matching releases found", "skipped": True})
                append_monitor_log({"ts": datetime.now().isoformat(), "title": title, "type": "movie", "status": "No matching releases found"})
                continue

            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0][1]
            dl_url = best.get("downloadUrl") or best.get("magnetUrl") or best.get("infoUrl", "")
            if not dl_url:
                results.append({"title": title, "type": "movie", "status": "No download URL in result", "skipped": True})
                continue

            save_path = storage_path if storage_path else build_download_path("movie", title, year)

            ok, err = await asyncio.to_thread(add_to_qbit, dl_url, save_path)
            status = f"Sent to qBit: {best.get('title', '')}" if ok else f"qBit error: {err}"
            results.append({"title": title, "type": "movie", "status": status, "skipped": not ok})
            append_monitor_log({"ts": datetime.now().isoformat(), "title": title, "type": "movie", "status": status, "torrent": best.get("title", ""), "path": save_path})

        elif media_type == "tv":
            if not user_id:
                results.append({"title": title, "type": "tv", "status": "Could not connect to media server", "skipped": True})
                continue

            # Find jellyfin ID by TMDB ID
            items_raw = await asyncio.to_thread(get_library_items, user_id, "Series")
            jellyfin_id = None
            for item in items_raw:
                if str(item.get("ProviderIds", {}).get("Tmdb", "")) == tmdb_id:
                    jellyfin_id = item["Id"]
                    break

            # Get missing episodes via TMDB diff
            today = date.today().isoformat()
            have = set()
            if jellyfin_id:
                eps_raw = await asyncio.to_thread(get_episodes, jellyfin_id)
                for ep in eps_raw:
                    s = ep.get("ParentIndexNumber")
                    e = ep.get("IndexNumber")
                    e_end = ep.get("IndexNumberEnd")
                    if s and e:
                        if e_end and e_end > e:
                            for i in range(e, e_end + 1):
                                have.add(f"S{str(s).zfill(2)}E{str(i).zfill(2)}")
                        else:
                            have.add(f"S{str(s).zfill(2)}E{str(e).zfill(2)}")

            show_data = await tmdb_get_async(f"/tv/{tmdb_id}")
            if not show_data:
                results.append({"title": title, "type": "tv", "status": "TMDB fetch failed", "skipped": True})
                continue

            missing = []
            for season in show_data.get("seasons", []):
                snum = season["season_number"]
                if snum == 0:
                    continue
                season_data = await tmdb_get_async(f"/tv/{tmdb_id}/season/{snum}")
                if not season_data:
                    continue
                for ep in season_data.get("episodes", []):
                    air_date = ep.get("air_date") or ""
                    if not air_date or air_date > today:
                        continue
                    key = f"S{str(snum).zfill(2)}E{str(ep['episode_number']).zfill(2)}"
                    if key not in have:
                        missing.append((snum, ep["episode_number"], key))

            print(f"[MONITOR] {title}: have={len(have)} missing={len(missing)}")

            if not missing:
                results.append({"title": title, "type": "tv", "status": "No missing episodes", "skipped": True})
                continue

            clean_title = re.sub(r'[^\w\s]', ' ', title).strip()
            ep_results = []
            missing_seasons = sorted(set(snum for snum, _, _ in missing))

            def is_pack(torrent_title):
                # season pack: has S01 but no E01 after it
                if re.search(r'[Ss]\d{1,2}[Ee]\d{1,2}', torrent_title):
                    return False
                if re.search(r'[Ss]\d{1,2}', torrent_title):
                    return True
                if re.search(r'\b(complete|season)\b', torrent_title.lower()):
                    return True
                return False

            async def search_scored(query, packs_only=False, no_packs=False):
                raw = await asyncio.to_thread(search_prowlarr, query)
                print(f"[MONITOR] query='{query}': {len(raw)} results")
                scored = []
                for r in raw:
                    t = r.get("title", "")
                    if not t:
                        continue
                    if packs_only and not is_pack(t):
                        print(f"[MONITOR]   SKIP not-pack: {t}")
                        continue
                    if no_packs and is_pack(t):
                        print(f"[MONITOR]   SKIP pack: {t}")
                        continue
                    if not title_matches(t, title):
                        print(f"[MONITOR]   SKIP title_matches: {t}")
                        continue
                    parsed = parse_torrent_title(t)
                    if matches_profile(parsed, profile):
                        print(f"[MONITOR]   PASS: {t}")
                        scored.append((score_torrent(parsed), r))
                    else:
                        print(f"[MONITOR]   SKIP profile: {t}")
                scored.sort(key=lambda x: x[0], reverse=True)
                return scored

            async def send_to_qbit(best, save_path, label):
                dl_url = best.get("downloadUrl") or best.get("magnetUrl") or best.get("infoUrl", "")
                if not dl_url:
                    print(f"[MONITOR] {label}: no URL")
                    return False
                os.makedirs(save_path, exist_ok=True)
                ok, err = await asyncio.to_thread(add_to_qbit, dl_url, save_path)
                print(f"[MONITOR] {label}: qBit -> {'sent' if ok else err}")
                if ok:
                    append_monitor_log({"ts": datetime.now().isoformat(), "title": title, "type": "tv",
                        "episode": label, "status": "sent", "torrent": best.get("title", ""), "path": save_path})
                return ok

            # Step 1: complete series (only if missing from multiple seasons)
            if len(missing_seasons) > 1:
                for q in [f"{clean_title} complete series", f"{clean_title} complete"]:
                    scored = await search_scored(q, packs_only=True)
                    if scored:
                        save_path = storage_path if storage_path else build_download_path("tv", title)
                        ok = await send_to_qbit(scored[0][1], save_path, "complete series")
                        if ok:
                            ep_results.append("complete series: sent")
                            results.append({"title": title, "type": "tv", "status": "complete series sent", "skipped": False})
                            continue

            # Step 2: season packs per season
            sent_seasons = set()
            for snum in missing_seasons:
                scored = await search_scored(f"{clean_title} S{str(snum).zfill(2)}", packs_only=True)
                if scored:
                    save_path = storage_path if storage_path else build_download_path("tv", title, season=snum)
                    ok = await send_to_qbit(scored[0][1], save_path, f"S{str(snum).zfill(2)} pack")
                    if ok:
                        sent_seasons.add(snum)
                        ep_results.append(f"S{str(snum).zfill(2)}: season pack sent")

            # Step 3: individual episodes for seasons without a pack
            for snum, enum, key in missing:
                if snum in sent_seasons:
                    continue
                scored = await search_scored(f"{clean_title} S{str(snum).zfill(2)}E{str(enum).zfill(2)}", no_packs=True)
                if not scored:
                    ep_results.append(f"{key}: no match")
                    append_monitor_log({"ts": datetime.now().isoformat(), "title": title, "type": "tv", "episode": key, "status": "No matching releases found"})
                    continue
                save_path = storage_path if storage_path else build_download_path("tv", title, season=snum)
                ok = await send_to_qbit(scored[0][1], save_path, key)
                ep_results.append(f"{key}: {'sent' if ok else 'failed'}")

            results.append({"title": title, "type": "tv", "status": ", ".join(ep_results) if ep_results else "no missing", "skipped": not ep_results})

    return {"ran": len(watchlist), "results": results}

@app.post("/api/monitor/run")
async def monitor_run():
    result = await run_monitor_cycle()
    return result

@app.get("/api/monitor/log")
async def monitor_log():
    return {"log": load_monitor_log()}

@app.post("/api/cache/clear")
async def clear_cache(prefix: str = None):
    cache_clear(prefix)
    return {"status": "cleared"}

@app.on_event("startup")
async def startup():
    scheduler.add_job(run_monitor_cycle, "interval", hours=6, id="monitor_cycle", replace_existing=True)
    scheduler.start()

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()

# --- Missing ---
@app.get("/missing", response_class=HTMLResponse)
async def missing_page(request: Request):
    return templates.TemplateResponse("missing.html", {"request": request})

@app.get("/api/missing")
async def get_all_missing():
    user_id = get_user_id()
    if not user_id:
        return {"error": "Could not connect to media server"}
    items_raw = await asyncio.to_thread(get_library_items, user_id, "Series")
    today = date.today().isoformat()
    all_missing = []
    for series in items_raw:
        tmdb_id = series.get("ProviderIds", {}).get("Tmdb")
        if not tmdb_id:
            continue
        item_id = series["Id"]
        show_title = series.get("Name", "")
        eps_raw = await asyncio.to_thread(get_episodes, item_id)
        have = set()
        for ep in eps_raw:
            s = ep.get("ParentIndexNumber")
            e = ep.get("IndexNumber")
            e_end = ep.get("IndexNumberEnd")
            if s and e:
                if e_end and e_end > e:
                    for i in range(e, e_end + 1):
                        have.add(f"S{str(s).zfill(2)}E{str(i).zfill(2)}")
                else:
                    have.add(f"S{str(s).zfill(2)}E{str(e).zfill(2)}")
        show_data = tmdb_get(f"/tv/{tmdb_id}")
        if not show_data:
            continue
        for season in show_data.get("seasons", []):
            season_num = season["season_number"]
            if season_num == 0:
                continue
            season_data = tmdb_get(f"/tv/{tmdb_id}/season/{season_num}")
            if not season_data:
                continue
            for ep in season_data.get("episodes", []):
                air_date = ep.get("air_date") or ""
                if not air_date or air_date > today:
                    continue
                key = f"S{str(season_num).zfill(2)}E{str(ep['episode_number']).zfill(2)}"
                if key not in have:
                    all_missing.append({
                        "show": show_title,
                        "jellyfin_id": item_id,
                        "episode": key,
                        "episode_name": ep.get("name", "")
                    })
    return {"missing": all_missing}

# --- Connection Tests ---
@app.get("/api/test/prowlarr")
async def test_prowlarr():
    config = load_config()
    if not config:
        return {"ok": False, "error": "No config"}
    try:
        r = requests.get(f"{config['prowlarr']['url']}/api/v1/system/status", params={"apikey": config['prowlarr']['api_key']}, timeout=5)
        r.raise_for_status()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/test/qbittorrent")
async def test_qbittorrent():
    config = load_config()
    if not config:
        return {"ok": False, "error": "No config"}
    try:
        session = requests.Session()
        r = session.post(f"{config['qbittorrent']['url']}/api/v2/auth/login", data={"username": config['qbittorrent']['username'], "password": config['qbittorrent']['password']}, timeout=5)
        if r.text == "Ok.":
            return {"ok": True}
        return {"ok": False, "error": "Invalid credentials"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/test/mediaserver")
async def test_mediaserver():
    config = load_config()
    if not config:
        return {"ok": False, "error": "No config"}
    sc = config.get("media_server", {})
    server_type = sc.get("type", "jellyfin")
    try:
        if server_type == "plex":
            r = requests.get(f"{sc['url']}/", headers={"X-Plex-Token": sc["api_key"], "Accept": "application/json"}, timeout=5)
        else:
            r = requests.get(f"{sc['url']}/System/Info", headers={"X-Emby-Token": sc["api_key"]}, timeout=5)
        r.raise_for_status()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- Settings ---
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "config": load_config() or {}, "active": "connections"})

@app.get("/settings/profiles", response_class=HTMLResponse)
async def profiles_page(request: Request):
    return templates.TemplateResponse("profiles.html", {
        "request": request,
        "profiles": load_profiles(),
        "all_resolutions": ALL_RESOLUTIONS,
        "all_hdr": ALL_HDR,
        "all_audio": ALL_AUDIO,
        "all_sources": ALL_SOURCES,
        "active": "profiles"
    })

@app.post("/settings/profiles/save")
async def save_profile(request: Request):
    data = await request.json()
    profiles = load_profiles()
    existing = next((p for p in profiles if p["name"] == data["name"]), None)
    if existing:
        existing.update(data)
    else:
        if data.get("is_default"):
            for p in profiles:
                p["is_default"] = False
        profiles.append(data)
    save_profiles(profiles)
    return {"status": "saved"}

@app.post("/settings/profiles/delete")
async def delete_profile(request: Request):
    data = await request.json()
    profiles = [p for p in load_profiles() if p["name"] != data["name"]]
    if not any(p.get("is_default") for p in profiles) and profiles:
        profiles[0]["is_default"] = True
    save_profiles(profiles)
    return {"status": "deleted"}

@app.post("/settings")
async def save_settings(
    request: Request,
    prowlarr_url: str = Form(...),
    prowlarr_api_key: str = Form(...),
    qbit_url: str = Form(...),
    qbit_username: str = Form(...),
    qbit_password: str = Form(...),
    media_server_type: str = Form("jellyfin"),
    media_server_url: str = Form(...),
    media_server_api_key: str = Form(...),
    tmdb_api_key: str = Form(""),
    opensub_api_key: str = Form(""),
    opensub_username: str = Form(""),
    opensub_password: str = Form(""),
    opensub_language: str = Form("en"),
    tv_folders: str = Form(""),
    movie_folders: str = Form(""),
    download_folder: str = Form("")
):
    config = {
        "prowlarr": {"url": prowlarr_url, "api_key": prowlarr_api_key},
        "qbittorrent": {"url": qbit_url, "username": qbit_username, "password": qbit_password},
        "media_server": {"type": media_server_type, "url": media_server_url, "api_key": media_server_api_key},
        "tmdb": {"api_key": tmdb_api_key},
        "opensubtitles": {"api_key": opensub_api_key, "username": opensub_username, "password": opensub_password, "language": opensub_language},
        "media": {
            "tv_folders": [f.strip() for f in tv_folders.split(",") if f.strip()],
            "movie_folders": [f.strip() for f in movie_folders.split(",") if f.strip()],
            "download_folder": download_folder.strip()
        }
    }
    save_config(config)
    cache_clear("library:")
    return RedirectResponse(url="/settings", status_code=303)

@app.get("/api/profiles")
async def get_profiles_list():
    return {"profiles": load_profiles()}

@app.get("/api/profile/{name}")
async def get_profile(name: str):
    profile = get_profile_by_name(name)
    return profile if profile else get_default_profile()

@app.get("/api/tmdb/search")
async def tmdb_search(q: str = ""):
    if not q.strip():
        return {"results": []}
    data = await tmdb_get_async("/search/multi", {"query": q, "page": 1})
    if not data:
        return {"results": []}
    watchlist = load_watchlist()
    monitored = {(str(e["tmdb_id"]), e["type"]) for e in watchlist}
    results = []
    for item in data.get("results", [])[:20]:
        media_type = item.get("media_type")
        if media_type not in ("movie", "tv"):
            continue
        tmdb_id = str(item.get("id", ""))
        results.append({
            "tmdb_id": tmdb_id,
            "type": media_type,
            "title": item.get("title") or item.get("name", ""),
            "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
            "poster": tmdb_poster(item.get("poster_path")),
            "overview": item.get("overview", ""),
            "monitored": (tmdb_id, media_type) in monitored
        })
    return {"results": results}

@app.get("/search")
async def search(
    request: Request,
    query: str,
    profile_name: str = None,
    resolutions: str = None,
    hdr_formats: str = None,
    audio_formats: str = None,
    sources: str = None
):
    if profile_name:
        profile = get_profile_by_name(profile_name) or get_default_profile()
    else:
        profile = get_default_profile()
    if resolutions:
        profile = dict(profile); profile["resolutions"] = resolutions.split(",")
    if hdr_formats:
        profile = dict(profile); profile["hdr_formats"] = hdr_formats.split(",")
    if audio_formats:
        profile = dict(profile); profile["audio_formats"] = audio_formats.split(",")
    if sources:
        profile = dict(profile); profile["sources"] = sources.split(",")

    media_info = {}
    tmdb_data = tmdb_get("/search/multi", {"query": query, "page": 1})
    if tmdb_data and tmdb_data.get("results"):
        top = tmdb_data["results"][0]
        media_info = {
            "title": top.get("title") or top.get("name", ""),
            "year": (top.get("release_date") or top.get("first_air_date") or "")[:4],
            "overview": top.get("overview", ""),
            "poster": tmdb_poster(top.get("poster_path")),
            "type": top.get("media_type", "unknown"),
            "tmdb_id": top.get("id")
        }

    results = search_prowlarr(query)
    if not results:
        return {"error": "No results found", "media_info": media_info}

    scored = []
    for result in results:
        title = result.get("title", "")
        if not title:
            continue
        parsed = parse_torrent_title(title)
        if matches_profile(parsed, profile):
            scored.append({
                "title": title,
                "parsed": parsed,
                "score": score_torrent(parsed),
                "size_gb": round(result.get("size", 0) / 1073741824, 2),
                "seeders": result.get("seeders", "N/A"),
                "download_url": result.get("downloadUrl", "N/A")
            })

    if not scored:
        return {"error": "No results matched your profile", "media_info": media_info}
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"results": scored, "media_info": media_info}

@app.post("/download")
async def download(request: Request):
    data = await request.json()
    download_url = data.get("download_url")
    media_type = data.get("media_type", "")
    title = data.get("title", "")
    year = data.get("year", "")
    season = data.get("season")

    if media_type and title:
        save_path = build_download_path(media_type, title, year, season)
    else:
        save_path = data.get("save_path", "/downloads")

    ok, err = add_to_qbit(download_url, save_path)
    if ok:
        return {"status": "Added to qBittorrent", "path": save_path}
    return {"status": f"Failed: {err}"}

# --- Setup ---
@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if config_exists():
        return RedirectResponse(url="/")
    return templates.TemplateResponse("setup.html", {"request": request})

@app.post("/api/setup")
async def save_setup(request: Request):
    data = await request.json()
    try:
        save_config(data)
        return {"status": "saved"}
    except Exception as e:
        return {"error": str(e)}
