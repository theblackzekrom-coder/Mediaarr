from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
import json
import os
import string

from parser import parse_torrent_title
from profiles import (
    score_torrent, get_best_match, load_profiles, save_profiles,
    get_default_profile, get_profile_by_name, matches_profile,
    ALL_RESOLUTIONS, ALL_HDR, ALL_AUDIO, ALL_SOURCES, DEFAULT_PROFILE
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CONFIG_FILE = "config.json"

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

# --- Prowlarr ---
def search_prowlarr(query):
    config = load_config()
    if not config:
        return []

    url = f"{config['prowlarr']['url']}/api/v1/search"
    api_key = config['prowlarr']['api_key']

    # Build query variations for fuzzy matching
    queries = [query]
    q = query.lower().strip()
    if q.endswith("s") and not q.endswith("ss"):
        queries.append(q[:-1])
    elif not q.endswith("s"):
        queries.append(q + "s")

    seen = set()
    all_results = []

    for q in queries:
        params = {
            "query": q,
            "apikey": api_key,
            "type": "search"
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            for result in response.json():
                title = result.get("title", "")
                if title and title not in seen:
                    seen.add(title)
                    all_results.append(result)
        except requests.exceptions.RequestException as e:
            print(f"Prowlarr error: {e}")

    return all_results

# --- qBittorrent ---
def add_to_qbit(download_url, save_path):
    config = load_config()
    if not config:
        return False
    qbit_url = f"{config['qbittorrent']['url']}/api/v2"
    session = requests.Session()
    try:
        session.post(f"{qbit_url}/auth/login", data={
            "username": config['qbittorrent']['username'],
            "password": config['qbittorrent']['password']
        })
        session.post(f"{qbit_url}/torrents/add", data={
            "urls": download_url,
            "savepath": save_path
        })
        return True
    except requests.exceptions.RequestException as e:
        print(f"qBittorrent error: {e}")
        return False

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
                items.append({
                    "name": item,
                    "path": full_path,
                    "type": "folder"
                })
    except PermissionError:
        pass

    parent = str(os.path.dirname(path)) if path != os.path.dirname(path) else None

    return {
        "current": path,
        "parent": parent,
        "items": items
    }

# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not config_exists():
        return RedirectResponse(url="/settings")
    default_profile = get_default_profile()
    profiles = load_profiles()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "profile": default_profile,
        "profiles": profiles,
        "all_resolutions": ALL_RESOLUTIONS,
        "all_hdr": ALL_HDR,
        "all_audio": ALL_AUDIO,
        "all_sources": ALL_SOURCES
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = load_config() or {}
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": config,
        "active": "connections"
    })

@app.get("/settings/profiles", response_class=HTMLResponse)
async def profiles_page(request: Request):
    profiles = load_profiles()
    return templates.TemplateResponse("profiles.html", {
        "request": request,
        "profiles": profiles,
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
    profiles = load_profiles()
    profiles = [p for p in profiles if p["name"] != data["name"]]
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
    jellyfin_url: str = Form(...),
    jellyfin_api_key: str = Form(...)
):
    config = {
        "prowlarr": {"url": prowlarr_url, "api_key": prowlarr_api_key},
        "qbittorrent": {"url": qbit_url, "username": qbit_username, "password": qbit_password},
        "jellyfin": {"url": jellyfin_url, "api_key": jellyfin_api_key}
    }
    save_config(config)
    return RedirectResponse(url="/", status_code=303)

@app.get("/api/profile/{name}")
async def get_profile(name: str):
    profile = get_profile_by_name(name)
    if not profile:
        return get_default_profile()
    return profile

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
        profile = dict(profile)
        profile["resolutions"] = resolutions.split(",")
    if hdr_formats:
        profile = dict(profile)
        profile["hdr_formats"] = hdr_formats.split(",")
    if audio_formats:
        profile = dict(profile)
        profile["audio_formats"] = audio_formats.split(",")
    if sources:
        profile = dict(profile)
        profile["sources"] = sources.split(",")

    results = search_prowlarr(query)

    if not results:
        return {"error": "No results found"}

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
        return {"error": "No results matched your profile"}

    scored.sort(key=lambda x: x["score"], reverse=True)

    return {"results": scored}

@app.post("/download")
async def download(request: Request):
    data = await request.json()
    download_url = data.get("download_url")
    save_path = data.get("save_path", "/downloads")
    success = add_to_qbit(download_url, save_path)
    if success:
        return {"status": "Added to qBittorrent successfully"}
    return {"status": "Failed to add to qBittorrent"}