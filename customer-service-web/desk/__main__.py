import argparse
import socket

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Merchant support web desk")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # Reserve the port before application startup/recovery. Reopening the launcher
    # must not disturb a running instance's upload jobs or outgoing message state.
    listener = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((args.host, args.port))
    except OSError:
        listener.close()
        raise SystemExit(
            f"Port {args.port} is unavailable. If the desk is already running, open its browser page."
        )
    try:
        server = uvicorn.Server(
            uvicorn.Config("desk.app:create_app", factory=True, host=args.host, port=args.port, workers=1)
        )
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
