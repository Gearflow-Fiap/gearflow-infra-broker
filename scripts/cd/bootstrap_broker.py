"""Secrets Kubernetes sao a fonte de verdade; nenhum password e rotacionado no bootstrap."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def password_matches(user, password):
    algorithms = {"rabbit_password_hashing_sha256": hashlib.sha256, "rabbit_password_hashing_sha512": hashlib.sha512}
    algorithm = algorithms.get(user.get("hashing_algorithm", "rabbit_password_hashing_sha256"))
    if not algorithm:
        raise RuntimeError("Algoritmo de senha nao suportado para migracao; revisar usuario")
    value = base64.b64decode(user["password_hash"], validate=True)
    return len(value) > 4 and hmac.compare_digest(value[4:], algorithm(value[:4] + password.encode()).digest())


def ensure_bootstrap(namespace, run, manifest_dir="k8s"):
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    backup = local / "credentials-backups" / secrets.token_hex(8)

    def kube(*args, **kwargs):
        return run("kubectl", "-n", namespace, *args, **kwargs)

    def get_secret(name):
        text = kube("get", "secret", name, "--ignore-not-found", "-o", "json", private=True)
        if not text.strip():
            return None
        value = json.loads(text)
        return {key: base64.b64decode(item).decode() for key, item in value["data"].items()}

    def create_secret(name, data):
        value = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": namespace},
                 "type": "Opaque", "stringData": data}
        kube("create", "-f", "-", input=json.dumps(value), private=True)

    def cache(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") != value:
            backup.mkdir(parents=True, exist_ok=True)
            saved = backup / path.name
            saved.write_bytes(path.read_bytes())
            saved.chmod(0o600)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)

    run("kubectl", "apply", "-f", "k8s/namespace.yaml", capture=False)
    admin_path = local / "cluster-admin.json"
    admin = get_secret("rabbitmq-credentials")
    if admin is None:
        old_claim = kube("get", "pvc", "data-rabbitmq-0", "--ignore-not-found", "-o", "name")
        if old_claim.strip() and not admin_path.exists():
            raise RuntimeError("PVC existente sem Secret/admin local: recuperar credenciais antes de prosseguir")
        admin = json.loads(admin_path.read_text()) if admin_path.exists() else {"username": "gearflow_admin", "password": secrets.token_hex(32)}
        if not admin.get("username") or not admin.get("password"):
            raise RuntimeError("Credencial administrativa incompleta")
        cache(admin_path, json.dumps(admin))
        create_secret("rabbitmq-credentials", admin)
    if not admin.get("username") or not admin.get("password"):
        raise RuntimeError("Secret administrativo incompleto; nao sobrescrever automaticamente")
    run("kubectl", "apply", "--dry-run=server", "-k", manifest_dir, capture=False)
    run("kubectl", "apply", "-k", manifest_dir, capture=False)
    kube("rollout", "status", "statefulset/rabbitmq", "--timeout=360s", capture=False)

    # O tunel e local e encerrado pelo proprio processo; senhas nunca sao argumentos CLI.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(["kubectl", "-n", namespace, "port-forward", "pod/rabbitmq-0", f"{port}:15672", "--address", "127.0.0.1"],
                               cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    authorization = base64.b64encode(f"{admin['username']}:{admin['password']}".encode()).decode()

    def api(method, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/{path}", method=method,
                                         headers={"Authorization": f"Basic {authorization}", "Content-Type": "application/json"},
                                         data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read()
            return json.loads(data) if data else None

    try:
        for attempt in range(30):
            try:
                api("GET", "overview")
                break
            except urllib.error.HTTPError:
                raise RuntimeError("Secret administrativo nao autentica no volume existente; revisar sem rotacionar") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == 29:
                    raise RuntimeError("API administrativa indisponivel") from None
                time.sleep(0.5)
        policy = json.loads((ROOT / "rabbitmq/service-permissions.json").read_text())
        legacy_path = local / "service-credentials.json"
        legacy = json.loads(legacy_path.read_text()) if legacy_path.exists() else {}
        prepared = []
        for item in policy["users"]:
            name = item["name"]
            remote = None
            try:
                remote = api("GET", "users/" + urllib.parse.quote(name, safe=""))
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            secret_name = "rabbitmq-" + name.removeprefix("gearflow_") + "-credentials"
            credential = get_secret(secret_name)
            if credential is None:
                if remote and name not in legacy:
                    raise RuntimeError(f"Usuario {name} existente sem Secret/senha local; recuperar antes de migrar")
                credential = {"username": name, "password": legacy.get(name) or secrets.token_hex(32)}
            if credential.get("username") != name or not credential.get("password"):
                raise RuntimeError(f"Secret de {name} invalido; nenhuma senha sera sobrescrita")
            if remote and (remote.get("tags") or not password_matches(remote, credential["password"])):
                raise RuntimeError(f"Usuario {name} possui tags/senha divergentes; revisar sem rotacionar")
            prepared.append((item, secret_name, credential, remote is None))
        for item, secret_name, credential, missing in prepared:
            if get_secret(secret_name) is None:
                create_secret(secret_name, credential)
            name = item["name"]
            if missing:
                api("PUT", "users/" + name, {"password": credential["password"], "tags": []})
            api("PUT", "permissions/gearflow/" + name, {key: item[key] for key in ("configure", "write", "read")})
            legacy[name] = credential["password"]
        cache(admin_path, json.dumps(admin))
        cache(legacy_path, json.dumps(legacy))
        # Limpa apenas os recursos de certificados da versao anterior, depois do rollout e autenticacao.
        kube("delete", "secret", "rabbitmq-tls", "--ignore-not-found")
        kube("delete", "configmap", "rabbitmq-ca", "--ignore-not-found")
        print("Bootstrap pronto: AMQP interno, administrador e quatro usuarios/Secrets verificados, sem rotacao.")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
