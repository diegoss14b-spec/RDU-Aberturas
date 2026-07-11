// Histórico & CLV — render a partir de window.HIST (gerado por build_history.py)
(function () {
  var H = window.HIST || { gerado: "?", limiares: { head: 30, bucket: 20, roi: 50 },
    banco: { monitoradas: 0, liquidadas: 0, clv_validas: 0, moveu_pct: 0 },
    head: { n_valid: 0, n_settled: 0, green_geral: null }, recortes: { mercado: [], casa: [], lado: [] },
    liquidadas: [], abertas: [] };
  var LIM = H.limiares || { head: 30, bucket: 20, roi: 50 };
  var ABBR = { "Cartões": "CAR", "Faltas": "FAL", "Finalizações": "FIN", "Impedimentos": "IMP", "Laterais": "LAT", "Tiros de meta": "TM" };
  var state = { aba: "liquidadas", merc: "todos", res: "todos" };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function br(x, d) { if (x == null || x !== x) return "—"; var n = Number(x); return (d != null ? n.toFixed(d) : String(n)).replace(".", ","); }
  function pct(x, d) { return x == null ? "—" : br(x, d == null ? 1 : d) + "%"; }
  function sign(x, d) { if (x == null) return "—"; return (x > 0 ? "+" : "") + br(x, d == null ? 1 : d) + "%"; }
  function cls(x) { return x == null ? "" : (x > 0 ? "pos" : (x < 0 ? "neg" : "")); }
  function pm(ci) { if (!ci || ci[0] == null) return ""; return ' <span class="pm">±' + Math.round((ci[1] - ci[0]) / 2) + '</span>'; }

  // --- gráficos de movimentação (window.MOVES de build_moves.py) ---
  var MV = window.MOVES || {};
  var CASA_COR = { betano: "#6d28d9", superbet: "#2f6f57", estrelabet: "#c2410c", "7k": "#1d4ed8" };

  function mvSeries(gk, casa) { var g = MV[gk]; return (g && g[casa] && g[casa].length >= 2) ? g[casa] : null; }

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

  function bigChart(gk) {
    var g = MV[gk];
    if (!g) return '<div class="pm">sem série pra essa linha</div>';
    var casas = Object.keys(g).filter(function (c) { return c !== "_ko" && g[c].length; });
    if (!casas.length) return '<div class="pm">sem série pra essa linha</div>';
    var all = [];
    casas.forEach(function (c) { all = all.concat(g[c]); });
    var ts = all.map(function (x) { return x[0]; }), os = all.map(function (x) { return x[1]; });
    var t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
    if (g._ko && g._ko > t1 && g._ko - t1 < 48 * 60) t1 = g._ko;   // estende até o kickoff se perto
    var o0 = Math.min.apply(null, os), o1 = Math.max.apply(null, os);
    var pad = (o1 - o0) * 0.15 + 0.02; o0 -= pad; o1 += pad;
    var W = 640, H = 170, L = 44, R = 10, T = 10, B = 22;
    var dt = (t1 - t0) || 1, dO = (o1 - o0) || 1;
    function X(t) { return L + (t - t0) / dt * (W - L - R); }
    function Y(o) { return T + (1 - (o - o0) / dO) * (H - T - B); }
    function fmtT(m) { var d = new Date(m * 60000); return ("0" + d.getDate()).slice(-2) + "/" + ("0" + (d.getMonth() + 1)).slice(-2) + " " + ("0" + d.getHours()).slice(-2) + "h"; }
    var sv = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '" style="font-family:var(--mono)">';
    // grid horizontal (3 linhas) + labels de odd
    for (var i = 0; i <= 2; i++) {
      var ov = o0 + dO * i / 2, y = Y(ov);
      sv += '<line x1="' + L + '" y1="' + y + '" x2="' + (W - R) + '" y2="' + y + '" stroke="#e7e2d8" stroke-width="1"/>';
      sv += '<text x="' + (L - 6) + '" y="' + (y + 3) + '" text-anchor="end" font-size="9" fill="#9a94a6">' + ov.toFixed(2) + "</text>";
    }
    // labels de tempo (início/fim)
    sv += '<text x="' + L + '" y="' + (H - 6) + '" font-size="9" fill="#9a94a6">' + fmtT(t0) + "</text>";
    sv += '<text x="' + (W - R) + '" y="' + (H - 6) + '" text-anchor="end" font-size="9" fill="#9a94a6">' + fmtT(t1) + "</text>";
    // kickoff
    if (g._ko && g._ko >= t0 && g._ko <= t1) {
      sv += '<line x1="' + X(g._ko) + '" y1="' + T + '" x2="' + X(g._ko) + '" y2="' + (H - B) + '" stroke="#c2410c" stroke-width="1" stroke-dasharray="3,3"/>';
      sv += '<text x="' + X(g._ko) + '" y="' + (T + 8) + '" text-anchor="middle" font-size="8" fill="#c2410c">kickoff</text>';
    }
    casas.forEach(function (c) {
      var cor = CASA_COR[c] || "#6b6577";
      var s = g[c].slice().sort(function (a, b) { return a[0] - b[0]; });
      var pts = s.map(function (x) { return X(x[0]).toFixed(1) + "," + Y(x[1]).toFixed(1); }).join(" ");
      sv += '<polyline points="' + pts + '" fill="none" stroke="' + cor + '" stroke-width="1.8"/>';
      s.forEach(function (x) { sv += '<circle cx="' + X(x[0]).toFixed(1) + '" cy="' + Y(x[1]).toFixed(1) + '" r="2.4" fill="' + cor + '"/>'; });
    });
    sv += "</svg>";
    var leg = '<div class="mv-legend">' + casas.map(function (c) {
      return '<span><span class="sw" style="background:' + (CASA_COR[c] || "#6b6577") + '"></span>' + esc(c) + "</span>";
    }).join("") + "</div>";
    return leg + '<div class="mv-chart">' + sv + "</div>";
  }

  function chip(label, active, extra, onclick) {
    var c = document.createElement("span");
    c.className = "chip" + (extra ? " " + extra : "") + (active ? " on" : "");
    c.innerHTML = label; c.onclick = onclick;
    c.setAttribute("role", "button"); c.tabIndex = 0;
    c.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onclick(); } };
    return c;
  }

  // --- banner de maturidade do banco (sempre) ---
  function banner() {
    var b = H.banco || {}, nv = (H.head || {}).n_valid || 0;
    var cls = nv >= LIM.head ? "cap-green" : (nv > 0 ? "cap-yellow" : "cap-red");
    var el = document.createElement("div");
    el.className = "capbar " + cls;
    el.innerHTML = "Banco de odds: <b>" + (b.monitoradas || 0) + "</b> linhas monitoradas · <b>" +
      (b.liquidadas || 0) + "</b> liquidadas · <b>" + (b.clv_validas || 0) + "</b> com CLV pré-jogo válido · " +
      br(b.moveu_pct, 1) + "% já moveram" +
      '<div class="cap-note">CLV pré-jogo válido = abertura vista <b>antes</b> do apito. Só essas entram nas taxas de valor.</div>';
    return el;
  }

  function statTile(k, val, valCls, sub) {
    return '<div class="stat"><div class="k">' + esc(k) + '</div><div class="v ' + (valCls || "") + '">' + val + '</div>' +
      (sub ? '<div class="s">' + esc(sub) + '</div>' : "") + '</div>';
  }

  // --- cabeçalho de métricas ---
  function headline() {
    var h = H.head || {}, wrap = document.createElement("div");
    var placarTile = statTile("Placar (green)", pct(h.green_geral, 1), "",
      "não é CLV · alta variância · N=" + (h.n_settled || 0));

    if ((h.n_valid || 0) < LIM.head) {
      var e = document.createElement("div"); e.className = "empty";
      e.innerHTML = '<div class="big">⏳</div><b>Ainda sem CLV confiável.</b><br>' +
        '<span style="font-size:12px;line-height:1.6;display:inline-block;margin-top:8px;max-width:460px">' +
        'As ' + (h.n_settled || 0) + ' linhas liquidadas foram vistas <b>depois</b> do apito (abertura = fechamento), então não medem valor de fechamento. ' +
        'O CLV aparece conforme o cron de 4/4h capturar linhas <b>horas antes</b> do jogo. ' +
        'Precisamos de ≥' + LIM.head + ' linhas com abertura pré-jogo (temos ' + (h.n_valid || 0) + ').</span>';
      wrap.appendChild(e);
      var row = document.createElement("div"); row.className = "stat-row";
      row.innerHTML = placarTile; wrap.appendChild(row);
      return wrap;
    }

    var tiles = "";
    var ciTxt = (h.beat_ci && h.beat_ci[0] != null) ? ("IC " + br(h.beat_ci[0], 0) + "–" + br(h.beat_ci[1], 0) + "%") : "";
    tiles += statTile("Bateu o fechamento", pct(h.beat_close_rate, 0),
      h.beat_close_rate == null ? "wait" : (h.beat_close_rate >= 50 ? "pos" : "neg"),
      ciTxt + " · N=" + (h.n_valid || 0));
    tiles += statTile("CLV médio", sign(h.clv_medio, 1), cls(h.clv_medio),
      "mediana " + sign(h.clv_mediana, 1));
    tiles += placarTile;
    if (h.roi_abertura != null) {
      tiles += statTile("ROI na abertura", sign(h.roi_abertura, 1), cls(h.roi_abertura),
        "vs fecha " + sign(h.roi_fechamento, 1) + " · Δ " + sign(h.roi_delta, 1) + " · hipotético 1u");
    }
    var row = document.createElement("div"); row.className = "stat-row"; row.innerHTML = tiles;
    wrap.appendChild(row);

    // barras: green quando bateu × não bateu o fechamento
    if (h.green_bateu != null && h.green_nao != null) {
      var bars = document.createElement("div"); bars.className = "bars";
      function ciTxt(ci) { return (ci && ci[0] != null) ? (" · IC " + br(ci[0], 0) + "–" + br(ci[1], 0) + "%") : ""; }
      function barc(lab, v, ci, no) {
        return '<div class="barc' + (no ? " no" : "") + '"><div class="lab">' + esc(lab) + " — " + pct(v, 1) + ciTxt(ci) +
          "</div><div style=\"background:var(--line2);border-radius:5px\"><div class=\"fill\" style=\"width:" +
          Math.max(0, Math.min(100, v)) + '%"></div></div></div>';
      }
      bars.innerHTML = barc("Green quando BATEU o fechamento (N=" + h.n_bateu + ")", h.green_bateu, h.green_bateu_ci, false) +
        barc("Green quando NÃO bateu (N=" + h.n_nao + ")", h.green_nao, h.green_nao_ci, true);
      wrap.appendChild(bars);
      if (h.green_diff_conclusiva === false) {
        var note = document.createElement("div"); note.className = "meta";
        note.style.marginTop = "-6px"; note.style.marginBottom = "12px";
        note.innerHTML = "⚠ As faixas de confiança se sobrepõem — a diferença ainda <b>não é conclusiva</b> (amostra pequena).";
        wrap.appendChild(note);
      }
    }

    // recortes
    var rec = H.recortes || {};
    [["mercado", "Por mercado"], ["casa", "Por casa"], ["lado", "Por lado"]].forEach(function (p) {
      var rows = rec[p[0]] || [];
      if (!rows.length) return;
      var t = '<div class="hist-scroll" style="margin-bottom:10px"><table class="lad hist-tbl"><thead><tr>' +
        '<th class="jg">' + p[1] + '</th><th>N</th><th>Bateu</th><th>CLV</th><th>Green</th></tr></thead><tbody>';
      rows.forEach(function (r) {
        var small = r.small;
        t += '<tr class="' + (small ? "sm" : "") + '" title="' + (small ? "amostra pequena (&lt;" + LIM.bucket + ")" : "") + '">' +
          '<td class="jg">' + esc(r.nome) + '</td><td>' + r.n + '</td>' +
          (small ? '<td>—</td><td>—</td><td>—</td>' :
            '<td>' + pct(r.beat, 0) + pm(r.beat_ci) + '</td><td class="' + cls(r.clv) + '">' + sign(r.clv, 1) + '</td><td>' + pct(r.green, 0) + pm(r.green_ci) + '</td>') +
          '</tr>';
      });
      t += "</tbody></table></div>";
      var box = document.createElement("div"); box.innerHTML = t; wrap.appendChild(box.firstChild);
    });
    return wrap;
  }

  // --- filtros (aba + mercado + resultado) ---
  function filtros(dataset) {
    // coerção: mercado filtrado que não existe no dataset atual volta a "todos"
    // (evita esconder tudo com filtro herdado da outra aba, sem chip de reset visível)
    if (state.merc !== "todos" && !dataset.some(function (r) { return r.mercado === state.merc; })) state.merc = "todos";
    var box = document.createElement("div"); box.className = "bar";
    box.appendChild(chip("Liquidadas <span class='ct2'>" + (H.liquidadas || []).length + "</span>", state.aba === "liquidadas", "", function () { if (state.aba !== "liquidadas") { state.merc = "todos"; state.res = "todos"; } state.aba = "liquidadas"; render(); }));
    box.appendChild(chip("Abertas (movendo) <span class='ct2'>" + (H.abertas || []).length + "</span>", state.aba === "abertas", "", function () { if (state.aba !== "abertas") { state.merc = "todos"; state.res = "todos"; } state.aba = "abertas"; render(); }));
    // mercados presentes no dataset atual
    var mkts = {};
    dataset.forEach(function (r) { mkts[r.mercado] = 1; });
    if (Object.keys(mkts).length > 1) {
      box.appendChild(chip("Todos mercados", state.merc === "todos", "", function () { state.merc = "todos"; render(); }));
      Object.keys(ABBR).forEach(function (m) {
        if (!mkts[m]) return;
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
      t += '<tr class="' + (invalid ? "sm" : "") + (mvSeries(r.gk, r.casa) ? " has-mv" : "") + '" data-gk="' + esc(r.gk || "") + '"' + (invalid ? ' title="abertura pós-apito — sem CLV real"' : "") + '>' +
        '<td class="jg" title="' + esc(r.jogo) + ' · ' + esc(r.data) + ' · ' + esc(r.casa) + '">' + esc(r.jogo) + '</td>' +
        '<td title="' + esc(r.mercado) + '">' + (ABBR[r.mercado] || esc(r.mercado)) + '</td>' +
        '<td class="ln">' + br(r.linha, 1) + '</td>' +
        '<td>' + esc(r.lado) + '</td>' +
        '<td class="o">' + br(r.open, 2) + '</td>' +
        '<td class="u">' + br(r.close, 2) + '</td>' +
        '<td class="' + (invalid ? "" : cls(r.clv)) + '">' + (invalid ? "—" : sign(r.clv, 1)) + '</td>' +
        '<td class="' + (r.won ? "hist-mv up" : "hist-mv dn") + '" title="total no jogo: ' + esc(r.result) + '">' + (r.won ? "green" : "red") + '</td>' +
        '<td>' + sparkline(r.gk, r.casa) + '</td>' +
        '</tr>';
    });
    return t + "</tbody></table></div>";
  }

  function tblAbertas(rows) {
    if (!rows.length) return '<div class="empty"><div class="big">🕓</div>Nenhuma linha aberta com movimento agora.<br><span style="font-size:12px">O movimento aparece quando uma linha é capturada em mais de uma rodada antes do jogo.</span></div>';
    var t = '<div class="hist-scroll"><table class="lad hist-tbl"><thead><tr>' +
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Agora</th><th>Δ%</th><th>Obs</th><th>Mov.</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var d = r.drift_pct;
      var mv = d == null ? "flat" : (d < 0 ? "up" : (d > 0 ? "dn" : "flat"));
      t += '<tr class="' + (mvSeries(r.gk, r.casa) ? "has-mv" : "") + '" data-gk="' + esc(r.gk || "") + '">' +
        '<td class="jg" title="' + esc(r.jogo) + ' · ' + esc(r.data) + ' · ' + esc(r.casa) + '">' + esc(r.jogo) + '</td>' +
        '<td title="' + esc(r.mercado) + '">' + (ABBR[r.mercado] || esc(r.mercado)) + '</td>' +
        '<td class="ln">' + br(r.linha, 1) + '</td>' +
        '<td>' + esc(r.lado) + '</td>' +
        '<td class="o">' + br(r.open, 2) + '</td>' +
        '<td class="u">' + br(r.last, 2) + '</td>' +
        '<td class="hist-mv ' + mv + '">' + sign(d, 1) + '</td>' +
        '<td>' + (r.n_moves || 0) + '</td>' +
        '<td>' + sparkline(r.gk, r.casa) + '</td>' +
        '</tr>';
    });
    return t + "</tbody></table></div>";
  }

  function render() {
    var root = document.getElementById("hist-root");
    if (!root) return;
    root.innerHTML = "";
    root.appendChild(banner());
    root.appendChild(headline());

    var base = state.aba === "liquidadas" ? (H.liquidadas || []) : (H.abertas || []);
    root.appendChild(filtros(base));
    var vis = applyFilters(base);

    var meta = document.createElement("div"); meta.className = "meta";
    meta.innerHTML = vis.length + (state.aba === "liquidadas" ? " linha" + (vis.length === 1 ? "" : "s") + " liquidada" + (vis.length === 1 ? "" : "s")
      : " linha" + (vis.length === 1 ? "" : "s") + " aberta" + (vis.length === 1 ? "" : "s") + " com movimento") +
      ' · atualizado ' + esc(H.gerado || "?");
    root.appendChild(meta);

    var tbl = document.createElement("div");
    tbl.innerHTML = state.aba === "liquidadas" ? tblLiquidadas(vis) : tblAbertas(vis);
    root.appendChild(tbl);

    // clique numa linha com série -> expande/recolhe o gráfico de movimentação
    tbl.querySelectorAll("tr.has-mv").forEach(function (tr) {
      tr.style.cursor = "pointer";
      tr.title = (tr.title ? tr.title + " · " : "") + "clique pra ver o gráfico";
      tr.onclick = function () {
        var nxt = tr.nextElementSibling;
        if (nxt && nxt.classList.contains("mv-chart-row")) {   // já aberto -> fecha
          nxt.remove(); tr.classList.remove("mv-open"); return;
        }
        // fecha outros abertos
        tbl.querySelectorAll(".mv-chart-row").forEach(function (x) { x.remove(); });
        tbl.querySelectorAll("tr.mv-open").forEach(function (x) { x.classList.remove("mv-open"); });
        var cr = document.createElement("tr");
        cr.className = "mv-chart-row";
        var td = document.createElement("td");
        td.colSpan = tr.children.length;
        td.innerHTML = bigChart(tr.getAttribute("data-gk"));
        cr.appendChild(td);
        tr.parentNode.insertBefore(cr, tr.nextSibling);
        tr.classList.add("mv-open");
      };
    });
  }

  render();
  window.__renderHist = render; // p/ re-render ao trocar de view, se necessário
})();
