"""
stock_stills.py - fallback documentary-still fetcher for beats that Flow
refuses or fails to generate. Sources: Bing Images (keyless scrape) and
Openverse (keyless API, CC-licensed). Downloads are validated (JPEG/PNG
header + minimum size) before being accepted.

Library use:
    from stock_stills import fetch_still
    ok = fetch_still("a quiet courthouse exterior at dawn", "scene_07.jpg")
"""

import re
import shutil
import time
import urllib.parse
from pathlib import Path

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
}
MIN_BYTES = 20000


def _is_image(data):
    """Real JPEG, PNG or WebP of nontrivial size."""
    if not data or len(data) < MIN_BYTES:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _clean(url):
    url = url.replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
    return url if url.lower().startswith("http") else None


def bing_candidates(query, count=12):
    """Full-resolution image URLs scraped from Bing's async image endpoint."""
    q = urllib.parse.quote(query)
    url = (f"https://www.bing.com/images/async?q={q}&first=0&count={count}"
           f"&mkt=en-US&adlt=moderate")
    html = requests.get(url, headers=HEADERS, timeout=30).text
    murls = re.findall(r'murl&quot;:&quot;(.*?)&quot;', html)
    out = []
    for mu in murls:
        c = _clean(mu)
        if c:
            out.append(c)
    return out


def openverse_candidates(query, count=10):
    """CC-licensed image URLs from the Openverse API (keyless)."""
    q = urllib.parse.quote(query)
    r = requests.get(
        f"https://api.openverse.org/v1/images/?q={q}&pagesize={count}",
        headers=HEADERS, timeout=30).json()
    out = []
    for row in r.get("results", []):
        u = row.get("url")
        if u:
            out.append(u)
    return out


def fetch_still(query, dest, width=None, per_source=12):
    """Fetch one still for `query` from stock sources and write it to `dest`.
    Returns True when dest is written with a validated image."""
    cands = []
    try:
        cands += bing_candidates(query)[:per_source]
    except Exception as e:
        print(f"stock: Bing search failed ({type(e).__name__})", flush=True)
    try:
        cands += openverse_candidates(query)[:per_source]
    except Exception as e:
        print(f"stock: Openverse search failed ({type(e).__name__})", flush=True)

    for url in cands:
        try:
            data = requests.get(url, headers=HEADERS, timeout=30).content
        except Exception:
            continue
        if _is_image(data):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(data)
            return True
    return False
