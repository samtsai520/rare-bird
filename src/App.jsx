import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Compass,
  MapPin,
  Calendar,
  RotateCw,
  Moon,
  Sun,
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Settings,
  Key,
  Eye
} from 'lucide-react';

const STORAGE_API_KEY_KEY = "taiwan_birds_ebird_api_key";
const STORAGE_THEME_KEY = "taiwan_birds_theme";

// Cache keys — notable: per-days; worth: fixed (yesterday+today), same-day TTL
const CACHE_KEYS = {
  obs: (days) => `taiwan_birds_notable_obs_${days}d`,
  time: (days) => `taiwan_birds_notable_time_${days}d`,
};
const WORTH_CACHE_KEYS = {
  obs: "taiwan_birds_worth_obs",
  time: "taiwan_birds_worth_time",
};

// 22 subnational1 regions, split into 3 batches for the `r` param (<=10 per call)
const TW_REGION_BATCHES = [
  "TW-CHA,TW-CYI,TW-CYQ,TW-HSZ,TW-HSQ,TW-HUA,TW-KHH,TW-KEE,TW-KIN,TW-LIE",
  "TW-MIA,TW-NAN,TW-TPQ,TW-PEN,TW-PIF,TW-TXG,TW-TNN,TW-TPE,TW-TTT,TW-TAO",
  "TW-ILA,TW-YUN",
];
const WORTH_BACK_DAYS = 30; // baseline window (fixed, not user-selectable)
const WORTH_MIN_STRICT = 3; // fallback triggers when strict rare count < this
const WORTH_FALLBACK_COUNT = 8; // how many species to show in fallback mode
const WORTH_MAX_LOCATIONS = 10; // 觀測點數超過此值 = 常見鳥，排除（綠繡眼/白頭翁等）

// Outer-island regions: Kinmen, Matsu, Penghu, Lanyu. Anything else counts as mainland (本島).
const ISLAND_KEYWORDS = ['金門', '馬祖', '澎湖', '蘭嶼'];
const isIslandLoc = (name) => ISLAND_KEYWORDS.some(k => (name || '').includes(k));

// ---- 全域 eBird 請求節流器 ----
// 所有 eBird API 呼叫共用同一把鎖，避免多個功能（稀有種/值得一看/月份統計）
// 同時對 eBird 爆發請求而觸發 429 限速。每筆請求至少間隔 MIN_API_INTERVAL ms。
const MIN_API_INTERVAL = 1400; // ms，eBird 約 1 req/sec
let lastApiCallTime = 0;
let apiQueue = Promise.resolve();

async function throttleApiCall(fn) {
  const run = async () => {
    const wait = Math.max(0, MIN_API_INTERVAL - (Date.now() - lastApiCallTime));
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
    lastApiCallTime = Date.now();
    return fn();
  };
  const result = apiQueue.then(run, run); // 序列化，失敗也繼續排隊
  apiQueue = result.then(() => {}, () => {});
  return result;
}


export default function App() {
  // Load API Key from localStorage
  const [apiKey, setApiKey] = useState(() => {
    return localStorage.getItem(STORAGE_API_KEY_KEY) || '';
  });
  const [inputApiKey, setInputApiKey] = useState(apiKey);
  const [apiKeySavedMsg, setApiKeySavedMsg] = useState('');

  // Active tab: 'notable' (稀有種快報) | 'worth' (值得一看的鳥)
  const [activeTab, setActiveTab] = useState('notable');

  // Notable tab state
  const [observations, setObservations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(() => {
    return Number(localStorage.getItem("taiwan_birds_notable_days_setting")) || 7;
  });
  const [lastUpdated, setLastUpdated] = useState(null);

  // Worth tab state
  const [worthList, setWorthList] = useState([]);   // processed species list
  const [worthLoading, setWorthLoading] = useState(false);
  const [worthError, setWorthError] = useState(null);
  const [worthLastUpdated, setWorthLastUpdated] = useState(null);
  const [worthMeta, setWorthMeta] = useState(null); // { targetDate, count, strictUsed }
  // Island section open state: mainland (本島) expanded by default, outer islands (外島) collapsed
  const [islandOpen, setIslandOpen] = useState({ main: true, island: false });

  // First-seen tab state (reads static first-seen.json, zero API requests)
  const [firstSeen, setFirstSeen] = useState(null);   // parsed JSON {year, lastUpdated, species}
  const [firstSeenLoading, setFirstSeenLoading] = useState(false);
  const [firstSeenError, setFirstSeenError] = useState(null);
  const [firstSeenView, setFirstSeenView] = useState('month'); // 'month' | 'recent'

  // Static Taiwan-bird names (code -> {comNameZh}) for zero-API Chinese names
  const [birdNames, setBirdNames] = useState({});
  const [birdNamesLoaded, setBirdNamesLoaded] = useState(false);
  // ref mirror so fetchWorth always reads latest birdNames (avoids closure race)
  const birdNamesRef = useRef(birdNames);
  useEffect(() => { birdNamesRef.current = birdNames; }, [birdNames]);

  const [theme, setTheme] = useState(() => {
    return localStorage.getItem(STORAGE_THEME_KEY) || 'dark';
  });

  // Accordion state
  const [expandedSpecies, setExpandedSpecies] = useState(new Set());
  const [showSettings, setShowSettings] = useState(false);

  // Initialize Theme
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_THEME_KEY, theme);
  }, [theme]);

  // Register Service Worker
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', async () => {
        try {
          const registration = await navigator.serviceWorker.register('/sw.js');
          console.log('[App] ServiceWorker registered: ', registration.scope);
        } catch (err) {
          console.error('[App] ServiceWorker failed: ', err);
        }
      });
    }
  }, []);

  // AbortController for cancelling stale fetches
  const abortRef = useRef(null);
  const worthAbortRef = useRef(null);

  // ---- NOTABLE fetch (unchanged behaviour) ----
  const fetchObservations = useCallback(async (selectedDays, activeKey = apiKey, isManual = false) => {
    if (!activeKey) {
      setError("請先設定您的 eBird API Key。");
      setLoading(false);
      return;
    }

    if (isManual && abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    const maxRetries = 2;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const basePath = `https://api.ebird.org/v2/data/obs/TW/recent/notable`;
        const urlEn = `${basePath}?back=${selectedDays}&detail=full&includeProvisional=true`;
        const urlZh = `${basePath}?back=${selectedDays}&detail=full&includeProvisional=true&sppLocale=zh`;
        const headers = { 'x-ebirdapitoken': activeKey };

        const [resEn, resZh] = await Promise.all([
          throttleApiCall(() => fetch(urlEn, { headers, signal: controller.signal })),
          throttleApiCall(() => fetch(urlZh, { headers, signal: controller.signal }))
        ]);

        if (resEn.status === 429 || resZh.status === 429) {
          if (attempt < maxRetries) {
            await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
            continue;
          }
        }

        if (!resEn.ok || !resZh.ok) {
          throw new Error(`eBird API error: ${resEn.status} / ${resZh.status}`);
        }

        const dataEn = await resEn.json();
        const dataZh = await resZh.json();

        if (controller.signal.aborted) return;

        const zhNamesMap = {};
        dataZh.forEach(item => {
          if (item.speciesCode) {
            zhNamesMap[item.speciesCode] = item.comName;
          }
        });

        const mergedList = dataEn.map(item => ({
          ...item,
          comNameZh: zhNamesMap[item.speciesCode] || item.comName,
          comNameEn: item.comName
        }));

        const nowStr = new Date().toISOString();

        setObservations(mergedList);
        setLoading(false);
        setError(null);
        setLastUpdated(nowStr);

        localStorage.setItem(CACHE_KEYS.obs(selectedDays), JSON.stringify(mergedList));
        localStorage.setItem(CACHE_KEYS.time(selectedDays), nowStr);
        localStorage.setItem("taiwan_birds_notable_days_setting", selectedDays.toString());

        return; // Success

      } catch (err) {
        if (err.name === 'AbortError') return;

        if (attempt < maxRetries) {
          await new Promise(r => setTimeout(r, 1500 * (attempt + 1)));
          continue;
        }

        console.error(`Fetch failed (attempt ${attempt + 1}):`, err);
        setLoading(false);

        if (observations.length > 0) {
          setError(null);
        } else {
          setError("查詢逾時或網路不穩，請稍後再按「更新」重試。");
        }
      }
    }
  }, [apiKey, observations.length]);

  // ---- WORTH fetch: rare birds reported yesterday or today, absent in prior 30d ----
  const fetchWorth = useCallback(async (activeKey = apiKey, isManual = false) => {
    if (!activeKey) {
      setWorthError("請先設定您的 eBird API Key。");
      setWorthLoading(false);
      return;
    }

    if (isManual && worthAbortRef.current) {
      worthAbortRef.current.abort();
    }
    const controller = new AbortController();
    worthAbortRef.current = controller;

    setWorthLoading(true);
    setWorthError(null);

    // 等 birdNames（中文名檔，~100KB）載入完成，確保取名稱不落空
    // 最多等 3 秒；若仍未就緒則用目前已載入的（缺中文就顯示英文名）
    if (!birdNamesRef.current || Object.keys(birdNamesRef.current).length === 0) {
      for (let i = 0; i < 15; i++) {
        if (birdNamesRef.current && Object.keys(birdNamesRef.current).length > 0) break;
        await new Promise(r => setTimeout(r, 200));
      }
    }

    // Target window = calendar 3天前 + 前天 + 昨天 + today (4 days)
    const now = new Date();
    const fmtD = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const todayStr = fmtD(now);
    const yest = new Date(now); yest.setDate(yest.getDate() - 1);   const yestStr = fmtD(yest);
    const twoAgo = new Date(now); twoAgo.setDate(twoAgo.getDate() - 2); const twoAgoStr = fmtD(twoAgo);
    const threeAgo = new Date(now); threeAgo.setDate(threeAgo.getDate() - 3); const threeAgoStr = fmtD(threeAgo);
    const targetDates = new Set([threeAgoStr, twoAgoStr, yestStr, todayStr]);

    const maxRetries = 3;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const headers = { 'x-ebirdapitoken': activeKey };
        // Fetch each region-batch sequentially, ONE request per batch (EN only).
        // Chinese names come from the static birdNames (zero extra eBird API calls),
        // which cuts request count from 6 → 3 and halves the chance of a 429/timeout.
        const enRecords = [];
        for (const batch of TW_REGION_BATCHES) {
          const url = `https://api.ebird.org/v2/data/obs/TW/recent?back=${WORTH_BACK_DAYS}&detail=full&includeProvisional=true&r=${encodeURIComponent(batch)}`;
          const res = await throttleApiCall(() => fetch(url, { headers, signal: controller.signal }));
          if (res.status === 429) {
            throw new Error('RATE_LIMIT');
          }
          if (!res.ok) {
            throw new Error('eBird API error');
          }
          const dataEn = await res.json();
          if (controller.signal.aborted) return;
          enRecords.push(...dataEn);
          // small gap between batches
          await new Promise(r => setTimeout(r, 900));
        }
        if (controller.signal.aborted) return;

        // Merge zh names from static Taiwan-bird birdNames; fallback to EN name
        const allRecords = enRecords.map(item => {
          const t = birdNamesRef.current && birdNamesRef.current[item.speciesCode];
          return {
            ...item,
            comNameZh: (t && t.comNameZh) || item.comName,
            comNameEn: item.comName,
          };
        });

        // Classify
        const baseline = new Set();   // species present before 3天前 (within 30d)
        const targetByCode = {};      // code -> { records, count }
        const countPerCode = {};      // rarity score = total records in window
        const locPerCode = {};        // code -> Set(unique locId) 觀測點數
        for (const rec of allRecords) {
          const d = (rec.obsDt || '').slice(0, 10);
          if (!d) continue;
          const code = rec.speciesCode;
          countPerCode[code] = (countPerCode[code] || 0) + 1;
          if (!locPerCode[code]) locPerCode[code] = new Set();
          if (rec.locId) locPerCode[code].add(rec.locId);
          if (targetDates.has(d)) {
            if (!targetByCode[code]) {
              targetByCode[code] = { records: [] };
            }
            targetByCode[code].records.push(rec);
          } else if (d < threeAgoStr) {
            baseline.add(code);
          }
        }

        // 排除常見鳥：觀測點數 ≥ 門檻(10) = 全台普遍出現，非「值得一看」稀有鳥
        const isCommonBird = (code) => (locPerCode[code] ? locPerCode[code].size : 0) >= WORTH_MAX_LOCATIONS;

        // Strict rare: in target, not in baseline, 且非常見鳥
        const rareCodes = Object.keys(targetByCode)
          .filter(code => !baseline.has(code) && !isCommonBird(code));

        // Fallback: if too few strict rare, rank target species by rarity score (fewer records = rarer)
        let selectedCodes;
        let strictUsed = rareCodes.length >= WORTH_MIN_STRICT;
        if (strictUsed) {
          selectedCodes = rareCodes;
        } else {
          selectedCodes = Object.keys(targetByCode)
            .filter(code => !isCommonBird(code))
            .sort((a, b) => (countPerCode[a] || 0) - (countPerCode[b] || 0))
            .slice(0, WORTH_FALLBACK_COUNT);
        }

        // Build species list (only target-window records for display)
        const grouped = {};
        for (const code of selectedCodes) {
          const g = targetByCode[code];
          if (!g) continue;
          const rec = g.records[0];
          grouped[code] = {
            speciesCode: code,
            comNameZh: rec.comNameZh,
            comNameEn: rec.comNameEn,
            rarityScore: countPerCode[code] || 0,
            sightings: g.records,
          };
        }
        const list = Object.values(grouped);

        // Sort species groups by GPS location (north→south by lat of latest sighting),
        // then by latest sighting date desc as secondary key (user's choice: GPS primary, date secondary)
        list.forEach(sp => sp.sightings.sort((a, b) => new Date(b.obsDt) - new Date(a.obsDt)));
        list.sort((a, b) => {
          const aLat = a.sightings[0].lat;
          const bLat = b.sightings[0].lat;
          if (typeof aLat === 'number' && typeof bLat === 'number' && aLat !== bLat) {
            return bLat - aLat; // north first (higher latitude)
          }
          return new Date(b.sightings[0].obsDt) - new Date(a.sightings[0].obsDt);
        });

        const nowStr = new Date().toISOString();
        setWorthList(list);
        setWorthLoading(false);
        setWorthError(null);
        setWorthLastUpdated(nowStr);
        setWorthMeta({ targetDate: todayStr, yesterday: yestStr, twoDaysAgo: twoAgoStr, threeDaysAgo: threeAgoStr, count: list.length, strictUsed });

        localStorage.setItem(WORTH_CACHE_KEYS.obs, JSON.stringify({ list, meta: { targetDate: todayStr, yesterday: yestStr, twoDaysAgo: twoAgoStr, threeDaysAgo: threeAgoStr, count: list.length, strictUsed } }));
        localStorage.setItem(WORTH_CACHE_KEYS.time, nowStr);

        return;
      } catch (err) {
        if (err.name === 'AbortError') return;
        if (attempt < maxRetries) {
          await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
          continue;
        }
        console.error(`Worth fetch failed (attempt ${attempt + 1}):`, err);
        setWorthLoading(false);
        // 若仍有資料（記憶體或 localStorage 快取），保留顯示並標示資料可能較舊，
        // 避免切換 tab 時因一時限速而閃出「載入失敗」空畫面。
        const cached = localStorage.getItem(WORTH_CACHE_KEYS.obs);
        if (worthList.length > 0) {
          setWorthError(null); // 已有資料，不顯示錯誤
        } else if (cached) {
          try {
            const parsed = JSON.parse(cached);
            setWorthList(parsed.list || []);
            setWorthMeta(parsed.meta || null);
            setWorthError(null);
          } catch {
            setWorthError("查詢逾時或網路不穩，請稍後再按「更新」重試。");
          }
        } else {
          setWorthError("查詢逾時或網路不穩，請稍後再按「更新」重試。");
        }
      }
    }
  }, [apiKey, worthList.length, birdNames]);

  // Auto-load: notable check cache first, then fetch
  useEffect(() => {
    if (!apiKey) return;

    const cachedObs = localStorage.getItem(CACHE_KEYS.obs(days));
    const cachedTime = localStorage.getItem(CACHE_KEYS.time(days));

    let needsFetch = true;

    if (cachedObs && cachedTime) {
      const parsedTime = new Date(cachedTime);
      const today = new Date();
      if (
        parsedTime.getFullYear() === today.getFullYear() &&
        parsedTime.getMonth() === today.getMonth() &&
        parsedTime.getDate() === today.getDate()
      ) {
        try {
          const parsedObs = JSON.parse(cachedObs);
          setObservations(parsedObs);
          setLastUpdated(cachedTime);
          needsFetch = false;
        } catch { /* ignore */ }
      }
    }

    if (needsFetch && !loading) {
      fetchObservations(days, apiKey);
    }
  }, [apiKey, days]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-load: worth check cache first, then fetch
  useEffect(() => {
    if (!apiKey) return;
    const cached = localStorage.getItem(WORTH_CACHE_KEYS.obs);
    const cachedTime = localStorage.getItem(WORTH_CACHE_KEYS.time);
    let needsFetch = true;
    let cacheHasData = false;
    let cacheMissingZh = false;
    if (cached && cachedTime) {
      const parsedTime = new Date(cachedTime);
      const today = new Date();
      if (
        parsedTime.getFullYear() === today.getFullYear() &&
        parsedTime.getMonth() === today.getMonth() &&
        parsedTime.getDate() === today.getDate()
      ) {
        try {
          const parsed = JSON.parse(cached);
          setWorthList(parsed.list || []);
          setWorthMeta(parsed.meta || null);
          setWorthLastUpdated(cachedTime);
          cacheHasData = true;
          // 若快取裡有鳥種缺中文名（birdNames 載入前抓的），birdNames 就緒後需重抓補名
          cacheMissingZh = (parsed.list || []).some(sp =>
            sp.comNameZh && sp.comNameEn && sp.comNameZh === sp.comNameEn
          );
          needsFetch = false;
        } catch { /* ignore */ }
      }
    }
    // birdNames 剛載入完成，且快取資料缺中文名 → 強制重抓，補上中文名
    if (birdNamesLoaded && cacheHasData && cacheMissingZh) {
      needsFetch = true;
    }
    if (needsFetch && !worthLoading) {
      fetchWorth(apiKey);
    }
  }, [apiKey, activeTab, birdNames, birdNamesLoaded]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load static first-seen.json + birdNames (works even without API key — zero live requests)
  useEffect(() => {
    let cancelled = false;
    const loadFirstSeen = async () => {
      setFirstSeenLoading(true);
      setFirstSeenError(null);
      try {
        const res = await fetch('/data/first-seen.json');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setFirstSeen(data);
          setFirstSeenError(null);
        }
      } catch (err) {
        if (!cancelled) setFirstSeenError('今年首見資料尚未產生，請稍後再試。');
      } finally {
        if (!cancelled) setFirstSeenLoading(false);
      }
    };
    loadFirstSeen();
    // Load static Taiwan-bird names (777 species, ~100KB) — zero API requests.
    // Much smaller + faster than the old 1.5MB global birdNames.
    fetch('/data/taiwan-birds.json')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => { if (!cancelled) { setBirdNames((data && data.species) || {}); setBirdNamesLoaded(true); } })
      .catch(() => { if (!cancelled) setBirdNamesLoaded(true); });
    return () => { cancelled = true; };
  }, []);

  const handleNotableSearch = () => {
    if (!apiKey) {
      setShowSettings(true);
      return;
    }
    fetchObservations(days, apiKey, true);
  };

  const handleWorthSearch = () => {
    if (!apiKey) {
      setShowSettings(true);
      return;
    }
    fetchWorth(apiKey, true);
  };

  const saveApiKey = (keyToSave = inputApiKey) => {
    const trimmed = keyToSave.trim();
    setApiKey(trimmed);
    localStorage.setItem(STORAGE_API_KEY_KEY, trimmed);
    setApiKeySavedMsg("API Key 儲存成功！正在獲取觀測資料...");
    setTimeout(() => setApiKeySavedMsg(''), 3000);
    if (trimmed) {
      if (activeTab === 'worth') {
        fetchWorth(trimmed);
      } else {
        fetchObservations(days, trimmed);
      }
    }
  };

  const clearApiKey = () => {
    setApiKey('');
    setInputApiKey('');
    setObservations([]);
    setWorthList([]);
    setWorthMeta(null);
    setLoading(false);
    setWorthLoading(false);
    setError(null);
    setWorthError(null);
    setLastUpdated(null);
    setWorthLastUpdated(null);
    localStorage.removeItem(STORAGE_API_KEY_KEY);
    [1, 3, 5, 7, 10, 14, 21, 30].forEach(d => {
      localStorage.removeItem(`taiwan_birds_notable_obs_${d}d`);
      localStorage.removeItem(`taiwan_birds_notable_time_${d}d`);
    });
    localStorage.removeItem("taiwan_birds_notable_days_setting");
    localStorage.removeItem(WORTH_CACHE_KEYS.obs);
    localStorage.removeItem(WORTH_CACHE_KEYS.time);
    localStorage.removeItem("taiwan_birds_notable_obs_cache");
    localStorage.removeItem("taiwan_birds_notable_last_fetch_time");
    setApiKeySavedMsg("API Key 已清除。觀測功能已停用。");
    setTimeout(() => setApiKeySavedMsg(''), 3000);
  };

  // Accordion Toggle
  const toggleSpecies = (speciesCode) => {
    const nextExpanded = new Set(expandedSpecies);
    if (nextExpanded.has(speciesCode)) {
      nextExpanded.delete(speciesCode);
    } else {
      nextExpanded.add(speciesCode);
    }
    setExpandedSpecies(nextExpanded);
  };

  // Group observations by species
  const getGroupedSpeciesList = (obs) => {
    const grouped = {};
    obs.forEach(item => {
      const code = item.speciesCode;
      if (!grouped[code]) {
        grouped[code] = {
          speciesCode: item.speciesCode,
          comNameZh: item.comNameZh,
          comNameEn: item.comNameEn,
          sightings: []
        };
      }
      grouped[code].sightings.push(item);
    });

    const groupedArray = Object.values(grouped);

    groupedArray.forEach(sp => {
      sp.sightings.sort((a, b) => new Date(b.obsDt) - new Date(a.obsDt));
    });

    groupedArray.sort((a, b) => {
      const aLatest = new Date(a.sightings[0].obsDt);
      const bLatest = new Date(b.sightings[0].obsDt);
      return bLatest - aLatest;
    });

    return groupedArray;
  };

  const formatTime = (isoString) => {
    if (!isoString) return '無紀錄';
    const date = new Date(isoString);
    return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  };

  // Accordion list renderer. showUnreviewed toggles the 未審核 yellow flag.
  const renderAccordionList = (obs, showUnreviewed = true) => {
    const groupedSpecies = Array.isArray(obs) && obs.length > 0 && typeof obs[0].sightings !== 'undefined'
      ? obs
      : getGroupedSpeciesList(obs);

    if (groupedSpecies.length === 0) {
      return (
        <div className="empty-state">
          <Compass size={48} style={{ color: 'var(--text-muted)' }} />
          <h3>查無觀測紀錄</h3>
          <p>在查詢區間內，eBird 系統尚未在台灣地區登錄任何觀測紀錄。</p>
        </div>
      );
    }

    return (
      <main className="species-list-container">
        {groupedSpecies.map((sp) => {
          const isExpanded = expandedSpecies.has(sp.speciesCode);
          const isUnreviewed = showUnreviewed && !sp.sightings[0].obsReviewed;
          return (
            <div
              className={`species-accordion-item ${isExpanded ? 'is-open' : ''} ${isUnreviewed ? 'unreviewed' : ''}`}
              key={sp.speciesCode}
            >
              <div
                className="species-accordion-header"
                onClick={() => toggleSpecies(sp.speciesCode)}
              >
                <div className="species-primary-info">
                  <div className="species-name-row">
                    <span className="species-chinese">{sp.comNameZh}</span>
                    {sp.comNameEn && sp.comNameEn !== sp.comNameZh && (
                      <span className="species-english">{sp.comNameEn}</span>
                    )}
                  </div>
                  <div className="species-subtitle-row">
                    <span className="species-latest-date">
                      {sp.sightings[0].obsDt}
                    </span>
                    <span className="species-latest-separator">·</span>
                    <span className="species-latest-loc" title={sp.sightings[0].locName}>
                      {sp.sightings[0].locName}
                    </span>
                  </div>
                </div>
                <div className="species-meta-info">
                  {isUnreviewed && (
                    <span className="unreviewed-badge">未審核</span>
                  )}
                  {typeof sp.rarityScore === 'number' && (
                    <span className="sightings-counter-pill" title="近30天通報紀錄數">
                      {sp.rarityScore} 筆
                    </span>
                  )}
                  {typeof sp.rarityScore !== 'number' && (
                    <span className="sightings-counter-pill">
                      {sp.sightings.length} 筆
                    </span>
                  )}
                  <button className="accordion-arrow-btn">
                    {isExpanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
                  </button>
                </div>
              </div>
              {isExpanded && (
                <div className="species-accordion-content">
                  <div className="sightings-table-container">
                    <table className="sightings-table">
                      <thead>
                        <tr>
                          <th>觀測日期</th>
                          <th>發現地點</th>
                          <th style={{ textAlign: 'right' }}>數量</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sp.sightings.map((sighting, sIdx) => {
                          const mapUrl = `https://www.google.com/maps/search/?api=1&query=${sighting.lat},${sighting.lng}`;
                          return (
                            <tr key={`${sighting.subId}-${sIdx}`} className={!showUnreviewed ? '' : (!sighting.obsReviewed ? 'unreviewed-row' : '')}>
                              <td className="td-date">
                                <Calendar size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle', color: 'var(--text-muted)' }} />
                                <span>{sighting.obsDt}</span>
                                {showUnreviewed && !sighting.obsReviewed && <span className="unreviewed-badge">未審核</span>}
                              </td>
                              <td className="td-location" title={sighting.locName}>
                                <a
                                  href={mapUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="location-link"
                                >
                                  <MapPin size={14} className="location-nav-icon" />
                                  <span className="location-name-text">{sighting.locName}</span>
                                </a>
                              </td>
                              <td className="td-count" style={{ textAlign: 'right' }}>
                                <span className="count-tag">
                                  {sighting.howMany ? `${sighting.howMany} 隻` : '出現'}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </main>
    );
  };

  // No API key banner (shared)
  const renderApiKeyBanner = () => (
    <section className="glass-panel warning-overlay-panel">
      <div className="warning-content">
        <AlertTriangle size={54} className="pulse-warning" style={{ color: 'var(--accent-warning)' }} />
        <h2>需要 eBird API 金鑰</h2>
        <p>
          為了載入台灣鳥類觀測清單，請先點擊右上方設定圖示（<Settings size={18} style={{ display: 'inline', verticalAlign: 'middle', margin: '0 0.2rem' }} />）輸入您的個人 eBird API Key。
        </p>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '-0.5rem' }}>
          金鑰申請完全免費，並只會安全地儲存在您目前的瀏覽器本地快取中。
        </p>
      </div>
    </section>
  );

  // NOTABLE tab content (unchanged behaviour)
  const renderNotableContent = () => {
    const groupedSpecies = getGroupedSpeciesList(observations);
    return (
      <>
        <section className="glass-panel">
          <div className="controls-grid">
            <div className="select-container">
              <label htmlFor="date-select" className="select-label">
                觀測區間
                <span className="last-update-text">
                  <span className="pulse-dot"></span>
                  最後更新: {formatTime(lastUpdated)}
                </span>
              </label>
              <select
                id="date-select"
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                className="custom-select"
              >
                <option value={3}>過去 3 天</option>
                <option value={7}>過去 7 天 (預設)</option>
                <option value={14}>過去 14 天</option>
                <option value={30}>過去 30 天</option>
              </select>
            </div>

            <button
              className="btn-primary"
              onClick={handleNotableSearch}
              disabled={loading}
            >
              <RotateCw size={18} className={loading ? "spin" : ""} />
              <span>{loading ? "更新中..." : "更新"}</span>
            </button>
          </div>
        </section>

        <div className="stats-ribbon">
          <div className="stats-text">
            過去{days}天，全台灣觀測到
            <span className="stats-count">{groupedSpecies.length}</span> 種罕見鳥類
          </div>
        </div>

        {loading && observations.length === 0 ? (
          <div className="loading-state">
            <div className="radar-loader">
              <div className="radar-circle"></div>
              <div className="radar-circle"></div>
              <div className="radar-circle"></div>
              <div className="radar-center"></div>
            </div>
            <p>正在搜尋稀有鳥種觀測紀錄...</p>
          </div>
        ) : error ? (
          <div className="error-state">
            <AlertTriangle size={48} style={{ color: '#ef4444' }} />
            <h3>載入失敗</h3>
            <p>{error}</p>
            <button className="btn-primary" onClick={handleNotableSearch}>重試</button>
          </div>
        ) : (
          renderAccordionList(observations, true)
        )}
      </>
    );
  };

  // WORTH tab content
  const renderWorthContent = () => {
    const meta = worthMeta;
    // Split into mainland (本島) / outer islands (外島) by the latest sighting's location name
    const mainList = [];
    const islandList = [];
    worthList.forEach(sp => {
      const latestLoc = sp.sightings[0]?.locName || '';
      if (isIslandLoc(latestLoc)) islandList.push(sp);
      else mainList.push(sp);
    });
    const toggleIsland = (key) => setIslandOpen(prev => ({ ...prev, [key]: !prev[key] }));
    return (
      <>
        <section className="glass-panel">
          <div className="controls-grid">
            <div className="select-container">
              <label className="select-label">
                {meta
                  ? `${meta.threeDaysAgo || meta.twoDaysAgo} ~ ${meta.targetDate}`
                  : '3天前 ~ 今天'}
              </label>
              <span className="last-update-text">
                <span className="pulse-dot"></span>
                最後更新: {formatTime(worthLastUpdated)}
              </span>
            </div>

            <button
              className="btn-primary"
              onClick={handleWorthSearch}
              disabled={worthLoading}
            >
              <RotateCw size={18} className={worthLoading ? "spin" : ""} />
              <span>{worthLoading ? "更新中..." : "更新"}</span>
            </button>
          </div>
        </section>

        {worthLoading && worthList.length === 0 ? (
          <div className="loading-state">
            <div className="radar-loader">
              <div className="radar-circle"></div>
              <div className="radar-circle"></div>
              <div className="radar-circle"></div>
              <div className="radar-center"></div>
            </div>
            <p>正在搜尋值得一看的鳥...</p>
          </div>
        ) : worthError && worthList.length === 0 ? (
          <div className="error-state">
            <AlertTriangle size={48} style={{ color: '#ef4444' }} />
            <h3>載入失敗</h3>
            <p>{worthError}</p>
            <button className="btn-primary" onClick={handleWorthSearch}>重試</button>
          </div>
        ) : (
          <>
            <div className="island-section">
              <button
                className={`island-header ${islandOpen.main ? 'is-open' : ''}`}
                onClick={() => toggleIsland('main')}
              >
                <span className="island-title">本島</span>
                <span className="sightings-counter-pill">{mainList.length} 種</span>
                {islandOpen.main ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
              </button>
              {islandOpen.main && renderAccordionList(mainList, false)}
            </div>

            {islandList.length > 0 && (
              <div className="island-section">
                <button
                  className={`island-header ${islandOpen.island ? 'is-open' : ''}`}
                  onClick={() => toggleIsland('island')}
                >
                  <span className="island-title">外島</span>
                  <span className="sightings-counter-pill">{islandList.length} 種</span>
                  {islandOpen.island ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                </button>
                {islandOpen.island && renderAccordionList(islandList, false)}
              </div>
            )}
          </>
        )}
      </>
    );
  };

  // FIRST-SEEN tab content (reads static first-seen.json, zero API requests)
  const renderFirstSeenContent = () => {
    if (firstSeenLoading) {
      return (
        <div className="loading-state">
          <div className="radar-loader">
            <div className="radar-circle"></div>
            <div className="radar-circle"></div>
            <div className="radar-circle"></div>
            <div className="radar-center"></div>
          </div>
          <p>正在載入今年首見資料...</p>
        </div>
      );
    }
    if (firstSeenError || !firstSeen || !firstSeen.species) {
      return (
        <div className="error-state">
          <AlertTriangle size={48} style={{ color: '#ef4444' }} />
          <h3>載入失敗</h3>
          <p>{firstSeenError || '今年首見資料尚未產生。'}</p>
        </div>
      );
    }

    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth() + 1;
    const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const yest = new Date(now); yest.setDate(yest.getDate() - 1);
    const twoDaysAgo = new Date(now); twoDaysAgo.setDate(twoDaysAgo.getDate() - 2);

    const targetDates = new Set([fmt(yest), fmt(twoDaysAgo)]);
    const monthPrefix = `${year}-${String(month).padStart(2, '0')}`;

    // Build list of species matching current view. Each entry has date + zh/en names.
    const entries = [];
    Object.entries(firstSeen.species).forEach(([code, info]) => {
      const fs = info.firstSeen || '';
      if (firstSeenView === 'recent') {
        if (targetDates.has(fs)) entries.push({ code, fs, info });
      } else {
        if (fs.startsWith(monthPrefix)) entries.push({ code, fs, info });
      }
    });
    // Sort by first-seen date ascending
    entries.sort((a, b) => a.fs.localeCompare(b.fs));

    const lastUpdated = firstSeen.lastUpdated || '—';
    const dataYear = firstSeen.year || '—';
    // short M/D format for the recent-two-days label
    const fmtShort = (dt) => `${dt.getMonth() + 1}/${dt.getDate()}`;
    const recentLabel = `最近兩天(${fmtShort(twoDaysAgo)}、${fmtShort(yest)})本月首見`;

    return (
      <>
        <section className="glass-panel">
          <div className="controls-grid">
            <div className="select-container">
              <label className="select-label">
                {firstSeenView === 'month' ? `${month}月份所觀察到的鳥種是今年以來首次見到：` : recentLabel}
                <span className="last-update-text">
                  <span className="pulse-dot"></span>
                  資料更新至: {lastUpdated}（{dataYear} 年度）
                </span>
              </label>
              <select
                value={firstSeenView}
                onChange={(e) => setFirstSeenView(e.target.value)}
                className="custom-select"
              >
                <option value="month">{month}月份今年首見</option>
                <option value="recent">{recentLabel}</option>
              </select>
            </div>
          </div>
        </section>

        <div className="stats-ribbon">
          <div className="stats-text">
            共 <span className="stats-count">{entries.length}</span> 種今年首見鳥類
          </div>
        </div>

        {entries.length === 0 ? (
          <div className="empty-state">
            <Eye size={48} style={{ color: 'var(--text-muted)' }} />
            <h3>{firstSeenView === 'month' ? '本月尚無今年首見鳥種' : '最近兩日尚無本月首見鳥種'}</h3>
            <p>此時間區間內沒有符合條件的紀錄。</p>
          </div>
        ) : (
          <main className="species-list-container">
            {entries.map(({ code, fs, info }) => {
              const isExpanded = expandedSpecies.has(code);
              return (
                <div
                  className={`species-accordion-item ${isExpanded ? 'is-open' : ''}`}
                  key={code}
                >
                  <div
                    className="species-accordion-header"
                    onClick={() => toggleSpecies(code)}
                  >
                    <div className="species-primary-info">
                      <div className="species-name-row">
                        <span className="species-chinese">{info.comNameZh || code}</span>
                        {info.comNameEn && info.comNameEn !== info.comNameZh && (
                          <span className="species-english">{info.comNameEn}</span>
                        )}
                      </div>
                      <div className="species-subtitle-row">
                        <span className="species-latest-date">{fs}</span>
                        <span className="species-latest-separator">·</span>
                        <span className="species-latest-loc">今年首見</span>
                      </div>
                    </div>
                    <div className="species-meta-info">
                      <span className="sightings-counter-pill">首見</span>
                      <button className="accordion-arrow-btn">
                        {isExpanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
                      </button>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="species-accordion-content">
                      <div className="sightings-table-container">
                        <table className="sightings-table">
                          <thead>
                            <tr>
                              <th>今年首見日期</th>
                              <th>首見年度</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr>
                              <td className="td-date">
                                <Calendar size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle', color: 'var(--text-muted)' }} />
                                <span>{fs}</span>
                              </td>
                              <td className="td-location">
                                <span>{dataYear} 年</span>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                        <p className="static-hint" style={{ padding: '0.5rem 0.75rem 0.75rem' }}>
                          代表今年首度被觀察到的日期（資料由每日 historic 累積）。
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </main>
        )}
      </>
    );
  };

  const renderContent = () => {
    if (activeTab === 'firstSeen') {
      // 今年首見 tab：純讀靜態資料，不需要 API key
      return renderFirstSeenContent();
    }
    if (!apiKey) {
      return renderApiKeyBanner();
    }
    return activeTab === 'worth' ? renderWorthContent() : renderNotableContent();
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="glass-panel app-header">
        <div className="brand-wrapper">
          <div className="logo-icon">
            <span>e</span>
          </div>
          <div className="title-section">
            <div className="title-row-container">
              <h1>eBird台灣鳥類查詢</h1>
            </div>
            <p className="header-credit">Sam Tsai 製作，歡迎公益使用</p>
          </div>
        </div>

        <div className="header-actions">
          <button
            className={`btn-icon ${showSettings ? 'active-btn' : ''}`}
            onClick={() => setShowSettings(!showSettings)}
            title="設定 API Key"
          >
            <Settings size={20} />
          </button>
          <button
            className="btn-icon"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="切換主題"
          >
            {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
          </button>
        </div>
      </header>

      {/* Tab Bar */}
      <nav className="tab-bar">
        <button
          className={`tab-btn ${activeTab === 'notable' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('notable')}
        >
          <Compass size={16} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} />
          稀有種快報
        </button>
        <button
          className={`tab-btn ${activeTab === 'worth' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('worth')}
        >
          <Eye size={16} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} />
          值得一看
        </button>
        <button
          className={`tab-btn ${activeTab === 'firstSeen' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('firstSeen')}
        >
          <Eye size={16} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} />
          今年首見
        </button>
      </nav>

      {/* Settings Panel */}
      {showSettings && (
        <section className="glass-panel settings-panel">
          <div className="settings-section">
            <div className="panel-title-row">
              <Key size={18} style={{ color: 'var(--accent-secondary)' }} />
              <h3>eBird API 金鑰設定</h3>
            </div>
            <p className="panel-desc">
              請輸入您的個人 eBird API Key。您可以在{' '}
              <a
                href="https://ebird.org/api/keygen"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-link"
              >
                eBird 官方網站申請金鑰
              </a>
              （需註冊 eBird 免費帳號）。
            </p>
            <div className="api-key-input-group">
              <input
                type="password"
                placeholder="請貼上您的 eBird API Key..."
                value={inputApiKey}
                onChange={(e) => setInputApiKey(e.target.value)}
                className="api-key-input"
              />
              <button
                className="btn-save-api-key"
                onClick={() => saveApiKey()}
              >
                儲存金鑰
              </button>
              {apiKey && (
                <button
                  className="btn-clear-api-key"
                  onClick={clearApiKey}
                  title="從本地快取移除金鑰並清空資料"
                >
                  清除金鑰
                </button>
              )}
            </div>
            {apiKeySavedMsg && (
              <div className="save-success-msg">
                <CheckCircle size={14} style={{ color: 'var(--accent-primary)' }} />
                <span>{apiKeySavedMsg}</span>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Tab Content */}
      {renderContent()}

      {/* Inline styles */}
      <style>{`
        .spin {
          animation: spin-anim 1s linear infinite;
        }
        @keyframes spin-anim {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .pulse-warning {
          animation: warning-pulse 2s infinite;
        }
        @keyframes warning-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.08); opacity: 0.8; }
        }
        .static-hint {
          font-size: 0.78rem;
          color: var(--text-muted);
          margin-top: 0.25rem;
        }
        .worth-fallback-note {
          font-size: 0.78rem;
          color: var(--text-muted);
        }
      `}</style>
    </div>
  );
}
