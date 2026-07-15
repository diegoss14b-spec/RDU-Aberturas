/* valor.js — view "Valor (+EV)": linhas com valor + score de confiança (qualidade de dados). */
(function () {
  "use strict";
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function evCls(ev) { return ev >= 10 ? "ev-hi" : ev >= 7 ? "ev-md" : "ev-lo"; }

  var MODELO_MKT = { "Cartões": 1, "Faltas": 1, "Finalizações": 1, "Escanteios": 1 };

  /** Score 0–100: qualidade dos dados, NÃO previsão extra. */
  function confScore(b, j, B) {
    var s = 0;
    // EV na zona útil (15–40 pts)
    var ev = +b.ev_pct || 0;
    if (ev >= 12) s += 20;
    else if (ev >= 8) s += 15;
    else if (ev >= 5) s += 10;
    else s += 5;
    // probabilidade na região calibrada (15–85%)
    var p = +b.nossa_prob || 0;
    if (p >= 20 && p <= 80) s += 20;
    else if (p >= 15 && p <= 85) s += 12;
    else s += 4;
    // cobertura: nº de casas no jogo/mercado
    var nCasas = 1;
    try {
      var m = (j.mercados || {})[b.mercado];
      if (m && typeof m === "object") nCasas = Object.keys(m).length || 1;
      else if (j.casas) nCasas = (j.casas.length || Object.keys(j.casas || {}).length) || 1;
    } catch (e) { /* ignore */ }
    if (nCasas >= 4) s += 20;
    else if (nCasas >= 3) s += 15;
    else if (nCasas >= 2) s += 10;
    else s += 4;
    // fixture sofa confirmado
    if (j.sofa_id) s += 15;
    else s += 5;
    // frescor da mesa
    var mins = ageMins(B.gerado);
    if (mins != null) {
      if (mins <= 90) s += 15;
      else if (mins <= 180) s += 10;
      else if (mins <= 360) s += 5;
    } else s += 8;
    // hard gates: iniciado / desconhecido / board stale → nunca alta confiança
    var toKo = minsToKick(b.inicio || j.inicio);
    var boardAge = ageMins(B.gerado);
    if (toKo == null || toKo <= 0 || (boardAge != null && boardAge > 300) || b.actionable === false) {
      return Math.min(s, 49); // hard cap: no máximo "média/baixa"
    }
    // kickoff razoável (não jogo já começado, nem >48h)
    if (toKo >= 15 && toKo <= 24 * 60) s += 10;
    else if (toKo > 0 && toKo < 15) s += 4; // late
    else if (toKo > 24 * 60 && toKo <= 48 * 60) s += 6;
    return Math.max(0, Math.min(100, Math.round(s)));
  }

  function ageMins(gerado) {
    var m = /(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(gerado || "");
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    return Math.round((Date.now() - d.getTime()) / 60000);
  }

  function minsToKick(inicio) {
    // "dd/mm HH:MM" (BRT implícito)
    var m = /(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/.exec(inicio || "");
    if (!m) return null;
    var now = new Date();
    var y = now.getFullYear();
    var d = new Date(y, +m[2] - 1, +m[1], +m[3], +m[4]);
    // se já passou >12h no calendário, assume ano seguinte (virada)
    if (d.getTime() - now.getTime() < -12 * 3600 * 1000) d.setFullYear(y + 1);
    return Math.round((d.getTime() - now.getTime()) / 60000);
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
          jogo: j.jogo, liga: j.liga, inicio: j.inicio, mercado: v.mercado, linha: v.linha,
          lado: v.lado, casa: v.casa, odd: v.odd, nossa_prob: v.nossa_prob, edge_pp: v.edge_pp,
          ev_pct: v.ev_pct, mu: v.mu, conf: conf, nCasas: nCasas, sofa_id: j.sofa_id || null,
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
    var head = '<div class="sub">Linhas com <b style="color:var(--green)">valor (+EV)</b> pelos modelos oficiais, ranqueadas por <b>confiança</b> (qualidade dos dados) e EV%. ' +
      'Confiança ≠ previsão: mede cobertura, frescor, fixture e região calibrada. A stake é sua.</div>'
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

    var boardAge = ageMins(B.gerado);
    var boardStale = boardAge != null && boardAge > 300;
    var filtered = bets.filter(function (b) {
      // hard: nunca mostrar started/unknown/stale como acionável
      var mk = minsToKick(b.inicio);
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
        + ' · μ ' + b.mu
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

    var meta = '<div class="meta">' + filtered.length + ' acionável' + (filtered.length > 1 ? 'is' : '')
      + ' de ' + bets.length + ' flag' + (bets.length > 1 ? 's' : '')
      + (dist ? ' · ' + dist : '')
      + ' · atualizado ' + esc(B.gerado || "") + '</div>';
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
