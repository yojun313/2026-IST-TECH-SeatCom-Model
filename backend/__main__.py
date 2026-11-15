"""Start the standalone backend: uv run --frozen python -m backend."""
import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="SeatCom SQLite backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8028)
    args = parser.parse_args()
    uvicorn.run("backend.main:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
