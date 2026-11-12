"""
Gera os arquivos de apoio de uma versão do leiaute a partir de outra já revisada:

    spec/semantica_<nova>.json       mesmos conceitos (cancelado, operação, espécie…) com os números
                                     de campo da versão nova (associados pelo nome do campo)
    spec/correcoes_<nova>.json       só as correções cujo defeito também existe no PDF da versão nova
    spec/regras_manuais_<nova>.json  só as regras cujo texto também aparece nas observações da versão nova

Uso:
    python tools/portar_versao.py <versao_base> <versao_nova>
    ex.: python tools/portar_versao.py 204 197

Requer spec/leiaute_<base>.raw.json e spec/leiaute_<nova>.raw.json. Mostra o que não pôde ser portado
(confira e ajuste à mão; arquivos existentes são sobrescritos só com --forcar).
"""
import copy
import difflib
import json
import os
import re
import sys
import unicodedata

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(RAIZ, "spec")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def carrega(nome, opcional=False):
    p = os.path.join(SPEC, nome)
    if opcional and not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def textos_obs(campo):
    return {norm(t).rstrip(".") for t in campo.get("descricao", [])}


class Mapa:
    """Associa campo da versão base -> campo da versão nova, por registro."""

    def __init__(self, base, nova):
        self.base, self.nova = base["registros"], nova["registros"]
        self.cache = {}

    def campo(self, reg, n):
        if reg not in self.base or reg not in self.nova:
            return None
        k = (reg, n)
        if k in self.cache:
            return self.cache[k]
        cb = next((c for c in self.base[reg]["campos"] if c["n"] == n), None)
        res = None
        if cb:
            nome = norm(cb["nome"])
            iguais = [c for c in self.nova[reg]["campos"] if norm(c["nome"]) == nome]
            if len(iguais) == 1:
                res = iguais[0]["n"]
            elif len(iguais) > 1:  # nomes repetidos: o de mesmo número, ou o mais próximo
                res = min(iguais, key=lambda c: abs(c["n"] - n))["n"]
            else:
                mesmo = next((c for c in self.nova[reg]["campos"] if c["n"] == n), None)
                if mesmo and difflib.SequenceMatcher(None, norm(mesmo["nome"]), nome).ratio() >= 0.8:
                    res = n
        self.cache[k] = res
        return res


def mapeia_cond(c, reg, mapa, pai):
    """Troca os números de campo de uma condição; None se algum campo não existir na versão nova."""
    if isinstance(c, list):
        out = [mapeia_cond(x, reg, mapa, pai) for x in c]
        return None if any(x is None for x in out) else out
    if not isinstance(c, dict):
        return c
    out = {}
    for k, v in c.items():
        if k == "campo" and isinstance(v, int):
            alvo = c.get("registro") or reg
            n = mapa.campo(alvo, v)
            if n is None:
                return None
            out[k] = n
        elif k == "grupo" and isinstance(v, list):
            ns = [mapa.campo(reg, x) for x in v]
            if None in ns:
                return None
            out[k] = ns
        elif isinstance(v, (dict, list)):
            m = mapeia_cond(v, (v.get("registro") if isinstance(v, dict) and v.get("registro") else reg), mapa, pai) \
                if isinstance(v, dict) else mapeia_cond(v, reg, mapa, pai)
            if m is None:
                return None
            out[k] = m
        else:
            out[k] = v
    return out


def portar(vbase, vnova):
    base = carrega(f"leiaute_{vbase}.raw.json")
    nova = carrega(f"leiaute_{vnova}.raw.json")
    mapa = Mapa(base, nova)
    relat = []

    # ---- semântica
    sem = carrega(f"semantica_{vbase}.json")
    sem_n = {"_descricao": sem.get("_descricao", ""), "documentos": {}, "contexto": sem.get("contexto", {})}
    for doc, d in sem["documentos"].items():
        if doc not in nova["registros"]:
            relat.append(f"semântica: documento {doc} não existe em {vnova}")
            continue
        nd = {}
        for k, v in d.items():
            if isinstance(v, int):
                n = mapa.campo(doc, v)
                if n is None:
                    relat.append(f"semântica: {doc}.{k} (campo {v}) sem correspondente em {vnova}")
                    continue
                nd[k] = n
            elif isinstance(v, dict) and "campo" in v:
                n = mapa.campo(doc, v["campo"])
                if n is None:
                    relat.append(f"semântica: {doc}.{k} (campo {v['campo']}) sem correspondente em {vnova}")
                    continue
                nd[k] = dict(v, campo=n)
            else:
                nd[k] = copy.deepcopy(v)
        sem_n["documentos"][doc] = nd

    # ---- correções: só se o PDF novo traz o mesmo defeito do PDF base
    cor = carrega(f"correcoes_{vbase}.json", True) or {}
    cor_n = {"_descricao": f"Correções manuais para lacunas/erros do PDF do leiaute {vnova} "
                           f"(portadas de {vbase} por tools/portar_versao.py e revisadas).",
             "campos": [], "camposInseridos": [], "registros": []}
    for k in ("tabelasIgnoradas",):
        if k in cor:
            cor_n[k] = cor[k]
    for c in cor.get("campos", []):
        reg = c["registro"]
        n = mapa.campo(reg, c["n"])
        if n is None:
            relat.append(f"correção {reg}.{c['n']}: campo não existe em {vnova}")
            continue
        cb = next(x for x in base["registros"][reg]["campos"] if x["n"] == c["n"])
        cn = next(x for x in nova["registros"][reg]["campos"] if x["n"] == n)
        chaves = [k for k in ("tam", "tipo", "nome") if k in c]
        if all((cn.get(k) or "") == (cb.get(k) or "") for k in chaves):
            cor_n["campos"].append(dict(c, n=n))
        elif all((cn.get(k) or "") == (c.get(k) or "") for k in chaves):
            pass  # o PDF novo já traz o valor certo
        else:
            relat.append(f"correção {reg}.{c['n']}: PDF {vnova} traz {[cn.get(k) for k in chaves]}, "
                         f"base trazia {[cb.get(k) for k in chaves]} - conferir")
    for c in cor.get("camposInseridos", []):
        reg = c["registro"]
        if reg in nova["registros"]:
            ns = [x["n"] for x in nova["registros"][reg]["campos"]]
            if c["campo"]["n"] not in ns and c["depoisDe"] in ns and c["campo"]["n"] == c["depoisDe"] + 1:
                cor_n["camposInseridos"].append(c)
    for c in cor.get("registros", []):
        if c["registro"] in nova["registros"]:
            cor_n["registros"].append(c)

    # ---- regras manuais: só as que correspondem a uma observação da versão nova
    man = carrega(f"regras_manuais_{vbase}.json", True) or {"campos": {}}
    man_n = {"_descricao": man.get("_descricao", ""), "campos": {}}
    for chave, regras in man.get("campos", {}).items():
        reg, n = chave.split(".")
        nn = mapa.campo(reg, int(n))
        if nn is None:
            relat.append(f"regra manual {chave}: campo não existe em {vnova}")
            continue
        cn = next(x for x in nova["registros"][reg]["campos"] if x["n"] == nn)
        obs = textos_obs(cn)
        for rg in regras:
            alvo = norm(rg.get("texto", "")).rstrip(".")
            if not any(alvo and alvo in o for o in obs):
                relat.append(f"regra manual {chave}: texto não aparece em {reg}.{nn} da versão {vnova}")
                continue
            m = mapeia_cond(rg, reg, mapa, None)
            if m is None:
                relat.append(f"regra manual {chave}: condição cita campo que não existe em {vnova}")
                continue
            man_n["campos"].setdefault(f"{reg}.{nn}", []).append(m)
    return sem_n, cor_n, man_n, relat


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    sys.stdout.reconfigure(encoding="utf-8")
    vbase, vnova = args
    sem, cor, man, relat = portar(vbase, vnova)
    for nome, dados in ((f"semantica_{vnova}.json", sem), (f"correcoes_{vnova}.json", cor),
                        (f"regras_manuais_{vnova}.json", man)):
        p = os.path.join(SPEC, nome)
        if os.path.exists(p) and "--forcar" not in sys.argv:
            print(f"{nome} já existe (use --forcar para sobrescrever)")
            continue
        with open(p, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        print(f"gravado {nome}")
    print(f"{len(cor['campos'])} correções, {sum(len(v) for v in man['campos'].values())} regras manuais portadas")
    for r in relat:
        print("  -", r)


if __name__ == "__main__":
    main()
