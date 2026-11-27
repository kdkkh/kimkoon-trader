/* 통합 화면 공통 함수 — 껍데기와 모든 탭 화면이 함께 쓴다(window.Desk).
   Desk.get(경로, {값})      GET /api/… → JSON(실패해도 예외 없이 {ok:false, error})
   Desk.post(경로, {본문})    POST(세션 토큰 머리 X-Desk-Token 을 붙임)
   Desk.confirm(글, 단추 글)  확인 창 → Promise<참/거짓>
   Desk.every(함수, ms)      겹치지 않는 주기 실행(앞 호출이 끝나야 다음)
   Desk.esc · won · signed · cls · pnlCell · alias · maskName · maskTitle · $
   주소의 rec(녹화 틀) · mask(가림)는 껍데기에서 탭 화면 주소로 그대로 넘어오고, mask 는 API 호출에도 붙는다. */
(function () {
  "use strict";
  const P = new URLSearchParams(location.search);
  const meta = document.querySelector('meta[name="desk-token"]');
  const D = (window.Desk = {
    token: meta ? meta.getAttribute("content") : "",
    rec: P.has("rec") && P.get("rec") !== "0",
    mask: P.has("mask") && P.get("mask") !== "0",
    search: location.search,
  });
  if (D.rec) document.documentElement.classList.add("rec");

  D.$ = (id) => document.getElementById(id);
  D.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  D.MASKED = "•••";                 /* 서버가 ?mask=1 일 때 가격 · 금액 칸에 넣는 글(desk/core/mask.py MASKED) */
  D.won = (v) => (v === D.MASKED ? v : v == null || v === "" || isNaN(+v) ? "—" : Math.round(+v).toLocaleString("ko-KR"));
  D.signed = (v) => (v === D.MASKED ? v : v == null || v === "" || isNaN(+v) ? "—" : (+v > 0 ? "+" : "") + Math.round(+v).toLocaleString("ko-KR"));
  D.cls = (v) => (+v > 0 ? "up" : +v < 0 ? "down" : "");
  D.pnlCell = (v) => `<span class="num ${D.cls(v)}">${D.signed(v)}</span>`;
  D.pct = (v, n = 2) => (v == null || isNaN(+v) ? "—" : (+v > 0 ? "+" : "") + (+v).toFixed(n) + "%");

  function url(path, params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v !== undefined && v !== null && v !== "") q.set(k, v);
    if (D.mask) q.set("mask", "1");
    const s = q.toString();
    return path + (s ? "?" + s : "");
  }
  async function asJson(r) {
    try { return await r.json(); } catch (e) { return { ok: false, error: `응답을 읽지 못함(${r.status})` }; }
  }
  D.get = async (path, params) => {
    try { return await asJson(await fetch(url(path, params), { cache: "no-store" })); }
    catch (e) { return { ok: false, error: "서버에 닿지 않음" }; }
  };
  D.post = async (path, body) => {
    try {
      const r = await fetch(url(path), { method: "POST", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Desk-Token": D.token }, body: JSON.stringify(body || {}) });
      return await asJson(r);
    } catch (e) { return { ok: false, error: "서버에 닿지 않음" }; }
  };
  D.every = (fn, ms) => {
    let busy = false;
    const tick = async () => { if (busy) return; busy = true; try { await fn(); } catch (e) { /* 다음 주기에 다시 */ } busy = false; };
    tick();
    return setInterval(tick, ms);
  };
  D.confirm = (text, okText = "확인") => new Promise((res) => {
    const bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML = `<div class="modal"><div class="msg">${D.esc(text)}</div><div class="row">
      <button class="btn" data-a="0">취소</button><button class="btn blue" data-a="1">${D.esc(okText)}</button></div></div>`;
    bg.addEventListener("click", (e) => {
      const a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (a === null && e.target !== bg) return;
      bg.remove();
      res(a === "1");
    });
    document.body.appendChild(bg);
  });
  /* 녹화 가림: 같은 값은 같은 이름표로(화면 하나 안에서) */
  const AL = {};
  D.alias = (kind, key, head) => { const m = (AL[kind] = AL[kind] || new Map()); if (!m.has(key)) m.set(key, `${head} ${m.size + 1}`); return m.get(key); };
  D.maskName = (code, name) => (D.mask ? D.alias("name", code || name, "종목") : name || code);
  D.maskTitle = (s) => (D.mask ? `<span class="pill">제목 가림</span>` : D.esc(s));
})();
