"""Create a sample board, so you can see what Mindboard looks like with content
in it. Usage:  python scripts/demo_board.py [--data-dir PATH]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mindboard.assets import store_bytes  # noqa: E402
from mindboard.boards import conn_for, create_board  # noqa: E402
from mindboard.ids import ulid  # noqa: E402
from mindboard.ops import apply_envelopes  # noqa: E402
from mindboard.paths import DataDir  # noqa: E402

def swatch_png() -> bytes:
    """A stand-in site plan, drawn rather than downloaded - the demo has to work
    with no network."""
    import io

    from PIL import Image, ImageDraw

    w, h = 480, 300
    im = Image.new("RGB", (w, h), "#131822")
    d = ImageDraw.Draw(im)
    for x in range(0, w, 24):
        d.line([(x, 0), (x, h)], fill="#1b2331")
    for y in range(0, h, 24):
        d.line([(0, y), (w, y)], fill="#1b2331")
    d.line([(40, 250), (300, 250), (300, 40)], fill="#6ea8fe", width=6)
    d.line([(300, 160), (440, 160)], fill="#6ea8fe", width=6)
    for cx, cy in [(300, 250), (300, 160), (300, 40)]:
        d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill="#ff9e64")
    d.rectangle([60, 60, 200, 150], outline="#6fd7a4", width=3)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def node(**kw):
    base = {"id": ulid(), "type": "text", "pos": [0, 0], "size": [None, None], "z": 0,
            "title": "", "content": {}, "style": {}, "collapsed": True, "provenance": "user",
            "parent": None}
    base.update(kw)
    return base


def edge(src, tgt, label, kind="relates"):
    return {"id": ulid(), "source": src, "target": tgt, "sourceHandle": None,
            "targetHandle": None, "label": label, "kind": kind, "directed": True,
            "style": {}, "provenance": "user"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir")
    args = ap.parse_args()
    dd = DataDir(Path(args.data_dir) if args.data_dir else None).ensure()

    board = create_board(dd, "Nottingham DT ideas", "Sensor siting, cost and the VLM angle")
    bid = board["id"]
    c = conn_for(dd, bid)
    img = store_bytes(dd, c, bid, swatch_png(), declared_mime="image/png",
                      orig_name="site-plan.png")

    hypothesis = node(pos=[-60, -300], title="Hypothesis",
                      content={"text": "Traffic density predicts dwell time at junction 4."},
                      style={"fontSize": 15})
    method = node(pos=[480, -140], title="Method",
                  content={"text": "Fuse loop-detector counts with VLM scene descriptions."})
    cost = node(pos=[500, 200], title="Budget ceiling",
                content={"text": "£18k for the pilot corridor."}, style={"color": "#e6b95c"})
    plan = node(pos=[-90, 60], type="image", size=[300, None], title="Site plan",
                content={"sha256": img["sha256"], "alt": "corridor plan",
                       "text": "Junction 4 and the two approaches we can instrument."})
    sensors = node(pos=[-540, -120], type="meta", title="Sensors",
                   content={"text": "Everything about siting and hardware"})
    lidar = node(parent=sensors["id"], pos=[0, 0], title="Lidar placement",
                 content={"text": "Corner mounting cuts occlusion by roughly a third."})
    cameras = node(parent=sensors["id"], pos=[420, 30], title="Camera count",
                   content={"text": "Four per junction, two if we accept blind spots."})
    power = node(parent=sensors["id"], pos=[190, 300], title="Power draw",
                 content={"text": "Mains at three of six poles; the rest need solar."})

    nodes = [hypothesis, method, cost, plan, sensors, lidar, cameras, power]
    edges = [
        edge(hypothesis["id"], method["id"], "tested by", "supports"),
        edge(method["id"], cost["id"], "pushes past", "contradicts"),
        edge(lidar["id"], cost["id"], "drives", "causes"),
        edge(cameras["id"], cost["id"], "drives", "causes"),
        edge(power["id"], cameras["id"], "constrains", "part-of"),
        edge(hypothesis["id"], plan["id"], "grounded in", "example-of"),
    ]
    envelopes = [{"opId": ulid(), "op": {"kind": "node.create", "node": n}} for n in nodes]
    envelopes += [{"opId": ulid(), "op": {"kind": "edge.create", "edge": e}} for e in edges]
    result = apply_envelopes(c, bid, envelopes)
    print(f"{board['title']}  ->  {bid}")
    print(f"{len(result['applied'])} applied, {len(result['rejected'])} rejected")
    for r in result["rejected"]:
        print("  rejected:", r)


if __name__ == "__main__":
    main()
