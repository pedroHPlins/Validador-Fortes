// Uso: node testes/rodar.js arquivo.fs [spec.json]
const fs = require("fs");
const path = require("path");
const V = require("../src/validador.js");
const arq = process.argv[2];
const spec = JSON.parse(fs.readFileSync(process.argv[3] || path.join(__dirname, "../spec/leiaute_204.json"), "utf8"));
const r = V.validar(new Uint8Array(fs.readFileSync(arq)), spec, {});
console.log(`${arq}: ${r.resumo.erros} erros, ${r.resumo.avisos} avisos, ${r.resumo.infos} infos (${r.codificacao})`);
for (const o of r.ocorrencias) {
  if (o.nivel === "info" && !process.env.INFO) continue;
  console.log(`  L${o.linha} ${o.registro || ""}${o.campo ? "." + o.campo : ""} ${o.nomeCampo ? "(" + o.nomeCampo + ")" : ""} [${o.nivel}/${o.codigo}] ${o.mensagem}${o.valor ? ' valor="' + o.valor + '"' : ""}`);
}
