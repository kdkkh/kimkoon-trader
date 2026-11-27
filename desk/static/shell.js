/* 껍데기: /api/tabs 의 탭을 줄에 놓고, 처음 누를 때 그 탭 화면(/tabs/<id>/view.html)을 iframe 으로 연다.
   자료 없음 탭은 iframe 대신 "자료 없음: <설정 이름>"만 보인다. 주소의 rec · mask 는 탭 화면 주소로 넘긴다.
   주소 끝 #<탭 id> 로 처음 열 탭을 고를 수 있다. */
(function () {
  "use strict";
  const D = window.Desk;
  const panes = D.$("panes"), nav = D.$("tabs");
  const made = {};
  function show(t) {
    for (const b of nav.children) b.classList.toggle("on", b.dataset.id === t.id);
    for (const el of panes.children) el.classList.remove("on");
    if (!made[t.id]) {
      let el;
      if (t.ok) {
        el = document.createElement("iframe");
        el.src = `/tabs/${encodeURIComponent(t.id)}/view.html${location.search}`;
        el.title = t.name;
      } else {
        el = document.createElement("div");
        el.className = "missing";
        el.textContent = t.message || "자료 없음";
      }
      el.dataset.id = t.id;
      panes.appendChild(el);
      made[t.id] = el;
    }
    made[t.id].classList.add("on");
    if (location.hash !== "#" + t.id) history.replaceState(null, "", location.search + "#" + t.id);
  }
  async function boot() {
    const r = await D.get("/api/tabs");
    const tabs = (r && r.tabs) || [];
    D.$("loading").remove();
    if (!tabs.length) {
      panes.innerHTML = `<div class="empty">켜진 탭이 없습니다 — 설정 파일의 tabs 목록을 확인하세요.</div>`;
      return;
    }
    for (const t of tabs) {
      const b = document.createElement("button");
      b.className = "tab" + (t.ok ? "" : " miss");
      b.textContent = t.name;
      b.dataset.id = t.id;
      b.title = t.ok ? t.name : t.message;
      b.addEventListener("click", () => show(t));
      nav.appendChild(b);
    }
    const want = location.hash.slice(1);
    show(tabs.find((t) => t.id === want) || tabs[0]);
  }
  boot();
})();
