// ===== 다낭 여행 대시보드 공용 스크립트 =====
// V1 - 기능 복구 / GitHub Pages / iPhone Safari 대응

// ------------------------------------------------------------
// 공통: fetch 타임아웃
// ------------------------------------------------------------
function fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  return fetch(url, {
    ...options,
    signal: controller.signal,
    cache: 'no-store',
  }).finally(() => clearTimeout(timer));
}

// ------------------------------------------------------------
// 서비스 워커 등록 (PWA)
// ------------------------------------------------------------
if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    const inPages = location.pathname.includes('/pages/');
    const swUrl = inPages ? '../sw.js' : './sw.js';
    const swScope = inPages ? '../' : './';

    try {
      const registration = await navigator.serviceWorker.register(swUrl, {
        scope: swScope,
        updateViaCache: 'none',
      });

      // 페이지가 열릴 때마다 새 sw.js 확인
      await registration.update();

      // 이미 새 Service Worker가 대기 중이면 즉시 활성화
      if (registration.waiting) {
        registration.waiting.postMessage({ type: 'SKIP_WAITING' });
      }

      // 설치 중인 새 Service Worker도 즉시 활성화하도록 처리
      registration.addEventListener('updatefound', () => {
        const newWorker = registration.installing;
        if (!newWorker) return;

        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
            newWorker.postMessage({ type: 'SKIP_WAITING' });
          }
        });
      });

      // 새 SW가 적용되었을 때 한 번만 새로고침
      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (refreshing) return;
        refreshing = true;
        window.location.reload();
      });
    } catch (error) {
      console.warn('Service Worker 등록 실패:', error);
    }
  });
}

// ------------------------------------------------------------
// PWA 설치 배너
// ------------------------------------------------------------
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredPrompt = event;

  const banner = document.getElementById('installBanner');
  if (banner) {
    banner.classList.remove('d-none');
    banner.style.display = 'flex';
  }
});

function installApp() {
  const banner = document.getElementById('installBanner');
  if (!deferredPrompt) return;

  deferredPrompt.prompt();

  deferredPrompt.userChoice
    .catch(() => {})
    .finally(() => {
      deferredPrompt = null;
      if (banner) {
        banner.classList.add('d-none');
        banner.style.display = 'none';
      }
    });
}

window.addEventListener('appinstalled', () => {
  const banner = document.getElementById('installBanner');
  if (banner) {
    banner.classList.add('d-none');
    banner.style.display = 'none';
  }
});

// ------------------------------------------------------------
// 토스트 알림
// ------------------------------------------------------------
function showToast(message) {
  let toast = document.querySelector('.toast');

  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);
  }

  toast.textContent = message;
  toast.classList.add('show');

  clearTimeout(window.__danangToastTimer);
  window.__danangToastTimer = setTimeout(() => {
    toast.classList.remove('show');
  }, 1800);
}

// ------------------------------------------------------------
// 주소 복사
// ------------------------------------------------------------
function copyAddress(text, button) {
  const done = () => {
    showToast('주소를 복사했어요 📋');

    if (button) {
      const original = button.innerHTML;
      button.innerHTML = '✓<span>복사됨</span>';
      setTimeout(() => {
        button.innerHTML = original;
      }, 1200);
    }
  };

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => {
      fallbackCopy(text, done);
    });
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, callback) {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';

  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  try {
    document.execCommand('copy');
    callback();
  } catch (error) {
    showToast('주소 복사에 실패했어요');
  } finally {
    document.body.removeChild(textarea);
  }
}

// ------------------------------------------------------------
// Google Maps
// ------------------------------------------------------------
function openMaps(address, lat, lng) {
  let url;

  if (
    typeof lat === 'number' &&
    typeof lng === 'number' &&
    Number.isFinite(lat) &&
    Number.isFinite(lng)
  ) {
    url = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
  } else {
    url = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address || '')}`;
  }

  window.open(url, '_blank', 'noopener,noreferrer');
}

// ------------------------------------------------------------
// Grab 앱 열기 (앱이 없으면 웹사이트)
// ------------------------------------------------------------
function openGrab() {
  const fallback = 'https://www.grab.com/vn/en/';
  let didHide = false;

  const onVisibilityChange = () => {
    if (document.hidden) didHide = true;
  };

  document.addEventListener('visibilitychange', onVisibilityChange, { once: true });

  try {
    window.location.href = 'grab://open';
  } catch (error) {
    // 웹 fallback은 아래 timeout에서 처리
  }

  setTimeout(() => {
    document.removeEventListener('visibilitychange', onVisibilityChange);

    if (!didHide && !document.hidden) {
      window.open(fallback, '_blank', 'noopener,noreferrer');
    }
  }, 900);
}

// ------------------------------------------------------------
// 실시간 환율 (KRW → VND)
// ------------------------------------------------------------
const FX_CACHE_KEY = 'danang_fx_cache_v2';

async function loadExchangeRate() {
  const rateEl = document.getElementById('fxRate');
  const updatedEl = document.getElementById('fxUpdated');
  const krwInput = document.getElementById('fxKrw');
  const vndInput = document.getElementById('fxVnd');

  if (!krwInput || !vndInput || !rateEl) return;

  let rate = null;
  let isLive = false;

  try {
    const response = await fetchWithTimeout(
      'https://open.er-api.com/v6/latest/KRW',
      { method: 'GET' },
      10000
    );

    if (!response.ok) {
      throw new Error(`Exchange API HTTP ${response.status}`);
    }

    const data = await response.json();

    if (data?.result === 'success' && Number.isFinite(data?.rates?.VND)) {
      rate = Number(data.rates.VND);
      isLive = true;

      localStorage.setItem(
        FX_CACHE_KEY,
        JSON.stringify({
          rate,
          ts: Date.now(),
        })
      );
    } else if (Number.isFinite(data?.rates?.VND)) {
      rate = Number(data.rates.VND);
      isLive = true;
    }
  } catch (error) {
    console.warn('실시간 환율 조회 실패:', error);
  }

  // 네트워크/API 실패 시 마지막으로 저장된 환율 사용
  if (!rate) {
    try {
      const cached = localStorage.getItem(FX_CACHE_KEY);

      if (cached) {
        const parsed = JSON.parse(cached);
        if (Number.isFinite(parsed?.rate)) {
          rate = Number(parsed.rate);
        }
      }
    } catch (error) {
      console.warn('저장된 환율 읽기 실패:', error);
    }
  }

  if (!rate) {
    rateEl.textContent = '환율을 불러오지 못했어요.';
    if (updatedEl) {
      updatedEl.textContent = '인터넷 연결을 확인해주세요.';
    }
    return;
  }

  window.__krwToVnd = rate;

  rateEl.textContent = `1 KRW ≈ ${rate.toFixed(2)} VND`;

  if (updatedEl) {
    const label = isLive ? '실시간 업데이트' : '마지막 저장값';
    updatedEl.textContent =
      `${label}: ${new Date().toLocaleTimeString('ko-KR', {
        hour: '2-digit',
        minute: '2-digit',
      })}`;
  }

  // 기존 이벤트가 중복 등록되지 않도록 clone 후 교체
  const newKrwInput = krwInput.cloneNode(true);
  const newVndInput = vndInput.cloneNode(true);

  krwInput.replaceWith(newKrwInput);
  vndInput.replaceWith(newVndInput);

  const formatNumber = (value) => {
    const digits = String(value || '').replace(/[^\d]/g, '');
    return digits ? Number(digits).toLocaleString('ko-KR') : '';
  };

  newKrwInput.addEventListener('input', () => {
    const value = Number(newKrwInput.value.replace(/[^\d]/g, '')) || 0;
    newKrwInput.value = value ? value.toLocaleString('ko-KR') : '';
    newVndInput.value = value
      ? Math.round(value * rate).toLocaleString('ko-KR')
      : '';
  });

  newVndInput.addEventListener('input', () => {
    const value = Number(newVndInput.value.replace(/[^\d]/g, '')) || 0;
    newVndInput.value = value ? value.toLocaleString('ko-KR') : '';
    newKrwInput.value = value
      ? Math.round(value / rate).toLocaleString('ko-KR')
      : '';
  });

  // 기존 입력값이 있으면 유지, 없으면 기본 100,000원
  const currentKrw = Number(newKrwInput.value.replace(/[^\d]/g, '')) || 0;

  if (currentKrw > 0) {
    newKrwInput.value = formatNumber(currentKrw);
    newVndInput.value = Math.round(currentKrw * rate).toLocaleString('ko-KR');
  } else {
    newKrwInput.value = '100,000';
    newKrwInput.dispatchEvent(new Event('input'));
  }
}

function swapFx() {
  const krw = document.getElementById('fxKrw');
  const vnd = document.getElementById('fxVnd');

  if (!krw || !vnd) return;

  const oldKrw = krw.value;
  krw.value = vnd.value;
  vnd.value = oldKrw;

  krw.dispatchEvent(new Event('input'));
}

// ------------------------------------------------------------
// 다낭 날씨 (Open-Meteo)
// ------------------------------------------------------------
const WMO = {
  0: ['☀️', '맑음'],
  1: ['🌤️', '대체로 맑음'],
  2: ['⛅', '구름 조금'],
  3: ['☁️', '흐림'],
  45: ['🌫️', '안개'],
  48: ['🌫️', '안개'],
  51: ['🌦️', '이슬비'],
  53: ['🌦️', '이슬비'],
  55: ['🌧️', '이슬비'],
  56: ['🌧️', '어는 이슬비'],
  57: ['🌧️', '강한 어는 이슬비'],
  61: ['🌧️', '약한 비'],
  63: ['🌧️', '비'],
  65: ['🌧️', '강한 비'],
  66: ['🌧️', '어는 비'],
  67: ['🌧️', '강한 어는 비'],
  71: ['🌨️', '약한 눈'],
  73: ['🌨️', '눈'],
  75: ['❄️', '강한 눈'],
  77: ['🌨️', '눈 알갱이'],
  80: ['🌦️', '소나기'],
  81: ['🌧️', '소나기'],
  82: ['⛈️', '강한 소나기'],
  85: ['🌨️', '눈 소나기'],
  86: ['❄️', '강한 눈 소나기'],
  95: ['⛈️', '뇌우'],
  96: ['⛈️', '우박 동반 뇌우'],
  99: ['⛈️', '강한 우박 동반 뇌우'],
};

function wIcon(code) {
  return (WMO[code] || ['🌡️', '-'])[0];
}

function wDesc(code) {
  return (WMO[code] || ['🌡️', '-'])[1];
}

const WEATHER_CACHE_KEY = 'danang_weather_cache_v2';

async function loadWeather() {
  const tempEl = document.getElementById('wTemp');
  const descEl = document.getElementById('wDesc');
  const iconEl = document.getElementById('wIcon');
  const daysEl = document.getElementById('wDays');

  if (!tempEl) return;

  const url =
    'https://api.open-meteo.com/v1/forecast' +
    '?latitude=16.0544' +
    '&longitude=108.2022' +
    '&current=temperature_2m,weather_code' +
    '&daily=temperature_2m_max,temperature_2m_min,weather_code' +
    '&timezone=Asia%2FBangkok' +
    '&forecast_days=5';

  let data = null;
  let isLive = false;

  try {
    const response = await fetchWithTimeout(url, { method: 'GET' }, 10000);

    if (!response.ok) {
      throw new Error(`Weather API HTTP ${response.status}`);
    }

    data = await response.json();

    if (data?.current && data?.daily) {
      isLive = true;
      localStorage.setItem(
        WEATHER_CACHE_KEY,
        JSON.stringify({
          data,
          ts: Date.now(),
        })
      );
    } else {
      data = null;
    }
  } catch (error) {
    console.warn('실시간 날씨 조회 실패:', error);
  }

  // 실패 시 마지막 날씨 사용
  if (!data) {
    try {
      const cached = localStorage.getItem(WEATHER_CACHE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed?.data?.current && parsed?.data?.daily) {
          data = parsed.data;
        }
      }
    } catch (error) {
      console.warn('저장된 날씨 읽기 실패:', error);
    }
  }

  if (!data?.current) {
    tempEl.textContent = '-';
    if (descEl) descEl.textContent = '날씨 정보를 불러오지 못했어요';
    if (iconEl) iconEl.textContent = '🌤️';
    return;
  }

  const currentTemp = Number(data.current.temperature_2m);
  const currentCode = Number(data.current.weather_code);

  tempEl.textContent = `${Math.round(currentTemp)}°C`;

  if (descEl) {
    descEl.textContent = `다낭 · ${wDesc(currentCode)}`;
  }

  if (iconEl) {
    iconEl.textContent = wIcon(currentCode);
  }

  if (daysEl && data.daily?.time) {
    const count = Math.min(5, data.daily.time.length);

    daysEl.innerHTML = Array.from({ length: count }, (_, i) => {
      const dateText = data.daily.time[i];
      const date = new Date(`${dateText}T12:00:00`);
      const label =
        i === 0
          ? '오늘'
          : date.toLocaleDateString('ko-KR', { weekday: 'short' });

      const max = Math.round(Number(data.daily.temperature_2m_max[i]));
      const min = Math.round(Number(data.daily.temperature_2m_min[i]));
      const code = Number(data.daily.weather_code[i]);

      return `
        <div class="wday">
          <div class="d">${label}</div>
          <div class="i">${wIcon(code)}</div>
          ${max}° / ${min}°
        </div>
      `;
    }).join('');
  }

  if (isLive && descEl) {
    // 실시간 조회 성공 여부는 텍스트를 과하게 바꾸지 않고 유지
  }
}

// ------------------------------------------------------------
// 숙소 + 주변 맛집 지도 (Leaflet / OpenStreetMap)
// ------------------------------------------------------------
function initStayMap(hotel, restaurants = []) {
  const mapEl = document.getElementById('stayMap');
  const listEl = document.getElementById('foodList');

  if (!mapEl) return;

  if (typeof L === 'undefined') {
    console.error('Leaflet이 로드되지 않았습니다.');
    if (listEl) {
      listEl.innerHTML =
        '<div class="text-muted p-3">지도를 불러오지 못했어요. 잠시 후 다시 시도해주세요.</div>';
    }
    return;
  }

  // 같은 페이지에서 함수가 두 번 호출되어도 지도 중복 생성 방지
  if (mapEl._leaflet_id) {
    try {
      mapEl._leaflet_id = null;
    } catch (error) {}
  }

  const hotelLat = Number(hotel?.lat);
  const hotelLng = Number(hotel?.lng);

  if (!Number.isFinite(hotelLat) || !Number.isFinite(hotelLng)) {
    console.error('숙소 좌표가 올바르지 않습니다:', hotel);
    return;
  }

  const map = L.map(mapEl, {
    zoomControl: true,
    attributionControl: true,
  }).setView([hotelLat, hotelLng], 15);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    crossOrigin: true,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);

  const hotelIcon = L.divIcon({
    className: '',
    html: `
      <div style="
        width:30px;
        height:30px;
        border-radius:50% 50% 50% 0;
        background:#0B4F6C;
        transform:rotate(-45deg);
        display:flex;
        align-items:center;
        justify-content:center;
        box-shadow:0 3px 8px rgba(0,0,0,0.3);
      ">
        <span style="transform:rotate(45deg);font-size:14px;">🏨</span>
      </div>
    `,
    iconSize: [30, 30],
    iconAnchor: [15, 30],
  });

  L.marker([hotelLat, hotelLng], { icon: hotelIcon })
    .addTo(map)
    .bindPopup(`
      <div class="popup-title">🏨 ${escapeHtml(hotel.name || '숙소')}</div>
      <div class="popup-addr">${escapeHtml(hotel.address || '')}</div>
    `);

  const markers = [[hotelLat, hotelLng]];

  const foodIcon = (number) =>
    L.divIcon({
      className: '',
      html: `
        <div style="
          width:26px;
          height:26px;
          border-radius:50% 50% 50% 0;
          background:#FF6B4A;
          transform:rotate(-45deg);
          display:flex;
          align-items:center;
          justify-content:center;
          box-shadow:0 3px 8px rgba(0,0,0,0.3);
        ">
          <span style="
            transform:rotate(45deg);
            font-size:11px;
            color:#fff;
            font-weight:700;
          ">${number}</span>
        </div>
      `,
      iconSize: [26, 26],
      iconAnchor: [13, 26],
    });

  const distKm = (lat1, lng1, lat2, lng2) => {
    const R = 6371;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLng = ((lng2 - lng1) * Math.PI) / 180;

    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos((lat1 * Math.PI) / 180) *
        Math.cos((lat2 * Math.PI) / 180) *
        Math.sin(dLng / 2) ** 2;

    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  };

  const distLabel = (km) =>
    km < 1 ? `${Math.round(km * 1000)}m` : `${km.toFixed(1)}km`;

  const safeRestaurants = Array.isArray(restaurants) ? restaurants : [];

  const listHtml = safeRestaurants
    .map((restaurant, index) => {
      const lat = Number(restaurant.lat);
      const lng = Number(restaurant.lng);

      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return '';

      markers.push([lat, lng]);

      const mapsLink =
        `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;

      const dishIcon = restaurant.icon || '🍽️';
      const name = restaurant.name || '맛집';
      const nameKo = restaurant.nameKo
        ? ` <span class="name-ko">· ${escapeHtml(restaurant.nameKo)}</span>`
        : '';

      const description = restaurant.desc
        ? `<div class="desc">${dishIcon} ${escapeHtml(restaurant.desc)}</div>`
        : '';

      const distance = distLabel(
        distKm(hotelLat, hotelLng, lat, lng)
      );

      L.marker([lat, lng], { icon: foodIcon(index + 1) })
        .addTo(map)
        .bindPopup(`
          <div class="popup-title">
            ${dishIcon} ${index + 1}. ${escapeHtml(name)}
          </div>
          <div class="popup-addr">
            ${escapeHtml(restaurant.address || '')} · 숙소에서 ${distance}
          </div>
          <a
            class="popup-link"
            href="${mapsLink}"
            target="_blank"
            rel="noopener noreferrer"
          >구글 지도에서 열기 →</a>
        `);

      return `
        <div
          class="food-item"
          role="button"
          tabindex="0"
          onclick="window.open('${mapsLink}', '_blank', 'noopener,noreferrer')"
          onkeydown="if(event.key==='Enter')window.open('${mapsLink}', '_blank', 'noopener,noreferrer')"
        >
          <div class="badge">${dishIcon}</div>
          <div class="info">
            <div class="name">${escapeHtml(name)}${nameKo}</div>
            ${description}
            <div class="addr">
              ${escapeHtml(restaurant.address || '')} ·
              <span class="dist">숙소에서 ${distance}</span>
            </div>
          </div>
          <div class="rating">★ ${Number(restaurant.rating || 0).toFixed(1)}</div>
        </div>
      `;
    })
    .join('');

  if (listEl) {
    listEl.innerHTML =
      listHtml ||
      '<div class="text-muted p-3">주변 맛집 정보가 없습니다.</div>';
  }

  if (markers.length > 1) {
    map.fitBounds(markers, {
      padding: [30, 30],
      maxZoom: 16,
    });
  }

  // 모바일에서 지도 컨테이너 크기를 다시 계산
  setTimeout(() => map.invalidateSize(), 250);
  setTimeout(() => map.invalidateSize(), 1000);

  // 페이지에서 필요하면 나중에 접근할 수 있도록 보관
  window.__stayMap = map;

  return map;
}

// ------------------------------------------------------------
// HTML 안전 처리
// ------------------------------------------------------------
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ------------------------------------------------------------
// 페이지 초기화
// ------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  loadExchangeRate();
  loadWeather();
});

// ============================================================
// V3-1 · 공동 여행방 / 참여자 시스템 (Supabase)
// ============================================================

// ============================================================
// V3-1 · 공동 여행방 / 참여자 시스템 (Supabase)
// ============================================================

const SUPABASE_URL = 'https://kngqkfkppdrnmdzebxsg.supabase.co';
const SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_3IOBdsAcYNgukQ7Q8wRy4A_4B8XEvmE';
const TRIP_STORAGE_KEY = 'danang_trip_v3_trip_id';
const NICKNAME_STORAGE_KEY = 'danang_trip_v3_nickname';

let __supabase = null;

function getSupabaseClient() {
  if (__supabase) return __supabase;
  if (!window.supabase || typeof window.supabase.createClient !== 'function') {
    console.warn('Supabase SDK가 로드되지 않았습니다.');
    return null;
  }
  __supabase = window.supabase.createClient(
    SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY,
    {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: false,
      },
    }
  );
  return __supabase;
}

async function ensureAnonymousSession() {
  const client = getSupabaseClient();
  if (!client) throw new Error('Supabase SDK를 불러오지 못했습니다.');

  const { data: sessionData } = await client.auth.getSession();
  if (sessionData && sessionData.session) return sessionData.session;

  const { data, error } = await client.auth.signInAnonymously();
  if (error) throw error;
  return data.session;
}

function tripUiMessage(message, type = 'info') {
  const el = document.getElementById('tripMessage');
  if (!el) return;
  el.textContent = message;
  el.className = `trip-message ${type}`;
}

function setTripStatus(trip, nickname) {
  const status = document.getElementById('tripStatus');
  if (!status || !trip) return;

  status.innerHTML = `
    <div class="trip-status-title">🌴 ${escapeHtml(trip.name)}</div>
    <div class="trip-status-code">여행방 코드 <strong>${escapeHtml(trip.trip_code)}</strong></div>
    <div class="trip-status-user">👤 ${escapeHtml(nickname || '여행자')}</div>
  `;
  status.style.display = 'block';
}

async function createTrip() {
  const client = getSupabaseClient();
  if (!client) return tripUiMessage('Supabase 연결을 확인해주세요.', 'error');

  const name = (document.getElementById('tripName')?.value || '').trim() || 'Da Nang Trip 2026';
  const nickname = (document.getElementById('tripNickname')?.value || '').trim();
  if (!nickname) {
    tripUiMessage('닉네임을 먼저 입력해주세요.', 'error');
    return;
  }

  const startDate = document.getElementById('tripStartDate')?.value || '2026-08-23';
  const endDate = document.getElementById('tripEndDate')?.value || '2026-09-04';

  try {
    await ensureAnonymousSession();

    const { data, error } = await client.rpc('create_trip', {
      p_name: name,
      p_start_date: startDate,
      p_end_date: endDate,
      p_nickname: nickname,
    });

    if (error) throw error;

    const trip = Array.isArray(data) ? data[0] : data;
    localStorage.setItem(TRIP_STORAGE_KEY, trip.id);
    localStorage.setItem(NICKNAME_STORAGE_KEY, nickname);

    setTripStatus(trip, nickname);
    tripUiMessage(`여행방이 만들어졌어요. 코드: ${trip.trip_code}`, 'success');
    await loadTripMembers(trip.id);
  } catch (error) {
    console.error(error);
    tripUiMessage(
      error.message?.includes('Anonymous')
        ? 'Supabase에서 Anonymous Sign-Ins를 켜주세요.'
        : `여행방 생성 실패: ${error.message || error}`,
      'error'
    );
  }
}

async function joinTrip() {
  const client = getSupabaseClient();
  if (!client) return tripUiMessage('Supabase 연결을 확인해주세요.', 'error');

  const code = (document.getElementById('tripCode')?.value || '').trim().toUpperCase();
  const nickname = (document.getElementById('tripNickname')?.value || '').trim();

  if (!code) {
    tripUiMessage('여행방 코드를 입력해주세요.', 'error');
    return;
  }
  if (!nickname) {
    tripUiMessage('닉네임을 입력해주세요.', 'error');
    return;
  }

  try {
    await ensureAnonymousSession();

    const { data, error } = await client.rpc('join_trip', {
      p_trip_code: code,
      p_nickname: nickname,
    });

    if (error) throw error;

    const trip = Array.isArray(data) ? data[0] : data;
    if (!trip) throw new Error('존재하지 않는 여행방입니다.');

    localStorage.setItem(TRIP_STORAGE_KEY, trip.id);
    localStorage.setItem(NICKNAME_STORAGE_KEY, nickname);

    setTripStatus(trip, nickname);
    tripUiMessage(`'${trip.name}' 여행방에 참여했어요.`, 'success');
    await loadTripMembers(trip.id);
  } catch (error) {
    console.error(error);
    tripUiMessage(`여행방 참여 실패: ${error.message || error}`, 'error');
  }
}

async function loadTripMembers(tripId) {
  const client = getSupabaseClient();
  const list = document.getElementById('tripMembers');
  if (!client || !list || !tripId) return;

  try {
    await ensureAnonymousSession();
    const { data, error } = await client.rpc('get_trip_members', {
      p_trip_id: tripId,
    });
    if (error) throw error;

    list.innerHTML = (data || []).map((member, index) => `
      <span class="trip-member">
        ${index === 0 ? '👑' : '👤'} ${escapeHtml(member.nickname)}
      </span>
    `).join('') || '<span class="text-muted">참여자가 없습니다.</span>';
  } catch (error) {
    console.error(error);
    list.innerHTML = '<span class="text-muted">참여자 정보를 불러오지 못했습니다.</span>';
  }
}

async function restoreTripSession() {
  const tripId = localStorage.getItem(TRIP_STORAGE_KEY);
  const nickname = localStorage.getItem(NICKNAME_STORAGE_KEY);
  if (!tripId) return;

  const client = getSupabaseClient();
  if (!client) return;

  try {
    await ensureAnonymousSession();
    const { data, error } = await client
      .from('trips')
      .select('id,name,trip_code,start_date,end_date,created_by')
      .eq('id', tripId)
      .maybeSingle();

    if (error) throw error;
    if (!data) {
      localStorage.removeItem(TRIP_STORAGE_KEY);
      return;
    }

    setTripStatus(data, nickname);
    await loadTripMembers(data.id);
  } catch (error) {
    console.warn('여행방 복원 실패:', error);
  }
}

async function copyTripCode() {
  const status = document.querySelector('.trip-status-code strong');
  if (!status) return;
  const code = status.textContent.trim();

  try {
    await navigator.clipboard.writeText(code);
    tripUiMessage('여행방 코드를 복사했어요 📋', 'success');
  } catch {
    tripUiMessage(`여행방 코드: ${code}`, 'info');
  }
}

window.createTrip = createTrip;
window.joinTrip = joinTrip;
window.copyTripCode = copyTripCode;

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('tripRoom')) {
    restoreTripSession();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('tripRoom')) {
    restoreTripSession();
  }
});

// ============================================================
// V3-2 · 공동 여행경비
// ============================================================

const EXPENSE_TRIP_STORAGE_KEY = 'danang_trip_v3_trip_id';

let __expenseMembers = [];
let __expenseItems = [];

function getCurrentTripId() {
  return localStorage.getItem(EXPENSE_TRIP_STORAGE_KEY);
}

function expenseMessage(message, type = 'info') {
  const el = document.getElementById('expenseMessage');
  if (!el) return;

  el.textContent = message;
  el.className = `expense-message ${type}`;
}

function formatMoney(value) {
  return Number(value || 0).toLocaleString('ko-KR');
}

function getCurrencyLabel(currency) {
  return currency === 'KRW' ? '원' : 'VND';
}

// ------------------------------------------------------------
// 여행방 참여자 가져오기
// ------------------------------------------------------------
async function loadExpenseMembers() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  if (!client || !tripId) return [];

  try {
    await ensureAnonymousSession();

    const { data, error } = await client.rpc('get_trip_members', {
      p_trip_id: tripId
    });

    if (error) throw error;

    __expenseMembers = data || [];

    renderExpenseMemberOptions();

    return __expenseMembers;
  } catch (error) {
    console.error('경비 참여자 조회 실패:', error);
    return [];
  }
}

// ------------------------------------------------------------
// 결제자 / 부담자 선택 UI
// ------------------------------------------------------------
function renderExpenseMemberOptions() {
  const payer = document.getElementById('expensePayer');
  const splitList = document.getElementById('expenseSplitMembers');

  if (!payer || !splitList) return;

  payer.innerHTML = __expenseMembers.map(member => `
    <option value="${escapeHtml(member.user_id)}">
      ${escapeHtml(member.nickname)}
    </option>
  `).join('');

  splitList.innerHTML = __expenseMembers.map(member => `
    <label class="expense-member-check">
      <input
        type="checkbox"
        value="${escapeHtml(member.user_id)}"
        data-nickname="${escapeHtml(member.nickname)}"
        checked
      >
      <span>${escapeHtml(member.nickname)}</span>
    </label>
  `).join('');
}

// ------------------------------------------------------------
// 여행경비 등록
// ------------------------------------------------------------
async function addExpense() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  if (!client) {
    expenseMessage('Supabase 연결을 확인해주세요.', 'error');
    return;
  }

  if (!tripId) {
    expenseMessage('먼저 여행방을 만들거나 참여해주세요.', 'error');
    return;
  }

  const description =
    document.getElementById('expenseDescription')?.value.trim();

  const amount =
    Number(
      document
        .getElementById('expenseAmount')
        ?.value
        .replace(/[^\d.]/g, '')
    );

  const currency =
    document.getElementById('expenseCurrency')?.value || 'VND';

  const expenseDate =
    document.getElementById('expenseDate')?.value ||
    new Date().toISOString().slice(0, 10);

  const payerId =
    document.getElementById('expensePayer')?.value;

  const category =
    document.getElementById('expenseCategory')?.value || '기타';

  const note =
    document.getElementById('expenseNote')?.value.trim() || null;

  if (!description) {
    expenseMessage('사용처를 입력해주세요.', 'error');
    return;
  }

  if (!Number.isFinite(amount) || amount <= 0) {
    expenseMessage('금액을 올바르게 입력해주세요.', 'error');
    return;
  }

  if (!payerId) {
    expenseMessage('결제자를 선택해주세요.', 'error');
    return;
  }

  const checkedMembers = [
    ...document.querySelectorAll(
      '#expenseSplitMembers input[type="checkbox"]:checked'
    )
  ];

  if (!checkedMembers.length) {
    expenseMessage('부담할 사람을 한 명 이상 선택해주세요.', 'error');
    return;
  }

  const payer = __expenseMembers.find(
    member => member.user_id === payerId
  );

  if (!payer) {
    expenseMessage('결제자를 찾을 수 없습니다.', 'error');
    return;
  }

  try {
    await ensureAnonymousSession();

    const { data: expense, error } = await client
      .from('expenses')
      .insert({
        trip_id: tripId,
        description,
        amount,
        currency,
        expense_date: expenseDate,
        paid_by: payerId,
        paid_by_nickname: payer.nickname,
        category,
        note,
        created_by: (await client.auth.getUser()).data.user.id
      })
      .select()
      .single();

    if (error) throw error;

    const splitAmount = amount / checkedMembers.length;

    const splits = checkedMembers.map(input => ({
      expense_id: expense.id,
      user_id: input.value,
      nickname: input.dataset.nickname,
      share_amount: Math.round(splitAmount * 100) / 100
    }));

    const { error: splitError } =
      await client
        .from('expense_splits')
        .insert(splits);

    if (splitError) throw splitError;

    expenseMessage('경비가 저장되었습니다. 💰', 'success');

    clearExpenseForm();

    await loadExpenses();

  } catch (error) {
    console.error('경비 저장 실패:', error);
    expenseMessage(
      `경비 저장 실패: ${error.message || error}`,
      'error'
    );
  }
}

// ------------------------------------------------------------
// 경비 목록
// ------------------------------------------------------------
async function loadExpenses() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  const list = document.getElementById('expenseList');

  if (!client || !tripId || !list) return;

  try {
    await ensureAnonymousSession();

    const { data, error } = await client
      .from('expenses')
      .select('*')
      .eq('trip_id', tripId)
      .order('expense_date', { ascending: false })
      .order('created_at', { ascending: false });

    if (error) throw error;

    __expenseItems = data || [];

    renderExpenses();

    await calculateSettlement();

  } catch (error) {
    console.error('경비 조회 실패:', error);

    list.innerHTML =
      '<div class="text-muted p-3">경비 정보를 불러오지 못했습니다.</div>';
  }
}

// ------------------------------------------------------------
// 경비 화면 표시
// ------------------------------------------------------------
function renderExpenses() {
  const list = document.getElementById('expenseList');

  if (!list) return;

  if (!__expenseItems.length) {
    list.innerHTML = `
      <div class="text-muted p-3 text-center">
        아직 등록된 여행경비가 없습니다.
      </div>
    `;
    return;
  }

  list.innerHTML = __expenseItems.map(expense => `
    <div class="expense-item">

      <div class="expense-main">

        <div class="expense-title">
          ${escapeHtml(expense.description)}
        </div>

        <div class="expense-meta">
          ${escapeHtml(expense.expense_date)}
          · ${escapeHtml(expense.category || '기타')}
          · 결제자 ${escapeHtml(expense.paid_by_nickname)}
        </div>

        ${
          expense.note
            ? `<div class="expense-note">${escapeHtml(expense.note)}</div>`
            : ''
        }

      </div>

      <div class="expense-amount">
        ${formatMoney(expense.amount)}
        <small>${getCurrencyLabel(expense.currency)}</small>
      </div>

      <button
        class="btn btn-sm btn-outline-danger expense-delete"
        onclick="deleteExpense('${expense.id}')"
      >
        삭제
      </button>

    </div>
  `).join('');
}

// ------------------------------------------------------------
// 경비 삭제
// ------------------------------------------------------------
async function deleteExpense(expenseId) {
  if (!confirm('이 경비를 삭제할까요?')) return;

  const client = getSupabaseClient();

  if (!client) return;

  try {
    const { error } =
      await client
        .from('expenses')
        .delete()
        .eq('id', expenseId);

    if (error) throw error;

    expenseMessage('경비를 삭제했습니다.', 'success');

    await loadExpenses();

  } catch (error) {
    console.error('경비 삭제 실패:', error);

    expenseMessage(
      `경비 삭제 실패: ${error.message || error}`,
      'error'
    );
  }
}

// ------------------------------------------------------------
// 정산 계산
// ------------------------------------------------------------
async function calculateSettlement() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  if (!client || !tripId) return;

  const summary = document.getElementById('expenseSummary');
  const settlement = document.getElementById('settlementList');

  if (!summary || !settlement) return;

  try {
    const { data: expenses, error } =
      await client
        .from('expenses')
        .select('*')
        .eq('trip_id', tripId);

    if (error) throw error;

    const { data: splits, error: splitError } =
      await client
        .from('expense_splits')
        .select('*')
        .in(
          'expense_id',
          (expenses || []).map(e => e.id)
        );

    if (splitError) throw splitError;

    const totals = {};

    __expenseMembers.forEach(member => {
      totals[member.user_id] = {
        nickname: member.nickname,
        paid: 0,
        share: 0
      };
    });

    let total = 0;

    (expenses || []).forEach(expense => {
      total += Number(expense.amount || 0);

      if (!totals[expense.paid_by]) {
        totals[expense.paid_by] = {
          nickname: expense.paid_by_nickname,
          paid: 0,
          share: 0
        };
      }

      totals[expense.paid_by].paid +=
        Number(expense.amount || 0);
    });

    (splits || []).forEach(split => {

      if (!totals[split.user_id]) {
        totals[split.user_id] = {
          nickname: split.nickname,
          paid: 0,
          share: 0
        };
      }

      totals[split.user_id].share +=
        Number(split.share_amount || 0);
    });

    const people = Object.values(totals);

    const count = people.length || 1;

    summary.innerHTML = `
      <div class="expense-total-label">총 여행경비</div>
      <div class="expense-total-value">
        ${formatMoney(total)} VND
      </div>
      <div class="expense-per-person">
        1인 평균 ${formatMoney(total / count)} VND
      </div>
    `;

    settlement.innerHTML = people.map(person => {

      const balance = person.paid - person.share;

      let status = '정산 완료';
      let cls = 'settled';

      if (balance > 0.5) {
        status = `받을 돈 ${formatMoney(balance)} VND`;
        cls = 'receive';
      }

      if (balance < -0.5) {
        status = `낼 돈 ${formatMoney(Math.abs(balance))} VND`;
        cls = 'pay';
      }

      return `
        <div class="settlement-item">

          <div class="settlement-name">
            ${escapeHtml(person.nickname)}
          </div>

          <div class="settlement-detail">
            결제 ${formatMoney(person.paid)}
            · 부담 ${formatMoney(person.share)}
          </div>

          <div class="settlement-balance ${cls}">
            ${status}
          </div>

        </div>
      `;

    }).join('');

  } catch (error) {
    console.error('정산 계산 실패:', error);

    settlement.innerHTML =
      '<div class="text-muted">정산 정보를 계산하지 못했습니다.</div>';
  }
}

// ------------------------------------------------------------
// 입력폼 초기화
// ------------------------------------------------------------
function clearExpenseForm() {
  const description =
    document.getElementById('expenseDescription');

  const amount =
    document.getElementById('expenseAmount');

  const note =
    document.getElementById('expenseNote');

  if (description) description.value = '';
  if (amount) amount.value = '';
  if (note) note.value = '';

  document
    .querySelectorAll(
      '#expenseSplitMembers input[type="checkbox"]'
    )
    .forEach(input => {
      input.checked = true;
    });
}

// ------------------------------------------------------------
// V3-2 초기화
// ------------------------------------------------------------
async function initExpenses() {

  if (!document.getElementById('expenseRoom')) return;

  const tripId = getCurrentTripId();

  if (!tripId) {
    expenseMessage(
      '여행방을 먼저 만들거나 참여해주세요.',
      'info'
    );
    return;
  }

  await ensureAnonymousSession();

  await loadExpenseMembers();

  await loadExpenses();

  const dateInput =
    document.getElementById('expenseDate');

  if (dateInput && !dateInput.value) {
    dateInput.value =
      new Date().toISOString().slice(0, 10);
  }
}

window.addExpense = addExpense;
window.deleteExpense = deleteExpense;
window.loadExpenses = loadExpenses;

document.addEventListener('DOMContentLoaded', () => {
  initExpenses();
});

// ============================================================
// V3-3 · 날짜별 일정 / 공동 체크리스트 / 장소 / 메뉴
// 기존 V3-1 / V3-2 기능은 유지하고 V3-3만 추가
// ============================================================

let __v33Trip = null;
let __v33Dates = [];
let __v33DateIndex = 0;
let __v33Schedules = [];
let __v33Places = [];
let __v33Foods = [];
let __v33PollingTimer = null;

const V33_DATE_STORAGE_KEY = 'danang_trip_v3_day_index';

// 기본 일정 예시
const V33_DEFAULT_SCHEDULES = [
  { day: 1, title: '✈️ 인천 → 다낭', description: '대한항공 KE459 · 18:20 → 21:05 · Florence Hotel 체크인' },
  { day: 2, title: '🏨 Florence → Bel Marina', description: 'Florence 체크아웃 · 호이안 이동 · Bel Marina 체크인 · 호이안 구시가지' },
  { day: 3, title: '🏮 호이안 여행', description: '호이안 올드타운 · 안방비치 · 코코넛배 등 자유 일정' },
  { day: 4, title: '🏨 Bel Marina → Hyatt', description: 'Bel Marina 체크아웃 · 다낭 이동 · Hyatt 체크인' },
  { day: 5, title: '🏖️ Hyatt 휴식', description: '리조트 · 해변 · 오행산 등 자유 일정' },
  { day: 6, title: '🏨 Hyatt → Altara', description: 'Hyatt 체크아웃 · 미케비치 이동 · Altara 체크인' },
  { day: 7, title: '🌴 다낭 시내', description: '미케비치 · 한시장 · 용다리 등 자유 일정' },
  { day: 8, title: '🍜 다낭 맛집 투어', description: '먹고 싶은 메뉴를 모아 우선순위 결정' },
  { day: 9, title: '🧖 마사지 & 휴식', description: '마사지 · 카페 · 숙소 휴식' },
  { day: 10, title: '🏝️ 바나힐 / 근교', description: '가고 싶은 장소를 투표해서 결정' },
  { day: 11, title: '📍 다낭 자유 일정', description: '장소 추천 결과에 따라 일정 확정' },
  { day: 12, title: '🛍️ 쇼핑 & 마지막 저녁', description: '한시장 · 롯데마트 등 쇼핑 및 마지막 식사' },
  { day: 13, title: '✈️ 다낭 → 인천', description: '체크아웃 · 공항 이동 · 대한항공 KE460 · 22:55 출발' },
];

function v33TodayString(date = new Date()) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function v33ParseLocalDate(text) {
  return new Date(`${text}T12:00:00`);
}

function v33FormatDate(text) {
  const d = v33ParseLocalDate(text);
  return d.toLocaleDateString('ko-KR', {
    month: 'long',
    day: 'numeric',
    weekday: 'long'
  });
}

function v33TripDayNumber(dateText) {
  const index = __v33Dates.indexOf(dateText);
  return index >= 0 ? index + 1 : 1;
}

function v33SetMessage(message, type = 'info') {
  const el = document.getElementById('v33Message');
  if (!el) return;
  el.textContent = message;
  el.className = `v33-message ${type}`;
}

async function v33LoadTrip() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  if (!client || !tripId) return null;

  await ensureAnonymousSession();

  const { data, error } = await client
    .from('trips')
    .select('id,name,trip_code,start_date,end_date')
    .eq('id', tripId)
    .maybeSingle();

  if (error) throw error;
  if (!data) return null;

  __v33Trip = data;

  const start = v33ParseLocalDate(data.start_date);
  const end = v33ParseLocalDate(data.end_date);

  __v33Dates = [];
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    __v33Dates.push(v33TodayString(d));
  }

  if (!__v33Dates.length) return data;

  const storageKey = `${V33_DATE_STORAGE_KEY}_${tripId}`;
  let savedIndex = Number(localStorage.getItem(storageKey));

  if (!Number.isInteger(savedIndex) || savedIndex < 0 || savedIndex >= __v33Dates.length) {
    savedIndex = 0;
  }

  __v33DateIndex = savedIndex;
  return data;
}

async function v33EnsureChecklist() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();
  if (!client || !tripId) return;

  const defaults = [
    ['passport', '🛂 여권'],
    ['flight', '✈️ 항공권'],
    ['roaming', '📱 로밍'],
    ['sim', '📶 유심 / eSIM'],
  ];

  const rows = defaults.map(([item_key, item_name]) => ({
    trip_id: tripId,
    item_key,
    item_name,
    checked: false,
  }));

  const { error } = await client
    .from('trip_checklist')
    .upsert(rows, {
      onConflict: 'trip_id,item_key',
      ignoreDuplicates: true,
    });

  if (error) throw error;
}

async function v33LoadChecklist() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();
  const list = document.getElementById('v33Checklist');

  if (!client || !tripId || !list) return;

  const { data, error } = await client
    .from('trip_checklist')
    .select('id,item_key,item_name,checked')
    .eq('trip_id', tripId)
    .order('id');

  if (error) throw error;

  list.innerHTML = (data || []).map(item => `
    <label class="v33-check-item ${item.checked ? 'is-checked' : ''}">
      <input
        type="checkbox"
        ${item.checked ? 'checked' : ''}
        onchange="v33ToggleChecklist('${item.id}', this.checked)"
      >
      <span>${escapeHtml(item.item_name)}</span>
      <span class="v33-check-mark">${item.checked ? '✓' : ''}</span>
    </label>
  `).join('');

  const done = (data || []).filter(item => item.checked).length;
  const total = (data || []).length;
  const count = document.getElementById('v33ChecklistCount');

  if (count) count.textContent = `${done} / ${total} 완료`;
}

async function v33ToggleChecklist(id, checked) {
  const client = getSupabaseClient();
  if (!client) return;

  try {
    await ensureAnonymousSession();

    const { error } = await client
      .from('trip_checklist')
      .update({
        checked,
        updated_by: (await client.auth.getUser()).data.user?.id || null,
        updated_at: new Date().toISOString(),
      })
      .eq('id', id);

    if (error) throw error;

    await v33LoadChecklist();
  } catch (error) {
    console.error('체크리스트 저장 실패:', error);
    v33SetMessage(`체크리스트 저장 실패: ${error.message || error}`, 'error');
  }
}

async function v33SeedSchedulesIfEmpty() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  if (!client || !tripId || !__v33Dates.length) return;

  const { data, error } = await client
    .from('trip_schedules')
    .select('id')
    .eq('trip_id', tripId)
    .limit(1);

  if (error) throw error;
  if (data && data.length) return;

  const rows = V33_DEFAULT_SCHEDULES
    .slice(0, __v33Dates.length)
    .map((item, index) => ({
      trip_id: tripId,
      schedule_date: __v33Dates[index],
      title: item.title,
      description: item.description,
      sort_order: 0,
      created_by: null,
    }));

  if (!rows.length) return;

  const { error: insertError } = await client
    .from('trip_schedules')
    .insert(rows);

  if (insertError) throw insertError;
}

async function v33LoadSchedules() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  if (!client || !tripId) return;

  const { data, error } = await client
    .from('trip_schedules')
    .select('id,schedule_date,title,description,sort_order')
    .eq('trip_id', tripId)
    .eq('schedule_date', __v33Dates[__v33DateIndex])
    .order('sort_order')
    .order('created_at');

  if (error) throw error;

  __v33Schedules = data || [];
  v33RenderSchedules();
}

function v33RenderSchedules() {
  const list = document.getElementById('v33ScheduleList');
  if (!list) return;

  if (!__v33Schedules.length) {
    list.innerHTML = `
      <div class="v33-empty">
        아직 등록된 일정이 없습니다.<br>
        아래에서 일정을 추가해보세요.
      </div>
    `;
    return;
  }

  list.innerHTML = __v33Schedules.map(item => `
    <div class="v33-schedule-item">
      <div class="v33-schedule-icon">📌</div>
      <div class="v33-schedule-body">
        <div class="v33-schedule-title">${escapeHtml(item.title)}</div>
        ${item.description ? `<div class="v33-schedule-desc">${escapeHtml(item.description)}</div>` : ''}
      </div>
      <button class="v33-mini-delete" onclick="v33DeleteSchedule('${item.id}')" aria-label="일정 삭제">×</button>
    </div>
  `).join('');
}

async function v33AddSchedule() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  const titleInput = document.getElementById('v33ScheduleTitle');
  const descInput = document.getElementById('v33ScheduleDesc');

  const title = titleInput?.value.trim();
  const description = descInput?.value.trim() || null;

  if (!client || !tripId) {
    v33SetMessage('먼저 여행방을 만들거나 참여해주세요.', 'error');
    return;
  }

  if (!title) {
    v33SetMessage('일정 제목을 입력해주세요.', 'error');
    titleInput?.focus();
    return;
  }

  try {
    await ensureAnonymousSession();

    const user = (await client.auth.getUser()).data.user;

    const { error } = await client
      .from('trip_schedules')
      .insert({
        trip_id: tripId,
        schedule_date: __v33Dates[__v33DateIndex],
        title,
        description,
        sort_order: 99,
        created_by: user?.id || null,
      });

    if (error) throw error;

    titleInput.value = '';
    descInput.value = '';

    await v33LoadSchedules();
    v33SetMessage('일정을 추가했습니다. 📅', 'success');
  } catch (error) {
    console.error('일정 추가 실패:', error);
    v33SetMessage(`일정 추가 실패: ${error.message || error}`, 'error');
  }
}

async function v33DeleteSchedule(id) {
  if (!confirm('이 일정을 삭제할까요?')) return;

  const client = getSupabaseClient();
  if (!client) return;

  try {
    const { error } = await client
      .from('trip_schedules')
      .delete()
      .eq('id', id);

    if (error) throw error;

    await v33LoadSchedules();
  } catch (error) {
    console.error('일정 삭제 실패:', error);
    v33SetMessage(`일정 삭제 실패: ${error.message || error}`, 'error');
  }
}

async function v33LoadPlaces() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();
  const list = document.getElementById('v33PlaceList');

  if (!client || !tripId || !list) return;

  const { data, error } = await client
    .from('trip_places')
    .select('id,place_name,description,created_by,created_at,trip_place_votes(count)')
    .eq('trip_id', tripId)
    .eq('trip_date', __v33Dates[__v33DateIndex])
    .order('created_at');

  if (error) {
    // 집계 select가 환경에 따라 실패하면 기본 목록으로 재시도
    const fallback = await client
      .from('trip_places')
      .select('id,place_name,description,created_by,created_at')
      .eq('trip_id', tripId)
      .eq('trip_date', __v33Dates[__v33DateIndex])
      .order('created_at');

    if (fallback.error) throw error;

    __v33Places = (fallback.data || []).map(item => ({ ...item, voteCount: 0 }));
  } else {
    __v33Places = (data || []).map(item => ({
      ...item,
      voteCount: Array.isArray(item.trip_place_votes) && item.trip_place_votes[0]
        ? Number(item.trip_place_votes[0].count || 0)
        : 0
    }));
  }

  await v33RenderPlacesWithMyVotes();
}

async function v33RenderPlacesWithMyVotes() {
  const client = getSupabaseClient();
  const list = document.getElementById('v33PlaceList');

  if (!list) return;

  let myVotes = new Set();

  if (client && __v33Places.length) {
    const ids = __v33Places.map(item => item.id);

    const { data } = await client
      .from('trip_place_votes')
      .select('place_id')
      .in('place_id', ids);

    // 현재 익명 사용자만 표시할 필요가 있어 전체 vote 대신 아래에서 user_id를 별도 확인
    if (data) {
      try {
        const user = (await client.auth.getUser()).data.user;
        if (user) {
          const mine = await client
            .from('trip_place_votes')
            .select('place_id')
            .eq('user_id', user.id)
            .in('place_id', ids);

          if (!mine.error) {
            myVotes = new Set((mine.data || []).map(v => v.place_id));
          }
        }
      } catch (_) {}
    }
  }

  if (!__v33Places.length) {
    list.innerHTML = '<div class="v33-empty">아직 추천 장소가 없습니다.<br>친구들과 가고 싶은 곳을 추가해보세요.</div>';
    return;
  }

  list.innerHTML = __v33Places.map(item => {
    const voted = myVotes.has(item.id);

    return `
      <div class="v33-recommend-item">
        <div class="v33-recommend-main">
          <div class="v33-recommend-title">${escapeHtml(item.place_name)}</div>
          ${item.description ? `<div class="v33-recommend-desc">${escapeHtml(item.description)}</div>` : ''}
        </div>
        <button
          class="v33-vote ${voted ? 'voted' : ''}"
          onclick="v33TogglePlaceVote('${item.id}', ${voted})"
        >
          ${voted ? '♥' : '♡'} ${item.voteCount || 0}
        </button>
        <button class="v33-mini-delete" onclick="v33DeletePlace('${item.id}')" aria-label="장소 삭제">×</button>
      </div>
    `;
  }).join('');
}

async function v33AddPlace() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  const nameInput = document.getElementById('v33PlaceName');
  const descInput = document.getElementById('v33PlaceDesc');

  const placeName = nameInput?.value.trim();
  const description = descInput?.value.trim() || null;

  if (!client || !tripId) {
    v33SetMessage('먼저 여행방을 만들거나 참여해주세요.', 'error');
    return;
  }

  if (!placeName) {
    v33SetMessage('가고 싶은 장소를 입력해주세요.', 'error');
    nameInput?.focus();
    return;
  }

  try {
    await ensureAnonymousSession();
    const user = (await client.auth.getUser()).data.user;

    const { error } = await client
      .from('trip_places')
      .insert({
        trip_id: tripId,
        trip_date: __v33Dates[__v33DateIndex],
        place_name: placeName,
        description,
        created_by: user?.id || null,
      });

    if (error) throw error;

    nameInput.value = '';
    descInput.value = '';

    await v33LoadPlaces();
    v33SetMessage('가고 싶은 장소를 추가했습니다. 📍', 'success');
  } catch (error) {
    console.error('장소 추가 실패:', error);
    v33SetMessage(`장소 추가 실패: ${error.message || error}`, 'error');
  }
}

async function v33TogglePlaceVote(placeId, currentlyVoted) {
  const client = getSupabaseClient();
  if (!client) return;

  try {
    const user = (await client.auth.getUser()).data.user;
    if (!user) throw new Error('사용자 세션이 없습니다.');

    if (currentlyVoted) {
      const { error } = await client
        .from('trip_place_votes')
        .delete()
        .eq('place_id', placeId)
        .eq('user_id', user.id);

      if (error) throw error;
    } else {
      const { error } = await client
        .from('trip_place_votes')
        .insert({
          place_id: placeId,
          user_id: user.id,
        });

      if (error) throw error;
    }

    await v33LoadPlaces();
  } catch (error) {
    console.error('장소 추천 처리 실패:', error);
    v33SetMessage(`장소 추천 실패: ${error.message || error}`, 'error');
  }
}

async function v33DeletePlace(id) {
  if (!confirm('이 추천 장소를 삭제할까요?')) return;

  const client = getSupabaseClient();
  if (!client) return;

  try {
    const { error } = await client
      .from('trip_places')
      .delete()
      .eq('id', id);

    if (error) throw error;

    await v33LoadPlaces();
  } catch (error) {
    console.error('장소 삭제 실패:', error);
    v33SetMessage(`장소 삭제 실패: ${error.message || error}`, 'error');
  }
}

async function v33LoadFoods() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();
  const list = document.getElementById('v33FoodList');

  if (!client || !tripId || !list) return;

  const { data, error } = await client
    .from('trip_foods')
    .select('id,food_name,description,created_by,created_at,trip_food_votes(count)')
    .eq('trip_id', tripId)
    .eq('trip_date', __v33Dates[__v33DateIndex])
    .order('created_at');

  if (error) {
    const fallback = await client
      .from('trip_foods')
      .select('id,food_name,description,created_by,created_at')
      .eq('trip_id', tripId)
      .eq('trip_date', __v33Dates[__v33DateIndex])
      .order('created_at');

    if (fallback.error) throw error;

    __v33Foods = (fallback.data || []).map(item => ({ ...item, voteCount: 0 }));
  } else {
    __v33Foods = (data || []).map(item => ({
      ...item,
      voteCount: Array.isArray(item.trip_food_votes) && item.trip_food_votes[0]
        ? Number(item.trip_food_votes[0].count || 0)
        : 0
    }));
  }

  await v33RenderFoodsWithMyVotes();
}

async function v33RenderFoodsWithMyVotes() {
  const client = getSupabaseClient();
  const list = document.getElementById('v33FoodList');

  if (!list) return;

  let myVotes = new Set();

  if (client && __v33Foods.length) {
    const ids = __v33Foods.map(item => item.id);

    try {
      const user = (await client.auth.getUser()).data.user;
      if (user) {
        const mine = await client
          .from('trip_food_votes')
          .select('food_id')
          .eq('user_id', user.id)
          .in('food_id', ids);

        if (!mine.error) {
          myVotes = new Set((mine.data || []).map(v => v.food_id));
        }
      }
    } catch (_) {}
  }

  if (!__v33Foods.length) {
    list.innerHTML = '<div class="v33-empty">아직 먹고 싶은 메뉴가 없습니다.<br>친구들과 메뉴를 모아보세요.</div>';
    return;
  }

  list.innerHTML = __v33Foods.map(item => {
    const voted = myVotes.has(item.id);

    return `
      <div class="v33-recommend-item">
        <div class="v33-recommend-main">
          <div class="v33-recommend-title">${escapeHtml(item.food_name)}</div>
          ${item.description ? `<div class="v33-recommend-desc">${escapeHtml(item.description)}</div>` : ''}
        </div>
        <button
          class="v33-vote ${voted ? 'voted' : ''}"
          onclick="v33ToggleFoodVote('${item.id}', ${voted})"
        >
          ${voted ? '♥' : '♡'} ${item.voteCount || 0}
        </button>
        <button class="v33-mini-delete" onclick="v33DeleteFood('${item.id}')" aria-label="메뉴 삭제">×</button>
      </div>
    `;
  }).join('');
}

async function v33AddFood() {
  const client = getSupabaseClient();
  const tripId = getCurrentTripId();

  const nameInput = document.getElementById('v33FoodName');
  const descInput = document.getElementById('v33FoodDesc');

  const foodName = nameInput?.value.trim();
  const description = descInput?.value.trim() || null;

  if (!client || !tripId) {
    v33SetMessage('먼저 여행방을 만들거나 참여해주세요.', 'error');
    return;
  }

  if (!foodName) {
    v33SetMessage('먹고 싶은 메뉴를 입력해주세요.', 'error');
    nameInput?.focus();
    return;
  }

  try {
    await ensureAnonymousSession();
    const user = (await client.auth.getUser()).data.user;

    const { error } = await client
      .from('trip_foods')
      .insert({
        trip_id: tripId,
        trip_date: __v33Dates[__v33DateIndex],
        food_name: foodName,
        description,
        created_by: user?.id || null,
      });

    if (error) throw error;

    nameInput.value = '';
    descInput.value = '';

    await v33LoadFoods();
    v33SetMessage('먹고 싶은 메뉴를 추가했습니다. 🍜', 'success');
  } catch (error) {
    console.error('메뉴 추가 실패:', error);
    v33SetMessage(`메뉴 추가 실패: ${error.message || error}`, 'error');
  }
}

async function v33ToggleFoodVote(foodId, currentlyVoted) {
  const client = getSupabaseClient();
  if (!client) return;

  try {
    const user = (await client.auth.getUser()).data.user;
    if (!user) throw new Error('사용자 세션이 없습니다.');

    if (currentlyVoted) {
      const { error } = await client
        .from('trip_food_votes')
        .delete()
        .eq('food_id', foodId)
        .eq('user_id', user.id);

      if (error) throw error;
    } else {
      const { error } = await client
        .from('trip_food_votes')
        .insert({
          food_id: foodId,
          user_id: user.id,
        });

      if (error) throw error;
    }

    await v33LoadFoods();
  } catch (error) {
    console.error('메뉴 추천 처리 실패:', error);
    v33SetMessage(`메뉴 추천 실패: ${error.message || error}`, 'error');
  }
}

async function v33DeleteFood(id) {
  if (!confirm('이 메뉴를 삭제할까요?')) return;

  const client = getSupabaseClient();
  if (!client) return;

  try {
    const { error } = await client
      .from('trip_foods')
      .delete()
      .eq('id', id);

    if (error) throw error;

    await v33LoadFoods();
  } catch (error) {
    console.error('메뉴 삭제 실패:', error);
    v33SetMessage(`메뉴 삭제 실패: ${error.message || error}`, 'error');
  }
}

async function v33RenderDay() {
  if (!__v33Dates.length) return;

  const dateText = __v33Dates[__v33DateIndex];
  const dayNumber = __v33DateIndex + 1;

  const dayEl = document.getElementById('v33DayNumber');
  const dateEl = document.getElementById('v33DateLabel');
  const prevBtn = document.getElementById('v33PrevDay');
  const nextBtn = document.getElementById('v33NextDay');

  if (dayEl) dayEl.textContent = `DAY ${dayNumber}`;
  if (dateEl) dateEl.textContent = v33FormatDate(dateText);

  if (prevBtn) prevBtn.disabled = __v33DateIndex === 0;
  if (nextBtn) nextBtn.disabled = __v33DateIndex === __v33Dates.length - 1;

  await Promise.all([
    v33LoadSchedules(),
    v33LoadPlaces(),
    v33LoadFoods(),
  ]);
}

async function v33ChangeDay(delta) {
  if (!__v33Dates.length) return;

  const nextIndex = Math.max(
    0,
    Math.min(__v33Dates.length - 1, __v33DateIndex + delta)
  );

  if (nextIndex === __v33DateIndex) return;

  __v33DateIndex = nextIndex;

  const tripId = getCurrentTripId();
  if (tripId) {
    localStorage.setItem(
      `${V33_DATE_STORAGE_KEY}_${tripId}`,
      String(__v33DateIndex)
    );
  }

  await v33RenderDay();
}

async function v33RefreshAll() {
  try {
    await v33LoadTrip();
    if (!__v33Trip) return;

    await v33EnsureChecklist();
    await v33SeedSchedulesIfEmpty();

    const tripNameEl = document.getElementById('v33TripName');
    if (tripNameEl) tripNameEl.textContent = __v33Trip.name || '여행 일정';

    const totalDaysEl = document.getElementById('v33TotalDays');
    if (totalDaysEl) totalDaysEl.textContent = `${__v33Dates.length}일 여행`;

    await v33LoadChecklist();
    await v33RenderDay();
  } catch (error) {
    console.error('V3-3 불러오기 실패:', error);
    v33SetMessage(`V3-3 정보를 불러오지 못했습니다: ${error.message || error}`, 'error');
  }
}

function v33StartPolling() {
  if (__v33PollingTimer) clearInterval(__v33PollingTimer);

  __v33PollingTimer = setInterval(() => {
    if (document.hidden) return;
    if (!getCurrentTripId()) return;

    v33LoadChecklist().catch(() => {});
    v33LoadSchedules().catch(() => {});
    v33LoadPlaces().catch(() => {});
    v33LoadFoods().catch(() => {});
  }, 15000);
}

window.v33ToggleChecklist = v33ToggleChecklist;
window.v33ChangeDay = v33ChangeDay;
window.v33AddSchedule = v33AddSchedule;
window.v33DeleteSchedule = v33DeleteSchedule;
window.v33AddPlace = v33AddPlace;
window.v33TogglePlaceVote = v33TogglePlaceVote;
window.v33DeletePlace = v33DeletePlace;
window.v33AddFood = v33AddFood;
window.v33ToggleFoodVote = v33ToggleFoodVote;
window.v33DeleteFood = v33DeleteFood;

document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('v33Planner')) return;

  setTimeout(async () => {
    await v33RefreshAll();
    v33StartPolling();
  }, 300);
});

// V3-4 actual itinerary + hotel status
const V34_STAYS=[{name:"Florence",start:"2026-08-23",end:"2026-08-24"},{name:"Bel Marina",start:"2026-08-24",end:"2026-08-27"},{name:"Hyatt",start:"2026-08-27",end:"2026-08-29"},{name:"Altara",start:"2026-08-29",end:"2026-09-04"}];
const V34_ACTUAL_SCHEDULES=[
["✈️ 인천 → 다낭 · Florence 체크인","18:20 KE459 → 21:05 도착 · Florence 체크인 · 휴식"],["🏮 호이안 올드타운 · Bel Marina","Florence 체크아웃 → 호이안 이동 → Bel Marina 체크인 · 올드타운 · 야시장"],["🏖️ 안방비치 · 코코넛배","안방비치 → 코코넛배 체험 후보 → 카페/마사지 → 호이안 저녁"],["🌴 호이안 자유 일정","Bel Marina 숙박 · 원하는 장소/맛집 투표 · 자유 일정"],["🏨 Bel Marina 체크아웃 → Hyatt","Bel Marina 체크아웃 → 다낭 이동 → Hyatt 체크인 · 리조트 휴식"],["⛰️ 오행산 · Hyatt 휴식","오행산 방문 후보 → 점심 → Hyatt 수영/해변 · 저녁"],["🏨 Hyatt 체크아웃 → Altara","Hyatt 체크아웃 → 미케비치 → Altara 체크인 · 해산물 저녁"],["🏝️ 바나힐 하루 일정","바나힐/골든브릿지 → 오후 다낭 복귀 → 휴식"],["🌊 선짜반도 · 린응사 · 미케비치","선짜반도/린응사 → 미케비치 → 카페/마사지"],["🛍️ 한시장 · 용다리 · 다낭 시내","한시장 쇼핑 → 점심 → 용다리 → 카페 · 저녁 맛집"],["🍜 맛집 · 마사지 · 자유 일정","투표한 메뉴 우선 방문 → 마사지 → 숙소 휴식"],["🛒 롯데마트 · 마지막 쇼핑","기념품/선물 쇼핑 → 롯데마트 → 마지막 저녁 · 짐 정리"],["✈️ Altara 체크아웃 → 인천","체크아웃 → 마지막 식사/카페 → 공항 → 22:55 KE460"]];
function v34Date(d=new Date()){return d.toISOString().slice(0,10)}
function v34Diff(a,b){return Math.round((new Date(b+'T12:00')-new Date(a+'T12:00'))/86400000)}
function v34Status(s,t=v34Date()){if(t<s.start)return ['next','D-'+v34Diff(t,s.start)];if(t===s.start)return ['checkin','오늘 체크인'];if(t<s.end)return ['current','숙박중'];if(t===s.end)return ['checkout','오늘 체크아웃'];return ['done','숙박 종료']}
function v34Render(){const t=v34Date(),cur=V34_STAYS.find(s=>t>=s.start&&t<s.end),next=V34_STAYS.find(s=>s.start>t);document.querySelectorAll('[data-v34-stay]').forEach(c=>{const s=V34_STAYS.find(x=>x.name===c.dataset.v34Stay),b=c.querySelector('.v34-stay-status-wrap');if(s&&b){const [type,label]=v34Status(s,t);b.innerHTML=`<span class="v34-stay-status ${type}"><i></i>${type==='current'?'현재 숙소':type==='next'?'다음 숙소':type==='checkin'?'오늘 체크인':type==='checkout'?'오늘 체크아웃':'숙박 종료'} · <b>${label}</b></span>`}});const sum=document.getElementById('v34StaySummary');if(sum)sum.innerHTML=cur?`🟢 지금 <b>${cur.name}</b> 숙박중`+(next?`　→　🔵 다음 <b>${next.name}</b> ${v34Status(next,t)[1]}`:''):next?`🔵 다음 숙소 <b>${next.name}</b> ${v34Status(next,t)[1]}`:'여행 일정 종료'}
async function v34ApplySchedules(){const c=getSupabaseClient(),t=getCurrentTripId();if(!c||!t)return;const {data}=await c.from('trip_schedules').select('id,title').eq('trip_id',t).order('schedule_date').order('sort_order');if(!data||data.length!==13)return;const legacy=/^(✈️ 인천 → 다낭|🏨 Florence → Bel Marina|🏮 호이안 여행|🏨 Bel Marina → Hyatt|🏖️ Hyatt 휴식|🏨 Hyatt → Altara|🌴 다낭 시내|🍜 다낭 맛집 투어|🧖 마사지 & 휴식|🏝️ 바나힐 \/ 근교|📍 다낭 자유 일정|🛍️ 쇼핑 & 마지막 저녁|✈️ 다낭 → 인천)$/;if(!data.every(r=>legacy.test(r.title)))return;for(let i=0;i<13;i++)await c.from('trip_schedules').update({title:V34_ACTUAL_SCHEDULES[i][0],description:V34_ACTUAL_SCHEDULES[i][1],sort_order:0}).eq('id',data[i].id)}
document.addEventListener('DOMContentLoaded',()=>{v34Render();setInterval(v34Render,60000);setTimeout(()=>v34ApplySchedules().catch(()=>{}),1500)});


/* V3-5.1 safe mobile controller */
(function(){"use strict";var s={page:0,total:8};function m(){return matchMedia("(max-width:767.98px)").matches}function render(){if(!m())return;document.querySelectorAll("#v35MobileApp .v35-mobile-page").forEach(function(x,i){x.classList.toggle("v35-active",i===s.page)});document.querySelectorAll("#v35MobileApp .v35-nav-btn").forEach(function(x){x.classList.toggle("active",+x.dataset.page===s.page)});var d=document.getElementById("v35PageDots");if(d)d.innerHTML=Array.from({length:s.total},(_,i)=>`<span class=\"v35-dot ${i===s.page?"active":""}\"></span>`).join("")}window.v35Go=function(n){s.page=Math.max(0,Math.min(s.total-1,Number(n)||0));try{document.body.classList.toggle("v35-mobile",m());render();if(m())scrollTo(0,0)}catch(e){document.body.classList.remove("v35-mobile")}};function v35BindNav(){document.querySelectorAll("#v35MobileApp .v35-nav-btn").forEach(function(b){b.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();v35Go(Number(b.getAttribute("data-page")));},false);});}var sx=0,sy=0;document.addEventListener("touchstart",function(e){if(m()){sx=e.touches[0].clientX;sy=e.touches[0].clientY}},{passive:true});document.addEventListener("touchend",function(e){if(!m())return;var dx=e.changedTouches[0].clientX-sx,dy=e.changedTouches[0].clientY-sy;if(Math.abs(dx)>70&&Math.abs(dx)>Math.abs(dy))v35Go(s.page+(dx<0?1:-1))},{passive:true});function init(){try{document.body.classList.toggle("v35-mobile",m());render()}catch(e){document.body.classList.remove("v35-mobile")}}if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",function(){init();v35BindNav();},{once:true});else {init();v35BindNav();}addEventListener("resize",init);addEventListener("orientationchange",function(){setTimeout(init,100)})})();
