// Mesa de Aberturas — linha colapsada por jogo + filtros mercado × casa (20/07/2026)
// Clique no jogo expande os detalhes (linhas do jogo/mandante/visitante por casa).
(function () {
  var B = (window.BOARD || { jogos: [], mercados: [], casas: [], gerado: "?" });
  var jogos = B.jogos || [];
  var MERCADOS = B.mercados || ["Cartões", "Faltas", "Finalizações", "Chutes no gol", "Escanteios", "Impedimentos", "Laterais", "Tiros de meta", "Desarmes"];
  var ABBR = { "Cartões": "CAR", "Faltas": "FAL", "Finalizações": "FIN", "Chutes no gol": "CG",
    "Escanteios": "ESC", "Impedimentos": "IMP", "Laterais": "LAT", "Tiros de meta": "TM", "Desarmes": "DES" };
  var LOGO = window.casaLogo || function (c) { return esc(c); };

  function hasMkt(j, m) {
    return (j.mercados && j.mercados[m]) || (j.times && j.times[m]);
  }

  // P0.4 — thresholds de idade da mesa (horas) pro banner; fácil de mudar aqui.
  var AGE_WARN_H = 8, AGE_CRIT_H = 12;

  // ---- filtros persistentes (localStorage) ----
  var LS_KEY = "rdu_mesa_filtros";
  function loadFilt() {
    try {
      var raw = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
      return {
        mercado: typeof raw.mercado === "string" ? raw.mercado : "todos",
        casa: typeof raw.casa === "string" ? raw.casa : "todas",
        soValor: !!raw.soValor,
        ordem: ["valor", "horario", "casas"].indexOf(raw.ordem) >= 0 ? raw.ordem : "valor"
      };
    } catch (e) { return { mercado: "todos", casa: "todas", soValor: false, ordem: "valor" }; }
  }
  function saveFilt() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        mercado: state.mercado, casa: state.casa, soValor: state.soValor, ordem: state.ordem
      }));
    } catch (e) { /* privado/quota — segue sem persistir */ }
  }
  var _f = loadFilt();
  var state = { mercado: _f.mercado, casa: _f.casa, soValor: _f.soValor,
    ordem: _f.ordem, mostrarTodos: false, expanded: {} };
  // filtro salvo pode apontar pra mercado/casa que não existe mais nesta board
  if (state.mercado !== "todos" && !jogos.some(function (j) { return hasMkt(j, state.mercado); })) state.mercado = "todos";
  if (state.casa !== "todas" && (B.casas || []).indexOf(state.casa) < 0) state.casa = "todas";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function sameCasa(a, b) {
    return String(a || "").toLowerCase() === String(b || "").toLowerCase();
  }

  /** Aplica o filtro de casa num objeto {casa: linhas}. */
  function filterCasas(perCasa) {
    if (state.casa === "todas" || !perCasa) return perCasa || {};
    var out = {};
    Object.keys(perCasa).forEach(function (c) {
      if (sameCasa(c, state.casa)) out[c] = perCasa[c];
    });
    return out;
  }

  function parseBrt(value) {
    if (!value) return null;
    var text = String(value);
    var ms = Date.parse(text);
    if (!isNaN(ms) && /(?:Z|[+-]\d{2}:?\d{2})$/.test(text)) return ms;
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
    if (!m) return isNaN(ms) ? null : ms;
    return Date.parse(m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + (m[6] || "00") + "-03:00");
  }

  function gameEpoch(j) {
    var ms = parseBrt(j && j.inicio_iso);
    if (ms != null) return ms;
    var m = /(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/.exec((j && j.inicio) || "");
    if (!m) return Number.MAX_SAFE_INTEGER;
    var year = new Date().getFullYear();
    return Date.parse(year + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[1]).slice(-2) +
      "T" + ("0" + m[3]).slice(-2) + ":" + m[4] + ":00-03:00");
  }
  function liveGameState(j, nowMs) {
    if (j && j.game_state === "finished") return "finished";
    var kickoff = gameEpoch(j);
    if (!isFinite(kickoff) || kickoff === Number.MAX_SAFE_INTEGER) return "unknown";
    var now = typeof nowMs === "number" ? nowMs : Date.now();
    return now >= kickoff ? "started" : "upcoming";
  }

  function freshness() {
    var ms = parseBrt(B.gerado_iso || B.gerado);
    if (ms == null) return { txt: "?", mins: null, stale: true, band: "unk" };
    var mins = Math.round((Date.now() - ms) / 60000);
    if (mins < 0) mins = 0;
    var txt = mins < 1 ? "agora mesmo" : mins < 60 ? ("há " + mins + " min")
      : ("há " + Math.floor(mins / 60) + "h" + (mins % 60 ? " " + (mins % 60) + "min" : ""));
    var band = mins <= 10 ? "fresh" : mins <= 60 ? "mid" : "old";
    return { txt: txt, mins: mins, stale: mins > 120, band: band };
  }

  function chip(label, active, cls, onclick, isHtml) {
    var c = document.createElement("span");
    c.className = "chip" + (cls ? " " + cls : "") + (active ? " on" : "");
    if (isHtml) c.innerHTML = label; else c.textContent = label;
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

    // linha 1: MERCADO (Todos + cada mercado com cobertura)
    var rowM = document.createElement("div"); rowM.className = "bar";
    rowM.appendChild(chip("Todos os mercados", state.mercado === "todos", "", function () {
      state.mercado = "todos"; saveFilt(); render();
    }));
    MERCADOS.forEach(function (m) {
      if (!jogos.some(function (j) { return hasMkt(j, m); })) return;
      rowM.appendChild(chip(m, state.mercado === m, "", function () {
        state.mercado = (state.mercado === m ? "todos" : m);
        saveFilt(); render();
      }));
    });
    box.appendChild(rowM);

    // linha 2: CASA (Todas + logo de cada casa) + toggles + ordenação
    var rowC = document.createElement("div"); rowC.className = "bar";
    rowC.appendChild(chip("Todas as casas", state.casa === "todas", "", function () {
      state.casa = "todas"; saveFilt(); render();
    }));
    (B.casas || []).forEach(function (c) {
      rowC.appendChild(chip(LOGO(c, "house-logo-sm"), sameCasa(state.casa, c), "", function () {
        state.casa = sameCasa(state.casa, c) ? "todas" : c;
        saveFilt(); render();
      }, true));
    });
    rowC.appendChild(chip("🎯 só com valor", state.soValor, "val", function () {
      state.soValor = !state.soValor; saveFilt(); render();
    }));
    // P0.4 — por padrão só jogos próximos; liga pra ver ao vivo/encerrados.
    rowC.appendChild(chip(state.mostrarTodos ? "👁 mostrando ao vivo/encerrados" : "👁 ver ao vivo/encerrados", state.mostrarTodos, "", function () {
      state.mostrarTodos = !state.mostrarTodos;
      render();
    }));
    var ords = [["valor", "＄ mais valor"], ["horario", "⏱ horário"], ["casas", "🏦 nº de casas"]];
    ords.forEach(function (o) {
      rowC.appendChild(chip(o[1], state.ordem === o[0], "ord", function () {
        state.ordem = o[0]; saveFilt(); render();
      }));
    });
    box.appendChild(rowC);
  }

  /** Mercados do jogo que passam nos filtros mercado × casa. */
  function marketsOf(j) {
    var list = state.mercado === "todos" ? MERCADOS : [state.mercado];
    return list.filter(function (m) {
      if (!hasMkt(j, m)) return false;
      if (state.casa === "todas") return true;
      var per = (j.mercados && j.mercados[m]) || {};
      if (Object.keys(per).some(function (c) { return sameCasa(c, state.casa); })) return true;
      var t = (j.times && j.times[m]) || {};
      return ["home", "away"].some(function (s) {
        return t[s] && t[s].casas && Object.keys(t[s].casas).some(function (c) { return sameCasa(c, state.casa); });
      });
    });
  }

  function valsOf(j) {
    return (j.valor || []).filter(function (v) {
      if (state.mercado !== "todos" && v.mercado !== state.mercado) return false;
      if (state.casa !== "todas" && !sameCasa(v.casa, state.casa)) return false;
      return true;
    });
  }

  function passa(j) {
    if (!marketsOf(j).length) return false;
    // P0.4 — default só upcoming; o toggle mostra ao vivo/encerrados. game_state ausente = mostra.
    if (!state.mostrarTodos && j.game_state && j.game_state !== "upcoming") return false;
    if (state.soValor && !valsOf(j).length) return false;
    return true;
  }

  function topEv(j) {
    var best = -999;
    valsOf(j).forEach(function (v) { if (v.ev_pct > best) best = v.ev_pct; });
    return best;
  }

  function nCasasDo(j) {
    var set = {};
    marketsOf(j).forEach(function (m) {
      Object.keys(filterCasas((j.mercados && j.mercados[m]) || {})).forEach(function (c) { set[c] = 1; });
      var t = (j.times && j.times[m]) || {};
      ["home", "away"].forEach(function (s) {
        if (t[s] && t[s].casas) Object.keys(filterCasas(t[s].casas)).forEach(function (c) { set[c] = 1; });
      });
    });
    return Object.keys(set);
  }

  function sortFn(a, b) {
    if (state.ordem === "horario") return gameEpoch(a) - gameEpoch(b);
    if (state.ordem === "casas") {
      return nCasasDo(b).length - nCasasDo(a).length || gameEpoch(a) - gameEpoch(b);
    }
    var ea = topEv(a), eb = topEv(b);
    var va = ea > -999, vb = eb > -999;
    if (va !== vb) return va ? -1 : 1;
    if (va && vb && eb !== ea) return eb - ea;
    return gameEpoch(a) - gameEpoch(b);
  }

  function valMap(j) {
    var m = {};
    (j.valor || []).forEach(function (v) {
      m[v.mercado + "|" + v.linha + "|" + v.lado] = v;
    });
    return m;
  }

  /** Descarta linhas de time misturadas no total de partida (ex. Fin 10.5 vs 24.5). */
  function matchOnlyLines(lines, mercado) {
    if (!lines || !lines.length) return lines || [];
    var arr = lines.slice().sort(function (a, b) { return a.linha - b.linha; });
    var maxL = arr[arr.length - 1].linha;
    if (mercado === "Finalizações" && maxL >= 18) {
      return arr.filter(function (l) { return +l.linha >= 16.5; });
    }
    if (mercado === "Faltas" && maxL >= 20) {
      return arr.filter(function (l) { return +l.linha >= 16.5; });
    }
    if (mercado === "Escanteios" && maxL >= 9) {
      return arr.filter(function (l) { return +l.linha >= 6.5; });
    }
    return arr;
  }

  /** Main line de uma casa: menor |over−under| (mais equilibrada). */
  function mainLineCasa(lines, mercado) {
    lines = matchOnlyLines(lines, mercado);
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
  function pickMainLine(perCasa, mercado) {
    var casas = Object.keys(perCasa || {});
    if (!casas.length) return null;
    var votes = {};
    casas.forEach(function (c) {
      var ml = mainLineCasa(perCasa[c], mercado);
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
      return "<th>" + LOGO(c, "house-logo-sm") + " +</th><th>" + LOGO(c, "house-logo-sm") + " −</th>";
    }).join("") + "</tr>";
    var body = lines.map(function (L) { return lineRow(perCasa, L, vm, mercado); }).join("");
    return '<table class="lad"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  /** Uma coluna: jogo | mandante | visitante */
  function sideCol(opts) {
    var tag = opts.tag, title = opts.title, sub = opts.sub || "", perCasa = opts.perCasa || {};
    var vm = opts.vm, mercado = opts.mercado, kind = opts.kind;
    var linhas = allLinhas(perCasa);
    var mainL = pickMainLine(perCasa, mercado);
    if (mainL == null && linhas.length) {
      var only = matchOnlyLines(linhas.map(function (L) { return { linha: L, over: 2, under: 2 }; }), mercado);
      mainL = only.length ? only[Math.floor(only.length / 2)].linha : linhas[Math.floor(linhas.length / 2)];
    }
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
          ? casas.map(function (c) { return LOGO(c); }).join("")
          : '<span class="side-none">sem casa</span>') +
        "</div>" +
        '<div class="side-body">' + bodyMain + "</div>" +
        altHtml +
      "</div>"
    );
  }

  /** Bloco expandido de UM mercado: valor + 3 colunas (jogo | mandante | visitante). */
  function marketBlock(j, mercado, staleCasas, valActionable, gsLabel, frCard) {
    var vm = valMap(j);
    var perCasa = filterCasas((j.mercados && j.mercados[mercado]) || {});
    var times = (j.times && j.times[mercado]) || {};
    var home = times.home || null;
    var away = times.away || null;

    var vals = (j.valor || []).filter(function (v) {
      if (v.mercado !== mercado) return false;
      if (state.casa !== "todas" && !sameCasa(v.casa, state.casa)) return false;
      return true;
    });
    var valStrip = "";
    if (vals.length && valActionable) {
      // stale por último e sem o selo verde de EV (odd pode ter desatualizado)
      var valsOrd = vals.slice().sort(function (a, b) {
        var sa = staleCasas[a.casa] ? 1 : 0, sb = staleCasas[b.casa] ? 1 : 0;
        return sa - sb;
      });
      valStrip = '<div class="val-strip">' + valsOrd.slice(0, 4).map(function (v) {
        var st = staleCasas[v.casa];
        return '<span class="val-item' + (st ? " val-stale" : "") + '"' +
          (st ? ' title="Odd da casa reutilizada (stale) — pode estar desatualizada; não conta como melhor preço"' : "") + ">" +
          esc(v.lado) + " " + v.linha + " @ " + v.odd.toFixed(2) +
          (st ? ' <span class="ev-stale">⚠ stale</span>' : ' <span class="ev">+' + v.ev_pct.toFixed(0) + "%</span>") +
          " · " + LOGO(v.casa, "house-logo-sm") + "</span>";
      }).join("") + "</div>";
    } else if (vals.length && !valActionable) {
      // Fail closed: iniciado/encerrado/board stale nunca mostra a faixa acionável.
      valStrip = '<div class="val-strip muted">' +
        (frCard.stale ? "Board desatualizado" : ("Jogo " + esc(gsLabel))) +
        " — valor não acionável</div>";
    }

    var homeName = (home && home.nome) || j.home || "Mandante";
    var awayName = (away && away.nome) || j.away || "Visitante";
    function short(n) {
      n = String(n || "");
      return n.length > 18 ? n.slice(0, 16) + "…" : n;
    }

    var nCasas = Object.keys(perCasa).length;
    return (
      '<div class="gr-mkt-block">' +
        '<div class="gr-mkt-title">' + esc(mercado) +
          '<span class="ct">' + nCasas + " casa" + (nCasas === 1 ? "" : "s") + "</span></div>" +
        valStrip +
        '<div class="side-grid">' +
          sideCol({ kind: "match", tag: "Jogo", title: mercado, sub: "total da partida",
            perCasa: perCasa, vm: vm, mercado: mercado }) +
          sideCol({ kind: "home", tag: "Time", title: short(homeName), sub: "mandante",
            perCasa: filterCasas((home && home.casas) || {}), vm: {}, mercado: mercado }) +
          sideCol({ kind: "away", tag: "Time", title: short(awayName), sub: "visitante",
            perCasa: filterCasas((away && away.casas) || {}), vm: {}, mercado: mercado }) +
        "</div>" +
      "</div>"
    );
  }

  function gameKey(j) {
    return j.sofa_id ? ("s:" + j.sofa_id) : ((j.jogo || "?") + "|" + (j.inicio || "?"));
  }

  /** Corpo expandido do jogo: um bloco por mercado (respeitando os filtros). */
  function buildBody(j) {
    var frCard = freshness();
    var gs = liveGameState(j);
    var gsLabel = { upcoming: "próximo", started: "iniciado", finished: "encerrado", unknown: "sem horário" }[gs] || gs;
    var valActionable = gs === "upcoming" && !frCard.stale;
    var staleCasas = {};
    (j.stale_casas || []).forEach(function (c) { staleCasas[c] = 1; });
    var mkts = marketsOf(j);
    if (!mkts.length) return '<div class="gr-none">Nenhum mercado com esse filtro.</div>';
    var staleNote = (j.stale_casas && j.stale_casas.length)
      ? '<div class="g-stale-note">⚠ casa reutilizada (full anterior): ' + esc(j.stale_casas.join(", ")) + "</div>"
      : "";
    return staleNote + mkts.map(function (m) {
      return marketBlock(j, m, staleCasas, valActionable, gsLabel, frCard);
    }).join("");
  }

  function wireAltButtons(el) {
    el.querySelectorAll(".alt-btn").forEach(function (btn) {
      var box = btn.nextElementSibling;
      if (!box || !box.classList.contains("alt-box")) return;
      btn.onclick = function (e) {
        e.stopPropagation();
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
  }

  /** Linha COMPACTA do jogo (default). Clique expande os detalhes. */
  function gameRow(j) {
    var key = gameKey(j);
    var frCard = freshness();
    var gs = liveGameState(j);
    var gsLabel = { upcoming: "próximo", started: "iniciado", finished: "encerrado", unknown: "sem horário" }[gs] || gs;
    var mkts = marketsOf(j);
    var casas = nCasasDo(j);
    var vals = valsOf(j);
    var bestEv = topEv(j);
    var valActionable = gs === "upcoming" && !frCard.stale;

    var pills = mkts.map(function (m) {
      var perCasa = filterCasas((j.mercados && j.mercados[m]) || {});
      var mainL = pickMainLine(perCasa, m);
      return '<span class="gr-pill" title="' + esc(m) + (mainL != null ? " · main line " + mainL : "") + '">' +
        (ABBR[m] || esc(m)) + (mainL != null ? " <b>" + mainL + "</b>" : "") + "</span>";
    }).join("");

    var valBadge = (vals.length && valActionable)
      ? '<span class="gr-val" title="Melhor EV do modelo neste jogo (com os filtros atuais)">🎯 +' + bestEv.toFixed(0) + "%</span>"
      : "";

    var el = document.createElement("div");
    el.className = "gr" + (state.expanded[key] ? " open" : "");
    el.innerHTML =
      '<div class="gr-head" role="button" tabindex="0" aria-expanded="' + (state.expanded[key] ? "true" : "false") + '">' +
        '<span class="gr-arw">▸</span>' +
        '<span class="gr-when">' + esc(j.inicio) + "</span>" +
        '<div class="gr-names">' +
          '<div class="gr-title">' + esc(j.jogo) +
            ' <span class="fresh-dot ' + frCard.band + '" title="Frescor da mesa: ' + esc(frCard.txt) + '"></span>' +
            (gs !== "upcoming" ? ' <span class="g-state ' + esc(gs) + '">' + esc(gsLabel) + "</span>" : "") +
          "</div>" +
          '<div class="gr-liga">' + esc(j.liga || "") + "</div>" +
        "</div>" +
        '<div class="gr-sum">' + valBadge + pills + "</div>" +
        '<div class="gr-houses">' + casas.map(function (c) {
          var st = (j.stale_casas || []).indexOf(c) >= 0;
          return st
            ? '<span title="' + esc(c) + ' — inventário antigo (stale-keep)" style="opacity:.55">' + LOGO(c) + "</span>"
            : LOGO(c);
        }).join("") + "</div>" +
      "</div>" +
      '<div class="gr-body"></div>';

    var head = el.querySelector(".gr-head");
    var body = el.querySelector(".gr-body");
    function fill() {
      if (!body.getAttribute("data-filled")) {
        body.innerHTML = buildBody(j);
        wireAltButtons(body);
        body.setAttribute("data-filled", "1");
      }
    }
    if (state.expanded[key]) fill();
    function toggle() {
      var open = !state.expanded[key];
      state.expanded[key] = open;
      if (open) fill();
      el.classList.toggle("open", open);
      head.setAttribute("aria-expanded", open ? "true" : "false");
    }
    head.onclick = toggle;
    head.onkeydown = function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    };
    return el;
  }

  function render() {
    renderFiltros();
    var vis = jogos.filter(passa).sort(sortFn);
    var fr = freshness();
    // P0.4 — banner de board velha (≥8h vermelho, ≥12h crítico)
    var ageEl = document.getElementById("boardage");
    if (ageEl) {
      var ageH = fr.mins == null ? null : fr.mins / 60;
      if (ageH != null && ageH >= AGE_CRIT_H) {
        ageEl.className = "age-banner age-crit";
        ageEl.innerHTML = "🛑 <b>Mesa MUITO desatualizada</b> — gerada há " + Math.floor(ageH) + "h. NÃO use para decisão; espere uma nova captura.";
      } else if (ageH != null && ageH >= AGE_WARN_H) {
        ageEl.className = "age-banner age-red";
        ageEl.innerHTML = "⚠ <b>Mesa desatualizada</b> (gerada há " + Math.floor(ageH) + "h). Não use para decisão sem recaptura — as odds provavelmente já moveram.";
      } else {
        ageEl.className = "";
        ageEl.innerHTML = "";
      }
    }
    var meta = document.getElementById("meta");
    var nCasas = {};
    vis.forEach(function (j) {
      nCasasDo(j).forEach(function (c) { nCasas[c] = 1; });
    });
    meta.innerHTML =
      "<b>" + esc(state.mercado === "todos" ? "Todos os mercados" : state.mercado) + "</b>" +
      (state.casa !== "todas" ? " · <b>" + esc(state.casa) + "</b>" : "") +
      " · " + vis.length + " jogo" + (vis.length === 1 ? "" : "s") +
      (!state.mostrarTodos ? " (só próximos)" : "") +
      " · " + esc(Object.keys(nCasas).join(", ") || "—") +
      ' · <span class="fresh fresh-' + fr.band + (fr.stale ? " stale" : "") + '">' +
      '<span class="fresh-dot ' + fr.band + '"></span> atualizado ' + esc(fr.txt) +
      (fr.stale ? " ⚠ (pode estar defasado)" : "") + "</span>" +
      ' · <span class="meta-hint">clique no jogo pra expandir</span>';

    var capEl = document.getElementById("capstatus");
    if (capEl) {
      var cap = B.capture;
      if (cap && ((cap.casas_ok || []).length || (cap.casas_fail || []).length || (cap.casas_stale || []).length)) {
        var okN = (cap.casas_ok || []).length, failN = (cap.casas_fail || []).length;
        var staleN = (cap.casas_stale || []).length;
        var parts = (cap.casas_ok || []).map(function (c) {
          return '<span class="cap-ok">' + LOGO(c, "house-logo-sm") + " ✓</span>";
        }).concat((cap.casas_fail || []).map(function (f) {
          return '<span class="cap-fail" title="' + esc((f.error_class ? f.error_class + ": " : "") + (f.error || "")) + '">' +
            LOGO(f.casa, "house-logo-sm") + " ✗</span>";
        })).concat((cap.casas_stale || []).map(function (c) {
          return '<span class="cap-stale" title="Full anterior reutilizado (stale-keep)">' + LOGO(c, "house-logo-sm") + " *</span>";
        }));
        var cls = failN === 0 && staleN === 0 ? "cap-green" : (okN >= 3 || staleN ? "cap-yellow" : "cap-red");
        if (fr.band === "old" || fr.stale) cls = "cap-red";
        else if (fr.band === "mid" && cls === "cap-green") cls = "cap-yellow";
        capEl.className = "capbar " + cls;
        var histTxt = "";
        if (cap.hist7) {
          var hs = Object.keys(cap.hist7).map(function (c) {
            var h = cap.hist7[c], pct = h.total ? Math.round(100 * h.ok / h.total) : 0;
            return c + " " + pct + "% (" + h.ok + "/" + h.total + ")";
          });
          histTxt = '<div class="cap-note">Últimos 7 dias: ' + hs.join(" · ") + "</div>";
        }
        capEl.innerHTML = '<span class="fresh-dot ' + fr.band + '"></span> Frescor mesa: <b>' + esc(fr.txt) + "</b> · Casas: " +
          parts.join(" · ") +
          (failN ? '<div class="cap-note">Captura incompleta — mercados podem existir nas casas ✗ e não aparecer aqui.</div>' : "") +
          (staleN ? '<div class="cap-note">* Stale-keep: odd da full anterior (até 12h) porque a captura atual falhou ou não atualizou.</div>' : "") +
          histTxt;
        capEl.style.display = "";
      } else {
        capEl.style.display = "none";
      }
    }

    var lista = document.getElementById("lista");
    lista.innerHTML = "";
    if (!vis.length) {
      lista.innerHTML = '<div class="empty"><div class="big">📭</div>Nenhum jogo com <b>' +
        esc(state.mercado === "todos" ? "mercados" : state.mercado) +
        (state.casa !== "todas" ? "</b> na <b>" + esc(state.casa) : "") +
        "</b> aberto agora.<br><span style=\"font-size:12px\">Troque os filtros nos chips acima ou volte após a próxima captura.</span></div>";
      return;
    }
    vis.forEach(function (j) { lista.appendChild(gameRow(j)); });
  }

  var sub = document.querySelector("#view-board .sub");
  if (sub) {
    sub.innerHTML = "Uma linha por jogo — <b>clique pra expandir</b> as linhas do jogo, do mandante e do visitante por casa. " +
      "Filtre por <b>mercado</b> e por <b>casa</b> (os filtros combinam e ficam salvos). " +
      "Onde há modelo: <b style=\"color:var(--green)\">valor (+EV)</b>.";
  }
  var disc = document.querySelector("#view-board .disc");
  if (disc) {
    disc.innerHTML = "Odds capturadas num instante — <b>podem ter movido</b>. Main line = menor gap Mais/Menos (só cluster de partida). " +
      "Linhas de time só quando a casa publica (ex.: Superbet Fin / Betano Cartões).";
  }

  window.setInterval(function () {
    var view = document.getElementById("view-board");
    if (!view || view.hidden) return;
    var sx = window.scrollX || 0;
    var sy = window.scrollY || 0;
    render();
    if (typeof window.scrollTo === "function") window.scrollTo(sx, sy);
  }, 60000);

  // P0.2 — badge honesto do modelo (candidate vs produção). Regra única, sem mentir:
  // source candidate_pricer → CANDIDATE (mesmo com status "promoted" no board);
  // shadow → SHADOW; value_pricers → PRODUÇÃO; qualquer outra coisa → MODELO ? (não "produção").
  window.rduModelBadge = function (model) {
    model = model || (window.BOARD && window.BOARD.model) || {};
    var src = String(model.source || "").toLowerCase();
    var st = String(model.status || "").toLowerCase();
    if (st.indexOf("shadow") === 0 || src.indexOf("shadow") >= 0)
      return { label: "SHADOW", cls: "mdl-shadow", title: "Modelo em sombra — não usar para decisão." };
    if (src.indexOf("candidate") >= 0)
      return { label: "CANDIDATE", cls: "mdl-cand", title: "Modelos candidatos (candidate_pricer): promovidos ao board, mas ainda em validação — não é produção." };
    if (src.indexOf("value_pricer") >= 0)
      return { label: "PRODUÇÃO", cls: "mdl-prod", title: "Modelos de produção (value_pricers)." };
    return { label: "MODELO ?", cls: "mdl-unk", title: "Origem do modelo não identificada — trate como não-produção." };
  };
  (function () {
    var el = document.getElementById("model-badge");
    if (!el) return;
    var mb = window.rduModelBadge(B.model);
    el.className = "mdl-badge " + mb.cls;
    el.textContent = mb.label;
    el.title = mb.title;
  })();

  render();
})();
