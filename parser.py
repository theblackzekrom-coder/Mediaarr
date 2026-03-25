import re

def parse_torrent_title(title):
    # Normalize separators to spaces first
    normalized = title.replace(".", " ").replace("_", " ")
    title_upper = normalized.upper()
    
    # Resolution
    resolution = None
    for res in ["2160P", "4K", "1080P", "720P", "480P"]:
        if res in title_upper:
            resolution = res.lower().replace("4k", "2160p")
            break
    
    # HDR format
    hdr = None
    for format in ["DV", "HDR10+", "HDR10", "HLG", "HDR"]:
        if format in title_upper:
            hdr = format
            break
    
    # Audio format
    audio = None
    for format in ["TRUEHD ATMOS", "TRUEHD", "DTS-X", "DTS-HD",
                   "ATMOS", "DDP5.1", "DD5.1", "DTS", "AAC"]:
        if format in title_upper:
            audio = format
            break

    # Source
    source = None
    for src in ["BLURAY", "BLU-RAY", "WEB-DL", "WEBRIP", "HDTV", "REMUX"]:
        if src in title_upper:
            source = src
            break

    return {
        "resolution": resolution,
        "hdr": hdr,
        "audio": audio,
        "source": source
    }

# Test it
titles = [
    "The.Bear.S03E01.2160p.UHD.BluRay.HDR10.DTS-X.7.1",
    "Family.Guy.S21E04.1080p.WEB-DL.DDP5.1",
    "Inception.2010.4K.UHD.BluRay.HDR10+.TrueHD.Atmos.7.1",
    "Breaking.Bad.S01E01.720p.HDTV.DD5.1",
    "Dune.2021.2160p.REMUX.BluRay.DV.TrueHD.Atmos.7.1"
]

for title in titles:
    print(f"\nTitle: {title}")
    print(parse_torrent_title(title))