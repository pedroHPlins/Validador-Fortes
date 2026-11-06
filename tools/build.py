"""
Monta a página única do validador (dist/validador-fortes-<versão>.html), com
especificação, motor e interface embutidos — basta abrir o arquivo no navegador.

Uso:
    python tools/build.py [spec/leiaute_204.json]
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ler(*p):
    with open(os.path.join(RAIZ, *p), encoding="utf-8") as f:
        return f.read()


def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "spec", "leiaute_204.json")
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    spec_js = "window.SPEC_FORTES=" + json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";"

    html = ler("src", "index.html")
    partes = {
        "/*CSS*/": ler("src", "estilo.css"),
        "/*SPEC*/": spec_js,
        "/*VALIDADOR*/": ler("src", "validador.js"),
        "/*APP*/": ler("src", "app.js"),
    }
    for marca, conteudo in partes.items():
        assert html.count(marca) == 1, marca
        html = html.replace(marca, conteudo.replace("</script", "<\\/script"))

    os.makedirs(os.path.join(RAIZ, "dist"), exist_ok=True)
    saida = os.path.join(RAIZ, "dist", f"validador-fortes-{spec['versao']}.html")
    with open(saida, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{saida} ({os.path.getsize(saida) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
