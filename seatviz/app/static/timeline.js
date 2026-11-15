/* 사용자별 시계열 좌석 선택 시각화 — /api/users/{uid}/timeline 응답을 그린다.
   1) 열람실 스윔레인: 언제 어느 열람실의 어느 좌석을 골랐는지(색=만족도, 모양=선택 경로)
   2) 만족도 추이: 세션별 만족도 + 이동평균
   3) 취향 학습 곡선: 누적 평균(선택 좌석 특성 − 같은 세션 가용 좌석 평균). 이력이 쌓일수록 드러나는 취향 방향
   4) 평면 재생: 실좌석 배치 위에 선택 좌석을 시간 순서대로 재생 */
(function () {
  const { esc, l } = SV;
  const ORIGIN_STYLE = { recommendation: 'circle', outsideRecommendations: 'triangle' };
  const day = (iso) => iso.slice(5, 10).replace('-', '/');
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const LBL = (f) => f === 'quietness' ? '조용함(1−소음)' : l(f);

  SV.userTimeline = function (root, d) {
    const S = d.sessions, chosen = S.filter(s => s.chosen_seat_id);
    if (!chosen.length) { root.innerHTML = '<div class="card"><div class="muted">선택 이력 없음</div></div>'; return; }
    const rooms = d.rooms, rIdx = Object.fromEntries(rooms.map((r, i) => [r.room_id, i]));
    const t0 = Date.parse(S[0].occurred_at), t1 = Date.parse(S[S.length - 1].occurred_at);
    root.innerHTML = `
      <div class="card" style="margin-bottom:14px"><h2>시간순 좌석 선택 <span class="muted small">세션 ${S.length}건 · 선택 ${chosen.length}건 · ${esc(S[0].occurred_at.slice(0, 10))} ~ ${esc(S[S.length - 1].occurred_at.slice(0, 10))}</span></h2>
        <div class="small muted" style="margin-bottom:6px">점 색 = 만족도(0~1, 노랑일수록 높음) · ● 추천 내 선택 · ▲ 추천 외 선택 · × 선택 없음. 점을 누르면 세션 상세로 이동합니다.</div>
        <div style="height:${Math.max(180, 60 + rooms.length * 42)}px"><canvas id="tlLane"></canvas></div></div>
      <div class="grid g2" style="margin-bottom:14px">
        <div class="card"><h2>만족도 추이</h2><div class="chart-box" style="height:260px"><canvas id="tlSat"></canvas></div>
          <div class="small muted">점 = 세션 만족도, 선 = 최근 5회 이동평균, 점선 = 노이즈 전 취향 적합도.</div></div>
        <div class="card"><h2>이력에서 드러나는 취향 (학습 곡선)</h2><div class="chart-box" style="height:260px"><canvas id="tlLearn"></canvas></div>
          <div class="small muted">세션 n까지의 누적 평균(선택 좌석 값 − 같은 세션 가용 좌석 평균). 0보다 위면 그 특성이 큰 좌석을 일부러 고른 것. 잠재 가중치가 있는 특성만 표시.</div></div>
      </div>
      <div class="card" style="margin-bottom:14px"><h2>선택 이력으로 본 취향 vs 정답</h2><div class="tbl-wrap" id="tlTbl"></div></div>
      <div class="card" style="margin-bottom:14px"><h2>평면 재생 <span class="muted small">세션 시점의 합성 환경(창문·냉방 바람·조명·콘센트·화장실)과 사용 중 좌석, 선택 이유</span></h2>
        <div class="tabs" id="tlRooms">${rooms.map((r, i) => `<button data-i="${i}" class="${i ? '' : 'on'}">${esc(r.name || r.room_id)} <span class="muted small">${r.visits}회</span></button>`).join('')}</div>
        <div class="toolbar" style="align-items:center;gap:10px">
          <button class="btn" id="tlPlay"><i class="fa-solid fa-play"></i> 재생</button>
          <input type="range" id="tlStep" min="0" value="0" style="flex:1;min-width:160px">
          <span class="small mono" id="tlNow" style="min-width:260px"></span>
        </div>
        <div class="grid" style="grid-template-columns:minmax(0,1fr) 420px;gap:14px;align-items:start">
          <div class="stage short" id="tlStage" style="height:620px"></div>
          <div id="tlSide" class="small" style="max-height:620px;overflow:auto"></div>
        </div></div>`;

    // 1) 스윔레인
    const lanePts = S.map(s => ({ x: Date.parse(s.occurred_at), y: rIdx[s.room_id], s }));
    const lane = new Chart(SV.$('#tlLane'), {
      type: 'scatter',
      data: { datasets: [{ data: lanePts, pointRadius: 7, pointHoverRadius: 10,
        pointStyle: lanePts.map(p => ORIGIN_STYLE[p.s.choice_origin] || 'crossRot'),
        backgroundColor: lanePts.map(p => p.s.satisfaction == null ? '#999' : SV.color(p.s.satisfaction)),
        borderColor: lanePts.map(p => p.s.chosen_seat_id ? 'rgba(0,0,0,.35)' : '#b8473a'), borderWidth: 1 }] },
      options: { maintainAspectRatio: false, animation: false,
        scales: { x: { type: 'linear', min: t0 - 864e5, max: t1 + 864e5, ticks: { callback: v => day(new Date(v + 9 * 36e5).toISOString()), maxTicksLimit: 12 } },
                  y: { min: -0.6, max: rooms.length - 0.4, reverse: true, ticks: { stepSize: 1, callback: v => rooms[v] ? (rooms[v].name || rooms[v].room_id) : '' } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => { const s = c.raw.s; return [`${s.occurred_at.slice(0, 16).replace('T', ' ')} ${l(s.time_slot)}`, `좌석 ${s.chosen_seat_id ? s.chosen_seat_id.split(':').pop() : '없음'} · ${l(s.choice_origin ?? 'none')}${s.rank ? ' ' + s.rank + '위' : ''}`, `만족도 ${s.satisfaction ?? '-'} · 점유율 ${(s.occupancy_rate * 100).toFixed(0)}%`]; } } } },
        onClick: (e, els) => { if (els.length) location.href = SV.base + '/sessions/' + encodeURIComponent(lanePts[els[0].index].s._id); } },
    });

    // 2) 만족도 추이
    const sat = chosen.map((s, i) => ({ x: i + 1, y: s.satisfaction }));
    const ma = sat.map((p, i) => { const w = sat.slice(Math.max(0, i - 4), i + 1); return { x: p.x, y: w.reduce((a, b) => a + b.y, 0) / w.length }; });
    new Chart(SV.$('#tlSat'), { type: 'scatter', data: { datasets: [
      { label: '만족도', data: sat, backgroundColor: sat.map(p => SV.color(p.y)), pointRadius: 4 },
      { label: '이동평균(5)', data: ma, type: 'line', borderColor: css('--accent'), pointRadius: 0, borderWidth: 2, tension: .3 },
      { label: '취향 적합도', data: chosen.map((s, i) => ({ x: i + 1, y: s.chosen_preference_fit })), type: 'line', borderColor: css('--muted'), borderDash: [4, 4], pointRadius: 0, borderWidth: 1 }] },
      options: { maintainAspectRatio: false, animation: false, scales: { x: { title: { display: true, text: 'n번째 선택' } }, y: { min: 0, max: 1 } }, plugins: { legend: { position: 'bottom' } } } });

    // 3) 학습 곡선
    const W = d.latent_weights, feats = d.features.filter(f => W[f] || (f === 'quietness' && d.noise_aversion > 0));
    const pal = ['#2f6f5e', '#c9852b', '#3567a8', '#b8473a', '#7a5ea8', '#5b8f3a', '#d06aa0', '#4a9fb5', '#8a6d3b', '#555', '#e0a24e', '#1f9d6a', '#9a4fd1'];
    const usable = chosen.filter(s => s.chosen && s.available_mean);
    const curves = {}, final = {};
    feats.forEach(f => { let acc = 0; curves[f] = usable.map((s, i) => { acc += s.chosen[f] - s.available_mean[f]; return { x: i + 1, y: acc / (i + 1) }; }); final[f] = curves[f].length ? curves[f][curves[f].length - 1].y : null; });
    new Chart(SV.$('#tlLearn'), { type: 'line', data: { datasets: feats.map((f, i) => ({ label: LBL(f), data: curves[f], borderColor: pal[i % pal.length], backgroundColor: pal[i % pal.length], pointRadius: 0, borderWidth: W[f] ? 1 + 2.5 * W[f].weight : 1.5, tension: .2 })) },
      options: { maintainAspectRatio: false, animation: false, parsing: false, scales: { x: { type: 'linear', title: { display: true, text: '누적 선택 수' } }, y: { title: { display: true, text: '선택 − 가용 평균' } } }, plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } } } });

    // 표: 이력 방향 vs 잠재 target·가중치 vs 관측 프로필
    const prof = Object.fromEntries(d.profile.map(p => [p.featureId, p]));
    const expect = (t) => t >= 0.7 ? 1 : t <= 0.3 ? -1 : 0;
    SV.$('#tlTbl').innerHTML = `<table class="tbl"><thead><tr><th>특성</th><th class="num">정답 target</th><th class="num">정답 가중치</th><th>관측 프로필</th><th class="num">이력 신호(선택−가용)</th><th>판정</th></tr></thead><tbody>
      ${feats.map(f => { const w = f === 'quietness' ? { target: 1, weight: 0.5 * d.noise_aversion } : W[f], v = final[f], e = expect(w.target), sgn = v == null ? 0 : v > 0.02 ? 1 : v < -0.02 ? -1 : 0;
        const verdict = e === 0 ? '<span class="badge b-mute">중간 target</span>' : sgn === e ? '<span class="badge b-ok">이력이 정답 방향</span>' : sgn === 0 ? '<span class="badge b-mute">신호 약함</span>' : '<span class="badge b-bad">반대</span>';
        const pp = prof[f]; return `<tr><td>${LBL(f)}${f === 'quietness' ? ' <span class="muted small">(숨은 취향)</span>' : !pp ? ' <span class="muted small">(프로필에 없음)</span>' : ''}</td><td class="num">${w.target}</td><td class="num">${w.weight.toFixed(2)}</td>
          <td class="small">${pp ? `target ${pp.target} · 중요도 ${pp.importance.toFixed(2)}` : '<span class="muted">-</span>'}</td><td class="num" style="color:${v > 0 ? 'var(--ok)' : 'var(--danger)'}">${v == null ? '-' : (v > 0 ? '+' : '') + v.toFixed(3)}</td><td>${verdict}</td></tr>`; }).join('')}</tbody></table>
      <div class="small muted" style="margin-top:6px">정답 = 생성기의 잠재 효용(모델은 볼 수 없음). '프로필에 없음' 특성은 카카오톡 분석에 드러나지 않아 선택 이력에서만 배울 수 있는 취향입니다. 가용 평균은 세션에 저장된 비교 좌석(최대 20개) 표본 기준입니다.</div>`;

    // 4) 평면 재생 — 세션 시점의 합성 환경(창문·냉방기 바람 범위·조명·콘센트·화장실)과 사용 중 좌석, 선택 이유
    let room = 0, step = 0, timer = null, token = 0;
    const slider = SV.$('#tlStep'), scCache = new Map(), exCache = new Map();
    const floor = new SV.Floor(SV.$('#tlStage'), { layers: new Set(['floor', 'desks', 'windows', 'toilets', 'ac', 'airflow', 'outlets', 'fixtures', 'glow', 'real']) });
    const getSc = (id) => { if (!scCache.has(id)) scCache.set(id, SV.api('/api/scenarios/' + encodeURIComponent(id))); return scCache.get(id); };
    const getEx = (id) => { if (!exCache.has(id)) exCache.set(id, SV.api('/api/sessions/' + encodeURIComponent(id) + '/explain')); return exCache.get(id); };
    const legend = `<span class="sw"><i style="background:#2fd27a"></i>이번 선택</span><span class="sw"><i style="background:#ffb547"></i>추천 노출(순위)</span>
      <span class="sw"><i style="background:#ff5cd6"></i>취향상 최고 빈자리</span><span class="sw"><i style="background:#c58cff"></i>예전에 고른 좌석(횟수)</span>
      <span class="sw"><i style="background:#b8473a"></i>✕ 다른 사람 사용 중</span><span class="sw"><i style="background:#4aa3ff"></i>냉방 바람 범위</span><span class="sw"><i style="background:#9fd3ff"></i>창문</span>`;
    function itemsTable(ex) {
      const mx = Math.max(0.01, ...ex.items.map(x => Math.abs(x.contribution)));
      return `<table class="tbl" style="margin-top:8px"><thead><tr><th>특성</th><th class="num">선택</th><th class="num">빈자리 평균</th><th class="num">원함</th><th>기여(선택−평균)</th></tr></thead><tbody>
        ${ex.items.map(x => `<tr><td>${esc(x.label)}${x.hidden ? ' <span class="badge b-mute" title="카카오톡 프로필에 없음">숨은</span>' : ''}<div class="muted" style="font-size:11px">중요도 ${x.weight.toFixed(2)}</div></td>
          <td class="num">${x.chosen_value.toFixed(2)}</td><td class="num">${x.available_mean.toFixed(2)}</td><td class="num">${x.target}</td>
          <td><div style="display:flex;align-items:center;gap:4px"><div style="flex:1;height:8px;position:relative;background:var(--panel-2);border-radius:4px">
            <i style="position:absolute;top:0;bottom:0;${x.contribution >= 0 ? 'left:50%' : 'right:50%'};width:${Math.abs(x.contribution) / mx * 50}%;background:${x.contribution >= 0 ? 'var(--ok)' : 'var(--danger)'};border-radius:4px"></i></div>
            <span class="mono" style="font-size:11px;width:44px;text-align:right">${x.contribution >= 0 ? '+' : ''}${x.contribution.toFixed(2)}</span></div></td></tr>`).join('')}</tbody></table>`;
    }
    async function draw() {
      const my = ++token, r = rooms[room], list = chosen.filter(s => s.room_id === r.room_id);
      slider.max = Math.max(0, list.length - 1); step = Math.min(step, list.length - 1); slider.value = step;
      const cur = list[step];
      if (!cur) { SV.$('#tlNow').textContent = '이 열람실 선택 없음'; return; }
      SV.$('#tlNow').textContent = `${step + 1}/${list.length} · ${cur.occurred_at.slice(0, 16).replace('T', ' ')} · ${l(cur.time_slot)} · 좌석 ${cur.chosen_seat_id.split(':').pop()} · 사용 중 ${(cur.occupied_seat_ids || []).length}/${r.seats.length}석`;
      SV.$('#tlSide').innerHTML = '<div class="empty"><span class="spinner"></span></div>';
      const [sc, ex] = await Promise.all([getSc(cur.scenario_id), getEx(cur._id)]);
      if (my !== token) return;  // 더 최근 요청이 있으면 버림
      const hl = {}, cnt = {};
      list.slice(0, step).forEach(s => { cnt[s.chosen_seat_id] = (cnt[s.chosen_seat_id] || 0) + 1; });
      Object.entries(cnt).forEach(([id, n]) => hl[id] = { kind: 'history', badge: n });
      (cur.occupied_seat_ids || []).forEach(id => hl[id] = { kind: 'occupied' });
      (cur.exposed_ids || []).forEach((id, i) => hl[id] = { kind: 'exposed', rank: i + 1 });
      if (ex.best_seat_id && ex.best_seat_id !== cur.chosen_seat_id) hl[ex.best_seat_id] = { kind: 'best', rank: hl[ex.best_seat_id]?.rank, badge: '최' };
      hl[cur.chosen_seat_id] = { kind: 'chosen', rank: (cur.exposed_ids || []).indexOf(cur.chosen_seat_id) + 1 || undefined };
      const same = floor.data && floor.data.scenario.scenario_id === sc.scenario.scenario_id;
      floor.opts.highlight = hl; floor.opts.slot = cur.time_slot;
      if (same) floor.render(); else {
        floor.setData(sc);  // 열람실에 맞춰 확대(화장실까지 포함하면 좌석이 너무 작아짐). 화장실은 축소 버튼으로 확인
        const b = floor.b, pd = 1.2 * floor.M; floor.vb = [b[0] - pd, b[1] - pd, b[2] - b[0] + 2 * pd, b[3] - b[1] + 2 * pd]; floor._applyVb();
      }
      floor.legend.hidden = false; floor.legend.innerHTML = legend;
      SV.$('#tlSide').innerHTML = `<h3>왜 이 좌석을 골랐나 <span class="muted small">좌석 ${esc(ex.chosen_label || '')}</span></h3>
        <ol style="padding-left:18px;margin:6px 0;line-height:1.55">${ex.reasons.map(x => `<li style="margin-bottom:4px">${x}</li>`).join('')}</ol>
        ${ex.items?.length ? itemsTable(ex) : ''}
        <div class="muted" style="margin-top:6px;font-size:11px">${esc(ex.note || '')} 기여 = 중요도 × 일치도의 (선택 좌석 − 빈자리 평균).</div>
        <a href="${SV.base}/sessions/${encodeURIComponent(cur._id)}">세션 상세 →</a>`;
      const nxt = list[step + 1]; if (nxt) { getSc(nxt.scenario_id); getEx(nxt._id); }  // 다음 단계 미리 읽기
    }
    slider.oninput = () => { step = +slider.value; draw(); };
    SV.$$('#tlRooms button').forEach(b => b.onclick = () => { SV.$$('#tlRooms button').forEach(x => x.classList.toggle('on', x === b)); room = +b.dataset.i; step = 0; stop(); draw(); });
    const stop = () => { clearInterval(timer); timer = null; SV.$('#tlPlay').innerHTML = '<i class="fa-solid fa-play"></i> 재생'; };
    SV.$('#tlPlay').onclick = () => {
      if (timer) return stop();
      if (step >= +slider.max) step = 0;
      SV.$('#tlPlay').innerHTML = '<i class="fa-solid fa-pause"></i> 정지';
      timer = setInterval(() => { if (step >= +slider.max) return stop(); step++; draw(); }, 2500);
    };
    step = 0; draw();
  };
})();
