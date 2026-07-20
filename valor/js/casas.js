/* casas.js — identidade visual das casas de apostas (logo + cor de marca).
   Carrega ANTES das views; toda a UI mostra a logo no lugar do nome em texto
   (sempre com alt/title = nome da casa, pra acessibilidade e tooltip). */
(function () {
  "use strict";
  var CASAS = {
    betano:     { nome: "Betano",     logo: "logos/betano.svg",     cor: "#ff6b00" },
    superbet:   { nome: "Superbet",   logo: "logos/superbet.svg",   cor: "#df1119" },
    estrelabet: { nome: "EstrelaBet", logo: "logos/estrelabet.svg", cor: "#e3021b" },
    "7k":       { nome: "7k",         logo: "logos/7k.svg",         cor: "#12d16d" },
    pinnacle:   { nome: "Pinnacle",   logo: "logos/pinnacle.svg",   cor: "#ff5c00" }
  };

  function info(casa) {
    var k = String(casa == null ? "" : casa).toLowerCase().trim();
    return CASAS[k] || null;
  }

  function escAttr(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  window.RDU_CASAS = CASAS;
  /** Cor da marca (linhas de gráfico, legendas). */
  window.casaCor = function (casa) {
    var i = info(casa);
    return i ? i.cor : "#6b7280";
  };
  /** Nome de exibição canônico. */
  window.casaNome = function (casa) {
    var i = info(casa);
    return i ? i.nome : String(casa || "?");
  };
  /** <img> da logo com alt/title; casa desconhecida cai num badge de texto.
      cls extra: "house-logo-sm" (menor) / "house-logo-lg" (maior). */
  window.casaLogo = function (casa, cls) {
    var i = info(casa);
    var nome = escAttr(i ? i.nome : String(casa || "?"));
    if (!i) return '<span class="house-badge" title="' + nome + '">' + nome + "</span>";
    return '<img class="house-logo' + (cls ? " " + cls : "") + '" src="' + i.logo +
      '" alt="' + nome + '" title="' + nome + '">';
  };
})();
