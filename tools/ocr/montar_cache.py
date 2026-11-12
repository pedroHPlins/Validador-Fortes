"""
Monta o cache de páginas (mesmo formato de extrair_leiaute.ler_pdf) a partir do OCR + grade detectada:
    [{"lines": [{text, x0, top, bottom}], "tables": [{bbox, rows, rowbbox}]}]   (coordenadas em pontos)

Uso: montar_cache.py <dir_ocr> <dir_seg> <saida.json> [--img <dir_png_sem_linhas>] <vocab1.raw.json> [...]

- células: palavras do OCR distribuídas pela grade de linhas detectada na imagem (grade.py)
- palavras corrigidas com o vocabulário de leiautes que têm texto (letras com descendente cortadas
  pela borda da célula: "Camoo Obriaatório" -> "Campo Obrigatório")
- com --img: letras isoladas da coluna Tipo (que o OCR ignora) classificadas por comparação com a fonte,
  e marcadores de lista (●/○) detectados à esquerda de cada linha
"""
import collections
import difflib
import json
import os
import re
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ESC = 72 / 300.0  # px (300 dpi) -> pt


# ------------------------------------------------------------------ correção de palavras
class Corretor:
    def __init__(self, raws):
        cont = collections.Counter()

        def add(t):
            for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", t or ""):
                cont[w] += 1
        for p in raws:
            r = json.load(open(p, encoding="utf-8"))
            for reg in r["registros"].values():
                add(reg.get("nome"))
                for o in reg.get("obs", []) + reg.get("texto", []):
                    add(o)
                for c in reg["campos"]:
                    add(c["nome"])
                    for d in c.get("descricao", []):
                        add(d)
            for t in r["tabelas"].values():
                add(t.get("titulo"))
                for x in t.get("texto", []):
                    add(x)
                for v in t.get("valores", []):
                    add(v.get("descricao"))
        self.low = collections.defaultdict(collections.Counter)
        for w, n in cont.items():
            self.low[w.lower()][w] += n
        self.lista = list(self.low)
        self.cache = {}

    def conhecida(self, w):
        return w.lower() in self.low

    def melhor(self, w):
        lw = w.lower()
        if lw in self.cache:
            return self.cache[lw]
        cands = difflib.get_close_matches(lw, self.lista, n=6, cutoff=0.74)
        res = None
        if cands:
            # mesmo tamanho (letra com descendente cortada vira outra letra) tem preferência
            cands.sort(key=lambda c: (-difflib.SequenceMatcher(None, lw, c).ratio() - (0.05 if len(c) == len(lw) else 0),
                                      -sum(self.low[c].values())))
            res = cands[0]
        self.cache[lw] = res
        return res

    @staticmethod
    def forma(orig, alvo):
        """Aplica a caixa do original à palavra corrigida."""
        if orig.isupper() and len(orig) > 1:
            return alvo.upper()
        if orig[:1].isupper():
            return alvo[:1].upper() + alvo[1:]
        return alvo

    def corrige(self, palavras):
        """palavras: [[texto, x0, y0, x1, y1], ...] de uma linha do OCR (em ordem)."""
        out = []
        i = 0
        while i < len(palavras):
            w = palavras[i]
            pre, mid, suf = re.match(r"^([\"“(]*)(.*?)([\"”).,;:]*)$", w[0]).groups()
            if re.fullmatch(r"[A-Za-zÀ-ÿ0]{3,}", mid) and re.search(r"[A-Za-zÀ-ÿ]", mid) and not self.conhecida(mid):
                m2 = mid.replace("0", "O") if mid.isupper() else mid.replace("0", "o")
                # junção com a próxima palavra ("Cam o" -> "Campo", "Obri atório" -> "Obrigatório")
                if i + 1 < len(palavras) and not suf:
                    prox = palavras[i + 1]
                    feito = False
                    for meio in ("", "p", "g", "q", "j", "y"):
                        cm = re.match(r"^(.*?)([\"”).,;:]*)$", m2 + meio + prox[0])
                        if self.conhecida(cm.group(1)):
                            out.append([pre + self.forma(m2, cm.group(1)) + cm.group(2), w[1], min(w[2], prox[2]),
                                        prox[3], max(w[4], prox[4])])
                            i += 2
                            feito = True
                            break
                    if feito:
                        continue
                if self.conhecida(m2):
                    out.append([pre + m2 + suf] + w[1:])
                    i += 1
                    continue
                b = self.melhor(m2)
                if b:
                    out.append([pre + self.forma(m2, b) + suf] + w[1:])
                    i += 1
                    continue
            out.append(w)
            i += 1
        return out


# ------------------------------------------------------------------ imagem
class Glifos:
    """Classifica letras isoladas (coluna Tipo), que o OCR do Windows costuma ignorar."""
    LETRAS = "NCDLVTS"
    DIGITOS = "0123456789"

    def __init__(self):
        self.modelos = []
        for fonte in ("verdana.ttf", "tahoma.ttf", "arial.ttf"):
            try:
                f = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", fonte), 64)
            except OSError:
                continue
            for c in self.LETRAS + self.DIGITOS:
                im = Image.new("L", (100, 100), 255)
                ImageDraw.Draw(im).text((10, 5), c, font=f, fill=0)
                self.modelos.append((c, self.norm(np.asarray(im) < 128)))

    @staticmethod
    def norm(a):
        ys, xs = np.where(a)
        if not len(ys):
            return None
        c = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        return np.asarray(Image.fromarray(np.where(c, 0, 255).astype(np.uint8)).resize((16, 20))) < 128

    def classifica(self, a, conjunto=None):
        conjunto = conjunto or self.LETRAS
        ys, xs = np.where(a)
        if not len(ys) or ys.max() - ys.min() < 15:  # sujeira, não letra
            return ""
        n = self.norm(a)
        return min((m for m in self.modelos if m[0] in conjunto), key=lambda m: np.count_nonzero(m[1] != n))[0]

    def numero(self, a):
        """Número de 1 a 3 dígitos: separa os caracteres pelas colunas em branco."""
        ys, xs = np.where(a)
        if not len(ys) or ys.max() - ys.min() < 15:
            return ""
        a = a[ys.min():ys.max() + 1, :]
        cols = a.any(axis=0)
        out, ini = "", None
        for x, v in enumerate(list(cols) + [False]):
            if v and ini is None:
                ini = x
            elif not v and ini is not None:
                if x - ini >= 3:
                    out += self.classifica(a[:, ini:x], self.DIGITOS)
                ini = None
        return out if 1 <= len(out) <= 3 else ""


def tinta(img, x0, y0, x1, y1):
    x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0), bool)
    return img[max(0, y0):y1, max(0, x0):x1] < 140


def marcador(img, x0, y0, x1, y1):
    """'●' / '○' se houver um ponto de lista na faixa à esquerda do texto da linha."""
    a = tinta(img, x0, y0, x1, y1)
    if a.size == 0 or not a.any():
        return ""
    ys, xs = np.where(a)
    h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
    if not (6 <= h <= 30 and 6 <= w <= 30 and abs(h - w) <= 6):
        return ""
    cheio = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1].mean()
    return "●" if cheio > 0.55 else "○"


# ------------------------------------------------------------------ tabelas a partir da grade
def tabelas_da_grade(seg):
    nos = [("h", s) for s in seg["h_segs"]] + [("v", s) for s in seg["v_segs"]]
    pai = list(range(len(nos)))

    def acha(i):
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    for i, (ti, a) in enumerate(nos):
        for j in range(i + 1, len(nos)):
            tj, b = nos[j]
            if ti == tj:
                continue
            h, v = (a, b) if ti == "h" else (b, a)
            if v[1] - 6 <= h[0] <= v[2] + 6 and h[1] - 6 <= v[0] <= h[2] + 6:
                pai[acha(i)] = acha(j)
    grupos = collections.defaultdict(list)
    for i, n in enumerate(nos):
        grupos[acha(i)].append(n)

    def une(vals, tol):
        out = []
        for v in vals:
            if out and v - out[-1] <= tol:
                continue
            out.append(v)
        return out

    tabs = []
    for g in grupos.values():
        gh = [s for t, s in g if t == "h"]
        gv = [s for t, s in g if t == "v"]
        if len(gh) < 2 or len(gv) < 2:
            continue
        x0 = min(min(s[1] for s in gh), min(s[0] for s in gv))
        x1 = max(max(s[2] for s in gh), max(s[0] for s in gv))
        y0 = min(min(s[0] for s in gh), min(s[1] for s in gv))
        y1 = max(max(s[0] for s in gh), max(s[2] for s in gv))
        # as bordas externas entram sempre (às vezes são cinza-claras e não são detectadas como linha)
        ys = une(sorted([y0, y1] + [s[0] for s in gh if s[2] - s[1] >= 0.6 * (x1 - x0)]), 8)
        xs = une(sorted([x0, x1] + [s[0] for s in gv if s[2] - s[1] >= 0.5 * (y1 - y0)]), 8)
        if len(ys) < 2 or len(xs) < 2:
            continue
        tabs.append({"bbox": [x0, y0, x1, y1], "ys": ys, "xs": xs})
    return tabs


def junta_linhas(ws):
    """Agrupa palavras em linhas pela sobreposição vertical (pontilhados e vírgulas entram na linha)."""
    ws = sorted(ws, key=lambda w: -(w[4] - w[2]))
    linhas = []
    for w in ws:
        alt = max(1, w[4] - w[2])
        melhor = None
        for l in linhas:
            sob = min(l["b"], w[4]) - max(l["t"], w[2])
            if sob >= 0.5 * min(alt, l["b"] - l["t"]) and (melhor is None or sob > melhor[0]):
                melhor = (sob, l)
        if melhor:
            melhor[1]["ws"].append(w)
        else:
            linhas.append({"t": w[2], "b": w[4], "ws": [w]})
    linhas.sort(key=lambda l: l["t"])
    return [sorted(l["ws"], key=lambda w: w[1]) for l in linhas]


def texto_linha(ws):
    return " ".join(w[0] for w in ws)


def texto_celula(ws, img, x_esq):
    linhas = []
    for l in junta_linhas(ws):
        t = texto_linha(l).replace("•", "●")
        if img is not None and t[:1] not in "●○":
            mk = marcador(img, x_esq + 4, min(w[2] for w in l) - 2, l[0][1] - 6, max(w[4] for w in l) + 2)
            if mk:
                t = mk + " " + t
        linhas.append(t)
    return "\n".join(linhas)


GLIFOS = None
RE_INDICE = re.compile(r"^(Registro\s+Tipo\s+[A-Z0-9]{3})[\s.…·]*([^-–—(.\s].*)?$")


def pagina(ocr, seg, cor, img=None):
    palavras = []
    for l in ocr["linhas"]:
        palavras.extend(cor.corrige(l))
    tabelas = []
    for t in tabelas_da_grade(seg):
        xs, ys = t["xs"], t["ys"]
        rows, rowbbox = [], []
        for r in range(len(ys) - 1):
            cel = []
            for c in range(len(xs) - 1):
                ws = [w for w in palavras
                      if xs[c] <= (w[1] + w[3]) / 2 < xs[c + 1] and ys[r] <= (w[2] + w[4]) / 2 < ys[r + 1]]
                cel.append(texto_celula(ws, img, xs[c]))
            rows.append(cel)
            rowbbox.append([xs[0] * ESC, ys[r] * ESC, xs[-1] * ESC, ys[r + 1] * ESC])
        cab = [x.lower() for x in rows[0]] if rows else []
        # coluna Nº: "II" -> 11, "O" -> 0 (o OCR troca dígitos por letras parecidas)
        if cab and cab[0] in ("no", "nº", "n°", "n"):
            for r in range(1, len(rows)):
                v = rows[r][0].strip()
                if v and re.fullmatch(r"[0-9IlOo|]+", v) and not v.isdigit():
                    rows[r][0] = v.translate(str.maketrans("IlOo|", "11001"))
        # tabela de campos (Nº | Campo | Descrição | Tam. | Tipo), com ou sem linha de cabeçalho na página
        num = lambda v: re.fullmatch(r"\d+|-", v.strip() or "x") is not None
        campos = len(xs) == 6 and rows and (
            any(x.startswith("tam") for x in cab) or
            sum(num(r[3]) for r in rows) >= 0.5 * sum(bool(r[3].strip()) for r in rows) > 0)
        if img is not None and campos:
            # dígitos isolados que o OCR ignorou na coluna Nº
            # só aceita o número lido se for o próximo da sequência da tabela
            ultimo = None
            for r in range(len(rows)):
                v = rows[r][0].strip()
                if v.isdigit():
                    ultimo = int(v)
                    continue
                if not v and rows[r][1].strip() and ultimo is not None and not rows[r][1].lower().startswith("campo"):
                    a = tinta(img, xs[0] + 4, ys[r] + 4, xs[1] - 4, ys[r + 1] - 4)
                    if a.size and a.any():
                        lido = GLIFOS.numero(a)
                        if lido.isdigit() and int(lido) == ultimo + 1:
                            rows[r][0] = lido
                            ultimo = int(lido)
        # coluna Tipo vazia mas com tinta: letra isolada que o OCR ignorou
        if img is not None and campos:
            ic = len(xs) - 2
            if True:
                for r in range(len(rows)):
                    if not rows[r][ic].strip():
                        a = tinta(img, xs[ic] + 6, ys[r] + 4, xs[ic + 1] - 6, ys[r + 1] - 4)
                        if a.size and a.any():
                            rows[r][ic] = GLIFOS.classifica(a)
        tabelas.append({"bbox": [v * ESC for v in t["bbox"]], "rows": rows, "rowbbox": rowbbox})
    # linhas de texto: todas as palavras (o extrator descarta as que estão dentro de tabelas)
    linhas = []
    for l in junta_linhas(palavras):
        txt = texto_linha(l)
        txt = re.sub(r"^(Registro\s+Tipo\s+)([A-Z0-9])\s([A-Z0-9]{2})\b", r"\1\2\3", txt)  # "T VC" -> "TVC"
        m = RE_INDICE.match(txt)  # índice "Registro Tipo XXX ....... Nome": o OCR perde pontilhados
        if m:
            txt = m.group(1) + (" ..... " + m.group(2) if m.group(2) else "")
        linhas.append({"text": txt, "x0": l[0][1] * ESC,
                       "top": min(w[2] for w in l) * ESC, "bottom": max(w[4] for w in l) * ESC})
    return {"lines": linhas, "tables": tabelas}


def main():
    global GLIFOS
    args = sys.argv[1:]
    d_img = None
    if "--img" in args:
        i = args.index("--img")
        d_img = args[i + 1]
        del args[i:i + 2]
    d_ocr, d_seg, saida = args[:3]
    cor = Corretor(args[3:])
    GLIFOS = Glifos()
    paginas = []
    for n in sorted(n for n in os.listdir(d_ocr) if n.endswith(".json")):
        ocr = json.load(open(os.path.join(d_ocr, n), encoding="utf-8-sig"))
        seg = json.load(open(os.path.join(d_seg, n)))
        img = np.asarray(Image.open(os.path.join(d_img, n.replace(".json", ".png"))).convert("L")) if d_img else None
        paginas.append(pagina(ocr, seg, cor, img))
    json.dump(paginas, open(saida, "w", encoding="utf-8"), ensure_ascii=False)
    print(len(paginas), "páginas")


if __name__ == "__main__":
    main()
