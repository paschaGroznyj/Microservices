"""fetch_monaco.py — monaco-editor для /editor качается из npm при сборке образа, в git не хранится.

Dockerfile (стадия monaco): python fetch_monaco.py /opt/vendor/monaco
Проверяет sha512 из npm (поле dist.integrity), кладёт min/vs и LICENSE, из nls оставляет только ru.
Файлы совпадают с прежним vendor/monaco (коммит 6427fca) один в один.
Переменные: MONACO_VERSION, MONACO_SHA512, NPM_REGISTRY (зеркало), MONACO_TGZ (локальный архив вместо сети).
"""
import base64
import hashlib
import io
import os
import re
import sys
import tarfile
import urllib.request

VERSION = os.environ.get("MONACO_VERSION", "0.52.2")
INTEGRITY = os.environ.get(
    "MONACO_SHA512",
    "GEQWEZmfkOGLdd3XK8ryrfWz3AIP8YymVXiPHEdewrUq7mh0qrKrfHLNCXcbB6sTnMLnOZ3ztSiKcciFUkIJwQ==")
REGISTRY = os.environ.get("NPM_REGISTRY", "https://registry.npmjs.org").rstrip("/")
NLS_KEEP = {"ru"}


def _target(name: str):
    if name == "package/LICENSE":
        return "LICENSE"
    if not name.startswith("package/min/vs/"):
        return None
    rel = name[len("package/min/"):]
    if ".." in rel.split("/"):
        return None
    m = re.fullmatch(r"vs/nls\.messages\.([\w-]+)\.js", rel)
    if m and m.group(1) not in NLS_KEEP:
        return None
    return rel


def main(dest: str) -> int:
    local = os.environ.get("MONACO_TGZ")
    if local:
        data = open(local, "rb").read()
    else:
        url = f"{REGISTRY}/monaco-editor/-/monaco-editor-{VERSION}.tgz"
        data = urllib.request.urlopen(url, timeout=120).read()
    got = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if INTEGRITY and got != INTEGRITY:
        sys.exit(f"monaco-editor {VERSION}: sha512 не совпал (получено {got[:16]}…)")
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            rel = _target(m.name) if m.isfile() else None
            if not rel:
                continue
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(tf.extractfile(m).read())
            n += 1
    with open(os.path.join(dest, "VERSION"), "w", encoding="utf-8") as f:
        f.write(f"monaco-editor {VERSION} (min/vs), npm pack; nls оставлен только ru\n")
    print(f"monaco-editor {VERSION}: {n} файлов -> {dest}")
    return n


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/opt/vendor/monaco")
