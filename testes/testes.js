// Testes do motor de validação com a especificação gerada do leiaute.
// Uso: node testes/testes.js
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const V = require("../src/validador.js");

const spec = JSON.parse(fs.readFileSync(path.join(__dirname, "../spec/leiaute_204.json"), "utf8"));
const VERSAO = spec.versao;

/** Monta uma linha com a quantidade de campos do leiaute; valores = { nº: "valor" } */
function linha(cod, valores) {
  const n = spec.registros[cod].campos.length;
  const c = new Array(n).fill("");
  c[0] = cod;
  for (const [k, v] of Object.entries(valores || {})) c[k - 1] = v;
  return c.join("|");
}

const CAB = linha("CAB", { 2: VERSAO, 3: "TESTE", 4: "20260101", 5: "1", 6: "20260101", 7: "20260131", 8: "TESTE", 9: "N" });
const PAR = linha("PAR", { 2: "10", 3: "CLIENTE", 4: "CE", 5: "12345678000199", 8: "N", 9: "N", 10: "N", 11: "N", 12: "N", 13: "N", 14: "N", 15: "N", 27: "N", 30: "1058", 31: "N", 32: "1" });

function arquivo(...linhas) {
  const todas = [CAB, ...linhas];
  todas.push(`TRA|${todas.length + 1}`);
  return todas.join("\r\n");
}

function valida(texto, contexto) {
  return V.validar(texto, spec, { contexto: contexto || {} });
}

function tem(r, filtro) {
  return r.ocorrencias.some((o) => Object.entries(filtro).every(([k, v]) => o[k] === v));
}

const testes = [];
const teste = (nome, fn) => testes.push({ nome, fn });

// ---------------------------------------------------------------- estrutura
teste("arquivo mínimo (CAB + PAR + TRA) sem erros", () => {
  const r = valida(arquivo(PAR));
  const erros = r.ocorrencias.filter((o) => o.nivel === "erro");
  assert.deepStrictEqual(erros, []);
});

teste("primeira linha precisa ser CAB", () => {
  const r = valida([PAR, CAB, "TRA|3"].join("\n"));
  assert.ok(tem(r, { codigo: "CAB_PRIMEIRO", linha: 1 }));
});

teste("CAB e TRA obrigatórios", () => {
  const r = valida(PAR);
  assert.ok(tem(r, { codigo: "REGISTRO_OBRIGATORIO", registro: "CAB" }));
  assert.ok(tem(r, { codigo: "REGISTRO_OBRIGATORIO", registro: "TRA" }));
});

teste("versão do leiaute diferente", () => {
  const r = valida(arquivo(PAR).replace(`CAB|${VERSAO}|`, "CAB|195|"));
  assert.ok(tem(r, { codigo: "VERSAO", registro: "CAB", campo: 2 }));
});

teste("TRA com quantidade de linhas errada", () => {
  const r = valida([CAB, PAR, "TRA|99"].join("\n"));
  assert.ok(tem(r, { codigo: "TRA_QTDE" }));
});

teste("registro desconhecido", () => {
  const r = valida(arquivo(PAR, "XYZ|1|2"));
  assert.ok(tem(r, { codigo: "REGISTRO_DESCONHECIDO", registro: "XYZ" }));
});

teste("registro filho sem o pai (PNM sem NFM)", () => {
  const r = valida(arquivo(PAR, linha("PNM", { 2: "1" })));
  assert.ok(tem(r, { codigo: "HIERARQUIA", registro: "PNM" }));
});

teste("campos além do leiaute", () => {
  const r = valida(arquivo(PAR + "|EXTRA"));
  assert.ok(tem(r, { codigo: "CAMPOS_EXCEDENTES", registro: "PAR" }));
});

// ---------------------------------------------------------------- tipos
teste("tipo N com letras e tamanho excedido", () => {
  const r = valida(arquivo(PAR.replace("PAR|10|", "PAR|1A|"), PAR.replace("PAR|10|", "PAR|1234567890|")));
  assert.ok(tem(r, { codigo: "TIPO_N", registro: "PAR", campo: 2, linha: 2 }));
  assert.ok(tem(r, { codigo: "TAMANHO", registro: "PAR", campo: 2, linha: 3 }));
});

teste("tipo D inválido", () => {
  const r = valida(arquivo(PAR).replace("|20260101|1|", "|20260231|1|"));
  assert.ok(tem(r, { codigo: "TIPO_D", registro: "CAB", campo: 4 }));
});

teste("tipo L aceita só S/N", () => {
  const p = PAR.split("|"); p[7] = "X";
  const r = valida(arquivo(p.join("|")));
  assert.ok(tem(r, { codigo: "TIPO_L", registro: "PAR", campo: 8 }));
});

teste("tipo V: vírgula decimal é erro", () => {
  const r = valida(arquivo(PAR, linha("UND", { 2: "UN", 3: "UNIDADE" }), linha("IPE", { 6: "10,50" })));
  assert.ok(tem(r, { codigo: "TIPO_V", registro: "IPE", campo: 6 }));
});

teste("campo obrigatório vazio", () => {
  const r = valida(arquivo(PAR.replace("|CLIENTE|", "||")));
  assert.ok(tem(r, { codigo: "OBRIGATORIO", registro: "PAR", campo: 3 }));
});

teste("domínio de valores (PAR.32 Indicador do ICMS: 1, 2, 3)", () => {
  const p = PAR.split("|"); p[31] = "7";
  const r = valida(arquivo(p.join("|")));
  assert.ok(tem(r, { codigo: "DOMINIO", registro: "PAR", campo: 32 }));
});

// ---------------------------------------------------------------- condicionais
teste("PAR.4 UF obrigatória quando Exterior = N", () => {
  const r = valida(arquivo(PAR.replace("|CLIENTE|CE|", "|CLIENTE||")));
  assert.ok(tem(r, { codigo: "OBRIGATORIO_COND", registro: "PAR", campo: 4 }));
});

teste("PAR.6 Inscrição Estadual não deve ser preenchida com Indicador do ICMS = 2", () => {
  const p = PAR.split("|"); p[5] = "123"; p[31] = "2";
  const r = valida(arquivo(p.join("|")));
  assert.ok(tem(r, { codigo: "PREENCHIMENTO_INDEVIDO", registro: "PAR", campo: 6 }));
  p[31] = "1";
  assert.ok(!tem(valida(arquivo(p.join("|"))), { codigo: "PREENCHIMENTO_INDEVIDO", registro: "PAR", campo: 6 }));
});

teste("PAR.31 Exterior deve ser S quando País ≠ 1058", () => {
  const p = PAR.split("|"); p[29] = "0230"; p[30] = "N";
  const r = valida(arquivo(p.join("|")));
  assert.ok(tem(r, { codigo: "VALOR_COND", registro: "PAR", campo: 31 }));
});

function nfm(valores) {
  return linha("NFM", Object.assign({ 2: "1", 3: "S", 4: "NFE", 5: "S", 6: "1", 9: "123", 12: "20260110", 13: "0", 14: "20260110", 15: "10" }, valores));
}

teste("documento cancelado não deve ter campos preenchidos (NFM.46)", () => {
  const r = valida(arquivo(PAR, nfm({ 13: "1", 46: "100.00" })));
  assert.ok(tem(r, { codigo: "PREENCHIMENTO_INDEVIDO", registro: "NFM", campo: 46 }));
  const ok = valida(arquivo(PAR, nfm({ 13: "0", 46: "100.00" })));
  assert.ok(!tem(ok, { codigo: "PREENCHIMENTO_INDEVIDO", registro: "NFM", campo: 46 }));
});

teste("zero em campo de valor não conta como preenchido em documento cancelado", () => {
  // NFM.27 Frete (tipo V)
  const r = valida(arquivo(PAR, nfm({ 13: "1", 27: "0.00" })));
  assert.ok(!tem(r, { codigo: "PREENCHIMENTO_INDEVIDO", registro: "NFM", campo: 27 }));
  assert.ok(tem(valida(arquivo(PAR, nfm({ 13: "1", 27: "5.00" }))), { codigo: "PREENCHIMENTO_INDEVIDO", registro: "NFM", campo: 27 }));
});

teste("referência: participante da NFM deve existir em PAR", () => {
  const r = valida(arquivo(PAR, nfm({ 15: "999" })));
  assert.ok(tem(r, { codigo: "REFERENCIA", registro: "NFM", campo: 15 }));
  assert.ok(!tem(valida(arquivo(PAR, nfm({ 15: "10" }))), { codigo: "REFERENCIA", registro: "NFM", campo: 15 }));
});

teste("regra de filho usa campo do pai (PNM: operação da NFM)", () => {
  // PNM.25 "Informar somente nas operações de entrada"
  const pnm = linha("PNM", { 2: "1", 25: "10.00" });
  const pro = linha("PRO", { 2: "1", 3: "PRODUTO" });
  const r = valida(arquivo(PAR, pro, nfm({ 3: "S" }), pnm));
  assert.ok(tem(r, { registro: "PNM", campo: 25, codigo: "PREENCHIMENTO_INDEVIDO" }));
  const r2 = valida(arquivo(PAR, pro, nfm({ 3: "E" }), pnm));
  assert.ok(!tem(r2, { registro: "PNM", campo: 25, codigo: "PREENCHIMENTO_INDEVIDO" }));
});

teste("regra que depende do regime só é avaliada com o parâmetro informado", () => {
  // PNM.37 CST COFINS: "Informar somente para empresas com regime de tributação Lucro Real ou Presumido"
  const pnm = linha("PNM", { 2: "1", 37: "01" });
  const pro = linha("PRO", { 2: "1", 3: "PRODUTO" });
  const base = arquivo(PAR, pro, nfm({}), pnm);
  const filtro = { registro: "PNM", campo: 37, codigo: "PREENCHIMENTO_INDEVIDO" };
  assert.ok(!tem(valida(base), filtro));
  assert.ok(tem(valida(base, { regime: "SN" }), filtro));
  assert.ok(!tem(valida(base, { regime: "LR" }), filtro));
});

teste("data: campo só a partir de uma data (CCF.14 a partir de 01/01/2014)", () => {
  const def = spec.registros.CCF.campos.find((c) => c.n === 14);
  assert.ok(def.regras.some((r) => r.quando && JSON.stringify(r.quando).includes("20140101")));
});

teste("codificação Windows-1252 é detectada", () => {
  const txt = arquivo(PAR.replace("CLIENTE", "JOSÉ"));
  const bytes = Uint8Array.from(Array.from(txt).map((ch) => (ch === "É" ? 0xc9 : ch.charCodeAt(0))));
  const r = V.validar(bytes, spec, {});
  assert.strictEqual(r.codificacao, "Windows-1252");
  assert.ok(r.linhas[1].includes("JOSÉ"));
});

// ---------------------------------------------------------------- somatórios, cálculos, comparações
const PRO = linha("PRO", { 2: "1", 3: "PRODUTO" });
function pnm(valores) {
  return linha("PNM", Object.assign({ 2: "1" }, valores));
}

teste("soma: NFM.26 Produtos = soma do Valor Bruto dos PNM", () => {
  const filtro = { codigo: "SOMA", registro: "NFM", campo: 26 };
  const ok = valida(arquivo(PAR, PRO, nfm({ 26: "150.00" }), pnm({ 9: "100.00" }), pnm({ 9: "50.00" })));
  assert.ok(!tem(ok, filtro));
  const ruim = valida(arquivo(PAR, PRO, nfm({ 26: "120.00" }), pnm({ 9: "100.00" }), pnm({ 9: "50.00" })));
  assert.ok(tem(ruim, filtro));
  // sem itens, a soma não é conferida (empresa pode não informar itens)
  assert.ok(!tem(valida(arquivo(PAR, nfm({ 26: "120.00" }))), filtro));
});

teste("soma com filtro: NFM.101 CBS = soma do Valor do Tributo dos IRM de CBS", () => {
  const irm = (trib, tipo, v) => linha("IRM", { 2: trib, 3: tipo, 14: v });
  const filtro = { codigo: "SOMA", registro: "NFM", campo: 101 };
  const ok = valida(arquivo(PAR, PRO, nfm({ 101: "9.00" }), pnm({ 9: "10.00" }), irm("4", "1", "9.00"), irm("1", "1", "5.00")));
  assert.ok(!tem(ok, filtro));
  const ruim = valida(arquivo(PAR, PRO, nfm({ 101: "14.00" }), pnm({ 9: "10.00" }), irm("4", "1", "9.00"), irm("1", "1", "5.00")));
  assert.ok(tem(ruim, filtro));
});

teste("cálculo: PNM.44 Valor Total = Bruto + 41 + 42 + 62 - Desconto(43)", () => {
  const f = { codigo: "CALCULO", registro: "PNM", campo: 44 };
  assert.ok(!tem(valida(arquivo(PAR, PRO, nfm({}), pnm({ 9: "100.00", 41: "10.00", 43: "5.00", 44: "105.00" }))), f));
  assert.ok(tem(valida(arquivo(PAR, PRO, nfm({}), pnm({ 9: "100.00", 41: "10.00", 43: "5.00", 44: "110.00" }))), f));
});

teste("cálculo por produto: PNM.54 Valor COFINS = Base × Alíquota% quando Tipo de Cálculo = 1", () => {
  const f = { codigo: "CALCULO", registro: "PNM", campo: 54 };
  assert.ok(!tem(valida(arquivo(PAR, PRO, nfm({}), pnm({ 39: "200.00", 51: "1", 52: "7.60", 54: "15.20" }))), f));
  assert.ok(tem(valida(arquivo(PAR, PRO, nfm({}), pnm({ 39: "200.00", 51: "1", 52: "7.60", 54: "20.00" }))), f));
});

teste("comparação: SVN.15 Redução ISS ≤ 100", () => {
  const f = { codigo: "COMPARACAO", registro: "SVN", campo: 15 };
  assert.ok(tem(valida(arquivo(PAR, nfm({}), linha("SVN", { 15: "150.00" }))), f));
  assert.ok(!tem(valida(arquivo(PAR, nfm({}), linha("SVN", { 15: "50.00" }))), f));
});

teste("domínio condicional: PNM.37 CST COFINS de saída só 01 a 09, 49 e 99", () => {
  const f = { codigo: "DOMINIO_COND", registro: "PNM", campo: 37 };
  assert.ok(tem(valida(arquivo(PAR, PRO, nfm({ 3: "S" }), pnm({ 37: "50" }))), f));
  assert.ok(!tem(valida(arquivo(PAR, PRO, nfm({ 3: "E" }), pnm({ 37: "50" }))), f));
});

teste("tamanho condicional: número da NF-e com 9 dígitos (aviso)", () => {
  const f = { codigo: "TAMANHO_COND", registro: "NFM", campo: 9 };
  assert.ok(tem(valida(arquivo(PAR, nfm({ 4: "NFE", 9: "1234" }))), f));
  assert.ok(!tem(valida(arquivo(PAR, nfm({ 4: "NFE", 9: "000001234" }))), f));
});

teste("obrigatório por grupo: IRM.27 Tipo ALC/ZFM quando algum campo do grupo é informado", () => {
  const f = { codigo: "OBRIGATORIO_COND", registro: "IRM", campo: 27 };
  const base = (v) => arquivo(PAR, PRO, nfm({}), pnm({ 9: "10.00" }), linha("IRM", Object.assign({ 2: "1", 3: "1" }, v)));
  assert.ok(tem(valida(base({ 29: "10.00" })), f));
  assert.ok(!tem(valida(base({})), f));
});

teste("parâmetro da empresa novo: regime do PIS/COFINS (NFM.69)", () => {
  // "Informar somente para operações de Entrada cuja empresa tenha recolhimento ... não cumulatividade"
  const f = { codigo: "PREENCHIMENTO_INDEVIDO", registro: "NFM", campo: 69 };
  const arq = arquivo(PAR, nfm({ 3: "E", 69: "100.00" }));
  assert.ok(!tem(valida(arq), f));
  assert.ok(!tem(valida(arq, { pisCofins: "N" }), f));
  assert.ok(tem(valida(arq, { pisCofins: "C" }), f));
});

// ---------------------------------------------------------------- versões anteriores do leiaute
const pastaSpec = path.join(__dirname, "../spec");
const versoes = fs.readdirSync(pastaSpec).map((f) => (/^leiaute_(\d+)\.json$/.exec(f) || [])[1]).filter(Boolean);
const specDe = (v) => JSON.parse(fs.readFileSync(path.join(pastaSpec, `leiaute_${v}.json`), "utf8"));

teste("todas as versões do leiaute estão disponíveis", () => {
  for (const v of ["175", "183", "190", "195", "197", "204"]) assert.ok(versoes.includes(v), `falta leiaute_${v}.json`);
});

teste("cada versão: CAB/TRA definidos, campos numerados em sequência, tipos válidos, CAB.2 fixo na versão", () => {
  for (const v of versoes) {
    const s = specDe(v);
    assert.strictEqual(s.versao, v);
    assert.ok(s.registros.CAB && s.registros.TRA, `${v}: CAB/TRA`);
    for (const [cod, r] of Object.entries(s.registros)) {
      r.campos.forEach((c, i) => {
        assert.strictEqual(c.n, i + 1, `${v} ${cod}: campo ${c.n} na posição ${i + 1}`);
        assert.ok("NCDLVTS".includes(c.tipo) && c.tipo, `${v} ${cod}.${c.n}: tipo "${c.tipo}"`);
      });
    }
    const cab2 = s.registros.CAB.campos[1];
    assert.ok(cab2.obs.some((o) => o.texto.includes(`"${v}"`)), `${v}: CAB.2 deveria conter "${v}"`);
  }
});

teste("leiaute 195 (lido por OCR) tem a estrutura dos arquivos reais gerados na 195", () => {
  // quantidade de campos observada em arquivos .fs reais no leiaute 195
  const s = specDe("195");
  const esperado = { NFM: 106, PNM: 155, NVC: 59, PNC: 55, PRO: 56, IVC: 21, INM: 34 };
  for (const [cod, n] of Object.entries(esperado)) assert.strictEqual(s.registros[cod].campos.length, n, cod);
});

teste("arquivo na versão do leiaute validado pela própria versão não acusa versão nem quantidade de campos", () => {
  for (const v of versoes) {
    const s = specDe(v);
    const lin = (cod, vals) => { const c = new Array(s.registros[cod].campos.length).fill(""); c[0] = cod; for (const [k, x] of Object.entries(vals)) c[k - 1] = x; return c.join("|"); };
    const txt = [lin("CAB", { 2: v, 3: "T", 4: "20200101", 5: "1", 6: "20200101", 7: "20200131", 8: "T", 9: "N" }), "TRA|2"].join("\r\n");
    const r = V.validar(new TextEncoder().encode(txt), s, {});
    const ruins = r.ocorrencias.filter((o) => /VERSAO|CAMPOS_/.test(o.codigo));
    assert.deepStrictEqual(ruins.map((o) => o.mensagem), [], v);
  }
});

// ---------------------------------------------------------------- execução
let falhas = 0;
for (const t of testes) {
  try {
    t.fn();
    console.log("  ok    " + t.nome);
  } catch (e) {
    falhas++;
    console.log("  FALHA " + t.nome + "\n        " + e.message.split("\n")[0]);
  }
}
console.log(`\n${testes.length - falhas}/${testes.length} testes passaram`);
process.exit(falhas ? 1 : 0);
