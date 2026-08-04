#!/usr/bin/env python3
"""
build_taiwan_birds.py — 產生台灣鳥類中英文名對照檔

從 eBird 官方資料一次抓取台灣鳥種，產生精簡的靜態 JSON：
  public/data/taiwan-birds.json

來源：
  - /product/spplist/TW  → 台灣鳥種 code 清單（官方，一次抓取，不用逐筆）
  - /ref/taxonomy/ebird  → 英文名 + 學名
  - /ref/taxonomy/ebird?locale=zh → 中文名

產出格式（依 speciesCode 索引，方便前端 O(1) 查詢）：
{
  "lastUpdated": "2026-08-04",
  "species": {
    "lewduc1": { "comNameZh": "樹鴨", "comNameEn": "Lesser Whistling-Duck", "sciName": "..." }
  }
}

安全：API key 從環境變數或 gitignored config.json 讀取。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "public" / "data" / "taiwan-birds.json"
BASE_URL = "https://api.ebird.org/v2"


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
    raise RuntimeError("找不到 eBird API key（環境變數 EBIRD_API_KEY 或 config.json）")


def api_fetch(url, key, retries=3):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if "429" in str(e) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  ERROR: {e}", flush=True)
            return None
    return None


def main():
    key = _load_api_key()

    # 1. 台灣鳥種清單
    print("抓取台灣鳥種清單 /product/spplist/TW ...", flush=True)
    tw_codes = api_fetch(f"{BASE_URL}/product/spplist/TW", key)
    if not tw_codes:
        print("❌ 無法取得台灣鳥種清單", flush=True)
        sys.exit(1)
    print(f"  ✅ 台灣鳥種 {len(tw_codes)} 種", flush=True)

    # 2. 英文 taxonomy
    print("抓取英文 taxonomy ...", flush=True)
    tax_en = api_fetch(f"{BASE_URL}/ref/taxonomy/ebird?fmt=json", key)
    en_map = {t["speciesCode"]: t for t in tax_en} if tax_en else {}
    print(f"  ✅ 英文 {len(en_map)} 筆", flush=True)
    time.sleep(1.5)

    # 3. 中文 taxonomy
    print("抓取中文 taxonomy ...", flush=True)
    tax_zh = api_fetch(f"{BASE_URL}/ref/taxonomy/ebird?fmt=json&locale=zh", key)
    zh_map = {t["speciesCode"]: t.get("comName", "") for t in tax_zh} if tax_zh else {}
    print(f"  ✅ 中文 {len(zh_map)} 筆", flush=True)

    # 4. 組裝
    species = {}
    missing_zh = []
    for code in tw_codes:
        en_info = en_map.get(code, {})
        en = en_info.get("comName", "")
        sci = en_info.get("sciName", "")
        zh = zh_map.get(code, "")
        if not zh or not any('\u4e00' <= c <= '\u9fff' for c in zh):
            missing_zh.append((code, en))
        species[code] = {
            "comNameZh": zh,
            "comNameEn": en,
            "sciName": sci,
        }

    data = {
        "lastUpdated": date.today().isoformat(),
        "species": species,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"\n✅ 已存 {OUTPUT_FILE.name}: {len(species)} 種 ({size_kb:.1f} KB)", flush=True)
    print(f"   缺中文（將顯示英文）：{len(missing_zh)} 種", flush=True)


if __name__ == "__main__":
    main()
