/* 공시 탭 화면 — 내 OpenDART 키로 오늘 공시 목록(단추식). 내 화면 전용 조각은 서버가 알려 줄 때만 불러온다. */
(function () {
  "use strict";
  const D = window.Desk, $ = D.$, esc = D.esc;
  const API = "/api/disclosure/";
  D.panes.add("p-query", "조회(내 키)", $("p-query"));

  async function loadInfo() {
    const r = await D.get(API + "info");
    if (!r.ok) { $("q-msg").textContent = r.error || ""; return; }
    if (!$("q-code").value) $("q-code").value = r.default_code || "";
    $("q-key").innerHTML = r.key ? '<span class="pill on">있음</span> <span class="g">opendart.yaml</span>'
      : '<span class="pill warn">키 없음</span> <span class="g">opendart.yaml 을 설정 폴더에</span>';
    $("q-calls").textContent = `${r.calls_today} / ${r.cap}건(오늘 몫 · 판단과 같은 수)`;
    $("q-note").textContent = r.note;
    $("q-src").textContent = `출처: ${r.source}`;
    $("topnote").textContent = r.codes.length ? `실행기 종목: ${r.codes.map((c) => D.maskName(c, c)).join(", ")}` : "";
    return r;
  }
  $("q-go").addEventListener("click", async () => {
    const code = $("q-code").value.trim();
    if (!code) { $("q-msg").textContent = "종목을 적으세요"; return; }
    if (!(await D.confirm(`OpenDART 에 오늘 공시 목록을 조회합니다(종목 ${code}). 호출 수는 오늘 몫에서 셉니다. 목록은 화면에만 보이고 저장하지 않습니다.`, "조회"))) return;
    $("q-msg").textContent = "조회 중…";
    const r = await D.post(API + "query", { code, confirm: true });
    if (!r.ok) { $("q-msg").textContent = r.message || r.error || "실패"; return; }
    $("q-msg").textContent = `${r.rows.length}건`;
    $("q-meta").textContent = `${r.codes.join(", ")} · 오늘 호출 ${r.calls_today} / ${r.cap}`;
    $("q-rows").innerHTML = r.rows.map((x) => `<tr><td class="l num">${esc(x.rcept_dt)}</td><td class="l num">${esc(x.rcept_no)}</td>
      <td class="l">${esc(x.corp_name)}</td><td class="l num">${esc(D.maskName(x.stock_code, x.stock_code))}</td><td class="l">${esc(x.report_nm)}</td>
      <td class="l">${esc(x.filer)}</td><td class="l">${x.url ? `<a class="ext" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">공시 뷰어</a>` : ""}</td></tr>`).join("")
      || `<tr><td class="l g" colspan="7">오늘 공시 없음</td></tr>`;
    $("q-src").textContent = `출처: ${r.source}`;
  });

  loadInfo().then((r) => { if (r) D.loadMine("disclosure", r.mine); });
})();
