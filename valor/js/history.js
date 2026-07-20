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
  // cores distintas por casa NO GRÁFICO (a cor de marca real repete vermelho
  // em superbet/estrelabet — aqui legibilidade ganha da identidade)
  var CASA_COR = { betano: "#f97316", superbet: "#dc2626", estrelabet: "#6d28d9", "7k": "#059669", pinnacle: "#1d4ed8" };
  function casaCor(c) { return CASA_COR[String(c || "").toLowerCase()] || "#6b7280"; }
  var LOGO = window.casaLogo || function (c) { return esc(c); };
  var LADO_EN = { "Mais": "over", "Menos": "under", over: "over", under: "under" };

  // explorar | liquidadas | abertas
  var state = { aba: "explorar", merc: "todos", res: "todos", quality: "todos",
    game: null, mercado: null, linha: null, casa: null };

  var Q_LABEL = {
    full_prematch: "pré-jogo OK", late_open: "open tarde", no_close: "sem close",
    post_kickoff: "pós-apito", open: "aberta", unknown: "?"
  };
  function qBadge(q) {
    if (q && typeof q === "object") q = q.band || q.quality || "unknown";
    if (!q || q === "unknown") return "";
    var cls = q === "full_prematch" ? "q-ok" : (q === "late_open" ? "q-mid" : "q-bad");
    return '<span class="q-badge ' + cls + '" title="Qualidade da captura">' + esc(Q_LABEL[q] || q) + "</span>";
  }

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function br(x, d) { if (x == null || x !== x) return "—"; var n = Number(x); return (d != null ? n.toFixed(d) : String(n)).replace(".", ","); }
  function pct(x, d) { return x == null ? "—" : br(x, d == null ? 1 : d) + "%"; }
  function sign(x, d) { if (x == null) return "—"; return (x > 0 ? "+" : "") + br(x, d == null ? 1 : d) + "%"; }
  function cls(x) { return x == null ? "" : (x > 0 ? "pos" : (x < 0 ? "neg" : "")); }
  function pm(ci) { if (!ci || ci[0] == null) return ""; return ' <span class="pm">±' + Math.round((ci[1] - ci[0]) / 2) + '</span>'; }
  function ciText(ci) {
    return !ci || ci[0] == null ? "" : ("IC95 " + br(ci[0], 1) + "% a " + br(ci[1], 1) + "%");
  }
  function rowEpoch(r) {
    if (r && r.kickoff_epoch != null) return Number(r.kickoff_epoch) || 0;
    var ms = Date.parse((r && (r.kickoff || r.data)) || "");
    return isNaN(ms) ? 0 : Math.floor(ms / 1000);
  }
  function fmtBrt(value, withTime) {
    var d = typeof value === "number" ? new Date(value * 1000) : new Date(value || "");
    if (isNaN(d.getTime())) return String(value || "").slice(0, withTime ? 16 : 10).replace("T", " ");
    try {
      var opt = { timeZone: "America/Fortaleza", day: "2-digit", month: "2-digit", year: "numeric" };
      if (withTime) { opt.hour = "2-digit"; opt.minute = "2-digit"; opt.hour12 = false; }
      return new Intl.DateTimeFormat("pt-BR", opt).format(d).replace(",", "");
    } catch (e) { return d.toISOString().slice(0, withTime ? 16 : 10).replace("T", " "); }
  }


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
    return Object.keys(g).some(function (c) { return c.charAt(0) !== "_" && g[c] && g[c].length >= 2; });
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
      if (c.charAt(0) !== "_" && gO[c] && gO[c].length >= 1) set[c] = 1;
    });
    Object.keys(gU).forEach(function (c) {
      if (c.charAt(0) !== "_" && gU[c] && gU[c].length >= 1) set[c] = 1;
    });
    return Object.keys(set);
  }

  /**
   * TOTAL IMPLÍCITO (μ) — a "linha projetada pela casa" ao longo do tempo.
   *
   * Pra cada snapshot (linha L, odd_over, odd_under) de uma casa:
   *   p_over_fair = (1/over) / (1/over + 1/under)          [remove o juice]
   *   μ resolve P(X > L) = p_over_fair sob Poisson          [bisseção em μ]
   *   com P(X > L) = 1 − CDF(floor(L))  (L meio-inteiro).
   * Assim "over 24.5 @ 1.90/1.90" vira μ≈24.7 — e uma linha nova (24.5→26.5)
   * não quebra a série: o μ continua comparável. Odd do over SUBINDO = μ CAINDO.
   * Em cada minuto usamos a linha mais equilibrada (menor |over−under|) da casa.
   */
  function poissonCdf(k, mu) {
    if (k < 0) return 0;
    var t = Math.exp(-mu), s = t;
    for (var i = 1; i <= k; i++) { t *= mu / i; s += t; }
    return Math.min(1, s);
  }
  function pOverFromMu(L, mu) { return 1 - poissonCdf(Math.floor(L), mu); }
  function solveMu(L, pOver) {
    if (!(pOver > 0 && pOver < 1) || L == null || L < 0) return null;
    var lo = 1e-6, hi = Math.max(2 * L + 10, 20), i;
    for (i = 0; i < 40 && pOverFromMu(L, hi) < pOver; i++) hi *= 1.5;
    for (i = 0; i < 80; i++) {
      var mid = (lo + hi) / 2;
      if (pOverFromMu(L, mid) < pOver) lo = mid; else hi = mid;
      if (hi - lo < 5e-4) break;
    }
    return (lo + hi) / 2;
  }

  /** Junta as séries over/under de TODAS as linhas do jogo+mercado.
      → { casas: {casa: [{t, mu, L, o, u}...]}, ko } */
  function impliedSeries(gid, mercado) {
    var prefix = gid + "|" + mercado + "|";
    var perCasa = {};   // casa -> minuto -> L -> {o, u}
    var ko = null;
    var linhasVistas = {};
    Object.keys(MV).forEach(function (gk) {
      if (gk.indexOf(prefix) !== 0) return;
      var rest = gk.slice(prefix.length).split("|");
      if (rest.length !== 2) return;
      var L = parseFloat(rest[0]), lado = rest[1];
      if (isNaN(L) || (lado !== "over" && lado !== "under")) return;
      linhasVistas[L] = 1;
      var g = MV[gk];
      if (g._ko && !ko) ko = g._ko;
      Object.keys(g).forEach(function (casa) {
        if (casa.charAt(0) === "_") return;
        (g[casa] || []).forEach(function (p) {
          var slot = ((perCasa[casa] = perCasa[casa] || {})[p[0]] =
            perCasa[casa][p[0]] || {});
          var cell = (slot[L] = slot[L] || {});
          cell[lado === "over" ? "o" : "u"] = p[1];
        });
      });
    });
    // só linhas de PARTIDA (descarta totais de time misturados na captura antiga)
    var okSet = {};
    matchLineSet(linhasVistas, mercado).forEach(function (L) { okSet[L] = 1; });
    var out = {};
    Object.keys(perCasa).forEach(function (casa) {
      var serie = [];
      Object.keys(perCasa[casa]).map(Number).sort(function (a, b) { return a - b; })
        .forEach(function (t) {
          var porLinha = perCasa[casa][t];
          var bestL = null, bestGap = Infinity;
          Object.keys(porLinha).forEach(function (Lk) {
            var c = porLinha[Lk];
            if (!(c.o > 1 && c.u > 1) || !okSet[+Lk]) return;
            var gap = Math.abs(c.o - c.u);
            if (gap < bestGap) { bestGap = gap; bestL = +Lk; }
          });
          if (bestL == null) return;
          var c = porLinha[bestL];
          var pFair = (1 / c.o) / (1 / c.o + 1 / c.u);
          var mu = solveMu(bestL, pFair);
          if (mu == null) return;
          serie.push({ t: t, mu: mu, L: bestL, o: c.o, u: c.u });
        });
      if (serie.length) out[casa] = serie;
    });
    return { casas: out, ko: ko };
  }

  /** Gráfico do total implícito: UMA linha por casa (ou uma casa só). */
  function impliedChart(gid, mercado, casaSel) {
    var data = impliedSeries(gid, mercado);
    var casas = Object.keys(data.casas).sort();
    if (casaSel && casaSel !== "__all__") {
      casas = casas.filter(function (c) { return c === casaSel; });
    }
    var series = casas.map(function (c) { return { casa: c, pts: data.casas[c] }; })
      .filter(function (s) { return s.pts.length >= 1; });
    if (!series.length || !series.some(function (s) { return s.pts.length >= 2; })) {
      return '<div class="ex-empty">Sem série suficiente pra desenhar o total implícito.' +
        "<br><span style=\"font-size:12px\">Precisa de ≥2 capturas com o par Mais/Menos da mesma casa. " +
        "Com o cron de 1h o banco enche rápido.</span></div>";
    }

    var allT = [], allMu = [];
    series.forEach(function (s) {
      s.pts.forEach(function (p) { allT.push(p.t); allMu.push(p.mu); });
    });
    var t0 = Math.min.apply(null, allT), t1 = Math.max.apply(null, allT);
    var ko = data.ko;
    if (ko && ko > t1) t1 = ko;
    var m0 = Math.min.apply(null, allMu), m1 = Math.max.apply(null, allMu);
    if (m1 - m0 < 0.6) { m0 -= 0.4; m1 += 0.4; }
    else { var padY = (m1 - m0) * 0.15 + 0.1; m0 -= padY; m1 += padY; }

    var W = 720, H = 300, Lp = 52, R = 24, T = 30, Bp = 42;
    var dt = (t1 - t0) || 1, dM = (m1 - m0) || 1;
    function X(t) { return Lp + (t - t0) / dt * (W - Lp - R); }
    function Y(m) { return T + (1 - (m - m0) / dM) * (H - T - Bp); }
    function fmtDelta(m) {
      if (!ko) return fmtBrt(m * 60, true);
      var mins = Math.round(m - ko);
      if (mins === 0) return "KO";
      if (mins < 0) {
        var h = Math.floor((-mins) / 60), mm = (-mins) % 60;
        return h > 0 ? ("−" + h + "h" + (mm ? mm : "")) : ("−" + mm + "m");
      }
      return "+" + mins + "m";
    }

    var sv = '<svg class="ex-svg" viewBox="0 0 ' + W + " " + H + '" width="100%" preserveAspectRatio="xMidYMid meet">';
    sv += '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#fafafa"/>';
    for (var i = 0; i <= 4; i++) {
      var mv = m0 + dM * i / 4, y = Y(mv);
      sv += '<line x1="' + Lp + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) +
        '" stroke="#e5e7eb" stroke-width="1"/>';
      sv += '<text x="' + (Lp - 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="11" fill="#6b7280" font-family="ui-monospace,monospace">' +
        mv.toFixed(1) + "</text>";
    }
    var nTicks = 5;
    for (var j = 0; j <= nTicks; j++) {
      var tv = t0 + (t1 - t0) * j / nTicks;
      var x = X(tv);
      sv += '<line x1="' + x.toFixed(1) + '" y1="' + T + '" x2="' + x.toFixed(1) + '" y2="' + (H - Bp) +
        '" stroke="#f3f4f6" stroke-width="1"/>';
      sv += '<text x="' + x.toFixed(1) + '" y="' + (H - 12) +
        '" text-anchor="middle" font-size="10" fill="#6b7280">' + fmtDelta(tv) + "</text>";
    }
    sv += '<text x="14" y="' + (H / 2) + '" text-anchor="middle" font-size="10" fill="#4b5563" ' +
      'transform="rotate(-90 14 ' + (H / 2) + ')">TOTAL IMPLÍCITO (μ)</text>';
    sv += '<text x="' + ((Lp + W - R) / 2) + '" y="' + (H - 2) +
      '" text-anchor="middle" font-size="10" fill="#4b5563">TEMPO (até o kickoff)</text>';
    if (ko && ko >= t0 && ko <= t1) {
      sv += '<line x1="' + X(ko).toFixed(1) + '" y1="' + T + '" x2="' + X(ko).toFixed(1) +
        '" y2="' + (H - Bp) + '" stroke="#9ca3af" stroke-width="1.5" stroke-dasharray="5,4"/>';
      sv += '<text x="' + X(ko).toFixed(1) + '" y="' + (T - 6) +
        '" text-anchor="middle" font-size="10" fill="#4b5563" font-weight="700">Kickoff</text>';
    }

    series.forEach(function (s) {
      var cor = casaCor(s.casa);
      var pts = s.pts.map(function (p) { return X(p.t).toFixed(1) + "," + Y(p.mu).toFixed(1); }).join(" ");
      if (s.pts.length >= 2) {
        sv += '<polyline points="' + pts + '" fill="none" stroke="' + cor +
          '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" opacity=".9"/>';
      }
      s.pts.forEach(function (p, idx) {
        var lineChanged = idx > 0 && p.L !== s.pts[idx - 1].L;
        var tip = (window.casaNome ? window.casaNome(s.casa) : s.casa) + " · " + fmtBrt(p.t * 60, true) +
          " · linha " + br(p.L, 1) + " · " + br(p.o, 2) + "/" + br(p.u, 2) +
          " · μ " + br(p.mu, 1) +
          (lineChanged ? " · LINHA MUDOU " + br(s.pts[idx - 1].L, 1) + " → " + br(p.L, 1) : "");
        if (lineChanged) {
          // marcador de mudança de linha oferecida: losango maior
          var cx = X(p.t), cy = Y(p.mu), r = 5.5;
          sv += '<path d="M' + cx.toFixed(1) + " " + (cy - r).toFixed(1) +
            "L" + (cx + r).toFixed(1) + " " + cy.toFixed(1) +
            "L" + cx.toFixed(1) + " " + (cy + r).toFixed(1) +
            "L" + (cx - r).toFixed(1) + " " + cy.toFixed(1) + 'Z" fill="#fff" stroke="' + cor +
            '" stroke-width="2"><title>' + esc(tip) + "</title></path>";
        } else {
          var rr = (idx === 0 || idx === s.pts.length - 1) ? 4 : 2.8;
          sv += '<circle cx="' + X(p.t).toFixed(1) + '" cy="' + Y(p.mu).toFixed(1) +
            '" r="' + rr + '" fill="' + cor + '" stroke="#fff" stroke-width="1"><title>' +
            esc(tip) + "</title></circle>";
        }
      });
      // rótulo do último ponto: "μ 24,7"
      var last = s.pts[s.pts.length - 1];
      sv += '<text x="' + (X(last.t) + 7).toFixed(1) + '" y="' + (Y(last.mu) + 3.5).toFixed(1) +
        '" font-size="10" font-weight="700" fill="' + cor + '">' + br(last.mu, 1) + "</text>";
    });
    sv += "</svg>";

    // resumo por casa: abertura → agora (μ) + Δ + mudanças de linha
    var sum = '<div class="ex-sum">' + series.map(function (s) {
      var a = s.pts[0], z = s.pts[s.pts.length - 1];
      var d = z.mu - a.mu;
      var nCh = 0;
      for (var q = 1; q < s.pts.length; q++) if (s.pts[q].L !== s.pts[q - 1].L) nCh++;
      return '<div class="ex-sum-item"><span class="dot" style="background:' + casaCor(s.casa) + '"></span>' +
        LOGO(s.casa, "house-logo-sm") + " μ " + br(a.mu, 1) + " → <b>" + br(z.mu, 1) + "</b>" +
        ' <span class="ex-drift">' + (d > 0 ? "+" : "") + br(d, 1) + "</span>" +
        (nCh ? ' <span class="pm">◇ ' + nCh + " troca" + (nCh > 1 ? "s" : "") + " de linha</span>" : "") +
        ' <span class="pm">(' + s.pts.length + " pts)</span></div>";
    }).join("") + "</div>";

    var leg =
      '<div class="ex-chart-head">' +
      '<div class="ex-chart-title-row">Total implícito do mercado (μ) · ' + esc(mercado) +
      (casaSel && casaSel !== "__all__" ? " · " + LOGO(casaSel, "house-logo-sm") : " · todas as casas") + "</div>" +
      '<div class="mv-legend">' +
      series.map(function (s) {
        return '<span><span class="sw" style="background:' + casaCor(s.casa) + ';height:3px"></span>' +
          LOGO(s.casa, "house-logo-sm") + "</span>";
      }).join("") +
      '<span class="ex-leg-note">μ = total que a odd embute (sem juice) · ◇ = a casa trocou a linha · odd do over subindo = μ caindo</span>' +
      "</div></div>";

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
    var b = H.banco || {}, nv = (H.head || {}).n_valid || 0, head = H.head || {};
    var el = document.createElement("div");
    var formacao = head.em_formacao != null ? head.em_formacao : (nv < LIM.head);
    el.className = "capbar " + (nv >= LIM.head ? "cap-green" : (nv > 0 ? "cap-yellow" : "cap-red"));
    var q = b.quality || {};
    var qBits = Object.keys(q).map(function (k) {
      return (Q_LABEL[k] || k) + " <b>" + q[k] + "</b>";
    }).join(" · ");
    el.innerHTML = "Banco de odds: <b>" + (b.monitoradas || 0) + "</b> linhas · <b>" +
      (b.liquidadas || 0) + "</b> linhas liquidadas · <b>" + (b.clv_validas || 0) +
      "</b> linhas CLV estritas · <b>" + (b.sinais_clv || nv) + "</b> sinais independentes · " +
      br(b.moveu_pct, 1) + "% moveram" +
      (formacao
        ? '<div class="cap-note"><b>CLV em formação</b> — precisa ≥' + (head.limiar_clv || LIM.head) +
          " sinais jogo+mercado com open e close pré-KO (agora " + nv + "). Não use taxa/ROI agregado ainda.</div>"
        : "") +
      (qBits ? '<div class="cap-note">Qualidade: ' + qBits + "</div>" : "") +
      '<div class="cap-note">Métricas e IC usam <b>1 sinal por jogo+mercado</b>: linha mais equilibrada e preço de abertura perto de 2,00. ' +
      'Alternativas, casas e os dois lados continuam no explorador, mas não multiplicam a amostra. ' +
      'O ROI é diagnóstico dessa regra fixa, <b>não</b> um backtest dos sinais do modelo.</div>' +
      '<div class="cap-note">Explore um <b>jogo → mercado → main line</b> para ver close observado e kickoff separadamente.</div>';
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
      h.beat_close_rate == null ? "wait" : (h.beat_close_rate >= 50 ? "pos" : "neg"),
      "N=" + (h.n_valid || 0) + " sinais / " + (h.n_valid_rows || h.n_valid || 0) + " linhas · " + ciText(h.beat_ci));
    tiles += statTile("CLV médio", sign(h.clv_medio, 1), cls(h.clv_medio),
      "mediana " + sign(h.clv_mediana, 1) + " · " + ciText(h.clv_ci));
    tiles += statTile("Placar (green)", pct(h.green_geral, 1), "",
      "N=" + (h.n_settled_dec || 0) + " sinais decididos · " + ciText(h.green_geral_ci));
    if (h.roi_abertura != null) {
      tiles += statTile("ROI abertura", sign(h.roi_abertura, 1), cls(h.roi_abertura),
        "N=" + (h.roi_n || 0) + " sinais · " + (h.roi_pushes || 0) + " pushes · " + ciText(h.roi_abertura_ci) +
        " · vs fecha " + sign(h.roi_fechamento, 1));
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
      return rowEpoch(b) - rowEpoch(a);
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
        filt.appendChild(chip(esc(m), state.merc === m, "", function () { state.merc = m; render(); }));
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
        // quality dominante do jogo (pior das rows)
        var qRank = { post_kickoff: 0, no_close: 1, late_open: 2, full_prematch: 3, open: 2, unknown: 2 };
        var bestQ = null, bestR = 99;
        Object.keys(g.mercados).forEach(function (m) {
          Object.keys(g.mercados[m].linhas || {}).forEach(function (Lk) {
            var ln = g.mercados[m].linhas[Lk];
            Object.keys(ln.lados || {}).forEach(function (lado) {
              (ln.lados[lado].rows || []).forEach(function (r) {
                var q = r.quality; if (!q) return;
                var rk = qRank[q] != null ? qRank[q] : 2;
                if (rk < bestR) { bestR = rk; bestQ = q; }
              });
            });
          });
        });
        card.innerHTML =
          '<div class="ex-g-top"><div class="ex-g-name">' + esc(g.jogo) + '</div>' +
          '<div class="ex-g-when">' + esc(fmtBrt(g.kickoff_epoch || g.kickoff || g.data, true)) + "</div></div>" +
          '<div class="ex-g-meta">' +
          (g.settled ? '<span class="ex-badge done">liquidado</span>' : '<span class="ex-badge live">aberto</span>') +
          (bestQ ? qBadge(bestQ) : "") +
          '<span class="ex-g-m">' + nMerc + " mercado" + (nMerc === 1 ? "" : "s") + "</span>" +
          '<span class="ex-g-c">' + Object.keys(g.casas).map(function (c) { return LOGO(c, "house-logo-sm"); }).join(" ") + "</span></div>";
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
      '<div class="ex-d-sub">' + esc(fmtBrt(g.kickoff || g.data, true)) +
      (g.settled ? ' · liquidado' : ' · em aberto') +
      " · " + Object.keys(g.casas).map(function (c) { return LOGO(c, "house-logo-sm"); }).join(" ") + "</div>";
    head.appendChild(title);
    root.appendChild(head);

    // chips de mercado
    var mbar = document.createElement("div"); mbar.className = "bar";
    Object.keys(g.mercados).forEach(function (m) {
      mbar.appendChild(chip(esc(m), state.mercado === m, "", function () {
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

    // chips de CASA: todas as casas (uma cor por casa) ou uma casa só
    var casasSerie = casasComSerie(g.id, state.mercado, state.linha);
    var overRows = (ln.lados["Mais"] || {}).rows || [];
    var underRows = (ln.lados["Menos"] || {}).rows || [];
    var casasAll = {};
    overRows.forEach(function (r) { casasAll[r.casa] = 1; });
    underRows.forEach(function (r) { casasAll[r.casa] = 1; });
    casasSerie.forEach(function (c) { casasAll[c] = 1; });
    var casaList = Object.keys(casasAll);
    if (!state.casa || (state.casa !== "__all__" && casaList.indexOf(state.casa) < 0)) {
      state.casa = "__all__";
    }

    var cbar = document.createElement("div"); cbar.className = "bar";
    var labCasa = document.createElement("span");
    labCasa.className = "ex-bar-lab"; labCasa.textContent = "Casa:";
    cbar.appendChild(labCasa);
    cbar.appendChild(chip("Todas as casas", state.casa === "__all__", "", function () {
      state.casa = "__all__"; render();
    }));
    casaList.forEach(function (c) {
      cbar.appendChild(chip(LOGO(c, "house-logo-sm"), state.casa === c, "", function () {
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

    var lnTxt = br(state.linha, 1);
    var tbl;
    if (state.casa === "__all__") {
      // resumo por casa da linha selecionada (abertura → fechamento dos 2 lados)
      tbl = '<table class="lad ex-odds"><thead><tr><th>Casa</th><th>+ abre</th><th>+ fecha</th><th>− abre</th><th>− fecha</th><th>CLV +</th></tr></thead><tbody>';
      casaList.forEach(function (c) {
        var ro = overRows.filter(function (r) { return r.casa === c; })[0];
        var ru = underRows.filter(function (r) { return r.casa === c; })[0];
        function endv(r) { return r ? (r.close != null ? r.close : r.last) : null; }
        tbl += "<tr><td>" + LOGO(c, "house-logo-sm") + "</td>" +
          '<td class="o">' + br(ro && ro.open, 2) + '</td><td class="o">' + br(endv(ro), 2) + "</td>" +
          '<td class="u">' + br(ru && ru.open, 2) + '</td><td class="u">' + br(endv(ru), 2) + "</td>" +
          '<td class="' + (ro && ro.clv_valido !== false ? cls(ro.clv) : "") + '">' +
          (ro && ro.clv_valido !== false ? sign(ro.clv, 1) : "—") + "</td></tr>";
      });
      tbl += "</tbody></table>";
    } else {
      var o = overRows.filter(function (r) { return r.casa === state.casa; })[0];
      var u = underRows.filter(function (r) { return r.casa === state.casa; })[0];
      tbl = '<table class="lad ex-odds"><thead><tr><th>Lado</th><th>Abertura</th><th>Fechamento</th><th>Δ%</th><th>CLV</th></tr></thead><tbody>';
      var rowSide = function (lab, r, tdCls) {
        if (!r) return "<tr><td>" + lab + "</td><td colspan='4' class='pm'>—</td></tr>";
        var close = r.close != null ? r.close : r.last;
        var d = (r.open && close) ? ((close / r.open - 1) * 100) : null;
        return "<tr><td><b>" + lab + "</b></td>" +
          '<td class="' + tdCls + '">' + br(r.open, 2) + "</td>" +
          '<td class="' + tdCls + '">' + br(close, 2) + "</td>" +
          '<td class="' + cls(d) + '">' + sign(d, 1) + "</td>" +
          '<td class="' + (r.clv_valido === false ? "" : cls(r.clv)) + '">' +
          (r.clv_valido === false ? "—" : sign(r.clv, 1)) + "</td></tr>";
      };
      tbl += rowSide("Mais de " + lnTxt, o, "o") + rowSide("Menos de " + lnTxt, u, "u") + "</tbody></table>";
    }

    panel.innerHTML =
      '<div class="ex-panel-grid">' + resultHtml +
      '<div class="ex-odds-wrap"><div class="ex-panel-title">' +
      (state.casa === "__all__" ? "Todas as casas" : LOGO(state.casa || "—", "house-logo-sm")) +
      " · linha " + br(state.linha, 1) + " · " + esc(state.mercado) +
      "</div>" + tbl + "</div></div>" +
      impliedChart(g.id, state.mercado, state.casa || "__all__");

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
        box.appendChild(chip(esc(m), state.merc === m, "", function () { state.merc = m; render(); }));
      });
    }
    if (state.aba === "liquidadas") {
      box.appendChild(chip("🟢 green", state.res === "green", "val", function () { state.res = state.res === "green" ? "todos" : "green"; render(); }));
      box.appendChild(chip("🔴 red", state.res === "red", "ord", function () { state.res = state.res === "red" ? "todos" : "red"; render(); }));
      if (dataset.some(function (r) { return r.push; })) {
        box.appendChild(chip("⚪ push", state.res === "push", "", function () { state.res = state.res === "push" ? "todos" : "push"; render(); }));
      }
    }
    // filtro qualidade (liquidadas + abertas)
    if (state.aba === "liquidadas" || state.aba === "abertas") {
      ["todos", "full_prematch", "late_open", "no_close", "post_kickoff"].forEach(function (q) {
        var lab = q === "todos" ? "Qualidade: todas" : (Q_LABEL[q] || q);
        box.appendChild(chip(lab, state.quality === q, "", function () {
          state.quality = q; render();
        }));
      });
    }
    return box;
  }

  function applyFilters(rows) {
    return rows.filter(function (r) {
      if (state.merc !== "todos" && r.mercado !== state.merc) return false;
      if (state.aba === "liquidadas" && state.res !== "todos") {
        if (state.res === "green" && r.won !== true) return false;
        if (state.res === "red" && r.won !== false) return false;
        if (state.res === "push" && !r.push) return false;
      }
      if (state.quality !== "todos" && (r.quality || "") !== state.quality) return false;
      return true;
    });
  }

  function tblLiquidadas(rows) {
    if (!rows.length) return '<div class="empty"><div class="big">📭</div>Nenhuma linha liquidada com esses filtros.</div>';
    var t = '<div class="hist-scroll"><table class="lad hist-tbl"><thead><tr>' +
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Fecha</th><th>CLV</th><th>Qual.</th><th>Res.</th><th>Mov.</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var invalid = !r.clv_valido;
      var resultTxt = r.push ? "push" : (r.won === true ? "green" : (r.won === false ? "red" : "—"));
      var resultCls = r.push ? "hist-mv flat" : (r.won === true ? "hist-mv up" : (r.won === false ? "hist-mv dn" : ""));
      t += '<tr class="' + (invalid ? "sm" : "") + (anyMv(r.gk) ? " has-mv" : "") + '" data-gk="' + esc(r.gk || "") +
        '" data-gid="' + esc(r.gid || "") + '" data-merc="' + esc(r.mercado || "") + '" data-line="' + esc(r.linha) + '">' +
        '<td class="jg">' + esc(r.jogo) + "</td>" +
        "<td>" + (ABBR[r.mercado] || esc(r.mercado)) + "</td>" +
        '<td class="ln">' + br(r.linha, 1) + "</td><td>" + esc(r.lado) + "</td>" +
        '<td class="o">' + br(r.open, 2) + '</td><td class="u">' + br(r.close, 2) + "</td>" +
        '<td class="' + (invalid ? "" : cls(r.clv)) + '">' + (invalid ? "—" : sign(r.clv, 1)) + "</td>" +
        "<td>" + qBadge(r.quality) + "</td>" +
        '<td class="' + resultCls + '">' + resultTxt + "</td>" +
        "<td>" + sparkline(r.gk, r.casa) + "</td></tr>";
    });
    return t + "</tbody></table></div>";
  }

  function tblAbertas(rows) {
    if (!rows.length) return '<div class="empty"><div class="big">🕓</div>Nenhuma linha aberta com movimento.</div>';
    var t = '<div class="hist-scroll"><table class="lad hist-tbl"><thead><tr>' +
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Agora</th><th>Δ%</th><th>Qual.</th><th>Obs</th><th>Mov.</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var d = r.drift_pct;
      var mv = d == null ? "flat" : (d < 0 ? "up" : (d > 0 ? "dn" : "flat"));
      t += '<tr class="' + (anyMv(r.gk) ? "has-mv" : "") + '" data-gk="' + esc(r.gk || "") +
        '" data-gid="' + esc(r.gid || "") + '" data-merc="' + esc(r.mercado || "") + '" data-line="' + esc(r.linha) + '">' +
        '<td class="jg">' + esc(r.jogo) + "</td><td>" + (ABBR[r.mercado] || esc(r.mercado)) + "</td>" +
        '<td class="ln">' + br(r.linha, 1) + "</td><td>" + esc(r.lado) + "</td>" +
        '<td class="o">' + br(r.open, 2) + '</td><td class="u">' + br(r.last, 2) + "</td>" +
        '<td class="hist-mv ' + mv + '">' + sign(d, 1) + "</td>" +
        "<td>" + qBadge(r.quality) + "</td>" +
        "<td>" + (r.n_moves || 0) + "</td>" +
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
    var shownSettled = (H.liquidadas || []).length;
    var totalSettled = H.liquidadas_total != null ? H.liquidadas_total : shownSettled;
    var settledChip = shownSettled + (totalSettled > shownSettled ? ("/" + totalSettled + " · limite " + (H.liquidadas_limit || shownSettled)) : "");
    nav.appendChild(chip("Liquidadas <span class='ct2'>" + settledChip + "</span>",
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
    var limitNote = state.aba === "liquidadas" && totalSettled > shownSettled
      ? " · exibindo no máximo " + (H.liquidadas_limit || shownSettled) + " de " + totalSettled
      : "";
    meta.innerHTML = vis.length + " linha" + (vis.length === 1 ? "" : "s") + limitNote +
      " · atualizado " + esc(H.gerado_iso || H.gerado || "?");
    root.appendChild(meta);
    var tbl = document.createElement("div");
    tbl.innerHTML = state.aba === "liquidadas" ? tblLiquidadas(vis) : tblAbertas(vis);
    root.appendChild(tbl);

    // clique em linha → abre explorador naquele jogo/mercado/linha
    tbl.querySelectorAll("tr.has-mv, tr[data-gk]").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.onclick = function () {
        var gid = tr.getAttribute("data-gid") || "";
        var mercado = tr.getAttribute("data-merc") || "";
        var linha = parseFloat(tr.getAttribute("data-line"));
        if (!gid || !mercado || isNaN(linha)) return;
        state.aba = "explorar";
        state.game = gid;
        state.mercado = mercado;
        state.linha = linha;
        render();
      };
    });
  }

  render();
  window.__renderHist = render;
})();
