/* 열람실 평면 렌더러 — 합성 시나리오(환경 객체)와 실좌석 bbox를 위에서 내려다본 도서관처럼 그린다.
   좌표: 원본 지도 픽셀. 크기는 시나리오의 synthetic_meters_per_px 로 미터 → 픽셀 환산. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const DESK_FILL = {
    individual_carrel: 'url(#wood)', long_shared_table: 'url(#woodL)', four_person_table: 'url(#woodL)',
    wide_individual_desk: 'url(#wood)', window_counter: 'url(#woodPale)', standing_desk: 'url(#metal)',
  };
  const RAW_METRICS = {
    'raw.lux': { label: '조도 lux(시간대)', get: (s, slot) => s.raw.lux[slot] },
    'raw.temperature_c': { label: '온도 °C', get: s => s.raw.temperature_c },
    'raw.noise_index': { label: '소음 지수', get: s => s.raw.noise_index },
    'raw.dist_toilet_walk_m': { label: '화장실 보행거리 m', get: s => s.raw.dist_toilet_walk_m, invert: true },
    'raw.dist_window_m': { label: '창문 거리 m', get: s => s.raw.dist_window_m, invert: true },
    'raw.dist_outlet_m': { label: '콘센트 거리 m', get: s => s.raw.dist_outlet_m, invert: true },
    'raw.dist_ac_m': { label: '냉방기 거리 m', get: s => s.raw.dist_ac_m, invert: true },
    'usage.chosen': { label: '선택 횟수(이 시나리오)', get: s => s.usage?.chosen ?? 0 },
    'usage.exposed': { label: '추천 노출 횟수', get: s => s.usage?.exposed ?? 0 },
    'usage.avg_satisfaction': { label: '평균 만족도', get: s => s.usage?.avg_satisfaction },
  };
  SV.RAW_METRICS = RAW_METRICS;

  class Floor {
    constructor(stage, opts = {}) {
      this.stage = stage; this.opts = Object.assign({ layers: new Set(['floor', 'desks', 'windows', 'toilets', 'ac', 'outlets', 'fixtures', 'glow', 'labels', 'real']), heat: 'none', colorBy: 'none', slot: 'noon', highlight: {} }, opts);
      stage.innerHTML = `<svg xmlns="${NS}"></svg><div class="hud"></div>
        <div class="zoom"><button data-z="in" title="확대"><i class="fa-solid fa-plus"></i></button><button data-z="out" title="축소"><i class="fa-solid fa-minus"></i></button><button data-z="fit" title="맞춤"><i class="fa-solid fa-expand"></i></button></div>
        <div class="legend" hidden></div>`;
      this.svg = stage.querySelector('svg'); this.hud = stage.querySelector('.hud'); this.legend = stage.querySelector('.legend');
      stage.querySelectorAll('.zoom button').forEach(b => b.onclick = () => b.dataset.z === 'fit' ? this.fit() : this.zoom(b.dataset.z === 'in' ? 0.8 : 1.25));
      this._pan();
    }

    set(opts) { Object.assign(this.opts, opts); if (this.data) this.render(); }

    setData(data) { this.data = data; this._prep(); this.fit(); this.render(); }

    _prep() {
      const sc = this.data.scenario, b = sc.room_bounds_px, M = 1 / sc.synthetic_meters_per_px;
      this.M = M; this.b = b;
      let [x0, y0, x1, y1] = [b[0] - 2.5 * M, b[1] - 2.5 * M, b[2] + 2.5 * M, b[3] + 2.5 * M];
      for (const o of sc.objects) if (o.type === 'toilet') {
        x0 = Math.min(x0, o.point[0] - 3 * M); y0 = Math.min(y0, o.point[1] - 3 * M);
        x1 = Math.max(x1, o.point[0] + 3 * M); y1 = Math.max(y1, o.point[1] + 3 * M);
      }
      this.building = [x0, y0, x1, y1];
      this.seatById = Object.fromEntries(this.data.seats.map(s => [s.seat_id, s]));
    }

    fit() {
      const [x0, y0, x1, y1] = this.building, p = 1.5 * this.M;
      this.vb = [x0 - p, y0 - p, x1 - x0 + 2 * p, y1 - y0 + 2 * p]; this._applyVb();
    }
    zoom(f, cx, cy) {
      const v = this.vb; cx ??= v[0] + v[2] / 2; cy ??= v[1] + v[3] / 2;
      this.vb = [cx - (cx - v[0]) * f, cy - (cy - v[1]) * f, v[2] * f, v[3] * f]; this._applyVb();
    }
    _applyVb() { this.svg.setAttribute('viewBox', this.vb.map(n => n.toFixed(2)).join(' ')); }
    _pt(ev) { const p = this.svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY; return p.matrixTransform(this.svg.getScreenCTM().inverse()); }
    _pan() {
      let drag = null;
      this.svg.addEventListener('wheel', ev => { if (!this.vb) return; ev.preventDefault(); const p = this._pt(ev); this.zoom(ev.deltaY < 0 ? 0.87 : 1.15, p.x, p.y); }, { passive: false });
      this.svg.addEventListener('pointerdown', ev => { if (!this.vb) return; drag = { x: ev.clientX, y: ev.clientY, vb: [...this.vb], moved: false }; });
      addEventListener('pointermove', ev => {
        if (!drag) return; const r = this.svg.getBoundingClientRect(), s = Math.max(drag.vb[2] / r.width, drag.vb[3] / r.height);
        const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; this.svg.classList.add('drag'); }
        this.vb = [drag.vb[0] - dx * s, drag.vb[1] - dy * s, drag.vb[2], drag.vb[3]]; this._applyVb();
      });
      addEventListener('pointerup', () => { if (drag) { this._justDragged = drag.moved; drag = null; this.svg.classList.remove('drag'); } });
      this.svg.addEventListener('click', ev => {
        if (this._justDragged) { this._justDragged = false; return; }
        const g = ev.target.closest('[data-seat]'); if (g && this.opts.onSeat) this.opts.onSeat(this.seatById[g.dataset.seat]);
      });
      this.svg.addEventListener('mousemove', ev => {
        const g = ev.target.closest('[data-tip]'); if (!g) { SV.tip(null); return; }
        if (g.dataset.seat) { SV.tip(this._seatTip(this.seatById[g.dataset.seat]), ev); return; }
        SV.tip(g.dataset.tip, ev);
      });
      this.svg.addEventListener('mouseleave', () => SV.tip(null));
    }

    _seatTip(s) {
      const f = s.features, slot = this.opts.slot;
      return `<b>${SV.esc(s.seat_label ?? s.seat_id.split(':').pop())}</b> · ${SV.l(s.raw.desk_type)}<br>
        밝기 ${s.brightness_by_slot[slot].toFixed(2)} (${s.raw.lux[slot]} lux) · 바람 ${f.airflow.toFixed(2)}<br>
        창문 ${s.raw.dist_window_m?.toFixed(1) ?? '-'}m · 화장실 ${s.raw.dist_toilet_walk_m.toFixed(1)}m<br>
        콘센트 ${f.outletAvailable ? '있음' : '없음'} · 칸막이 ${f.partition ? '있음' : '없음'} · ${s.raw.temperature_c}°C
        ${s.usage ? `<br>선택 ${s.usage.chosen}회 · 노출 ${s.usage.exposed}회` : ''}`;
    }

    _metric() {
      const cb = this.opts.colorBy, slot = this.opts.slot;
      if (!cb || cb === 'none') return null;
      if (SV.FEATURES.includes(cb)) {
        const get = cb === 'brightness' ? s => s.brightness_by_slot[slot] : s => s.features[cb];
        return { label: SV.l(cb), get, min: 0, max: 1, binary: SV.BINARY.has(cb) };
      }
      const r = RAW_METRICS[cb]; if (!r) return null;
      const vals = this.data.seats.map(s => r.get(s, slot)).filter(v => v !== null && v !== undefined);
      return { label: r.label, get: s => r.get(s, slot), min: Math.min(...vals), max: Math.max(...vals), invert: r.invert };
    }

    render() {
      const sc = this.data.scenario, M = this.M, b = this.b, L = this.opts.layers, slot = this.opts.slot, hl = this.opts.highlight || {};
      const [bx0, by0, bx1, by1] = this.building, W = b[2] - b[0], H = b[3] - b[1];
      const o = (t) => sc.objects.filter(x => x.type === t);
      const ent = o('entrance')[0];
      const out = [];
      out.push(`<defs>
        <pattern id="carpet" width="${0.5 * M}" height="${0.5 * M}" patternUnits="userSpaceOnUse"><rect width="${0.5 * M}" height="${0.5 * M}" fill="#d8d2c4"/><path d="M0 0H${0.5 * M}V${0.5 * M}" fill="none" stroke="#cbc4b4" stroke-width="${0.02 * M}"/></pattern>
        <pattern id="corr" width="${1.2 * M}" height="${1.2 * M}" patternUnits="userSpaceOnUse"><rect width="${1.2 * M}" height="${1.2 * M}" fill="#b9b5ad"/><path d="M0 0H${1.2 * M}V${1.2 * M}" fill="none" stroke="#a9a59c" stroke-width="${0.03 * M}"/></pattern>
        <pattern id="tile" width="${0.3 * M}" height="${0.3 * M}" patternUnits="userSpaceOnUse"><rect width="${0.3 * M}" height="${0.3 * M}" fill="#e6eef3"/><path d="M0 0H${0.3 * M}V${0.3 * M}" fill="none" stroke="#c4d3dc" stroke-width="${0.02 * M}"/></pattern>
        <linearGradient id="wood" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#c99a64"/><stop offset="1" stop-color="#a97945"/></linearGradient>
        <linearGradient id="woodL" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#d7b07c"/><stop offset="1" stop-color="#b88d58"/></linearGradient>
        <linearGradient id="woodPale" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e6cfa5"/><stop offset="1" stop-color="#cdb07e"/></linearGradient>
        <linearGradient id="metal" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#c3c8cd"/><stop offset="1" stop-color="#959ca4"/></linearGradient>
        <radialGradient id="glow"><stop offset="0" stop-color="#fff6c8" stop-opacity=".75"/><stop offset=".6" stop-color="#fff2b0" stop-opacity=".18"/><stop offset="1" stop-color="#fff2b0" stop-opacity="0"/></radialGradient>
        <radialGradient id="air"><stop offset="0" stop-color="#4aa3ff" stop-opacity=".55"/><stop offset="1" stop-color="#4aa3ff" stop-opacity="0"/></radialGradient>
        <clipPath id="roomClip"><rect x="${b[0]}" y="${b[1]}" width="${W}" height="${H}"/></clipPath>
        <filter id="sh" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="${0.05 * M}" dy="${0.07 * M}" stdDeviation="${0.06 * M}" flood-opacity=".35"/></filter>
      </defs>`);
      // 건물 바닥(복도) + 외부
      out.push(`<rect x="${bx0 - 50 * M}" y="${by0 - 50 * M}" width="${bx1 - bx0 + 100 * M}" height="${by1 - by0 + 100 * M}" fill="#2b2d31"/>`);
      out.push(`<rect x="${bx0}" y="${by0}" width="${bx1 - bx0}" height="${by1 - by0}" fill="url(#corr)" stroke="#1f2023" stroke-width="${0.35 * M}"/>`);

      // 화장실 + 보행 경로
      if (L.has('toilets')) for (const t of o('toilet')) {
        const [tx, ty] = t.point, w = 3 * M, h = 2.4 * M;
        if (ent) out.push(`<polyline points="${ent.point[0]},${ent.point[1]} ${ent.point[0]},${ty} ${tx},${ty}" fill="none" stroke="#2f9c8a" stroke-width="${0.12 * M}" stroke-dasharray="${0.4 * M} ${0.25 * M}" opacity=".85"/>`);
        out.push(`<g data-tip="${SV.esc(`<b>화장실</b> (${t.origin === 'synthetic' ? '합성' : '실데이터 후보'})`)}">
          <rect x="${tx - w / 2}" y="${ty - h / 2}" width="${w}" height="${h}" fill="url(#tile)" stroke="#33363b" stroke-width="${0.18 * M}" filter="url(#sh)"/>
          <text x="${tx}" y="${ty + 0.1 * M}" font-size="${0.9 * M}" text-anchor="middle" dominant-baseline="middle" font-family="'Font Awesome 6 Free'" font-weight="900" fill="#3b6f9c">&#xf7bd;</text>
          <text x="${tx}" y="${ty + h / 2 - 0.25 * M}" font-size="${0.38 * M}" text-anchor="middle" fill="#3b6f9c" font-weight="700">WC</text></g>`);
      }

      // 열람실 바닥
      out.push(`<rect x="${b[0]}" y="${b[1]}" width="${W}" height="${H}" fill="url(#carpet)"/>`);

      // 창 채광(시간대)
      const dayF = { morning: 0.7, noon: 1, evening: 0 }[slot] * ({ clear: 1, cloudy: 0.55, rain: 0.3 }[sc.conditions.weather] ?? 1);
      if (L.has('glow') && dayF > 0) for (const w of o('window')) {
        if (w.blind_closed) continue;
        const [[ax, ay], [cx, cy]] = w.line, d = 3 * M, dir = { top: [0, 1], bottom: [0, -1], left: [1, 0], right: [-1, 0] }[w.wall];
        const gx = dir[0] ? (dir[0] > 0 ? '0' : '1') : '0', gid = 'dl' + Math.random().toString(36).slice(2, 7);
        const x = Math.min(ax, cx) - (dir[0] < 0 ? d : 0), y = Math.min(ay, cy) - (dir[1] < 0 ? d : 0);
        const ww = dir[0] ? d : Math.abs(cx - ax), hh = dir[1] ? d : Math.abs(cy - ay);
        out.push(`<linearGradient id="${gid}" x1="${dir[0] < 0 ? 1 : 0}" y1="${dir[1] < 0 ? 1 : 0}" x2="${dir[0] > 0 ? 1 : 0}" y2="${dir[1] > 0 ? 1 : 0}"><stop offset="0" stop-color="#fff9de" stop-opacity="${0.55 * dayF}"/><stop offset="1" stop-color="#fff9de" stop-opacity="0"/></linearGradient>`);
        out.push(`<rect x="${x}" y="${y}" width="${ww}" height="${hh}" fill="url(#${gid})" clip-path="url(#roomClip)"/>`);
        void gx;
      }

      // 히트맵
      if (this.opts.heat !== 'none') out.push(this._heat());

      // 조명 글로우
      if (L.has('glow')) {
        const lf = slot === 'evening' ? 1 : 0.6;
        out.push(`<g clip-path="url(#roomClip)" style="mix-blend-mode:screen">` + o('ceiling_light').map(l =>
          `<circle cx="${l.point[0]}" cy="${l.point[1]}" r="${1.9 * M}" fill="url(#glow)" opacity="${Math.min(1, l.lux_at_desk_below / 650 * lf).toFixed(2)}"/>`).join('') + '</g>');
      }

      // 냉방 바람
      if (L.has('airflow')) for (const a of o('air_conditioner')) {
        const [x, y] = a.point, r = a.throw_m * M;
        if (a.direction) {
          const [dx, dy] = a.direction, ang = Math.atan2(dy, dx), sp = 0.62, R = r * 1.4;
          const p1 = [x + R * Math.cos(ang - sp), y + R * Math.sin(ang - sp)], p2 = [x + R * Math.cos(ang + sp), y + R * Math.sin(ang + sp)];
          out.push(`<path d="M${x} ${y}L${p1}A${R} ${R} 0 0 1 ${p2}Z" fill="url(#air)" opacity="${a.strength}" clip-path="url(#roomClip)"/>`);
        } else out.push(`<circle cx="${x}" cy="${y}" r="${r}" fill="url(#air)" opacity="${a.strength}" clip-path="url(#roomClip)"/>`);
      }

      // 책상·좌석
      const metric = this._metric();
      if (L.has('desks')) {
        for (const g of sc.desk_groups) {
          const ss = g.seat_ids.map(id => this.seatById[id]).filter(s => s?.bbox); if (!ss.length) continue;
          const x0 = Math.min(...ss.map(s => s.bbox.x)), y0 = Math.min(...ss.map(s => s.bbox.y));
          const x1 = Math.max(...ss.map(s => s.bbox.x + s.bbox.width)), y1 = Math.max(...ss.map(s => s.bbox.y + s.bbox.height));
          const pad = 0.04 * M;
          out.push(`<rect x="${x0 - pad}" y="${y0 - pad}" width="${x1 - x0 + 2 * pad}" height="${y1 - y0 + 2 * pad}" rx="${0.06 * M}" fill="${DESK_FILL[g.desk_type] || 'url(#wood)'}" stroke="#6e4f2c" stroke-width="${0.03 * M}" filter="url(#sh)"/>`);
        }
        for (const s of this.data.seats) {
          if (!s.bbox) continue;
          const { x, y, width: w, height: h } = s.bbox, cx = x + w / 2, cy = y + h / 2, cs = Math.min(w, h);
          let fill = 'none', op = 0;
          if (metric) {
            let v = metric.get(s);
            if (v !== null && v !== undefined) { v = (v - metric.min) / ((metric.max - metric.min) || 1); if (metric.invert) v = 1 - v; fill = metric.binary ? (v > 0.5 ? '#3fbf7f' : '#d9534f') : SV.color(v); op = 0.88; }
            else { fill = '#777'; op = 0.6; }
          }
          const part = s.raw.partition ? `<rect x="${x + 0.02 * M}" y="${y + 0.02 * M}" width="${w - 0.04 * M}" height="${h - 0.04 * M}" fill="none" stroke="#5b4630" stroke-width="${0.05 * M}"/>` : `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="#8a6a44" stroke-width="${0.012 * M}" opacity=".7"/>`;
          const chair = `<rect x="${cx - cs * 0.24}" y="${cy - cs * 0.24}" width="${cs * 0.48}" height="${cs * 0.48}" rx="${cs * 0.14}" fill="#3e4d5f" opacity=".92"/><rect x="${cx - cs * 0.24}" y="${cy + cs * 0.14}" width="${cs * 0.48}" height="${cs * 0.1}" rx="${cs * 0.04}" fill="#2a3644"/>`;
          const lbl = L.has('labels') && s.seat_label ? `<text x="${cx}" y="${cy - cs * 0.02}" font-size="${cs * 0.28}" text-anchor="middle" dominant-baseline="middle" fill="#fff" font-weight="700" style="pointer-events:none">${SV.esc(s.seat_label)}</text>` : '';
          const h1 = hl[s.seat_id];
          let ring = '';
          if (h1 && h1.kind === 'occupied') {  // 그 시점 다른 사람이 사용 중 → 선택 불가
            const d = 0.12 * Math.min(w, h);
            ring = `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#b8473a" opacity=".55"/><path d="M${x + d} ${y + d}L${x + w - d} ${y + h - d}M${x + w - d} ${y + d}L${x + d} ${y + h - d}" stroke="#ffd6d3" stroke-width="${0.07 * M}" stroke-linecap="round"/>`;
          } else if (h1) {
            const col = { chosen: '#2fd27a', exposed: '#ffb547', negative: '#7fb4ff', history: '#c58cff', best: '#ff5cd6' }[h1.kind] || '#fff';
            const dash = h1.kind === 'negative' || h1.kind === 'history';
            ring = `<rect x="${x - 0.08 * M}" y="${y - 0.08 * M}" width="${w + 0.16 * M}" height="${h + 0.16 * M}" rx="${0.1 * M}" fill="none" stroke="${col}" stroke-width="${(h1.kind === 'chosen' || h1.kind === 'best' ? 0.16 : 0.1) * M}" ${dash ? `stroke-dasharray="${0.15 * M} ${0.1 * M}"` : ''}/>`;
            if (h1.badge) ring += `<circle cx="${x}" cy="${y + h}" r="${0.26 * M}" fill="${col}" stroke="#222" stroke-width="${0.03 * M}"/><text x="${x}" y="${y + h + 0.01 * M}" font-size="${0.28 * M}" text-anchor="middle" dominant-baseline="middle" font-weight="800" fill="#222">${SV.esc(h1.badge)}</text>`;
            if (h1.rank) ring += `<circle cx="${x + w}" cy="${y}" r="${0.28 * M}" fill="${col}" stroke="#222" stroke-width="${0.03 * M}"/><text x="${x + w}" y="${y + 0.01 * M}" font-size="${0.32 * M}" text-anchor="middle" dominant-baseline="middle" font-weight="800" fill="#222">${h1.rank}</text>`;
            if (h1.kind === 'chosen') ring += `<text x="${x}" y="${y}" font-size="${0.55 * M}" text-anchor="middle" dominant-baseline="middle" font-family="'Font Awesome 6 Free'" font-weight="900" fill="#2fd27a" stroke="#123" stroke-width="${0.02 * M}">&#xf005;</text>`;
          }
          out.push(`<g data-seat="${SV.esc(s.seat_id)}" data-tip="1" style="cursor:pointer">${part}${metric ? `<rect x="${x + 0.03 * M}" y="${y + 0.03 * M}" width="${w - 0.06 * M}" height="${h - 0.06 * M}" fill="${fill}" opacity="${op}"/>` : ''}${metric ? '' : chair}${lbl}${ring}</g>`);
        }
      }

      // 콘센트
      if (L.has('outlets')) for (const c of o('outlet')) {
        const [x, y] = c.point, r = (c.kind === 'desk' ? 0.16 : 0.13) * M;
        out.push(`<g data-tip="${SV.esc(`<b>${c.kind === 'desk' ? '책상 콘센트' : '벽 콘센트'}</b> (합성)`)}"><circle cx="${x}" cy="${y}" r="${r}" fill="#f6c343" stroke="#6b4e00" stroke-width="${0.03 * M}"/><path d="M${x + 0.02 * M} ${y - 0.1 * M}L${x - 0.05 * M} ${y + 0.01 * M}H${x + 0.01 * M}L${x - 0.02 * M} ${y + 0.1 * M}L${x + 0.06 * M} ${y - 0.02 * M}H${x}Z" fill="#6b4e00"/></g>`);
      }

      // 벽
      out.push(`<rect x="${b[0]}" y="${b[1]}" width="${W}" height="${H}" fill="none" stroke="#26272b" stroke-width="${0.28 * M}"/>`);
      // 창문
      if (L.has('windows')) for (const w of o('window')) {
        const [[ax, ay], [cx, cy]] = w.line;
        out.push(`<g data-tip="${SV.esc(`<b>창문</b> 전망 ${w.view_score.toFixed(2)}${w.blind_closed ? ' · 블라인드 닫힘' : ''} (합성)`)}">
          <line x1="${ax}" y1="${ay}" x2="${cx}" y2="${cy}" stroke="${w.blind_closed ? '#8e9297' : '#9fd8f5'}" stroke-width="${0.3 * M}" ${w.blind_closed ? `stroke-dasharray="${0.12 * M} ${0.06 * M}"` : ''}/>
          <line x1="${ax}" y1="${ay}" x2="${cx}" y2="${cy}" stroke="#ffffff" stroke-width="${0.05 * M}" opacity=".9"/></g>`);
      }
      // 출입구
      if (ent) {
        const [x, y] = ent.point, d = 1.1 * M, horiz = ent.wall === 'top' || ent.wall === 'bottom';
        const into = { top: 1, bottom: -1, left: 1, right: -1 }[ent.wall] ?? 1;
        out.push(`<g data-tip="${SV.esc(`<b>출입구</b> (${ent.origin === 'synthetic' ? '합성' : '실데이터 후보'})`)}">` + (horiz
          ? `<rect x="${x - d / 2}" y="${y - 0.2 * M}" width="${d}" height="${0.4 * M}" fill="#d8d2c4"/><path d="M${x - d / 2} ${y}L${x - d / 2} ${y + into * d}A${d} ${d} 0 0 ${into > 0 ? 0 : 1} ${x + d / 2} ${y}" fill="rgba(255,255,255,.15)" stroke="#444" stroke-width="${0.04 * M}"/>`
          : `<rect x="${x - 0.2 * M}" y="${y - d / 2}" width="${0.4 * M}" height="${d}" fill="#d8d2c4"/><path d="M${x} ${y - d / 2}L${x + into * d} ${y - d / 2}A${d} ${d} 0 0 ${into > 0 ? 1 : 0} ${x} ${y + d / 2}" fill="rgba(255,255,255,.15)" stroke="#444" stroke-width="${0.04 * M}"/>`)
          + `<text x="${x}" y="${y}" font-size="${0.45 * M}" text-anchor="middle" dominant-baseline="middle" font-family="'Font Awesome 6 Free'" font-weight="900" fill="#c0392b">&#xf52b;</text></g>`);
      }

      // 천장 설비: 조명 기구·냉방기
      if (L.has('fixtures')) for (const l of o('ceiling_light')) {
        const bad = l.lux_at_desk_below < 300;
        out.push(`<rect data-tip="${SV.esc(`<b>천장 조명</b> 직하 ${Math.round(l.lux_at_desk_below)} lux${bad ? ' · 어두움/고장' : ''} (합성)`)}" x="${l.point[0] - 0.6 * M}" y="${l.point[1] - 0.09 * M}" width="${1.2 * M}" height="${0.18 * M}" rx="${0.05 * M}" fill="${bad ? '#5d5f63' : '#fffbe8'}" stroke="${bad ? '#3a3a3a' : '#d8c77a'}" stroke-width="${0.03 * M}" opacity=".9"/>`);
      }
      if (L.has('ac')) for (const a of o('air_conditioner')) {
        const [x, y] = a.point, tip = `data-tip="${SV.esc(`<b>냉방기</b> ${SV.l(a.kind)} · 세기 ${a.strength.toFixed(2)} · 도달 ${a.throw_m.toFixed(1)}m (합성)`)}"`;
        if (a.kind === 'ceiling_cassette') {
          const s = 0.9 * M;
          out.push(`<g ${tip}><rect x="${x - s / 2}" y="${y - s / 2}" width="${s}" height="${s}" rx="${0.06 * M}" fill="#f1f4f7" stroke="#6f7c8a" stroke-width="${0.04 * M}"/><rect x="${x - s * 0.22}" y="${y - s * 0.22}" width="${s * 0.44}" height="${s * 0.44}" fill="#d5dde5" stroke="#8a97a5" stroke-width="${0.02 * M}"/>
            ${[[0, -1], [0, 1], [-1, 0], [1, 0]].map(([dx, dy]) => `<line x1="${x + dx * s * 0.36 - dy * s * 0.18}" y1="${y + dy * s * 0.36 - dx * s * 0.18}" x2="${x + dx * s * 0.36 + dy * s * 0.18}" y2="${y + dy * s * 0.36 + dx * s * 0.18}" stroke="#4a90d9" stroke-width="${0.05 * M}"/>`).join('')}</g>`);
        } else if (a.kind === 'wall_mounted') {
          const [dx, dy] = a.direction || [0, 1], lw = 1.0 * M, th = 0.3 * M, horiz = dx === 0;
          const rx = horiz ? x - lw / 2 : x - (dx > 0 ? 0 : th), ry = horiz ? y - (dy > 0 ? 0 : th) : y - lw / 2;
          out.push(`<g ${tip}><rect x="${rx}" y="${ry}" width="${horiz ? lw : th}" height="${horiz ? th : lw}" rx="${0.06 * M}" fill="#f1f4f7" stroke="#6f7c8a" stroke-width="${0.04 * M}"/><line x1="${x}" y1="${y}" x2="${x + dx * 0.8 * M}" y2="${y + dy * 0.8 * M}" stroke="#4a90d9" stroke-width="${0.06 * M}" marker-end=""/></g>`);
        } else {
          out.push(`<g ${tip}><circle cx="${x}" cy="${y}" r="${0.3 * M}" fill="#f1f4f7" stroke="#6f7c8a" stroke-width="${0.04 * M}"/><circle cx="${x}" cy="${y}" r="${0.12 * M}" fill="#4a90d9"/></g>`);
        }
      }

      // 실데이터 시설(같은 지도)
      if (L.has('real')) for (const f of this.data.real_facilities || []) out.push(SV.facilitySvg(f, M));

      // 저녁: 실내 어둡게
      if (slot === 'evening') out.push(`<rect x="${bx0}" y="${by0}" width="${bx1 - bx0}" height="${by1 - by0}" fill="#0b1030" opacity=".22" style="pointer-events:none"/>`);

      // 축척 막대(5m)
      const sx = bx0 + 0.8 * M, sy = by1 - 0.8 * M;
      out.push(`<g style="pointer-events:none"><rect x="${sx - 0.2 * M}" y="${sy - 0.8 * M}" width="${5.4 * M}" height="${1.1 * M}" rx="${0.15 * M}" fill="rgba(0,0,0,.45)"/><line x1="${sx}" y1="${sy}" x2="${sx + 5 * M}" y2="${sy}" stroke="#fff" stroke-width="${0.1 * M}"/><text x="${sx + 2.5 * M}" y="${sy - 0.3 * M}" font-size="${0.4 * M}" fill="#fff" text-anchor="middle">5 m (합성 축척)</text></g>`);

      this.svg.innerHTML = out.join('');
      this._hud(); this._legend(metric);
    }

    _heat() {
      const sc = this.data.scenario, g = sc.brightness_grid, mode = this.opts.heat, M = this.M;
      const [ox, oy] = g.origin_px, [cw, ch] = g.cell_px, out = [];
      let vals;
      if (mode === 'brightness') vals = g.values.map(r => r.map(v => Math.max(0, Math.min(1, (v - 100) / 900))));
      else {
        const acs = sc.objects.filter(o => o.type === 'air_conditioner');
        vals = g.values.map((r, j) => r.map((_, i) => {
          const px = ox + (i + 0.5) * cw, py = oy + (j + 0.5) * ch; let t = 0;
          for (const a of acs) {
            const dx = (px - a.point[0]) / M, dy = (py - a.point[1]) / M, d = Math.hypot(dx, dy);
            if (a.direction) { const c = (dx * a.direction[0] + dy * a.direction[1]) / (d + 1e-9); t += a.strength * Math.max(0, c) ** 2 * Math.exp(-d / a.throw_m); }
            else t += a.strength * Math.exp(-((d / a.throw_m) ** 2));
          }
          return Math.min(1, t);
        }));
      }
      const ramp = mode === 'brightness' ? SV.rampHeat : SV.rampCool;
      vals.forEach((r, j) => r.forEach((v, i) => out.push(`<rect x="${ox + i * cw}" y="${oy + j * ch}" width="${cw + 0.5}" height="${ch + 0.5}" fill="${SV.color(v, ramp)}"/>`)));
      return `<g opacity=".58" clip-path="url(#roomClip)" style="pointer-events:none">${out.join('')}</g>`;
    }

    _hud() {
      const sc = this.data.scenario, c = sc.conditions;
      this.hud.innerHTML = `<span class="badge">${SV.esc(sc.library_name)} · ${SV.esc(sc.room_name || sc.room_id.split(':').pop())}</span>
        <span class="badge">시나리오 #${sc.scenario_index}</span><span class="badge">${SV.l(c.season)} · ${SV.l(c.weather)} · ${c.base_temperature_c}°C</span>
        <span class="badge">${SV.l(this.opts.slot)}</span><span class="badge">노트북 ${c.laptop_allowed ? '허용' : '금지'}</span><span class="badge" style="background:rgba(201,133,43,.85)">합성 환경</span>`;
    }

    _legend(metric) {
      const parts = [];
      if (metric) {
        if (metric.binary) parts.push(`<div><b>${SV.esc(metric.label)}</b><br><span class="sw"><i style="background:#3fbf7f"></i>1 (있음/허용)</span><span class="sw"><i style="background:#d9534f"></i>0</span></div>`);
        else parts.push(`<div><b>좌석 색: ${SV.esc(metric.label)}</b>${metric.invert ? ' <span style="opacity:.7">(가까울수록 밝은색)</span>' : ''}<div class="ramp" style="background:${SV.gradCss(SV.ramp)}"></div><div class="ends"><span>${metric.invert ? (+metric.max).toFixed(1) : (+metric.min).toFixed(2)}</span><span>${metric.invert ? (+metric.min).toFixed(1) : (+metric.max).toFixed(2)}</span></div></div>`);
      }
      if (this.opts.heat === 'brightness') parts.push(`<div style="margin-top:6px"><b>바닥 히트맵: 정오 조도</b><div class="ramp" style="background:${SV.gradCss(SV.rampHeat)}"></div><div class="ends"><span>100 lux</span><span>1000+</span></div></div>`);
      if (this.opts.heat === 'airflow') parts.push(`<div style="margin-top:6px"><b>바닥 히트맵: 직접 바람</b><div class="ramp" style="background:${SV.gradCss(SV.rampCool)}"></div><div class="ends"><span>0</span><span>1</span></div></div>`);
      this.legend.hidden = !parts.length; this.legend.innerHTML = parts.join('');
    }
  }
  SV.Floor = Floor;

  /* 실데이터 시설 기하 → SVG */
  SV.FAC_COLOR = { toilet: '#3b8fd9', entrance: '#e0533d', elevator: '#8e5bd6', stairs: '#d98e2b', ev: '#2fae68' };
  SV.facilitySvg = (f, M) => {
    const g = f.geometry || {}, col = SV.FAC_COLOR[f.type] || '#e04fa0', tip = `data-tip="${SV.esc(`<b>${SV.esc(f.type)}</b> ${SV.esc(f.attributes?.label || '')}<br><span style='opacity:.8'>실데이터 · ${SV.esc(f.verification)}</span>`)}"`;
    const sw = 0.12 * M;
    if (g.type === 'Point') { const [x, y] = g.coordinates, r = 0.4 * M; return `<g ${tip}><path d="M${x} ${y - r}L${x + r} ${y}L${x} ${y + r}L${x - r} ${y}Z" fill="${col}" stroke="#fff" stroke-width="${0.06 * M}"/></g>`; }
    if (g.type === 'LineString') return `<g ${tip}><polyline points="${g.coordinates.map(p => p.join(',')).join(' ')}" fill="none" stroke="${col}" stroke-width="${sw * 2}" stroke-linecap="round" opacity=".9"/></g>`;
    if (g.type === 'Polygon') return `<g ${tip}><polygon points="${(Array.isArray(g.coordinates[0][0]) ? g.coordinates[0] : g.coordinates).map(p => p.join(',')).join(' ')}" fill="${col}" fill-opacity=".35" stroke="${col}" stroke-width="${sw}"/></g>`;
    if (f.access_point) { const [x, y] = f.access_point; return `<g ${tip}><circle cx="${x}" cy="${y}" r="${0.35 * M}" fill="${col}"/></g>`; }
    return '';
  };
})();
