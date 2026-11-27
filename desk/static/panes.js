/* 탭 안의 작은 갈래(위쪽 단추 줄) + 내 화면 전용 조각 불러오기.
   Desk.panes.add(id, 이름, 요소, 보일 때 함수)   갈래 하나를 더한다(단추 줄 #subnav · 담을 곳 #panes)
   Desk.panes.show(id)
   Desk.loadMine(탭 id, mine)   서버가 알려 준 조각(mine = {KEY: {js: 참}})의 /tabs/<탭>/mine_<KEY>.js 만 부른다.
                                공개판에는 mine_* 파일이 없고 서버도 알려 주지 않으므로 아무것도 부르지 않는다.
   Desk.css(글)                  조각이 쓸 style 을 한 번 넣는다 */
(function () {
  "use strict";
  const D = window.Desk;
  const items = {};
  let current = "";
  D.panes = {
    add(id, label, el, onShow) {
      const nav = D.$("subnav"), box = D.$("panes");
      if (!nav || !box || items[id]) return;
      const b = document.createElement("button");
      b.className = "sub";
      b.textContent = label;
      b.dataset.pane = id;
      b.addEventListener("click", () => D.panes.show(id));
      nav.appendChild(b);
      el.classList.add("pane");
      el.dataset.pane = id;
      box.appendChild(el);
      items[id] = { b, el, onShow, shown: false };
      if (!current) D.panes.show(id);
      nav.classList.toggle("one", Object.keys(items).length < 2);
    },
    show(id) {
      const it = items[id];
      if (!it) return;
      current = id;
      for (const [k, v] of Object.entries(items)) { v.b.classList.toggle("on", k === id); v.el.classList.toggle("on", k === id); }
      if (it.onShow) { try { it.onShow(!it.shown); } catch (e) { /* 다음에 다시 */ } }
      it.shown = true;
      if (location.hash !== "#" + id) history.replaceState(null, "", location.search + "#" + id);
    },
    want() { return location.hash.slice(1); },
  };
  D.css = (text) => { const s = document.createElement("style"); s.textContent = text; document.head.appendChild(s); };
  D.loadMine = (tab, mine) => {
    for (const [key, v] of Object.entries(mine || {})) {
      if (!v || !v.js) continue;
      const s = document.createElement("script");
      s.src = `/tabs/${encodeURIComponent(tab)}/mine_${encodeURIComponent(key)}.js`;
      s.dataset.key = key;
      s.async = false;                       // 서버가 알려 준 순서대로 갈래가 붙게
      document.body.appendChild(s);
    }
  };
  D.loadScript = (src) => new Promise((res) => {
    if (document.querySelector(`script[src="${src}"]`)) return res(true);
    const s = document.createElement("script");
    s.src = src; s.onload = () => res(true); s.onerror = () => res(false);
    document.body.appendChild(s);
  });
})();
