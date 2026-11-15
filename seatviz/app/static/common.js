/* 공통 유틸 */
const SV = window.SV = {};
SV.$ = (s, r = document) => r.querySelector(s);
SV.$$ = (s, r = document) => [...r.querySelectorAll(s)];
SV.esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
SV.api = async (path, params) => {
  const u = new URL(path, location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') u.searchParams.set(k, v); });
  const r = await fetch(u);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return r.json();
};
SV.fmt = (v, d = 3) => v === null || v === undefined ? '<span class="muted">null</span>' :
  typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(d)) : typeof v === 'boolean' ? (v ? 'true' : 'false') : SV.esc(v);
SV.num = (v) => (v ?? 0).toLocaleString();
SV.qs = () => Object.fromEntries(new URLSearchParams(location.search));
SV.setQs = (obj) => { const u = new URL(location.href); Object.entries(obj).forEach(([k, v]) => v === '' || v == null ? u.searchParams.delete(k) : u.searchParams.set(k, v)); history.replaceState(null, '', u); };
SV.json = (obj) => {
  const s = SV.esc(JSON.stringify(obj, null, 2));
  return '<pre class="json">' + s.replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\d+\.?\d*(?:e[+-]?\d+)?)/g,
    (m, str, colon, b, n) => str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`) : b ? `<span class="b">${b}</span>` : `<span class="n">${n}</span>`) + '</pre>';
};
SV.bar = (v, max = 1, color) => `<div class="bar"><i style="width:${Math.max(0, Math.min(100, (v ?? 0) / max * 100))}%;${color ? 'background:' + color : ''}"></i></div>`;
SV.loading = (el) => { el.innerHTML = '<div class="empty"><span class="spinner"></span> 불러오는 중…</div>'; };
SV.err = (el, e) => { el.innerHTML = `<div class="empty" style="color:var(--danger)"><i class="fa-solid fa-triangle-exclamation"></i> ${SV.esc(e.message || e)}</div>`; };
SV.tip = (html, ev) => {
  const t = SV.$('#tip'); if (!html) { t.hidden = true; return; }
  t.innerHTML = html; t.hidden = false;
  const x = Math.min(ev.clientX + 14, innerWidth - t.offsetWidth - 8), y = Math.min(ev.clientY + 14, innerHeight - t.offsetHeight - 8);
  t.style.left = x + 'px'; t.style.top = y + 'px';
};
SV.pager = (el, total, skip, limit, go) => {
  const p = Math.floor(skip / limit) + 1, pages = Math.max(1, Math.ceil(total / limit));
  el.innerHTML = `<span>${SV.num(total)}건 · ${p}/${pages} 페이지</span>
    <button class="btn" ${p <= 1 ? 'disabled' : ''} data-s="${skip - limit}"><i class="fa-solid fa-chevron-left"></i></button>
    <button class="btn" ${p >= pages ? 'disabled' : ''} data-s="${skip + limit}"><i class="fa-solid fa-chevron-right"></i></button>`;
  SV.$$('button', el).forEach(b => b.onclick = () => go(+b.dataset.s));
};

/* 한글 라벨 */
SV.SAT_BINS = [...Array(10)].map((_, i) => (i / 10).toFixed(1));  // 만족도 0.1 폭 구간 하한
SV.FEATURES = ['laptopAllowed', 'keyboardAllowed', 'partition', 'outletAvailable', 'windowProximity', 'toiletProximity',
  'entranceProximity', 'brightness', 'airflow', 'deskHeight', 'deskWidth', 'viewQuality'];
SV.BINARY = new Set(['laptopAllowed', 'keyboardAllowed', 'partition', 'outletAvailable']);
SV.L = {
  laptopAllowed: '노트북 허용', keyboardAllowed: '키보드 허용', partition: '칸막이', outletAvailable: '콘센트',
  windowProximity: '창문 근접', toiletProximity: '화장실 근접', entranceProximity: '출입구 근접', brightness: '밝기',
  airflow: '직접 바람', deskHeight: '책상 높이', deskWidth: '책상 폭', viewQuality: '전망',
  morning: '아침 10시', noon: '정오 13시', evening: '저녁 19시',
  summer: '여름', winter: '겨울', spring_fall: '봄·가을', clear: '맑음', cloudy: '흐림', rain: '비',
  individual_carrel: '개인 칸막이석', long_shared_table: '긴 공용 책상', four_person_table: '4인 테이블',
  wide_individual_desk: '넓은 개인 책상', window_counter: '창가 바 테이블', standing_desk: '스탠딩 책상',
  laptop_worker: '노트북 작업형', exam_focus: '시험 집중형', comfort_seeker: '쾌적 추구형', heat_sensitive: '더위 민감형',
  frequent_break: '잦은 휴식형', casual_reader: '가벼운 독서형',
  restroom: '화장실', lighting: '조명', air_conditioning: '냉방', desk: '책상', laptop_usage: '노트북', outlet: '콘센트', view: '전망',
  recommendation: '추천 내 선택', outsideRecommendations: '추천 외 선택', none: '선택 없음',
  recommendationExposed: '추천 노출', candidateSelected: '좌석 선택', useConfirmed: '이용 확인', ratingSubmitted: '평가 제출', candidateRejected: '추천 거절',
  ceiling_cassette: '천장형', wall_mounted: '벽걸이', standing: '스탠드형', wall: '벽 콘센트', 'desk-outlet': '책상 콘센트',
  active: '학습 대상', backup: '보관(좌표 없음)',
};
SV.l = (k) => SV.L[k] ?? k;

/* 색상 스케일 (viridis 근사) */
SV.ramp = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'];
SV.rampHeat = ['#1a1a40', '#5a2a82', '#c43c6b', '#f68d3c', '#fde89a'];
SV.rampCool = ['#f4f8ff', '#bcd7f5', '#6ea8e6', '#2f6fc0', '#173c7a'];
SV.color = (t, ramp = SV.ramp) => {
  if (t === null || t === undefined || Number.isNaN(t)) return '#888';
  t = Math.max(0, Math.min(1, t)); const n = ramp.length - 1, i = Math.min(n - 1, Math.floor(t * n)), f = t * n - i;
  const a = ramp[i], b = ramp[i + 1], h = (s, o) => parseInt(s.substr(o, 2), 16);
  const c = (o) => Math.round(h(a, o) + (h(b, o) - h(a, o)) * f).toString(16).padStart(2, '0');
  return '#' + c(1) + c(3) + c(5);
};
SV.gradCss = (ramp) => `linear-gradient(90deg, ${ramp.join(',')})`;

SV.$('#themeBtn')?.addEventListener('click', () => {
  const d = document.documentElement, next = d.dataset.theme === 'dark' ? 'light' : 'dark';
  d.dataset.theme = next; try { localStorage.setItem('sv-theme', next); } catch (e) {}
  document.dispatchEvent(new Event('sv-theme'));
});
