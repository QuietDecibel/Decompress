"""Generate packaging/decompress.ico (and .png) — a camera lens with a tracked stick figure. Needs only OpenCV."""
import struct
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent


def draw(size: int) -> np.ndarray:
    s = 512
    img = np.zeros((s, s, 4), np.uint8)
    bg = (38, 30, 23, 255)                                            # BGRA dark slate
    cv2.rectangle(img, (40, 40), (s - 40, s - 40), bg, -1)
    for cx, cy in ((40, 40), (s - 40, 40), (40, s - 40), (s - 40, s - 40)):   # rounded corners
        cv2.circle(img, (cx, cy), 40, bg, -1, cv2.LINE_AA)
    cv2.rectangle(img, (0, 40), (s, s - 40), bg, -1)
    cv2.rectangle(img, (40, 0), (s - 40, s), bg, -1)
    c = s // 2
    cv2.circle(img, (c, c), 190, (255, 154, 76, 255), 26, cv2.LINE_AA)   # lens ring (accent blue)
    cv2.circle(img, (c, c), 150, (60, 48, 38, 255), -1, cv2.LINE_AA)
    white, green = (245, 240, 235, 255), (133, 191, 63, 255)
    joints = {"head": (c, c - 95), "neck": (c, c - 55), "hip": (c, c + 30), "lh": (c - 70, c - 110), "rh": (c + 75, c - 10),
              "le": (c - 50, c - 60), "re": (c + 45, c - 40), "lk": (c - 40, c + 80), "rk": (c + 45, c + 75),
              "lf": (c - 55, c + 130), "rf": (c + 60, c + 130)}
    for a, b in (("neck", "hip"), ("neck", "le"), ("le", "lh"), ("neck", "re"), ("re", "rh"),
                 ("hip", "lk"), ("lk", "lf"), ("hip", "rk"), ("rk", "rf")):
        cv2.line(img, joints[a], joints[b], green, 20, cv2.LINE_AA)
    cv2.circle(img, joints["head"], 30, white, -1, cv2.LINE_AA)
    for k, p in joints.items():
        if k != "head":
            cv2.circle(img, p, 13, white, -1, cv2.LINE_AA)
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    pngs = [cv2.imencode(".png", draw(n))[1].tobytes() for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for n, png in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


if __name__ == "__main__":
    write_ico(OUT / "decompress.ico")
    cv2.imwrite(str(OUT / "decompress.png"), draw(256))
    print(OUT / "decompress.ico")
