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
  (bird names read from static public/data/taiwan-birds.json)

Each recent/notable file contains a JSON array of observation records
with comNameZh added from taiwan-birds.json.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# eBird API key 安全讀取：優先環境變數 EBIRD_API_KEY，其次 gitignored config.json。
# 兩者皆無 → 直接失敗，不提供預設值。切勿把 key 寫死在這裡。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _load_api_key():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if key:
        return key
    cfg = PROJECT_ROOT / "config.json"
    if cfg.exists():
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                key = json.load(f).get("ebird_api_key", "").strip()
        except Exception:
            key = ""
    if key:
        return key
    raise RuntimeError(
        "找不到 eBird API key。請設定環境變數 EBIRD_API_KEY，或建立 gitignored 的 config.json（內容 {\"ebird_api_key\":\"...\"}）。"
    )

API_KEY = _load_api_key()
BASE_URL = "https://api.ebird.org/v2"
DATA_DIR = PROJECT_ROOT / "public" / "data"
TAIWAN_BIRDS_FILE = DATA_DIR / "taiwan-birds.json"

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


def load_bird_names():
    """Load Taiwan bird names from static taiwan-birds.json (777 species, ~100KB).

    Returns a flat map {speciesCode: {comNameZh, comNameEn, sciName}} for O(1) lookup.
    No eBird API calls — reads the prebuilt static file.
    """
    if not TAIWAN_BIRDS_FILE.exists():
        print("  ERROR: 找不到 taiwan-birds.json（請先執行 build_taiwan_birds.py）", flush=True)
        return {}
    try:
        with open(TAIWAN_BIRDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ERROR 讀取 taiwan-birds.json: {e}", flush=True)
        return {}
    species = data.get("species", {})
    tax_map = {}
    for code, info in species.items():
        tax_map[code] = {
            "comNameZh": info.get("comNameZh", ""),
            "comNameEn": info.get("comNameEn", ""),
            "sciName": info.get("sciName", ""),
        }
    print(f"  已載入 taiwan-birds.json：{len(tax_map)} 種（台灣鳥類名稱）", flush=True)
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


def add_zh_names(obs_list, bird_names):
    """Add comNameZh to each observation using bird_names."""
    for item in obs_list:
        code = item.get("speciesCode", "")
        if not item.get("comNameZh"):
            tax = bird_names.get(code)
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


def save_cumulative(existing, new_obs, bird_names):
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
            tax = bird_names.get(code)
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

    # 1. Load Taiwan bird names (static taiwan-birds.json, no API calls)
    bird_names = load_bird_names()
    if not bird_names:
        print("ERROR: No bird_names data available. Aborting.", flush=True)
        sys.exit(1)

    # 2. Fetch recent observations for today (back=1) and merge into cumulative
    today_obs = fetch_recent(1)
    today_obs = add_zh_names(today_obs, bird_names)
    existing = load_cumulative()
    print(f"  Existing cumulative: {len(existing)} records", flush=True)
    print(f"  New today: {len(today_obs)} records", flush=True)
    cumulative = save_cumulative(existing, today_obs, bird_names)
    print(f"  Cumulative after merge: {len(cumulative)} records", flush=True)

    # Also save recent-1d/2d/3d as before (for quick static loads)
    for days in [2, 3]:
        obs = fetch_recent(days)
        obs = add_zh_names(obs, bird_names)
        save_json(DATA_DIR / f"recent-{days}d.json", obs)
    # recent-1d is just today_obs
    save_json(DATA_DIR / "recent-1d.json", today_obs)

    # 3. Fetch notable observations (7, 14, 30 days)
    for days in [7, 14, 30]:
        obs = fetch_notable(days)
        # Fill any missing zh names from bird_names
        for item in obs:
            if not item.get("comNameZh"):
                code = item.get("speciesCode", "")
                tax = bird_names.get(code)
                if tax:
                    item["comNameZh"] = tax.get("comNameZh", item.get("comName", ""))
                else:
                    item["comNameZh"] = item.get("comName", "")
            item["comNameEn"] = item.get("comName", "")
        save_json(DATA_DIR / f"notable-{days}d.json", obs)

    print(f"=== Done at {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)


if __name__ == "__main__":
    main()