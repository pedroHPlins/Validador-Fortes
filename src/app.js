/* Interface do Validador Fortes Fiscal (página local, sem servidor). */
(function () {
  "use strict";
  // especificações de cada versão do leiaute, comprimidas (gzip+base64) e abertas sob demanda
  const LEIAUTES = window.LEIAUTES_FORTES || {};
  const VERSOES = Object.keys(LEIAUTES).sort((a, b) => b - a);
  let SPEC = null;
  const specs = {};
  const V = window.ValidadorFortes;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n).toLocaleString("pt-BR");
  const POR_PAGINA = 200;
  const ROTULO_NIVEL = { erro: "Erro", aviso: "Aviso", info: "Info" };
  const ROTULO_STATUS = { regra: "validado", contexto: "validado c/ parâmetros", info: "descritivo", pendente: "não automatizado" };

  const estado = { arquivo: null, bytes: null, resultado: null, visao: "grupos", pagina: 0, filtrados: [], escolha: null };

  // ------------------------------------------------------------ util
  const armazenar = {
    ler(k, padrao) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : padrao; } catch (e) { return padrao; } },
    gravar(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* sem armazenamento */ } },
  };

  function campoDef(reg, n) {
    const r = SPEC.registros[reg];
    return r ? r.campos.find((c) => c.n === n) : null;
  }

  // ------------------------------------------------------------ abas
  $$(".aba").forEach((b) => b.addEventListener("click", () => {
    $$(".aba").forEach((x) => { x.classList.toggle("ativa", x === b); x.setAttribute("aria-selected", x === b); });
    $$(".painel-aba").forEach((p) => (p.hidden = p.id !== "aba-" + b.dataset.aba));
    if (b.dataset.aba === "leiaute" && !$("#listaRegistros").children.length) montaLeiaute();
  }));

  // ------------------------------------------------------------ versões do leiaute
  async function abreSpec(v) {
    if (specs[v]) return specs[v];
    if (typeof DecompressionStream === "undefined") throw new Error("Este navegador não abre as especificações comprimidas. Use Chrome, Edge ou Firefox atualizados.");
    const bin = Uint8Array.from(atob(LEIAUTES[v]), (c) => c.charCodeAt(0));
    const txt = await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
    return (specs[v] = JSON.parse(txt));
  }

  async function usaVersao(v) {
    if (SPEC && SPEC.versao === v) return;
    SPEC = await abreSpec(v);
    $("#versaoLeiaute").textContent = SPEC.versao;
    montaContexto();
    // a aba Leiaute é remontada para a versão nova
    $("#listaRegistros").innerHTML = "";
    $("#cobertura").innerHTML = "";
    const pend = $("#pendencias");
    if (pend) pend.parentNode.removeChild(pend);
    $("#detalheRegistro").innerHTML = `<p class="vazio">Escolha um registro à esquerda para ver os campos, as observações do leiaute e como cada uma é validada.</p>`;
    if (!$("#aba-leiaute").hidden) montaLeiaute();
  }

  /** Versão do CAB (campo 2), lida direto dos bytes do arquivo. */
  function versaoDoArquivo(bytes) {
    let ini = bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF ? 3 : 0;
    let fim = bytes.indexOf(10, ini);
    if (fim < 0 || fim - ini > 400) fim = Math.min(bytes.length, ini + 400);
    const cab = String.fromCharCode.apply(null, bytes.subarray(ini, fim)).split("|");
    const v = (cab[1] || "").trim();
    return cab[0] === "CAB" && /^\d+$/.test(v) ? v : null;
  }

  /** Escolhe a versão: a do arquivo, se existir; senão a mais próxima abaixo dela; senão a mais antiga. */
  function escolheVersao(vArq) {
    const sel = $("#selVersao").value;
    if (sel !== "auto") return { versao: sel, modo: "manual" };
    if (!vArq) return { versao: VERSOES[0], modo: "semVersao" };
    if (LEIAUTES[vArq]) return { versao: vArq, modo: "exata" };
    const abaixo = VERSOES.find((v) => +v <= +vArq);
    return { versao: abaixo || VERSOES[VERSOES.length - 1], modo: "proxima" };
  }

  (function montaSeletor() {
    const sel = $("#selVersao");
    sel.innerHTML = `<option value="auto">Automático (versão do arquivo)</option>` + VERSOES.map((v) => `<option value="${v}">${v}${(window.LEIAUTES_INFO || {})[v] ? " (lido por OCR)" : ""}</option>`).join("");
    const salvo = armazenar.ler("fortes.versao", "auto");
    sel.value = salvo === "auto" || LEIAUTES[salvo] ? salvo : "auto";
    sel.addEventListener("change", async () => {
      armazenar.gravar("fortes.versao", sel.value);
      if (estado.bytes) validar();
      else await usaVersao(sel.value === "auto" ? VERSOES[0] : sel.value);
    });
  })();

  // ------------------------------------------------------------ contexto da empresa
  const UFS = "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split(" ");
  function montaContexto() {
    const salvo = armazenar.ler("fortes.contexto", {});
    const box = $("#camposContexto");
    box.innerHTML = "";
    for (const [k, d] of Object.entries(SPEC.contexto || {})) {
      let ctrl;
      if (d.tipo === "uf") ctrl = `<select data-ctx="${k}"><option value="">Não informado</option>${UFS.map((u) => `<option>${u}</option>`).join("")}</select>`;
      else if (d.tipo === "lista") ctrl = `<select data-ctx="${k}"><option value="">Não informado</option>${Object.entries(d.valores).map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select>`;
      else if (d.tipo === "SN") ctrl = `<select data-ctx="${k}"><option value="">Não informado</option><option value="S">Sim</option><option value="N">Não</option></select>`;
      else ctrl = `<input data-ctx="${k}" type="text">`;
      box.insertAdjacentHTML("beforeend", `<label>${esc(d.rotulo)}${ctrl}</label>`);
    }
    $$("[data-ctx]", box).forEach((el) => {
      if (salvo[el.dataset.ctx]) el.value = salvo[el.dataset.ctx];
      el.addEventListener("change", () => {
        armazenar.gravar("fortes.contexto", lerContexto());
        if (estado.bytes) validar();
      });
    });
    if (Object.values(salvo).some(Boolean)) $("#contexto").open = true;
  }
  function lerContexto() {
    const c = {};
    $$("[data-ctx]").forEach((el) => { if (el.value) c[el.dataset.ctx] = el.value; });
    return c;
  }

  // ------------------------------------------------------------ entrada do arquivo
  const zona = $("#zona");
  $("#arquivo").addEventListener("change", (e) => e.target.files[0] && carregaArquivo(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.add("arrastando"); }));
  ["dragleave", "drop"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.remove("arrastando"); }));
  zona.addEventListener("drop", (e) => e.dataTransfer.files[0] && carregaArquivo(e.dataTransfer.files[0]));
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) carregaArquivo(e.dataTransfer.files[0]); });

  function carregaArquivo(f) {
    estado.arquivo = f;
    const leitor = new FileReader();
    leitor.onload = () => { estado.bytes = new Uint8Array(leitor.result); validar(); };
    leitor.readAsArrayBuffer(f);
  }

  async function validar() {
    $("#processando").hidden = false;
    $("#resultado").hidden = true;
    const vArq = versaoDoArquivo(estado.bytes);
    estado.escolha = Object.assign({ arquivo: vArq }, escolheVersao(vArq));
    try {
      await usaVersao(estado.escolha.versao);
    } catch (e) {
      $("#processando").textContent = e.message;
      return;
    }
    setTimeout(() => {
      const t0 = performance.now();
      estado.resultado = V.validar(estado.bytes, SPEC, { contexto: lerContexto() });
      estado.ms = Math.round(performance.now() - t0);
      $("#processando").hidden = true;
      zona.classList.add("compacta");
      zona.querySelector("strong").textContent = "Validar outro arquivo";
      montaResultado();
    }, 30);
  }

  // ------------------------------------------------------------ resultado
  function montaResultado() {
    const r = estado.resultado;
    $("#resultado").hidden = false;
    const versaoArq = estado.escolha.arquivo || (r.cabecalho ? r.cabecalho[1] : "?");
    const motivo = { manual: "escolhido manualmente", proxima: "versão mais próxima disponível", semVersao: "CAB sem versão; usada a mais recente" }[estado.escolha.modo];
    const selVersao = versaoArq === SPEC.versao
      ? `<span class="selo ok">leiaute ${esc(versaoArq)}</span>`
      : `<span class="selo aviso">arquivo no leiaute ${esc(versaoArq)} · validando pelo ${SPEC.versao}${motivo ? " (" + motivo + ")" : ""}</span>`;
    const cab = r.cabecalho || [];
    $("#arquivoInfo").innerHTML = [
      `<b>${esc(estado.arquivo.name)}</b>`,
      selVersao,
      cab[2] ? `Sistema: ${esc(cab[2])}` : "",
      cab[4] ? `Empresa: ${esc(cab[4])}` : "",
      cab[5] && cab[6] ? `Período: ${dataBr(cab[5])} a ${dataBr(cab[6])}` : "",
      `${esc(r.codificacao)}`,
      `${fmt(estado.ms)} ms`,
      r.estatisticas.regrasIndeterminadas ? `<span title="Regras que dependem dos parâmetros da empresa">${fmt(r.estatisticas.regrasIndeterminadas)} verificações aguardando parâmetros</span>` : "",
      r.truncado ? `<span class="selo erro">lista limitada às primeiras ${fmt(r.ocorrencias.length)} ocorrências</span>` : "",
    ].filter(Boolean).map((x) => `<span>${x}</span>`).join("");
    $("#qErros").textContent = fmt(r.resumo.erros);
    $("#qAvisos").textContent = fmt(r.resumo.avisos);
    $("#qInfos").textContent = fmt(r.resumo.infos);
    $("#qLinhas").textContent = fmt(r.estatisticas.linhas);

    const regs = Object.entries(r.estatisticas.porRegistro).sort((a, b) => a[0].localeCompare(b[0]));
    $("#fRegistro").innerHTML = `<option value="">Todos os registros</option>` +
      regs.map(([k, v]) => `<option value="${esc(k)}">${esc(k)} — ${esc((SPEC.registros[k] || {}).nome || "desconhecido")} (${fmt(v)})</option>`).join("");
    estado.pagina = 0;
    aplicaFiltros();
  }

  function dataBr(d) { return /^\d{8}$/.test(d) ? `${d.slice(6)}/${d.slice(4, 6)}/${d.slice(0, 4)}` : d; }

  $("#fNivel").addEventListener("change", () => { estado.pagina = 0; aplicaFiltros(); });
  $("#fRegistro").addEventListener("change", () => { estado.pagina = 0; aplicaFiltros(); });
  let tBusca;
  $("#fBusca").addEventListener("input", () => { clearTimeout(tBusca); tBusca = setTimeout(() => { estado.pagina = 0; aplicaFiltros(); }, 200); });
  $$(".cartao[data-nivel]").forEach((c) => c.addEventListener("click", () => {
    const sel = $("#fNivel");
    sel.value = sel.value === c.dataset.nivel ? "" : c.dataset.nivel;
    estado.pagina = 0;
    aplicaFiltros();
  }));
  $$(".seg-b").forEach((b) => b.addEventListener("click", () => {
    estado.visao = b.dataset.visao;
    $$(".seg-b").forEach((x) => x.classList.toggle("ativo", x === b));
    estado.pagina = 0;
    renderLista();
  }));

  function aplicaFiltros() {
    const nivel = $("#fNivel").value, reg = $("#fRegistro").value, q = $("#fBusca").value.trim().toLowerCase();
    $$(".cartao[data-nivel]").forEach((c) => c.classList.toggle("selecionado", c.dataset.nivel === nivel));
    estado.filtrados = estado.resultado.ocorrencias.filter((o) => {
      if (nivel && o.nivel !== nivel) return false;
      if (reg && o.registro !== reg) return false;
      if (q) {
        const alvo = `${o.linha} ${o.registro || ""}.${o.campo || ""} ${o.nomeCampo || ""} ${o.mensagem} ${o.valor || ""} ${o.codigo}`.toLowerCase();
        if (!alvo.includes(q)) return false;
      }
      return true;
    });
    renderLista();
  }

  function local(o) {
    if (!o.registro) return "Arquivo";
    return o.campo ? `${o.registro}.${o.campo} · ${o.nomeCampo || ""}` : `Registro ${o.registro}`;
  }

  function renderLista() {
    const lista = $("#lista"), pag = $("#paginacao");
    const ocs = estado.filtrados;
    pag.innerHTML = "";
    if (!estado.resultado.ocorrencias.length) {
      lista.innerHTML = `<div class="sucesso">✓ Nenhuma divergência encontrada em relação ao leiaute ${SPEC.versao}.</div>`;
      return;
    }
    if (!ocs.length) { lista.innerHTML = `<p class="vazio">Nenhuma ocorrência com esses filtros.</p>`; return; }

    if (estado.visao === "grupos") {
      const grupos = new Map();
      for (const o of ocs) {
        const k = `${o.nivel}|${o.codigo}|${o.registro || ""}|${o.campo || ""}|${o.regra || o.mensagem.replace(/"[^"]*"|\d+/g, "#")}`;
        let g = grupos.get(k);
        if (!g) grupos.set(k, (g = { k, itens: [], o }));
        g.itens.push(o);
      }
      const arr = Array.from(grupos.values()).sort((a, b) => ordemNivel(a.o.nivel) - ordemNivel(b.o.nivel) || b.itens.length - a.itens.length);
      const ini = estado.pagina * 50, fatia = arr.slice(ini, ini + 50);
      lista.innerHTML = fatia.map((g, i) => {
        const o = g.o;
        return `<details class="grupo" data-g="${ini + i}">
          <summary>
            <span class="qtd">${fmt(g.itens.length)}×</span>
            <span class="titulo-grupo">
              <span class="local">${esc(local(o))}</span>
              <span class="msg">${esc(o.mensagem)}</span>
              ${o.regra ? `<span class="regra-txt">Leiaute: “${esc(o.regra)}”</span>` : ""}
            </span>
            <span class="selo ${o.nivel}">${ROTULO_NIVEL[o.nivel]} · ${esc(o.codigo)}</span>
          </summary>
          <div class="ocorrencias"></div>
        </details>`;
      }).join("");
      $$(".grupo", lista).forEach((d) => d.addEventListener("toggle", () => {
        if (!d.open || d.dataset.ok) return;
        d.dataset.ok = 1;
        const g = arr[+d.dataset.g];
        const max = 1000;
        d.querySelector(".ocorrencias").innerHTML = `<table><thead><tr><th>Linha</th><th>Valor</th><th>Mensagem</th></tr></thead><tbody>${
          g.itens.slice(0, max).map((o, j) => `<tr data-j="${j}"><td>${o.linha || "—"}</td><td class="mono">${esc(o.valor || "")}</td><td>${esc(o.mensagem)}</td></tr>`).join("")
        }</tbody></table>${g.itens.length > max ? `<p class="vazio">Exibindo ${fmt(max)} de ${fmt(g.itens.length)}. Use a busca ou exporte o CSV.</p>` : ""}`;
        $$("tbody tr", d).forEach((tr) => tr.addEventListener("click", () => abreLinha(g.itens[+tr.dataset.j])));
      }));
      paginacao(arr.length, 50);
      return;
    }
    const ini = estado.pagina * POR_PAGINA, fatia = ocs.slice(ini, ini + POR_PAGINA);
    lista.innerHTML = fatia.map((o, i) => `<div class="item" data-i="${ini + i}">
        <span class="lin">${o.linha ? "Linha " + o.linha : "Arquivo"}</span>
        <span><span class="selo ${o.nivel}">${ROTULO_NIVEL[o.nivel]}</span></span>
        <span><b>${esc(local(o))}</b> — ${esc(o.mensagem)}${o.valor ? ` <span class="mono">“${esc(o.valor)}”</span>` : ""}</span>
      </div>`).join("");
    $$(".item", lista).forEach((el) => el.addEventListener("click", () => abreLinha(ocs[+el.dataset.i])));
    paginacao(ocs.length, POR_PAGINA);
  }

  function ordemNivel(n) { return n === "erro" ? 0 : n === "aviso" ? 1 : 2; }

  function paginacao(total, tam) {
    const pag = $("#paginacao");
    const paginas = Math.ceil(total / tam);
    if (paginas <= 1) { pag.innerHTML = ""; return; }
    pag.innerHTML = `<button class="botao" ${estado.pagina === 0 ? "disabled" : ""} data-p="-1">‹ Anterior</button>
      <span>Página ${estado.pagina + 1} de ${fmt(paginas)} · ${fmt(total)} ${estado.visao === "grupos" ? "grupos" : "ocorrências"}</span>
      <button class="botao" ${estado.pagina >= paginas - 1 ? "disabled" : ""} data-p="1">Próxima ›</button>`;
    $$("button", pag).forEach((b) => b.addEventListener("click", () => {
      estado.pagina += +b.dataset.p;
      renderLista();
      $("#lista").scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }

  // ------------------------------------------------------------ detalhe da linha
  function abreLinha(o) {
    const dlg = $("#dlgLinha");
    if (!o.linha) {
      $("#dlgTitulo").textContent = "Ocorrência no arquivo";
      $("#dlgCorpo").innerHTML = `<p>${esc(o.mensagem)}</p>`;
      dlg.showModal();
      return;
    }
    const texto = estado.resultado.linhas[o.linha - 1] || "";
    const campos = texto.split("|");
    const cod = campos[0];
    const def = SPEC.registros[cod];
    const daLinha = estado.resultado.ocorrencias.filter((x) => x.linha === o.linha);
    $("#dlgTitulo").textContent = `Linha ${o.linha} — ${cod}${def ? " · " + def.nome : ""}`;
    let html = `<div class="linha-bruta">${esc(texto)}</div>`;
    const geral = daLinha.filter((x) => !x.campo);
    if (geral.length) html += `<ul class="msgs">${geral.map((x) => `<li><span class="selo ${x.nivel}">${ROTULO_NIVEL[x.nivel]}</span> ${esc(x.mensagem)}</li>`).join("")}</ul>`;
    if (def) {
      html += `<label class="so-problemas"><input type="checkbox" id="soPreench" checked> Mostrar apenas campos preenchidos ou com ocorrência</label>`;
      html += `<div class="tabela-wrap"><table class="tabela tabela-campos"><thead><tr><th>Nº</th><th>Campo</th><th>Valor</th><th>Tipo/Tam.</th><th>Validação</th></tr></thead><tbody>`;
      const max = Math.max(def.campos.length, campos.length);
      for (let n = 1; n <= max; n++) {
        const c = def.campos.find((x) => x.n === n);
        const v = campos[n - 1] !== undefined ? campos[n - 1] : "";
        const msgs = daLinha.filter((x) => x.campo === n);
        const cls = msgs.some((x) => x.nivel === "erro") ? "tem-erro" : msgs.some((x) => x.nivel === "aviso") ? "tem-aviso" : "";
        const vis = v !== "" || msgs.length ? "" : "data-vazio";
        const obs = c ? c.obs.filter((x) => x.status !== "info" || x.nota).map((x) => `<li><span class="selo ${x.status}">${ROTULO_STATUS[x.status]}</span> ${esc(x.texto)}</li>`).join("") : "";
        html += `<tr class="${cls} ${o.campo === n ? "foco" : ""}" ${vis}>
          <td>${n}</td>
          <td>${c ? esc(c.nome) : "<i>campo além do leiaute</i>"}</td>
          <td class="mono">${v === "" ? "<span style='color:var(--texto-2)'>(vazio)</span>" : esc(v)}</td>
          <td>${c ? `${c.tipo}${c.tam ? "/" + c.tam : ""}${c.dec !== undefined ? " · " + c.dec + " dec." : ""}${c.obrigatorio ? " · obrig." : ""}` : ""}</td>
          <td>${msgs.length ? `<ul class="msgs">${msgs.map((x) => `<li class="${x.nivel}">${esc(x.mensagem)}${x.regra ? ` <span class="regra-txt">— “${esc(x.regra)}”</span>` : ""}</li>`).join("")}</ul>` : ""}
              ${obs ? `<details><summary style="cursor:pointer;color:var(--texto-2);font-size:12px">Observações do leiaute</summary><ul class="obs-lista">${obs}</ul></details>` : ""}</td>
        </tr>`;
      }
      html += `</tbody></table></div>`;
    }
    $("#dlgCorpo").innerHTML = html;
    const chk = $("#soPreench");
    const filtra = () => $$("tr[data-vazio]", $("#dlgCorpo")).forEach((tr) => (tr.hidden = chk.checked));
    if (chk) { chk.addEventListener("change", filtra); filtra(); }
    dlg.showModal();
    const foco = $("tr.foco", dlg);
    if (foco) foco.scrollIntoView({ block: "center" });
  }
  $("#dlgLinha").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

  // ------------------------------------------------------------ exportação
  $("#exportar").addEventListener("click", () => {
    const ocs = estado.filtrados;
    const col = (s) => `"${String(s == null ? "" : s).replace(/"/g, '""')}"`;
    const linhas = [["Linha", "Nível", "Código", "Registro", "Campo", "Nome do campo", "Valor", "Mensagem", "Regra do leiaute"].map(col).join(";")];
    for (const o of ocs) linhas.push([o.linha || "", o.nivel, o.codigo, o.registro || "", o.campo || "", o.nomeCampo || "", o.valor || "", o.mensagem, o.regra || ""].map(col).join(";"));
    const blob = new Blob(["﻿" + linhas.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (estado.arquivo ? estado.arquivo.name.replace(/\.[^.]+$/, "") : "validacao") + "_ocorrencias.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // ------------------------------------------------------------ aba leiaute
  function montaLeiaute() {
    const e = SPEC.estatisticas;
    const total = Object.values(e).reduce((a, b) => a + b, 0);
    const pct = (n) => Math.round((n / total) * 100) + "%";
    const nCampos = Object.values(SPEC.registros).reduce((a, r) => a + r.campos.length, 0);
    const origem = SPEC.origem ? `<div class="c" style="grid-column:1/-1;flex-basis:100%"><span><span class="selo aviso">leiaute ${SPEC.versao}</span> ${esc(SPEC.origem)}. Textos e regras podem conter imprecisões de leitura.</span></div>` : "";
    $("#cobertura").innerHTML = origem + `
      <div class="c"><b>${fmt(Object.keys(SPEC.registros).length)}</b><span>registros · ${fmt(nCampos)} campos</span></div>
      <div class="c"><b>${fmt(e.regra)}</b><span><span class="selo regra">validado</span> ${pct(e.regra)} das observações</span></div>
      <div class="c"><b>${fmt(e.contexto)}</b><span><span class="selo contexto">c/ parâmetros</span> ${pct(e.contexto)}</span></div>
      <div class="c"><b>${fmt(e.info)}</b><span><span class="selo info">descritivo</span> ${pct(e.info)}</span></div>
      <div class="c"><b>${fmt(e.pendente)}</b><span><span class="selo pendente">não automatizado</span> ${pct(e.pendente)}</span></div>`;
    // observações ainda não automatizadas, agrupadas por texto
    const pend = new Map();
    for (const [cod, r] of Object.entries(SPEC.registros))
      for (const c of r.campos)
        for (const o of c.obs)
          if (o.status === "pendente") {
            const k = o.texto;
            if (!pend.has(k)) pend.set(k, []);
            pend.get(k).push(`${cod}.${c.n}`);
          }
    const arrPend = Array.from(pend.entries()).sort((a, b) => b[1].length - a[1].length);
    $("#cobertura").insertAdjacentHTML("afterend", `<details class="contexto" id="pendencias" style="margin-bottom:14px">
      <summary>Observações do leiaute ainda não verificadas automaticamente <span class="dica">(${fmt(arrPend.length)} textos distintos em ${fmt(e.pendente)} campos)</span></summary>
      <div class="tabela-wrap" style="max-height:420px;overflow:auto;margin-top:10px"></div></details>`);
    $("#pendencias").addEventListener("toggle", (ev) => {
      const box = ev.target.querySelector(".tabela-wrap");
      if (!ev.target.open || box.childElementCount) return;
      box.innerHTML = `<table class="tabela"><thead><tr><th>Qtde</th><th>Observação</th><th>Campos</th></tr></thead><tbody>${
        arrPend.map(([t, cs]) => `<tr><td>${cs.length}</td><td>${esc(t)}</td><td class="mono">${esc(cs.slice(0, 12).join(", "))}${cs.length > 12 ? " …" : ""}</td></tr>`).join("")
      }</tbody></table>`;
    });

    const ul = $("#listaRegistros");
    const ordem = Object.keys(SPEC.registros);
    const nivel = (cod) => { let n = 0, p = SPEC.registros[cod].pai; while (p) { n++; p = (SPEC.registros[p] || {}).pai; } return n; };
    ul.innerHTML = ordem.map((cod) => `<li><button data-cod="${cod}" style="padding-left:${8 + nivel(cod) * 12}px"><span class="cod">${cod}</span><span class="nm">${esc(SPEC.registros[cod].nome)}</span></button></li>`).join("");
    $$("button", ul).forEach((b) => b.addEventListener("click", () => mostraRegistro(b.dataset.cod)));
    $("#buscaReg").dispatchEvent(new Event("input"));
  }
  $("#buscaReg").addEventListener("input", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    $$("#listaRegistros li").forEach((li) => (li.hidden = q && !li.textContent.toLowerCase().includes(q)));
  });

  function mostraRegistro(cod) {
    $$("#listaRegistros button").forEach((b) => b.classList.toggle("ativo", b.dataset.cod === cod));
    const r = SPEC.registros[cod];
    const card = r.cardinalidade ? [r.cardinalidade.min ? "obrigatório" : "", r.cardinalidade.max === 1 ? "no máximo 1" : ""].filter(Boolean).join(", ") : "";
    let html = `<h2>${cod} — ${esc(r.nome)}</h2>
      <div class="meta">
        <span>Pai: ${r.pai ? `<b>${r.pai}</b>` : "raiz do arquivo"}</span>
        ${r.filhos.length ? `<span>Filhos: ${r.filhos.map((f) => `<b>${f}</b>`).join(", ")}</span>` : ""}
        <span>${r.campos.length} campos</span>
        ${card ? `<span>${card}</span>` : ""}
        ${r.pagina ? `<span>PDF pág. ${r.pagina}</span>` : ""}
      </div>`;
    if (r.obs.length) html += `<ul class="obs-reg">${r.obs.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>`;
    html += `<div class="tabela-wrap"><table class="tabela"><thead><tr><th>Nº</th><th>Campo</th><th>Tipo</th><th>Tam.</th><th>Observações do leiaute</th></tr></thead><tbody>`;
    for (const c of r.campos) {
      const extras = [];
      if (c.obrigatorio) extras.push(`<span class="selo regra">obrigatório</span>`);
      if (c.fixo) extras.push(`<span class="selo regra">fixo “${esc(c.fixo)}”</span>`);
      if (c.dominio) extras.push(`<span class="selo regra">valores: ${esc(c.dominio.join(", "))}</span>`);
      if (c.tabela) extras.push(`<span class="selo regra">Tabela ${c.tabela}</span>`);
      if (c.referencia) extras.push(`<span class="selo regra">existe em ${c.referencia.registro}.${c.referencia.campo}</span>`);
      if (c.dec !== undefined) extras.push(`<span class="selo regra">${c.dec} decimais</span>`);
      if (c.correcao) extras.push(`<span class="selo aviso" title="${esc(c.correcao)}">corrigido</span>`);
      html += `<tr><td>${c.n}</td><td><b>${esc(c.nome)}</b><div>${extras.join(" ")}</div></td><td>${c.tipo}</td><td>${c.tam || "—"}</td><td>${
        c.obs.map((o) => `<div class="obs-item"><span class="selo ${o.status}">${ROTULO_STATUS[o.status]}</span><span>${esc(o.texto)}${o.nota ? ` <i>(${esc(o.nota)})</i>` : ""}</span></div>`).join("")
      }</td></tr>`;
    }
    html += `</tbody></table></div>`;
    $("#detalheRegistro").innerHTML = html;
  }

  const inicial = $("#selVersao").value;
  usaVersao(inicial === "auto" ? VERSOES[0] : inicial);
})();
