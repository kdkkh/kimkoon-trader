/* AI 판단 탭 화면 — 판단 목록 · 상태 · 지금 걸린 것 · 판단 기록(새 줄만) · 한 번 묻기. 서버 /api/ai/… 만 부른다.
   답 옆에는 늘 'AI 판단' 표시를 붙인다(매수 · 매도 권유가 아님). */
(function () {
  "use strict";
  const D = window.Desk, $ = D.$, esc = D.esc;
  const API = "/api/ai/";
  const S = { ov: null, day: "", pos: 0, n: 0, rows: [], sel: "", mineLoaded: false };
  const MARK = `<span class="mark">AI 판단</span>`;
  const onoff = (v, on = "켜짐", off = "꺼짐") => `<span class="pill ${v ? "on" : "off"}">${v ? on : off}</span>`;
  const codesTxt = (cs) => (cs || []).map((c) => esc(D.maskName(c, c))).join(", ");

  D.panes.add("p-main", "판단", $("p-main"));

  // ---------- 목록 · 상태 ----------
  async function loadOverview() {
    const r = await D.get(API + "overview");
    if (!r.ok) { $("statemeta").textContent = r.error || ""; return; }
    S.ov = r;
    renderState(r);
    renderDefs(r);
    renderDoors(r);
    renderAskList(r);
    $("topnote").textContent = r.note;
    if (!S.mineLoaded) { S.mineLoaded = true; D.loadMine("ai", r.mine); }
  }
  function renderState(r) {
    const p = r.process, g = r.global, st = p.status;
    $("statemeta").textContent = g.file ? `전역 설정 ${g.file}` : "전역 설정 기본값";
    const sw = r.switches.map((s) => `<div>${esc(s.label)}</div><div>${onoff(s.on, "보냄", "안 보냄")}</div>`).join("");
    $("state").innerHTML = `
      <div class="kvs">
        <div>판단 프로세스</div><div>${p.running ? '<span class="pill on">돌고 있음</span>' : '<span class="pill off">꺼짐</span>'}
          ${st ? `<span class="g num">마지막 상태 ${esc(st.updated)}${st.fake ? " · 가짜" : ""}${st.dry ? " · 기록만" : ""}</span>` : ""}</div>
        <div>AI 기능</div><div>${onoff(g.ai_enabled)} · 고지 확인 ${onoff(g.ack, "함", "안 함")}</div>
        <div>Anthropic 키</div><div>${r.keys.anthropic ? '<span class="pill on">있음</span>' : '<span class="pill warn">키 없음</span>'} <span class="g">anthropic.yaml(설정 폴더)</span></div>
        <div>오늘 호출</div><div class="num">${g.calls_today} / ${g.daily_calls}번</div>
        <div>오늘 비용 어림</div><div class="num">$${(+g.usd_today).toFixed(4)} / $${esc(g.daily_usd)}</div>
        <div>멈춤</div><div>${g.stopped ? `<span class="pill up">오늘 멈춤</span> <span class="g">${esc(g.stopped_reason)}</span>` : '<span class="pill off">아님</span>'}</div>
        <div>단가표 확인일</div><div class="num">${esc(g.prices_checked || "없음")}</div>
      </div>
      <div class="blk"><b>자료 스위치 — AI 로 보낼지</b><div class="sw">${sw}</div>
        <div class="note">${esc(r.switch_note)} ${g.file_note ? "· " + esc(g.file_note) : ""}${g.error ? `<br><span class="warn">전역 설정 오류: ${esc(g.error)}</span>` : ""}</div></div>
      <div class="blk note">${esc(p.how)}</div>`;
  }
  function renderDefs(r) {
    $("defsmeta").textContent = `${r.defs.length}개 · 켜짐 ${r.defs.filter((d) => d.enabled && !d.example).length}`;
    $("defrows").innerHTML = r.defs.map((d) => {
      const last = d.last ? `${MARK} <b>${esc(d.last.answer || "버림")}</b>${d.last.fake ? ' <span class="pill">가짜</span>' : ""}
        <div class="g num">${esc(d.last.at.slice(11))}${d.last.valid ? "" : " · " + esc(d.last.invalid_reason)}</div>` : '<span class="g">오늘 없음</span>';
      return `<tr><td class="l wrap"><b>${esc(d.id)}</b> ${d.example ? '<span class="pill">견본</span>' : onoff(d.enabled)}
          <div class="g">${esc(d.prov)} ${esc(d.model)}${d.per_code ? " · 종목마다" : ""}</div>
          ${d.switch_off.length ? `<div class="warn">스위치 꺼짐: ${esc(d.switch_off.join(", "))}</div>` : ""}</td>
        <td class="l wrap">${esc(d.when.join(" · "))}</td><td class="l wrap">${esc(d.inputs.join(", "))}</td>
        <td class="l wrap">${esc(d.uses.join(", "))}</td><td class="l wrap">${last}</td>
        <td class="num">${d.calls_today} / ${d.daily_calls}</td><td class="num">${(+d.cost_today).toFixed(4)}</td></tr>`;
    }).join("") || `<tr><td class="l g" colspan="7">판단 정의 없음(judgments/*.yaml)</td></tr>`;
    $("deferrs").innerHTML = r.def_errors.length
      ? r.def_errors.map((e) => `<div class="warn">정의 오류 ${esc(e.file)}: ${esc(e.error)}</div>`).join("")
      : "견본(*.example.yaml)은 judgments/ 에 이름을 바꿔 복사해야 방아쇠대로 돕니다. 손으로 한 번 묻기는 견본도 됩니다.";
  }
  function renderDoors(r) {
    $("doors").innerHTML = r.doors.map((d) => {
      if (d.error) return `<div class="door"><b>${esc(d.label)}</b> <span class="g">읽지 못함(${esc(d.error)})</span></div>`;
      const gates = d.gates.length ? d.gates.map((g) => `<div class="line">거르기 <b>${esc(g.judge)}</b> — ${esc(g.reason || "이유 없음")}
          <span class="g num">· ${Math.floor(g.left_s / 60)}분 ${g.left_s % 60}초 남음${g.codes.length ? " · " + codesTxt(g.codes) : " · 모든 종목"}</span></div>`).join("")
        : '<div class="line g">걸린 거르기 없음</div>';
      const rv = d.review ? `<div class="line">검토 표시 ${d.review.alive ? '<span class="pill on">살아 있음</span>' : '<span class="pill off">만료</span>'}
          <span class="g">답 없으면 ${d.review.on_timeout === "pass" ? "진입" : "진입 안 함"}</span></div>` : '<div class="line g">검토 표시 없음</div>';
      const owned = d.owned.length ? d.owned.map((o) => `<div class="line">바깥이 산 것 <b>${esc(D.maskName(o.code, o.code))}</b> ${esc(o.qty ?? "")}주
          <span class="g">· ${esc(o.source || "")} · 파는 길 ${esc(o.exit_by || "")}${o.max_hold_min ? " · 보유 " + esc(o.max_hold_min) + "분" : ""}${o.stop_pct ? " · 손절 " + esc(o.stop_pct) + "%" : ""}</span></div>`).join("")
        : '<div class="line g">바깥이 산 것 없음</div>';
      return `<div class="door"><div class="head"><b>${esc(d.label)} 문</b>${d.open ? '<span class="pill on">켜짐</span>' : '<span class="pill off">꺼짐</span>'}
          <span class="g">${esc(d.why)}</span>${d.inbox_waiting ? `<span class="pill warn">신호 대기 ${d.inbox_waiting}</span>` : ""}</div>${gates}${rv}${owned}</div>`;
    }).join("");
  }
  function renderAskList(r) {
    const sel = $("a-judge"), keep = sel.value;
    sel.innerHTML = r.defs.map((d) => `<option value="${esc(d.id)}">${esc(d.id)}${d.example ? " (견본)" : ""} · ${esc(d.prov)}</option>`).join("");
    if (keep) sel.value = keep;
  }

  // ---------- 판단 기록(새 줄만) ----------
  async function loadRecords() {
    const want = $("day").value || S.day;
    if (want !== S.day) { S.day = want; S.pos = 0; S.n = 0; S.rows = []; }
    const r = await D.get(API + "records", { day: S.day, pos: S.pos, n0: S.n });
    if (!r.ok) { $("recnote").textContent = r.error || ""; return; }
    if (!S.day) S.day = r.day;
    if (r.reset) { S.rows = []; }
    S.pos = r.pos; S.n = r.n_next;
    const daySel = $("day");
    if (daySel.options.length !== r.days.length) {
      daySel.innerHTML = r.days.map((d) => `<option value="${d}">${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}</option>`).join("");
      daySel.value = S.day;
    }
    if (r.rows.length) { S.rows = S.rows.concat(r.rows).slice(-1500); renderRecords(); }
    else if (!S.rows.length) renderRecords();
    $("recnote").textContent = r.exists ? `${S.rows.length}줄` : "이날 기록 없음";
    if (r.more) setTimeout(loadRecords, 50);
  }
  const OUTN = { signal: "신호", gate: "거르기", settings: "설정", notify: "알림", review: "검토 답" };
  function renderRecords() {
    const rows = S.rows.slice().reverse();
    $("recmeta").textContent = `판단 ${S.rows.filter((x) => x.event === "judge").length} · 묻지 않음 ${S.rows.filter((x) => x.event === "skip").length}`;
    $("recrows").innerHTML = rows.map((x) => {
      if (x.event !== "judge") {
        return `<tr><td class="l num">${esc(x.at.slice(11))}</td><td class="l">${esc(x.judge)}</td><td class="l g">${esc(x.event)}</td>
          <td class="l g wrap" colspan="2">${esc(x.reason || "")}</td></tr>`;
      }
      const outs = x.outputs.map((o) => { const [k, s] = o.split(":"); return `${OUTN[k] || esc(k)} ${esc(s)}`; }).join(", ") || '<span class="g">없음</span>';
      return `<tr class="click ${S.sel === x.ref ? "sel" : ""}" data-ref="${esc(x.ref)}"><td class="l num">${esc(x.at.slice(11))}</td>
        <td class="l">${esc(x.judge)}${x.fake ? ' <span class="pill">가짜</span>' : ""}${x.dry ? ' <span class="pill">기록만</span>' : ""}</td>
        <td class="l">${esc(x.trigger)}${x.codes.length ? " · " + codesTxt(x.codes) : ""}</td>
        <td class="l">${x.valid ? `${MARK} <b>${esc(x.answer)}</b>` : `<span class="warn">버림</span> <span class="g">${esc(x.invalid_reason)}</span>`}</td>
        <td class="l wrap">${outs}</td></tr>`;
    }).join("") || `<tr><td class="l g" colspan="5">기록 없음</td></tr>`;
  }
  $("day").addEventListener("change", loadRecords);
  $("recrows").addEventListener("click", async (e) => {
    const tr = e.target.closest("tr[data-ref]");
    if (!tr) return;
    S.sel = tr.getAttribute("data-ref");
    renderRecords();
    const r = await D.get(API + "record", { day: S.day, ref: S.sel });
    if (!r.ok) { $("detmeta").textContent = r.error || ""; return; }
    renderDetail(r.record);
  });
  function renderDetail(x) {
    $("detmeta").textContent = `${x.judge} · ${x.at || ""}`;
    const ins = (x.inputs || []).map((s) => {
      if (s.dropped) return `<div>${esc(s.kind)}: <span class="g">뺌 — ${esc(s.dropped)}</span></div>`;
      if (s.kind === "news_titles") return `<div>뉴스 제목 ${s.n}건 <span class="g">(${(s.items || []).slice(0, 3).map((i) => "#" + esc(i.srno) + (i.title && !D.mask ? " " + esc(i.title) : " 제목 가림")).join(" · ")}${s.n > 3 ? " …" : ""})</span></div>`;
      if (s.kind === "disclosures") return `<div>공시 ${s.n}건 <span class="g">출처 ${esc(s.source || "")}</span></div>`;
      if (s.kind === "bars") return `<div>1분봉(${esc(s.form)}) ${(s.codes || []).map((c) => esc(D.maskName(c.code, c.code)) + " " + c.n + "개").join(", ")}</div>`;
      return `<div>${esc(s.kind)} ${esc(JSON.stringify(Object.fromEntries(Object.entries(s).filter(([k]) => k !== "kind"))))}</div>`;
    }).join("");
    const outs = (x.outputs || []).map((o) => `<div>${OUTN[o.kind] || esc(o.kind)} — <b>${esc(o.status)}</b>${o.why ? ` <span class="g">${esc(o.why)}</span>` : ""}
      ${o.targets ? `<span class="g">· 대상 ${esc([].concat(o.targets).join(", "))}</span>` : ""}${o.obj ? `<div class="g num">${esc(JSON.stringify(o.obj))}</div>` : ""}</div>`).join("") || '<span class="g">없음</span>';
    const ans = x.answer ? (x.answer.answer !== undefined ? x.answer.answer : x.answer.score + "점") : "";
    $("det").innerHTML = `
      <div class="kvs">
        <div>답</div><div>${x.valid ? `${MARK} <span class="ans">${esc(ans)}</span>` : `<span class="warn">버림 — ${esc(x.invalid_reason)}</span>`}</div>
        <div>근거(모델 글)</div><div>${esc((x.answer || {}).reason || "")}</div>
        <div>방아쇠</div><div>${esc((x.trigger || {}).kind || "")} ${esc((x.trigger || {}).detail || "")}</div>
        <div>모델</div><div>${esc(x.prov)} · ${esc((x.model || {}).served || (x.model || {}).name || "")}${x.fake ? " · 가짜" : ""}${x.dry ? " · 기록만" : ""}</div>
        <div>호출 · 비용</div><div class="num">${esc(x.calls)}번 · ${x.cost_est == null ? "—" : "$" + (+x.cost_est).toFixed(5)} <span class="g">${esc(x.cost_note || "")}</span></div>
        <div>입력 요약</div><div>${ins}<div class="g">${esc((x.notes || []).join(" · "))}</div><div class="g">${esc(x.titles_note)}</div></div>
        <div>질문</div><div style="white-space:pre-wrap">${esc(x.question || "(정의 파일에서 찾지 못함)")}</div>
        <div>검증</div><div>${(x.attempts || []).map((a) => esc(a.error ? "실패 " + a.error : `${a.stop_reason} · ${a.valid ? "통과" : "어긋남 " + (a.why || "")}`)).join(" / ")}</div>
        <div>내보낸 것</div><div>${outs}</div>
      </div>
      <b>원답</b><pre class="out">${esc(x.raw_answer || "")}</pre>
      <b>프롬프트</b><pre class="out">${esc(x.prompt || "")}</pre>`;
  }

  // ---------- 한 번 묻기 ----------
  $("a-go").addEventListener("click", async () => {
    const id = $("a-judge").value, mode = $("a-mode").value;
    const d = (S.ov && S.ov.defs.find((x) => x.id === id)) || {};
    const codes = $("a-codes").value.split(/[,\s]+/).filter(Boolean);
    const text = $("a-text").value;
    const msg = mode === "fake"
      ? `가짜 응답으로 한 번 묻습니다 — 판단 "${id}". 바깥 API · AI 를 부르지 않고(비용 없음) 판단 기록에만 남깁니다(신호 · 거르기 · 알림 안 보냄).`
      : `실제 호출 — 판단 "${id}" 를 ${d.prov || ""} ${d.model || ""} 로 부릅니다(비용 발생). 정의의 쓰임대로 내보낼 수 있습니다. 결과는 'AI 판단'이며 매수 · 매도 권유가 아닙니다.`;
    if (!(await D.confirm(msg, mode === "fake" ? "가짜로 묻기" : "실제로 호출"))) return;
    const r = await D.post(API + "ask", { judge: id, mode, codes, text, confirm: true });
    $("a-msg").textContent = r.key_missing ? "키 없음: anthropic.yaml 을 설정 폴더에" : (r.message || r.error || "");
    loadJob();
  });
  const JST = { running: "도는 중", done: "끝남", failed: "실패", error: "띄우지 못함" };
  async function loadJob() {
    const r = await D.get(API + "jobs");
    if (!r.ok || !r.job) return;
    const j = r.job;
    $("a-job").innerHTML = `<div class="job"><div class="row"><b>${esc(j.label)}</b><span class="pill ${j.state === "done" ? "on" : j.state === "running" ? "warn" : "up"}">${JST[j.state] || esc(j.state)}</span>
      <span class="g num">${esc(j.started)} · ${j.seconds}초</span></div><pre class="out">${esc(j.output || "(출력 없음)")}</pre></div>`;
  }

  loadOverview();
  D.every(loadRecords, 3000);
  setInterval(loadOverview, 10000);
  setInterval(loadJob, 3000);
})();
