from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
import requests
import json
import os

from parser import parse_torrent_title
from profiles import score_torrent, get_best_match

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

jinja_env = Environment(loader=FileSystemLoader("templates"))
templates = Jinja2Templates(env=jinja_env)

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
    params = {
        "query": query,
        "apikey": config['prowlarr']['api_key'],
        "type": "search"
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Prowlarr error: {e}")
        return []

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

# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not config_exists():
        return RedirectResponse(url="/settings")
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = load_config() or {}
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": config
    })

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
        "prowlarr": {
            "url": prowlarr_url,
            "api_key": prowlarr_api_key
        },
        "qbittorrent": {
            "url": qbit_url,
            "username": qbit_username,
            "password": qbit_password
        },
        "jellyfin": {
            "url": jellyfin_url,
            "api_key": jellyfin_api_key
        }
    }
    save_config(config)
    return RedirectResponse(url="/", status_code=303)

@app.get("/search")
async def search(query: str, resolution: str = "1080p", hdr_required: bool = False, audio_min: str = "DDP5.1"):
    if not config_exists():
        return {"error": "Not configured yet"}

    results = search_prowlarr(query)

    if not results:
        return {"error": "No results found"}

    torrent_titles = []
    torrent_map = {}

    for result in results:
        title = result.get("title", "")
        if title:
            torrent_titles.append(title)
            torrent_map[title] = result

    profile = {
        "resolution": resolution,
        "hdr_required": hdr_required,
        "audio_min": audio_min
    }

    best_title, best_parsed = get_best_match(torrent_titles, profile)

    if not best_title:
        return {"error": "No results matched your profile"}

    best_result = torrent_map[best_title]

    return {
        "title": best_title,
        "parsed": best_parsed,
        "size_gb": round(best_result.get("size", 0) / 1073741824, 2),
        "seeders": best_result.get("seeders", "N/A"),
        "download_url": best_result.get("downloadUrl", "N/A")
    }

@app.post("/download")
async def download(request: Request):
    data = await request.json()
    download_url = data.get("download_url")
    save_path = data.get("save_path", "/downloads")

    success = add_to_qbit(download_url, save_path)

    if success:
        return {"status": "Added to qBittorrent successfully"}
    return {"status": "Failed to add to qBittorrent"}