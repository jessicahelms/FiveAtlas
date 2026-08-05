import time
from pathlib import Path

import datasets as ds
from stains import StainStack

OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")
DS = "DNMT3A_002_27_38_Het_F"
d = ds.get_dataset(DS)
mf = d["sources"]["morphology_focus"]
print("source:", mf["kind"], mf["path"])

t = time.time()
ss = StainStack(mf)
print("prime (dims+level): %.2fs" % (time.time() - t))
print("info:", ss.info())

# composite DAPI + membrane + 18S, timing each channel's first read
spec = []
for idx, color in [(0, [80, 130, 255]), (1, [90, 230, 120]), (2, [200, 200, 210])]:
    t = time.time()
    ss._plane(idx)  # force read + contrast
    print("  channel %d read+contrast: %.2fs  contrast=%s dataMax=%d"
          % (idx, time.time() - t,
             [round(c) for c in ss._contrast[idx]], int(ss._planes[idx].max())))
    lo, hi = ss._contrast[idx]
    spec.append({"index": idx, "color": color, "min": lo, "max": hi, "visible": True})

t = time.time()
png = ss.composite_png(spec)
print("composite_png (3 ch): %.2fs  bytes=%d" % (time.time() - t, len(png)))
(OUT / "stains_composite.png").write_bytes(png)
print("saved stains_composite.png  levelShape=%s" % (ss.info()["levelShape"],))
