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
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Fecha</th><th>CLV</th><th>Res.</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var invalid = !r.clv_valido;
      t += '<tr class="' + (invalid ? "sm" : "") + '"' + (invalid ? ' title="abertura pós-apito — sem CLV real"' : "") + '>' +
        '<td class="jg" title="' + esc(r.jogo) + ' · ' + esc(r.data) + ' · ' + esc(r.casa) + '">' + esc(r.jogo) + '</td>' +
        '<td title="' + esc(r.mercado) + '">' + (ABBR[r.mercado] || esc(r.mercado)) + '</td>' +
        '<td class="ln">' + br(r.linha, 1) + '</td>' +
        '<td>' + esc(r.lado) + '</td>' +
        '<td class="o">' + br(r.open, 2) + '</td>' +
        '<td class="u">' + br(r.close, 2) + '</td>' +
        '<td class="' + (invalid ? "" : cls(r.clv)) + '">' + (invalid ? "—" : sign(r.clv, 1)) + '</td>' +
        '<td class="' + (r.won ? "hist-mv up" : "hist-mv dn") + '" title="total no jogo: ' + esc(r.result) + '">' + (r.won ? "green" : "red") + '</td>' +
        '</tr>';
    });
    return t + "</tbody></table></div>";
  }

  function tblAbertas(rows) {
    if (!rows.length) return '<div class="empty"><div class="big">🕓</div>Nenhuma linha aberta com movimento agora.<br><span style="font-size:12px">O movimento aparece quando uma linha é capturada em mais de uma rodada antes do jogo.</span></div>';
    var t = '<div class="hist-scroll"><table class="lad hist-tbl"><thead><tr>' +
      '<th class="jg">Jogo</th><th>Merc</th><th>Ln</th><th>Lado</th><th>Abre</th><th>Agora</th><th>Δ%</th><th>Obs</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var d = r.drift_pct;
      var mv = d == null ? "flat" : (d < 0 ? "up" : (d > 0 ? "dn" : "flat"));
      t += '<tr>' +
        '<td class="jg" title="' + esc(r.jogo) + ' · ' + esc(r.data) + ' · ' + esc(r.casa) + '">' + esc(r.jogo) + '</td>' +
        '<td title="' + esc(r.mercado) + '">' + (ABBR[r.mercado] || esc(r.mercado)) + '</td>' +
        '<td class="ln">' + br(r.linha, 1) + '</td>' +
        '<td>' + esc(r.lado) + '</td>' +
        '<td class="o">' + br(r.open, 2) + '</td>' +
        '<td class="u">' + br(r.last, 2) + '</td>' +
        '<td class="hist-mv ' + mv + '">' + sign(d, 1) + '</td>' +
        '<td>' + (r.n_moves || 0) + '</td>' +
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
  }

  render();
  window.__renderHist = render; // p/ re-render ao trocar de view, se necessário
})();
