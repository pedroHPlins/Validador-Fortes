import pypdfium2 as pdfium, sys, os
pdf, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
doc = pdfium.PdfDocument(pdf)
for i in range(len(doc)):
    f = os.path.join(out, f"p{i:04d}.png")
    if os.path.exists(f): continue
    img = doc[i].render(scale=300/72, grayscale=True).to_pil()
    img.save(f)
print("ok", len(doc))
