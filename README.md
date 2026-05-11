# Mediaarr

A self-hosted media manager built by Claude to replace the entire arr stack — Sonarr, Radarr, and Bazarr — in a single app.

Mediaarr was built out of a real frustration: managing a home media server remotely, from work, meant juggling three separate apps with three separate UIs, three separate configs, and three separate points of failure. Mediaarr collapses all of that into one install, one interface, and one config file.

## What it does

- **Search** — search your indexers via Prowlarr and filter results by resolution, HDR format, audio format, and source using quality profiles
- **Library** — browse your full Jellyfin/Plex/Emby library in one place
- **Monitor** — add movies and TV shows to a watchlist; Mediaarr automatically searches and downloads them when they become available
- **Missing episodes** — see every episode your library is missing across all tracked shows
- **Collections** — automatically groups your movies into TMDB collections (MCU sub-collections, John Wick, etc.)
- **Show pages** — full season/episode breakdown with have/missing status, title card uploads, dual episode support
- **File replacement** — upload a transcoded file and Mediaarr swaps it directly in your media server
- **Subtitles** — search and download subtitles from OpenSubtitles
- **Poster changer** — scrape and apply custom posters from ThePosterDB
- **Quality profiles** — define exactly what you want (4K DV TrueHD, 1080p WEB-DL, etc.) and Mediaarr scores and picks the best match automatically, with an optional size cap per profile

## How it differs from Sonarr/Radarr/Bazarr

| | Sonarr + Radarr + Bazarr | Mediaarr |
|---|---|---|
| Apps to install | 3 | 1 |
| Config files | 3+ | 1 |
| UI | 3 separate interfaces | Unified |
| Setup time | 30–60 min | ~5 min |
| Mobile-friendly | No | Yes |
| File replacement | No | Yes |
| Poster management | No | Yes |

## Requirements

- Python 3.11+
- [Prowlarr](https://prowlarr.com) — indexer management
- [qBittorrent](https://www.qbittorrent.org) — download client
- Jellyfin, Plex, or Emby — media server
- TMDB API key (free at [themoviedb.org](https://www.themoviedb.org/settings/api))
- OpenSubtitles API key (optional, for subtitles)

## Installation

### Linux (recommended)

```bash
git clone https://github.com/yourusername/mediaarr.git
cd mediaarr
chmod +x install.sh
sudo ./install.sh
```

The install script will:
- Install Python dependencies
- Create a systemd service that starts Mediaarr automatically on boot
- Start the service immediately

Mediaarr will be available at `http://localhost:8787`

### Windows

Download and run the installer from the releases page. It will install Mediaarr as a Windows service and open the app in your browser.

Mediaarr will be available at `http://localhost:8787`

### Manual (any OS)

```bash
git clone https://github.com/yourusername/mediaarr.git
cd mediaarr
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8787
```

## First run

On first launch you'll be taken through a setup wizard to configure:
- Prowlarr URL and API key
- qBittorrent URL, username, and password
- Media server type, URL, and API key
- TMDB API key
- TV and movie library folders

Settings can be changed at any time under Settings → Connections.

## Remote access

Mediaarr is designed to be used remotely. Bind it to `0.0.0.0` and access it via your server's IP or hostname from anywhere. For secure remote access, put it behind a reverse proxy (Nginx, Caddy) with HTTPS.

## Quality profiles

Profiles define what Mediaarr will accept when searching. Create profiles under Settings → Profiles. Each profile sets:
- Allowed resolutions (2160p, 1080p, 720p...)
- Allowed HDR formats (Dolby Vision, HDR10+, HDR10...)
- Allowed audio formats (TrueHD Atmos, DTS-X, DDP5.1...)
- Allowed sources (Blu-ray, WEB-DL, REMUX...)
- Optional size cap in GB — prefers releases under the limit, falls back to best match if nothing fits (This only applies to movies)

## Monitor

Add any movie or TV show to Monitor and Mediaarr will check every 6 hours for new releases matching your profile. For TV shows it checks for missing episodes, tries a complete series first, then season packs, then individual episodes.

Hit **Run Now** on the Monitor page to trigger an immediate check.

## License

MIT
