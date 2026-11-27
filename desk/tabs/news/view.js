/* 뉴스 탭 화면 — 내 한투 키로 뉴스 제목(단추식, 저장하지 않음). 내 화면 전용 조각은 서버가 알려 줄 때만 불러온다. */
(function () {
  "use strict";
  const D = window.Desk, $ = D.$, esc = D.esc;
  const API = "/api/news/";
  D.panes.add("p-query", "조회(내 키)", $("p-query"));

  async function loadInfo() {
    const r = await D.get(API + "info");
    if (!r.ok) { $("q-msg").textContent = r.error || ""; return null; }
    if (!$("q-code").value) $("q-code").value = r.default_code || "";
    $("q-key").innerHTML = r.key ? '<span class="pill on">설정 파일 있음</span> <span class="g">kis_devlp.yaml · 실전 키 칸은 조회 때 확인</span>'
      : '<span class="pill warn">키 없음</span> <span class="g">kis_devlp.yaml(실전 키)을 설정 폴더에</span>';
    $("q-note").textContent = r.note + (r.running.length ? ` · 지금 도는 실행기: ${r.running.join(", ")}` : "");
    $("q-src").textContent = `출처: ${r.source}`;
    return r;
  }
  $("q-go").addEventListener("click", async () => {
    const code = $("q-code").value.trim(), n = +$("q-n").value || 40;
    if (!(await D.confirm(`한투 뉴스 제목을 ${code ? "종목 " + code + " 로 " : "전체에서 "}최근 ${n}건 조회합니다(실전 키 · 조회만, 주문 없음). 제목은 화면에만 보이고 저장하지 않습니다.`, "조회"))) return;
    $("q-msg").textContent = "조회 중…";
    const r = await D.post(API + "query", { code, n, confirm: true });
    if (!r.ok) { $("q-msg").textContent = r.message || r.error || "실패"; return; }
    $("q-msg").textContent = `${r.n}건 · ${r.note}`;
    $("q-meta").textContent = r.code ? `종목 ${D.maskName(r.code, r.code)}` : "전체";
    $("q-rows").innerHTML = r.rows.map((x) => `<tr><td class="l num">${esc(x.at)}</td><td class="l">${esc(x.provider)}</td>
      <td class="l num">${x.codes.map((c) => esc(D.maskName(c, c))).join(", ")}</td><td class="l wrap">${D.mask ? '<span class="pill">제목 가림</span>' : esc(x.title)}</td></tr>`).join("")
      || `<tr><td class="l g" colspan="4">없음</td></tr>`;
  });

  loadInfo().then((r) => { if (r) D.loadMine("news", r.mine); });
})();
