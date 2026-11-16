"""Start the FastAPI backend, configured local vector API, and Streamlit UI."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent
STREAMLIT_APP = PROJECT_ROOT / "streamlit_app"
API_HOST = "127.0.0.1"
API_PORT = 8000
VECTOR_HOST = "127.0.0.1"
VECTOR_PORT = 8001
STREAMLIT_PORT = 8501
STARTUP_TIMEOUT_SECONDS = 30


def _environment_from_dotenv() -> dict[str, str]:
    environment = os.environ.copy()
    file_values = dotenv_values(PROJECT_ROOT / ".env")
    for name, value in file_values.items():
        if name not in environment and value is not None:
            environment[name] = value
    return environment


def _require_modules() -> None:
    missing = [
        name
        for name in ("uvicorn", "streamlit")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise RuntimeError(
            "Missing required packages: "
            + ", ".join(missing)
            + ". Install the project requirements first."
        )


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((host, port))
        except OSError:
            return False
    return True


def _require_free_port(host: str, port: int) -> None:
    if not _port_is_free(host, port):
        raise RuntimeError(f"Port {port} on {host} is already in use.")


def _wait_for_service(
    process: subprocess.Popen | None,
    url: str,
    service_name: str,
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        exit_code = process.poll() if process is not None else None
        if exit_code is not None:
            raise RuntimeError(
                f"The {service_name} process exited during startup (exit code {exit_code})."
            )
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError, TimeoutError):
            time.sleep(0.25)
    raise TimeoutError(
        f"{service_name} did not become ready within {STARTUP_TIMEOUT_SECONDS} seconds."
    )


def _local_vector_port(environment: dict[str, str]) -> int | None:
    mode = environment.get("VECTOR_SEARCH_MODE", "").strip().casefold() or "local"
    if mode == "local":
        return None
    if mode != "remote":
        raise RuntimeError("VECTOR_SEARCH_MODE must be 'local' or 'remote'.")

    required = (
        "VECTOR_SERVICE_URL",
        "VECTOR_TOKEN_SECRET",
        "VECTOR_TOKEN_ISSUER",
        "VECTOR_TOKEN_AUDIENCE",
    )
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Remote vector search is missing required settings: "
            + ", ".join(missing)
            + "."
        )

    service_url = environment["VECTOR_SERVICE_URL"].strip()
    parsed_url = urlsplit(service_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise RuntimeError("VECTOR_SERVICE_URL must be an HTTP(S) base URL.")

    if parsed_url.hostname not in {"127.0.0.1", "localhost"}:
        return None
    if parsed_url.scheme != "http" or parsed_url.path not in {"", "/"}:
        raise RuntimeError(
            "The one-command launcher requires a local vector URL without HTTPS or a path prefix."
        )
    try:
        port = parsed_url.port or VECTOR_PORT
    except ValueError as exc:
        raise RuntimeError("VECTOR_SERVICE_URL contains an invalid port.") from exc
    if port in {API_PORT, STREAMLIT_PORT}:
        raise RuntimeError("VECTOR_SERVICE_URL conflicts with an application port.")

    environment["VECTOR_SERVICE_URL"] = f"http://{VECTOR_HOST}:{port}"
    return port


def _vector_environment(environment: dict[str, str]) -> dict[str, str]:
    child_environment = os.environ.copy()
    for name in (
        "APP_TOKEN_SECRET",
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
    ):
        child_environment.pop(name, None)
    for name in (
        "CHROMA_PERSIST_DIRECTORY",
        "VECTOR_TOKEN_SECRET",
        "VECTOR_TOKEN_ISSUER",
        "VECTOR_TOKEN_AUDIENCE",
    ):
        if name in environment:
            child_environment[name] = environment[name]
    child_environment["PYTHONPATH"] = str(PROJECT_ROOT)
    return child_environment


def _streamlit_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "APP_TOKEN_SECRET",
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "VECTOR_TOKEN_SECRET",
    ):
        environment.pop(name, None)
    environment["ASSISTANT_API_MODE"] = "real"
    environment["ASSISTANT_API_URL"] = f"http://{API_HOST}:{API_PORT}"
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(PROJECT_ROOT)
        if not existing_pythonpath
        else str(PROJECT_ROOT) + os.pathsep + existing_pythonpath
    )
    return environment


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> int:
    _require_modules()
    environment = _environment_from_dotenv()
    vector_port = _local_vector_port(environment)
    _require_free_port(API_HOST, API_PORT)
    _require_free_port(API_HOST, STREAMLIT_PORT)

    api_process = None
    vector_process = None
    streamlit_process = None
    try:
        if vector_port is not None:
            vector_url = f"http://{VECTOR_HOST}:{vector_port}/healthz"
            if _port_is_free(VECTOR_HOST, vector_port):
                vector_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "vector_service.app:app",
                        "--host",
                        VECTOR_HOST,
                        "--port",
                        str(vector_port),
                    ],
                    cwd=PROJECT_ROOT,
                    env=_vector_environment(environment),
                )
                print("Starting the local vector search service...")
            else:
                print("Using the vector search service already running locally.")
            _wait_for_service(vector_process, vector_url, "Vector search")

        api_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app_api.main:app",
                "--host",
                API_HOST,
                "--port",
                str(API_PORT),
            ],
            cwd=PROJECT_ROOT,
            env=os.environ.copy(),
        )
        _wait_for_service(
            api_process,
            f"http://{API_HOST}:{API_PORT}/healthz",
            "FastAPI",
        )

        streamlit_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.address",
                API_HOST,
                "--server.port",
                str(STREAMLIT_PORT),
            ],
            cwd=STREAMLIT_APP,
            env=_streamlit_environment(),
        )
        print(f"FinSolve Assistant: http://{API_HOST}:{STREAMLIT_PORT}")
        print("Press Ctrl+C to stop the API and Streamlit.")

        while True:
            streamlit_exit = streamlit_process.poll()
            if streamlit_exit is not None:
                return streamlit_exit
            api_exit = api_process.poll()
            if api_exit is not None:
                print(f"FastAPI stopped unexpectedly (exit code {api_exit}).")
                return api_exit or 1
            vector_exit = vector_process.poll() if vector_process is not None else None
            if vector_exit is not None:
                print(
                    "Vector search stopped unexpectedly "
                    f"(exit code {vector_exit})."
                )
                return vector_exit or 1
            time.sleep(0.25)
    finally:
        _stop_process(streamlit_process)
        _stop_process(api_process)
        _stop_process(vector_process)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except (RuntimeError, TimeoutError) as exc:
        print(f"Unable to start FinSolve Assistant: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
