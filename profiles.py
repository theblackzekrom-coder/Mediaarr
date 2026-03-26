from parser import parse_torrent_title
import json
import os

PROFILES_FILE = "profiles.json"

# Ranking for finding best match within allowed formats
RESOLUTION_RANK = {
    "2160p": 4,
    "1080p": 3,
    "720p": 2,
    "480p": 1,
    None: 0
}

HDR_RANK = {
    "DOLBY VISION": 4,
    "HDR10+": 3,
    "HDR10": 2,
    "HLG": 1,
    "HDR": 1,
    None: 0
}

AUDIO_RANK = {
    "TRUEHD ATMOS": 9,
    "DTS-X": 8,
    "TRUEHD": 7,
    "DTS-HD MA": 6,
    "DTS-HD": 5,
    "DOLBY ATMOS": 5,
    "DDP5.1": 4,
    "DDP": 3,
    "EAC3": 3,
    "DD5.1": 2,
    "AC3": 2,
    "DTS": 2,
    "FLAC": 2,
    "AAC": 1,
    "MP3": 1,
    "STEREO": 1,
    None: 0
}

SOURCE_RANK = {
    "REMUX": 5,
    "BLURAY": 4,
    "BLU-RAY": 4,
    "WEB-DL": 3,
    "WEBRIP": 2,
    "HDTV": 1,
    None: 0
}

# All available options for each category
ALL_RESOLUTIONS = ["2160p", "1080p", "720p", "480p"]
ALL_HDR = ["DOLBY VISION", "HDR10+", "HDR10", "HLG", "SDR"]
ALL_AUDIO = [
    "TRUEHD ATMOS", "TRUEHD", "DTS-X", "DTS-HD MA", "DTS-HD",
    "DOLBY ATMOS", "DDP5.1", "DDP", "DD5.1", "EAC3",
    "AC3", "DTS", "FLAC", "AAC", "MP3", "STEREO"
]
ALL_SOURCES = ["REMUX", "BLURAY", "WEB-DL", "WEBRIP", "HDTV"]

DEFAULT_PROFILE = {
    "name": "Default",
    "resolutions": ["2160p", "1080p", "720p", "480p"],
    "hdr_formats": ["DOLBY VISION", "HDR10+", "HDR10", "HLG", "SDR"],
    "audio_formats": [
        "TRUEHD ATMOS", "TRUEHD", "DTS-X", "DTS-HD MA", "DTS-HD",
        "DOLBY ATMOS", "DDP5.1", "DDP", "DD5.1", "EAC3",
        "AC3", "DTS", "FLAC", "AAC", "MP3", "STEREO"
    ],
    "sources": ["REMUX", "BLURAY", "WEB-DL", "WEBRIP", "HDTV"],
    "is_default": True
}

def load_profiles():
    if not os.path.exists(PROFILES_FILE):
        save_profiles([DEFAULT_PROFILE])
        return [DEFAULT_PROFILE]
    with open(PROFILES_FILE, "r") as f:
        return json.load(f)

def save_profiles(profiles):
    with open(PROFILES_FILE, "w") as f:
        json.dump(profiles, f, indent=4)

def get_default_profile():
    profiles = load_profiles()
    for p in profiles:
        if p.get("is_default"):
            return p
    return profiles[0] if profiles else DEFAULT_PROFILE

def get_profile_by_name(name):
    profiles = load_profiles()
    for p in profiles:
        if p["name"] == name:
            return p
    return None

def score_torrent(parsed):
    res_score = RESOLUTION_RANK.get(parsed["resolution"], 0)
    hdr_score = HDR_RANK.get(parsed["hdr"], 0)
    audio_score = AUDIO_RANK.get(parsed["audio"], 0)
    source_score = SOURCE_RANK.get(parsed["source"], 0)
    return (res_score * 1000) + (source_score * 100) + (hdr_score * 10) + audio_score

def matches_profile(parsed, profile):
    # ANY logic — if all formats checked just accept everything
    res_any = set(profile.get("resolutions", [])) == set(ALL_RESOLUTIONS)
    hdr_any = set(profile.get("hdr_formats", [])) == set(ALL_HDR)
    audio_any = set(profile.get("audio_formats", [])) == set(ALL_AUDIO)
    source_any = set(profile.get("sources", [])) == set(ALL_SOURCES)

    # Resolution check
    if not res_any:
        if parsed["resolution"] not in profile.get("resolutions", []):
            return False

    # HDR check
    if not hdr_any:
        hdr_val = parsed["hdr"] if parsed["hdr"] else "SDR"
        if hdr_val not in profile.get("hdr_formats", []):
            return False

    # Audio check
    if not audio_any:
        if parsed["audio"] not in profile.get("audio_formats", []):
            return False

    # Source check
    if not source_any:
        if parsed["source"] not in profile.get("sources", []):
            return False

    return True

def get_best_match(torrent_list, profile):
    candidates = []

    for torrent in torrent_list:
        parsed = parse_torrent_title(torrent)

        if matches_profile(parsed, profile):
            candidates.append((torrent, parsed, score_torrent(parsed)))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: x[2], reverse=True)
    best_title, best_parsed, best_score = candidates[0]

    return best_title, best_parsed

if __name__ == "__main__":
    torrents = [
        "Family.Guy.S21E04.1080p.WEB-DL.DDP5.1",
        "Family.Guy.S21E04.720p.HDTV.DD5.1",
        "Family.Guy.S21E04.2160p.BluRay.HDR10.DTS-X",
        "Family.Guy.S21E04.1080p.BluRay.TrueHD.Atmos.7.1",
        "Family.Guy.S21E04.480p.WEB-DL.AAC",
        "Family.Guy.S21E04.1080p.WEB-DL.EAC3",
        "Family.Guy.S21E04.1080p.HDTV.Stereo",
    ]

    profile = DEFAULT_PROFILE

    print("Available torrents:")
    for t in torrents:
        parsed = parse_torrent_title(t)
        score = score_torrent(parsed)
        print(f"  Score {score}: {t}")

    print(f"\nUsing profile: {profile['name']}")
    best, parsed = get_best_match(torrents, profile)
    print(f"\nBest match: {best}")
    print(f"Parsed: {parsed}")