"""
Extrai a especificação do "Leiaute de Importação do Fortes Fiscal" (PDF) para JSON.

Uso:
    python tools/extrair_leiaute.py <leiaute.pdf> <saida.raw.json> [--cache arquivo.json]

Gera um JSON "cru" com:
  - registros: código, nome, pai (hierarquia), observações e campos
               (nº, nome, descrição em tópicos, tamanho, tipo)
  - tabelas:   tabelas de códigos do final do documento (Tabela 01, 02, ...)
  - avisos:    inconsistências encontradas no PDF

A leitura do PDF é lenta (~4 min). Com --cache, o resultado bruto da leitura é
gravado/reaproveitado nesse arquivo.

As regras de validação são derivadas desse JSON por tools/gerar_regras.py.
Requer: pdfplumber (ver tools/requirements.txt).
"""
import json
import os
import re
import sys

RE_VERSAO = re.compile(r"Vers[aã]o\s+(\d{3})")
RE_HIER = re.compile(r"^Registro\s+[Tt]ipo\s+([A-Z0-9]{3})\s*[\.…·]+\s*(.*)$")
RE_DETALHE = re.compile(r"^Registro\s+[Tt]ipo\s+([A-Z0-9]{3})\s*(?:[-–—]\s*(.+)|\((.+?)\)?)\s*$")
RE_TABELA = re.compile(r"^Tabela\s+(\d+)\s*[-–—]\s*(.+)$")
RE_OBS = re.compile(r"^Obs\.?\s*\d*\s*:", re.I)
RE_PAGINA = re.compile(r"^P[áa]gina\s+\d+\s*$")

BULLETS = "●○■▪•"
TIPOS = ("N", "C", "D", "L", "V", "T", "S")


# ---------------------------------------------------------------- leitura PDF

def ler_pdf(caminho_pdf):
    import pdfplumber
    paginas = []
    with pdfplumber.open(caminho_pdf) as pdf:
        for page in pdf.pages:
            tabelas = []
            for t in page.find_tables():
                tabelas.append({"bbox": list(t.bbox), "rows": t.extract(),
                                "rowbbox": [list(r.bbox) for r in t.rows]})
            linhas = [{"text": l["text"], "x0": l["x0"], "top": l["top"], "bottom": l["bottom"]}
                      for l in page.extract_text_lines()]
            paginas.append({"lines": linhas, "tables": tabelas})
    return paginas


# ---------------------------------------------------------------- utilitários

def limpa(s):
    if s is None:
        return ""
    s = s.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    return re.sub(r"[ \t]+", " ", s).strip()


RE_ITEM = re.compile(r"^[\"“]?[0-9A-Z]{1,4}[\"”]?\s*[-–—=]\s*\S")
RE_INICIO_FRASE = re.compile(r"^(Campo |O campo |Este campo |Esse campo |O valor |Valores |Valor válido|Informar |Preencher |"
                             r"Não |Só |Somente |Deverá |Deve |Indica |Quando |Caso )")


def separa_topicos(texto):
    """Divide a célula de descrição em tópicos (cada marcador '●'/'○' inicia um novo)."""
    topicos = []
    sem_marcador = not any(b in (texto or "") for b in BULLETS)
    for l in (texto or "").split("\n"):
        l = limpa(l)
        if not l:
            continue
        if l[0] in BULLETS:
            topicos.append(l.lstrip(BULLETS).strip())
        elif sem_marcador and topicos and RE_INICIO_FRASE.match(l):
            # células sem marcadores: cada frase normativa vira um tópico
            topicos.append(l)
        elif topicos and not RE_ITEM.match(l):
            topicos[-1] += " " + l
        else:
            topicos.append(l)
    return topicos


def eh_cabecalho_campos(row):
    c = [limpa(x).lower() for x in row if x is not None]
    return len(c) >= 4 and c[0] in ("nº", "n°", "no", "n") and c[1].startswith("campo")


def eh_cabecalho_valores(row):
    c = [limpa(x).lower() for x in row if limpa(x)]
    return len(c) >= 2 and c[0] in ("valor", "código", "codigo", "cód.", "cod") and c[1].startswith("descri")


def contido(inner, outer, tol=2):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and
            inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def normaliza_tipo(tp):
    tp = limpa(tp).upper()
    return tp


# ---------------------------------------------------------------- montagem

class Montador:
    def __init__(self):
        self.versao = None
        self.hierarquia = []
        self.registros = {}
        self.ordem = []
        self.tabelas = {}
        self.avisos = []
        self.atual = None
        self.tabela_atual = None
        self.em_detalhe = False
        self.ultimo_obs = None
        self.pendentes = []  # campos aguardando Tam/Tipo (células mescladas)
        self.cur = None      # campo que recebe as linhas de continuação

    def aviso(self, msg):
        self.avisos.append(msg)

    # ---- linhas de texto fora de tabelas
    def linha(self, txt, x0, pn):
        txt = limpa(txt)
        if not txt or RE_PAGINA.match(txt):
            return
        if self.versao is None:
            m = RE_VERSAO.search(txt)
            if m:
                self.versao = m.group(1)
        if "Detalhamento dos Registros" in txt:
            self.em_detalhe = True
            return
        if not self.em_detalhe:
            m = RE_HIER.match(txt)
            if m:
                self.hierarquia.append((m.group(1), limpa(m.group(2)), x0, pn))
            return
        m = RE_DETALHE.match(txt)
        if m:
            cod = m.group(1)
            nome = limpa(m.group(2) or m.group(3) or "")
            self.tabela_atual = None
            self.ultimo_obs = None
            self.pendentes = []
            if cod in self.registros:
                self.aviso(f"Registro {cod} detalhado mais de uma vez (pág. {pn}); mantida a primeira definição.")
                self.atual = None
            else:
                self.atual = {"codigo": cod, "nome": nome, "pagina": pn, "obs": [], "texto": [], "campos": []}
                self.cur = None
                self.registros[cod] = self.atual
                self.ordem.append(cod)
            return
        m = RE_TABELA.match(txt)
        if m and "TRA" in self.registros:  # tabelas de códigos vêm após o Trailler
            num = m.group(1).zfill(2)
            self.tabela_atual = {"numero": num, "titulo": limpa(m.group(2)), "pagina": pn,
                                 "texto": [], "valores": []}
            if num in self.tabelas:
                self.aviso(f"Tabela {num} repetida (pág. {pn})")
            self.tabelas[num] = self.tabela_atual
            self.atual = None
            return
        if self.tabela_atual is not None:
            self.tabela_atual["texto"].append(txt)
            return
        if self.atual is not None:
            if RE_OBS.match(txt):
                self.atual["obs"].append(txt)
                self.ultimo_obs = len(self.atual["obs"]) - 1
            elif self.ultimo_obs is not None:
                self.atual["obs"][self.ultimo_obs] += " " + txt
            else:
                self.atual["texto"].append(txt)

    # ---- tabelas de códigos (final do documento)
    def tabela_valores(self, rows):
        t = self.tabela_atual
        # tabelas em várias colunas: "Valor | Descrição | Valor | Descrição ..."
        if rows:
            cab = [limpa(c).lower() for c in rows[0]]
            idx = [i for i, c in enumerate(cab) if c in ("valor", "código", "codigo")]
            if len(idx) > 1:
                pares = []
                for k, i in enumerate(idx):
                    fim = idx[k + 1] if k + 1 < len(idx) else len(cab)
                    pares.append((i, fim))
                t["_pares"] = pares
            elif idx:
                t.pop("_pares", None)
        if t.get("_pares"):
            colunas = [[] for _ in t["_pares"]]
            for r in rows:
                if eh_cabecalho_valores(r):
                    continue
                for k, (i, fim) in enumerate(t["_pares"]):
                    cel = [limpa(c) for c in r[i:fim]]
                    if any(cel):
                        colunas[k].append([cel[0], " ".join(x for x in cel[1:] if x)])
            for col in colunas:  # lê coluna a coluna, preservando a ordem do documento
                for valor, desc in col:
                    if valor == "" and t["valores"]:
                        t["valores"][-1]["descricao"] += " " + desc
                    else:
                        t["valores"].append({"valor": valor, "descricao": desc})
            return
        for r in rows:
            cel = [limpa(c) for c in r if c is not None]
            if any(cel) and len(cel) > 2 and cel[0] == "":
                cel = cel[1:]
            if not any(cel) or eh_cabecalho_valores(r):
                continue
            if len(cel) == 1:
                if t["valores"]:
                    t["valores"][-1]["descricao"] += " " + cel[0]
                else:
                    t["texto"].append(cel[0])
                continue
            valor, desc = cel[0], limpa(" ".join(cel[1:]))
            if valor == "" and t["valores"]:
                t["valores"][-1]["descricao"] += " " + desc
            else:
                t["valores"].append({"valor": valor, "descricao": desc})

    # ---- tabelas de campos
    def tabela_campos(self, tab, aninhadas, pn):
        reg = self.atual
        self.ultimo_obs = None
        rows = tab["rows"]
        rowbbox = tab.get("rowbbox") or [None] * len(rows)
        campos_por_linha = []
        orfa = None  # linha sem Nº mas com Tam/Tipo: pode ser o conteúdo do próximo campo

        for r, rb in zip(rows, rowbbox):
            if eh_cabecalho_campos(r):
                continue
            r = list(r)
            if len(r) < 5:
                r = r + [None] * (5 - len(r))
            lin = {
                "num": limpa(r[0]),
                "nome": r[1] or "",
                "desc": "\n".join(x for x in r[2:-2] if x),
                "tam": (r[-2] or "").strip(),
                "tp": (r[-1] or "").strip().upper(),
                "rb": rb,
            }
            # colunas Tam/Tipo trocadas no PDF
            if lin["tam"].upper() in TIPOS and re.fullmatch(r"\d+|-", lin["tp"]):
                lin["tam"], lin["tp"] = lin["tp"], lin["tam"].upper()

            nums = lin["num"].split() if lin["num"] else []
            if nums and all(re.fullmatch(r"\d+", n) for n in nums):
                if len(nums) == 1 and not lin["tam"] and not lin["tp"] and orfa is not None:
                    # nº centralizado numa célula alta: o conteúdo veio na linha anterior
                    lin["nome"] = lin["nome"] or orfa["nome"]
                    lin["desc"] = "\n".join(x for x in (orfa["desc"], lin["desc"]) if x)
                    lin["tam"], lin["tp"] = orfa["tam"], orfa["tp"]
                    orfa = None
                elif orfa is not None:
                    self._continua(orfa, pn, campos_por_linha)
                    orfa = None
                self._novos_campos(reg, nums, lin, pn, campos_por_linha)
                continue

            if lin["num"]:
                # texto de sub-tabela capturado na coluna Nº: anexa ao campo corrente
                self.aviso(f"{reg['codigo']}: célula Nº inesperada '{lin['num'][:40]}' (pág. {pn}) - anexada ao campo anterior")
                lin = {"num": "", "nome": "", "desc": "\n".join(x for x in r if x), "tam": "", "tp": "", "rb": rb}

            cur = self.cur
            if (lin["tam"] or lin["tp"]) and cur is not None and cur["tam"] and cur["tipo"] and not self.pendentes:
                if orfa is not None:
                    self._continua(orfa, pn, campos_por_linha)
                orfa = lin
                continue
            if orfa is not None:
                self._continua(orfa, pn, campos_por_linha)
                orfa = None
            self._continua(lin, pn, campos_por_linha)
        if orfa is not None:
            self._continua(orfa, pn, campos_por_linha)

        # sub-tabelas desenhadas dentro de células: anexa o texto ao campo da linha
        for sub in aninhadas:
            alvo = None
            for rb, c in campos_por_linha:
                if rb and c is not None and rb[1] - 1 <= sub["bbox"][1] <= rb[3] + 1:
                    alvo = c
            if alvo is None:
                continue
            ja = " ".join([alvo["nome"]] + alvo["descricao"])
            for row in sub["rows"]:
                for x in row:
                    x = limpa(x)
                    if x and limpa(x.lstrip(BULLETS)) not in ja:
                        alvo["descricao"].append(x.lstrip(BULLETS).strip())

    def _novos_campos(self, reg, nums, lin, pn, campos_por_linha):
        topicos = separa_topicos(lin["desc"])
        nomes = [limpa(x) for x in lin["nome"].split("\n") if limpa(x)]
        if len(nums) == 1:
            nomes = [limpa(" ".join(nomes))]
        else:
            self.aviso(f"{reg['codigo']}: campos {','.join(nums)} em células mescladas (pág. {pn}) - conferir")
        tams = lin["tam"].split() if lin["tam"] else []
        tps = lin["tp"].split() if lin["tp"] else []
        novos = []
        for i, n in enumerate(nums):
            c = {"n": int(n), "nome": nomes[i] if i < len(nomes) else "", "descricao": [],
                 "tam": tams[i] if i < len(tams) else "", "tipo": tps[i] if i < len(tps) else "", "pagina": pn}
            if len(nums) == 1:
                c["descricao"] = topicos
            elif len(topicos) == len(nums):
                c["descricao"] = [topicos[i]]
            elif i == 0:
                c["descricao"] = topicos
            existente = next((x for x in reg["campos"] if x["n"] == c["n"]), None)
            if existente is not None:
                if limpa(existente["nome"]).lower() == c["nome"].lower():
                    # linha repetida na quebra de página
                    novos.append(existente)
                    continue
                if any("passa a ser" in t for t in c["descricao"]):
                    self.aviso(f"{reg['codigo']}.{c['n']}: '{existente['nome']}' substituído por '{c['nome']}' (pág. {pn})")
                    existente.clear()
                    existente.update(c)
                    novos.append(existente)
                    continue
                self.aviso(f"{reg['codigo']}: campo nº {c['n']} repetido ('{existente['nome']}' / '{c['nome']}') (pág. {pn})")
            reg["campos"].append(c)
            novos.append(c)
        self.pendentes = [c for c in novos if not c["tam"] and not c["tipo"]]
        self.cur = novos[-1]
        campos_por_linha.append((lin["rb"], novos[-1]))

    def _continua(self, lin, pn, campos_por_linha):
        reg = self.atual
        c = self.cur
        topicos = separa_topicos(lin["desc"])
        if c is None:
            if lin["desc"] or lin["nome"] or lin["tam"]:
                self.aviso(f"{reg['codigo']}: linha de continuação sem campo anterior (pág. {pn})")
            return
        if limpa(lin["nome"]) and limpa(lin["nome"]).lower() not in c["nome"].lower():
            c["nome"] = limpa(c["nome"] + " " + limpa(lin["nome"]))
        if topicos:
            primeira = lin["desc"].lstrip()[:1]
            if c["descricao"] and primeira not in BULLETS and not RE_ITEM.match(topicos[0]):
                c["descricao"][-1] += " " + topicos[0]
                topicos = topicos[1:]
            for t in topicos:
                if t not in c["descricao"]:
                    c["descricao"].append(t)
        if lin["tam"] or lin["tp"]:
            self._atribui_tam_tipo(lin["tam"], lin["tp"])
        campos_por_linha.append((lin["rb"], c))

    def _atribui_tam_tipo(self, tam, tp):
        if self.pendentes:
            c = self.pendentes.pop(0)
            c["tam"], c["tipo"] = tam, tp
            return
        c = self.cur
        if not c["tam"]:
            c["tam"] = tam
        if not c["tipo"]:
            c["tipo"] = tp

    # ---- página
    def pagina(self, pg, pn):
        tabs = pg["tables"]
        # separa tabelas aninhadas (desenhadas dentro de outra tabela)
        externas, aninhadas = [], {}
        for i, t in enumerate(tabs):
            pai = None
            for j, o in enumerate(tabs):
                if i != j and contido(t["bbox"], o["bbox"]) and not contido(o["bbox"], t["bbox"]):
                    pai = j
                    break
            if pai is None:
                externas.append(i)
            else:
                aninhadas.setdefault(pai, []).append(t)

        def dentro_tabela(l):
            for t in tabs:
                x0, top, x1, bottom = t["bbox"]
                if l["top"] >= top - 1 and l["bottom"] <= bottom + 1 and l["x0"] >= x0 - 1 and l["x0"] <= x1:
                    return True
            return False

        eventos = [(l["top"], 0, l) for l in pg["lines"] if not dentro_tabela(l)]
        eventos += [(tabs[i]["bbox"][1], 1, i) for i in externas]
        eventos.sort(key=lambda e: (e[0], e[1]))
        for _, tipo, obj in eventos:
            if tipo == 0:
                self.linha(obj["text"], obj["x0"], pn)
                continue
            tab = tabs[obj]
            if not tab["rows"]:
                continue
            if self.tabela_atual is not None:
                self.tabela_valores(tab["rows"])
            elif self.atual is not None and self.em_detalhe:
                self.tabela_campos(tab, aninhadas.get(obj, []), pn)

    # ---- pós-processamento
    def finaliza(self):
        pilha, pais, ordem_hier = [], {}, []
        for cod, nome, x0, pn in self.hierarquia:
            while pilha and pilha[-1][1] > x0 - 8:
                pilha.pop()
            pais[cod] = pilha[-1][0] if pilha else None
            pilha.append((cod, x0))
            ordem_hier.append((cod, nome))
        nomes = dict(ordem_hier)
        for cod in self.ordem:
            r = self.registros[cod]
            r["pai"] = pais.get(cod)
            r["nomeIndice"] = nomes.get(cod)
            if cod not in pais:
                self.aviso(f"Registro {cod} detalhado mas ausente da hierarquia")
            if [c["n"] for c in r["campos"]] != list(range(1, len(r["campos"]) + 1)):
                if r["campos"] and r["campos"][-1]["n"] == len(r["campos"]):
                    self.aviso(f"{cod}: numeração dos campos no PDF fora de sequência "
                               f"({[c['n'] for c in r['campos']]}); renumerado pela posição")
                    for i, c in enumerate(r["campos"], start=1):
                        if c["n"] != i:
                            c["nDocumento"] = c["n"]
                            c["n"] = i
            esperado = 1
            for c in r["campos"]:
                if c["n"] != esperado:
                    self.aviso(f"{cod}: numeração de campos salta de {esperado - 1} para {c['n']}")
                esperado = c["n"] + 1
                if c["tipo"] not in TIPOS:
                    self.aviso(f"{cod}.{c['n']} ({c['nome']}): tipo '{c['tipo']}' desconhecido")
                if not re.fullmatch(r"\d+|-|", c["tam"]):
                    self.aviso(f"{cod}.{c['n']} ({c['nome']}): tamanho '{c['tam']}' inesperado")
            if not r["texto"]:
                del r["texto"]
        for t in self.tabelas.values():
            t.pop("_pares", None)
            if not t["valores"]:
                self.aviso(f"Tabela {t['numero']} ({t['titulo']}) sem valores extraídos")
        for cod, _ in ordem_hier:
            if cod not in self.registros:
                self.aviso(f"Registro {cod} está na hierarquia mas não tem detalhamento")
        return {
            "versao": self.versao,
            "ordemHierarquia": [c for c, _ in ordem_hier],
            "registros": self.registros,
            "tabelas": self.tabelas,
            "avisos": self.avisos,
        }


def extrair(paginas):
    m = Montador()
    for pn, pg in enumerate(paginas, start=1):
        m.pagina(pg, pn)
    return m.finaliza()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    cache = None
    if "--cache" in args:
        i = args.index("--cache")
        cache = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    pdf_path, saida = args
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            paginas = json.load(f)
    else:
        paginas = ler_pdf(pdf_path)
        if cache:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(paginas, f, ensure_ascii=False)
    dados = extrair(paginas)
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    print(f"Versão {dados['versao']}: {len(dados['registros'])} registros, "
          f"{len(dados['tabelas'])} tabelas, {len(dados['avisos'])} avisos")
    for a in dados["avisos"]:
        print("  -", a)
