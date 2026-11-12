# Validador Fortes Fiscal (.fs)

Página HTML que valida arquivos de importação `.fs` do Fortes Fiscal conforme o
**Leiaute de Importação**, nas versões **204, 197, 195, 190, 183 e 175**. Roda 100% no navegador:
o arquivo não sai do computador.

## Usar

Abra `dist/validador-fortes.html` no navegador (Chrome/Edge) e arraste o arquivo `.fs`.

- **Conferir pelo leiaute** (topo da página): *Automático* usa a versão informada no CAB do arquivo; se ela
  não estiver disponível, usa a versão mais próxima abaixo dela (ex.: 196 → 195) e avisa. Também dá para
  escolher uma versão fixa (fica salva no navegador).

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

### Publicar na web

A pasta `dist/` é o site completo: `index.html` abre a versão atual, e `_headers` define uma política de
segurança (CSP com `connect-src 'none'`) que faz o navegador bloquear qualquer envio de dados. Nenhum build
é necessário no servidor; depois de `python tools/build.py`, basta fazer commit e push.

- **Netlify**: em *Add new site → Import an existing project*, escolha GitHub e o repositório `Validador-Fortes`.
  O `netlify.toml` já informa a pasta `dist` e que não há comando de build. Clique em *Deploy*.
- **Cloudflare Pages**: em *Workers & Pages → Create → Pages → Connect to Git*, escolha o repositório.
  Em *Framework preset* use *None*, deixe *Build command* vazio e, em *Build output directory*, use `dist`.

Os dois serviços dão acesso só aos repositórios que você autorizar e republicam o site a cada push na `main`.

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
  build.py                 monta dist/validador-fortes.html (arquivo único, todas as versões)
  portar_versao.py         semântica/correções/regras manuais de uma versão para outra
  ocr/                     leitura por OCR dos PDFs sem texto (190, 195)
testes/
  testes.js                testes do motor (node testes/testes.js)
  resumo.js                valida um .fs pela versão do CAB e agrupa as ocorrências (LEIAUTE=204 força a versão)
```

## Versões anteriores

Cada versão tem os seus arquivos em `spec/` (`leiaute_<v>.raw.json`, `leiaute_<v>.json`, `correcoes_<v>.json`,
`semantica_<v>.json`, `regras_manuais_<v>.json`). O `build.py` embute todas as especificações
`spec/leiaute_<v>.json` na página, comprimidas (gzip + base64, ~200 KB por versão), e a página só abre a
versão em uso.

Para incluir uma versão nova (ou antiga):

```bash
python tools/extrair_leiaute.py "<pdf>" spec/leiaute_<v>.raw.json --cache spec/.cache_pdf_<v>.json
python tools/portar_versao.py 204 <v>        # semântica/correções/regras manuais com os campos da versão <v>
python tools/gerar_regras.py spec/leiaute_<v>.raw.json spec/leiaute_<v>.json
python tools/build.py
```

`portar_versao.py` associa os campos pelo nome e só leva correções cujo defeito também está no PDF novo e
regras manuais cujo texto também aparece nas observações da versão; o que não puder portar é listado.
PDFs antigos (175, 183) têm espaços estreitos entre palavras: use `--xtol 1.5` no extrator.

### PDFs sem texto (OCR)

Para PDFs cujo texto é desenho vetorial (190, 195), `tools/ocr/` gera o cache de páginas por OCR
(Windows.Media.Ocr pt-BR; requer `pypdfium2`, `pillow`, `numpy`):

```bash
python tools/ocr/ocr_leiaute.py "P:/Base/Leiaute Fortes 195.pdf" 195 <pasta_temporária>
python tools/extrair_leiaute.py x spec/leiaute_195.raw.json --cache spec/.cache_pdf_195.json
python tools/ocr/reconciliar.py 195 197 204 183
```

A grade das tabelas é detectada na imagem e apagada antes do OCR; letras e números isolados (colunas Tipo e
Nº), que o OCR ignora, são classificados por comparação com a fonte; palavras são corrigidas com o vocabulário
dos leiautes com texto. `reconciliar.py` mantém a estrutura lida e troca os textos pelos do vizinho quando
batem (nome do campo, observações, tabelas de códigos), desmembra campos que o OCR juntou e completa
tamanho/tipo ilegíveis.

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
- Os PDFs das versões **190 e 195** não têm texto (as letras são desenhadas como vetores). Foram lidos por
  OCR e reconciliados com os leiautes vizinhos (ver abaixo). A estrutura (registros, campos, ordem,
  tamanho e tipo) confere com arquivos reais gerados na 195, mas textos e regras podem ter imprecisões;
  a página sinaliza essas versões como "lido por OCR".
- O PDF da 197 traz `Texto Fixo Contendo "196"` no CAB.2; o gerador ajusta para a versão do leiaute.
