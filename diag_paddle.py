"""Diagnostic only: minimal PaddleOCR repro. Not part of the production pipeline."""
import sys
from PIL import Image, ImageDraw

from paddleocr import PaddleOCR

print("building engine", flush=True)
engine = PaddleOCR(
    lang="chinese_cht",
    ocr_version="PP-OCRv5",
    device="cpu",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    text_det_limit_type="max",
    text_det_limit_side_len=4096,
)
print("engine ready", flush=True)

img = Image.new("RGB", (600, 200), "white")
d = ImageDraw.Draw(img)
d.text((20, 80), "Synthetic test 12345", fill="black")
img.save("/tmp/diag_small.png")

print("predict small", flush=True)
res = engine.predict("/tmp/diag_small.png")
print("small ok", flush=True)
for r in res:
    print("small texts:", r.get("rec_texts"), flush=True)

if len(sys.argv) > 1:
    print("predict", sys.argv[1], flush=True)
    res = engine.predict(sys.argv[1])
    print("real ok", flush=True)
    for r in res:
        print("n boxes:", len(r.get("rec_texts") or []), flush=True)
