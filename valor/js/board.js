// Mesa de Aberturas — render do board a partir de window.BOARD
(function () {
  var B = (window.BOARD || { jogos: [], mercados: [], casas: [], gerado: "?" });
  var jogos = B.jogos || [];
  var MERCADOS = B.mercados || ["Cartões", "Faltas", "Finalizações", "Chutes no gol", "Impedimentos", "Laterais", "Tiros de meta"];
  var state = { mercado: "todos", soValor: false };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function chip(label, active, cls, onclick) {
    var c = document.createElement("span");
    c.className = "chip" + (cls ? " " + cls : "") + (active ? " on" : "");
    c.textContent = label; c.onclick = onclick;
    return c;
  }

  function renderFiltros() {
    var box = document.getElementById("filtros"); box.innerHTML = "";
    box.appendChild(chip("Todos mercados", state.mercado === "todos", "", function () { state.mercado = "todos"; render(); }));
    MERCADOS.forEach(function (m) {
      if (!jogos.some(function (j) { return j.mercados[m]; })) return;
      box.appendChild(chip(m, state.mercado === m, "", function () { state.mercado = m; render(); }));
    });
    box.appendChild(chip("🎯 só com valor", state.soValor, "val", function () { state.soValor = !state.soValor; render(); }));
  }

  function passa(j) {
    if (state.soValor && !j.tem_valor) return false;
    if (state.mercado !== "todos" && !j.mercados[state.mercado]) return false;
    return true;
  }

  // chave de linha com valor -> {lado,ev} para taguear na escada
  function valMap(j) {
    var m = {};
    (j.valor || []).forEach(function (v) { m[v.mercado + "|" + v.linha + "|" + v.lado] = v; });
    return m;
  }

  function ladder(j, mercado, vm) {
    var perCasa = j.mercados[mercado];              // {casa:[{linha,over,under}]}
    var casas = Object.keys(perCasa);
    // uniao de linhas
    var linhas = {};
    casas.forEach(function (c) { perCasa[c].forEach(function (l) { linhas[l.linha] = 1; }); });
    var ord = Object.keys(linhas).map(Number).sort(function (a, b) { return a - b; });
    var head = "<tr><th>Linha</th>" + casas.map(function (c) { return "<th>" + esc(c) + " +</th><th>" + esc(c) + " −</th>"; }).join("") + "</tr>";
    var body = ord.map(function (L) {
      var vO = vm[mercado + "|" + L + "|Mais"], vU = vm[mercado + "|" + L + "|Menos"];
      var cells = casas.map(function (c) {
        var row = (perCasa[c] || []).filter(function (x) { return x.linha === L; })[0];
        var o = row ? row.over.toFixed(2) : "—", u = row ? row.under.toFixed(2) : "—";
        return '<td class="o">' + o + (vO && vO.casa === c ? '<span class="vtag">+' + vO.ev_pct.toFixed(0) + '%</span>' : "") + '</td>' +
               '<td class="u">' + u + (vU && vU.casa === c ? '<span class="vtag">+' + vU.ev_pct.toFixed(0) + '%</span>' : "") + '</td>';
      }).join("");
      var hasVal = vO || vU;
      return '<tr class="' + (hasVal ? "val-row" : "") + '"><td class="ln">' + L + "</td>" + cells + "</tr>";
    }).join("");
    return '<table class="lad"><thead>' + head + "</thead><tbody>" + body + "</tbody></table>";
  }

  function gameCard(j) {
    var vm = valMap(j);
    var el = document.createElement("div"); el.className = "game";
    var valStrip = "";
    if (j.tem_valor) {
      valStrip = '<div class="val-strip">' + j.valor.slice(0, 4).map(function (v) {
        return '<span class="val-item">' + esc(v.mercado) + " " + v.lado + " " + v.linha + " @ " + v.odd.toFixed(2) + ' <span class="ev">+' + v.ev_pct.toFixed(0) + "%</span></span>";
      }).join("") + "</div>";
    }
    var mkts = MERCADOS.filter(function (m) { return j.mercados[m]; }).map(function (m) {
      var perCasa = j.mercados[m];
      var allL = [];
      Object.keys(perCasa).forEach(function (c) { perCasa[c].forEach(function (l) { allL.push(l.linha); }); });
      var mn = Math.min.apply(null, allL), mx = Math.max.apply(null, allL);
      var nL = Object.keys(allL.reduce(function (a, x) { a[x] = 1; return a; }, {})).length;
      var hasVal = (j.valor || []).some(function (v) { return v.mercado === m; });
      return '<div class="mkt" data-mkt="' + esc(m) + '">' +
        '<div class="mkt-h"><span class="nm">' + esc(m) + (hasVal ? ' 🎯' : "") + '</span>' +
        '<span class="ct">' + nL + ' linha' + (nL === 1 ? "" : "s") + '</span>' +
        '<span class="rng">' + mn + "–" + mx + '</span><span class="arw">▸</span></div>' +
        '<div class="mkt-b">' + ladder(j, m, vm) + '</div></div>';
    }).join("");
    el.innerHTML =
      '<div class="g-top"><div><div class="g-name">' + esc(j.jogo) + '</div>' +
      '<div class="g-liga">' + esc(j.liga) + '</div>' +
      '<div class="houses">' + (j.casas || []).map(function (c) { return '<span class="house">' + esc(c) + "</span>"; }).join("") + '</div></div>' +
      '<div class="g-when">' + esc(j.inicio) + "</div></div>" +
      valStrip +
      '<div class="mkts">' + mkts + "</div>";
    // toggles
    el.querySelectorAll(".mkt-h").forEach(function (h) {
      h.onclick = function () { h.parentElement.classList.toggle("open"); };
    });
    return el;
  }

  function render() {
    renderFiltros();
    var vis = jogos.filter(passa);
    document.getElementById("meta").textContent =
      vis.length + " jogo" + (vis.length === 1 ? "" : "s") + " com mercado aberto · " +
      (B.casas || []).join(", ") + " · atualizado " + (B.gerado || "?") + " (BRT)";
    var lista = document.getElementById("lista"); lista.innerHTML = "";
    if (!vis.length) {
      lista.innerHTML = '<div class="empty"><div class="big">📭</div>Nenhum jogo com esses mercados abertos agora.<br><span style="font-size:12px">As casas abrem os mercados de estatística mais perto do jogo. Volte após a próxima captura.</span></div>';
      return;
    }
    vis.forEach(function (j) { lista.appendChild(gameCard(j)); });
  }

  render();
})();
