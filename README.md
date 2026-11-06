# Validador Fortes Fiscal (.fs)

Página HTML que valida arquivos de importação `.fs` do Fortes Fiscal conforme o
**Leiaute de Importação versão 204**. Roda 100% no navegador: o arquivo não sai do computador.

## Usar

Abra `dist/validador-fortes-204.html` no navegador (Chrome/Edge) e arraste o arquivo `.fs`.

- **Agrupado / Por linha**: ocorrências agrupadas pela regra ou listadas linha a linha.
- Clique numa ocorrência para ver a linha decomposta campo a campo, com as observações do leiaute.
- **Parâmetros da empresa** (opcional): UF, regime de tributação, regime do PIS/COFINS, contribuinte do IPI,
  apura retenções, CPRB, informa itens, códigos contábeis, "Conta Informada na Baixa", ICMS no RMD.
  Habilitam as regras que dependem do histórico da empresa no Fortes
  (ex.: "Informar somente para empresas do Lucro Real ou Presumido").
  Sem eles, essas regras ficam de fora e aparecem como "verificações aguardando parâmetros".
- **Exportar CSV**: exporta as ocorrências filtradas (abre direto no Excel).
- Aba **Leiaute e regras**: todos os registros/campos do leiaute e, para cada observação do PDF,
  se ela é validada automaticamente, depende de parâmetros, é só descritiva ou ainda não foi automatizada.

### O que é verificado

| Verificação | Exemplo |
|---|---|
| Estrutura | CAB na 1ª linha, TRA no fim com a quantidade de linhas, registro desconhecido, quantidade de campos |
| Hierarquia | registro filho fora do pai (ex.: `PNM` sem `NFM`), máximo de ocorrências |
| Tipo e tamanho | N (só dígitos), D (data AAAAMMDD válida), L (S/N), V (ponto decimal, casas decimais), tamanho máximo |
| Obrigatoriedade | "Campo Obrigatório", inclusive combinada com restrições ("obrigatório, exceto se cancelado") |
| Valores aceitos | listas do próprio campo ("1 - Contribuinte; 2 - Isento") e Tabelas 01–57 do leiaute |
| Referências | "O valor informado deve existir no campo 2 do registro PAR/PRO/…" |
| Condicionais | "não deve ser preenchido quando documento for cancelado", "obrigatório quando campo 31 = N", "informar somente nas operações de entrada", "a partir de 01/01/2014", CST/CSOSN, espécie, documento próprio, campos do registro pai etc. |
| Valores por condição | "Para as operações de Saídas os valores válidos são: 01 a 09, 49 e 99", "Quando a espécie for NFST deve ser informado o valor: …", "Poderá ser informado tipo 2 apenas quando …" |
| Somatórios | "deve ser igual a soma do campo Valor Bruto dos registros PNM"; IBS/CBS somados por tributo/tipo dos registros IRM/IIC/IEE… (aviso) |
| Cálculos e limites | "Valor Bruto (campo 9) menos Descontos (22) e Deduções (23)", "Base × Alíquota (%)", "menor ou igual a 100", "não deve ser maior que Valor Adicional INSS" |
| Tamanho por espécie | número da NF-e com 9 dígitos, demais com 7 (regras manuais) |

Níveis: **erro** (viola o leiaute), **aviso** (provável problema ou regra com restrição não verificável,
ex.: casas decimais acima do padrão de 2 quando o leiaute não especifica), **info** (ex.: linha com menos
campos que o leiaute — os ausentes são tratados como vazios).

## Estrutura

```
spec/
  leiaute_204.raw.json     extraído do PDF (tools/extrair_leiaute.py)
  correcoes_204.json       correções manuais de lacunas/erros do PDF (com a origem de cada uma)
  semantica_204.json       qual campo é "cancelado", "operação", "espécie", "data", "substituição"… em cada
                           documento, e a lista de parâmetros da empresa exibidos na página
  regras_manuais_204.json  regras escritas à mão para frases que o gerador não entende
  leiaute_204.json         especificação final usada pelo validador (tools/gerar_regras.py)
src/
  validador.js             motor de validação (navegador e Node)
  app.js, index.html, estilo.css   interface
tools/
  extrair_leiaute.py       PDF -> JSON cru
  gerar_regras.py          JSON cru -> regras
  build.py                 monta dist/validador-fortes-<versão>.html (arquivo único)
testes/
  testes.js                testes do motor (node testes/testes.js)
  resumo.js                valida um .fs e agrupa as ocorrências (node testes/resumo.js arquivo.fs)
```

## Regerar (novo PDF / nova versão do leiaute)

Requer Python 3.9+ e `pip install -r tools/requirements.txt` (pdfplumber 0.11.4).

```bash
python tools/extrair_leiaute.py "P:\Base\Leiaute_de_Importacao_do_Fortes_Fiscal_-_204.pdf" spec/leiaute_204.raw.json --cache spec/.cache_pdf.json
python tools/gerar_regras.py spec/leiaute_204.raw.json spec/leiaute_204.json
node testes/testes.js
python tools/build.py
```

A leitura do PDF leva ~4 minutos; com `--cache` as execuções seguintes são instantâneas.
O extrator lista avisos (campos sem tipo, numeração fora de sequência, células mescladas) —
confira-os e registre o ajuste em `correcoes_XXX.json`. Para uma nova versão, copie
`correcoes_204.json` e `semantica_204.json` com o novo número e revise.

### Regras escritas à mão

Observações que o gerador não entende ficam como "não automatizado" (lista na aba Leiaute e regras).
Para cobrir uma delas, acrescente em `spec/regras_manuais_204.json` (o tópico com o mesmo texto passa a
aparecer como validado):

```json
{
  "campos": {
    "SVG.5": [
      { "tipo": "vazio", "quando": { "campo": 4, "op": "=", "valor": "S" },
        "texto": "Campo não deve ser preenchido quando “Sem Sit. Tributária (ICMS)” for informado “S”" }
    ]
  }
}
```

Tipos de regra: `obrigatorio`, `vazio`, `valor` (`valor`/`valores`), `dominio` / `proibido` (`valores`),
`tamanho` (`exato`/`max`), `calculo` (`termos: [{campo, sinal}]` ou `produto: [a, b]` + `divisor`),
`comparacao` (`op` + `valor` ou `termos`) e `soma` (`termos: [{registro, campo, sinal, filtro}]`, conferida
ao fechar o registro pai). Todas aceitam `nivel` (`erro`/`aviso`) e `quando`.
Condições: `{campo, op, valor|valores}` (op: `=`, `in`, `!=`, `notin`, `preenchido`, `vazio`, `>=`, `<`,
`posicaoIn`), `registro` para ler um campo do registro pai, `ctx` para parâmetros da empresa,
e combinações `{e:[…]}`, `{ou:[…]}`, `{nao:…}`.

## Limitações conhecidas

- Regras que dependem do cadastro do Fortes (histórico da empresa, configurações de apuração,
  "Conta Informada na Baixa" etc.) não podem ser conferidas só com o arquivo.
- Cerca de 5% das observações do PDF (474 tópicos) seguem sem verificação automática: dependem de CFOP/natureza
  da operação, de configurações do Fortes sem parâmetro na tela (PRODEPE, ZFM, RET, MCMV…), de comparações com
  outros registros ("mesmo produto do registro pai", "unidade do produto de origem") ou têm texto ambíguo.
  A lista completa está na aba **Leiaute e regras**.
- Somatórios de IBS/CBS por tipo de tributo seguem a leitura do texto do leiaute ("IBS Estadual + IBS Municipal
  - Crédito Presumido") e por isso são emitidos como aviso.
- Os arquivos de exemplo em `P:\Base` estão no leiaute **195**; validados pelo 204, campos incluídos
  depois da 195 aparecem como divergências (o validador avisa isso no topo).
