import React, { useState, useEffect, useCallback } from 'react';
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
  Key
} from 'lucide-react';

const STORAGE_API_KEY_KEY = "taiwan_birds_ebird_api_key";
const STORAGE_THEME_KEY = "taiwan_birds_theme";
const STORAGE_REGION_STATS_KEY = "taiwan_birds_region_stats";
const STORAGE_REGION_STATS_TIME_KEY = "taiwan_birds_region_stats_time";

// Per-tab storage keys
const TAB_KEYS = {
  recent: {
    obs: "taiwan_birds_recent_obs_cache",
    days: "taiwan_birds_recent_days_setting",
    time: "taiwan_birds_recent_last_fetch_time",
  },
  notable: {
    obs: "taiwan_birds_notable_obs_cache",
    days: "taiwan_birds_notable_days_setting",
    time: "taiwan_birds_notable_last_fetch_time",
  },
};

const TAB_LABELS = {
  recent: "最近觀察",
  notable: "稀有種快報",
};

export default function App() {
  const [activeTab, setActiveTab] = useState('recent');

  // Load API Key from localStorage
  const [apiKey, setApiKey] = useState(() => {
    return localStorage.getItem(STORAGE_API_KEY_KEY) || '';
  });
  const [inputApiKey, setInputApiKey] = useState(apiKey);
  const [apiKeySavedMsg, setApiKeySavedMsg] = useState('');

  // Per-tab state
  const [tabState, setTabState] = useState({
    recent: { observations: [], loading: false, error: null, days: 7, lastUpdated: null, loaded: false },
    notable: { observations: [], loading: false, error: null, days: 7, lastUpdated: null, loaded: false },
  });

  // Month species count
  const [monthSpeciesCount, setMonthSpeciesCount] = useState(null);

  const [theme, setTheme] = useState(() => {
    return localStorage.getItem(STORAGE_THEME_KEY) || 'dark';
  });

  // Accordion state
  const [expandedSpecies, setExpandedSpecies] = useState(new Set());
  const [showSettings, setShowSettings] = useState(false);

  const updateTab = (tab, patch) => {
    setTabState(prev => ({
      ...prev,
      [tab]: { ...prev[tab], ...patch },
    }));
  };

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

  // Fetch this month's unique species count
  const fetchMonthSpecies = useCallback(async (activeKey = apiKey) => {
    if (!activeKey) return;
    try {
      const headers = { 'x-ebirdapitoken': activeKey };
      const now = new Date();
      const backDays = Math.min(now.getDate(), 30);
      const url = `https://api.ebird.org/v2/data/obs/TW/recent?back=${backDays}&detail=simple`;
      const res = await fetch(url, { headers, cache: 'no-store' });
      const data = res.ok ? await res.json() : [];
      const count = Array.isArray(data) ? data.length : 0;
      setMonthSpeciesCount(count);
      localStorage.setItem(STORAGE_REGION_STATS_KEY, JSON.stringify({ monthSpecies: count }));
      localStorage.setItem(STORAGE_REGION_STATS_TIME_KEY, new Date().toISOString());
    } catch (err) {
      console.error("Fetch month species failed:", err);
    }
  }, [apiKey]);

  // Main API Fetch function — works for both tabs
  const fetchObservations = useCallback(async (tab, selectedDays, activeKey = apiKey) => {
    if (!activeKey) {
      updateTab(tab, { error: "請先設定您的 eBird API Key。", loading: false });
      return;
    }

    updateTab(tab, { loading: true, error: null });

    try {
      const notable = tab === 'notable';
      const basePath = notable
        ? `https://api.ebird.org/v2/data/obs/TW/recent/notable`
        : `https://api.ebird.org/v2/data/obs/TW/recent`;
      const urlEn = `${basePath}?back=${selectedDays}&detail=simple`;
      const urlZh = `${basePath}?back=${selectedDays}&detail=simple&sppLocale=zh`;
      const headers = { 'x-ebirdapitoken': activeKey };

      const [resEn, resZh] = await Promise.all([
        fetch(urlEn, { headers, cache: 'no-store' }),
        fetch(urlZh, { headers, cache: 'no-store' })
      ]);

      if (!resEn.ok || !resZh.ok) {
        throw new Error(`eBird API error: ${resEn.status} / ${resZh.status}`);
      }

      const dataEn = await resEn.ok ? await resEn.json() : [];
      const dataZh = await resZh.ok ? await resZh.json() : [];

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
      const keys = TAB_KEYS[tab];

      updateTab(tab, {
        observations: mergedList,
        loading: false,
        error: null,
        lastUpdated: nowStr,
        loaded: true,
      });

      localStorage.setItem(keys.obs, JSON.stringify(mergedList));
      localStorage.setItem(keys.time, nowStr);
      localStorage.setItem(keys.days, selectedDays.toString());

    } catch (err) {
      console.error(`Fetch ${tab} failed:`, err);
      updateTab(tab, {
        loading: false,
        error: "無法取得觀測資料，請確認您的 eBird API Key 是否正確且有效。",
      });
    }
  }, [apiKey]);

  // Auto-fetch month species count (once per day cache)
  useEffect(() => {
    if (!apiKey) return;
    const cachedStats = localStorage.getItem(STORAGE_REGION_STATS_KEY);
    const cachedTime = localStorage.getItem(STORAGE_REGION_STATS_TIME_KEY);
    if (cachedStats && cachedTime) {
      const parsedTime = new Date(cachedTime);
      const today = new Date();
      if (
        parsedTime.getFullYear() === today.getFullYear() &&
        parsedTime.getMonth() === today.getMonth() &&
        parsedTime.getDate() === today.getDate()
      ) {
        try {
          const obj = JSON.parse(cachedStats);
          setMonthSpeciesCount(obj.monthSpecies ?? null);
          return;
        } catch { /* fall through */ }
      }
    }
    fetchMonthSpecies(apiKey);
  }, [apiKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fetch active tab on mount or when apiKey / activeTab changes
  useEffect(() => {
    if (!apiKey) return;

    const tab = activeTab;
    const current = tabState[tab];
    const keys = TAB_KEYS[tab];

    const cachedObs = localStorage.getItem(keys.obs);
    const cachedTime = localStorage.getItem(keys.time);
    const cachedDays = localStorage.getItem(keys.days);

    let needsFetch = true;

    if (cachedObs && cachedTime && cachedDays === current.days.toString()) {
      const parsedTime = new Date(cachedTime);
      const today = new Date();
      if (
        parsedTime.getFullYear() === today.getFullYear() &&
        parsedTime.getMonth() === today.getMonth() &&
        parsedTime.getDate() === today.getDate()
      ) {
        const parsedObs = JSON.parse(cachedObs);
        updateTab(tab, {
          observations: parsedObs,
          lastUpdated: cachedTime,
          loaded: true,
        });
        needsFetch = false;
      }
    }

    if (needsFetch && !current.loading) {
      fetchObservations(tab, current.days, apiKey);
    }
  }, [apiKey, activeTab]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearchClick = () => {
    if (!apiKey) {
      setShowSettings(true);
      return;
    }
    fetchObservations(activeTab, tabState[activeTab].days, apiKey);
  };

  const saveApiKey = (keyToSave = inputApiKey) => {
    const trimmed = keyToSave.trim();
    setApiKey(trimmed);
    localStorage.setItem(STORAGE_API_KEY_KEY, trimmed);
    setApiKeySavedMsg("API Key 儲存成功！正在獲取觀測資料...");
    setTimeout(() => setApiKeySavedMsg(''), 3000);
    if (trimmed) {
      fetchObservations('recent', tabState.recent.days, trimmed);
      fetchObservations('notable', tabState.notable.days, trimmed);
      fetchMonthSpecies(trimmed);
    }
  };

  const clearApiKey = () => {
    setApiKey('');
    setInputApiKey('');
    setTabState({
      recent: { observations: [], loading: false, error: null, days: 7, lastUpdated: null, loaded: false },
      notable: { observations: [], loading: false, error: null, days: 7, lastUpdated: null, loaded: false },
    });
    setMonthSpeciesCount(null);
    localStorage.removeItem(STORAGE_API_KEY_KEY);
    localStorage.removeItem(STORAGE_REGION_STATS_KEY);
    localStorage.removeItem(STORAGE_REGION_STATS_TIME_KEY);
    Object.values(TAB_KEYS).forEach(keys => {
      localStorage.removeItem(keys.obs);
      localStorage.removeItem(keys.time);
      localStorage.removeItem(keys.days);
    });
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
  const getGroupedSpeciesList = (observations) => {
    const grouped = {};
    observations.forEach(obs => {
      const code = obs.speciesCode;
      if (!grouped[code]) {
        grouped[code] = {
          speciesCode: obs.speciesCode,
          comNameZh: obs.comNameZh,
          comNameEn: obs.comNameEn,
          sciName: obs.sciName,
          sightings: []
        };
      }
      grouped[code].sightings.push(obs);
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

  const cleanLocationName = (locName) => {
    if (!locName) return '';
    let clean = locName.replace(/\s*\([\d.-]+,\s*[\d.-]+\)/g, '');
    clean = clean.replace(/\s*\([\d.-]+,[\d.-]+\)/g, '');
    const hasChinese = /[\u4e00-\u9fa5]/.test(clean);
    if (!hasChinese) return clean.trim();
    const parenMatches = clean.match(/\(([^)]*[\u4e00-\u9fa5][^)]*)\)/);
    if (parenMatches && parenMatches[1]) return parenMatches[1].trim();
    clean = clean.replace(/\([^)]*[a-zA-Z]{2,}[^)]*\)/g, '');
    const parts = clean.split(/\s*(?:\/|--)\s*/);
    for (let part of parts) {
      if (/[\u4e00-\u9fa5]/.test(part)) {
        const chineseMatch = part.match(/[\u4e00-\u9fa5]+/g);
        if (chineseMatch) return chineseMatch.join('');
        return part.trim();
      }
    }
    const chineseBlocks = clean.match(/[\u4e00-\u9fa5]+/g);
    if (chineseBlocks) return chineseBlocks.join('');
    return clean.trim();
  };

  const renderAccordionList = (observations) => {
    const groupedSpecies = getGroupedSpeciesList(observations);

    if (groupedSpecies.length === 0) {
      return (
        <div className="empty-state">
          <Compass size={48} style={{ color: 'var(--text-muted)' }} />
          <h3>查無觀測紀錄</h3>
          <p>在過去 {tabState[activeTab].days} 天內，eBird 系統尚未在台灣地區登錄任何觀測紀錄。</p>
        </div>
      );
    }

    return (
      <main className="species-list-container">
        {groupedSpecies.map((sp) => {
          const isExpanded = expandedSpecies.has(sp.speciesCode);
          return (
            <div
              className={`species-accordion-item ${isExpanded ? 'is-open' : ''}`}
              key={sp.speciesCode}
            >
              <div
                className="species-accordion-header"
                onClick={() => toggleSpecies(sp.speciesCode)}
              >
                <div className="species-primary-info">
                  <div className="species-name-row">
                    <span className="species-chinese">{sp.comNameZh}</span>
                    <span className="species-english">{sp.comNameEn}</span>
                    <span className="species-scientific">({sp.sciName})</span>
                  </div>
                  <div className="species-subtitle-row">
                    <span className="species-latest-date">
                      {sp.sightings[0].obsDt}
                    </span>
                    <span className="species-latest-separator">·</span>
                    <span className="species-latest-loc" title={sp.sightings[0].locName}>
                      {cleanLocationName(sp.sightings[0].locName)}
                    </span>
                  </div>
                </div>
                <div className="species-meta-info">
                  <span className="sightings-counter-pill">
                    {sp.sightings.length} 次觀測點
                  </span>
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
                            <tr key={`${sighting.subId}-${sIdx}`}>
                              <td className="td-date">
                                <Calendar size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle', color: 'var(--text-muted)' }} />
                                <span>{sighting.obsDt}</span>
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

  const renderTabContent = () => {
    const current = tabState[activeTab];

    if (!apiKey) {
      return (
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
    }

    const groupedSpecies = getGroupedSpeciesList(current.observations);
    const isNotable = activeTab === 'notable';
    const statsLabel = isNotable ? '罕見鳥類' : '鳥種';

    return (
      <>
        {/* Control Panel with last update inline */}
        <section className="glass-panel">
          <div className="controls-grid">
            <div className="select-container">
              <label htmlFor="date-select" className="select-label">觀測日期區間</label>
              <select
                id="date-select"
                value={current.days}
                onChange={(e) => updateTab(activeTab, { days: Number(e.target.value) })}
                className="custom-select"
              >
                <option value={1}>過去 1 天</option>
                <option value={3}>過去 3 天</option>
                <option value={5}>過去 5 天</option>
                <option value={7}>過去 7 天 (預設)</option>
                <option value={10}>過去 10 天</option>
                <option value={14}>過去 14 天</option>
                <option value={21}>過去 21 天</option>
                <option value={30}>過去 30 天</option>
              </select>
            </div>

            <button
              className="btn-primary"
              onClick={handleSearchClick}
              disabled={current.loading}
            >
              <RotateCw size={18} className={current.loading ? "spin" : ""} />
              <span>{current.loading ? "更新中..." : "更新鳥類名單"}</span>
            </button>
          </div>
          <div className="last-update-inline">
            <span className="pulse-dot"></span>
            <span>最後更新: {formatTime(current.lastUpdated)}</span>
          </div>
        </section>

        {/* Info Section */}
        <div className="stats-ribbon">
          <div className="stats-text">
            在過去 <span className="stats-count">{current.days}</span> 天內，觀測到
            <span className="stats-count">{groupedSpecies.length}</span> 種{statsLabel}，本月觀察鳥種共計
            <span className="stats-count">{monthSpeciesCount ?? '—'}</span> 種
          </div>
        </div>

        {/* Accordion List */}
        {current.loading && current.observations.length === 0 ? (
          <div className="loading-state">
            <div className="radar-loader">
              <div className="radar-circle"></div>
              <div className="radar-circle"></div>
              <div className="radar-circle"></div>
              <div className="radar-center"></div>
            </div>
            <p>正在搜尋並整合台灣地區的最新鳥況紀錄...</p>
          </div>
        ) : current.error ? (
          <div className="error-state">
            <AlertTriangle size={48} style={{ color: '#ef4444' }} />
            <h3>載入失敗</h3>
            <p>{current.error}</p>
            <button className="btn-primary" onClick={handleSearchClick}>重試</button>
          </div>
        ) : (
          renderAccordionList(current.observations)
        )}
      </>
    );
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

      {/* Tab Bar */}
      <div className="tab-bar">
        {Object.entries(TAB_LABELS).map(([key, label]) => (
          <button
            key={key}
            className={`tab-btn ${activeTab === key ? 'tab-active' : ''}`}
            onClick={() => setActiveTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {renderTabContent()}

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
      `}</style>
    </div>
  );
}