// Uso: node testes/resumo.js arquivo.fs [...]  -> agrupa ocorrências por código/registro/campo
// A versão do leiaute vem do CAB (a mais próxima abaixo, se não houver); LEIAUTE=204 força uma versão.
const fs = require("fs");
const path = require("path");
const V = require("../src/validador.js");
const versoes = fs.readdirSync(path.join(__dirname, "../spec")).map((f) => (/^leiaute_(\d+)\.json$/.exec(f) || [])[1]).filter(Boolean).sort((a, b) => b - a);
function specPara(bytes) {
  const v = process.env.LEIAUTE || (/^(?:﻿)?CAB\|(\d+)/.exec(Buffer.from(bytes.subarray(0, 200)).toString("latin1")) || [])[1];
  const escolhida = versoes.includes(v) ? v : versoes.find((x) => +x <= +v) || versoes[0];
  return JSON.parse(fs.readFileSync(path.join(__dirname, `../spec/leiaute_${escolhida}.json`), "utf8"));
}
for (const arq of process.argv.slice(2)) {
  const t0 = Date.now();
  const bytes = new Uint8Array(fs.readFileSync(arq));
  const spec = specPara(bytes);
  const r = V.validar(bytes, spec, {});
  console.log(`\n${arq} [leiaute ${spec.versao}]: ${r.estatisticas.linhas} linhas, ${r.resumo.erros} erros, ${r.resumo.avisos} avisos, ${r.resumo.infos} infos (${r.codificacao}, ${Date.now() - t0} ms)`);
  console.log("  registros:", Object.entries(r.estatisticas.porRegistro).map(([k, v]) => k + "=" + v).join(" "));
  const g = {};
  for (const o of r.ocorrencias) {
    const k = `${o.nivel} ${o.codigo} ${o.registro || ""}${o.campo ? "." + o.campo : ""} ${o.nomeCampo || ""}`;
    (g[k] = g[k] || { n: 0, ex: o }).n++;
  }
  for (const [k, v] of Object.entries(g).sort((a, b) => b[1].n - a[1].n).slice(0, Number(process.env.TOP || 60)))
    console.log(`  ${String(v.n).padStart(6)}  ${k} | ${v.ex.mensagem.slice(0, 110)} | L${v.ex.linha} valor="${(v.ex.valor || "").slice(0, 20)}"`);
}
