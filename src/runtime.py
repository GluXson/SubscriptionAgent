import os
import socket
import subprocess
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
OLLAMA_DIR = RUNTIME / "ollama"
MODELS_DIR = RUNTIME / "models"
EXE = OLLAMA_DIR / "ollama.exe"

MODEL = "llama3.2:3b"

DOWNLOAD_URL = (
    "https://github.com/ollama/ollama/releases/latest/download/"
    "ollama-windows-amd64.zip"
)


def download_ollama():
    if EXE.is_file():
        return

    RUNTIME.mkdir(parents=True, exist_ok=True)
    OLLAMA_DIR.mkdir(parents=True, exist_ok=True)

    archive = RUNTIME / "ollama.zip"

    print("Pobieranie Ollama do katalogu projektu...", flush=True)

    try:
        with requests.get(
            DOWNLOAD_URL,
            stream=True,
            timeout=(30, 300),
        ) as response:
            response.raise_for_status()

            with archive.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)

        print("Rozpakowywanie Ollama...", flush=True)

        with zipfile.ZipFile(archive) as package:
            destination = OLLAMA_DIR.resolve()

            for member in package.infolist():
                target = (destination / member.filename).resolve()

                if not target.is_relative_to(destination):
                    raise RuntimeError("Archiwum zawiera niedozwolona sciezke.")

            package.extractall(destination)

        if not EXE.is_file():
            raise RuntimeError("Po rozpakowaniu nie znaleziono ollama.exe.")

    finally:
        archive.unlink(missing_ok=True)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(base_url, process, timeout=120):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Serwer Ollama zakonczyl dzialanie. Sprawdz runtime/server.log."
            )

        try:
            response = requests.get(
                f"{base_url}/api/tags",
                timeout=2,
            )
            response.raise_for_status()
            return
        except requests.RequestException:
            time.sleep(0.5)

    raise TimeoutError("Serwer Ollama nie uruchomil sie w limicie czasu.")


def ensure_model(base_url):
    response = requests.get(f"{base_url}/api/tags", timeout=10)
    response.raise_for_status()

    names = {
        item["name"]
        for item in response.json().get("models", [])
    }

    if MODEL in names:
        return

    print(f"Pobieranie modelu {MODEL} do runtime/models...", flush=True)

    response = requests.post(
        f"{base_url}/api/pull",
        json={"model": MODEL, "stream": False},
        timeout=(30, 7200),
    )
    response.raise_for_status()

    result = response.json()

    if result.get("error"):
        raise RuntimeError(result["error"])

    if result.get("status") != "success":
        raise RuntimeError(f"Nie zakonczono pobierania modelu: {result}")


@contextmanager
def local_runtime():
    download_ollama()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = f"127.0.0.1:{port}"
    environment["OLLAMA_MODELS"] = str(MODELS_DIR)
    environment["OLLAMA_NO_CLOUD"] = "1"

    process = None

    with (RUNTIME / "server.log").open("ab") as log:
        try:
            print("Uruchamianie lokalnego serwera Ollama...", flush=True)

            process = subprocess.Popen(
                [str(EXE), "serve"],
                cwd=RUNTIME,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            wait_for_server(base_url, process)
            ensure_model(base_url)

            yield base_url, MODEL

        finally:
            if process is not None and process.poll() is None:
                print("Zatrzymywanie lokalnego serwera...", flush=True)
                process.terminate()

                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()