"""Plan publico resumido; apply manual vinculado ao conteudo do plano e commit."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "infra/cluster"
ALLOWED_INPUTS = {"aws_region", "cluster_name", "kubernetes_version", "cluster_role_arn", "node_role_arn",
                  "admin_cidrs", "availability_zones", "node_instance_type", "enable_ebs_csi",
                  "ebs_csi_role_arn", "ebs_csi_version"}


def validate_config(config):
    if set(config) - ALLOWED_INPUTS:
        raise ValueError("TF_CLUSTER_CONFIG_JSON contem campos desconhecidos; nao incluir credenciais AWS")
    required = {"aws_region", "cluster_name", "kubernetes_version", "cluster_role_arn", "node_role_arn", "admin_cidrs", "availability_zones"}
    if not required <= set(config):
        raise ValueError("TF_CLUSTER_CONFIG_JSON incompleto")
    if config["cluster_name"] != "gearflow-fase4" or config["aws_region"] != "us-east-1":
        raise ValueError("Configuracao diferente do ambiente Academy aprovado")
    if not isinstance(config["admin_cidrs"], list) or not config["admin_cidrs"]:
        raise ValueError("admin_cidrs deve conter uma lista IPv4 restrita")
    for cidr in config["admin_cidrs"]:
        network = ipaddress.ip_network(cidr, strict=True)
        if network.version != 4 or network.prefixlen == 0:
            raise ValueError("admin_cidrs nao pode liberar /0 ou IPv6")


def reviewed_changes(plan):
    return [item for item in plan.get("resource_changes", [])
            if item["mode"] == "managed" and item["change"]["actions"] != ["no-op"]]


def fingerprint(plan, commit):
    review = {"commit": commit, "variables": plan.get("variables", {}), "resources": reviewed_changes(plan),
              "outputs": {key: value for key, value in plan.get("output_changes", {}).items()
                          if value["actions"] != ["no-op"]}}
    return hashlib.sha256(json.dumps(review, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approve(plan, commit, expected, confirmation, allow_destructive):
    if confirmation != "gearflow-fase4" or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Apply requer nome exato do cluster e SHA256 do plano revisado")
    if fingerprint(plan, commit) != expected:
        raise ValueError("Plano/inputs/commit mudaram. Execute plan novamente e revise o novo identificador")
    if not allow_destructive and any("delete" in item["change"]["actions"] for item in reviewed_changes(plan)):
        raise ValueError("Plano inclui exclusao/substituicao. Requer permissao explicita allow_destructive")


def sanitized(text):
    text = re.sub(r"arn:aws[^\s\"']+", "[ARN omitido]", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b", "[IP omitido]", text)
    return text


def command(*arguments, accepted=(0,)):
    result = subprocess.run(arguments, cwd=DIRECTORY, text=True, capture_output=True, check=False)
    if result.returncode not in accepted:
        # Nunca publicar o plano completo, state ou inputs no log de repositorio publico.
        message = "\n".join((result.stderr or "Comando falhou; reproduza localmente para diagnostico").splitlines()[-12:])
        raise RuntimeError(sanitized(message))
    return result


def apply_saved(saved):
    process = subprocess.Popen(["terraform", "apply", "-input=false", "-lock-timeout=120s", "-no-color", str(saved)],
                               cwd=DIRECTORY, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in process.stdout:
        print(sanitized(line.rstrip()), flush=True)
    if process.wait():
        raise RuntimeError("Apply falhou; state HCP preservado para diagnostico")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "apply"), default="plan")
    parser.add_argument("--expected-plan-sha", default="")
    parser.add_argument("--confirm-cluster", default="")
    parser.add_argument("--allow-destructive", action="store_true")
    args = parser.parse_args()
    config = json.loads(os.environ["TF_CLUSTER_CONFIG_JSON"])
    validate_config(config)
    token = os.environ["TF_TOKEN_app_terraform_io"]
    request = urllib.request.Request(
        "https://app.terraform.io/api/v2/organizations/gearflowfiapmurilo/workspaces/gearflow-fase4-cluster",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.api+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        workspace = json.load(response)["data"]
    if workspace["attributes"]["execution-mode"] != "local":
        raise RuntimeError("Workspace HCP deve estar em modo local para executar no GitHub")
    identity = json.loads(command("aws", "sts", "get-caller-identity", "--output", "json").stdout)
    if identity["Account"] != "531827661311" or any((config[key].split(":")[4] != identity["Account"])
                                                   for key in ("cluster_role_arn", "node_role_arn")):
        raise RuntimeError("Conta AWS/roles diferentes do ambiente aprovado")
    command("terraform", "fmt", "-check", "-recursive")
    command("terraform", "init", "-input=false", "-lockfile=readonly")
    command("terraform", "validate")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    commit = os.environ.get("TF_REVIEWED_COMMIT") or head
    if commit != head:
        raise RuntimeError("Checkout diferente do commit aprovado pelo CI")
    temporary_root = Path(os.environ.get("RUNNER_TEMP", str(ROOT / ".local"))).resolve()
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gearflow-infra-", dir=temporary_root) as directory:
        variables = Path(directory) / "inputs.tfvars.json"
        variables.write_text(json.dumps(config), encoding="utf-8")
        variables.chmod(0o600)
        saved = Path(directory) / "cluster.tfplan"
        result = command("terraform", "plan", f"-var-file={variables}", f"-out={saved}", "-input=false",
                         "-lock-timeout=120s", "-no-color", "-detailed-exitcode", accepted=(0, 2))
        plan = json.loads(command("terraform", "show", "-json", str(saved)).stdout)
        if plan.get("errored") or plan.get("complete") is False:
            raise RuntimeError("Plano incompleto; nao aplicar automaticamente")
        digest = fingerprint(plan, commit)
        changes = result.returncode == 2
        summary = ["## Plano do EKS", f"Commit: `{commit}`", f"Identificador revisavel: `{digest}`", "",
                   "| Recurso | Acao | Atributos alterados (sem valores) |", "| --- | --- | --- |"]
        for item in reviewed_changes(plan):
            before, after = item["change"].get("before") or {}, item["change"].get("after") or {}
            keys = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
            summary.append(f"| `{item['address']}` | {', '.join(item['change']['actions'])} | {', '.join(keys)} |")
        output_names = [name for name, change in plan.get("output_changes", {}).items() if change["actions"] != ["no-op"]]
        if output_names:
            summary.append("Outputs alterados (sem valores): " + ", ".join(output_names))
        if not changes:
            summary.append("Sem mudancas de infraestrutura.")
        elif args.mode == "plan":
            summary.append("Infraestrutura pendente: execute GearFlow CD manualmente com operation=apply, este identificador e confirmacao gearflow-fase4.")
        print("\n".join(summary), flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write("\n".join(summary) + "\n")
        applied = False
        if args.mode == "apply":
            approve(plan, commit, args.expected_plan_sha, args.confirm_cluster, args.allow_destructive)
            if changes:
                print("Aplicando o plano conferido; aguarde a operacao AWS.", flush=True)
                apply_saved(saved)
            applied = True
            print("Apply concluido; state no HCP atualizado.", flush=True)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                stream.write(f"changes={str(changes).lower()}\napplied={str(applied).lower()}\nplan_sha={digest}\n")
    print("Arquivos locais de inputs e plano removidos; nenhum plan/state foi publicado como artifact.")


if __name__ == "__main__":
    main()
