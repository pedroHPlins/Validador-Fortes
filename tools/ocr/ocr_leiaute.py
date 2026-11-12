"""
Leitura por OCR dos PDFs de leiaute cujo texto está desenhado como vetores (sem texto extraível,
ex.: versões 190 e 195). Gera um cache no mesmo formato de extrair_leiaute.ler_pdf, usado depois com
    python tools/extrair_leiaute.py x spec/leiaute_195.raw.json --cache spec/.cache_pdf_195.json

Etapas (cada uma retoma de onde parou):
  1. render.py        páginas -> PNG 300 dpi (pypdfium2)
  2. grade.py         detecta as linhas das tabelas na imagem e as apaga (melhora o OCR)
  3. ocr.ps1          OCR do Windows (Windows.Media.Ocr, pt-BR) -> palavras com posição
  4. montar_cache.py  monta células pela grade, corrige palavras com o vocabulário de leiautes com texto

Uso (Windows, com o idioma pt-BR do OCR instalado; requer pypdfium2, pillow, numpy):
    python tools/ocr/ocr_leiaute.py "P:/Base/Leiaute Fortes 195.pdf" 195 <pasta_temporaria>
"""
import glob
import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    pdf, versao, tmp = sys.argv[1:]
    img, limpa, seg, ocr = (os.path.join(tmp, f"{x}{versao}") for x in ("img", "lp", "seg", "ocrp"))
    py = sys.executable
    subprocess.check_call([py, os.path.join(AQUI, "render.py"), pdf, img])
    subprocess.check_call([py, os.path.join(AQUI, "grade.py"), img, limpa, seg])
    subprocess.check_call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           os.path.join(AQUI, "ocr.ps1"), limpa, ocr])
    vocab = [p for p in glob.glob(os.path.join(RAIZ, "spec", "leiaute_*.raw.json"))
             if not p.endswith(f"_{versao}.raw.json")]
    saida = os.path.join(RAIZ, "spec", f".cache_pdf_{versao}.json")
    subprocess.check_call([py, os.path.join(AQUI, "montar_cache.py"), ocr, seg, saida, "--img", limpa] + vocab)
    print("cache:", saida)


if __name__ == "__main__":
    main()
