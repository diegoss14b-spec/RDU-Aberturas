// Mesa de Aberturas — render do board a partir de window.BOARD
(function () {
  var B = (window.BOARD || { jogos: [], mercados: [], casas: [], gerado: "?" });
  var jogos = B.jogos || [];
  var MERCADOS = B.mercados || ["Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta"];
  var state = { mercado: "todos", soValor: false, ordem: "valor" };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // --- frescor dos dados ---
  function freshness() {
    var m = /(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(B.gerado || "");
    if (!m) return { txt: "?", stale: false };
    var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    var mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 0) mins = 0;
    var txt = mins < 1 ? "agora mesmo" : mins < 60 ? ("há " + mins + " min")
              : ("há " + Math.floor(mins / 60) + "h" + (mins % 60 ? " " + (mins % 60) + "min" : ""));
    return { txt: txt, stale: mins > 300 };   // >5h = perdeu um ciclo (captura é 4/4h)
  }

  function chip(label, active, cls, onclick) {
    var c = document.createElement("span");
    c.className = "chip" + (cls ? " " + cls : "") + (active ? " on" : "");
    c.textContent = label; c.onclick = onclick;
    c.setAttribute("role", "button"); c.tabIndex = 0;
    c.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onclick(); } };
    return c;
  }

  function renderFiltros() {
    var box = document.getElementById("filtros"); box.innerHTML = "";
    box.appendChild(chip("Todos mercados", state.mercado === "todos", "", function () { state.mercado = "todos"; render(); }));
    MERCADOS.forEach(function (m) {
      if (!jogos.some(function (j) { return j.mercados[m]; })) return;
      box.appendChild(chip(m, state.mercado === m, "", function () { state.mercado = m; render(); }));
    });
    box.appendChild(chip("🎯 só com valor", state.soValor, "val", function () { state.soValor = !state.soValor; render(); }));
    // ordenação
    var ords = [["valor", "＄ mais valor"], ["horario", "⏱ horário"], ["casas", "🏦 nº de casas"]];
    ords.forEach(function (o) {
      box.appendChild(chip(o[1], state.ordem === o[0], "ord", function () { state.ordem = o[0]; render(); }));
    });
  }

  function passa(j) {
    if (state.soValor && !j.tem_valor) return false;
    if (state.mercado !== "todos" && !j.mercados[state.mercado]) return false;
    return true;
  }

  function topEv(j) { return (j.valor && j.valor.length) ? j.valor[0].ev_pct : -999; }
  function sortFn(a, b) {
    if (state.ordem === "horario") return (a.inicio || "").localeCompare(b.inicio || "");
    if (state.ordem === "casas") return (b.casas || []).length - (a.casas || []).length || (a.inicio || "").localeCompare(b.inicio || "");
    // valor (default): com valor primeiro, por EV desc, depois horário
    if (a.tem_valor !== b.tem_valor) return a.tem_valor ? -1 : 1;
    if (a.tem_valor && b.tem_valor) return topEv(b) - topEv(a);
    return (a.inicio || "").localeCompare(b.inicio || "");
  }

  function valMap(j) {
    var m = {};
    (j.valor || []).forEach(function (v) { m[v.mercado + "|" + v.linha + "|" + v.lado] = v; });
    return m;
  }

  function ladder(j, mercado, vm) {
    var perCasa = j.mercados[mercado];
    var casas = Object.keys(perCasa);
    var linhas = {};
    casas.forEach(function (c) { perCasa[c].forEach(function (l) { linhas[l.linha] = 1; }); });
    var ord = Object.keys(linhas).map(Number).sort(function (a, b) { return a - b; });
    var head = "<tr><th>Linha</th>" + casas.map(function (c) { return "<th>" + esc(c) + " +</th><th>" + esc(c) + " −</th>"; }).join("") + "</tr>";
    var body = ord.map(function (L) {
      var vO = vm[mercado + "|" + L + "|Mais"], vU = vm[mercado + "|" + L + "|Menos"];
      var cells = casas.map(function (c) {
        var row = (perCasa[c] || []).filter(function (x) { return x.linha === L; })[0];
        var o = row ? row.over.toFixed(2) : "—", u = row ? row.under.toFixed(2) : "—";
        return '<td class="o">' + o + (vO && vO.casa === c ? '<span class="vtag">+' + vO.ev_pct.toFixed(0) + '%</span>' : "") + '</td>' +
               '<td class="u">' + u + (vU && vU.casa === c ? '<span class="vtag">+' + vU.ev_pct.toFixed(0) + '%</span>' : "") + '</td>';
      }).join("");
      return '<tr class="' + ((vO || vU) ? "val-row" : "") + '"><td class="ln">' + L + "</td>" + cells + "</tr>";
    }).join("");
    return '<table class="lad"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  function gameCard(j) {
    var vm = valMap(j);
    var el = document.createElement("div"); el.className = "game";
    var valStrip = "";
    if (j.tem_valor) {
      valStrip = '<div class="val-strip">' + j.valor.slice(0, 4).map(function (v) {
        return '<span class="val-item" data-mkt="' + esc(v.mercado) + '" role="button" tabindex="0" title="ver a escada de ' + esc(v.mercado) + '">' +
          esc(v.mercado) + " " + v.lado + " " + v.linha + " @ " + v.odd.toFixed(2) + ' <span class="ev">+' + v.ev_pct.toFixed(0) + "%</span></span>";
      }).join("") + "</div>";
    }
    var mkts = MERCADOS.filter(function (m) { return j.mercados[m]; }).map(function (m) {
      var perCasa = j.mercados[m];
      var allL = [];
      Object.keys(perCasa).forEach(function (c) { perCasa[c].forEach(function (l) { allL.push(l.linha); }); });
      var mn = Math.min.apply(null, allL), mx = Math.max.apply(null, allL);
      var nL = Object.keys(allL.reduce(function (a, x) { a[x] = 1; return a; }, {})).length;
      var hasVal = (j.valor || []).some(function (v) { return v.mercado === m; });
      return '<div class="mkt" data-mkt="' + esc(m) + '">' +
        '<div class="mkt-h" role="button" tabindex="0" aria-expanded="false"><span class="nm">' + esc(m) + (hasVal ? ' 🎯' : "") + '</span>' +
        '<span class="ct">' + nL + ' linha' + (nL === 1 ? "" : "s") + '</span>' +
        '<span class="rng">' + mn + "–" + mx + '</span><span class="arw" aria-hidden="true">▸</span></div>' +
        '<div class="mkt-b">' + ladder(j, m, vm) + '</div></div>';
    }).join("");
    el.innerHTML =
      '<div class="g-top"><div><div class="g-name">' + esc(j.jogo) + '</div>' +
      '<div class="g-liga">' + esc(j.liga) + '</div>' +
      '<div class="houses">' + (j.casas || []).map(function (c) { return '<span class="house">' + esc(c) + "</span>"; }).join("") + '</div></div>' +
      '<div class="g-when">' + esc(j.inicio) + "</div></div>" +
      valStrip +
      '<div class="mkts">' + mkts + "</div>";

    function toggle(mktEl) {
      var open = mktEl.classList.toggle("open");
      mktEl.querySelector(".mkt-h").setAttribute("aria-expanded", open ? "true" : "false");
      return open;
    }
    el.querySelectorAll(".mkt-h").forEach(function (h) {
      h.onclick = function () { toggle(h.parentElement); };
      h.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(h.parentElement); } };
    });
    // badge de valor → abre e rola até o mercado correspondente
    el.querySelectorAll(".val-item").forEach(function (b) {
      function go() {
        var mk = el.querySelector('.mkt[data-mkt="' + (b.getAttribute("data-mkt") || "").replace(/"/g, '\\"') + '"]');
        if (mk) { if (!mk.classList.contains("open")) toggle(mk); mk.scrollIntoView({ behavior: "smooth", block: "center" }); }
      }
      b.onclick = go;
      b.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    });
    return el;
  }

  function render() {
    renderFiltros();
    var vis = jogos.filter(passa).sort(sortFn);
    var fr = freshness();
    var meta = document.getElementById("meta");
    meta.innerHTML = vis.length + " jogo" + (vis.length === 1 ? "" : "s") + " com mercado aberto · " +
      esc((B.casas || []).join(", ")) + ' · <span class="fresh' + (fr.stale ? " stale" : "") + '">atualizado ' + esc(fr.txt) +
      (fr.stale ? " ⚠ (pode estar defasado)" : "") + "</span>";
    // transparência da captura: quais casas entraram/falharam nesta rodada
    var capEl = document.getElementById("capstatus");
    if (capEl) {
      var cap = B.capture;
      if (cap && ((cap.casas_ok || []).length || (cap.casas_fail || []).length)) {
        var okN = (cap.casas_ok || []).length, failN = (cap.casas_fail || []).length;
        var parts = (cap.casas_ok || []).map(function (c) { return '<span class="cap-ok">' + esc(c) + " ✓</span>"; })
          .concat((cap.casas_fail || []).map(function (f) { return '<span class="cap-fail" title="' + esc(f.error || "") + '">' + esc(f.casa) + " ✗</span>"; }));
        var cls = failN === 0 ? "cap-green" : (okN >= 3 ? "cap-yellow" : "cap-red");
        capEl.className = "capbar " + cls;
        // confiabilidade 7 dias por casa (rodadas ok / rodadas totais)
        var histTxt = "";
        if (cap.hist7) {
          var hs = Object.keys(cap.hist7).map(function (c) {
            var h = cap.hist7[c], pct = h.total ? Math.round(100 * h.ok / h.total) : 0;
            return c + " " + pct + "% (" + h.ok + "/" + h.total + ")";
          });
          histTxt = '<div class="cap-note">Últimos 7 dias: ' + hs.join(" · ") + "</div>";
        }
        capEl.innerHTML = "Casas nesta rodada: " + parts.join(" · ") +
          (failN ? '<div class="cap-note">Captura incompleta — mercados podem existir nas casas marcadas com ✗ e não aparecer aqui.</div>' : "") +
          histTxt;
        capEl.style.display = "";
      } else {
        capEl.style.display = "none";
      }
    }
    var lista = document.getElementById("lista"); lista.innerHTML = "";
    if (!vis.length) {
      lista.innerHTML = '<div class="empty"><div class="big">📭</div>Nenhum jogo com esses mercados abertos agora.<br><span style="font-size:12px">As casas abrem os mercados de estatística mais perto do jogo. Volte após a próxima captura.</span></div>';
      return;
    }
    vis.forEach(function (j) { lista.appendChild(gameCard(j)); });
  }

  render();
})();
