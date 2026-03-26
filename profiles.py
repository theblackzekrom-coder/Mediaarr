from parser import parse_torrent_title

# Define quality tiers for resolution, HDR, and audio
RESOLUTION_RANK = {
    "2160p": 4,
    "1080p": 3,
    "720p": 2,
    "480p": 1,
    None: 0
}

HDR_RANK = {
    "DV": 4,
    "HDR10+": 3,
    "HDR10": 2,
    "HLG": 1,
    "HDR": 1,
    None: 0
}

AUDIO_RANK = {
    "TRUEHD ATMOS": 6,
    "DTS-X": 5,
    "TRUEHD": 4,
    "DTS-HD": 3,
    "ATMOS": 3,
    "DDP5.1": 2,
    "DD5.1": 1,
    "DTS": 1,
    "AAC": 0,
    None: 0
}

def score_torrent(parsed):
    res_score = RESOLUTION_RANK.get(parsed["resolution"], 0)
    hdr_score = HDR_RANK.get(parsed["hdr"], 0)
    audio_score = AUDIO_RANK.get(parsed["audio"], 0)
    return (res_score * 100) + (hdr_score * 10) + audio_score

def get_best_match(torrent_list, profile):
    resolution_min = profile.get("resolution")
    hdr_required = profile.get("hdr_required", False)
    audio_min = profile.get("audio_min")

    candidates = []

    for torrent in torrent_list:
        parsed = parse_torrent_title(torrent)

        # Check minimum resolution
        if resolution_min:
            if RESOLUTION_RANK.get(parsed["resolution"], 0) < RESOLUTION_RANK.get(resolution_min, 0):
                continue

        # Check HDR required
        if hdr_required and parsed["hdr"] is None:
            continue

        # Check minimum audio
        if audio_min:
            if AUDIO_RANK.get(parsed["audio"], 0) < AUDIO_RANK.get(audio_min, 0):
                continue

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
    ]

    profile = {
        "resolution": "1080p",
        "hdr_required": False,
        "audio_min": "DDP5.1"
    }

    print("Available torrents:")
    for t in torrents:
        parsed = parse_torrent_title(t)
        score = score_torrent(parsed)
        print(f"  Score {score}: {t}")

    print(f"\nProfile: {profile}")
    best, parsed = get_best_match(torrents, profile)
    print(f"\nBest match: {best}")
    print(f"Parsed: {parsed}")