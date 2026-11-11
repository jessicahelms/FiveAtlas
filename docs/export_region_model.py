"""Turn a VMamba pose checkpoint into the single ONNX file FiveAtlas predicts with.

Dev tool, not part of the app. Run in an environment with torch, onnx, onnxruntime
and the training notebook's imports:

    python export_region_model.py <training notebook.ipynb> <best.pt> <out.onnx> [<canvas dir with <gene>.tif> ...]

The seg logits and the tissue-silhouette logits are exported; the pose heads are not.
FiveAtlas needs a fixed 640 x 640 input, so the pyramid pooling's adaptive average
pool (which ONNX can't express at sizes that don't divide the input) is written out
as the same bins PyTorch uses. Before saving, the script checks that rewrite and the
exported file against the checkpoint on every canvas given, and refuses to write the
metadata if a label differs.
"""
import json
import sys
from pathlib import Path

import numpy as np

NB, CKPT, OUT, *CANVASES = sys.argv[1:]
METADATA_KEY = "fiveatlas_region_model"

nb = json.loads(Path(NB).read_text(encoding="utf-8"))
code = [c for c in nb["cells"] if c["cell_type"] == "code"]
NS = {"__name__": "nb_defs"}
for ci in range(3):
    exec(compile("".join(code[ci]["source"]), f"nb_cell{ci}", "exec"), NS)
torch = NS["torch"]

ck = torch.load(CKPT, map_location="cpu", weights_only=False)
p = ck["params"]
if p.get("real_resample_to_render_px"):
    raise SystemExit("this checkpoint expects a resampled canvas; FiveAtlas does not do that step")
net = NS["build_pose_net"](p["variant"], num_classes=len(ck["class_ids"]) + 1, in_chans=int(ck["in_channels"]))
NS["load_pose_checkpoint"](net, ck["model"])
net.eval()


class SegOnly(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, x):
        o = self.m(x)
        return o[0], o[1]


def exact_adaptive(x, output_size):
    if isinstance(output_size, int):
        output_size = (output_size, output_size)
    H, W = int(x.shape[-2]), int(x.shape[-1])
    Sh, Sw = int(output_size[0]), int(output_size[1])
    rows = []
    for i in range(Sh):
        r0, r1 = (i * H) // Sh, -((-(i + 1) * H) // Sh)
        cells = []
        for j in range(Sw):
            c0, c1 = (j * W) // Sw, -((-(j + 1) * W) // Sw)
            cells.append(x[..., r0:r1, c0:c1].mean(dim=(-2, -1), keepdim=True))
        rows.append(torch.cat(cells, dim=-1))
    return torch.cat(rows, dim=-2)


def inputs(d):
    image, _ = NS["read_real_canvas"](d)
    return torch.from_numpy(((image - NS["NORM_MEAN"]) / NS["NORM_STD"]).transpose(2, 0, 1)).float().unsqueeze(0)


size = int(p.get("img_size", 640))
wrapped = SegOnly(net).eval()
samples = [inputs(d) for d in CANVASES] or [torch.rand(1, int(ck["in_channels"]), size, size)]
with torch.no_grad():
    want = [wrapped(x) for x in samples]
    torch.nn.functional.adaptive_avg_pool2d = exact_adaptive
    torch.onnx.export(wrapped, (samples[0],), OUT, input_names=["x"], output_names=["logits", "hemi"],
                      opset_version=18, dynamo=False, do_constant_folding=True)

import onnx  # noqa: E402
import onnxruntime as ort  # noqa: E402

sess = ort.InferenceSession(OUT, providers=["CPUExecutionProvider"])
for x, (lt, ht) in zip(samples, want):
    lo, ho = sess.run(["logits", "hemi"], {"x": x.numpy()})
    dl, dh = np.abs(lo - lt.numpy()).max(), np.abs(ho - ht.numpy()).max()
    same = np.array_equal(lo.argmax(1), lt.numpy().argmax(1)) and np.array_equal(ho > 0, ht.numpy() > 0)
    print(f"max|dlogit| {dl:.2e}  max|dhemi| {dh:.2e}  labels identical {same}")
    if not same:
        raise SystemExit("the exported model disagrees with the checkpoint; metadata not written")

m = onnx.load(OUT)
mean, std = NS["NORM_MEAN"], NS["NORM_STD"]
meta = {"model_id": "VMamba-small pose 18ch idx=22 ep=10 btch=4 rnd=1362865 (folder 12, miou=0.844)" if "rnd=1362865" in CKPT else Path(CKPT).parent.parent.name,
        "channel_names": list(ck["channel_names"]), "division_names": {str(k): v for k, v in ck["division_names"].items()},
        "img_size": size, "norm_mean": [float(v) for v in mean], "norm_std": [float(v) for v in std]}
e = m.metadata_props.add()
e.key, e.value = METADATA_KEY, json.dumps(meta)
onnx.save(m, OUT)
print("wrote", OUT, f"{Path(OUT).stat().st_size / 1e6:.1f} MB")
print(meta["model_id"])
