"""
Gera a especificação de validação (spec/leiaute_XXX.json) a partir do JSON extraído do PDF.

Uso:
    python tools/gerar_regras.py spec/leiaute_204.raw.json spec/leiaute_204.json

Entradas adicionais (mesma pasta do .raw.json):
    correcoes_XXX.json  - correções manuais de lacunas do PDF
    semantica_XXX.json  - campos que representam "cancelado", "operação", "espécie", "data"...
    regras_manuais_XXX.json (opcional) - regras escritas à mão, somadas às automáticas

Cada tópico ("●") da coluna Descrição/Observação recebe um status:
    regra     -> convertido em validação automática
    contexto  -> convertido, mas depende de parâmetros da empresa (UF, regime...) informados na tela
    info      -> texto descritivo, não exige validação
    pendente  -> contém uma exigência que o gerador ainda não sabe converter
"""
import json
import os
import re
import sys
import unicodedata
from datetime import date

# ------------------------------------------------------------------ texto


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s):
    """minúsculas, sem acento, aspas e espaços padronizados"""
    s = sem_acento(s or "").lower()
    s = re.sub(r"[“”\"'‘’`´]", '"', s)
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def norm_nome(s):
    return re.sub(r"[^a-z0-9]", "", norm(s))


MESES = {"jan": 1, "janeiro": 1, "fev": 2, "fevereiro": 2, "mar": 3, "marco": 3, "abr": 4, "abril": 4,
         "mai": 5, "maio": 5, "jun": 6, "junho": 6, "jul": 7, "julho": 7, "ago": 8, "agosto": 8,
         "set": 9, "setembro": 9, "out": 10, "outubro": 10, "nov": 11, "novembro": 11, "dez": 12, "dezembro": 12}

UFS = {"acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
       "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso do sul": "MS",
       "mato grosso": "MT", "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
       "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN", "rio grande do sul": "RS",
       "rondonia": "RO", "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO"}


def data_iso(txt):
    """'01 de janeiro de 2014' | '01/01/2025' | 'jan/2026' | '01.01.2019' -> '20140101'"""
    t = norm(txt)
    m = re.search(r"(\d{1,2}) de ([a-z]+) de (\d{4})", t)
    if m and m.group(2) in MESES:
        return f"{m.group(3)}{MESES[m.group(2)]:02d}{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t)
    if m:
        return f"{m.group(3)}{int(m.group(2)):02d}{int(m.group(1)):02d}"
    m = re.search(r"\b([a-z]{3,9})[/ ](?:de )?(\d{4})", t)
    if m and m.group(1) in MESES:
        return f"{m.group(2)}{MESES[m.group(1)]:02d}01"
    return None


# ------------------------------------------------------------------ gerador


class Gerador:
    def __init__(self, raw, correcoes, semantica, manuais):
        self.raw = raw
        self.cor = correcoes or {}
        self.sem = semantica or {"documentos": {}, "contexto": {}}
        self.man = manuais or {}
        self.regs = raw["registros"]
        self.avisos = []
        self.estat = {"regra": 0, "contexto": 0, "info": 0, "pendente": 0}
        t10 = raw["tabelas"].get("10", {"valores": []})
        self.especies = {v["valor"].upper() for v in t10["valores"]} | {"NFSE", "CTEOS", "NF3E", "NFAG", "NFGAS", "NF"}

    # ---------------------------------------------------------- correções
    def aplica_correcoes(self):
        for c in self.cor.get("campos", []):
            reg = self.regs[c["registro"]]
            campo = next(x for x in reg["campos"] if x["n"] == c["n"])
            for k in ("tam", "tipo", "nome"):
                if k in c:
                    campo[k] = c[k]
            campo["correcao"] = c["origem"]
        for c in self.cor.get("camposInseridos", []):
            reg = self.regs[c["registro"]]
            if any(x["n"] == c["campo"]["n"] for x in reg["campos"]):
                continue
            i = next(i for i, x in enumerate(reg["campos"]) if x["n"] == c["depoisDe"])
            novo = dict(c["campo"], correcao=c["origem"])
            reg["campos"].insert(i + 1, novo)

    # ---------------------------------------------------------- hierarquia
    def ancestrais(self, cod):
        out = []
        p = self.regs[cod].get("pai")
        while p:
            out.append(p)
            p = self.regs[p].get("pai") if p in self.regs else None
        return out

    def documento(self, cod):
        """registro-documento (raiz com semântica) ao qual cod pertence"""
        for c in [cod] + self.ancestrais(cod):
            if c in self.sem["documentos"]:
                return c
        return None

    def campo_por_nome(self, cod, nome):
        alvo = norm_nome(nome)
        if not alvo:
            return None
        for c in self.regs[cod]["campos"]:
            if norm_nome(c["nome"]) == alvo:
                return c["n"]
        for c in self.regs[cod]["campos"]:
            nn = norm_nome(c["nome"])
            if nn and (nn.startswith(alvo) or alvo.startswith(nn)) and min(len(nn), len(alvo)) >= 4:
                return c["n"]
        return None

    def registro_ref(self, cod, ref, nome=None):
        """registro citado ('do Registro PNM'): o próprio, um ancestral ou CAB.
        Se o leiaute citar um registro que não é ancestral (cópia de outro registro), usa o
        ancestral mais próximo que tenha o campo com esse nome."""
        ref = ref.upper()
        if ref == cod or ref in self.ancestrais(cod) or ref == "CAB":
            return ref
        if nome:
            for a in self.ancestrais(cod):
                if self.campo_por_nome(a, nome):
                    return a
        return None

    RE_PREF_VALOR = re.compile(r"^(?:valor|vr|vl)(?:do|da|de|dos|das)?")

    def campo_nome_estrito(self, cod, nome, ancestrais=True):
        """(registro, nº) do campo cujo nome normalizado é igual a 'nome' (aceita 'valor do X' ~ 'X')"""
        def var(x):
            return {x, re.sub(r"^(?:valor|vr|vl)", "", x), self.RE_PREF_VALOR.sub("", x)}
        alvo = norm_nome(nome)
        variantes = var(alvo) | {re.sub(r"^(?:o|a)(?=[a-z]{4})", "", alvo)}
        variantes = {v for v in variantes if len(v) >= 3}
        for reg in [cod] + (self.ancestrais(cod) if ancestrais else []):
            for c in self.regs[reg]["campos"]:
                if var(norm_nome(c["nome"])) & variantes:
                    return reg, c["n"]
        return None, None

    def codigo_por_palavra(self, reg, n, palavra):
        """'Judicial' -> '2' a partir de itens '"2" - Judicial' na descrição do campo"""
        c = next((x for x in self.regs[reg]["campos"] if x["n"] == n), None)
        if not c:
            return None
        txt = norm(" ; ".join(c.get("descricao", [])))
        m = re.search(r'"?([0-9a-z]{1,4})"?\s*(?:-|=)\s*' + re.escape(norm(palavra)) + r"\b", txt)
        return m.group(1).upper() if m else None

    def cond_nome(self, cod, nome, registro=None):
        """condição-base {campo[, registro]} para um campo citado pelo nome"""
        if registro:
            n = self.campo_por_nome(registro, nome)
            if not n:
                return None
            return {"campo": n} if registro == cod else {"campo": n, "registro": registro}
        reg, n = self.campo_nome_estrito(cod, nome)
        if not n:
            return None
        return {"campo": n} if reg == cod else {"campo": n, "registro": reg}

    def campos_por_padrao(self, cod, padrao):
        """[(registro, nº)] dos campos (no registro ou ancestrais) cujo nome casa com o regex"""
        for reg in [cod] + self.ancestrais(cod):
            achados = [(reg, c["n"]) for c in self.regs[reg]["campos"] if re.search(padrao, norm(c["nome"]))]
            if achados:
                return achados
        return []

    @staticmethod
    def palavras(txt):
        return {w for w in re.findall(r"[a-z0-9$%]{3,}", norm(txt)) if w not in ("campo", "valor", "dos", "das", "codigo")}

    def confere_num(self, reg, n, citado):
        """O leiaute às vezes cita o nº errado ('campo 9 (Quantidade)' quando Quantidade é o 8).
        Mantém n se o nome citado combina com o campo; senão procura um único campo com esse nome."""
        if not citado:
            return n
        citado = citado.strip(' ()"')
        campos = self.regs[reg]["campos"]
        atual = next((c for c in campos if c["n"] == n), None)
        pc = self.palavras(citado)
        if not pc:
            return n
        if atual and (pc <= self.palavras(atual["nome"]) or norm_nome(citado) in norm_nome(atual["nome"])):
            return n
        alvo = norm_nome(citado)
        iguais = [c["n"] for c in campos if norm_nome(c["nome"]) == alvo]
        if len(iguais) == 1:
            return iguais[0]
        cand = [c["n"] for c in campos if pc <= self.palavras(c["nome"])]
        return cand[0] if len(cand) == 1 else None

    def base(self, cod, reg, n):
        return {"campo": n} if reg == cod else {"campo": n, "registro": reg}

    # ---------------------------------------------------------- átomos de condição
    def atomo_semantico(self, cod, conceito, valor=None):
        doc = self.documento(cod)
        if not doc:
            return None
        s = self.sem["documentos"][doc].get(conceito)
        if s is None:
            return None
        reg = None if doc == cod else doc
        if conceito == "cancelado":
            c = {"campo": s["campo"], "op": "in", "valores": s["valores"]}
        elif conceito == "operacao":
            if "fixo" in s:
                return {"const": s["fixo"] == valor}
            c = {"campo": s["campo"], "op": "=", "valor": s["entrada" if valor == "E" else "saida"]}
        elif conceito == "especie":
            c = {"campo": s, "op": "in", "valores": valor}
        elif conceito == "proprio":
            c = {"campo": s, "op": "=", "valor": "S" if valor else "N"}
        elif conceito == "data":
            c = {"campo": s, "op": valor[0], "valor": valor[1]}
        elif isinstance(s, dict) and "campo" in s:
            c = {"campo": s["campo"], "op": "in", "valores": s["valores"]}
        else:
            return None
        if reg:
            c["registro"] = reg
        return c

    def valores_citados(self, txt):
        """'"1 - IBS Estadual", "2 - IBS Municipal" ou "4 - CBS"' -> ['1','2','4']; 'S' -> ['S'];
        '03, 05, 50 a 56 e 75' -> ['03','05','50',...,'56','75']"""
        t = txt.strip().rstrip(".;,: ")
        itens = re.findall(r'"\s*([^"]+?)\s*"', t)
        if not itens:
            itens = re.split(r",|\bou\b|\be\b|;|(?<=\d)e(?=\s)", t)
        vals = []
        for it in itens:
            it = it.strip()
            if not it:
                continue
            m = re.match(r"^(\d{1,6}) a (\d{1,6})$", it)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if b < a or b - a > 200:
                    return None
                vals += [str(x).zfill(len(m.group(1))) for x in range(a, b + 1)]
                continue
            m = re.match(r"^([0-9a-z]{1,9})(?:\s*[-=]\s*.*|\s*\(.*\)?)?$", it, re.I)
            if not m:
                return None
            vals.append(m.group(1).upper())
        return vals or None

    def cond_campo_num(self, cod, n, nome, registro=None):
        alvo = registro or cod
        if alvo not in self.regs or not any(c["n"] == n for c in self.regs[alvo]["campos"]):
            return None
        d = {"campo": n}
        if registro and registro != cod:
            d["registro"] = registro
        return d

    def parse_atomo(self, cod, t):
        """Tenta reconhecer UMA condição no início de t (já normalizado).
        Retorna (cond, resto, eh_contexto) ou None."""
        pats = []

        # documento cancelado
        m = re.match(r"^(?:o |este |todos os )?(?:documento|nota|bilhete|registro)s? (?:for|seja|forem|estiver|esteja)m? cancelad[oa]s?", t)
        if m:
            c = self.atomo_semantico(cod, "cancelado")
            if c is None:
                return ({"semCampo": "cancelado"}, t[m.end():], False)
            return (c, t[m.end():], False)
        # operação entrada/saída
        m = re.match(r"^(?:a |o tipo de )?(?:operacao|operacoes)(?: do documento)? (?:for |seja |e )?(?:de |uma )?(entrada|saida)s?", t)
        if not m:
            m = re.match(r"^(?:nas |as |para as |para |em )?(?:operacoes|operacao|notas|documentos|situacao|lancamentos) de (entrada|saida)s?", t)
        if m:
            c = self.atomo_semantico(cod, "operacao", "E" if m.group(1) == "entrada" else "S")
            return (c, t[m.end():], False) if c else None
        # espécie
        m = re.match(r'^(?:a |o )?(?:especie|tipo)(?: do documento)? (?:for |=|seja |igual a )?\s*', t)
        if m and m.group(0).startswith(("a especie", "especie", "o especie")) or (m and "especie" in m.group(0)):
            resto, vals = t[m.end():], []
            while True:
                mm = re.match(r'"?([a-z0-9-]{2,6})"?(?:\s*\([^)]*\)|\s*"[^"]*"(?=\s*(?:$|[,.;]| e | ou )))?', resto)
                if not mm or mm.group(1).replace("-", "").upper() not in self.especies:
                    break
                vals.append(mm.group(1).replace("-", "").upper())
                resto = resto[mm.end():]
                ms = re.match(r'\s*(?:,|ou|e)\s*(?="?[a-z0-9-]{2,6}"?(?:$|[\s,.;("]))', resto)
                if not ms:
                    break
                prox = re.match(r'\s*(?:,|ou|e)\s*"?([a-z0-9-]{2,6})', resto)
                if not prox or prox.group(1).replace("-", "").upper() not in self.especies:
                    break
                resto = resto[ms.end():]
            if vals:
                c = self.atomo_semantico(cod, "especie", vals)
                mo = re.match(r"^ de (entrada|saida)s?", resto)
                if c and mo:
                    o = self.atomo_semantico(cod, "operacao", "E" if mo.group(1) == "entrada" else "S")
                    if not o:
                        return None
                    resto = resto[mo.end():]
                    if "const" in o:
                        return ({"const": False}, resto, False) if not o["const"] else (c, resto, False)
                    c = {"e": [c, o]}
                return (c, resto, False) if c else None
        m = re.match(r'^(?:para )?(?:a |o )?"?(nfe|nfce|nfs-e|nfse|cteos|cte|bpe|bpeta|bpetm|nfcom|nfag|nfgas)"?(?=$|[.,;]| e | ou )', t)
        if m:
            c = self.atomo_semantico(cod, "especie", [m.group(1).upper().replace("-", "")])
            return (c, t[m.end():], False) if c else None
        # data do documento
        m = re.match(r"^(?:para )?(?:lancamentos |documentos )?(?:emitidos )?a partir de (\d{1,2} de [a-z]+ de \d{4}|\d{1,2}[/.]\d{1,2}[/.]\d{4}|[a-z]{3,9}/\d{4})", t)
        if m:
            d = data_iso(m.group(1))
            c = self.atomo_semantico(cod, "data", (">=", d)) if d else None
            return (c, t[m.end():], False) if c else None
        m = re.match(r"^(?:a )?data do documento (?:for )?(inferior|anterior|superior|posterior) a (\d{1,2}[/.]\d{1,2}[/.]\d{4})", t)
        if m:
            d = data_iso(m.group(2))
            op = "<" if m.group(1) in ("inferior", "anterior") else ">"
            c = self.atomo_semantico(cod, "data", (op if op == "<" else ">=", d)) if d else None
            return (c, t[m.end():], False) if c else None

        # contexto: regime
        m = re.match(r"^(?:para )?(?:as )?empresas? (?:com )?regime de tributacao (?:\"|)?lucro real ou (?:lucro )?presumido(?:\"|)?", t)
        if m:
            return ({"ctx": "regime", "op": "in", "valores": ["LR", "LP"]}, t[m.end():], True)
        m = re.match(r"^(?:a |o )?(?:empresa|estabelecimento) (?:for )?(?:contribuinte|participante|participar|optante)(?: do| no)? simples nacional", t)
        if not m:
            m = re.match(r"^(?:para )?(?:empresas|estabelecimentos) participantes do simples nacional", t)
        if m:
            return ({"ctx": "regime", "op": "=", "valor": "SN"}, t[m.end():], True)
        m = re.match(r"^(?:a )?empresa (?:for )?contribuinte do ipi", t)
        if m:
            return ({"ctx": "contribuinteIPI", "op": "=", "valor": "S"}, t[m.end():], True)
        m = re.match(r"^(?:para )?(?:os )?estabelecimentos? que inform(?:em|a) codigos contabeis", t)
        if m:
            return ({"ctx": "informaContabil", "op": "=", "valor": "S"}, t[m.end():], True)
        m = re.match(r"^(?:para )?(?:os )?(?:estabelecimentos?|empresas?)(?: da uf)?(?: do| de| da)? (?:estado )?(?:do |de |da )?([a-z ]+?)(?: \(([a-z]{2})\))?(?=$|[,.;]| e | ou | com )", t)
        if m and (m.group(1).strip() in UFS or m.group(2)):
            uf = (m.group(2) or UFS.get(m.group(1).strip(), "")).upper()
            if uf:
                return ({"ctx": "uf", "op": "=", "valor": uf}, t[m.end():], True)
        m = re.match(r"^(?:para )?(?:os )?estados?:? ([a-z ,]+?)(?:\.|$)", t)
        if m:
            ufs = [UFS.get(x.strip()) for x in re.split(r",| e ", m.group(1)) if x.strip()]
            if ufs and all(ufs):
                return ({"ctx": "uf", "op": "in", "valores": ufs}, t[m.end():], True)

        # registro CAB campo 9 (...) estiver informado "S" / valor igual a "N"
        m = re.match(r'^(?:se |quando )?(?:informado )?no registro ([a-z]{3}) campo (\d+)(?: \([^)]*\))?,? (?:estiver informado|valor igual a|for igual a|for) "?([a-z0-9]+)"?', t)
        if m:
            reg = self.registro_ref(cod, m.group(1))
            if reg:
                base = self.cond_campo_num(cod, int(m.group(2)), None, reg)
                if base:
                    base.update(op="=", valor=m.group(3).upper())
                    return (base, t[m.end():], False)
            return None

        # campo N (Nome) valor igual a X / diferente de X  |  no campo N os seguintes valores: ...
        m = re.match(r'^(?:informado )?(?:no |o )?campo (\d+) ?(\([^)]*(?:\([^)]*\))?[^)]*\)|"[^"]*"| - "[^"]*"|, "[^"]*")?(?: do registro ([a-z]{3}))?,? (?:valor |estiverem |estiver |for )?(igual a|igual|diferente de|for igual a|for diferente de|=|informados? os seguintes valores ?:?|informado o seguinte valor:?|os seguintes valores:?|preenchido com:?|for(?! preenchid| informad)|(?<=valor )(?="))\s*([^;]*?)(?=$|\.$|;| e (?:o |se |quando )|, e |, o valor)', t)
        if m:
            reg = self.registro_ref(cod, m.group(3)) if m.group(3) else None
            n = self.confere_num(reg or cod, int(m.group(1)), m.group(2))
            base = self.cond_campo_num(cod, n, None, reg) if n else None
            vals = self.valores_citados(m.group(5)) if base else None
            if base and vals:
                base.update(op="notin" if "diferente" in m.group(4) else "in", valores=vals)
                return (base, t[m.end():], False)
        # campo N "Nome" estiver/for preenchido|informado ; não for informado
        m = re.match(r'^(?:se |quando )?(?:informado )?(?:o )?campo (\d+) ?(\([^)]*\)|"[^"]*")?(?: do registro ([a-z]{3}))? (?:estiver|for) (preenchid[oa]|informad[oa]|preenchendo)(?! com)', t)
        if m:
            reg = self.registro_ref(cod, m.group(3)) if m.group(3) else None
            if m.group(3) and not reg:
                return None
            n = self.confere_num(reg or cod, int(m.group(1)), m.group(2))
            base = self.cond_campo_num(cod, n, None, reg) if n else None
            if base:
                base["op"] = "preenchido"
                return (base, t[m.end():], False)
            return None
        # campo Nome (Campo N) for igual 0 (zero)
        m = re.match(r'^(?:o )?campo [^()]+ \(campo (\d+)\) (?:for )?igual (?:a )?(\d+)(?: \(zero\))?', t)
        if m:
            base = self.cond_campo_num(cod, int(m.group(1)), None)
            if base:
                base.update(op="=", valor=m.group(2))
                return (base, t[m.end():], False)
            return None
        # o campo "Nome" [do Registro XXX] for "X - ..."[, "Y - ..." ou "Z"] | for informado | não for informado
        m = re.match(r'^(?:ou,? )?(?:se )?(?:o )?campo "([^"]+)"(?: do registro ([a-z]{3}))? (nao )?(?:for|estiver) (informad[oa]|preenchid[oa]|igual a |)', t)
        if m:
            reg = cod
            if m.group(2):
                reg = self.registro_ref(cod, m.group(2), m.group(1))
                if not reg:
                    return None
            n = self.campo_por_nome(reg, m.group(1))
            if not n:
                return None
            base = {"campo": n}
            if reg != cod:
                base["registro"] = reg
            resto = t[m.end():]
            if m.group(4).strip():
                base["op"] = "vazio" if m.group(3) else "preenchido"
                return (base, resto, False)
            mv = re.match(r'^((?:"[^"]+"\s*(?:,|ou|e)?\s*)+)', resto)
            if not mv:
                return None
            vals = self.valores_citados(mv.group(1))
            if not vals:
                return None
            base.update(op="notin" if m.group(3) else "in", valores=vals)
            return (base, resto[mv.end():], False)

        # "Nome do Campo" for informado "S"
        m = re.match(r'^(?:o campo )?"([^"]+)" (?:for|estiver) (?:informado|preenchido)(?: com)? (?:igual a )?"([0-9a-z]{1,6})"', t)
        if m:
            n = self.campo_por_nome(cod, m.group(1))
            if n:
                return ({"campo": n, "op": "=", "valor": m.group(2).upper()}, t[m.end():], False)
            return None

        # o 4º caractere do campo 59 "Nome" for diferente de "7" | "1 e 2"
        m = re.match(r'^(?:o )?(\d)[ºo°]? caractere do campo (\d+) ?(?:"[^"]*"|\([^)]*\))? (?:for )?(diferente de|igual a) ((?:"[^"]+"|[0-9a-z]+)(?:\s*(?:,|e|ou)\s*(?:"[^"]+"|[0-9a-z]+))*)', t)
        if m:
            base = self.cond_campo_num(cod, int(m.group(2)), None)
            vals = re.findall(r"[0-9a-z]", m.group(4).replace(" e ", " ").replace(" ou ", " "))
            if base and vals:
                base.update(op="posicaoNotIn" if "diferente" in m.group(3) else "posicaoIn", pos=int(m.group(1)), valores=[v.upper() for v in vals])
                return (base, t[m.end():], False)
            return None

        # produtos / serviços (registros de detalhamento com campo "1 - Produtos; 2 - Serviços")
        m = re.match(r"^(?:para )?(?:as |os )?(?:valores de |informacoes de )?(produtos|servicos)(?=$|[.,;]| e | ou )", t)
        if m:
            for c in self.regs[cod]["campos"]:
                d = norm(" ".join(c.get("descricao", [])))
                if re.search(r"1 - informac\w* d[eo]s? produtos", d) and re.search(r"2 - informac\w* d[eo]s? servicos", d):
                    return ({"campo": c["n"], "op": "=", "valor": "1" if m.group(1) == "produtos" else "2"}, t[m.end():], False)
            return None

        # CST do COFINS/PIS: lista de códigos (aceita faixas)
        m = re.match(r'^(?:informado para |for |o |a )*csts? (?:do |da )?(cofins e pis|cofins|pis|ipi)(?: for| forem)?:? ((?:\d{2,3}|\d{2} a \d{2})(?:\s*(?:,|e|ou)\s*(?:\d{2,3}|\d{2} a \d{2}))*)', t)
        if m:
            vals = self.valores_citados(m.group(2))
            conds = []
            for trib in m.group(1).split(" e "):
                achados = self.campos_por_padrao(cod, r"^cst " + trib + r"$|^cst " + trib + r" |^cst-" + trib)
                if len(achados) != 1 or not vals:
                    return None
                conds.append(dict(self.base(cod, *achados[0]), op="in", valores=vals))
            return (conds[0] if len(conds) == 1 else {"e": conds}, t[m.end():], False)
        # CST/CSOSN ICMS: x00, x10, x101... (CSTB com 2 dígitos; CSOSN com 3)
        m = re.match(r'^(?:o )?(?:cstb? ?/ ?csosn|cstb|cst)(?: do)? (?:icms )?(?:for )?(?:igual a )?((?:x?\d{2,3})(?:\s*(?:,|e|ou)\s*x?\d{2,3})*)', t)
        if m and ("x" in m.group(0) or "icms" in m.group(0) or "cstb" in m.group(0)):
            codigos = re.findall(r"x?(\d{2,3})", m.group(1))
            cstb = self.campos_por_padrao(cod, r"^cstb$|^cst b$|^cst\(icms\)$|^cst icms$")
            csosn = self.campos_por_padrao(cod, r"^csosn$")
            partes = []
            d2 = [c for c in codigos if len(c) == 2]
            d3 = [c for c in codigos if len(c) == 3]
            if d2:
                if len(cstb) != 1:
                    return None
                partes.append(dict(self.base(cod, *cstb[0]), op="in", valores=d2))
            if d3:
                if len(csosn) != 1:
                    return None
                partes.append(dict(self.base(cod, *csosn[0]), op="in", valores=d3))
            if not partes:
                return None
            return (partes[0] if len(partes) == 1 else {"ou": partes}, t[m.end():], False)

        # conceitos do documento declarados em semantica_XXX.json
        m = re.match(r'^(?:(?:se tratar de|for) )?(?:o |um |uma )?cte simplificado', t)
        if m:
            c = self.atomo_semantico(cod, "simplificado")
            return (c, t[m.end():], False) if c else None
        m = re.match(r'^(?:se tratar de )?(?:um |uma )?(?:nfcom|nfgas|nfag|nf3e|bp-?e|ct-?e) de substituicao', t)
        if m:
            c = self.atomo_semantico(cod, "substituicao")
            return (c, t[m.end():], False) if c else None
        m = re.match(r'^(?:o )?documento (?:for )?(?:emitido pelo proprio estabelecimento|proprio)', t)
        if m:
            c = self.atomo_semantico(cod, "proprio", True)
            return (c, t[m.end():], False) if c else None
        m = re.match(r'^(?:para )?(?:as |a )?(?:operacao de )?(?:notas fiscais eletronicas|nfe|nf-e) (?:de )?(entrada|saida)s?(?= ou entrada documento proprio|$|[.,;]| e )', t)
        if m:
            e = self.atomo_semantico(cod, "especie", ["NFE"])
            o = self.atomo_semantico(cod, "operacao", "E" if m.group(1) == "entrada" else "S")
            resto = t[m.end():]
            if not e or not o or "const" in o:
                return None
            if resto.startswith(" ou entrada documento proprio"):
                b = self.atomo_semantico(cod, "proprio", True)
                if not b:
                    return None
                o = {"ou": [o, b]}
                resto = resto[len(" ou entrada documento proprio"):]
            return ({"e": [e, o]}, resto, False)
        m = re.match(r'^(?:a )?operacao for (?:de )?saida ou entrada \(documento proprio\)', t) or             re.match(r'^(?:para )?(?:as )?(?:nfe|nf-e|notas fiscais eletronicas) de saidas? e entradas? \(?documento proprio\)?', t)
        if m:
            a = self.atomo_semantico(cod, "operacao", "S")
            b = self.atomo_semantico(cod, "proprio", True)
            if not a or not b or "const" in a:
                return None
            c = {"ou": [a, b]}
            if "nf" in m.group(0)[:30]:
                e = self.atomo_semantico(cod, "especie", ["NFE"])
                if not e:
                    return None
                c = {"e": [e, c]}
            return (c, t[m.end():], False)
        m = re.match(r'^(?:os )?documentos de saida e documento proprio(?: \(entrada\))?', t)
        if m:
            a = self.atomo_semantico(cod, "operacao", "S")
            b = self.atomo_semantico(cod, "proprio", True)
            return ({"ou": [a, b]}, t[m.end():], False) if a and b and "const" not in a else None
        m = re.match(r'^(?:o )?participante for do exterior', t)
        if m:
            doc = self.documento(cod)
            s = self.sem["documentos"].get(doc, {}).get("participante") if doc else None
            if s:
                c = {"campo": s, "op": "existeExterior"}
                if doc != cod:
                    c["registro"] = doc
                return (c, t[m.end():], False)
            return None

        # 'qualquer campo do grupo ALC/ZFM for informado' -> algum campo do bloco preenchido
        m = re.match(r'^qualquer campo do grupo ([a-z0-9/]+) for (?:informado|preenchido)', t)
        if m:
            tok = m.group(1)
            idx = [c["n"] for c in self.regs[cod]["campos"] if tok in norm(c["nome"])]
            if len(idx) >= 2:
                bloco = [c["n"] for c in self.regs[cod]["campos"] if min(idx) <= c["n"] <= max(idx)]
                return ({"ou": [{"campo": n, "op": "preenchido", "ignorarZero": True} for n in bloco], "grupo": bloco}, t[m.end():], False)
            return None
        # 'a UF não for do estado do Ceará'
        m = re.match(r'^(?:a )?uf (nao )?for (?:do |de |da )?(?:estado )?(?:do |de |da )?([a-z ]+?)(?=$|[.,;]| e | ou )', t)
        if m and UFS.get(m.group(2).strip()):
            return ({"ctx": "uf", "op": "!=" if m.group(1) else "=", "valor": UFS[m.group(2).strip()]}, t[m.end():], True)

        # parâmetros da empresa (histórico no Fortes)
        ctxs = [
            (r'^(?:cuj[oa] )?(?:a )?empresa tenha recolhimento do cofins e pis pelo regime da nao cumulatividade(?: e pis/cofins cumulativo e nao cumulativo)?', ("pisCofins", "in", ["N", "CN"])),
            (r'^(?:com )?recolhimento (?:do )?cofins e pis nao cumulativo ou cumulativo e nao cumulativo', ("pisCofins", "in", ["N", "CN"])),
            (r'^(?:o )?regime tributario da empresa for nao cumulativ[oa] ou cumulativo e nao cumulativ[oa]', ("pisCofins", "in", ["N", "CN"])),
            (r'^(?:o )?regime tributario da empresa for cumulativ[oa] ou cumulativo e nao cumulativ[oa]', ("pisCofins", "in", ["C", "CN"])),
            (r'^(?:- )?tributacao "lucro real"', ("regime", "=", "LR")),
            (r'^(?:- )?"pis/cofins cumulativos e nao-? ?cumulativos"', ("pisCofins", "=", "CN")),
            (r'^(?:- )?"apura retencoes de pis/cofins cumulativos e nao-? ?cumulativos ?"', ("apuraRetencoes", "=", "S")),
            (r'^estiver marcado no historico da empresa a opcao "apura retencoes de pis/cofins cumulativos e nao-? ?cumulativos ?"', ("apuraRetencoes", "=", "S")),
            (r'^(?:as )?empresas que calcul(?:em|am) valores de contribuicao previdenciaria', ("cprb", "=", "S")),
            (r'^tiver contabilizacao com conta de debito/credito com a opcao de "conta informada na baixa"', ("contaNaBaixa", "=", "S")),
            (r'^(?:o sistema estiver configurado para|a empresa) informar valores do icms no rmd', ("icmsRMD", "=", "S")),
            (r'^(?:a )?(?:empresa|estabelecimento) (?:nao )informar itens(?: dos documentos(?: fiscais)?)?', ("informaItens", "=", "N")),
            (r'^(?:a )?(?:empresa|estabelecimento) informar itens(?: dos documentos(?: fiscais)?)?', ("informaItens", "=", "S")),
            (r'^(?:para )?(?:o )?regime de tributacao normal, outros ou especial', ("regime", "notin", ["SN"])),
            (r'^(?:o )?estabelecimento nao participar do simples nacional', ("regime", "notin", ["SN"])),
            (r'^(?:para )?(?:a )?empresa e estabelecimentos do simples', ("regime", "=", "SN")),
            (r'^(?:a )?empresa (?:for )?do simples nacional', ("regime", "=", "SN")),
            (r'^(?:a )?empresa nao for contribuinte do ipi', ("contribuinteIPI", "=", "N")),
            (r'^(?:para )?empresas contribuintes do ipi', ("contribuinteIPI", "=", "S")),
            (r'^(?:para )?(?:as )?empresas com regime de tributacao lucro real(?! ou)', ("regime", "=", "LR")),
        ]
        for rx, (k, op, v) in ctxs:
            m = re.match(rx, t)
            if m:
                c = {"ctx": k, "op": op}
                c["valores" if isinstance(v, list) else "valor"] = v
                return (c, t[m.end():], True)
        # UF: 'a empresa possuir estabelecimentos de Pernambuco', 'o estado da Bahia (BA)', 'estabelecimentos das UFs: PA, ES e RN'
        m = re.match(r'^(?:a )?empresa possuir estabelecimentos? (?:de|do|da|em) ([a-z ]+?)(?=$|[,.;]| e | ou )', t)
        if not m:
            m = re.match(r'^(?:para )?(?:o )?estado (?:de |do |da )?([a-z ]+?)(?: \(?-? ?([a-z]{2})\)?)?(?=$|[,.;]| e | ou )', t)
        if not m:
            m = re.match(r'^(?:se tratar de )?(?:o )?estabelecimento (?:for )?(?:do|de|da) (?:estado (?:do |de |da )?)?([a-z ]+?)(?=$|[,.;]| e | ou )', t)
        if m:
            nome = m.group(1).strip()
            uf = UFS.get(nome) or (m.group(2).upper() if m.lastindex and m.lastindex >= 2 and m.group(2) else None)
            if uf:
                return ({"ctx": "uf", "op": "=", "valor": uf}, t[m.end():], True)
        m = re.match(r'^(?:para )?(?:os )?estabelecimentos das ufs:? ([a-z]{2}(?:\s*(?:,|e)\s*[a-z]{2})*)', t)
        if m:
            return ({"ctx": "uf", "op": "in", "valores": [x.upper() for x in re.findall(r"[a-z]{2}", m.group(1))]}, t[m.end():], True)

        # data: 'a partir do ano de 2023', 'a Data da Entrada for superior a 01/07/2024'
        m = re.match(r'^(?:para )?(?:documentos? )?a partir do ano de (\d{4})', t)
        if m:
            c = self.atomo_semantico(cod, "data", (">=", m.group(1) + "0101"))
            return (c, t[m.end():], False) if c else None
        m = re.match(r'^(?:a )?([a-z][a-z /]{2,40}?) (?:for )?(superior|inferior|maior|menor|posterior|anterior) (?:ou igual )?a (\d{1,2}/\d{1,2}/\d{4})', t)
        if m:
            b = self.cond_nome(cod, m.group(1))
            d = data_iso(m.group(3))
            if b and d:
                op = ">=" if m.group(2) in ("superior", "maior", "posterior") else "<"
                b.update(op=op, valor=d)
                return (b, t[m.end():], False)

        # campo citado pelo nome (sem nº): preenchido / em branco / houver valor
        m = re.match(r'^(?:(?:tiver|houver) (?:valor (?:de |do |da )?)?|(?:do )?preenchimento do campo |preenchid[oa] (?:a |o )?)([a-z][a-z0-9 ./()-]{2,50}?)(?=$|[.,;]| e | ou )', t)
        if m:
            b = self.cond_nome(cod, m.group(1))
            if b:
                b.update(op="preenchido", ignorarZero=True)
                return (b, t[m.end():], False)
            return None
        m = re.match(r'^(?:o |a )?(?:campo )?([a-z][a-z0-9 ./()-]{2,50}?) (?:estiver|for) (preenchid[oa]|informad[oa]|em branco|maior que zero)', t)
        if m:
            b = self.cond_nome(cod, m.group(1))
            if b:
                b.update(op="vazio" if m.group(2) == "em branco" else "preenchido", ignorarZero=True)
                return (b, t[m.end():], False)
        # 'o Tipo do Processo for Judicial (Registro PAJ)' / 'tipo de isenção for 6 (...)'
        m = re.match(r'^(?:o |a )?([a-z][a-z0-9 ./-]{2,40}?) (?:for|seja) ([a-z]{3,20}|\d{1,3})(?: \([^)]*\))?(?: \(registro ([a-z]{3})\))?(?=$|[.,;]| e | ou )', t)
        if m:
            reg = self.registro_ref(cod, m.group(3), m.group(1)) if m.group(3) else None
            b = self.cond_nome(cod, m.group(1), reg)
            if b:
                alvo = b.get("registro", cod)
                val = m.group(2).upper() if m.group(2).isdigit() else self.codigo_por_palavra(alvo, b["campo"], m.group(2))
                if val:
                    b.update(op="=", valor=val)
                    return (b, t[m.end():], False)

        # campo citado pelo nome, sem aspas: 'Tipo for igual a 2 - Monofásico', 'Finalidade = 101 (...) (campo 23)'
        m = re.match(r'^(?:o )?(?:campo )?([a-z][a-z0-9 ./()-]{1,40}?) (?:for igual a|for|=|igual a) "?([0-9a-z]{1,6}(?: ou [0-9a-z]{1,6})*)"?(?: - [^,;.]*| \((?!campo)[^)]*\)| "[^"]*")*(?: \(campo (\d+)\))?', t)
        if m:
            n = int(m.group(3)) if m.group(3) else self.campo_por_nome(cod, m.group(1))
            if n and (not m.group(3) or self.cond_campo_num(cod, n, None)):
                nome_ok = norm_nome(next(c["nome"] for c in self.regs[cod]["campos"] if c["n"] == n))
                if m.group(3) or nome_ok == norm_nome(m.group(1)):
                    vals = [v.upper() for v in m.group(2).split(" ou ")]
                    return ({"campo": n, "op": "in", "valores": vals}, t[m.end():], False)
        return None

    def parse_condicao(self, cod, txt):
        """Converte o texto de uma condição em {e:[...]} / {ou:[...]} / átomo.
        Só aceita quando TODO o texto foi entendido."""
        t = norm(txt).strip(" .;:")
        atomos, conectores, contexto = [], [], False
        while t:
            t = re.sub(r"^(?:(?:quando|se tratar de|se|caso|sempre que|somente|apenas|para|e se|e quando|ou se|ou|for|em|nos|nas|com|cuj[oa])\s+)+", "", t)
            r = self.parse_atomo(cod, t)
            if r is None:
                return None, False
            c, t, ctx = r
            contexto = contexto or ctx
            atomos.append(c)
            t = t.strip(" .;:")
            if not t:
                break
            m = re.match(r"^(,?\s*e\s+|,?\s*ou\s+|,\s*|(?=(?:para|nos|nas|em|a partir|com|cuj[oa]|quando|se) ))", t)
            if not m:
                return None, False
            conectores.append("ou" if re.search(r"\bou\b", m.group(1)) else "e")
            t = t[m.end():]
        if not atomos:
            return None, False
        if any("semCampo" in a for a in atomos):
            # o registro não tem o campo que a condição cita (ex.: não há indicador de cancelamento)
            return {"semCampo": True}, False
        # constantes (ex.: operação fixa do registro)
        if len(set(conectores)) > 1:
            return None, False
        op = conectores[0] if conectores else "e"
        consts = [a for a in atomos if "const" in a]
        resto = [a for a in atomos if "const" not in a]
        if op == "e":
            if any(not a["const"] for a in consts):
                return {"const": False}, contexto
            if not resto:
                return {"const": True}, contexto
            return (resto[0] if len(resto) == 1 else {"e": resto}), contexto
        if any(a["const"] for a in consts):
            return {"const": True}, contexto
        if not resto:
            return {"const": False}, contexto
        return (resto[0] if len(resto) == 1 else {"ou": resto}), contexto

    # ---------------------------------------------------------- tópicos
    RE_OBRIG = re.compile(r"^(?:este )?campo (?:e |torna-se )?obrigatorio\.?$")
    RE_NAO_OBRIG = re.compile(r"^(?:este )?campo nao (?:e )?obrigatorio\.?$|^pode ficar em branco\.?$")

    def interpreta(self, cod, campo, texto, grupo):
        """Retorna (status, efeito). efeito é aplicado ao campo pelo chamador."""
        t = norm(texto)
        if not t:
            return "info", None

        # conteúdo fixo
        m = re.match(r'^texto fixo contendo "([^"]+)"', t)
        if m:
            return "regra", ("fixo", texto[texto.find('"') + 1: texto.rfind('"')] if '"' in texto else m.group(1).upper())
        if self.RE_OBRIG.match(t):
            return "regra", ("obrigatorio", True)
        if self.RE_NAO_OBRIG.match(t):
            return "info", None
        # casas decimais
        m = re.search(r"(?:informar )?(?:valores? )?com (\d) casas(?: decimais)?|\((\d) casas decimais\)|^(\d) casas decimais", t)
        if m:
            return "regra", ("dec", int(next(g for g in m.groups() if g)))
        # referência a outro registro
        m = re.search(r"o valor informado deve existir no campo (\d+) do registro ([a-z]{3})\b", t)
        if m:
            return "regra", ("referencia", {"registro": m.group(2).upper(), "campo": int(m.group(1))})
        m = re.search(r"\(campo (\d+) do registro ([a-z]{3})\)", t)
        if m and m.group(2).upper() in self.regs:
            return "regra", ("referencia", {"registro": m.group(2).upper(), "campo": int(m.group(1)), "nivel": "aviso"})
        # tabela de códigos
        m = re.search(r"(?:conforme|-|–)\s*(?:a\s+)?(?:\"?tabela\s+(\d{1,2})\b)", t)
        if m and not re.search(r"tabela \d+ do sped|tabela 4\.|tabela de pais|tabela da receita|tabela [a-z]", t):
            num = m.group(1).zfill(2)
            if num in self.raw["tabelas"] and num not in self.cor.get("tabelasIgnoradas", {}):
                return "regra", ("tabela", num)

        # textos puramente informativos que parecem exigências
        if re.match(r"^(?:devera|deve) ser informado com o valor da coluna|^neste campo devem ser informados os codigos de municipios", t):
            return "info", None
        if re.match(r"^(?:este |o )?campo nao deve(?:ra)? ser preenchido\.?$", t):
            return "regra", ("regra", {"tipo": "vazio", "quando": {"const": True}, "texto": texto})

        # remove o nome do próprio campo no início: 'Campo "Autoria da Ação" deve ser informado quando...'
        t = re.sub(r'^campo "[^"]+" (?=(?:deve|devera|so|somente|nao))', "campo ", t)
        t = re.sub(r"\s*e atender as validacoes acima\.?$", "", t)
        t = t.replace("tornasse", "torna-se")

        # somatório dos registros filhos
        r = self.regra_soma(cod, t, texto)
        if r is not None:
            return r
        # cálculo / comparação com outros campos do registro
        r = self.regra_calculo(cod, campo, t, texto)
        if r is not None:
            return r

        # "Para as operações de Saídas os valores válidos são: 01 a 09, 49 e 99"
        m = re.match(r"^(?:para|nas) (?:as )?operac(?:oes|ao) de (entradas?|saidas?),? (?:os )?valores (?:validos|permitidos|aceitos)(?: sao)?:? (.+)$", t)
        if m:
            vals = self.valores_citados(m.group(2))
            c = self.atomo_semantico(cod, "operacao", "E" if m.group(1).startswith("entrada") else "S")
            if vals and c:
                if "const" in c:
                    if not c["const"]:
                        return "naoaplica", None
                    c = None
                rg = {"tipo": "dominio", "valores": vals, "texto": texto}
                if c:
                    rg["quando"] = c
                else:
                    rg["quando"] = {"const": True}
                return "regra", ("regra", rg)
        # "Poderá ser informado tipo “2” apenas quando ..."
        m = re.match(r'^podera ser informad[oa] (?:o )?(?:tipo|valor|codigo) "?([0-9a-z]{1,4})"? (?:apenas|somente) (?:quando|se) (.*)$', t)
        if m:
            cond, ctx = self.parse_condicao(cod, m.group(2))
            if cond is None:
                return "pendente", None
            return ("contexto" if ctx else "regra"), ("regra", {"tipo": "proibido", "valores": [m.group(1).upper()],
                                                               "quando": {"nao": cond}, "texto": texto})
        # "Preencher com “N” caso a operação seja de Entrada" / "Quando se tratar de operação de entrada preencher com “N”"
        m = re.match(r'^preencher com "([0-9a-z]{1,4})" (?:caso|quando|se) (.*)$', t) or \
            re.match(r'^(?:quando se tratar de|quando|caso|se) (.*?),? preencher com "([0-9a-z]{1,4})"\.?$', t)
        if m:
            val, ctxt = (m.group(1), m.group(2)) if t.startswith("preencher") else (m.group(2), m.group(1))
            cond, ctx = self.parse_condicao(cod, ctxt)
            if cond is None:
                return "pendente", None
            return ("contexto" if ctx else "regra"), ("regra", {"tipo": "valor", "valor": val.upper(), "quando": cond, "texto": texto})

        # obrigatório / vazio condicional
        Q = r"(?:quando|se|caso|sempre que|para|nas|nos|em)\s+|(?=a partir d[eo])"
        padroes = [
            # (regex, tipo, invertido)  invertido=True -> "somente quando C" == vazio quando não C
            (r"^(?:este )?campo (?:e |torna-se )?obrigatorio:?\s*(?:" + Q + r")(.*)$", "obrigatorio", False),
            (r"^(?:este )?campo (?:e )?obrigatorio quando:?(.*)$", "obrigatorio", False),
            (r"^(?:o )?campo (?:deve|devera) ser preenchido (?:quando|se|caso|sempre que)\s+(.*)$", "obrigatorio", False),
            (r"^(?:este |o |esse )?campo (?:nao deve|nao devera|nao pode) ser (?:preenchido|informado)\s*(?:" + Q + r")(.*)$", "vazio", False),
            (r"^nao (?:preencher|informar)(?: este campo)?\s*(?:" + Q + r")(.*)$", "vazio", False),
            (r"^(?:informar|preencher)(?: este campo)? (?:somente|apenas|exclusivamente)\s*(?:" + Q + r"|)(.*)$", "vazio", True),
            (r"^(?:o |esse |este )?campo (?:so |somente )?(?:pode|devera|deve|podera) ser (?:preenchido|informado)(?: valor)? (?:somente |apenas |exclusivamente )?(?:" + Q + r")(.*)$", "vazio", True),
            (r"^(?:o |esse |este )?campo (?:so|somente) (?:devera|deve|pode) ser (?:preenchido|informado)\s*(?:" + Q + r"|)(.*)$", "vazio", True),
            (r"^(?:somente|so) (?:informar|preencher)\s*(?:" + Q + r"|)(.*)$", "vazio", True),
            (r"^(?:o )?campo (?:so|somente) pode ser preenchido quando:?(.*)$", "vazio", True),
            (r"^(?:devera|deve) ser (?:preenchido|informado) (?:somente|apenas|exclusivamente)\s*(?:" + Q + r"|)(.*)$", "vazio", True),
            (r"^(?:somente|so) (?:devera|deve|pode) ser (?:preenchido|informado)\s*(?:" + Q + r"|)(.*)$", "vazio", True),
            (r"^(?:somente|so|apenas) (?:para|quando|se|nas|nos)\s+(.*)$", "vazio", True),
            (r"^(?:somente|so) deve ser informado (?:o )?[a-z ]+? (?:para|quando|se)\s+(.*)$", "vazio", True),
            (r"^(?:o )?campo (?:so |somente )?podera ser preenchido (?:quando|se)\s+(.*)$", "vazio", True),
            (r"^(?:so )?deve(?:ra)? ser informand?o valor (?:nesse|neste) campo (?:quando|se)\s+(.*)$", "vazio", True),
            (r"^(?:somente )?(?:devera|deve) ser (?:informado|preenchido) quando\s+(.*)$", "obrigatorio", False),
            (r"^(?:campo )?obrigatorio (?:quando|se|para|caso)\s+(.*)$", "obrigatorio", False),
            (r"^(?:este campo e )?obrigatorio caso\s+(.*)$", "obrigatorio", False),
            (r"^(?:campo )?(?:e |torna-se )?obrigatorio para (?:as |os )?(.*)$", "obrigatorio", False),
            (r"^(?:pode ficar )?nao pode ficar em branco (?:quando|se)\s+(?:informado\s+)?(.*)$", "obrigatorio", False),
            (r"^(?:campo )?(?:valido|utilizado) apenas para\s+(.*)$", "vazio", True),
            (r"^(?:este campo )?nao deve ser (?:informado|preenchido) (?:quando|se)\s+(.*)$", "vazio", False),
            (r"^nao (?:pode|deve) ser informad[oa] (?:quando|se|para)\s+(.*)$", "vazio", False),
        ]
        for rx, tipo, inv in padroes:
            m = re.match(rx, t)
            if not m:
                continue
            cond_txt = m.group(1).strip()
            if not cond_txt or cond_txt.endswith(":") or cond_txt in ("quando", "quando:"):
                # condições vêm nos sub-tópicos seguintes ("Campo é obrigatório quando:")
                return "grupo", (tipo, inv)
            cond, ctx = self.parse_condicao(cod, cond_txt)
            if cond is None:
                return "pendente", None
            if "semCampo" in cond:
                return "naoaplica", None
            if inv:
                cond = {"nao": cond} if "const" not in cond else {"const": not cond["const"]}
            regra = {"tipo": tipo, "quando": cond, "texto": texto}
            return ("contexto" if ctx else "regra"), ("regra", regra)

        # valor fixo condicional: "Campo deverá ser preenchido com S sempre que ..."
        m = re.match(r'^(?:o )?campo (?:devera|deve) ser preenchido com (?:o )?(?:valor )?"?([a-z0-9]+)"?(?: \([^)]*\))? (?:sempre que|quando|se) (.*)$', t)
        if not m:
            m = re.match(r'^preencher sempre com "?([a-z0-9]+)"? (?:quando|se) (.*)$', t)
        if m:
            cond, ctx = self.parse_condicao(cod, m.group(2))
            if cond is None:
                return "pendente", None
            return ("contexto" if ctx else "regra"), ("regra", {"tipo": "valor", "valor": m.group(1).upper(),
                                                               "obrigatorio": True, "quando": cond, "texto": texto})

        if not getattr(self, "_em_frase", False):
            frases = [f for f in re.split(r'(?<=[a-z0-9")\]])\.\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])', texto) if f.strip()]
            if len(frases) > 1:
                self._em_frase = True
                try:
                    res = [self.interpreta(cod, campo, f, grupo) for f in frases]
                finally:
                    self._em_frase = False
                if all(r[0] in ("regra", "contexto", "info") for r in res) and any(r[1] for r in res):
                    st = "contexto" if any(r[0] == "contexto" for r in res) else "regra"
                    return st, ("multi", [r[1] for r in res if r[1]])

        if re.search(r"obrigat|nao deve|nao devera|nao pode|somente|apenas|so deve|so pode|exclusivamente|"
                     r"deve ser igual|soma|nao preencher|nao informar|deve existir|maior|menor|exceto|"
                     r"valores? (?:permitid|valid|aceit)|preencher com|informar com|deve(?:ra)? ser", t):
            return "pendente", None
        return "info", None

    @staticmethod
    def prepara_topicos(topicos):
        """separa marcadores embutidos ('◦') e junta palavras hifenizadas quebradas entre tópicos"""
        out = []
        for tp in topicos:
            partes = [p.strip(" -") for p in re.split(r"\s*[◦▪]\s*", tp)]
            for p in partes:
                if not p:
                    continue
                if out and re.search(r"[A-Za-zÀ-ú]-$", out[-1]):
                    out[-1] += p
                else:
                    out.append(p)
        return out

    # termos de IBS/CBS usados em "Corresponde ao IBS Estadual + IBS Municipal - IBS Crédito Presumido"
    TERMOS_TRIBUTO = [
        (r"ibs monofasico sujeito a retencao", None),  # já incluído em "IBS Monofásico"
        (r"cbs monofasico sujeito a retencao", None),
        (r"ibs monofasico", {"tributo": ["3"], "tipo": ["2"]}),
        (r"cbs monofasico", {"tributo": ["4"], "tipo": ["2"]}),
        (r"ibs credito presumido", {"tributo": ["3"], "tipo": ["3"]}),
        (r"cbs credito presumido", {"tributo": ["4"], "tipo": ["3"]}),
        (r"ibs estadual", {"tributo": ["1"]}),
        (r"ibs municipal", {"tributo": ["2"]}),
        (r"cbs estadual|cbs municipal|cbs", {"tributo": ["4"], "tipoNot": ["2", "3"]}),
    ]

    def regra_soma(self, cod, t, texto):
        m = re.match(r'^(?:o )?valor (?:desse|deste) campo deve ser igual (?:a|à|ao) soma dos valores do campo "([^"]+)" dos registros ([a-z]{3})\.?(?: corresponde (?:ao|a|à) (.*))?$', t)
        if not m:
            m2 = re.match(r'^(?:valor [a-z .]+, )?corresponde a soma (?:do|de) "([^"]+)" dos servicos(?: (.*?))? \(registro ([a-z]{3})\)\.?$', t)
            if not m2:
                return None
            nome, filtro_txt, filho = m2.group(1), (m2.group(2) or "").strip(), m2.group(3).upper()
            corr = None
        else:
            nome, filho, corr = m.group(1), m.group(2).upper(), m.group(3)
            filtro_txt = ""
        if filho not in self.regs or self.regs[filho].get("pai") is None or cod not in [filho] + self.ancestrais(filho):
            return "pendente", None
        n = self.campo_por_nome(filho, nome) or self.campo_nome_estrito(filho, nome, ancestrais=False)[1]
        if not n:
            return "pendente", None
        filtro = None
        if filtro_txt:
            if filtro_txt == "normais":
                return "pendente", None
            f, _ = self.parse_condicao(filho, re.sub(r"^com ", "", filtro_txt))
            if f is None:
                return "pendente", None
            filtro = f
        termos = []
        nivel = None
        if corr:
            ntrib = self.campo_por_nome(filho, "Tributo")
            ntipo = self.campo_por_nome(filho, "Tipo")
            if not ntrib or not ntipo:
                return "pendente", None
            resto = corr.strip(" .")
            sinal = 1
            vistos = set()
            while resto:
                for rx, mapa in self.TERMOS_TRIBUTO:
                    mm = re.match(rx, resto)
                    if mm:
                        break
                else:
                    return "pendente", None
                resto = resto[mm.end():].strip()
                if mapa is not None and rx not in vistos:
                    vistos.add(rx)
                    conds = [{"campo": ntrib, "op": "in", "valores": mapa["tributo"]}]
                    if "tipo" in mapa:
                        conds.append({"campo": ntipo, "op": "in", "valores": mapa["tipo"]})
                    if "tipoNot" in mapa:
                        conds.append({"campo": ntipo, "op": "notin", "valores": mapa["tipoNot"]})
                    termos.append({"registro": filho, "campo": n, "sinal": sinal, "filtro": {"e": conds}})
                mo = re.match(r"^([+-])\s*", resto)
                if mo:
                    sinal = 1 if mo.group(1) == "+" else -1
                    resto = resto[mo.end():]
                elif resto:
                    return "pendente", None
            nivel = "aviso"  # composição por tipo de tributo interpretada do texto do leiaute
        else:
            termos.append({"registro": filho, "campo": n, "sinal": 1, **({"filtro": filtro} if filtro else {})})
        rg = {"tipo": "soma", "termos": termos, "texto": texto}
        if nivel:
            rg["nivel"] = nivel
        return "regra", ("regra", rg)

    def termo_campo(self, cod, txt):
        """'"Valor Bruto" (campo 9)' | 'campo 43' | '9' | 'Valor Bruto' -> nº do campo no registro"""
        txt = txt.strip(" .,")
        txt = re.sub(r"^(?:o |a )?(?:valor (?:dos|do|de) )?(?:campos? )?", "", txt)
        m = re.search(r"\(campo (\d+)\)", txt) or re.fullmatch(r"(\d+)", txt) or re.fullmatch(r"campo (\d+)", txt)
        if m:
            n = int(m.group(1))
            citado = txt[:m.start()].strip() if "(campo" in txt else ""
            n = self.confere_num(cod, n, citado) if citado else n
            return n if n and any(c["n"] == n for c in self.regs[cod]["campos"]) else None
        nome = txt.strip('"')
        reg, n = self.campo_nome_estrito(cod, nome, ancestrais=False)
        return n

    def expressao(self, cod, txt):
        """'"Valor Bruto" (campo 9) menos o valor dos campos "Desc" (campo 22) e "Ded" (campo 23)' -> termos"""
        txt = re.sub(r"\.$", "", txt.strip())
        partes = re.split(r"\s+(mais|menos|\+|-)\s+", txt)
        termos, sinal = [], 1
        for i, p in enumerate(partes):
            if p in ("mais", "+"):
                sinal = 1
                continue
            if p in ("menos", "-"):
                sinal = -1
                continue
            for sub in re.split(r",\s*|\s+e\s+", p):
                sub = sub.strip()
                if not sub:
                    continue
                n = self.termo_campo(cod, sub)
                if not n:
                    return None
                termos.append({"campo": n, "sinal": sinal})
        return termos or None

    def regra_calculo(self, cod, campo, t, texto):
        if campo.get("tipo") not in ("V", "N", "D"):
            return None
        # soma/diferença de campos do próprio registro
        m = re.match(r'^(?:o )?valor (?:desse|deste) campo deve ser igual (?:ao|a|à) (?:soma dos valores dos campos )?(.+)$', t) or \
            re.match(r'^deve ser igual ao somatorio de:? (.+)$', t)
        if m and "registros" not in m.group(1):
            termos = self.expressao(cod, m.group(1))
            if not termos:
                return "pendente", None
            return "regra", ("regra", {"tipo": "calculo", "termos": termos, "texto": texto})
        # produto: '[Quando informado no campo 21 (...) valor "1", ]o valor informado deverá ser o resultado do valor do campo 10 (...) multiplicado pelo campo 22 (...)'
        m = re.match(r'^(?:quando (.+?), )?o valor informado devera ser o resultado do valor do campo (\d+) ?(\([^)]*(?:\([^)]*\))?[^)]*\))? multiplicado pelo campo (\d+) ?(\([^)]*(?:\([^)]*\))?[^)]*\)?)?\.?$', t)
        if m:
            a = self.confere_num(cod, int(m.group(2)), m.group(3))
            b = self.confere_num(cod, int(m.group(4)), m.group(5))
            if not a or not b:
                return "pendente", None
            if not all(any(c["n"] == x for c in self.regs[cod]["campos"]) for x in (a, b)):
                return "pendente", None
            nome_b = norm(next(c["nome"] for c in self.regs[cod]["campos"] if c["n"] == b))
            rg = {"tipo": "calculo", "produto": [a, b], "divisor": 100 if "%" in nome_b or "%" in (m.group(5) or "") else 1,
                  "tolerancia": 0.02, "nivel": "aviso", "texto": texto}
            if m.group(1):
                cond, ctx = self.parse_condicao(cod, m.group(1))
                if cond is None:
                    return "pendente", None
                rg["quando"] = cond
            return "regra", ("regra", rg)
        # comparações: 'menor ou igual a 100', 'Não deve ser maior que Valor Adicional INSS'
        m = re.match(r'^(?:o valor deste campo deve ser|informar somente valor|deve ser|nao deve ser) (menor ou igual|maior ou igual|menor|maior)(?: que| a)?:? (.+?)\.?$', t)
        if m:
            neg = t.startswith("nao deve")
            op = {"menor ou igual": "<=", "maior ou igual": ">=", "menor": "<", "maior": ">"}[m.group(1)]
            if neg:
                op = {"<=": ">", ">=": "<", "<": ">=", ">": "<="}[op]
            alvo = m.group(2).strip()
            mn = re.fullmatch(r"(\d+(?:,\d+)?)\s*%?", alvo)
            if mn:
                return "regra", ("regra", {"tipo": "comparacao", "op": op, "valor": float(mn.group(1).replace(",", ".")), "texto": texto})
            termos = self.expressao(cod, alvo)
            if not termos:
                return "pendente", None
            return "regra", ("regra", {"tipo": "comparacao", "op": op, "termos": termos, "texto": texto})
        return None

    # ---------------------------------------------------------- domínio
    RE_ITEM_DOM = re.compile(r'^"?([0-9a-z]{1,6})"?\s*(?:-|=|–)\s*\S')
    RE_CAB_VALORES = re.compile(r"^(?:os )?(?:valor(?:es)? (?:permitidos?|validos?|aceitos?|possiveis)|devera ser preenchido com os valores|preencher com)(?: sao)?\s*:?\s*")

    def codigos_lista(self, resto):
        """extrai os códigos de um texto de lista de valores (vários formatos usados no leiaute)"""
        r = resto.strip().strip(".;: ")
        if not r:
            return []
        r2 = r.strip("()")
        itens = [x.strip() for x in re.split(r";", r2) if x.strip()]
        ach = [self.RE_ITEM_DOM.match(x.strip('"')) for x in itens]
        if len(itens) >= 2 and all(ach):
            return [a.group(1).upper() for a in ach]
        q = re.findall(r'"([0-9a-z]{1,6})"\s*(?:-|=)', r)
        if len(q) >= 2:
            return [x.upper() for x in q]
        q = re.findall(r'(?:^|,|\be\b|\bou\b)\s*([0-9a-z]{1,4})\s*\(', r)
        if len(q) >= 2:
            return [x.upper() for x in q]
        q = re.findall(r'-\s*([0-9a-z]{1,4})\s*(?:;|$)', r)
        if len(q) >= 2:
            return [x.upper() for x in q]
        q = re.findall(r'\(([0-9a-z]{1,3})\)', r)
        if len(q) >= 2 and re.fullmatch(r'(?:\s*\([0-9a-z]{1,3}\)\s*[a-z]+\s*(?:ou|e|,)?)+', r):
            return [x.upper() for x in q]
        v = self.valores_citados(r2)
        return v or []

    def dominio(self, campo, topicos, cod=None):
        """Lista de valores aceitos quando a descrição enumera os códigos; também listas condicionais
        ('Quando a espécie for NFST deve ser informado o valor: ...'). Retorna (vals, usados, regras)."""
        tam = campo.get("tam") or 99
        vals, usados = [], set()
        regras = []
        cond_atual = None  # (cond, idx_cabecalho, valores, ctx)
        ignorar_resto = False
        aditivo = any(re.search(r"podera(?:o)? ser informad|e permitido|poderao ser informad", norm(x)) for x in topicos)

        def fecha():
            nonlocal cond_atual
            if cond_atual and cond_atual[2]:
                c, ic, vs, txt = cond_atual[:4]
                regras.append({"tipo": "dominio", "valores": list(dict.fromkeys(vs)), "quando": c, "texto": txt})
                usados.add(ic)
                usados.update(cond_atual[4] if len(cond_atual) > 4 else [])
            cond_atual = None

        for i, tp in enumerate(topicos):
            t = norm(tp).strip()
            # cabeçalho condicional
            m = re.match(r'^(?:quando|para|se) (.+?),? (?:deve ser informado o valor|(?:os )?valores? (?:permitidos?|validos?|aceitos?)(?: sao)?)\s*:?\s*(.*)$', t) or \
                re.match(r'^valores permitidos para (especie .+?):\s*(.*)$', t)
            if m and cod and aditivo:
                ignorar_resto = True
                cond_atual = None
                continue
            if m and cod:
                fecha()
                ignorar_resto = True
                ctxt = m.group(1)
                ctxt = re.sub(r'^especie ("?[a-z0-9]+"?(?: e "?[a-z0-9]+"?)*)$', r'especie for \1', ctxt)
                c, _ = self.parse_condicao(cod, ctxt)
                if c is None or "const" in c or "semCampo" in c:
                    ignorar_resto = True
                    cond_atual = None
                    continue
                vs = self.codigos_lista(m.group(2)) if m.group(2) else []
                cond_atual = [c, i, vs, tp, [i]]
                if vs:
                    fecha()
                continue
            if cond_atual is not None:
                a = self.RE_ITEM_DOM.match(t.strip('".; '))
                if a:
                    cond_atual[2].append(a.group(1).upper())
                    cond_atual[4].append(i)
                    cond_atual[3] += " / " + tp
                    continue
                fecha()
            if ignorar_resto:
                continue
            m = self.RE_CAB_VALORES.match(t)
            if m:
                lista = self.codigos_lista(t[m.end():])
                if lista:
                    vals += lista
                    usados.add(i)
                elif not t[m.end():].strip():
                    usados.add(i)  # cabeçalho; itens nos tópicos seguintes
                continue
            # itens "0 - Nada", "1 = Normal", '"01" - Liminar' (um ou vários no mesmo tópico)
            inline = [x.strip() for x in re.split(r";", t) if x.strip()]
            achados = [self.RE_ITEM_DOM.match(re.sub(r"^(?:\(|informar:?\s*)", "", x).strip('"')) for x in inline]
            if achados and all(achados):
                vals += [a.group(1).upper() for a in achados]
                usados.add(i)
        fecha()
        vals = list(dict.fromkeys(vals))
        if campo["tipo"] == "L":
            return None, usados if vals and set(vals) <= {"S", "N"} else set(), []
        ok = len(vals) >= 2 and all(len(v) <= tam for v in vals) and campo["tipo"] in ("N", "C") and \
            not (campo["tipo"] == "N" and not all(v.isdigit() for v in vals))
        regras = [r for r in regras if all(len(v) <= tam for v in r["valores"])]
        if not ok:
            # sem lista válida: só os tópicos das listas condicionais contam como usados
            usados = {i for r in regras for i in range(len(topicos)) if topicos[i] in r["texto"]}
            return None, usados, regras
        return vals, usados, regras

    # ---------------------------------------------------------- cardinalidade
    def cardinalidade(self, reg):
        card = {}
        obs = " ".join(norm(o) for o in reg.get("obs", []))
        if "registro obrigatorio" in obs:
            card["min"] = 1
        if "um registro por arquivo" in obs or "nenhum ou um registro" in obs or "nenhum ou somente um registro" in obs:
            card["max"] = 1
        # "Pode conter um ou vários registros" não é tratado como mínimo: o leiaute usa a frase
        # também para registros opcionais (ex.: BCE em NVC)
        return card or None

    # ---------------------------------------------------------- principal
    def gera(self):
        self.aplica_correcoes()
        tabelas = {}
        for num, t in self.raw["tabelas"].items():
            vals = {}
            for v in t["valores"]:
                vals.setdefault(v["valor"], v["descricao"])
            tabelas[num] = {"titulo": t["titulo"].strip("() "), "valores": vals}
            if num in self.cor.get("tabelasIgnoradas", {}):
                tabelas[num]["naoValidar"] = self.cor["tabelasIgnoradas"][num]

        filhos = {}
        for cod, r in self.regs.items():
            if r.get("pai"):
                filhos.setdefault(r["pai"], []).append(cod)

        saida = {}
        for cod, r in self.regs.items():
            campos = []
            for c in r["campos"]:
                campos.append(self.gera_campo(cod, c))
            saida[cod] = {
                "nome": r["nome"],
                "pai": r.get("pai"),
                "filhos": filhos.get(cod, []),
                "obs": r.get("obs", []),
                "pagina": r.get("pagina"),
                "campos": campos,
            }
            card = self.cardinalidade(r)
            if card:
                saida[cod]["cardinalidade"] = card
            if any(c.pop("temSoma", False) for c in campos):
                saida[cod]["temSoma"] = True

        # regras manuais
        for chave, regras in self.man.get("campos", {}).items():
            cod, n = chave.split(".")
            campo = next(c for c in saida[cod]["campos"] if c["n"] == int(n))
            for rg in regras:
                rg = dict(rg, manual=True)
                campo.setdefault("regras", []).append(rg)
                for o in campo["obs"]:
                    if norm(o["texto"]).rstrip(".") == norm(rg.get("texto", "")).rstrip(".") and o["status"] != "regra":
                        self.estat[o["status"]] -= 1
                        self.estat["regra"] += 1
                        o["status"] = "regra"
                        o["nota"] = "regra escrita à mão (spec/regras_manuais)"
                if rg["tipo"] == "soma":
                    saida[cod]["temSoma"] = True

        # campos-chave usados em referências
        for cod, r in saida.items():
            for c in r["campos"]:
                ref = c.get("referencia")
                if ref:
                    alvo = saida.get(ref["registro"])
                    if not alvo:
                        del c["referencia"]
                        continue
                    for a in alvo["campos"]:
                        if a["n"] == ref["campo"]:
                            a["chave"] = True

        return {
            "versao": self.raw["versao"],
            "geradoEm": date.today().isoformat(),
            "contexto": {k: v for k, v in self.sem.get("contexto", {}).items() if not k.startswith("_")},
            "registros": saida,
            "tabelas": tabelas,
            "estatisticas": self.estat,
            "avisos": self.avisos,
        }

    def gera_campo(self, cod, c):
        tam = int(c["tam"]) if str(c.get("tam", "")).isdigit() else None
        out = {"n": c["n"], "nome": c["nome"], "tipo": c.get("tipo") or "C"}
        if tam:
            out["tam"] = tam
        if c.get("correcao"):
            out["correcao"] = c["correcao"]
        topicos = self.prepara_topicos(c.get("descricao", []))
        obs = []
        dom, usados, regras_dom = self.dominio(out, topicos, cod)
        if dom:
            out["dominio"] = dom
        grupo = None  # ("obrigatorio"/"vazio", invertido, [condições], [índices])
        regras = list(regras_dom)

        def fecha_grupo():
            nonlocal grupo
            if not grupo:
                return
            tipo, inv, conds, idx, ctx_any, ok = grupo
            if ok and conds:
                # sub-tópicos se somam (E); um tópico iniciado por "Ou" é alternativa ao anterior
                partes = []
                for txt, cnd in conds:
                    if norm(txt).startswith("ou") and partes:
                        ant = partes.pop()
                        partes.append({"ou": (ant["ou"] if "ou" in ant else [ant]) + [cnd]})
                    else:
                        partes.append(cnd)
                cond = partes[0] if len(partes) == 1 else {"e": partes}
                if inv:
                    cond = {"nao": cond}
                regras.append({"tipo": tipo, "quando": cond,
                               "texto": " / ".join([obs[idx[0]]["texto"]] + [t for t, _ in conds])})
                st = "contexto" if ctx_any else "regra"
            else:
                st = "pendente"
            for i in idx:
                obs[i]["status"] = st
            grupo = None

        for i, tp in enumerate(topicos):
            obs.append({"texto": tp, "status": "info"})
            if i in usados:
                obs[i]["status"] = "regra"
                continue
            if grupo is not None:
                t = norm(tp)
                tl = re.sub(r"\s+e$", "", t)
                cond_try = None
                if not (t.startswith("o campo") or t.startswith("ou") or t.startswith("se ")):
                    cond_try = self.parse_condicao(cod, tl)
                if t.startswith("o campo") or t.startswith("ou") or t.startswith("se ") or cond_try[0] is not None:
                    cond, ctx = cond_try if cond_try else self.parse_condicao(cod, re.sub(r"^ou,?\s*", "", tl))
                    grupo[3].append(i)
                    if cond is None:
                        grupo[5] = False
                    else:
                        grupo[2].append((tp, cond))
                        grupo[4] = grupo[4] or ctx
                    continue
                fecha_grupo()
            st, ef = self.interpreta(cod, out, tp, grupo)
            if st == "naoaplica":
                obs[i]["nota"] = "o registro não possui o campo citado na condição"
                st = "info"
            if st == "grupo":
                grupo = [ef[0], ef[1], [], [i], False, True]
                continue
            obs[i]["status"] = st
            efeitos = ef[1] if ef and ef[0] == "multi" else ([ef] if ef else [])
            for k, v in efeitos:
                if k == "regra":
                    q = v.get("quando")
                    if q is not None and "const" in q:
                        if not q["const"]:
                            if len(efeitos) == 1:
                                obs[i]["status"] = "info"
                                obs[i]["nota"] = "não se aplica a este registro"
                            continue
                        v = dict(v)
                        v.pop("quando")
                    if v["tipo"] == "soma":
                        out["temSoma"] = True
                    regras.append(v)
                elif k == "obrigatorio":
                    out["obrigatorio"] = True
                else:
                    out[k] = v
        fecha_grupo()
        self.ajusta_obrigatorio(out, obs, regras)
        if regras:
            out["regras"] = regras
        out["obs"] = obs
        for o in obs:
            self.estat[o["status"]] = self.estat.get(o["status"], 0) + 1
        return out


RE_RESTRICAO = re.compile(
    r"^(?:informar|preencher|so |somente|nao preencher|nao informar|(?:este |esse |o )?campo (?:nao deve|nao devera|so|somente)|"
    r"(?:o )?campo (?:devera|deve) ser (?:preenchido|informado) (?:apenas|somente|exclusivamente)|(?:devera|deve) ser informado apenas)")


def _ajusta_obrigatorio(self, out, obs, regras):
    """'Campo Obrigatório' + 'Informar somente quando X' => obrigatório só quando X.
    'Campo Obrigatório' + 'não preencher quando Y' => obrigatório quando não Y."""
    pend = [o for o in obs if o["status"] == "pendente" and RE_RESTRICAO.match(norm(o["texto"]))]
    if pend:
        for r in regras:
            if r["tipo"] == "obrigatorio":
                r["nivel"] = "aviso"
                r["nota"] = "Há restrições de preenchimento não verificadas automaticamente: " +                     " / ".join(o["texto"] for o in pend)
    vazios_c = [r for r in regras if r["tipo"] == "vazio" and "quando" in r]
    if vazios_c:
        # obrigatório condicional não vale quando outra regra manda deixar o campo vazio
        excecao = {"nao": vazios_c[0]["quando"] if len(vazios_c) == 1 else {"ou": [r["quando"] for r in vazios_c]}}
        for r in regras:
            if r["tipo"] == "obrigatorio" and "quando" in r:
                r["quando"] = {"e": [r["quando"], excecao]}
    if not out.get("obrigatorio"):
        return
    vazios = [r for r in regras if r["tipo"] == "vazio"]
    if any("quando" not in r for r in vazios):
        out.pop("obrigatorio")  # campo sempre vazio: a obrigatoriedade não se aplica
        return
    pend = [o for o in obs if o["status"] == "pendente" and RE_RESTRICAO.match(norm(o["texto"]))]
    if vazios:
        out.pop("obrigatorio")
        conds = [r["quando"] for r in vazios]
        cond = {"nao": conds[0] if len(conds) == 1 else {"ou": conds}}
        regra = {"tipo": "obrigatorio", "quando": cond,
                 "texto": "Campo Obrigatório, exceto quando: " + " / ".join(r["texto"] for r in vazios)}
        if pend:
            regra["nivel"] = "aviso"
        regras.append(regra)
    elif pend:
        out["obrigatorioNivel"] = "aviso"
        out["obrigatorioNota"] = "Há restrições de preenchimento não verificadas automaticamente: " +             " / ".join(o["texto"] for o in pend)


Gerador.ajusta_obrigatorio = _ajusta_obrigatorio


def carrega(caminho, opcional=False):
    if opcional and not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    entrada, saida = sys.argv[1], sys.argv[2]
    raw = carrega(entrada)
    pasta = os.path.dirname(os.path.abspath(entrada))
    v = raw["versao"]
    g = Gerador(raw,
                carrega(os.path.join(pasta, f"correcoes_{v}.json"), True),
                carrega(os.path.join(pasta, f"semantica_{v}.json"), True),
                carrega(os.path.join(pasta, f"regras_manuais_{v}.json"), True))
    spec = g.gera()
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, separators=(",", ":"))
    e = spec["estatisticas"]
    total = sum(e.values())
    print(f"Leiaute {v}: {len(spec['registros'])} registros, {len(spec['tabelas'])} tabelas")
    print(f"Tópicos: {total} | regra {e['regra']} | contexto {e['contexto']} | info {e['info']} | pendente {e['pendente']}")
