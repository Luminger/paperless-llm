"""CLI: `paperless-llm serve` / `paperless-llm seed`."""

from __future__ import annotations

import argparse
import asyncio
import sys


def _wait_for_paperless(url: str, timeout: int) -> None:
    import time

    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url.rstrip('/')}/api/", timeout=5)
            if r.status_code in (200, 401, 403):
                print("paperless is up")
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    print(f"paperless not reachable within {timeout}s", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="paperless-llm")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--reload", action="store_true")

    seed = sub.add_parser(
        "seed", help="seed the (ad-hoc) paperless instance with the test corpus"
    )
    seed.add_argument("--url", required=True, help="paperless base url")
    seed.add_argument("--token", default="", help="paperless API token")
    seed.add_argument("--username", default="", help="fetch a token with these credentials")
    seed.add_argument("--password", default="")
    seed.add_argument(
        "--wait", action="store_true", help="wait for document consumption to finish"
    )
    seed.add_argument(
        "--wait-for-paperless",
        type=int,
        default=0,
        metavar="SECONDS",
        help="poll until paperless is reachable before seeding",
    )

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    elif args.command == "seed":
        from app.seeding import seed_corpus

        if args.wait_for_paperless:
            _wait_for_paperless(args.url, args.wait_for_paperless)
        report = asyncio.run(
            seed_corpus(
                args.url,
                args.token,
                username=args.username,
                password=args.password,
                wait=args.wait,
            )
        )
        print(report)
    else:  # pragma: no cover
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
