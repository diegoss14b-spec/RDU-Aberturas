// Histórico & CLV — métricas + explorador jogo → mercado → main line (gráfico até o fechamento)
(function () {
  var H = window.HIST || { gerado: "?", limiares: { head: 30, bucket: 20, roi: 50 },
    banco: { monitoradas: 0, liquidadas: 0, clv_validas: 0, moveu_pct: 0 },
    head: { n_valid: 0, n_settled: 0, green_geral: null }, recortes: { mercado: [], casa: [], lado: [] },
    liquidadas: [], abertas: [] };
  var LIM = H.limiares || { head: 30, bucket: 20, roi: 50 };
  var ABBR = { "Cartões": "CAR", "Faltas": "FAL", "Finalizações": "FIN", "Impedimentos": "IMP",
    "Laterais": "LAT", "Tiros de meta": "TM", "Escanteios": "ESC", "Chutes no gol": "CG", "Desarmes": "DES" };
  var MV = window.MOVES || {};
  var CASA_COR = { betano: "#6d28d9", superbet: "#2f6f57", estrelabet: "#c2410c", "7k": "#1d4ed8", pinnacle: "#0f766e" };
  var LADO_EN = { "Mais": "over", "Menos": "under", over: "over", under: "under" };

  // explorar | liquidadas | abertas
  var state = { aba: "explorar", merc: "todos", res: "todos",
    game: null, mercado: null, linha: null, casa: null };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function br(x, d) { if (x == null || x !== x) return "—"; var n = Number(x); return (d != null ? n.toFixed(d) : String(n)).replace(".", ","); }
  function pct(x, d) { return x == null ? "—" : br(x, d == null ? 1 : d) + "%"; }
  function sign(x, d) { if (x == null) return "—"; return (x > 0 ? "+" : "") + br(x, d == null ? 1 : d) + "%"; }
  function cls(x) { return x == null ? "" : (x > 0 ? "pos" : (x < 0 ? "neg" : "")); }
  function pm(ci) { if (!ci || ci[0] == null) return ""; return ' <span class="pm">±' + Math.round((ci[1] - ci[0]) / 2) + '</span>'; }

  function chip(label, active, extra, onclick) {
    var c = document.createElement("span");
    c.className = "chip" + (extra ? " " + extra : "") + (active ? " on" : "");
    c.innerHTML = label; c.onclick = onclick;
    c.setAttribute("role", "button"); c.tabIndex = 0;
    c.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onclick(); } };
    return c;
  }

  function gameId(r) {
    // prefer gid estável (sofa:id ou day|hn|an) do build_history
    if (r.gid) return r.gid;
    if (r.sofa_id) return "sofa:" + r.sofa_id;
    // fallback: gk = gid|mercado|linha|lado  (sofa: 4 partes; legado: 6)
    var p = (r.gk || "").split("|");
    if (p.length >= 1 && String(p[0]).indexOf("sofa:") === 0) return p[0];
    if (p.length >= 3) return p[0] + "|" + p[1] + "|" + p[2];
    return (r.data || "") + "|" + (r.jogo || "");
  }

  function mvSeries(gk, casa) {
    var g = MV[gk];
    return (g && g[casa] && g[casa].length >= 2) ? g[casa] : null;
  }

  function anyMv(gk) {
    var g = MV[gk];
    if (!g) return false;
    return Object.keys(g).some(function (c) { return c !== "_ko" && g[c] && g[c].length >= 2; });
  }

  // --- índices jogo → mercados → linhas ---
  function buildGameIndex() {
    var idx = {};
    function add(r, settled) {
      var gid = gameId(r);
      if (!gid || gid === "|") return;
      var g = idx[gid] || (idx[gid] = {
        id: gid, jogo: r.jogo, data: r.data, kickoff: r.kickoff,
        settled: false, mercados: {}, casas: {}
      });
      if (settled) g.settled = true;
      if (r.casa) g.casas[r.casa] = 1;
      var m = g.mercados[r.mercado] || (g.mercados[r.mercado] = { linhas: {} });
      var L = String(r.linha);
      var ln = m.linhas[L] || (m.linhas[L] = { linha: +r.linha, lados: {}, result: null });
      if (r.result != null) ln.result = r.result;
      var lado = r.lado;
      var slot = ln.lados[lado] || (ln.lados[lado] = { rows: [], gk: r.gk });
      slot.rows.push(r);
      if (r.gk) slot.gk = r.gk;
    }
    (H.liquidadas || []).forEach(function (r) { add(r, true); });
    (H.abertas || []).forEach(function (r) { add(r, false); });
    return idx;
  }

  /**
   * Filtra linhas de JOGO (descarta totais de time misturados no mesmo balde).
   * Ex.: Finalizações 0.5–15.5 (time) + 20.5–25.5 (partida) → só o cluster de partida.
   */
  function matchLineSet(linhasObj, mercado) {
    var arr = Object.keys(linhasObj).map(Number).sort(function (a, b) { return a - b; });
    if (arr.length < 2) return arr;
    var maxL = arr[arr.length - 1], minL = arr[0];
    // Finalizações de partida tipicamente ≥16.5; time sozinho fica 8–15
    // (se max≥16.5 já há cluster de jogo — não exigir 18, senão 15.5 “esconde” o filtro)
    if (mercado === "Finalizações" && maxL >= 16.5) {
      return arr.filter(function (L) { return L >= 16.5; });
    }
    if (mercado === "Faltas" && maxL >= 18) {
      return arr.filter(function (L) { return L >= 16.5; });
    }
    if (mercado === "Escanteios" && maxL >= 9 && maxL - minL >= 4) {
      return arr.filter(function (L) { return L >= 6.5; });
    }
    if (mercado === "Chutes no gol" && maxL >= 8 && maxL - minL >= 4) {
      return arr.filter(function (L) { return L >= 5.5; });
    }
    // gap grande no meio: fica o cluster de cima se for o de jogo
    if (maxL - minL >= 8) {
      var bestGap = 0, cut = -1;
      for (var i = 1; i < arr.length; i++) {
        var g = arr[i] - arr[i - 1];
        if (g > bestGap) { bestGap = g; cut = i; }
      }
      if (bestGap >= 4 && cut > 0) {
        var high = arr.slice(cut);
        if (high[high.length - 1] >= 12) return high;
      }
    }
    return arr;
  }

  function pickMainLine(linhasObj, mercado) {
    // main = menor |over−under| só entre linhas de PARTIDA (não de time)
    var cands = matchLineSet(linhasObj, mercado || "");
    if (!cands.length) {
      cands = Object.keys(linhasObj).map(Number).sort(function (a, b) { return a - b; });
    }
    var best = null, score = Infinity;
    cands.forEach(function (L) {
      var ln = linhasObj[String(L)] || linhasObj[L];
      if (!ln) return;
      var overs = (ln.lados["Mais"] || ln.lados["over"] || {}).rows || [];
      var unders = (ln.lados["Menos"] || ln.lados["under"] || {}).rows || [];
      if (!overs.length || !unders.length) return;
      var byCasa = {};
      overs.forEach(function (r) { byCasa[r.casa] = byCasa[r.casa] || {}; byCasa[r.casa].o = r.open || r.last; });
      unders.forEach(function (r) { byCasa[r.casa] = byCasa[r.casa] || {}; byCasa[r.casa].u = r.open || r.last; });
      var gaps = [];
      Object.keys(byCasa).forEach(function (c) {
        var o = byCasa[c].o, u = byCasa[c].u;
        if (o > 1 && u > 1) gaps.push(Math.abs(o - u));
      });
      if (!gaps.length) return;
      var gap = gaps.reduce(function (a, b) { return a + b; }, 0) / gaps.length;
      var near = Math.abs(((overs[0].open || overs[0].last || 2) + (unders[0].open || unders[0].last || 2)) / 2 - 1.9);
      var sc = gap * 10 + near;
      if (sc < score) { score = sc; best = +L; }
    });
    if (best != null) return best;
    return cands.length ? cands[Math.floor(cands.length / 2)] : null;
  }

  function lineHit(linha, result) {
    if (result == null || linha == null) return null;
    // over wins if result > linha; under if result < linha; push if equal (inteiro)
    if (result > linha) return "Mais";
    if (result < linha) return "Menos";
    return "Push";
  }

  /** Casas com série (Mais e/ou Menos) pra gid|mercado|linha */
  function casasComSerie(gid, mercado, linha) {
    var base = gid + "|" + mercado + "|" + linha + "|";
    var gO = MV[base + "over"] || {};
    var gU = MV[base + "under"] || {};
    var set = {};
    Object.keys(gO).forEach(function (c) {
      if (c !== "_ko" && gO[c] && gO[c].length >= 1) set[c] = 1;
    });
    Object.keys(gU).forEach(function (c) {
      if (c !== "_ko" && gU[c] && gU[c].length >= 1) set[c] = 1;
    });
    return Object.keys(set);
  }

  /**
   * Gráfico Odds vs Tempo — UMA casa (estilo referência).
   * Pré-jogo: eixo X = tempo até o kickoff (abertura → fechamento).
   * Séries: Mais (azul) e Menos (vermelho).
   */
  function houseChart(gid, mercado, linha, casa) {
    var base = gid + "|" + mercado + "|" + linha + "|";
    var gO = MV[base + "over"] || {};
    var gU = MV[base + "under"] || {};
    var sO = (gO[casa] || []).slice().sort(function (a, b) { return a[0] - b[0]; });
    var sU = (gU[casa] || []).slice().sort(function (a, b) { return a[0] - b[0]; });
    if (sO.length < 2 && sU.length < 2) {
      return '<div class="ex-empty">Sem série suficiente em <b>' + esc(casa) +
        "</b> pra essa linha.<br><span style=\"font-size:12px\">Precisa de ≥2 capturas (abertura → fechamento). " +
        "Com o cron de 1h o banco enche rápido.</span></div>";
    }

    var all = sO.concat(sU);
    var ts = all.map(function (x) { return x[0]; });
    var os = all.map(function (x) { return x[1]; });
    var t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
    var ko = gO._ko || gU._ko || null;
    // estende eixo até o kickoff (fechamento natural)
    if (ko && ko > t1) t1 = ko;
    // se só temos 2 pontos iguais, ainda desenha
    var o0 = Math.min.apply(null, os), o1 = Math.max.apply(null, os);
    if (o1 - o0 < 0.05) { o0 -= 0.15; o1 += 0.15; }
    else { var pad = (o1 - o0) * 0.15 + 0.04; o0 -= pad; o1 += pad; }

    var W = 680, H = 280, L = 52, R = 56, T = 28, B = 36;
    var dt = (t1 - t0) || 1, dO = (o1 - o0) || 1;
    function X(t) { return L + (t - t0) / dt * (W - L - R); }
    function Y(o) { return T + (1 - (o - o0) / dO) * (H - T - B); }
    function fmtT(m) {
      var d = new Date(m * 60000);
      return ("0" + d.getDate()).slice(-2) + "/" + ("0" + (d.getMonth() + 1)).slice(-2) + " " +
        ("0" + d.getHours()).slice(-2) + "h" + ("0" + d.getMinutes()).slice(-2);
    }
    function fmtDelta(m) {
      // minutos até o kickoff (negativo = antes)
      if (!ko) return fmtT(m);
      var mins = Math.round(m - ko);
      if (mins === 0) return "KO";
      if (mins < 0) {
        var h = Math.floor((-mins) / 60), mm = (-mins) % 60;
        return h > 0 ? ("−" + h + "h" + (mm ? mm : "")) : ("−" + mm + "m");
      }
      return "+" + mins + "m";
    }

    var COR_MAIS = "#2563eb";   // azul
    var COR_MENOS = "#dc2626";  // vermelho
    var sv = '<svg class="ex-svg" viewBox="0 0 ' + W + " " + H + '" width="100%" preserveAspectRatio="xMidYMid meet">';
    // fundo
    sv += '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#fafaf9"/>';
    // grid horizontal
    for (var i = 0; i <= 4; i++) {
      var ov = o0 + dO * i / 4, y = Y(ov);
      sv += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) +
        '" stroke="#e5e5e5" stroke-width="1"/>';
      sv += '<text x="' + (L - 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="11" fill="#737373" font-family="ui-monospace,monospace">' +
        ov.toFixed(2) + "</text>";
    }
    // grid vertical (ticks de tempo)
    var nTicks = 5;
    for (var j = 0; j <= nTicks; j++) {
      var tv = t0 + (t1 - t0) * j / nTicks;
      var x = X(tv);
      sv += '<line x1="' + x.toFixed(1) + '" y1="' + T + '" x2="' + x.toFixed(1) + '" y2="' + (H - B) +
        '" stroke="#f0f0f0" stroke-width="1"/>';
      sv += '<text x="' + x.toFixed(1) + '" y="' + (H - 12) +
        '" text-anchor="middle" font-size="10" fill="#737373">' + fmtDelta(tv) + "</text>";
    }
    // labels eixos
    sv += '<text x="14" y="' + (H / 2) + '" text-anchor="middle" font-size="10" fill="#525252" ' +
      'transform="rotate(-90 14 ' + (H / 2) + ')">ODD</text>';
    sv += '<text x="' + ((L + W - R) / 2) + '" y="' + (H - 2) +
      '" text-anchor="middle" font-size="10" fill="#525252">TEMPO (até o kickoff)</text>';

    // kickoff
    if (ko && ko >= t0 && ko <= t1) {
      sv += '<line x1="' + X(ko).toFixed(1) + '" y1="' + T + '" x2="' + X(ko).toFixed(1) +
        '" y2="' + (H - B) + '" stroke="#a3a3a3" stroke-width="1.5" stroke-dasharray="5,4"/>';
      sv += '<text x="' + X(ko).toFixed(1) + '" y="' + (T + 12) +
        '" text-anchor="middle" font-size="10" fill="#525252" font-weight="700">Fechamento / KO</text>';
    }

    var lnLab = br(linha, 1); // "20,5"
    var labMais = "Mais de " + lnLab;
    var labMenos = "Menos de " + lnLab;

    function drawSeries(s, cor, label) {
      if (!s || s.length < 1) return;
      var pts = s.map(function (p) { return X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" ");
      sv += '<polyline points="' + pts + '" fill="none" stroke="' + cor + '" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>';
      s.forEach(function (p, idx) {
        var r = (idx === 0 || idx === s.length - 1) ? 4 : 2.6;
        sv += '<circle cx="' + X(p[0]).toFixed(1) + '" cy="' + Y(p[1]).toFixed(1) +
          '" r="' + r + '" fill="' + cor + '" stroke="#fff" stroke-width="1"/>';
      });
      // label no último ponto: "Mais de 20,5 @ 1.80"
      var last = s[s.length - 1];
      var tag = label + " · " + Number(last[1]).toFixed(2).replace(".", ",");
      var tw = Math.max(96, tag.length * 6.2);
      sv += '<rect x="' + (X(last[0]) + 6).toFixed(1) + '" y="' + (Y(last[1]) - 18).toFixed(1) +
        '" width="' + tw + '" height="16" rx="4" fill="#fff" stroke="' + cor + '" stroke-width="1"/>';
      sv += '<text x="' + (X(last[0]) + 6 + tw / 2).toFixed(1) + '" y="' + (Y(last[1]) - 6.5).toFixed(1) +
        '" text-anchor="middle" font-size="10" fill="' + cor + '" font-weight="700">' + tag + "</text>";
      // label abertura
      if (s.length >= 1) {
        var first = s[0];
        sv += '<text x="' + X(first[0]).toFixed(1) + '" y="' + (Y(first[1]) - 8).toFixed(1) +
          '" text-anchor="middle" font-size="9" fill="' + cor + '">' +
          Number(first[1]).toFixed(2).replace(".", ",") + "</text>";
      }
    }
    drawSeries(sO, COR_MAIS, labMais);
    drawSeries(sU, COR_MENOS, labMenos);
    sv += "</svg>";

    // resumo numérico abertura → fechamento
    function ends(s) {
      if (!s || !s.length) return { o: null, c: null, n: 0 };
      return { o: s[0][1], c: s[s.length - 1][1], n: s.length };
    }
    var eO = ends(sO), eU = ends(sU);
    function drift(a, b) {
      if (a == null || b == null || !a) return "—";
      var d = (b / a - 1) * 100;
      return (d > 0 ? "+" : "") + d.toFixed(1).replace(".", ",") + "%";
    }
    var sum =
      '<div class="ex-sum">' +
      '<div class="ex-sum-item"><span class="dot" style="background:' + COR_MAIS + '"></span><b>' + labMais + "</b> " +
      br(eO.o, 2) + " → " + br(eO.c, 2) + ' <span class="ex-drift">' + drift(eO.o, eO.c) + "</span>" +
      ' <span class="pm">(' + eO.n + " pts)</span></div>" +
      '<div class="ex-sum-item"><span class="dot" style="background:' + COR_MENOS + '"></span><b>' + labMenos + "</b> " +
      br(eU.o, 2) + " → " + br(eU.c, 2) + ' <span class="ex-drift">' + drift(eU.o, eU.c) + "</span>" +
      ' <span class="pm">(' + eU.n + " pts)</span></div>" +
      "</div>";

    var leg =
      '<div class="ex-chart-head">' +
      '<div class="ex-chart-title-row">Gráfico Odds vs Tempo · <b>' + esc(casa) + "</b> · " +
      esc(mercado) + " " + lnLab + "</div>" +
      '<div class="mv-legend">' +
      '<span><span class="sw" style="background:' + COR_MAIS + ';height:3px"></span>' + labMais + "</span>" +
      '<span><span class="sw" style="background:' + COR_MENOS + ';height:3px"></span>' + labMenos + "</span>" +
      '<span class="ex-leg-note">abertura → fechamento (pré-jogo)</span></div></div>';

    return leg + sum + '<div class="mv-chart ex-chart ex-chart-ref">' + sv + "</div>";
  }

  function sparkline(gk, casa) {
    var s = mvSeries(gk, casa);
    if (!s) return '<span class="pm">—</span>';
    var w = 84, h = 22, p = 2;
    var ts = s.map(function (x) { return x[0]; }), os = s.map(function (x) { return x[1]; });
    var t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
    var o0 = Math.min.apply(null, os), o1 = Math.max.apply(null, os);
    var dt = (t1 - t0) || 1, dO = (o1 - o0) || 1;
    var pts = s.map(function (x) {
      return (p + (x[0] - t0) / dt * (w - 2 * p)).toFixed(1) + "," + (h - p - (x[1] - o0) / dO * (h - 2 * p)).toFixed(1);
    }).join(" ");
    var dir = os[os.length - 1] > os[0] ? "dn" : (os[os.length - 1] < os[0] ? "up" : "");
    return '<svg class="spark ' + dir + '" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '"><polyline points="' + pts + '"/></svg>';
  }

  // --- banner / headline (CLV) ---
  function banner() {
    var b = H.banco || {}, nv = (H.head || {}).n_valid || 0;
    var el = document.createElement("div");
    el.className = "capbar " + (nv >= LIM.head ? "cap-green" : (nv > 0 ? "cap-yellow" : "cap-red"));
    el.innerHTML = "Banco de odds: <b>" + (b.monitoradas || 0) + "</b> linhas · <b>" +
      (b.liquidadas || 0) + "</b> liquidadas · <b>" + (b.clv_validas || 0) + "</b> CLV pré-jogo · " +
      br(b.moveu_pct, 1) + "% moveram" +
      '<div class="cap-note">Explore um <b>jogo → mercado → main line</b> pra ver a curva até o fechamento e se a linha bateu.</div>';
    return el;
  }

  function statTile(k, val, valCls, sub) {
    return '<div class="stat"><div class="k">' + esc(k) + '</div><div class="v ' + (valCls || "") + '">' + val + '</div>' +
      (sub ? '<div class="s">' + esc(sub) + '</div>' : "") + '</div>';
  }

  function headline() {
    var h = H.head || {}, wrap = document.createElement("div");
    if ((h.n_valid || 0) < LIM.head) {
      var e = document.createElement("div"); e.className = "empty"; e.style.padding = "20px";
      e.innerHTML = '<div class="big" style="font-size:28px">⏳</div><b>CLV agregado ainda sem amostra pré-jogo suficiente</b><br>' +
        '<span style="font-size:12px">≥' + LIM.head + ' linhas com abertura antes do apito (temos ' + (h.n_valid || 0) + '). ' +
        'O explorador de jogos funciona mesmo assim.</span>';
      wrap.appendChild(e);
      return wrap;
    }
    var tiles = "";
    tiles += statTile("Bateu o fechamento", pct(h.beat_close_rate, 0),
      h.beat_close_rate == null ? "wait" : (h.beat_close_rate >= 50 ? "pos" : "neg"), "N=" + (h.n_valid || 0));
    tiles += statTile("CLV médio", sign(h.clv_medio, 1), cls(h.clv_medio), "mediana " + sign(h.clv_mediana, 1));
    tiles += statTile("Placar (green)", pct(h.green_geral, 1), "", "N=" + (h.n_settled || 0));
    if (h.roi_abertura != null) {
      tiles += statTile("ROI abertura", sign(h.roi_abertura, 1), cls(h.roi_abertura),
        "vs fecha " + sign(h.roi_fechamento, 1));
    }
    var row = document.createElement("div"); row.className = "stat-row"; row.innerHTML = tiles;
    wrap.appendChild(row);
    return wrap;
  }

  // --- EXPLORAR ---
  function renderExplorar(root) {
    var idx = buildGameIndex();
    var games = Object.keys(idx).map(function (k) { return idx[k]; });
    games.sort(function (a, b) {
      return (b.kickoff || b.data || "").localeCompare(a.kickoff || a.data || "");
    });

    // se game selecionado sumiu, limpa
    if (state.game && !idx[state.game]) {
      state.game = state.mercado = state.linha = null;
    }

    if (!state.game) {
      // lista de jogos
      var filt = document.createElement("div"); filt.className = "bar";
      filt.appendChild(chip("Todos", state.merc === "todos", "", function () { state.merc = "todos"; render(); }));
      var mkts = {};
      games.forEach(function (g) { Object.keys(g.mercados).forEach(function (m) { mkts[m] = 1; }); });
      Object.keys(ABBR).forEach(function (m) {
        if (!mkts[m]) return;
        filt.appendChild(chip(m, state.merc === m, "", function () { state.merc = m; render(); }));
      });
      root.appendChild(filt);

      var list = games.filter(function (g) {
        if (state.merc === "todos") return true;
        return !!g.mercados[state.merc];
      });
      var meta = document.createElement("div"); meta.className = "meta";
      meta.innerHTML = list.length + " jogo" + (list.length === 1 ? "" : "s") + " no histórico · toque pra abrir o gráfico";
      root.appendChild(meta);

      var box = document.createElement("div"); box.className = "ex-games";
      list.forEach(function (g) {
        var nMerc = Object.keys(g.mercados).length;
        var card = document.createElement("button");
        card.type = "button";
        card.className = "ex-game-card" + (g.settled ? " settled" : " open");
        card.innerHTML =
          '<div class="ex-g-top"><div class="ex-g-name">' + esc(g.jogo) + '</div>' +
          '<div class="ex-g-when">' + esc((g.kickoff || g.data || "").slice(0, 16).replace("T", " ")) + "</div></div>" +
          '<div class="ex-g-meta">' +
          (g.settled ? '<span class="ex-badge done">liquidado</span>' : '<span class="ex-badge live">aberto</span>') +
          '<span class="ex-g-m">' + nMerc + " mercado" + (nMerc === 1 ? "" : "s") + "</span>" +
          '<span class="ex-g-c">' + Object.keys(g.casas).join(" · ") + "</span></div>";
        card.onclick = function () {
          state.game = g.id;
          state.mercado = null;
          state.linha = null;
          state.casa = null;
          var ms = Object.keys(g.mercados);
          if (ms.indexOf("Cartões") >= 0) state.mercado = "Cartões";
          else if (ms.length) state.mercado = ms[0];
          if (state.mercado) {
            state.linha = pickMainLine(g.mercados[state.mercado].linhas, state.mercado);
          }
          render();
        };
        box.appendChild(card);
      });
      if (!list.length) {
        box.innerHTML = '<div class="empty"><div class="big">📭</div>Nenhum jogo no histórico ainda.</div>';
      }
      root.appendChild(box);
      return;
    }

    // detalhe do jogo
    var g = idx[state.game];
    var head = document.createElement("div");
    head.className = "ex-detail-head";
    var back = document.createElement("button");
    back.type = "button"; back.className = "ex-back";
    back.textContent = "← Jogos";
    back.onclick = function () { state.game = state.mercado = state.linha = state.casa = null; render(); };
    head.appendChild(back);
    var title = document.createElement("div");
    title.innerHTML = '<div class="ex-d-name">' + esc(g.jogo) + '</div>' +
      '<div class="ex-d-sub">' + esc(g.data || "") +
      (g.settled ? ' · liquidado' : ' · em aberto') +
      " · " + Object.keys(g.casas).join(", ") + "</div>";
    head.appendChild(title);
    root.appendChild(head);

    // chips de mercado
    var mbar = document.createElement("div"); mbar.className = "bar";
    Object.keys(g.mercados).forEach(function (m) {
      mbar.appendChild(chip(m, state.mercado === m, "", function () {
        state.mercado = m;
        state.linha = pickMainLine(g.mercados[m].linhas, m);
        state.casa = null;
        render();
      }));
    });
    root.appendChild(mbar);

    if (!state.mercado || !g.mercados[state.mercado]) {
      var em = document.createElement("div"); em.className = "empty"; em.textContent = "Escolha um mercado.";
      root.appendChild(em);
      return;
    }

    var mkt = g.mercados[state.mercado];
    var allLinhas = Object.keys(mkt.linhas).map(Number).sort(function (a, b) { return a - b; });
    var jogoLinhas = matchLineSet(mkt.linhas, state.mercado);
    var jogoSet = {};
    jogoLinhas.forEach(function (L) { jogoSet[L] = 1; });
    if (state.linha == null || !mkt.linhas[String(state.linha)]) {
      state.linha = pickMainLine(mkt.linhas, state.mercado);
    }
    // se a linha atual é lixo de time e existe cluster de jogo, salta pra main de jogo
    if (jogoLinhas.length && !jogoSet[+state.linha]) {
      state.linha = pickMainLine(mkt.linhas, state.mercado);
    }

    // chips de linha — prioriza linhas de PARTIDA; alts baixas (time) ficam no fim / sem main
    var lbar = document.createElement("div"); lbar.className = "bar";
    var mainL = pickMainLine(mkt.linhas, state.mercado);
    // mostra primeiro cluster de jogo, depois baixas se existirem
    var show = jogoLinhas.length ? jogoLinhas.concat(allLinhas.filter(function (L) { return !jogoSet[L]; })) : allLinhas;
    show.forEach(function (L) {
      var isJogo = !jogoLinhas.length || !!jogoSet[L];
      var lab = br(L, 1) + (L === mainL ? " · main" : "") + (!isJogo ? " · ?" : "");
      lbar.appendChild(chip(lab, +state.linha === +L, L === mainL ? "ord" : (!isJogo ? "sm-chip" : ""), function () {
        state.linha = L; state.casa = null; render();
      }));
    });
    if (jogoLinhas.length && jogoLinhas.length < allLinhas.length) {
      var note = document.createElement("div");
      note.className = "meta";
      note.innerHTML = "Main line usa só linhas de <b>partida</b> (≥16,5 em Finalizações quando há cluster alto). " +
        "Linhas com <b>?</b> costumam ser total de time misturado na captura antiga.";
      root.appendChild(lbar);
      root.appendChild(note);
    } else {
      root.appendChild(lbar);
    }

    var ln = mkt.linhas[String(state.linha)];
    if (!ln) return;

    // chips de CASA (gráfico separado por casa)
    var casasSerie = casasComSerie(g.id, state.mercado, state.linha);
    var overRows = (ln.lados["Mais"] || {}).rows || [];
    var underRows = (ln.lados["Menos"] || {}).rows || [];
    var casasAll = {};
    overRows.forEach(function (r) { casasAll[r.casa] = 1; });
    underRows.forEach(function (r) { casasAll[r.casa] = 1; });
    casasSerie.forEach(function (c) { casasAll[c] = 1; });
    var casaList = Object.keys(casasAll);
    if (!state.casa || casaList.indexOf(state.casa) < 0) {
      // prefere casa com mais pontos na série
      var bestC = null, bestN = -1;
      casaList.forEach(function (c) {
        var base = g.id + "|" + state.mercado + "|" + state.linha + "|";
        var n = ((MV[base + "over"] || {})[c] || []).length + ((MV[base + "under"] || {})[c] || []).length;
        if (n > bestN) { bestN = n; bestC = c; }
      });
      state.casa = bestC || casaList[0] || null;
    }

    var cbar = document.createElement("div"); cbar.className = "bar";
    var labCasa = document.createElement("span");
    labCasa.className = "ex-bar-lab"; labCasa.textContent = "Casa:";
    cbar.appendChild(labCasa);
    casaList.forEach(function (c) {
      cbar.appendChild(chip(c, state.casa === c, "", function () {
        state.casa = c; render();
      }));
    });
    root.appendChild(cbar);

    // painel resultado + open/close da casa
    var panel = document.createElement("div");
    panel.className = "ex-panel";
    var hit = lineHit(state.linha, ln.result);
    var resultHtml = "";
    if (ln.result != null) {
      resultHtml =
        '<div class="ex-result ' + (hit === "Mais" ? "over" : (hit === "Menos" ? "under" : "push")) + '">' +
        '<div class="ex-res-k">Resultado no jogo</div>' +
        '<div class="ex-res-v">' + br(ln.result, 0) + " " + esc(state.mercado.toLowerCase()) + "</div>" +
        '<div class="ex-res-hit">Linha ' + br(state.linha, 1) + " → <b>" +
        (hit === "Push" ? "Push (exata)" : (hit + " bateu")) + "</b></div></div>";
    } else {
      resultHtml = '<div class="ex-result wait"><div class="ex-res-k">Resultado</div><div class="ex-res-v">aguardando liquidação</div></div>';
    }

    var o = overRows.filter(function (r) { return r.casa === state.casa; })[0];
    var u = underRows.filter(function (r) { return r.casa === state.casa; })[0];
    var tbl = '<table class="lad ex-odds"><thead><tr><th>Lado</th><th>Abertura</th><th>Fechamento</th><th>Δ%</th><th>CLV</th></tr></thead><tbody>';
    function rowSide(lab, r, tdCls) {
      if (!r) return "<tr><td>" + lab + "</td><td colspan='4' class='pm'>—</td></tr>";
      var close = r.close != null ? r.close : r.last;
      var d = (r.open && close) ? ((close / r.open - 1) * 100) : null;
      return "<tr><td><b>" + lab + "</b></td>" +
        '<td class="' + tdCls + '">' + br(r.open, 2) + "</td>" +
        '<td class="' + tdCls + '">' + br(close, 2) + "</td>" +
        '<td class="' + cls(d) + '">' + sign(d, 1) + "</td>" +
        '<td class="' + (r.clv_valido === false ? "" : cls(r.clv)) + '">' +
        (r.clv_valido === false ? "—" : sign(r.clv, 1)) + "</td></tr>";
    }
    var lnTxt = br(state.linha, 1);
    tbl += rowSide("Mais de " + lnTxt, o, "o") + rowSide("Menos de " + lnTxt, u, "u") + "</tbody></table>";

    panel.innerHTML =
      '<div class="ex-panel-grid">' + resultHtml +
      '<div class="ex-odds-wrap"><div class="ex-panel-title">' + esc(state.casa || "—") +
      " · linha " + br(state.linha, 1) + " · " + esc(state.mercado) +
      "</div>" + tbl + "</div></div>" +
      (state.casa ? houseChart(g.id, state.mercado, state.linha, state.casa) :
        '<div class="ex-empty">Escolha uma casa.</div>');

    root.appendChild(panel);
  }

  // --- tabelas legadas ---
  function filtros(dataset) {
    if (state.merc !== "todos" && !dataset.some(function (r) { return r.mercado === state.merc; })) state.merc = "todos";
    var box = document.createElement("div"); box.className = "bar";
    if (Object.keys(dataset.reduce(function (a, r) { a[r.mercado] = 1; return a; }, {})).length > 1) {
      box.appendChild(chip("Todos mercados", state.merc === "todos", "", function () { state.merc = "todos"; render(); }));
      Object.keys(ABBR).forEach(function (m) {
        if (!dataset.some(function (r) { return r.mercado === m; })) return;
        box.appendChild(chip(m, state.merc === m, "", function () { state.merc = m; render(); }));
      });
    }
    if (state.aba === "liquidadas") {
      box.appendChild(chip("🟢 green", state.res === "green", "val", function () { state.res = state.res === "green" ? "todos" : "green"; render(); }));
      box.appendChild(chip("🔴 red", state.res === "red", "ord", function () { state.res = state.res === "red" ? "todos" : "red"; render(); }));
    }
    return box;
  }

  function applyFilters(rows) {
    return rows.filter(function (r) {
      if (state.merc !== "todos" && r.mercado !== state.merc) return false;
      if (state.aba === "liquidadas" && state.res !== "todos") {
        if (state.res === "green" && !r.won) return false;
        if (state.res === "red" && r.won) return false;
      }
      return true;
    });
  }

  function tblLiquidadas(rows) {
    if (!rows.length) return '<div class="empty"><div class="big">📭</div>Nenhuma linha liquidada com esses filtros.</div>';
    var t = '<div class="hist-scroll"><table class="lad hist-tbl"><thead><tr>' +
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Fecha</th><th>CLV</th><th>Res.</th><th>Mov.</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var invalid = !r.clv_valido;
      t += '<tr class="' + (invalid ? "sm" : "") + (anyMv(r.gk) ? " has-mv" : "") + '" data-gk="' + esc(r.gk || "") + '">' +
        '<td class="jg">' + esc(r.jogo) + "</td>" +
        "<td>" + (ABBR[r.mercado] || esc(r.mercado)) + "</td>" +
        '<td class="ln">' + br(r.linha, 1) + "</td><td>" + esc(r.lado) + "</td>" +
        '<td class="o">' + br(r.open, 2) + '</td><td class="u">' + br(r.close, 2) + "</td>" +
        '<td class="' + (invalid ? "" : cls(r.clv)) + '">' + (invalid ? "—" : sign(r.clv, 1)) + "</td>" +
        '<td class="' + (r.won ? "hist-mv up" : "hist-mv dn") + '">' + (r.won ? "green" : "red") + "</td>" +
        "<td>" + sparkline(r.gk, r.casa) + "</td></tr>";
    });
    return t + "</tbody></table></div>";
  }

  function tblAbertas(rows) {
    if (!rows.length) return '<div class="empty"><div class="big">🕓</div>Nenhuma linha aberta com movimento.</div>';
    var t = '<div class="hist-scroll"><table class="lad hist-tbl"><thead><tr>' +
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Agora</th><th>Δ%</th><th>Obs</th><th>Mov.</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var d = r.drift_pct;
      var mv = d == null ? "flat" : (d < 0 ? "up" : (d > 0 ? "dn" : "flat"));
      t += '<tr class="' + (anyMv(r.gk) ? "has-mv" : "") + '" data-gk="' + esc(r.gk || "") + '">' +
        '<td class="jg">' + esc(r.jogo) + "</td><td>" + (ABBR[r.mercado] || esc(r.mercado)) + "</td>" +
        '<td class="ln">' + br(r.linha, 1) + "</td><td>" + esc(r.lado) + "</td>" +
        '<td class="o">' + br(r.open, 2) + '</td><td class="u">' + br(r.last, 2) + "</td>" +
        '<td class="hist-mv ' + mv + '">' + sign(d, 1) + "</td><td>" + (r.n_moves || 0) + "</td>" +
        "<td>" + sparkline(r.gk, r.casa) + "</td></tr>";
    });
    return t + "</tbody></table></div>";
  }

  function render() {
    var root = document.getElementById("hist-root");
    if (!root) return;
    root.innerHTML = "";
    root.appendChild(banner());
    root.appendChild(headline());

    // nav principal
    var nav = document.createElement("div"); nav.className = "bar";
    nav.appendChild(chip("🔎 Explorar jogo", state.aba === "explorar", "", function () {
      state.aba = "explorar"; render();
    }));
    nav.appendChild(chip("Liquidadas <span class='ct2'>" + (H.liquidadas || []).length + "</span>",
      state.aba === "liquidadas", "", function () {
        state.aba = "liquidadas"; state.merc = "todos"; state.res = "todos"; render();
      }));
    var nAbertasMv = (H.abertas || []).filter(function (r) { return (r.n_moves || 0) >= 1; }).length;
    nav.appendChild(chip("Abertas <span class='ct2'>" + nAbertasMv + "</span>",
      state.aba === "abertas", "", function () {
        state.aba = "abertas"; state.merc = "todos"; state.res = "todos"; render();
      }));
    root.appendChild(nav);

    if (state.aba === "explorar") {
      renderExplorar(root);
      return;
    }

    var base = state.aba === "liquidadas" ? (H.liquidadas || [])
      : (H.abertas || []).filter(function (r) { return (r.n_moves || 0) >= 1; });
    root.appendChild(filtros(base));
    var vis = applyFilters(base);
    var meta = document.createElement("div"); meta.className = "meta";
    meta.innerHTML = vis.length + " linha" + (vis.length === 1 ? "" : "s") +
      " · atualizado " + esc(H.gerado || "?");
    root.appendChild(meta);
    var tbl = document.createElement("div");
    tbl.innerHTML = state.aba === "liquidadas" ? tblLiquidadas(vis) : tblAbertas(vis);
    root.appendChild(tbl);

    // clique em linha → abre explorador naquele jogo/mercado/linha
    tbl.querySelectorAll("tr.has-mv, tr[data-gk]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.onclick = function () {
        var gk = tr.getAttribute("data-gk") || "";
        var p = gk.split("|");
        if (p.length < 6) return;
        state.aba = "explorar";
        state.game = p[0] + "|" + p[1] + "|" + p[2];
        state.mercado = p[3];
        state.linha = parseFloat(p[4]);
        render();
      };
    });
  }

  render();
  window.__renderHist = render;
})();
