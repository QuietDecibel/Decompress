"""Command line: python -m decompress <command>

  serve      run the web app (multi-view grid, discovery, queue)       --host 127.0.0.1 --port 8000
  discover   list cameras on your LAN                                    [--subnet 192.168.1.0/24]
  analyze    analyse a video file -> annotated mp4 + .motion.json        video [--out DIR]
  train      train the movement classifier from labelled clips          labels.json
  backends   show available vision backends
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="decompress", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", help="mediapipe | yolo | yolo-det | hog (default: best available)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s.add_argument("--hub-project", help="report queue activity to this Progress Hub project")
    d = sub.add_parser("discover"); d.add_argument("--subnet"); d.add_argument("--timeout", type=float, default=2.0)
    a = sub.add_parser("analyze"); a.add_argument("video"); a.add_argument("--out", default="decompress_data")
    a.add_argument("--no-video", action="store_true")
    t = sub.add_parser("train"); t.add_argument("labels"); t.add_argument("--out", default="decompress_data/models/movement.json")
    sub.add_parser("backends")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        uvicorn.run(create_app(args.backend, args.hub_project), host=args.host, port=args.port)
    elif args.cmd == "discover":
        from .discovery import discover_all, candidate_urls
        cams = discover_all(args.subnet, args.timeout)
        for c in cams:
            print(f"{c.ip:15} ports={c.ports} via={'+'.join(c.methods)} {c.name or c.vendor_hint or c.rtsp_server}")
            if c.rtsp:
                print("   try:", candidate_urls(c.ip, vendor=c.vendor_hint, user="USER", password="PASS")[0])
        if not cams:
            print("no cameras found")
    elif args.cmd == "analyze":
        from .jobs import JobQueue
        from .pipeline import Analyzer
        from .vision import get_backend
        q = JobQueue(Path(args.out), analyzer_factory=lambda: Analyzer(get_backend(args.backend)))
        job = q.add(args.video, write_video=not args.no_video)
        q.run_job(job)
        print(json.dumps({k: job[k] for k in ("state", "people", "outputs", "error")}, indent=2))
        sys.exit(0 if job["state"] == "done" else 1)
    elif args.cmd == "train":
        from .training import train_from_labels
        clf, n = train_from_labels(args.labels)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        clf.save(args.out)
        print(f"trained on {n} clips, labels={sorted(set(clf.y))} -> {args.out}")
    elif args.cmd == "backends":
        from .vision import available_backends
        print(", ".join(available_backends()))


if __name__ == "__main__":
    main()
