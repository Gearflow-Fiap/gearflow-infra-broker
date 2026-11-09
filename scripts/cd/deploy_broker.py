"""Prepara ou atualiza o broker, preservando credenciais existentes; sem Terraform."""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def run(*arguments, capture=True, input=None, private=False):
    result = subprocess.run(arguments, cwd=ROOT, text=True, capture_output=capture, input=input, check=False)
    if result.returncode:
        if capture and result.stderr and not private:
            print(result.stderr.strip())
        raise RuntimeError(f"Comando falhou: {arguments[0]} {arguments[1]}")
    return result.stdout if capture else None


def table(value):
    if isinstance(value, dict):
        return value
    return {key: table(item) if kind == "table" else item for key, kind, item in (value or [])}


def topology_matches(expected, actual):
    """Compara recursos declarados; recursos extras sao preservados."""
    for kind in ("exchanges", "queues"):
        for resource in expected.get(kind, []):
            found = next((item for item in actual[kind] if item["name"] == resource["name"]), None)
            if not found or any(found[key] != resource[key] for key in ("durable", "auto_delete")):
                return False
            if kind == "exchanges" and found["type"] != resource["type"]:
                return False
            arguments = table(found["arguments"])
            if any(arguments.get(key) != value for key, value in resource.get("arguments", {}).items()):
                return False
    for binding in expected.get("bindings", []):
        if not any(item["source_name"] == binding["source"]
                   and item["destination_name"] == binding["destination"]
                   and item["destination_kind"] == binding["destination_type"]
                   and item["routing_key"] == binding["routing_key"]
                   and table(item["arguments"]) == binding.get("arguments", {})
                   for item in actual["bindings"]):
            return False
    for policy in expected.get("policies", []):
        found = next((item for item in actual["policies"] if item["name"] == policy["name"]), None)
        if not found or any(found[key] != policy[key] for key in ("pattern", "apply-to", "priority")):
            return False
        definition = found["definition"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        if definition != policy["definition"]:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--namespace", choices=("messaging",), required=True)
    args = parser.parse_args()
    definitions = json.loads((ROOT / "rabbitmq/definitions.json").read_text(encoding="utf-8-sig"))
    if set(definitions) - {"vhosts", "exchanges", "queues", "bindings", "policies"}:
        raise ValueError("definitions.json deve conter somente topologia, sem usuarios/permissoes")
    if definitions["vhosts"] != [{"name": "gearflow"}]:
        raise ValueError("Este deploy administra somente o vhost gearflow")
    if any(item["vhost"] != "gearflow" for kind in ("exchanges", "queues", "bindings", "policies")
           for item in definitions.get(kind, [])):
        raise ValueError("Recurso declarado em outro vhost")
    permissions = json.loads((ROOT / "rabbitmq/service-permissions.json").read_text(encoding="utf-8-sig"))
    expected_users = {"gearflow_os", "gearflow_budget", "gearflow_operation", "gearflow_inventory"}
    if permissions["vhost"] != "gearflow" or {item["name"] for item in permissions["users"]} != expected_users:
        raise ValueError("Revise bootstrap/credenciais antes de alterar os usuarios administrados")
    cluster = json.loads(run("aws", "eks", "describe-cluster", "--name", args.cluster,
                             "--region", args.region, "--output", "json"))["cluster"]
    server = run("kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
    if cluster["status"] != "ACTIVE" or server.strip() != cluster["endpoint"]:
        raise RuntimeError("Contexto Kubernetes diferente do cluster ACTIVE selecionado")

    def kube(*command, capture=True):
        return run("kubectl", "-n", args.namespace, *command, capture=capture)

    def rabbit(*command):
        return kube("exec", "rabbitmq-0", "--", "rabbitmqctl", "-q", *command)

    def rabbit_json(*command):
        return json.loads(rabbit(*command, "--formatter", "json"))

    from bootstrap_broker import ensure_bootstrap
    ensure_bootstrap(args.namespace, run)
    existing = {item["user"]: item for item in rabbit_json("list_users")}
    if any(name not in existing or existing[name]["tags"] for name in expected_users):
        raise RuntimeError("Bootstrap pendente: requer quatro usuarios existentes, sem tags administrativas")
    kube("cp", "rabbitmq/definitions.json", "rabbitmq-0:/tmp/gearflow-definitions.json")
    print(rabbit("import_definitions", "/tmp/gearflow-definitions.json").strip())
    for _ in range(12):
        actual = {
            "queues": rabbit_json("list_queues", "-p", "gearflow", "name", "durable", "auto_delete", "arguments"),
            "exchanges": rabbit_json("list_exchanges", "-p", "gearflow", "name", "type", "durable", "auto_delete", "arguments"),
            "bindings": rabbit_json("list_bindings", "-p", "gearflow", "source_name", "destination_name", "destination_kind", "routing_key", "arguments"),
            "policies": rabbit_json("list_policies", "-p", "gearflow"),
        }
        if topology_matches(definitions, actual):
            break
        time.sleep(5)
    else:
        raise RuntimeError("Topologia nao corresponde ao arquivo; revisar import/migracao, sem excluir filas automaticamente")
    for user in permissions["users"]:
        rabbit("set_permissions", "-p", "gearflow", user["name"], user["configure"], user["write"], user["read"])
    actual = rabbit_json("list_permissions", "-p", "gearflow")
    for user in permissions["users"]:
        if not any(item["user"] == user["name"] and all(item[key] == user[key] for key in ("configure", "write", "read")) for item in actual):
            raise RuntimeError(f"Permissoes divergentes para {user['name']}")
    kube("exec", "rabbitmq-0", "--", "rabbitmq-diagnostics", "-q", "check_running")
    kube("exec", "rabbitmq-0", "--", "rabbitmq-diagnostics", "-q", "check_local_alarms")
    print("Deploy validado: workload, topologia e permissoes. Credenciais e mensagens preservadas.")


if __name__ == "__main__":
    main()
