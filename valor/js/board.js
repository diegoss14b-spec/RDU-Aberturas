// Mesa de Aberturas — por mercado · 3 colunas: jogo | mandante | visitante
(function () {
  var B = (window.BOARD || { jogos: [], mercados: [], casas: [], gerado: "?" });
  var jogos = B.jogos || [];
  var MERCADOS = B.mercados || ["Cartões", "Faltas", "Finalizações", "Chutes no gol", "Escanteios", "Impedimentos", "Laterais", "Tiros de meta", "Desarmes"];

  function firstMarket() {
    if (jogos.some(function (j) { return hasMkt(j, "Cartões"); })) return "Cartões";
    for (var i = 0; i < MERCADOS.length; i++) {
      var m = MERCADOS[i];
      if (jogos.some(function (j) { return hasMkt(j, m); })) return m;
    }
    return MERCADOS[0] || "Cartões";
  }

  function hasMkt(j, m) {
    return (j.mercados && j.mercados[m]) || (j.times && j.times[m]);
  }

  var state = { mercado: firstMarket(), soValor: false, ordem: "valor" };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function freshness() {
    var m = /(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(B.gerado || "");
    if (!m) return { txt: "?", stale: false };
    var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    var mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 0) mins = 0;
    var txt = mins < 1 ? "agora mesmo" : mins < 60 ? ("há " + mins + " min")
      : ("há " + Math.floor(mins / 60) + "h" + (mins % 60 ? " " + (mins % 60) + "min" : ""));
    return { txt: txt, stale: mins > 300 };
  }

  function chip(label, active, cls, onclick) {
    var c = document.createElement("span");
    c.className = "chip" + (cls ? " " + cls : "") + (active ? " on" : "");
    c.textContent = label;
    c.onclick = onclick;
    c.setAttribute("role", "button");
    c.tabIndex = 0;
    c.onkeydown = function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onclick(); }
    };
    return c;
  }

  function renderFiltros() {
    var box = document.getElementById("filtros");
    box.innerHTML = "";
    MERCADOS.forEach(function (m) {
      if (!jogos.some(function (j) { return hasMkt(j, m); })) return;
      box.appendChild(chip(m, state.mercado === m, "", function () {
        state.mercado = m;
        render();
      }));
    });
    box.appendChild(chip("🎯 só com valor", state.soValor, "val", function () {
      state.soValor = !state.soValor;
      render();
    }));
    var ords = [["valor", "＄ mais valor"], ["horario", "⏱ horário"], ["casas", "🏦 nº de casas"]];
    ords.forEach(function (o) {
      box.appendChild(chip(o[1], state.ordem === o[0], "ord", function () {
        state.ordem = o[0];
        render();
      }));
    });
  }

  function passa(j) {
    if (!hasMkt(j, state.mercado)) return false;
    if (state.soValor) {
      var has = (j.valor || []).some(function (v) { return v.mercado === state.mercado; });
      if (!has) return false;
    }
    return true;
  }

  function topEvMkt(j) {
    var best = -999;
    (j.valor || []).forEach(function (v) {
      if (v.mercado === state.mercado && v.ev_pct > best) best = v.ev_pct;
    });
    return best;
  }

  function sortFn(a, b) {
    if (state.ordem === "horario") return (a.inicio || "").localeCompare(b.inicio || "");
    if (state.ordem === "casas") {
      var na = Object.keys((a.mercados && a.mercados[state.mercado]) || {}).length;
      var nb = Object.keys((b.mercados && b.mercados[state.mercado]) || {}).length;
      return nb - na || (a.inicio || "").localeCompare(b.inicio || "");
    }
    var ea = topEvMkt(a), eb = topEvMkt(b);
    var va = ea > -999, vb = eb > -999;
    if (va !== vb) return va ? -1 : 1;
    if (va && vb && eb !== ea) return eb - ea;
    return (a.inicio || "").localeCompare(b.inicio || "");
  }

  function valMap(j) {
    var m = {};
    (j.valor || []).forEach(function (v) {
      m[v.mercado + "|" + v.linha + "|" + v.lado] = v;
    });
    return m;
  }

  /** Main line de uma casa: menor |over−under| (mais equilibrada). */
  function mainLineCasa(lines) {
    if (!lines || !lines.length) return null;
    var best = null, score = Infinity;
    lines.forEach(function (l) {
      var o = +l.over, u = +l.under;
      if (!(o > 1) || !(u > 1)) return;
      var s = Math.abs(o - u);
      var near = Math.abs((o + u) / 2 - 1.9);
      var sc = s * 10 + near;
      if (sc < score) { score = sc; best = l; }
    });
    return best || lines[Math.floor(lines.length / 2)];
  }

  /** Main line “do bloco”: moda das main lines por casa. */
  function pickMainLine(perCasa) {
    var casas = Object.keys(perCasa || {});
    if (!casas.length) return null;
    var votes = {};
    casas.forEach(function (c) {
      var ml = mainLineCasa(perCasa[c]);
      if (!ml) return;
      var L = ml.linha;
      votes[L] = (votes[L] || 0) + 1;
    });
    var bestL = null, bestV = -1;
    Object.keys(votes).forEach(function (L) {
      if (votes[L] > bestV) { bestV = votes[L]; bestL = +L; }
    });
    return bestL == null ? null : bestL;
  }

  function allLinhas(perCasa) {
    var set = {};
    Object.keys(perCasa || {}).forEach(function (c) {
      (perCasa[c] || []).forEach(function (l) { set[l.linha] = 1; });
    });
    return Object.keys(set).map(Number).sort(function (a, b) { return a - b; });
  }

  function lineRow(perCasa, L, vm, mercado) {
    var casas = Object.keys(perCasa);
    var vO = vm[mercado + "|" + L + "|Mais"];
    var vU = vm[mercado + "|" + L + "|Menos"];
    var cells = casas.map(function (c) {
      var row = (perCasa[c] || []).filter(function (x) { return +x.linha === +L; })[0];
      var o = row && row.over != null ? (+row.over).toFixed(2) : "—";
      var u = row && row.under != null ? (+row.under).toFixed(2) : "—";
      return '<td class="o">' + o + (vO && vO.casa === c ? '<span class="vtag">+' + vO.ev_pct.toFixed(0) + "%</span>" : "") + "</td>" +
        '<td class="u">' + u + (vU && vU.casa === c ? '<span class="vtag">+' + vU.ev_pct.toFixed(0) + "%</span>" : "") + "</td>";
    }).join("");
    return '<tr class="' + ((vO || vU) ? "val-row" : "") + '"><td class="ln">' + L + "</td>" + cells + "</tr>";
  }

  function ladderTable(perCasa, lines, vm, mercado) {
    var casas = Object.keys(perCasa || {});
    if (!casas.length || !lines.length) {
      return '<div class="col-empty">Sem linha aberta</div>';
    }
    var head = "<tr><th>Linha</th>" + casas.map(function (c) {
      return "<th>" + esc(c) + " +</th><th>" + esc(c) + " −</th>";
    }).join("") + "</tr>";
    var body = lines.map(function (L) { return lineRow(perCasa, L, vm, mercado); }).join("");
    return '<table class="lad"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  /** Uma coluna: jogo | mandante | visitante */
  function sideCol(opts) {
    var tag = opts.tag, title = opts.title, sub = opts.sub || "", perCasa = opts.perCasa || {};
    var vm = opts.vm, mercado = opts.mercado, kind = opts.kind;
    var linhas = allLinhas(perCasa);
    var mainL = pickMainLine(perCasa);
    if (mainL == null && linhas.length) mainL = linhas[Math.floor(linhas.length / 2)];
    var alts = linhas.filter(function (L) { return +L !== +mainL; });
    var casas = Object.keys(perCasa);

    var bodyMain = mainL != null
      ? ladderTable(perCasa, [mainL], vm, mercado)
      : '<div class="col-empty">—</div>';

    var altHtml = "";
    if (alts.length) {
      altHtml =
        '<button type="button" class="alt-btn col-alt" data-kind="' + kind + '" aria-expanded="false">' +
          '<span class="alt-arw">▸</span> ' + alts.length + " alt" +
        "</button>" +
        '<div class="alt-box" hidden data-kind="' + kind + '">' +
          ladderTable(perCasa, alts, vm, mercado) +
        "</div>";
    }

    return (
      '<div class="side-col side-' + kind + '">' +
        '<div class="side-h">' +
          '<span class="side-tag">' + esc(tag) + "</span>" +
          '<div class="side-titles">' +
            '<div class="side-title">' + esc(title) + "</div>" +
            (sub ? '<div class="side-sub">' + esc(sub) + "</div>" : "") +
          "</div>" +
          (mainL != null ? '<span class="side-ln">' + mainL + "</span>" : "") +
        "</div>" +
        '<div class="side-houses">' + (casas.length
          ? casas.map(function (c) { return '<span class="house">' + esc(c) + "</span>"; }).join("")
          : '<span class="side-none">sem casa</span>') +
        "</div>" +
        '<div class="side-body">' + bodyMain + "</div>" +
        altHtml +
      "</div>"
    );
  }

  function gameCard(j) {
    var mercado = state.mercado;
    var perCasa = (j.mercados && j.mercados[mercado]) || {};
    var times = (j.times && j.times[mercado]) || {};
    var home = times.home || null;
    var away = times.away || null;
    var vm = valMap(j);
    var casasMatch = Object.keys(perCasa);
    var allHouses = {};
    casasMatch.forEach(function (c) { allHouses[c] = 1; });
    if (home) Object.keys(home.casas || {}).forEach(function (c) { allHouses[c] = 1; });
    if (away) Object.keys(away.casas || {}).forEach(function (c) { allHouses[c] = 1; });

    var vals = (j.valor || []).filter(function (v) { return v.mercado === mercado; });
    var el = document.createElement("div");
    el.className = "game" + ((home || away) ? " game-3col" : "");

    var valStrip = "";
    if (vals.length) {
      valStrip = '<div class="val-strip">' + vals.slice(0, 4).map(function (v) {
        return '<span class="val-item">' + esc(v.lado) + " " + v.linha + " @ " + v.odd.toFixed(2) +
          ' <span class="ev">+' + v.ev_pct.toFixed(0) + "%</span> · " + esc(v.casa) + "</span>";
      }).join("") + "</div>";
    }

    var homeName = (home && home.nome) || j.home || "Mandante";
    var awayName = (away && away.nome) || j.away || "Visitante";
    // encurta nomes longos pra caber
    function short(n) {
      n = String(n || "");
      return n.length > 18 ? n.slice(0, 16) + "…" : n;
    }

    var grid =
      '<div class="side-grid">' +
        sideCol({
          kind: "match", tag: "Jogo", title: mercado, sub: "total da partida",
          perCasa: perCasa, vm: vm, mercado: mercado
        }) +
        sideCol({
          kind: "home", tag: "Time", title: short(homeName), sub: "mandante",
          perCasa: (home && home.casas) || {}, vm: {}, mercado: mercado
        }) +
        sideCol({
          kind: "away", tag: "Time", title: short(awayName), sub: "visitante",
          perCasa: (away && away.casas) || {}, vm: {}, mercado: mercado
        }) +
      "</div>";

    // se não há NENHUMA linha de time em nenhuma casa, ainda mostramos as colunas vazias
    // (usuário pediu layout fixo 3 colunas pra usar a tela)

    el.innerHTML =
      '<div class="g-top"><div><div class="g-name">' + esc(j.jogo) + "</div>" +
      '<div class="g-liga">' + esc(j.liga || "") + "</div>" +
      '<div class="houses">' + Object.keys(allHouses).map(function (c) {
        return '<span class="house">' + esc(c) + "</span>";
      }).join("") + "</div></div>" +
      '<div class="g-when">' + esc(j.inicio) + "</div></div>" +
      valStrip +
      grid;

    // wire alt toggles per column
    el.querySelectorAll(".alt-btn").forEach(function (btn) {
      var kind = btn.getAttribute("data-kind");
      var box = el.querySelector('.alt-box[data-kind="' + kind + '"]');
      if (!box) return;
      btn.onclick = function () {
        var open = box.hasAttribute("hidden");
        if (open) {
          box.removeAttribute("hidden");
          btn.classList.add("open");
          btn.setAttribute("aria-expanded", "true");
          btn.querySelector(".alt-arw").textContent = "▾";
        } else {
          box.setAttribute("hidden", "");
          btn.classList.remove("open");
          btn.setAttribute("aria-expanded", "false");
          btn.querySelector(".alt-arw").textContent = "▸";
        }
      };
    });
    return el;
  }

  function render() {
    renderFiltros();
    var vis = jogos.filter(passa).sort(sortFn);
    var fr = freshness();
    var meta = document.getElementById("meta");
    var nCasas = {};
    vis.forEach(function (j) {
      Object.keys((j.mercados && j.mercados[state.mercado]) || {}).forEach(function (c) { nCasas[c] = 1; });
      var t = (j.times && j.times[state.mercado]) || {};
      ["home", "away"].forEach(function (s) {
        if (t[s] && t[s].casas) Object.keys(t[s].casas).forEach(function (c) { nCasas[c] = 1; });
      });
    });
    meta.innerHTML =
      "<b>" + esc(state.mercado) + "</b> · " + vis.length + " jogo" + (vis.length === 1 ? "" : "s") +
      " · " + esc(Object.keys(nCasas).join(", ") || "—") +
      ' · <span class="fresh' + (fr.stale ? " stale" : "") + '">atualizado ' + esc(fr.txt) +
      (fr.stale ? " ⚠ (pode estar defasado)" : "") + "</span>" +
      ' · <span class="meta-hint">jogo · mandante · visitante</span>';

    var capEl = document.getElementById("capstatus");
    if (capEl) {
      var cap = B.capture;
      if (cap && ((cap.casas_ok || []).length || (cap.casas_fail || []).length)) {
        var okN = (cap.casas_ok || []).length, failN = (cap.casas_fail || []).length;
        var parts = (cap.casas_ok || []).map(function (c) {
          return '<span class="cap-ok">' + esc(c) + " ✓</span>";
        }).concat((cap.casas_fail || []).map(function (f) {
          return '<span class="cap-fail" title="' + esc(f.error || "") + '">' + esc(f.casa) + " ✗</span>";
        }));
        var cls = failN === 0 ? "cap-green" : (okN >= 3 ? "cap-yellow" : "cap-red");
        capEl.className = "capbar " + cls;
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

    var lista = document.getElementById("lista");
    lista.innerHTML = "";
    if (!vis.length) {
      lista.innerHTML = '<div class="empty"><div class="big">📭</div>Nenhum jogo com <b>' + esc(state.mercado) +
        "</b> aberto agora.<br><span style=\"font-size:12px\">Troque o mercado nos chips acima ou volte após a próxima captura.</span></div>";
      return;
    }
    vis.forEach(function (j) { lista.appendChild(gameCard(j)); });
  }

  var sub = document.querySelector("#view-board .sub");
  if (sub) {
    sub.innerHTML = "Escolha um <b>mercado</b> nos chips. Cada jogo abre em <b>3 colunas</b>: " +
      "<b>linha do jogo</b> (esquerda), <b>mandante</b> (meio) e <b>visitante</b> (direita). " +
      "Onde há modelo, marcamos <b style=\"color:var(--green)\">valor (+EV)</b>.";
  }
  var disc = document.querySelector("#view-board .disc");
  if (disc) {
    disc.innerHTML = "Odds capturadas num instante — <b>podem ter movido</b>. Main line = menor gap Mais/Menos. " +
      "Linhas de time só aparecem quando a casa publica (ex.: Superbet Finalizações / Betano Cartões).";
  }

  render();
})();
