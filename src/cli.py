from __future__ import annotations

import argparse
import sys

from . import __version__, config, db


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import create_app

    app = create_app()
    print(f"me want pickle v{__version__}  ->  http://{args.host}:{args.port}/   (data: {config.DATA_DIR})")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False, threaded=True)
    return 0


def cmd_facilities(_: argparse.Namespace) -> int:
    db.init_db()
    rows = db.list_facilities()
    if not rows:
        print("no facilities yet; run `mwp serve` and add one in the browser")
        return 0
    for f in rows:
        print(f"[{f['id']}] org {f['org_id']:<7} {f['name']:<32} {f['username']}  verified {f['verified_at'] or 'never'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mwp", description="CourtReserve court and event watcher")
    p.add_argument("--version", action="version", version=f"mwp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default=config.DEFAULT_HOST)
    s.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    s.add_argument("--debug", action="store_true")
    s.set_defaults(fn=cmd_serve)

    f = sub.add_parser("facilities", help="list configured facilities")
    f.set_defaults(fn=cmd_facilities)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
