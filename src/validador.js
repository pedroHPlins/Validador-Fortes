/*
 * Motor de validação de arquivos .fs (Leiaute de Importação Fortes Fiscal).
 *
 * Funciona no navegador (window.ValidadorFortes) e no Node (module.exports).
 * A especificação (registros, campos, tabelas e regras) vem de spec/leiaute_XXX.json,
 * gerado por tools/gerar_regras.py a partir do PDF oficial.
 *
 * Uso:
 *   const r = ValidadorFortes.validar(textoDoArquivo, spec, { contexto: {...} });
 *   r.ocorrencias -> [{ linha, registro, campo, nomeCampo, nivel, codigo, mensagem, valor }]
 */
(function (raiz, fabrica) {
  if (typeof module === "object" && module.exports) module.exports = fabrica();
  else raiz.ValidadorFortes = fabrica();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const NIVEL = { ERRO: "erro", AVISO: "aviso", INFO: "info" };
  const LIMITE_OCORRENCIAS = 50000;

  // ------------------------------------------------------------ utilidades

  function dataValida(s) {
    if (!/^\d{8}$/.test(s)) return false;
    const a = +s.slice(0, 4), m = +s.slice(4, 6), d = +s.slice(6, 8);
    if (a < 1900 || a > 2199 || m < 1 || m > 12 || d < 1) return false;
    const dias = [31, (a % 4 === 0 && a % 100 !== 0) || a % 400 === 0 ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return d <= dias[m - 1];
  }

  function normalizaComparacao(v) {
    // compara números ignorando zeros à esquerda e casas decimais zeradas
    if (/^-?\d+(\.\d+)?$/.test(v)) return String(Number(v));
    return v.toUpperCase();
  }

  /** número de um campo V/N; vazio = 0; NaN quando não numérico */
  function num(v) {
    if (v === undefined || v === "") return 0;
    return /^\s*-?\d+(\.\d+)?\s*$/.test(v) ? Number(v) : NaN;
  }
  const fmtNum = (x) => (Math.round(x * 10000) / 10000).toFixed(2);

  /** valor de uma expressão {termos:[{campo,sinal}]} ou {produto:[a,b], divisor} sobre os campos */
  function calcula(r, campos) {
    if (r.produto) {
      const a = num(campos[r.produto[0] - 1]), b = num(campos[r.produto[1] - 1]);
      return a * b / (r.divisor || 1);
    }
    let t = 0;
    for (const x of r.termos) {
      const v = x.campo ? num(campos[x.campo - 1]) : Number(x.valor);
      t += (x.sinal || 1) * v;
    }
    return t;
  }

  function igual(a, b) {
    return normalizaComparacao(a) === normalizaComparacao(String(b));
  }

  // ------------------------------------------------------------ condições

  /**
   * Avalia uma condição de regra.
   *  { campo: 31, op: "=", valor: "N" }                       campo do próprio registro
   *  { registro: "NFM", campo: 2, op: "in", valores: [...] }  campo de um ancestral
   *  { ctx: "uf", op: "in", valores: ["CE"] }                 parâmetro da empresa (opcional)
   *  { e: [...] } | { ou: [...] } | { nao: {...} }
   * Retorna true/false, ou null quando não for possível avaliar (contexto ausente).
   */
  function avalia(cond, amb) {
    if (!cond) return true;
    if (cond.e) {
      let indef = false;
      for (const c of cond.e) {
        const r = avalia(c, amb);
        if (r === false) return false;
        if (r === null) indef = true;
      }
      return indef ? null : true;
    }
    if (cond.ou) {
      let indef = false;
      for (const c of cond.ou) {
        const r = avalia(c, amb);
        if (r === true) return true;
        if (r === null) indef = true;
      }
      return indef ? null : false;
    }
    if (cond.const !== undefined) return !!cond.const;
    if (cond.semCampo) return false;
    if (cond.nao) {
      const r = avalia(cond.nao, amb);
      return r === null ? null : !r;
    }
    let valor;
    if (cond.ctx) {
      const v = amb.contexto ? amb.contexto[cond.ctx] : undefined;
      if (v === undefined || v === null || v === "") return null;
      valor = String(v);
    } else if (cond.registro && cond.registro !== amb.registro) {
      const anc = amb.ancestral(cond.registro);
      if (!anc) return cond.op === "ausente" ? true : (cond.op === "vazio" ? true : false);
      if (cond.op === "existe") return true;
      valor = anc.campos[cond.campo - 1] || "";
    } else {
      valor = amb.campos[cond.campo - 1] || "";
    }
    const vals = cond.valores || (cond.valor !== undefined ? [cond.valor] : []);
    switch (cond.op) {
      case "=": case "in": return vals.some((x) => igual(valor, x));
      case "!=": case "notin": return valor !== "" && !vals.some((x) => igual(valor, x));
      case "preenchido": return valor !== "" && !(cond.ignorarZero && Number(valor) === 0);
      case "vazio": return valor === "" || (cond.ignorarZero && Number(valor) === 0);
      case ">": return valor !== "" && Number(valor) > Number(vals[0]);
      case ">=": return valor !== "" && valor >= String(vals[0]);
      case "<": return valor !== "" && valor < String(vals[0]);
      case "comeca": return vals.some((x) => valor.startsWith(String(x)));
      case "posicaoIn": return valor.length >= cond.pos && vals.some((x) => valor[cond.pos - 1].toUpperCase() === String(x));
      case "posicaoNotIn": return valor.length >= cond.pos && !vals.some((x) => valor[cond.pos - 1].toUpperCase() === String(x));
      default: return null;
    }
  }

  // ------------------------------------------------------------ validação

  function decodifica(bytes) {
    // Arquivos do Fortes normalmente são ANSI (Windows-1252); aceita UTF-8 se for válido.
    if (typeof bytes === "string") return { texto: bytes, codificacao: "texto" };
    try {
      const t = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      return { texto: t.replace(/^﻿/, ""), codificacao: "UTF-8" };
    } catch (e) {
      return { texto: new TextDecoder("windows-1252").decode(bytes), codificacao: "Windows-1252" };
    }
  }

  function validar(entrada, spec, opcoes) {
    opcoes = opcoes || {};
    const contexto = opcoes.contexto || {};
    const { texto, codificacao } = decodifica(entrada);
    const ocorrencias = [];
    const regs = spec.registros;
    const estat = { linhas: 0, porRegistro: {}, regrasIndeterminadas: 0 };

    function add(o) {
      if (ocorrencias.length < LIMITE_OCORRENCIAS) ocorrencias.push(o);
    }

    const linhas = texto.split(/\r\n|\n|\r/);
    if (linhas.length && linhas[linhas.length - 1] === "") linhas.pop();

    // pilha da hierarquia: [{ codigo, linha, campos, filhos: {COD: qtde} }]
    const pilha = [];
    const raizFilhos = {};
    const chaves = {}; // valores existentes para referências: "PAR.2" -> Set
    const referencias = []; // conferidas no final
    let cab = null;
    let tra = null;
    const somados = new Set(); // registros cujos valores são somados em campos do registro pai
    for (const cod in regs)
      for (const c of regs[cod].campos)
        for (const r of c.regras || []) if (r.tipo === "soma") r.termos.forEach((x) => somados.add(x.registro));

    function ancestral(cod) {
      for (let i = pilha.length - 1; i >= 0; i--) if (pilha[i].codigo === cod) return pilha[i];
      return null;
    }

    function fechaNo(no) {
      // registros-filho obrigatórios / cardinalidade
      const def = regs[no.codigo];
      if (!def) return;
      if (def.temSoma) validaSomas(no, def);
      if (!def.filhos) return;
      for (const f of def.filhos) {
        const fd = regs[f];
        if (!fd || !fd.cardinalidade) continue;
        const q = no.filhos[f] || 0;
        const card = fd.cardinalidade;
        if (card.min && q < card.min && avalia(card.quando, { campos: no.campos, registro: no.codigo, ancestral, contexto }) !== false) {
          add({ linha: no.linha, registro: no.codigo, nivel: card.minNivel || NIVEL.ERRO, codigo: "FILHO_OBRIGATORIO",
                mensagem: `Registro ${no.codigo} deve ter ao menos ${card.min} registro(s) ${f} (${fd.nome}).` });
        }
      }
    }

    function validaSomas(no, def) {
      for (const c of def.campos) {
        for (const r of c.regras || []) {
          if (r.tipo !== "soma") continue;
          // só confere quando o documento tem os registros somados (empresa pode não informar itens)
          if (!r.termos.some((x) => no.desc[x.registro] && no.desc[x.registro].length)) continue;
          const amb = { campos: no.campos, registro: no.codigo, ancestral, contexto };
          const ok = avalia(r.quando, amb);
          if (ok === null) { estat.regrasIndeterminadas++; continue; }
          if (!ok) continue;
          let total = 0, invalido = false;
          for (const x of r.termos) {
            for (const filho of no.desc[x.registro] || []) {
              if (x.filtro) {
                const f = avalia(x.filtro, { campos: filho, registro: x.registro, ancestral, contexto });
                if (!f) continue;
              }
              const v = num(filho[x.campo - 1]);
              if (isNaN(v)) invalido = true; else total += (x.sinal || 1) * v;
            }
          }
          const v = no.campos[c.n - 1] || "";
          const atual = num(v);
          if (invalido || isNaN(atual)) continue;
          if (Math.abs(atual - total) > (r.tolerancia || 0.011)) {
            add({ linha: no.linha, registro: no.codigo, campo: c.n, nomeCampo: c.nome, valor: v, nivel: r.nivel || NIVEL.ERRO,
                  codigo: "SOMA", regra: r.texto,
                  mensagem: `Valor ${fmtNum(atual)} difere da soma dos registros ${[...new Set(r.termos.map((x) => x.registro))].join("/")} (${fmtNum(total)}).` });
          }
        }
      }
    }

    for (let i = 0; i < linhas.length; i++) {
      const nl = i + 1;
      const linha = linhas[i];
      estat.linhas++;
      if (linha.trim() === "") {
        add({ linha: nl, nivel: NIVEL.ERRO, codigo: "LINHA_VAZIA", mensagem: "Linha em branco no meio do arquivo." });
        continue;
      }
      const campos = linha.split("|");
      const cod = campos[0];
      const def = regs[cod];
      estat.porRegistro[cod] = (estat.porRegistro[cod] || 0) + 1;
      if (!def) {
        add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "REGISTRO_DESCONHECIDO",
              mensagem: `Tipo de registro "${cod.slice(0, 20)}" não existe no leiaute ${spec.versao}.` });
        continue;
      }

      // ---------------- posição e hierarquia
      if (i === 0 && cod !== "CAB") {
        add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "CAB_PRIMEIRO", mensagem: "A primeira linha do arquivo deve ser o registro CAB." });
      }
      if (cod === "CAB") {
        if (cab) add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "CAB_DUPLICADO", mensagem: `Registro CAB repetido (o primeiro está na linha ${cab.linha}).` });
        else cab = { linha: nl, campos };
      }
      if (tra) {
        add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "APOS_TRA", mensagem: `Registro ${cod} após o Trailler (TRA, linha ${tra.linha}).` });
      }
      if (cod === "TRA") tra = { linha: nl, campos };

      const pai = def.pai;
      if (pai) {
        while (pilha.length && pilha[pilha.length - 1].codigo !== pai) fechaNo(pilha.pop());
        if (!pilha.length) {
          add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "HIERARQUIA",
                mensagem: `Registro ${cod} (${def.nome}) deve vir após um registro ${pai} (${(regs[pai] || {}).nome || ""}) e seus filhos.` });
        }
      } else {
        while (pilha.length) fechaNo(pilha.pop());
      }
      const noPai = pilha.length ? pilha[pilha.length - 1] : null;
      const contFilhos = noPai ? noPai.filhos : raizFilhos;
      contFilhos[cod] = (contFilhos[cod] || 0) + 1;
      const card = def.cardinalidade;
      if (card && card.max && contFilhos[cod] > card.max) {
        add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "CARDINALIDADE",
              mensagem: card.max === 1
                ? `Só pode haver um registro ${cod} ${noPai ? "por registro " + noPai.codigo + " (linha " + noPai.linha + ")" : "por arquivo"}.`
                : `Máximo de ${card.max} registros ${cod} ${noPai ? "por " + noPai.codigo : "por arquivo"}.` });
      }
      if (somados.has(cod)) for (const anc of pilha) (anc.desc[cod] = anc.desc[cod] || []).push(campos);
      const no = { codigo: cod, linha: nl, campos, filhos: {}, desc: {} };
      pilha.push(no);

      // ---------------- quantidade de campos
      const esperado = def.campos.length;
      if (campos.length > esperado) {
        const extras = campos.slice(esperado);
        if (extras.some((x) => x !== "")) {
          add({ linha: nl, registro: cod, nivel: NIVEL.ERRO, codigo: "CAMPOS_EXCEDENTES",
                mensagem: `Registro ${cod} tem ${campos.length} campos; o leiaute ${spec.versao} define ${esperado}.` });
        } else if (campos.length > esperado + 1) {
          add({ linha: nl, registro: cod, nivel: NIVEL.AVISO, codigo: "CAMPOS_EXCEDENTES",
                mensagem: `Registro ${cod} tem ${campos.length} campos (vazios no final); o leiaute ${spec.versao} define ${esperado}.` });
        }
      } else if (campos.length < esperado) {
        add({ linha: nl, registro: cod, nivel: NIVEL.INFO, codigo: "CAMPOS_FALTANTES",
              mensagem: `Registro ${cod} tem ${campos.length} campos; o leiaute ${spec.versao} define ${esperado}. Os ausentes serão considerados vazios.` });
      }

      // ---------------- campos
      const amb = { campos, registro: cod, ancestral, contexto };
      for (const c of def.campos) {
        const v = campos[c.n - 1] !== undefined ? campos[c.n - 1] : "";
        const base = { linha: nl, registro: cod, campo: c.n, nomeCampo: c.nome, valor: v };
        validaCampo(c, v, base, amb, add, estat, spec);
        if (c.chave && v !== "") {
          const k = cod + "." + c.n;
          (chaves[k] = chaves[k] || new Set()).add(normalizaComparacao(v));
        }
        if (c.referencia && v !== "") referencias.push({ ...base, ref: c.referencia });
      }
    }
    while (pilha.length) fechaNo(pilha.pop());

    // ---------------- registros obrigatórios no arquivo
    for (const cod in regs) {
      const d = regs[cod];
      if (!d.pai && d.cardinalidade && d.cardinalidade.min && !raizFilhos[cod]) {
        add({ linha: 0, registro: cod, nivel: NIVEL.ERRO, codigo: "REGISTRO_OBRIGATORIO",
              mensagem: `Registro ${cod} (${d.nome}) é obrigatório e não foi encontrado.` });
      }
    }

    // ---------------- referências entre registros (ex.: participante existe em PAR)
    for (const r of referencias) {
      const k = r.ref.registro + "." + r.ref.campo;
      const s = chaves[k];
      if (!s || !s.has(normalizaComparacao(r.valor))) {
        add({ ...r, nivel: r.ref.nivel || NIVEL.ERRO, codigo: "REFERENCIA",
              mensagem: `Valor "${r.valor}" não encontrado no campo ${r.ref.campo} de nenhum registro ${r.ref.registro} do arquivo.` });
      }
    }

    // ---------------- Trailler
    if (tra && regs.TRA) validaTrailler(tra, linhas.length, add);

    ocorrencias.sort((a, b) => a.linha - b.linha || (a.campo || 0) - (b.campo || 0));
    const resumo = { erros: 0, avisos: 0, infos: 0 };
    for (const o of ocorrencias) {
      if (o.nivel === NIVEL.ERRO) resumo.erros++;
      else if (o.nivel === NIVEL.AVISO) resumo.avisos++;
      else resumo.infos++;
    }
    return { versao: spec.versao, codificacao, estatisticas: estat, resumo, ocorrencias, linhas,
             truncado: ocorrencias.length >= LIMITE_OCORRENCIAS, cabecalho: cab ? cab.campos : null };
  }

  function validaTrailler(tra, totalLinhas, add) {
    const q = tra.campos[1];
    if (q !== undefined && q !== "" && /^\d+$/.test(q) && Number(q) !== totalLinhas) {
      add({ linha: tra.linha, registro: "TRA", campo: 2, nomeCampo: "Quantidade de Linhas", valor: q, nivel: NIVEL.ERRO,
            codigo: "TRA_QTDE", mensagem: `O Trailler informa ${q} linhas, mas o arquivo tem ${totalLinhas}.` });
    }
  }

  function validaCampo(c, v, base, amb, add, estat, spec) {
    const erro = (codigo, mensagem, nivel) => add({ ...base, nivel: nivel || NIVEL.ERRO, codigo, mensagem });

    // conteúdo fixo
    if (c.fixo !== undefined && v !== c.fixo) {
      if (base.registro === "CAB" && c.n === 2)
        erro("VERSAO", `Arquivo gerado no leiaute ${v || "(vazio)"}; este validador segue o leiaute ${c.fixo}. ` +
             "Campos incluídos ou alterados entre as versões aparecerão como divergências.");
      else erro("VALOR_FIXO", `Deve conter "${c.fixo}".`);
      return;
    }

    if (v === "") {
      if (c.obrigatorio) erro("OBRIGATORIO", "Campo obrigatório não preenchido." + (c.obrigatorioNota ? " " + c.obrigatorioNota : ""), c.obrigatorioNivel);
    } else {
      // tipo e tamanho
      switch (c.tipo) {
        case "N":
          if (/^\s+\d+\s*$|^\d+\s+$/.test(v)) erro("ESPACOS", "Campo numérico com espaços em branco antes/depois do número.", NIVEL.AVISO);
          else if (!/^\d+$/.test(v)) erro("TIPO_N", "Deve conter somente dígitos (campo numérico).");
          else if (c.tam && v.length > c.tam) erro("TAMANHO", `Tamanho ${v.length} excede o máximo de ${c.tam}.`);
          break;
        case "C":
          if (c.tam && v.length > c.tam) erro("TAMANHO", `Tamanho ${v.length} excede o máximo de ${c.tam}.`);
          break;
        case "D":
          if (!dataValida(v)) erro("TIPO_D", "Data inválida; use o formato AAAAMMDD.");
          break;
        case "L":
          if (v !== "S" && v !== "N") erro("TIPO_L", 'Campo lógico: preencher com "S" ou "N".');
          break;
        case "V": {
          if (/,/.test(v)) { erro("TIPO_V", "Use ponto como separador decimal (não vírgula) e sem separador de milhar."); break; }
          if (!/^-?\d+(\.\d+)?$/.test(v)) { erro("TIPO_V", "Valor numérico inválido (ex.: 12450.20)."); break; }
          if (v.startsWith("-") && !c.permiteNegativo) erro("TIPO_V", "Valor negativo não permitido.", NIVEL.AVISO);
          const dec = (v.split(".")[1] || "").length;
          const maxDec = c.dec !== undefined ? c.dec : 2;
          // casas decimais explícitas no leiaute geram erro; o padrão (2) é recomendação -> aviso
          if (dec > maxDec) erro("DECIMAIS", `Máximo de ${maxDec} casas decimais (informado ${dec}).`, c.dec !== undefined ? NIVEL.ERRO : NIVEL.AVISO);
          if (c.tam && v.length > c.tam) erro("TAMANHO", `Tamanho ${v.length} excede o máximo de ${c.tam}.`);
          break;
        }
        case "S":
          erro("DEVE_SER_VAZIO", "Campo reservado: não deve ser preenchido.");
          break;
        default:
          if (c.tam && c.tipo !== "T" && v.length > c.tam) erro("TAMANHO", `Tamanho ${v.length} excede o máximo de ${c.tam}.`);
      }
      // domínio de valores
      if (c.dominio && c.dominio.length && !c.dominio.some((x) => igual(v, x))) {
        const lista = c.dominio.length > 12 ? c.dominio.slice(0, 12).join(", ") + ", ..." : c.dominio.join(", ");
        erro("DOMINIO", `Valor "${v}" não permitido. Valores aceitos: ${lista}.`, c.dominioNivel);
      }
      if (c.tabela) {
        const t = spec.tabelas[c.tabela];
        if (t && t.valores && !Object.keys(t.valores).some((x) => igual(v, x))) {
          erro("TABELA", `Valor "${v}" não consta na Tabela ${c.tabela} (${t.titulo}).`, c.tabelaNivel);
        }
      }
    }

    // regras condicionais
    if (c.regras) {
      for (const r of c.regras) {
        const ok = avalia(r.quando, amb);
        if (ok === null) { estat.regrasIndeterminadas++; continue; }
        if (!ok) continue;
        const nivel = r.nivel || NIVEL.ERRO;
        switch (r.tipo) {
          case "obrigatorio":
            if (v === "") add({ ...base, nivel, codigo: "OBRIGATORIO_COND", mensagem: r.msg || "Campo obrigatório nesta condição." + (r.nota ? " " + r.nota : ""), regra: r.texto });
            break;
          case "vazio":
            // zero em campo de valor e "N" em campo lógico equivalem a "não informado"
            if (v !== "" && !(c.tipo === "V" && /^0*(\.0*)?$/.test(v)) && !(c.tipo === "L" && v === "N"))
              add({ ...base, nivel, codigo: "PREENCHIMENTO_INDEVIDO", mensagem: r.msg || "Campo não deve ser preenchido nesta condição.", regra: r.texto });
            break;
          case "valor": {
            const vals = r.valores || [r.valor];
            if (v !== "" && !vals.some((x) => igual(v, x)) || (v === "" && r.obrigatorio))
              add({ ...base, nivel, codigo: "VALOR_COND", mensagem: r.msg || `Nesta condição o valor deve ser ${vals.join(" ou ")}.`, regra: r.texto });
            break;
          }
          case "dominio": {
            if (v !== "" && !r.valores.some((x) => igual(v, x)))
              add({ ...base, nivel, codigo: "DOMINIO_COND", mensagem: r.msg || `Nesta condição os valores aceitos são: ${r.valores.join(", ")}.`, regra: r.texto });
            break;
          }
          case "proibido": {
            if (v !== "" && r.valores.some((x) => igual(v, x)))
              add({ ...base, nivel, codigo: "VALOR_NAO_PERMITIDO", mensagem: r.msg || `O valor "${v}" não é permitido nesta condição.`, regra: r.texto });
            break;
          }
          case "tamanho": {
            if (v === "") break;
            if (r.exato && v.length !== r.exato)
              add({ ...base, nivel, codigo: "TAMANHO_COND", mensagem: r.msg || `Nesta condição o campo deve ter ${r.exato} posições (informado ${v.length}).`, regra: r.texto });
            else if (r.max && v.length > r.max)
              add({ ...base, nivel, codigo: "TAMANHO_COND", mensagem: r.msg || `Nesta condição o tamanho máximo é ${r.max} (informado ${v.length}).`, regra: r.texto });
            break;
          }
          case "calculo": {
            if (v === "") break;
            const esperado = calcula(r, amb.campos), atual = num(v);
            if (isNaN(esperado) || isNaN(atual)) break;
            if (Math.abs(atual - esperado) > (r.tolerancia || 0.011))
              add({ ...base, nivel, codigo: "CALCULO", mensagem: r.msg || `Valor ${fmtNum(atual)} difere do calculado pelo leiaute (${fmtNum(esperado)}).`, regra: r.texto });
            break;
          }
          case "comparacao": {
            if (v === "") break;
            const atual = c.tipo === "D" ? Number(v) : num(v);
            const ref = r.termos ? calcula(r, amb.campos) : r.campo ? (c.tipo === "D" ? Number(amb.campos[r.campo - 1] || NaN) : num(amb.campos[r.campo - 1])) : Number(r.valor);
            if (isNaN(atual) || isNaN(ref) || (r.campo && !(amb.campos[r.campo - 1] || ""))) break;
            const okc = { "<=": atual <= ref + 1e-9, "<": atual < ref, ">=": atual >= ref - 1e-9, ">": atual > ref }[r.op];
            if (!okc)
              add({ ...base, nivel, codigo: "COMPARACAO", mensagem: r.msg || `Valor deve ser ${r.op} ${r.campo ? "o campo " + r.campo : fmtNum(ref)}.`, regra: r.texto });
            break;
          }
        }
      }
    }
  }

  return { validar, avalia, dataValida, decodifica, NIVEL };
});
