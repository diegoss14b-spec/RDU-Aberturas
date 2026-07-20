/* ops.js — Painel de Operação: saúde das capturas, cobertura e avisos */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function br(x, d) {
    if (x == null || x !== x) return "—";
    return Number(x).toFixed(d == null ? 0 : d).replace(".", ",");
  }
  function ageTxt(mins) {
    if (mins == null) return "—";
    if (mins < 1) return "agora";
    if (mins < 60) return "há " + mins + " min";
    var h = Math.floor(mins / 60), m = mins % 60;
    return "há " + h + "h" + (m ? " " + m + "min" : "");
  }
  function parseOpsTime(ts) {
    if (!ts) return null;
    var s = String(ts).trim();
    var bare = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2})(\.\d+)?)?$/;
    var m = bare.exec(s);
    if (m) {
      s = m[1] + "T" + m[2] + ":" + (m[3] || "00") + (m[4] || "") + "-03:00";
    } else {
      s = s.replace(/^(\d{4}-\d{2}-\d{2})\s+/, "$1T");
      s = s.replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
    }
    // Date.parse e inconsistente com fracoes acima de milissegundos em alguns browsers.
    s = s.replace(/(\.\d{3})\d+(?=(?:Z|[+-]\d{2}:?\d{2})$)/, "$1");
    var ms = Date.parse(s);
    return isNaN(ms) ? null : ms;
  }
  function liveAge(ts, fallback) {
    var ms = parseOpsTime(ts);
    if (ms == null) return fallback;
    return Math.max(0, Math.floor((Date.now() - ms) / 60000));
  }
  function clsRate(r) {
    if (r == null) return "";
    if (r >= 90) return "op-good";
    if (r >= 70) return "op-mid";
    return "op-bad";
  }
  function levelCls(lv) {
    return lv === "bad" ? "op-av-bad" : lv === "warn" ? "op-av-warn" : "op-av-info";
  }

  window.renderOps = function () {
    var root = document.getElementById("view-ops");
    if (!root) return;
    var O = window.OPS;
    if (!O) {
      root.innerHTML = '<div class="empty"><div class="big">🛰️</div>Painel de operação ainda não gerado.<br>'
        + '<span style="font-size:12px;color:var(--faint)">Rode <code>python build_ops.py</code> após a captura.</span></div>';
      return;
    }

    var S = O.summary || {};
    var B = O.board || {};
    var H = O.historico || {};
    var L = O.liquidacao || {};
    var casas = O.casas || [];
    var avisos = O.avisos || [];
    var hist7 = O.hist7 || {};
    var heat = O.heat || { casas: [], cols: [] };
    var runs = O.runs || [];
    var fx = O.fixtures || {};
    var summaryAge = liveAge(S.ts_brt, S.age_min);
    var boardAge = liveAge(B.gerado, B.age_min);

    var mb = (window.rduModelBadge ? window.rduModelBadge(window.BOARD && window.BOARD.model) : null);
    var head =
      '<div class="sub">Saúde das <b>capturas</b>, cobertura da <b>mesa</b> e qualidade do <b>histórico</b>. '
      + "Área operacional — não é ranking de tip. Atualizado " + esc(O.gerado || "—") + "."
      + (mb ? ' Modelo do board: <span class="mdl-badge ' + mb.cls + '" title="' + esc(mb.title) + '">' + esc(mb.label) + "</span>." : "")
      + "</div>";

    // --- KPI cards ---
    var kpis = [
      {
        lab: "Última captura",
        val: ageTxt(summaryAge),
        sub: (S.ts_brt || "—") + (S.deploy_allowed === false ? " · deploy bloqueado" : ""),
        tone: summaryAge != null && summaryAge > 180 ? "bad" : summaryAge != null && summaryAge > 90 ? "mid" : "good",
      },
      {
        lab: "Casas ok",
        val: (S.n_ok != null ? S.n_ok : "—") + " / " + ((S.n_ok || 0) + (S.n_fail || 0) || 5),
        sub: (S.total_events != null ? S.total_events + " eventos" : "—") + (S.reason && S.reason !== "ok" ? " · " + S.reason : ""),
        tone: (S.n_fail || 0) === 0 ? "good" : (S.n_ok || 0) >= 2 ? "mid" : "bad",
      },
      {
        lab: "Mesa",
        val: B.n_jogos != null ? String(B.n_jogos) : "—",
        sub: ageTxt(boardAge) + (B.casas && B.casas.length ? " · " + B.casas.join(", ") : ""),
        tone: boardAge != null && boardAge > 120 ? "bad" : "good",
      },
      {
        lab: "Sofa match",
        val: B.sofa_pct != null ? br(B.sofa_pct, 0) + "%" : "—",
        sub: (B.sofa_matched != null ? B.sofa_matched + " jogos" : "—")
          + (fx.n_fixtures ? " · " + fx.n_fixtures + " fixtures" : ""),
        tone: (B.sofa_pct || 0) >= 50 ? "good" : (B.sofa_pct || 0) >= 30 ? "mid" : "bad",
      },
    ];
    var kpiHtml = '<div class="op-kpis">' + kpis.map(function (k) {
      return '<div class="op-kpi op-kpi-' + k.tone + '">'
        + '<div class="op-kpi-lab">' + esc(k.lab) + "</div>"
        + '<div class="op-kpi-val">' + esc(k.val) + "</div>"
        + '<div class="op-kpi-sub">' + esc(k.sub) + "</div>"
        + "</div>";
    }).join("") + "</div>";

    // --- avisos ---
    var avHtml = "";
    if (avisos.length) {
      avHtml = '<div class="op-avisos">' + avisos.map(function (a) {
        return '<div class="op-av ' + levelCls(a.level) + '">' + esc(a.txt) + "</div>";
      }).join("") + "</div>";
    } else {
      avHtml = '<div class="op-avisos"><div class="op-av op-av-info">Nenhum aviso no momento — capturas e mesa dentro do esperado.</div></div>';
    }

    // --- tabela por casa ---
    var casaRows = casas.map(function (c) {
      var h7 = hist7[c.nome] || {};
      var rate = h7.rate;
      var st = c.ok
        ? '<span class="op-pill op-pill-ok">ok</span>'
        : '<span class="op-pill op-pill-fail" title="' + esc(c.error || "") + '">fail</span>';
      var dur = c.duration_sec != null ? br(c.duration_sec, 0) + "s" : "—";
      var n = c.n_events != null ? String(c.n_events) : "—";
      var proxy = c.proxy_br === true ? "BR" : c.proxy_br === false ? "direct" : "—";
      var cAge = liveAge(c.ts_brt, c.age_min);
      var rateHtml = rate != null
        ? '<span class="' + clsRate(rate) + '">' + br(rate, 0) + "% <span class=\"op-muted\">(" + h7.ok + "/" + h7.total + ")</span></span>"
        : "—";
      return "<tr>"
        + "<td><b>" + esc(c.nome) + "</b>" + (c.kind === "fixture" ? ' <span class="op-muted">fixture</span>' : "") + "</td>"
        + "<td>" + st + "</td>"
        + '<td class="op-num">' + esc(n) + "</td>"
        + '<td class="op-num">' + esc(dur) + "</td>"
        + "<td>" + esc(ageTxt(cAge)) + "</td>"
        + "<td>" + esc(proxy) + "</td>"
        + "<td>" + rateHtml + "</td>"
        + '<td class="op-err">' + esc(c.ok ? "" : (c.error || "—")) + "</td>"
        + "</tr>";
    }).join("");

    var casaTbl =
      '<div class="op-sec"><div class="op-sec-h">Casas — última rodada + confiabilidade 7 dias</div>'
      + '<div class="op-table-wrap"><table class="op-table">'
      + "<thead><tr><th>Casa</th><th>Status</th><th>Eventos</th><th>Duração</th><th>Idade</th><th>Proxy</th><th>7 dias</th><th>Erro</th></tr></thead>"
      + "<tbody>" + casaRows + "</tbody></table></div></div>";

    // --- heatmap runs ---
    var heatHtml = "";
    if (heat.cols && heat.cols.length) {
      var headCells = heat.cols.map(function (col) {
        return '<th title="' + esc(col.ts) + '">' + esc((col.ts || "").slice(-5)) + "</th>";
      }).join("");
      var body = (heat.casas || []).map(function (nome, i) {
        var cells = heat.cols.map(function (col) {
          var cell = (col.cells || [])[i] || {};
          var ok = cell.ok;
          var cls = ok === true ? "ht-ok" : ok === false ? "ht-fail" : "ht-na";
          var title = nome + " " + (col.ts || "") + (cell.n != null ? " · n=" + cell.n : "");
          return '<td class="' + cls + '" title="' + esc(title) + '"></td>';
        }).join("");
        return "<tr><th>" + esc(nome) + "</th>" + cells + "</tr>";
      }).join("");
      heatHtml =
        '<div class="op-sec"><div class="op-sec-h">Rodadas recentes <span class="op-muted">(verde = ok · vermelho = fail)</span></div>'
        + '<div class="op-heat-wrap"><table class="op-heat"><thead><tr><th></th>' + headCells + "</tr></thead>"
        + "<tbody>" + body + "</tbody></table></div>"
        + '<div class="op-muted op-note">' + runs.length + " rodadas full nos últimos 7 dias (modo close não entra)</div></div>";
    }

    // --- cobertura mercados ---
    var pm = B.por_mercado || {};
    var mercKeys = Object.keys(pm).sort(function (a, b) {
      return (pm[b].jogos || 0) - (pm[a].jogos || 0);
    });
    var maxJ = mercKeys.length ? (pm[mercKeys[0]].jogos || 1) : 1;
    var mercHtml = mercKeys.length
      ? '<div class="op-sec"><div class="op-sec-h">Cobertura por mercado (mesa publicada)</div>'
        + '<div class="op-mercs">' + mercKeys.map(function (m) {
          var s = pm[m];
          var pct = Math.round(100 * (s.jogos || 0) / maxJ);
          var casasTxt = Object.keys(s.casas || {}).map(function (c) {
            return c + " " + s.casas[c];
          }).join(" · ");
          return '<div class="op-merc">'
            + '<div class="op-merc-top"><b>' + esc(m) + "</b>"
            + '<span class="op-num">' + s.jogos + " jogos</span>"
            + '<span class="op-muted">' + (s.linhas || 0) + " linhas · multi " + (s.multi_casa || 0) + "</span></div>"
            + '<div class="op-bar"><i style="width:' + pct + '%"></i></div>'
            + '<div class="op-merc-casas">' + esc(casasTxt || "—") + "</div>"
            + "</div>";
        }).join("") + "</div></div>"
      : "";

    // --- próximos kickoffs ---
    var soon = B.proximos_24h || [];
    var soonHtml = soon.length
      ? '<div class="op-sec"><div class="op-sec-h">Próximos kickoffs (24h) · ' + soon.length + "</div>"
        + '<div class="op-soon">' + soon.slice(0, 12).map(function (g) {
          return '<div class="op-soon-row">'
            + '<span class="op-soon-t">' + esc(g.inicio) + '</span>'
            + '<span class="op-soon-j">' + esc(g.jogo) + "</span>"
            + '<span class="op-muted">' + g.n_casas + "c · " + g.n_mercados + "m"
            + (g.sofa ? " · sofa" : "")
            + (g.tem_valor ? ' · <span class="op-good">+EV</span>' : "")
            + "</span></div>";
        }).join("") + "</div></div>"
      : "";

    // --- histórico banco ---
    var q = H.quality || {};
    var qTxt = Object.keys(q).length
      ? Object.keys(q).map(function (k) { return k + " " + q[k]; }).join(" · ")
      : "";
    var lm = H.line_moves_7d;
    var histHtml = H
      ? '<div class="op-sec"><div class="op-sec-h">Banco de odds (histórico)</div>'
        + '<div class="op-kpis op-kpis-sm">'
        + kpiMini("Em retry", (L.backlog || {}).total)
        + kpiMini("Monitoradas", H.monitoradas)
        + kpiMini("Liquidadas", H.liquidadas)
        + kpiMini("CLV válidas", H.clv_validas)
        + kpiMini("% moveu", H.moveu_pct != null ? br(H.moveu_pct, 1) + "%" : "—")
        + kpiMini("Line moves 7d", lm != null ? String(lm) : "—")
        + "</div>"
        + (H.clv_em_formacao
          ? '<div class="op-av op-av-warn">CLV em formação — N pré-jogo &lt; ' + esc(String(H.clv_limiar || 30)) +
            " (agora " + esc(String(H.clv_validas || 0)) + "). Não use ROI/green agregado como prova.</div>"
          : "")
        + (qTxt ? '<div class="op-muted op-note">Qualidade captura: ' + esc(qTxt) + "</div>" : "")
        + '<div class="op-muted op-note">CLV válido exige abertura vista antes do apito. Line move = main line mudou. '
        + "Gerado " + esc(H.gerado || "—") + ".</div></div>"
      : "";

    var settleMarkets = Object.keys(L.by_market || {}).sort(function (a, b) {
      return (L.by_market[b].pending || 0) - (L.by_market[a].pending || 0);
    });
    var settleHtml = "";
    if (L.generated_at) {
      var settleRows = settleMarkets.map(function (market) {
        var row = L.by_market[market] || {};
        var age = row.age || {};
        var reasons = row.reasons || {};
        var topReason = Object.keys(reasons).sort(function (a, b) {
          return reasons[b] - reasons[a];
        })[0] || "n/a";
        return "<tr>"
          + "<td><b>" + esc(market) + "</b></td>"
          + '<td class="op-num">' + esc(row.pending || 0) + "</td>"
          + '<td class="op-num">' + esc(age["0-24h"] || 0) + "</td>"
          + '<td class="op-num">' + esc(age["1-3d"] || 0) + "</td>"
          + '<td class="op-num">' + esc(age["3-7d"] || 0) + "</td>"
          + '<td class="op-num">' + esc((age["7-30d"] || 0) + (age["30d+"] || 0)) + "</td>"
          + "<td>" + esc(topReason) + "</td></tr>";
      }).join("");
      settleHtml = '<div class="op-sec"><div class="op-sec-h">Liquida&ccedil;&atilde;o &mdash; backlog retryable por mercado e idade</div>'
        + '<div class="op-table-wrap"><table class="op-table"><thead><tr>'
        + "<th>Mercado</th><th>Retry</th><th>0-24h</th><th>1-3d</th><th>3-7d</th><th>7d+</th><th>Motivo principal</th>"
        + "</tr></thead><tbody>"
        + (settleRows || '<tr><td colspan="7">Sem backlog &mdash; liquida&ccedil;&atilde;o em dia.</td></tr>')
        + "</tbody></table></div>"
        + '<div class="op-muted op-note">Resultado dispon&iacute;vel em ' + esc(L.results_rows || 0)
        + " jogos &middot; atualizado " + esc(L.generated_at) + ".</div></div>";
    }

    function kpiMini(lab, val) {
      return '<div class="op-kpi"><div class="op-kpi-lab">' + esc(lab) + '</div>'
        + '<div class="op-kpi-val op-kpi-val-sm">' + esc(val == null ? "—" : String(val)) + "</div></div>";
    }

    // --- meta rodapé ---
    var foot = '<div class="meta">ops · ' + esc(O.gerado || "")
      + (B.fonte ? " · board fonte " + esc(B.fonte) : "")
      + (fx.file ? " · fixtures " + esc(fx.file) : "")
      + "</div>";

    foot = settleHtml + foot;
    root.innerHTML = head + kpiHtml + avHtml + casaTbl + heatHtml + mercHtml + soonHtml + histHtml + foot;
  };

  window.setInterval(function () {
    var view = document.getElementById("view-ops");
    if (!view || view.hidden) return;
    window.renderOps();
  }, 60000);

})();
