/* lines.js — view "Linhas": como a LINHA andou entre a abertura e o jogo.
   Complementa o explorador de Histórico & CLV, que mostra a ODD de uma linha fixa.
   Aqui a série é da linha PRINCIPAL do mercado (a mais equilibrada), então
   responde "abriu 25,5 na quarta e fechou 23,5 no sábado". Dados: data/lines.js. */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var LOGO = window.casaLogo || function (c) { return esc(c); };

  function num(x) {
    if (x == null || x !== x) return "—";
    return String(x).replace(".", ",");
  }
  function odd(x) {
    return x == null ? "—" : Number(x).toFixed(2).replace(".", ",");
  }
  function quando(min) {
    var d = new Date(min * 60000);
    var dia = String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0");
    var hh = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    return dia + " " + hh;
  }
  function koTxt(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(/([+-]\d{2})(\d{2})$/, "$1:$2"));
    if (isNaN(d)) return String(iso).slice(0, 10);
    return String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0")
      + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  var estado = { busca: "", mercado: "", aberto: {}, ordem: "mov" };

  // nº total de mudanças de linha do jogo (todas as casas/mercados) — é o que
  // define "jogo interessante" na ordenação padrão
  function movJogo(mercs) {
    var t = 0;
    Object.keys(mercs).forEach(function (m) {
      Object.keys(mercs[m]).forEach(function (c) {
        var s = mercs[m][c];
        for (var i = 1; i < s.length; i++) {
          if (Math.abs(s[i][1] - s[i - 1][1]) >= 0.01) t++;
        }
      });
    });
    return t;
  }

  // jogo já começou? define se o último ponto é FECHAMENTO ou leitura corrente —
  // dizer "fechou 10,5" num jogo que só acontece daqui a duas semanas é mentira
  function jaComecou(ko) {
    if (!ko) return true;
    var d = new Date(String(ko).replace(/([+-]\d{2})(\d{2})$/, "$1:$2"));
    return isNaN(d) ? true : d.getTime() <= Date.now();
  }

  // resumo de uma série: abertura, fechamento e nº de mudanças
  function resumo(serie) {
    var ab = serie[0], fe = serie[serie.length - 1];
    var mud = 0;
    for (var i = 1; i < serie.length; i++) {
      if (Math.abs(serie[i][1] - serie[i - 1][1]) >= 0.01) mud++;
    }
    return { ab: ab, fe: fe, mud: mud, delta: fe[1] - ab[1] };
  }

  function setaDelta(d) {
    if (Math.abs(d) < 0.01) return '<span class="ln-flat">sem mudança</span>';
    var cls = d > 0 ? "ln-up" : "ln-down";
    var sig = d > 0 ? "+" : "";
    return '<span class="' + cls + '">' + (d > 0 ? "▲" : "▼") + " " + sig + num(d.toFixed(1)) + "</span>";
  }

  window.renderLines = function () {
    var root = document.getElementById("view-lines");
    if (!root) return;
    var L = window.LINES;
    if (!L || !L.s) {
      root.innerHTML = '<div class="empty"><div class="big">📉</div>Histórico de linhas ainda não gerado.<br>'
        + '<span style="font-size:12px;color:var(--faint)">Rode <code>python build_lines.py</code>.</span></div>';
      return;
    }

    // mercados presentes, pra barra de filtro
    var mercados = {};
    Object.keys(L.s).forEach(function (g) {
      Object.keys(L.s[g]).forEach(function (m) { mercados[m] = (mercados[m] || 0) + 1; });
    });
    var mkList = Object.keys(mercados).sort(function (a, b) { return mercados[b] - mercados[a]; });

    // jogos que passam no filtro, mais recentes primeiro
    var termo = estado.busca.toLowerCase().trim();
    var jogos = Object.keys(L.s).filter(function (g) {
      var info = L.games[g] || {};
      if (estado.mercado && !L.s[g][estado.mercado]) return false;
      if (!termo) return true;
      return ((info.h || "") + " " + (info.a || "") + " " + (info.lg || "")).toLowerCase().indexOf(termo) >= 0;
    }).sort(function (a, b) {
      if (estado.ordem === "mov") {
        var d = movJogo(L.s[b]) - movJogo(L.s[a]);
        if (d) return d;
      }
      return String((L.games[b] || {}).ko || "").localeCompare(String((L.games[a] || {}).ko || ""));
    });

    var LIM = 60;
    var mostrados = jogos.slice(0, LIM);

    var h = [];
    h.push('<div class="bar" id="ln-mkts">');
    h.push('<span class="chip' + (estado.mercado ? "" : " on") + '" data-mk="">Todos</span>');
    mkList.forEach(function (m) {
      h.push('<span class="chip' + (estado.mercado === m ? " on" : "") + '" data-mk="' + esc(m) + '">'
        + esc(m) + "</span>");
    });
    h.push("</div>");
    h.push('<div class="bar" id="ln-ord">');
    h.push('<span class="chip ord' + (estado.ordem === "mov" ? " on" : "") + '" data-ord="mov">Mais movimento</span>');
    h.push('<span class="chip ord' + (estado.ordem === "ko" ? " on" : "") + '" data-ord="ko">Mais recente</span>');
    h.push("</div>");
    h.push('<input id="ln-busca" class="ln-busca" type="search" placeholder="Filtrar por time ou liga…" '
      + 'value="' + esc(estado.busca) + '" aria-label="Filtrar jogos">');
    h.push('<div class="meta">' + jogos.length.toLocaleString("pt-BR") + " jogo(s) com histórico de linha"
      + (jogos.length > LIM ? " · mostrando " + LIM + " (" + (estado.ordem === "mov"
        ? "os que mais moveram" : "os mais recentes") + ")" : "")
      + (L.built ? " · gerado " + esc(String(L.built).slice(0, 16).replace("T", " ")) : "") + "</div>");

    if (!mostrados.length) {
      h.push('<div class="empty"><div class="big">🔍</div>Nenhum jogo com esse filtro.</div>');
      root.innerHTML = h.join("");
      liga(root);
      return;
    }

    mostrados.forEach(function (g) {
      var info = L.games[g] || {};
      var mercs = L.s[g];
      var chaves = Object.keys(mercs).filter(function (m) {
        return !estado.mercado || m === estado.mercado;
      }).sort();
      var aberto = !!estado.aberto[g];
      var fechado = jaComecou(info.ko);

      h.push('<div class="game">');
      h.push('<div class="g-top"><div><div class="g-teams">' + esc(info.h) + " × " + esc(info.a) + "</div>");
      if (info.lg) h.push('<div class="g-liga">' + esc(info.lg) + "</div>");
      h.push('</div><div class="g-when">' + esc(koTxt(info.ko)) + "</div></div>");

      // resumo por mercado (fechado)
      h.push('<div class="ln-sum">');
      chaves.forEach(function (m) {
        // pega a casa com a série mais longa como representativa do resumo
        var casas = Object.keys(mercs[m]);
        var melhor = casas[0], n = 0;
        casas.forEach(function (c) {
          if (mercs[m][c].length > n) { n = mercs[m][c].length; melhor = c; }
        });
        var r = resumo(mercs[m][melhor]);
        h.push('<div class="ln-sum-row"><span class="ln-mk">' + esc(m) + "</span>"
          + '<span class="ln-vals"><b>' + num(r.ab[1]) + "</b> → <b>" + num(r.fe[1]) + "</b></span>"
          + '<span class="ln-cap">' + (fechado ? "fechou" : "agora") + "</span>"
          + setaDelta(r.delta) + "</div>");
      });
      h.push("</div>");

      h.push('<button class="alt-btn ln-toggle" data-g="' + esc(g) + '" aria-expanded="' + aberto + '">'
        + '<span class="alt-arw">' + (aberto ? "▾" : "▸") + "</span>"
        + (aberto ? "Ocultar" : "Ver") + " o caminho da linha casa por casa</button>");

      if (aberto) {
        h.push('<div class="alt-box">');
        chaves.forEach(function (m) {
          h.push('<div class="alt-note">' + esc(m) + "</div>");
          h.push('<table class="lad ln-tab"><thead><tr><th>Casa</th><th>Caminho da linha</th>'
            + "<th>Mudanças</th></tr></thead><tbody>");
          Object.keys(mercs[m]).sort().forEach(function (c) {
            var s = mercs[m][c], r = resumo(s);
            var passos = [];
            var ant = null;
            s.forEach(function (p) {
              if (ant !== null && Math.abs(p[1] - ant) < 0.01) return;
              ant = p[1];
              passos.push('<span class="ln-step"><b>' + num(p[1]) + "</b>"
                + '<span class="ln-t">' + esc(quando(p[0])) + "</span>"
                + '<span class="ln-o">' + odd(p[2]) + " / " + odd(p[3]) + "</span></span>");
            });
            h.push("<tr><td>" + LOGO(c) + "</td><td class=\"ln-path\">"
              + passos.join('<span class="ln-arr">→</span>') + "</td><td>"
              + (r.mud || "—") + "</td></tr>");
          });
          h.push("</tbody></table>");
        });
        h.push("</div>");
      }
      h.push("</div>");
    });

    root.innerHTML = h.join("");
    liga(root);
  };

  function liga(root) {
    var ord = root.querySelector("#ln-ord");
    if (ord) {
      ord.querySelectorAll(".chip").forEach(function (c) {
        c.onclick = function () {
          estado.ordem = c.getAttribute("data-ord");
          window.renderLines();
        };
      });
    }
    var mk = root.querySelector("#ln-mkts");
    if (mk) {
      mk.querySelectorAll(".chip").forEach(function (c) {
        c.onclick = function () {
          estado.mercado = c.getAttribute("data-mk") || "";
          window.renderLines();
        };
      });
    }
    var b = root.querySelector("#ln-busca");
    if (b) {
      var t = null;
      b.oninput = function () {
        clearTimeout(t);
        t = setTimeout(function () {
          estado.busca = b.value;
          window.renderLines();
          var nb = document.getElementById("ln-busca");
          if (nb) { nb.focus(); nb.setSelectionRange(nb.value.length, nb.value.length); }
        }, 220);
      };
    }
    root.querySelectorAll(".ln-toggle").forEach(function (btn) {
      btn.onclick = function () {
        var g = btn.getAttribute("data-g");
        estado.aberto[g] = !estado.aberto[g];
        window.renderLines();
      };
    });
  }
})();
