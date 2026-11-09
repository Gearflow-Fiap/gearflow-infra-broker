"""Limpeza limitada ao PVC RabbitMQ; journal privado permite retomar falhas parciais."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

NAMESPACE, CLAIM = "messaging", "data-rabbitmq-0"


def identify(items):
    claims = [item for item in items if item["kind"] == "PersistentVolumeClaim"
              and item["metadata"].get("namespace") == NAMESPACE and item["metadata"]["name"] == CLAIM]
    volumes = [item for item in items if item["kind"] == "PersistentVolume"
               and item["spec"].get("claimRef", {}).get("namespace") == NAMESPACE
               and item["spec"]["claimRef"].get("name") == CLAIM]
    if len(claims) > 1 or len(volumes) > 1:
        raise RuntimeError("Multiplos volumes RabbitMQ; revisar os dados antes da limpeza")
    claim, volume = (claims or [None])[0], (volumes or [None])[0]
    allowed = {id(item) for item in claims + volumes}
    for item in items:
        if ((item["kind"] in ("PersistentVolumeClaim", "PersistentVolume", "Ingress") and id(item) not in allowed)
                or (item["kind"] == "Service" and item["spec"].get("type") == "LoadBalancer")):
            raise RuntimeError(f"Recurso fora do RabbitMQ: {item['kind']} {item['metadata'].get('namespace', '')}/{item['metadata']['name']}. Nada removido")
    if claim and (not volume or claim["spec"].get("volumeName") != volume["metadata"]["name"]
                  or claim["metadata"]["uid"] != volume["spec"]["claimRef"].get("uid")):
        raise RuntimeError("PVC/PV ainda nao vinculados ou divergentes; aguarde/revise antes de descartar")
    if volume:
        spec = volume["spec"]
        if (spec.get("storageClassName") != "gearflow-broker-gp3" or spec.get("csi", {}).get("driver") != "ebs.csi.aws.com"
                or not re.fullmatch(r"vol-[0-9a-f]+", spec["csi"].get("volumeHandle", ""))):
            raise RuntimeError("PV nao corresponde ao EBS/StorageClass do RabbitMQ")
    return claim, volume


def validate_volume(volume, record):
    tags = {item["Key"]: item["Value"] for item in volume.get("Tags", [])}
    if (volume["VolumeId"] != record["volume_id"] or tags.get("CSIVolumeName") != record["pv_name"]
            or tags.get("ebs.csi.aws.com/cluster") != "true"):
        raise RuntimeError("EBS sem comprovacao de origem no PV RabbitMQ; nenhuma exclusao autorizada")
    for key, expected in (("kubernetes.io/created-for/pvc/namespace", NAMESPACE), ("kubernetes.io/created-for/pvc/name", CLAIM)):
        if key in tags and tags[key] != expected:
            raise RuntimeError("Tags do EBS apontam para outro PVC")


class Removal:
    def __init__(self, cluster, region, journal, execute=False):
        self.cluster, self.region = cluster, region
        self.path, self.execute = Path(journal), execute

    def command(self, *args, absent=None):
        result = subprocess.run(args, text=True, capture_output=True, check=False)
        if result.returncode:
            if absent and absent in result.stderr:
                return None
            raise RuntimeError(f"Falha em {args[0]} {args[1]}; interrompido sem prosseguir para Terraform")
        return result.stdout

    def aws(self, service, *args, absent=None):
        text = self.command("aws", service, *args, "--region", self.region, "--output", "json", "--no-cli-pager",
                            "--cli-connect-timeout", "10", "--cli-read-timeout", "30", absent=absent)
        return json.loads(text) if text else None

    def kube(self, *args):
        return self.command("kubectl", *args, "--request-timeout=" + ("300s" if "delete" in args else "30s"))

    def save(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, self.path)

    def volume(self, record):
        if not record.get("volume_id"):
            return None
        result = self.aws("ec2", "describe-volumes", "--volume-ids", record["volume_id"], absent="InvalidVolume.NotFound")
        volume = result["Volumes"][0] if result else None
        if volume:
            validate_volume(volume, record)
        return volume

    def prepare(self):
        account = self.aws("sts", "get-caller-identity")["Account"]
        old = json.loads(self.path.read_text()) if self.path.exists() else None
        names = self.aws("eks", "list-clusters")["clusters"]
        cluster = self.aws("eks", "describe-cluster", "--name", self.cluster)["cluster"] if self.cluster in names else None
        if old and (old["account"] != account or old["cluster"] != self.cluster or old["region"] != self.region):
            raise RuntimeError("Journal pertence a outra conta/cluster/regiao")
        if not cluster:
            if old and not old["completed"]:
                self.volume(old)
                return old, False
            return None, False
        if cluster["status"] != "ACTIVE":
            raise RuntimeError("Cluster precisa estar ACTIVE, sem outro deploy/atualizacao")
        if old and not old["completed"] and old["createdAt"] != cluster["createdAt"]:
            raise RuntimeError("Cluster foi recriado com limpeza anterior pendente; revisar journal")
        endpoint = self.kube("config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}").strip()
        if endpoint != cluster["endpoint"]:
            raise RuntimeError("Kubeconfig aponta para outro cluster")
        items = json.loads(self.kube("get", "pvc,pv,services,ingresses", "--all-namespaces", "-o", "json"))["items"]
        claim, volume = identify(items)
        record = {"account": account, "cluster": self.cluster, "region": self.region, "createdAt": cluster["createdAt"],
                  "pv_name": volume["metadata"]["name"] if volume else None,
                  "pv_uid": volume["metadata"]["uid"] if volume else None,
                  "pvc_uid": volume["spec"]["claimRef"]["uid"] if volume else None,
                  "volume_id": volume["spec"]["csi"]["volumeHandle"] if volume else None, "completed": False}
        if old and not old["completed"]:
            if volume and record["volume_id"] != old["volume_id"]:
                raise RuntimeError("Volume mudou durante a limpeza; revisar sem sobrescrever o journal")
            record = old
        self.volume(record)
        sts_text = self.kube("-n", NAMESPACE, "get", "statefulset", "rabbitmq", "--ignore-not-found", "-o", "json")
        if sts_text.strip():
            sts = json.loads(sts_text)
            if sts["spec"]["selector"]["matchLabels"].get("app") != "rabbitmq":
                raise RuntimeError("StatefulSet nao identificado como RabbitMQ")
        pods = json.loads(self.kube("-n", NAMESPACE, "get", "pods", "--ignore-not-found", "-o", "json"))["items"]
        for pod in pods:
            if any(item.get("persistentVolumeClaim", {}).get("claimName") == CLAIM for item in pod["spec"].get("volumes", [])):
                owners = pod["metadata"].get("ownerReferences", [])
                if not any(owner["kind"] == "StatefulSet" and owner["name"] == "rabbitmq" for owner in owners):
                    raise RuntimeError("Outro workload esta usando o PVC RabbitMQ")
        if not volume and not claim and not sts_text.strip() and not (old and not old["completed"]):
            return None, True
        return record, True

    def remove(self):
        record, live = self.prepare()
        if record is None:
            print("Sem volume RabbitMQ conhecido para limpar.")
            return
        print(f"RabbitMQ: descartar PVC {NAMESPACE}/{CLAIM}, PV {record['pv_name']} e EBS {record['volume_id']}.", flush=True)
        if not self.execute:
            print("PREVIA: nenhum dado removido.")
            return
        self.save(record)  # Antes de excluir PVC: identidade do EBS permanece em uma retomada.
        if live:
            print("Parando RabbitMQ e aguardando remocao do Pod...", flush=True)
            self.kube("-n", NAMESPACE, "delete", "statefulset", "rabbitmq", "--ignore-not-found", "--cascade=foreground", "--wait=true", "--timeout=240s")
            self.kube("-n", NAMESPACE, "delete", "pvc", CLAIM, "--ignore-not-found", "--wait=true", "--timeout=240s")
        print("Aguardando EBS desanexar para excluir somente o volume confirmado...", flush=True)
        deadline = time.monotonic() + 600
        next_progress = time.monotonic() + 15
        while True:
            volume = self.volume(record)
            if volume is None:
                break
            if volume["State"] == "available" and not volume.get("Attachments"):
                self.aws("ec2", "delete-volume", "--volume-id", record["volume_id"])
                print("Exclusao do EBS solicitada; confirmando...", flush=True)
            if time.monotonic() > deadline:
                raise TimeoutError("EBS ainda anexado/excluindo; journal preservado para repetir o mesmo comando")
            if time.monotonic() >= next_progress:
                print(f"EBS ainda em {volume['State']}; aguardando...", flush=True)
                next_progress = time.monotonic() + 15
            time.sleep(5)
        if live and record["pv_name"]:
            text = self.kube("get", "pv", record["pv_name"], "--ignore-not-found", "-o", "json")
            if text.strip() and json.loads(text)["metadata"]["uid"] != record["pv_uid"]:
                raise RuntimeError("PV substituido; nao excluir o objeto novo")
            self.kube("delete", "pv", record["pv_name"], "--ignore-not-found", "--wait=true", "--timeout=120s")
        record["completed"] = True
        self.save(record)
        print("Dados RabbitMQ removidos e exclusao do EBS confirmada. Prosseguindo com Terraform.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-cluster", default="")
    args = parser.parse_args()
    if args.execute and args.confirm_cluster != args.cluster:
        raise ValueError("Exclusao requer confirmacao do nome exato do cluster")
    Removal(args.cluster, args.region, args.journal, args.execute).remove()


if __name__ == "__main__":
    main()
