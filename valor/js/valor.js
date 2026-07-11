/* valor.js — view "Valor (+EV)": agrega TODAS as linhas com valor de todos os jogos do board
   (multi-casa, já calculado pelo build_board), ranqueadas por EV%. Sem stake. */
(function () {
  "use strict";
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function evCls(ev) { return ev >= 10 ? "ev-hi" : ev >= 7 ? "ev-md" : "ev-lo"; }

  window.renderValor = function () {
    var root = document.getElementById("view-valor");
    if (!root) return;
    var B = window.BOARD || {};
    var bets = [];
    (B.jogos || []).forEach(function (j) {
      (j.valor || []).forEach(function (v) {
        bets.push({ jogo: j.jogo, liga: j.liga, inicio: j.inicio, mercado: v.mercado, linha: v.linha,
          lado: v.lado, casa: v.casa, odd: v.odd, nossa_prob: v.nossa_prob, edge_pp: v.edge_pp,
          ev_pct: v.ev_pct, mu: v.mu });
      });
    });
    bets.sort(function (a, b) { return b.ev_pct - a.ev_pct; });

    var head = '<div class="sub">Todas as linhas com <b style="color:var(--green)">valor (+EV)</b> pelos nossos modelos, de todas as casas capturadas, <b>ranqueadas por EV%</b>. A decisão de <b>quanto</b> apostar é sua — aqui só apontamos onde a odd está acima do justo.</div>'
      + '<div class="disc"><b>EV%</b> = retorno esperado por real apostado, se a nossa probabilidade estiver certa. <b>Odd justa</b> = 100 ÷ nossa probabilidade. Odds capturadas num instante — <b>podem ter movido</b>. Cobrimos Cartões/Faltas/Finalizações (7 ligas) + Escanteios (11 ligas).</div>';

    if (!bets.length) {
      root.innerHTML = head + '<div class="empty"><div class="big">🎯</div>Nenhuma aposta de valor no momento.<br>'
        + '<span style="font-size:12px;color:var(--faint)">Aparecem quando há jogo das ligas cobertas (Brasileirão A/B, big-5 europeias, China/Bolívia/Equador/Noruega) com mercado aberto nas casas.</span></div>';
      return;
    }

    var rows = bets.map(function (b, i) {
      var fair = b.nossa_prob > 0 ? (100 / b.nossa_prob) : 0;
      return '<div class="vbet">'
        + '<div class="vb-rank">' + (i + 1) + '</div>'
        + '<div class="vb-main">'
        + '<div class="vb-top"><span class="vb-ev ' + evCls(b.ev_pct) + '">+' + b.ev_pct.toFixed(1) + '%</span>'
        + '<span class="vb-pick">' + esc(b.mercado) + ' <b>' + esc(b.lado) + ' ' + b.linha + '</b></span></div>'
        + '<div class="vb-game">' + esc(b.jogo) + '</div>'
        + '<div class="vb-meta">' + esc(b.inicio) + (b.liga ? ' · ' + esc(b.liga) : '') + ' · μ ' + b.mu + '</div>'
        + '</div>'
        + '<div class="vb-num">'
        + '<div class="vb-odd"><span class="vb-house">' + esc(b.casa) + '</span> @ <b>' + b.odd.toFixed(2) + '</b></div>'
        + '<div class="vb-prob">nossa <b>' + b.nossa_prob.toFixed(0) + '%</b> · justa ' + fair.toFixed(2) + '</div>'
        + '</div>'
        + '</div>';
    }).join("");

    var meta = '<div class="meta">' + bets.length + ' linha' + (bets.length > 1 ? 's' : '') + ' com valor · atualizado ' + esc(B.gerado || "") + '</div>';
    root.innerHTML = head + meta + '<div class="vbets">' + rows + '</div>';
  };
})();
