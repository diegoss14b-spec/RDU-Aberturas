/* valor.js — view "Valor (+EV)": linhas com valor + score de confiança (qualidade de dados). */
(function () {
  "use strict";
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function evCls(ev) { return ev >= 10 ? "ev-hi" : ev >= 7 ? "ev-md" : "ev-lo"; }

  var MODELO_MKT = { "Cartões": 1, "Faltas": 1, "Finalizações": 1, "Escanteios": 1 };

  /** Score 0–100 de qualidade operacional; EV não participa do score. */
  function confScore(b, j, B) {
    var s = 0;
    var nCasas = 1;
    try {
      var m = (j.mercados || {})[b.mercado];
      if (m && typeof m === "object") nCasas = Object.keys(m).length || 1;
      else if (j.casas) nCasas = (j.casas.length || Object.keys(j.casas || {}).length) || 1;
    } catch (e) { /* ignore */ }
    if (nCasas >= 4) s += 35;
    else if (nCasas >= 3) s += 28;
    else if (nCasas >= 2) s += 18;
    else s += 6;

    if (j.sofa_id) s += 30;
    else s += 5;

    var mins = ageMins(B.gerado_iso || B.gerado);
    if (mins != null) {
      if (mins <= 90) s += 20;
      else if (mins <= 180) s += 12;
      else if (mins <= 300) s += 5;
    }

    var toKo = minsToKick(b.inicio_iso || b.inicio || j.inicio_iso || j.inicio);
    var boardAge = ageMins(B.gerado_iso || B.gerado);
    if (toKo == null || toKo <= 0 || boardAge == null || boardAge > 120 || b.actionable === false) {
      return Math.min(s, 49);
    }
    if (toKo >= 15 && toKo <= 24 * 60) s += 15;
    else if (toKo > 0 && toKo < 15) s += 6;
    else if (toKo <= 48 * 60) s += 10;
    var score = Math.max(0, Math.min(100, Math.round(s)));
    return j.sofa_id ? score : Math.min(score, 69);
  }

  function parseBrt(value) {
    if (!value) return null;
    var text = String(value);
    var ms = Date.parse(text);
    if (!isNaN(ms) && /(?:Z|[+-]\d{2}:?\d{2})$/.test(text)) return ms;
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
    if (m) return Date.parse(m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + (m[6] || "00") + "-03:00");
    return isNaN(ms) ? null : ms;
  }

  function ageMins(gerado) {
    var ms = parseBrt(gerado);
    return ms == null ? null : Math.round((Date.now() - ms) / 60000);
  }

  function minsToKick(inicio) {
    var iso = parseBrt(inicio);
    if (iso != null && /^\d{4}-/.test(String(inicio))) return Math.round((iso - Date.now()) / 60000);
    var m = /(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/.exec(inicio || "");
    if (!m) return null;
    var now = new Date();
    var y = now.getFullYear();
    var isoBrt = y + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[1]).slice(-2) +
      "T" + ("0" + m[3]).slice(-2) + ":" + m[4] + ":00-03:00";
    var target = Date.parse(isoBrt);
    if (target - Date.now() < -12 * 3600 * 1000) target = Date.parse((y + 1) + isoBrt.slice(4));
    return Math.round((target - Date.now()) / 60000);
  }

  function confLabel(sc) {
    if (sc >= 70) return { cls: "conf-hi", txt: "alta" };
    if (sc >= 50) return { cls: "conf-md", txt: "média" };
    return { cls: "conf-lo", txt: "baixa" };
  }

  var filt = { minConf: 0, multiCasa: false, near: false };

  window.renderValor = function () {
    var root = document.getElementById("view-valor");
    if (!root) return;
    var B = window.BOARD || {};
    var bets = [];
    (B.jogos || []).forEach(function (j) {
      (j.valor || []).forEach(function (v) {
        var conf = confScore(v, j, B);
        var nCasas = 1;
        try {
          var m = (j.mercados || {})[v.mercado];
          if (m && typeof m === "object") nCasas = Object.keys(m).length || 1;
        } catch (e) { /* ignore */ }
        bets.push({
          jogo: j.jogo, liga: j.liga, inicio: j.inicio, inicio_iso: j.inicio_iso,
          mercado: v.mercado, linha: v.linha,
          lado: v.lado, casa: v.casa, odd: v.odd, nossa_prob: v.nossa_prob, edge_pp: v.edge_pp,
          ev_pct: v.ev_pct, mu: v.mu, mu_cal: v.mu_cal, mu_raw: v.mu_raw,
          conf: conf, nCasas: nCasas, sofa_id: j.sofa_id || null,
          p_push: v.p_push || 0, push_line: !!v.push_line, fair_odd: v.fair_odd,
          actionable: v.actionable !== false, game_state: j.game_state || null,
          model_status: v.model_status || (B.model && B.model.status) || "production"
        });
      });
    });
    // ordena por confiança desc, depois EV
    bets.sort(function (a, b) {
      if (b.conf !== a.conf) return b.conf - a.conf;
      return b.ev_pct - a.ev_pct;
    });

    var mod = B.model || {};
    var head = '<div class="sub">Linhas com <b style="color:var(--green)">valor (+EV)</b> pelos modelos oficiais, ranqueadas por <b>qualidade operacional</b> e, depois, EV%. ' +
      'Qualidade ≠ EV: o score mede somente cobertura, frescor, fixture e tempo até o jogo. A stake é sua.</div>'
      + '<div class="disc"><b>EV%</b> = p_win × odd + p_push − 1 (linha inteira devolve stake no empate exato). ' +
      '<b>Odd justa</b> = (1 − p_push) / p_win. ' +
      'Modelo: <b>' + esc(mod.source || "value_pricers") + '</b> · status <b>' + esc(mod.status || "production") + '</b>. ' +
      'Só jogos <b>ainda não iniciados</b> entram como acionáveis. Odds de um instante — <b>podem ter movido</b>.</div>';

    // filtros — "Todos" ainda exclui jogos iniciados (hard gate)
    var filtHtml = '<div class="vb-filt" id="vb-filt">'
      + '<span class="chip' + (filt.minConf === 0 ? ' on' : '') + '" data-f="all">Acionáveis</span>'
      + '<span class="chip' + (filt.minConf === 70 ? ' on' : '') + '" data-f="hi">Alta confiança</span>'
      + '<span class="chip' + (filt.multiCasa ? ' on' : '') + '" data-f="multi">≥2 casas</span>'
      + '<span class="chip' + (filt.near ? ' on' : '') + '" data-f="near">Próx. 90 min</span>'
      + '</div>';

    var boardAge = ageMins(B.gerado_iso || B.gerado);
    var boardStale = boardAge == null || boardAge > 120;
    var filtered = bets.filter(function (b) {
      // hard: nunca mostrar started/unknown/stale como acionável
      var mk = minsToKick(b.inicio_iso || b.inicio);
      if (mk == null || mk <= 0) return false;
      if (boardStale) return false;
      if (b.actionable === false) return false;
      if (b.model_status && b.model_status.indexOf("shadow") === 0) return false;
      if (b.conf < filt.minConf) return false;
      if (filt.multiCasa && b.nCasas < 2) return false;
      if (filt.near) {
        if (mk > 90) return false;
      }
      return true;
    });

    // distribuição por mercado
    var byM = {};
    filtered.forEach(function (b) { byM[b.mercado] = (byM[b.mercado] || 0) + 1; });
    var dist = Object.keys(byM).sort(function (a, b) { return byM[b] - byM[a]; })
      .map(function (m) { return esc(m) + " <b>" + byM[m] + "</b>"; }).join(" · ");

    var staleBanner = boardStale
      ? '<div class="disc" style="border-color:var(--red);color:var(--red)"><b>Board desatualizado</b> (' + boardAge + ' min). Valor acionável desabilitado até nova captura.</div>'
      : '';

    if (!bets.length) {
      root.innerHTML = head + staleBanner + '<div class="empty"><div class="big">🎯</div>Nenhuma aposta de valor no momento.<br>'
        + '<span style="font-size:12px;color:var(--faint)">Aparecem quando há jogo das ligas cobertas com mercado aberto nas casas.</span></div>';
      return;
    }

    if (!filtered.length) {
      root.innerHTML = head + staleBanner + filtHtml + '<div class="empty"><div class="big">🔍</div>Nenhum sinal acionável com este filtro'
        + (boardStale ? ' (board stale).' : ' (jogos iniciados/sem horário ficam de fora).') + '</div>';
      bindFilt(root);
      return;
    }

    var rows = filtered.map(function (b, i) {
      var fair = (b.fair_odd != null) ? b.fair_odd
        : (b.nossa_prob > 0 ? (100 / b.nossa_prob) : 0);
      var cl = confLabel(b.conf);
      var confHtml = '<span class="vb-conf ' + cl.cls + '" title="Score de qualidade dos dados (0–100)">'
        + '<span class="vb-conf-bar"><i style="width:' + b.conf + '%"></i></span> '
        + b.conf + ' · ' + cl.txt + '</span>';
      var pushTag = b.push_line
        ? ' <span class="vb-push" title="Linha inteira: empate exato = devolução (push)">push</span>'
        : '';
      var pushMeta = (b.p_push > 0)
        ? ' · push ' + Number(b.p_push).toFixed(0) + '%'
        : '';
      return '<div class="vbet">'
        + '<div class="vb-rank">' + (i + 1) + '</div>'
        + '<div class="vb-main">'
        + '<div class="vb-top"><span class="vb-ev ' + evCls(b.ev_pct) + '">+' + b.ev_pct.toFixed(1) + '%</span>'
        + confHtml
        + '<span class="vb-pick">' + esc(b.mercado) + ' <b>' + esc(b.lado) + ' ' + b.linha + '</b>' + pushTag + '</span></div>'
        + '<div class="vb-game">' + esc(b.jogo) + '</div>'
        + '<div class="vb-meta">' + esc(b.inicio) + (b.liga ? ' · ' + esc(b.liga) : '')
        + ' · μ cal ' + (b.mu_cal != null ? b.mu_cal : b.mu)
        + (b.mu_raw != null && Math.abs(b.mu_raw - (b.mu_cal != null ? b.mu_cal : b.mu)) >= 0.05 ? (' · raw ' + b.mu_raw) : '')
        + (b.sofa_id ? ' · sofa' : ' · sem fixture')
        + ' · ' + b.nCasas + ' casa' + (b.nCasas > 1 ? 's' : '')
        + pushMeta
        + '</div>'
        + '</div>'
        + '<div class="vb-num">'
        + '<div class="vb-odd"><span class="vb-house">' + esc(b.casa) + '</span> @ <b>' + b.odd.toFixed(2) + '</b></div>'
        + '<div class="vb-prob">nossa <b>' + b.nossa_prob.toFixed(0) + '%</b> · justa ' + Number(fair).toFixed(2) + '</div>'
        + '</div>'
        + '</div>';
    }).join("");

    var meta = '<div class="meta">' + filtered.length + (filtered.length === 1 ? ' acionável' : ' acionáveis')
      + ' de ' + bets.length + ' flag' + (bets.length > 1 ? 's' : '')
      + (dist ? ' · ' + dist : '')
      + ' · atualizado ' + esc(B.gerado_iso || B.gerado || "") + '</div>';
    root.innerHTML = head + staleBanner + filtHtml + meta + '<div class="vbets">' + rows + '</div>';
    bindFilt(root);
  };

  function bindFilt(root) {
    var bar = root.querySelector("#vb-filt");
    if (!bar) return;
    bar.querySelectorAll(".chip").forEach(function (c) {
      c.onclick = function () {
        var f = c.getAttribute("data-f");
        if (f === "all") { filt.minConf = 0; filt.multiCasa = false; filt.near = false; }
        else if (f === "hi") { filt.minConf = filt.minConf === 70 ? 0 : 70; }
        else if (f === "multi") { filt.multiCasa = !filt.multiCasa; }
        else if (f === "near") { filt.near = !filt.near; }
        window.renderValor();
      };
    });
  }
})();
