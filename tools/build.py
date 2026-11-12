"""
Monta a página única do validador (dist/validador-fortes.html), com o motor, a interface e
as especificações de todas as versões do leiaute (spec/leiaute_<versão>.json) embutidas —
basta abrir o arquivo no navegador.

As especificações vão comprimidas (gzip + base64) e só são abertas quando a versão é usada.

Uso:
    python tools/build.py
"""
import base64
import glob
import gzip
import json
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ler(*p):
    with open(os.path.join(RAIZ, *p), encoding="utf-8") as f:
        return f.read()


def specs():
    out, info = {}, {}
    for p in glob.glob(os.path.join(RAIZ, "spec", "leiaute_*.json")):
        m = re.search(r"leiaute_(\d+)\.json$", p)
        if not m:
            continue  # *.raw.json
        with open(p, encoding="utf-8") as f:
            spec = json.load(f)
        txt = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if spec.get("origem"):
            info[m.group(1)] = {"origem": spec["origem"]}
        out[m.group(1)] = base64.b64encode(gzip.compress(txt, 9, mtime=0)).decode("ascii")
    return dict(sorted(out.items(), key=lambda kv: -int(kv[0]))), info


def main():
    leiautes, info = specs()
    leiautes_js = ("window.LEIAUTES_FORTES=" + json.dumps(leiautes, separators=(",", ":")) + ";"
                   "window.LEIAUTES_INFO=" + json.dumps(info, ensure_ascii=False) + ";")

    html = ler("src", "index.html")
    partes = {
        "/*CSS*/": ler("src", "estilo.css"),
        "/*LEIAUTES*/": leiautes_js,
        "/*VALIDADOR*/": ler("src", "validador.js"),
        "/*APP*/": ler("src", "app.js"),
    }
    for marca, conteudo in partes.items():
        assert html.count(marca) == 1, marca
        html = html.replace(marca, conteudo.replace("</script", "<\\/script"))

    dist = os.path.join(RAIZ, "dist")
    os.makedirs(dist, exist_ok=True)
    saida = os.path.join(dist, "validador-fortes.html")
    with open(saida, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{saida} ({os.path.getsize(saida) / 1024:.0f} KB) - versões: {', '.join(leiautes)}")

    # publicação na web (Netlify / Cloudflare Pages): index.html leva ao validador
    nome = os.path.basename(saida)
    with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><title>Validador Fortes Fiscal</title>'
                f'<meta http-equiv="refresh" content="0; url={nome}"><a href="{nome}">Abrir o validador</a>\n')
    with open(os.path.join(dist, "_headers"), "w", encoding="utf-8") as f:
        f.write(ler("src", "_headers"))


if __name__ == "__main__":
    main()
