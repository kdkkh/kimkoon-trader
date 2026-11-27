/* 마크다운 보기(읽기 전용) — 제목 · 표 · 목록 · 문단 · 굵게 · 코드만. 모든 글은 먼저 escape 한 뒤 꾸민다(html 을 그대로 넣지 않음).
   Desk.md(글) → html 문자열. 받아 둔 문서를 읽는 칸(내 화면 전용 조각)이 쓴다. */
(function () {
  "use strict";
  const D = window.Desk;
  const inline = (s) => D.esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const cells = (ln) => ln.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  D.md = function (text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    const out = [];
    let i = 0, para = [];
    const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
    while (i < lines.length) {
      const ln = lines[i];
      const t = ln.trim();
      if (!t) { flush(); i++; continue; }
      const h = /^(#{1,6})\s+(.*)$/.exec(t);
      if (h) { flush(); out.push(`<h${Math.min(h[1].length + 2, 6)} class="mdh">${inline(h[2])}</h${Math.min(h[1].length + 2, 6)}>`); i++; continue; }
      if (t.startsWith("|")) {
        flush();
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(cells(lines[i])); i++; }
        const sep = rows.length > 1 && rows[1].every((c) => /^:?-{2,}:?$/.test(c));
        const head = sep ? rows[0] : null, body = sep ? rows.slice(2) : rows;
        out.push(`<div class="mdtab"><table>${head ? "<thead><tr>" + head.map((c) => `<th class="l">${inline(c)}</th>`).join("") + "</tr></thead>" : ""}<tbody>`
          + body.map((r) => "<tr>" + r.map((c) => `<td class="l">${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>");
        continue;
      }
      if (/^[-*]\s+/.test(t) || /^\d+\.\s+/.test(t)) {
        flush();
        const items = [];
        while (i < lines.length && (/^\s*[-*]\s+/.test(lines[i]) || /^\s*\d+\.\s+/.test(lines[i]))) {
          items.push(lines[i].trim().replace(/^([-*]|\d+\.)\s+/, "")); i++;
        }
        out.push("<ul>" + items.map((x) => `<li>${inline(x)}</li>`).join("") + "</ul>");
        continue;
      }
      if (t.startsWith(">")) { flush(); out.push(`<blockquote>${inline(t.replace(/^>\s?/, ""))}</blockquote>`); i++; continue; }
      if (t.startsWith("```")) {
        flush(); i++;
        const buf = [];
        while (i < lines.length && !lines[i].trim().startsWith("```")) { buf.push(lines[i]); i++; }
        i++;
        out.push(`<pre class="out">${D.esc(buf.join("\n"))}</pre>`);
        continue;
      }
      para.push(t); i++;
    }
    flush();
    return `<div class="md">${out.join("")}</div>`;
  };
})();
