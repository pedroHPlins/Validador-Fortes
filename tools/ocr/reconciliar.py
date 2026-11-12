"""
Reconcilia um leiaute lido por OCR (spec/leiaute_<v>.raw.json) com leiautes vizinhos que têm texto
extraível. A ESTRUTURA continua sendo a do OCR (quais registros e campos existem, em que ordem, tamanho
e tipo lidos); os TEXTOS (nomes, observações, tabelas de códigos) são trocados pelos do vizinho quando
batem, o que corrige os erros de leitura ("Camoo Obriaatório", "(0/0)" no lugar de "(%)", códigos "OI").

- campos alinhados por programação dinâmica sobre a semelhança dos nomes
- campo do OCR que junta dois campos do vizinho ("Código Contábil Item Não Compõe...") é desmembrado
- tamanho/tipo ilegíveis no OCR são completados pelo vizinho
- observações: cada tópico do OCR é trocado pelo tópico mais parecido do vizinho (semelhança >= 0,85)

Uso: python tools/ocr/reconciliar.py <versao_ocr> <vizinho1> [vizinho2 ...]
     ex.: python tools/ocr/reconciliar.py 195 197 183
"""
import difflib
import json
import os
import re
import sys
import unicodedata

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIPOS = ("N", "C", "D", "L", "V", "T", "S")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9%]+", " ", s).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def alinha(na, nb):
    """Alinhamento global (Needleman-Wunsch) de duas listas de nomes; devolve pares (i|None, j|None)."""
    n, m = len(na), len(nb)
    GAP = -0.6
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    T = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        S[i][0], T[i][0] = S[i - 1][0] + GAP, "a"
    for j in range(1, m + 1):
        S[0][j], T[0][j] = S[0][j - 1] + GAP, "b"
    cache = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = cache.setdefault((i, j), sim(na[i - 1], nb[j - 1]))
            op = max((S[i - 1][j - 1] + s - 0.5, "d"), (S[i - 1][j] + GAP, "a"), (S[i][j - 1] + GAP, "b"))
            S[i][j], T[i][j] = op
    i, j, out = n, m, []
    while i > 0 or j > 0:
        t = T[i][j]
        if t == "d":
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif t == "a":
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1], S[n][m]


def troca_topicos(desc_ocr, desc_ref):
    if not desc_ref:
        return desc_ocr, 0
    if desc_ocr and sim(" ".join(desc_ocr), " ".join(desc_ref)) >= 0.9 and len(desc_ocr) <= len(desc_ref) + 1:
        return list(desc_ref), len(desc_ocr)
    out, n = [], 0
    for t in desc_ocr:
        melhor = max(desc_ref, key=lambda r: sim(t, r))
        if sim(t, melhor) >= 0.85:
            out.append(melhor)
            n += 1
        else:
            out.append(t)
    return out, n


def campo_de(ref_c, n):
    c = {k: v for k, v in ref_c.items() if k not in ("nDocumento",)}
    c["n"] = n
    return c


def reconcilia_registro(reg, refs, est):
    melhor = None
    for ref in refs:
        rr = ref["registros"].get(reg["codigo"])
        if not rr or not rr["campos"]:
            continue
        pares, score = alinha([c["nome"] for c in reg["campos"]], [c["nome"] for c in rr["campos"]])
        if melhor is None or score > melhor[2]:
            melhor = (rr, pares, score)
    if melhor is None:
        return
    rr, pares, _ = melhor
    if sim(reg["nome"], rr["nome"]) >= 0.7:
        reg["nome"] = rr["nome"]
    reg["obs"] = [max(rr["obs"], key=lambda r: sim(o, r)) if rr["obs"] and max(sim(o, r) for r in rr["obs"]) >= 0.85 else o
                  for o in reg["obs"]]
    if not reg["campos"]:
        # tabela do registro não reconhecida no OCR: campos do vizinho
        reg["campos"] = [dict(campo_de(c, c["n"]), correcao="campos não lidos pelo OCR; copiados do leiaute vizinho")
                         for c in rr["campos"]]
        est["registrosCopiados"].append(reg["codigo"])
        return
    novos = []
    k = 0
    while k < len(pares):
        i, j = pares[k]
        if i is None:
            k += 1
            continue
        c = reg["campos"][i]
        if j is None:
            novos.append(c)
            k += 1
            continue
        rc = rr["campos"][j]
        s = sim(c["nome"], rc["nome"])
        # campo do OCR que juntou dois do vizinho (linha divisória não detectada): o par vizinho
        # sem correspondente pode estar antes ou depois no alinhamento
        juntos = []
        if k + 1 < len(pares) and pares[k + 1][0] is None:
            juntos.append((rc, rr["campos"][pares[k + 1][1]]))
        if k > 0 and pares[k - 1][0] is None and novos is not None:
            juntos.append((rr["campos"][pares[k - 1][1]], rc))
        feito = False
        for a, b in juntos:
            if sim(c["nome"], a["nome"] + " " + b["nome"]) >= max(0.8, s + 0.05):
                novos.append(dict(campo_de(a, 0), correcao="OCR juntou dois campos; desmembrado pelo leiaute vizinho"))
                novos.append(dict(campo_de(b, 0), correcao="OCR juntou dois campos; desmembrado pelo leiaute vizinho"))
                est["desmembrados"] += 1
                k += 2 if b is not rc else 1
                feito = True
                break
        if feito:
            continue
        if s >= 0.7 or len(norm(c["nome"])) <= 3:
            if c["nome"] != rc["nome"]:
                est["nomes"] += 1
            c["nome"] = rc["nome"]
            if not re.fullmatch(r"\d+|-", c.get("tam") or ""):
                c["tam"] = rc.get("tam", "")
                est["tamTipo"] += 1
            if (c.get("tipo") or "") not in TIPOS:
                c["tipo"] = rc.get("tipo", "")
                est["tamTipo"] += 1
            c["descricao"], n = troca_topicos(c.get("descricao", []), rc.get("descricao", []))
            est["topicos"] += n
            # tamanho/tipo: se pelo menos dois vizinhos concordam entre si e o OCR diverge, vale o vizinho
            # (o OCR confunde dígitos parecidos, ex.: 5/6, 1/7)
            iguais = [x for r in refs for x in (r["registros"].get(reg["codigo"]) or {"campos": []})["campos"]
                      if norm(x["nome"]) == norm(rc["nome"])]
            for chave in ("tam", "tipo"):
                vals = [x.get(chave) for x in iguais]
                if len(vals) >= 2 and len(set(vals)) == 1 and c.get(chave) != vals[0]:
                    c[chave] = vals[0]
                    est["tamTipoVizinhos"] += 1
        novos.append(c)
        k += 1
    for n, c in enumerate(novos, start=1):
        if c["n"] != n and "nDocumento" not in c:
            c["nDocumento"] = c["n"]
        c["n"] = n
    reg["campos"] = novos


def reconcilia_tabela(t, refs, est):
    cands = [r["tabelas"][t["numero"]] for r in refs if t["numero"] in r["tabelas"]]
    if not cands:
        return
    rt = max(cands, key=lambda x: sim(x["titulo"], t["titulo"]))
    if sim(rt["titulo"], t["titulo"]) >= 0.7:
        t["titulo"] = rt["titulo"]
    if not t["valores"]:
        t["valores"] = rt["valores"]
        est["tabelasCopiadas"].append(t["numero"])
        return
    out = []
    for v in t["valores"]:
        m = max(rt["valores"], key=lambda r: sim(v.get("descricao"), r.get("descricao"))) if rt["valores"] else None
        if m and sim(v.get("descricao"), m.get("descricao")) >= 0.8:
            out.append(m)
            est["valores"] += 1
        else:
            out.append(v)
    t["valores"] = out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    sys.stdout.reconfigure(encoding="utf-8")
    v, vizinhos = sys.argv[1], sys.argv[2:]
    p = os.path.join(RAIZ, "spec", f"leiaute_{v}.raw.json")
    raw = json.load(open(p, encoding="utf-8"))
    refs = [json.load(open(os.path.join(RAIZ, "spec", f"leiaute_{x}.raw.json"), encoding="utf-8")) for x in vizinhos]
    est = {"nomes": 0, "tamTipo": 0, "tamTipoVizinhos": 0, "topicos": 0, "desmembrados": 0, "valores": 0,
           "registrosCopiados": [], "tabelasCopiadas": []}
    for reg in raw["registros"].values():
        reconcilia_registro(reg, refs, est)
    for t in raw["tabelas"].values():
        reconcilia_tabela(t, refs, est)
    # a versão vem do nome do arquivo, não do OCR (que confunde dígitos)
    raw["versao"] = v
    cab = raw["registros"].get("CAB")
    if cab and len(cab["campos"]) > 1:
        cab["campos"][1]["descricao"] = [re.sub(r'(Texto Fixo Contendo\s*["“])\d+(["”])', r"\g<1>" + v + r"\g<2>", d)
                                         for d in cab["campos"][1].get("descricao", [])]
    raw["ocr"] = {"vizinhos": vizinhos, "ajustes": est,
                  "nota": "PDF sem texto extraível: lido por OCR e reconciliado com os leiautes vizinhos"}
    json.dump(raw, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(est, ensure_ascii=False))


if __name__ == "__main__":
    main()
