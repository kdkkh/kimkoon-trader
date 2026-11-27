/* 백테스트 탭 화면 — 지난 결과 보기 · 실행 단추(자식 프로세스). 서버 /api/backtest/… 만 부른다. */
(function () {
  "use strict";
  const D = window.Desk, $ = D.$, esc = D.esc;
  const API = "/api/backtest/";
  const S = { list: null, kind: "fill", sel: null, opts: null };
  const KINDNAME = { fill: "재생 백테스트(체결 · 봉 재생)", sim: "지난 실시간 시뮬레이션", bar: "봉 백테스트" };
  const nm = (c) => esc(D.maskName(c, c));

  // ---------- 목록 ----------
  async function loadList() {
    const r = await D.get(API + "list");
    if (!r.ok) { $("listnote").textContent = r.error || ""; return; }
    S.list = r;
    $("listnote").textContent = r.note;
    $("listmeta").textContent = `체결 ${r.fill.length} · 시뮬레이션 ${r.sim.length} · 봉 ${r.bar.length}`;
    renderList();
  }
  function renderList() {
    const items = (S.list && S.list[S.kind]) || [];
    const head = {
      fill: ["폴더", "기록 안의 거래일", "돌린 날", "거래", "손익(전)"],
      sim: ["기록", "거래일", "끝 요약", "체결 끝 · 취소", "실현(전)"],
      bar: ["결과", "기간", "돌린 날", "거래", "총 손익(전)"],
    }[S.kind];
    $("listhead").innerHTML = "<tr>" + head.map((h, i) => `<th class="${i < 3 ? "l" : ""}">${h}</th>`).join("") + "</tr>";
    const row = (x) => S.kind === "fill"
      ? `<td class="l">${esc(x.id.replace(/^data\//, ""))}</td><td class="l num">${esc(x.trade_day || "—")}</td><td class="l num">${esc(x.run_day || "—")}</td><td class="num">${x.trades}</td><td>${D.pnlCell(x.pnl)}</td>`
      : S.kind === "sim"
      ? `<td class="l">${esc(x.name)}</td><td class="l num">${esc(x.trade_day)}</td><td class="l">${x.ended ? "있음" : '<span class="g">없음</span>'}</td><td class="num">${x.done_filled} · ${x.cancelled}</td><td>${x.realized == null ? "—" : D.pnlCell(x.realized)}</td>`
      : `<td class="l">${esc(x.name)}</td><td class="l num">${esc(x.trade_day)}</td><td class="l num">${esc(x.run_day)}</td><td class="num">${x.trades ?? "—"}</td><td>${D.pnlCell(x.pnl)}</td>`;
    $("listrows").innerHTML = items.map((x, i) => `<tr class="click ${S.sel && S.sel.id === x.id ? "sel" : ""}" data-i="${i}">${row(x)}</tr>`).join("")
      || `<tr><td class="l g" colspan="5">${KINDNAME[S.kind]} 결과 없음</td></tr>`;
  }
  $("kind").addEventListener("change", () => { S.kind = $("kind").value; renderList(); });
  $("reload").addEventListener("click", loadList);
  $("listrows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    const x = S.list[S.kind][+tr.getAttribute("data-i")];
    S.sel = { kind: S.kind, id: x.id };
    renderList();
    loadDetail();
  });

  // ---------- 결과 ----------
  async function loadDetail() {
    const r = await D.get(API + "detail", S.sel);
    if (!r.ok) { $("detmeta").textContent = r.error || ""; return; }
    $("detmeta").textContent = `${KINDNAME[r.kind]} · ${r.id}`;
    if (r.kind === "bar") return renderBar(r);
    const t = r.totals;
    const stop = r.stop ? `실현 ${D.signed(r.stop.realized)} · 체결 끝 ${(r.stop.orders_done || {}).filled ?? "—"} · 취소 ${(r.stop.orders_done || {}).cancelled ?? "—"}` : "—";
    $("dettop").innerHTML = `<div class="kv">
      <div>이름</div><div>${esc(r.name)}</div>
      <div>기록 안의 거래일</div><div class="num">${esc((r.trade_days || []).join(", ") || "—")}</div>
      <div>${r.kind === "fill" ? "돌린 날(기록 파일 이름)" : "기록 날짜"}</div><div class="num">${esc(r.run_day || "—")}${r.folders > 1 ? ` · 종목 폴더 ${r.folders}개` : ""}</div>
      <div>합계(수수료 · 세금 전)</div><div>거래 ${t.trades}건 · ${D.pnlCell(t.pnl)} · 이긴 거래 ${t.wins}${t.win_pct == null ? "" : ` (${t.win_pct}%)`} · 평균 ${D.pct(t.avg_ret_pct, 3)}</div>
      <div>끝 요약(stop)</div><div class="num">${stop}</div></div>`;
    $("codehead").innerHTML = `<tr><th class="l">종목</th><th>거래</th><th>이긴 거래</th><th>손익(전)</th></tr>`;
    $("coderows").innerHTML = t.per_code.map((c) => `<tr><td class="l">${nm(c.code)}</td><td>${c.n}</td><td>${c.wins}</td><td>${D.pnlCell(c.pnl)}</td></tr>`).join("");
    $("trhead").innerHTML = `<tr><th class="l">종목</th><th class="l">진입</th><th class="l">청산</th><th>수량</th><th>진입가</th><th>청산가</th><th>손익</th><th>%</th></tr>`;
    $("trrows").innerHTML = r.trades.map((x) => `<tr><td class="l">${nm(x.code)}</td><td class="l num">${esc(x.entry_at)}</td><td class="l num">${esc(x.exit_at)}</td>
      <td class="num">${x.qty}</td><td class="num">${D.won(x.entry)}</td><td class="num">${D.won(x.exit)}</td><td>${D.pnlCell(x.pnl)}</td><td class="num ${D.cls(x.ret_pct)}">${D.pct(x.ret_pct, 3)}</td></tr>`).join("")
      + (r.cut ? `<tr><td class="l g" colspan="8">… ${r.cut}건 더(앞 2,000건만 보임)</td></tr>` : "");
  }
  function renderBar(r) {
    const s = r.summary || {};
    $("dettop").innerHTML = `<div class="kv"><div>종목 · 봉</div><div>${nm(r.code)} · ${esc(r.tf)}</div><div>전략</div><div>${esc(r.strategy)} ${esc(JSON.stringify(r.params || {}))}</div>
      <div>API 호출</div><div>${esc(r.api_calls)}</div>${r.pending ? `<div>대기</div><div>${esc(r.pending)}</div>` : ""}</div>`;
    $("codehead").innerHTML = `<tr><th class="l">요약</th><th>값</th></tr>`;
    $("coderows").innerHTML = Object.entries(s).map(([k, v]) => `<tr><td class="l">${esc(k)}</td><td class="num">${typeof v === "number" ? v.toLocaleString("ko-KR") : esc(v)}</td></tr>`).join("");
    const rows = r.bar_trades || [];
    const cols = rows.length ? Object.keys(rows[0]) : [];
    $("trhead").innerHTML = "<tr>" + cols.map((c) => `<th class="l">${esc(c)}</th>`).join("") + "</tr>";
    $("trrows").innerHTML = rows.map((x) => "<tr>" + cols.map((c) => `<td class="l num">${esc(x[c])}</td>`).join("") + "</tr>").join("")
      || `<tr><td class="l g">거래 없음</td></tr>`;
  }

  // ---------- 실행 ----------
  async function loadOptions() {
    const r = await D.get(API + "options");
    if (!r.ok) return;
    S.opts = r;
    const csv = r.csvs.map((c) => `<option>${esc(c)}</option>`).join("");
    $("r-csv").innerHTML = csv || `<option value="">${esc(r.csv_dir)} 에 봉 CSV 없음</option>`;
    $("v-csv2").innerHTML = `<option value="">(합성 봉만)</option>` + csv;
    const st = r.strategies.map((c) => `<option>${esc(c)}</option>`).join("");
    $("r-strat").innerHTML = st;
    $("v-strat").innerHTML = st;
  }
  $("r-src").addEventListener("change", () => $("run").classList.toggle("src-fetch", $("r-src").value === "fetch"));
  $("r-go").addEventListener("click", async () => {
    const fetchMode = $("r-src").value === "fetch";
    const body = { kind: fetchMode ? "bar_fetch" : "bar_csv", strategy: $("r-strat").value, params: $("r-params").value,
      amount: +$("r-amount").value, csv: $("r-csv").value, code: $("r-code").value.trim(), days: +$("r-days").value };
    if (fetchMode) {
      if (!/^\d{6}$/.test(body.code)) { $("r-msg").textContent = "종목 코드를 넣어 주세요(6자리 숫자)"; return; }
      const run = (S.opts && S.opts.running) || [];
      if (run.length) { $("r-msg").textContent = `실행기가 돌고 있어 막음(${run.join(", ")})`; return; }
      if (!(await D.confirm(`한투 조회를 합니다(일봉 ${body.days}일, 실전 키 시세 조회만 — 주문 없음). 계속할까요?`, "조회 실행"))) return;
      body.confirm = true;
    }
    const r = await D.post(API + "run", body);
    $("r-msg").textContent = r.message || r.error || "";
    loadJobs();
  });
  $("v-go").addEventListener("click", async () => {
    const r = await D.post(API + "run", { kind: "validate", strategy: $("v-strat").value, csv: $("v-csv2").value });
    $("v-msg").textContent = r.message || r.error || "";
    loadJobs();
  });
  const STATE = { running: "도는 중", done: "끝남", failed: "실패", error: "띄우지 못함" };
  let lastSig = "";
  async function loadJobs() {
    const r = await D.get(API + "jobs");
    if (!r.ok) return;
    const sig = JSON.stringify(r.jobs.map((j) => [j.name, j.state, j.output.length]));
    if (sig === lastSig) return;
    const finishedNow = lastSig && r.jobs.some((j) => j.state !== "running");
    lastSig = sig;
    $("jobs").innerHTML = r.jobs.map((j) => `<div class="job"><div class="head"><b>${esc(j.label)}</b>
      <span class="pill ${j.state === "done" ? "on" : j.state === "running" ? "warn" : "up"}">${STATE[j.state] || esc(j.state)}</span>
      <span class="g num">${esc(j.started)} · ${j.seconds}초</span>${j.result && j.result.verdict ? `<span class="pill">${esc(j.result.verdict)}</span>` : ""}</div>
      <div class="g num" style="margin-bottom:4px">${esc(j.args)}</div>${j.message && j.state !== "running" ? `<div class="msg">${esc(j.message)}</div>` : ""}
      <pre class="out">${esc(j.output || "(출력 없음)")}</pre></div>`).join("");
    if (finishedNow) loadList();
  }

  loadList();
  loadOptions();
  D.every(loadJobs, 2000);
})();
