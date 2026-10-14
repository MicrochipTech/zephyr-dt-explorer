"""Deterministic JSON snapshot of what the explorer's BuildModel knows about one build.

Usage:  py tools/snapshot_build.py <build_dir> [-o out.json]

All paths are relative to the west workspace (BuildModel.rel); paths below the build directory
itself are written as "<build>/...". No timestamps, no absolute paths.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:                                    # layout after the package move (card P1.1)
    from dt_explorer.core.build import BuildModel, origin_kind
except ImportError:                     # today's single-file layout (importing has no side effects:
    from dt_explorer import BuildModel, origin_kind  # server starts only under the __main__ guard)

SCHEMA = 1


def snapshot(build_dir) -> dict:
    build = Path(build_dir).resolve()
    m = BuildModel(build)
    west = m.west_top.resolve()

    def p(x) -> str:
        """Machine-independent path string."""
        if not x:
            return ""
        s = str(x)
        try:
            return "<build>/" + Path(s).resolve().relative_to(build).as_posix()
        except (ValueError, OSError):
            pass
        r = m.rel(s)
        if Path(r).is_absolute() or (len(r) > 1 and r[1] == ":"):
            r = s
        return r.replace("\\", "/")

    nodes = {}
    for path in sorted(m.by_path):
        n = m.by_path[path]
        st, _msg = m.state(n)
        o = m.origins.get(path)
        nodes[path] = {
            "state": st,
            "status": n.status,
            "compats": list(n.compats),
            "binding": p(n.binding_path) if n.binding_path else "",
            "in_image": n.dep_ordinal in m.device_ords,
            "origin_kind": m.node_origin_kind(n),
            "touched_by_overlay": m.touched_by_overlay(n),
            "origin": f"{p(o.file)}:{o.line}" if o and o.file else "",
            "props": {k: f"{p(v.file)}:{v.line}" if v.file else ""
                      for k, v in sorted(o.props.items())} if o else {},
            "drivers": sorted(p(d) for d in m.drivers_for(n)),
        }

    inc = []

    def walk(t):
        if t:
            inc.append(p(t["path"]))
            for c in t["children"]:
                walk(c)
    walk(m.includes)

    refs = sorted(
        ({"file": p(r["file"]), "line": r["line"], "macro": r["macro"],
          "node": r["node"].path if r["node"] is not None else None, "code": r["code"]}
         for r in m.code_refs),
        key=lambda r: (r["file"], r["line"], r["macro"]))

    orphans = sorted(([c, sorted(p(f) for f in files)] for c, files in m.orphan_drivers()))

    return {
        "schema": SCHEMA,
        "linked": bool(m.linked),
        "counts": {
            "nodes": len(nodes),
            "include_files": len(inc),
            "include_files_unique": len(set(inc)),
            "code_refs": len(refs),
            "orphan_drivers": len(orphans),
            "device_ords": len(m.device_ords),
            "compiled_files": len(m.compiled),
        },
        "nodes": nodes,
        "include_files": sorted(set(inc)),
        "code_refs": refs,
        "orphan_drivers": orphans,
        "aliases": sorted(m.aliases),
        "chosen": sorted(m.chosen),
    }


def dumps(snap: dict) -> str:
    return json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("build_dir")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    text = dumps(snapshot(a.build_dir))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"{a.out}: sha256={hashlib.sha256(text.encode('utf-8')).hexdigest()}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
