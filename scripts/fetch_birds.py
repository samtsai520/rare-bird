#!/usr/bin/env python3
"""
Fetch eBird observation data for Taiwan and generate static JSON files
for the rare-bird website. Run by Hermes cron every 2 hours.

Generates:
  public/data/recent-1d.json   — recent observations, past 1 day
  public/data/recent-2d.json   — recent observations, past 2 days
  public/data/recent-3d.json   — recent observations, past 3 days
  public/data/notable-7d.json  — notable observations, past 7 days
  public/data/notable-14d.json — notable observations, past 14 days
  public/data/notable-30d.json — notable observations, past 30 days
  public/data/taxonomy-zh.json — taxonomy with Chinese names (cached 30 days)

Each recent/notable file contains a JSON array of observation records
with comNameZh added from taxonomy.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API_KEY = "b9vdcl0h5951"
BASE_URL = "https://api.ebird.org/v2"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "public" / "data"
TAXONOMY_FILE = DATA_DIR / "taxonomy-zh.json"
TAXONOMY_TIME_FILE = DATA_DIR / "taxonomy-zh.time"

TW_REGIONS = [
    "TW-CHA", "TW-CYI", "TW-CYQ", "TW-HSZ", "TW-HSQ", "TW-HUA",
    "TW-KHH", "TW-KEE", "TW-KIN", "TW-LIE", "TW-MIA", "TW-NAN",
    "TW-TPQ", "TW-PEN", "TW-PIF", "TW-TXG", "TW-TNN", "TW-TPE",
    "TW-TTT", "TW-TAO", "TW-ILA", "TW-YUN",
]

REQUEST_DELAY = 1.2  # seconds between API calls


def api_fetch(url, retries=3):
    """Fetch JSON from eBird API with retry on 429."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": API_KEY})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if "429" in str(e) and attempt < retries:
                wait = 3 * (attempt + 1)
                print(f"  429 rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            print(f"  ERROR: {e}", flush=True)
            return None
    return None


def fetch_taxonomy():
    """Fetch taxonomy with Chinese names. Cache for 30 days."""
    # Check cache
    if TAXONOMY_FILE.exists() and TAXONOMY_TIME_FILE.exists():
        cache_age = time.time() - TAXONOMY_TIME_FILE.stat().st_mtime
        if cache_age < 30 * 24 * 3600:
            print("Taxonomy cache still valid, skipping.", flush=True)
            with open(TAXONOMY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Fetching taxonomy (locale=zh)...", flush=True)
    data = api_fetch(f"{BASE_URL}/ref/taxonomy/ebird?fmt=json&locale=zh")
    if not data:
        # Try to use existing cache as fallback
        if TAXONOMY_FILE.exists():
            print("Using existing taxonomy cache as fallback.", flush=True)
            with open(TAXONOMY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    tax_map = {}
    for item in data:
        if item.get("speciesCode"):
            tax_map[item["speciesCode"]] = {
                "comNameZh": item.get("comName", ""),
                "sciName": item.get("sciName", ""),
            }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TAXONOMY_FILE, "w", encoding="utf-8") as f:
        json.dump(tax_map, f, ensure_ascii=False)
    with open(TAXONOMY_TIME_FILE, "w") as f:
        f.write(str(time.time()))
    print(f"Taxonomy saved: {len(tax_map)} species", flush=True)
    return tax_map


def fetch_recent(days):
    """Fetch recent observations from all 22 subnational1 regions."""
    print(f"Fetching recent (back={days})...", flush=True)
    all_obs = []
    for i, region in enumerate(TW_REGIONS):
        url = (
            f"{BASE_URL}/data/obs/{region}/recent"
            f"?back={days}&detail=simple&includeProvisional=true&sppLocale=zh"
        )
        data = api_fetch(url)
        if data:
            all_obs.extend(data)
            print(f"  [{i+1}/22] {region}: {len(data)} records", flush=True)
        else:
            print(f"  [{i+1}/22] {region}: FAILED", flush=True)
        time.sleep(REQUEST_DELAY)

    print(f"  Total: {len(all_obs)} records", flush=True)
    return all_obs


def fetch_notable(days):
    """Fetch notable observations for all of Taiwan."""
    print(f"Fetching notable (back={days})...", flush=True)
    url = (
        f"{BASE_URL}/data/obs/TW/recent/notable"
        f"?back={days}&detail=simple&includeProvisional=true"
    )
    data_en = api_fetch(url)
    if not data_en:
        data_en = []

    # Also fetch zh locale for notable
    time.sleep(REQUEST_DELAY)
    url_zh = (
        f"{BASE_URL}/data/obs/TW/recent/notable"
        f"?back={days}&detail=simple&includeProvisional=true&sppLocale=zh"
    )
    data_zh = api_fetch(url_zh)
    if not data_zh:
        data_zh = []

    # Build zh name map
    zh_map = {}
    for item in data_zh:
        if item.get("speciesCode") and item["speciesCode"] not in zh_map:
            zh_map[item["speciesCode"]] = item.get("comName", "")

    # Merge zh names into EN data
    for item in data_en:
        code = item.get("speciesCode", "")
        item["comNameZh"] = zh_map.get(code, "")

    print(f"  Total: {len(data_en)} records", flush=True)
    return data_en


def add_zh_names(obs_list, taxonomy):
    """Add comNameZh to each observation using taxonomy."""
    for item in obs_list:
        code = item.get("speciesCode", "")
        if not item.get("comNameZh"):
            tax = taxonomy.get(code)
            if tax:
                item["comNameZh"] = tax.get("comNameZh", "")
        if not item.get("comNameZh"):
            item["comNameZh"] = item.get("comName", "")
        item["comNameEn"] = item.get("comName", "")
    return obs_list


def save_json(filepath, data):
    """Save data as JSON with ensure_ascii=False."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    size_kb = os.path.getsize(filepath) / 1024
    print(f"  Saved {filepath.name}: {len(data)} records ({size_kb:.1f} KB)", flush=True)


CUMULATIVE_FILE = DATA_DIR / "recent-cumulative.json"
CUMULATIVE_DAYS = 30  # Keep last 30 days of data


def load_cumulative():
    """Load existing cumulative data."""
    if CUMULATIVE_FILE.exists():
        try:
            with open(CUMULATIVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []


def save_cumulative(existing, new_obs, taxonomy):
    """Merge new observations into cumulative data, trim to CUMULATIVE_DAYS."""
    # Build set of existing subId+speciesCode to avoid duplicates
    seen = set()
    for item in existing:
        key = (item.get("subId", ""), item.get("speciesCode", ""))
        seen.add(key)

    # Add only new records
    for item in new_obs:
        key = (item.get("subId", ""), item.get("speciesCode", ""))
        if key not in seen:
            seen.add(key)
            existing.append(item)

    # Add zh names to any records missing them
    for item in existing:
        if not item.get("comNameZh"):
            code = item.get("speciesCode", "")
            tax = taxonomy.get(code)
            if tax:
                item["comNameZh"] = tax.get("comNameZh", "")
            if not item.get("comNameZh"):
                item["comNameZh"] = item.get("comName", "")
        if not item.get("comNameEn"):
            item["comNameEn"] = item.get("comName", "")

    # Trim to last CUMULATIVE_DAYS by date
    cutoff = time.time() - CUMULATIVE_DAYS * 24 * 3600
    trimmed = []
    for item in existing:
        obs_dt = item.get("obsDt", "")
        try:
            # obsDt format: "2026-08-03 13:34" or "2026-08-03"
            dt = time.strptime(obs_dt[:10], "%Y-%m-%d")
            if time.mktime(dt) >= cutoff:
                trimmed.append(item)
        except:
            trimmed.append(item)  # Keep if can't parse date

    save_json(CUMULATIVE_FILE, trimmed)
    return trimmed


def main():
    print(f"=== Bird data fetch started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)

    # 1. Fetch taxonomy (cached 30 days)
    taxonomy = fetch_taxonomy()
    if not taxonomy:
        print("ERROR: No taxonomy data available. Aborting.", flush=True)
        sys.exit(1)

    # 2. Fetch recent observations for today (back=1) and merge into cumulative
    today_obs = fetch_recent(1)
    today_obs = add_zh_names(today_obs, taxonomy)
    existing = load_cumulative()
    print(f"  Existing cumulative: {len(existing)} records", flush=True)
    print(f"  New today: {len(today_obs)} records", flush=True)
    cumulative = save_cumulative(existing, today_obs, taxonomy)
    print(f"  Cumulative after merge: {len(cumulative)} records", flush=True)

    # Also save recent-1d/2d/3d as before (for quick static loads)
    for days in [2, 3]:
        obs = fetch_recent(days)
        obs = add_zh_names(obs, taxonomy)
        save_json(DATA_DIR / f"recent-{days}d.json", obs)
    # recent-1d is just today_obs
    save_json(DATA_DIR / "recent-1d.json", today_obs)

    # 3. Fetch notable observations (7, 14, 30 days)
    for days in [7, 14, 30]:
        obs = fetch_notable(days)
        # Fill any missing zh names from taxonomy
        for item in obs:
            if not item.get("comNameZh"):
                code = item.get("speciesCode", "")
                tax = taxonomy.get(code)
                if tax:
                    item["comNameZh"] = tax.get("comNameZh", item.get("comName", ""))
                else:
                    item["comNameZh"] = item.get("comName", "")
            item["comNameEn"] = item.get("comName", "")
        save_json(DATA_DIR / f"notable-{days}d.json", obs)

    print(f"=== Done at {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)


if __name__ == "__main__":
    main()