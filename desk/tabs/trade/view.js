/* 매매 탭 화면 — 서버 /api/trade/… 만 부른다(증권사 API 를 부르지 않는다). */
(function () {
  "use strict";
  const D = window.Desk, $ = D.$, esc = D.esc;
  const API = "/api/trade/";
  const S = { day: "", today: "", ev: { key: "", pos: 0, rows: [] }, cats: null, off: new Set(), settings: null, previewed: null };
  const MODES = ["limit", "market", "best", "escalate", "capped"];
  const KEYNAME = { codes: "종목", strategy: "전략", params: "전략 값", order_mode: "주문 방식", amount: "1회 금액(원)",
    wait: "지정가 기다림(초)", max_orders: "하루 진입 상한", loss_limit: "일 손실 한도(원)", apply_to_open: "걸린 주문에도",
    poll: "주문 조회 간격(초)", settle: "봉 마감 뒤(초)", max_amount: "금액 상한(원)", tf: "봉(분)", start: "시작", until: "끝" };

  // ---------- 실행기 카드 ----------
  async function loadStatus() {
    const r = await D.get(API + "status");
    $("clock").textContent = r.ok ? `상태 갱신 ${r.now}` : r.error || "";
    if (!r.ok) return;
    $("runners").innerHTML = r.cards.map((c) => `
      <div class="runner">
        <div class="head"><b>${esc(c.label)}</b>
          <span class="pill ${c.alive ? "on" : "off"}">${c.alive ? "돌고 있음" : "꺼짐"}</span>
          <span class="pill ${c.door_open ? "on" : "off"}" title="${esc(c.door_why)}">문 ${c.door_open ? "켜짐" : "꺼짐"}</span>
          ${c.stop_file ? '<span class="pill warn">정지 파일 있음</span>' : ""}</div>
        <div class="kv">
          <div>마지막 heartbeat</div><div class="num">${esc(c.heartbeat_at || "—")} <span class="g">${esc(c.why)}</span></div>
          <div>종목</div><div class="num">${c.codes.length ? c.codes.map((x) => esc(D.maskName(x, x))).join(", ") : "—"}</div>
          <div>진행 중 주문</div><div class="num">${c.active_orders}</div>
          <div>들고 있는 것</div><div class="num">${Object.keys(c.positions).length ? Object.entries(c.positions).map(([k, v]) => `${esc(D.maskName(k, k))} ${v.qty}주`).join(", ") : "없음"}</div>
        </div>
        <div class="row"><button class="btn" data-start="${c.target}">켜기</button>
          <button class="btn red" data-stop="${c.target}">끄기(정지 파일)</button><span class="g msg" id="runmsg-${c.target}"></span></div>
      </div>`).join("");
  }
  $("runners").addEventListener("click", async (e) => {
    const st = e.target.getAttribute("data-start"), sp = e.target.getAttribute("data-stop");
    if (st) {
      const chk = await D.get(API + "start_check", { target: st });
      if (!chk.ok && chk.status !== "closed") { $("runmsg-" + st).textContent = chk.message || chk.error || ""; return; }
      if (!(await D.confirm(`${chk.confirm}${(chk.notes || []).length ? "\n" + chk.notes.join("\n") : ""}`, "켜기"))) return;
      const r = await D.post(API + "start", { target: st, confirm: true });
      $("runmsg-" + st).textContent = r.message || r.error || "";
      loadStatus();
    } else if (sp) {
      if (!(await D.confirm("정지 파일을 만듭니다. 실행기가 새 주문을 멈추고 진행 중 주문을 정리한 뒤 끝냅니다.", "끄기"))) return;
      const r = await D.post(API + "stop", { target: sp });
      $("runmsg-" + sp).textContent = r.message || r.error || "";
      loadStatus();
    }
  });

  // ---------- 날짜 ----------
  async function loadDays() {
    const r = await D.get(API + "days");
    if (!r.ok) return;
    S.today = r.today;
    const days = r.days.slice();
    if (!days.find((d) => d.day === r.today)) days.unshift({ day: r.today, label: r.today.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3"), paper: false, sim: false });
    $("day").innerHTML = days.map((d) => `<option value="${d.day}">${esc(d.label)}${d.day === r.today ? " (오늘)" : ""}${d.paper || d.sim ? "" : " · 기록 없음"}</option>`).join("");
    const want = new URLSearchParams(location.search).get("day");
    S.day = days.find((d) => d.day === want) ? want : (days.find((d) => d.day === r.today && (d.paper || d.sim)) || days.find((d) => d.paper || d.sim) || days[0]).day;
    $("day").value = S.day;
  }
  $("day").addEventListener("change", () => { S.day = $("day").value; refreshDay(); });

  // ---------- 하루 요약 ----------
  function sumRow(name, a, b, cls = "") { return `<tr class="${cls}"><td>${name}</td><td>${a}</td><td>${b}</td></tr>`; }
  async function loadSummary() {
    const r = await D.get(API + "summary", { day: S.day });
    if (!r.ok) { $("summary").innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
    $("sumday").textContent = `${r.label}${S.day === S.today ? " (오늘)" : ""}`;
    const [p, s] = r.runners;
    const v = (x, f) => (x.summary ? f(x.summary) : '<span class="g">기록 없음</span>');
    const net = (x) => x.net.ok ? D.pnlCell(x.net.value) + `<div class="g">수수료 ${D.won(x.net.fee)} · 세금 ${D.won(x.net.tax)}</div>`
      : `<span class="g">${esc(x.net.text)}</span>`;
    const hold = (x) => x.holdings.length ? x.holdings.map((h) => `${esc(D.maskName(h.code, h.code))} ${h.qty}주 @${D.won(h.avg)}`).join("<br>") : "없음";
    const f0 = (p.summary || s.summary || {}).net || {};
    $("summary").innerHTML = `<table class="sumtab"><thead><tr><th></th><th>${esc(p.label)}</th><th>${esc(s.label)}</th></tr></thead><tbody>
      ${sumRow("거래일(기록 안)", v(p, (x) => esc(x.trade_day)), v(s, (x) => esc(x.trade_day)))}
      ${sumRow("신호", v(p, (x) => x.signals), v(s, (x) => x.signals))}
      ${sumRow("주문(의도 · 보냄)", v(p, (x) => `${x.intents} · ${x.sent}${x.unknown ? ` <span class="g">+응답 없음 ${x.unknown}</span>` : ""}`), v(s, (x) => `${x.intents} · ${x.sent}${x.unknown ? ` <span class="g">+응답 없음 ${x.unknown}</span>` : ""}`))}
      ${sumRow("체결 사건", v(p, (x) => x.fills), v(s, (x) => x.fills))}
      ${sumRow("체결 끝난 주문", v(p, (x) => x.done_filled), v(s, (x) => x.done_filled))}
      ${sumRow("취소", v(p, (x) => x.cancelled), v(s, (x) => x.cancelled))}
      ${sumRow("위험 점검 막힘 · 거름", v(p, (x) => `${x.risk_blocks} · ${x.filtered}`), v(s, (x) => `${x.risk_blocks} · ${x.filtered}`))}
      ${sumRow("실현 손익", v(p, (x) => D.pnlCell(x.realized)), v(s, (x) => D.pnlCell(x.realized)))}
      ${sumRow("평가 손익", v(p, (x) => D.pnlCell(x.unrealized)), v(s, (x) => D.pnlCell(x.unrealized)))}
      ${sumRow("손익 — 수수료 · 세금 전", v(p, (x) => D.pnlCell(x.gross)), v(s, (x) => D.pnlCell(x.gross)), "big")}
      ${sumRow("손익 — 수수료 · 세금 넣은 값", v(p, net), v(s, net), "big")}
      ${sumRow("들고 있는 것(체결 기준)", v(p, hold), v(s, hold))}
    </tbody></table>
    <div class="note" style="margin-top:8px">${r.fees_set ? `수수료 ${f0.commission_pct}%(매수 · 매도 각각) · 매도세 ${f0.sell_tax_pct}% — 설정 파일의 fees 칸${f0.source ? " · " + esc(f0.source) : ""}. 넣은 값 = 수수료 · 세금 전 − 체결 금액 × 율.` : "수수료 · 세금 율은 설정 파일(desk.yaml)의 fees 칸에서 받습니다."}
      손익 출처: ${esc(((p.summary || s.summary) || {}).pnl_source || "—")}</div>`;
  }

  // ---------- 설정 ----------
  const show = (k, v) => (v === null || v === undefined ? "" : k === "codes" && Array.isArray(v) ? v.join(", ")
    : typeof v === "object" ? "{" + Object.entries(v).map(([a, b]) => `${a}: ${b}`).join(", ") + "}" : String(v));
  async function loadSettings() {
    const r = await D.get(API + "settings");
    S.settings = r;
    if (!r.ok) { $("settings").innerHTML = `<div class="empty">${esc(r.error || "설정을 읽지 못함")}</div>`; return; }
    $("setmeta").textContent = `${r.file} · 적용 대상: ${r.running.length ? r.running.join(", ") : "돌고 있는 실행기 없음(다음 시작 때)"}`;
    const field = (k) => {
      const val = show(k, r.settings[k]);
      if (k === "strategy") return `<select data-k="${k}" data-o="${esc(val)}">${(r.strategies.includes(val) ? r.strategies : [val, ...r.strategies]).map((s) => `<option ${s === val ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>`;
      if (k === "order_mode") return `<select data-k="${k}" data-o="${esc(val)}">${(r.modes && r.modes.length ? r.modes : MODES).map((m) => `<option ${m === val ? "selected" : ""}>${m}</option>`).join("")}</select>`;
      if (k === "apply_to_open") return `<select data-k="${k}" data-o="${esc(val || "false")}">${["false", "true"].map((m) => `<option ${m === String(val || "false") ? "selected" : ""}>${m}</option>`).join("")}</select>`;
      return `<input type="text" data-k="${k}" data-o="${esc(val)}" value="${esc(val)}">`;
    };
    const rows = r.editable.map((k) => `<div class="k">${esc(KEYNAME[k] || k)}</div><div>${field(k)}</div>`).join("")
      + r.locked.map((k) => `<div class="k">${esc(KEYNAME[k] || k)}</div><div><input type="text" readonly value="${esc(show(k, r.settings[k]))} (다시 시작해야 반영)"></div>`).join("");
    const recent = r.recent.map((x) => `<div><span class="num">${esc(x.at)}</span> ${esc(x.actor)} · ${esc(x.text)} — ${esc(x.message)}</div>`).join("")
      + r.runner_events.map((x) => `<div><span class="num">${esc(x.at)}</span> ${esc(x.label)} · ${esc(x.text)}</div>`).join("");
    $("settings").innerHTML = `<div class="setgrid" id="setgrid">${rows}</div>
      <div class="row" style="margin-top:10px"><button class="btn" id="btn-pre">미리 검사</button><button class="btn blue" id="btn-apply" disabled>적용</button></div>
      <div class="msg" id="setmsg"></div>
      <div class="recent"><div class="g">최근 변경(오늘)</div>${recent || '<div class="g">없음</div>'}</div>`;
    $("setgrid").addEventListener("input", onEdit);
    $("setgrid").addEventListener("change", onEdit);
    $("btn-pre").addEventListener("click", preview);
    $("btn-apply").addEventListener("click", apply);
  }
  function changes() {
    const ch = {};
    for (const el of document.querySelectorAll("#setgrid [data-k]")) if (el.value !== el.getAttribute("data-o")) ch[el.getAttribute("data-k")] = el.value;
    return ch;
  }
  function onEdit(e) {
    if (e.target.getAttribute) e.target.classList.toggle("changed", e.target.value !== e.target.getAttribute("data-o"));
    S.previewed = null;
    $("btn-apply").disabled = true;
  }
  async function preview() {
    const ch = changes();
    if (!Object.keys(ch).length) { $("setmsg").textContent = "바꾼 칸이 없습니다"; return; }
    const r = await D.post(API + "settings/preview", { changes: ch });
    const diff = Object.entries(r.diff || {}).map(([k, v]) => `${KEYNAME[k] || k}: ${show(k, v[0])} → ${show(k, v[1])}`).join("\n");
    $("setmsg").innerHTML = `<span class="${r.ok ? "ok" : "warn"}">${esc(r.ok ? "미리 검사 통과" : "미리 검사에서 막힘 — " + (r.message || r.error))}</span>${diff ? "\n" + esc(diff) : ""}${(r.notes || []).length ? '\n<span class="warn">' + esc(r.notes.join("\n")) + "</span>" : ""}`;
    S.previewed = r.ok ? JSON.stringify(ch) : null;
    $("btn-apply").disabled = !r.ok;
  }
  async function apply() {
    const ch = changes();
    if (S.previewed !== JSON.stringify(ch)) { $("setmsg").textContent = "미리 검사를 다시 하세요"; $("btn-apply").disabled = true; return; }
    $("btn-apply").disabled = true;
    const r = await D.post(API + "settings/apply", { changes: ch });
    if (!r.ok) { $("setmsg").textContent = r.message || r.error; return; }
    $("setmsg").textContent = r.message;
    const t0 = Date.now();
    const iv = setInterval(async () => {
      const j = await D.get(API + "settings/job", { id: r.job });
      if (j.ok && j.state === "done") {
        clearInterval(iv);
        const x = j.result;
        $("setmsg").innerHTML = `<span class="${x.ok ? "ok" : "warn"}">${esc(x.text)}</span>\n${esc(x.message)}`;
        S.previewed = null;
        setTimeout(loadSettings, 800);
      } else if (Date.now() - t0 > 200000) clearInterval(iv);
      else $("setmsg").textContent = `${r.message} (${Math.round((Date.now() - t0) / 1000)}초)`;
    }, 1000);
  }

  // ---------- 사건 흐름 ----------
  async function loadEvents() {
    const target = $("evtarget").value, key = `${target}|${S.day}`;
    if (S.ev.key !== key) S.ev = { key, pos: 0, rows: [] };
    const r = await D.get(API + "events", { target, day: S.day, pos: S.ev.pos });
    if (!r.ok) { $("evmeta").textContent = r.error || ""; return; }
    if (S.ev.key !== key) return;                                   // 기다리는 사이 날짜 · 실행기가 바뀜
    if (r.reset) S.ev.rows = [];
    S.ev.pos = r.pos;
    if (!S.cats) {
      S.cats = r.cats;
      $("evcats").innerHTML = Object.entries(r.cats).map(([k, n]) => `<label><input type="checkbox" data-c="${k}" checked> ${esc(n)}</label>`).join("");
    }
    if (r.lines.length || r.reset) {
      S.ev.rows = S.ev.rows.concat(r.lines).slice(-3000);
      renderEvents();
    }
    $("evmeta").textContent = r.exists ? `${S.ev.rows.length}줄(봉 사건 뺌) · 새 줄만 2초마다` : "이 날 기록 없음";
  }
  function renderEvents() {
    const rows = S.ev.rows.filter((x) => !S.off.has(x.cat)).slice(-600).reverse();
    $("evrows").innerHTML = rows.map((x) => `<tr><td class="l num">${esc(x.at)}</td><td class="l"><span class="pill cat">${esc((S.cats || {})[x.cat] || x.cat)}</span></td>
      <td class="l">${esc(D.mask && x.code ? x.text.split(x.code).join(D.maskName(x.code, x.code)) : x.text)}</td></tr>`).join("")
      || `<tr><td class="l g" colspan="3">사건 없음</td></tr>`;
  }
  $("evcats").addEventListener("change", (e) => {
    const c = e.target.getAttribute("data-c");
    if (!c) return;
    if (e.target.checked) S.off.delete(c); else S.off.add(c);
    renderEvents();
  });
  $("evtarget").addEventListener("change", loadEvents);

  // ---------- 모의 대 시뮬레이션 ----------
  async function loadCompare() {
    const r = await D.get(API + "compare", { day: S.day });
    if (!r.ok || !r.rows.length) { $("cmpmeta").textContent = r.message || r.error || ""; $("cmprows").innerHTML = ""; return; }
    $("cmpmeta").textContent = `신호 모의 ${r.signals_paper} · 시뮬 ${r.signals_sim} · 짝 ${r.paired} · 둘 다 주문 ${r.both_ordered} · 둘 다 체결 ${r.both_filled} · 한쪽만 체결 ${r.one_filled}`;
    const all = $("cmpall").checked;
    const side = (x) => !x ? '<span class="g">신호 없음</span>' : !x.ordered ? '<span class="g">주문 없음</span>'
      : `${x.side === "buy" ? "매수" : "매도"} ${x.state === "filled" ? `체결 ${D.won(x.avg)}` : x.state === "cancelled" ? "취소됨" : esc(x.state || "—")}`;
    $("cmprows").innerHTML = r.rows.filter((x) => all || (x.paper && x.paper.ordered) || (x.sim && x.sim.ordered)).map((x) => `<tr>
      <td class="l num">${esc(x.bar)}</td><td class="l">${esc(D.maskName(x.code, x.code))}</td><td class="l">${esc(x.kind)}</td>
      <td>${side(x.paper)}</td><td>${side(x.sim)}</td><td class="num">${x.diff == null ? "—" : D.signed(x.diff)}</td>
      <td>${x.diff_won == null ? "—" : D.pnlCell(x.diff_won)}</td><td class="l g">${esc(x.note)}</td></tr>`).join("");
  }
  $("cmpall").addEventListener("change", loadCompare);

  function refreshDay() {
    $("dayinfo").textContent = S.day === S.today ? "오늘 기록 — 새 줄을 이어 받습니다" : "지난 기록 — 읽기만";
    loadSummary(); loadEvents(); loadCompare();
  }

  async function boot() {
    await loadDays();
    refreshDay();
    loadSettings();
    D.every(loadStatus, 10000);
    D.every(loadEvents, 2000);
    setInterval(() => { if (S.day === S.today) { loadSummary(); loadCompare(); } }, 15000);
  }
  boot();
})();
