import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="litexplorer",
        description="Local-first research literature dashboard",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--datadir",
        default=None,
        help="Data directory for DB, PDFs and cache (default: ~/.litexplorer)",
    )
    args = parser.parse_args()

    if args.datadir is not None:
        os.environ["LITEXPLORER_DATA_DIR"] = args.datadir

    # Import uvicorn only after env is set so Settings() picks up LITEXPLORER_DATA_DIR
    # when litexplorer.app is loaded by uvicorn.
    import uvicorn

    uvicorn.run("litexplorer.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
