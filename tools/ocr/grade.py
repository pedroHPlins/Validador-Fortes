# Detecta as linhas das tabelas na imagem renderizada (corridas longas de pixels escuros),
# grava a imagem sem as linhas (para o OCR) e um JSON com os segmentos (pixels).
import json
import os
import sys

import numpy as np
from PIL import Image

MIN_H = 90   # px (300 dpi): ~7,6 mm
MIN_V = 55


def corridas(v, minimo):
    """Corridas de True em um vetor 1D: lista de (ini, fim) com fim exclusivo."""
    d = np.diff(np.concatenate(([0], v.astype(np.int8), [0])))
    ini = np.where(d == 1)[0]
    fim = np.where(d == -1)[0]
    ok = (fim - ini) >= minimo
    return list(zip(ini[ok], fim[ok]))


def segmentos(esc, minimo, horizontal):
    a = esc if horizontal else esc.T
    brutos = []
    for y in range(a.shape[0]):
        for x0, x1 in corridas(a[y], minimo):
            brutos.append([y, y + 1, int(x0), int(x1)])
    # junta corridas em linhas vizinhas (espessura da linha)
    brutos.sort(key=lambda s: (s[0], s[2]))
    segs = []
    for s in brutos:
        for t in segs:
            if t[0] - 1 <= s[0] <= t[1] + 1 and abs(s[2] - t[2]) <= 4 and abs(s[3] - t[3]) <= 4:
                t[1] = max(t[1], s[1])
                t[2], t[3] = min(t[2], s[2]), max(t[3], s[3])
                break
        else:
            segs.append(list(s))
    return segs  # [a0, a1 (espessura), b0, b1 (extensão)]


def processa(src, dst_img, dst_json):
    im = Image.open(src).convert("L")
    a = np.asarray(im)
    esc = a < 215  # linhas cinza-claras (~180) também
    hs = segmentos(esc, MIN_H, True)
    vs = segmentos(esc, MIN_V, False)
    limpa = a.copy()
    for y0, y1, x0, x1 in hs:
        limpa[max(0, y0 - 2):y1 + 2, max(0, x0 - 1):x1 + 1] = 255
    for x0, x1, y0, y1 in vs:
        limpa[max(0, y0 - 1):y1 + 1, max(0, x0 - 2):x1 + 2] = 255
    Image.fromarray(limpa).save(dst_img)
    with open(dst_json, "w") as f:
        json.dump({"w": im.width, "h": im.height,
                   "h_segs": [[(y0 + y1) / 2, x0, x1] for y0, y1, x0, x1 in hs],
                   "v_segs": [[(x0 + x1) / 2, y0, y1] for x0, x1, y0, y1 in vs]}, f)


if __name__ == "__main__":
    src, out_img, out_seg = sys.argv[1:4]
    os.makedirs(out_img, exist_ok=True)
    os.makedirs(out_seg, exist_ok=True)
    for nome in sorted(os.listdir(src)):
        if not nome.endswith(".png"):
            continue
        di = os.path.join(out_img, nome)
        if os.path.exists(di):
            continue
        processa(os.path.join(src, nome), di, os.path.join(out_seg, nome.replace(".png", ".json")))
    print("ok")
