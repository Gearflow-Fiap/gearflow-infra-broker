"""Libera um unico /32 para o CD e remove somente essa liberacao ao encerrar."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


class ClusterAccess:
    def __init__(self, cluster, region, lease, timeout=600):
        self.cluster, self.region = cluster, region
        self.path = Path(lease)
        self.timeout = timeout

    def aws(self, *arguments):
        result = subprocess.run(
            ["aws", "eks", *arguments, "--region", self.region, "--output", "json", "--no-cli-pager"],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "AWS CLI falhou")
        return json.loads(result.stdout)

    def cluster_info(self):
        return self.aws("describe-cluster", "--name", self.cluster)["cluster"]

    def save(self, lease):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(lease), encoding="utf-8")
        os.replace(temporary, self.path)

    def wait(self, update_id=None):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if update_id:
                update = self.aws("describe-update", "--name", self.cluster, "--update-id", update_id)["update"]
                if update["status"] == "Successful":
                    return
                if update["status"] in ("Failed", "Cancelled"):
                    raise RuntimeError(f"Atualizacao EKS terminou em {update['status']}")
            elif self.cluster_info()["status"] == "ACTIVE":
                return
            time.sleep(10)
        raise TimeoutError("Timeout aguardando atualizacao EKS; use o arquivo de acesso para concluir a limpeza")

    def update(self, cidrs):
        if not cidrs:
            raise RuntimeError("Recusa a enviar lista vazia de CIDRs")
        for cidr in cidrs:
            if ipaddress.ip_network(cidr).prefixlen == 0:
                raise RuntimeError("Recusa a configurar acesso irrestrito /0")
        result = self.aws("update-cluster-config", "--name", self.cluster,
                          "--resources-vpc-config", json.dumps({"publicAccessCidrs": cidrs}))
        return result["update"]["id"]

    def prepare(self, cidr):
        network = ipaddress.ip_network(cidr, strict=True)
        if network.version != 4 or network.prefixlen != 32:
            raise ValueError("O executor deve informar exatamente um IPv4 /32")
        if self.path.exists():
            raise RuntimeError("Arquivo de acesso ja existe; conclua a limpeza anterior antes de abrir outro")
        cluster = self.cluster_info()
        config = cluster["resourcesVpcConfig"]
        if cluster["status"] != "ACTIVE" or not config["endpointPublicAccess"] or not config["endpointPrivateAccess"]:
            raise RuntimeError("Requer cluster ACTIVE, com endpoints publico restrito e privado habilitados")
        cidrs = config["publicAccessCidrs"]
        if not cidrs or any(ipaddress.ip_network(item).prefixlen == 0 for item in cidrs):
            raise RuntimeError("Requer lista existente de CIDRs restritos, sem /0")
        # Se a origem ja estiver abrangida por outra regra, nao assume propriedade dela.
        owns = not any(network.subnet_of(ipaddress.ip_network(item)) for item in cidrs
                       if ipaddress.ip_network(item).version == 4)
        lease = {"version": 1, "cluster": self.cluster, "region": self.region,
                 "arn": cluster["arn"], "createdAt": cluster["createdAt"],
                 "cidr": str(network), "owns": owns, "closed": False}
        self.save(lease)  # Antes da mutacao: permite limpeza mesmo com falha parcial.
        print("Arquivo de recuperacao preparado; endpoint ainda nao alterado.")

    def open(self):
        lease = json.loads(self.path.read_text(encoding="utf-8"))
        cluster = self.cluster_info()
        if lease["cluster"] != self.cluster or lease["region"] != self.region or lease["closed"]:
            raise RuntimeError("Arquivo de acesso invalido para esta execucao")
        if cluster["arn"] != lease["arn"] or cluster["createdAt"] != lease["createdAt"]:
            raise RuntimeError("Cluster foi substituido depois da preparacao")
        config = cluster["resourcesVpcConfig"]
        if cluster["status"] != "ACTIVE" or not config["endpointPublicAccess"] or not config["endpointPrivateAccess"]:
            raise RuntimeError("Configuracao do endpoint mudou depois da preparacao")
        cidrs = config["publicAccessCidrs"]
        if lease["owns"] and lease["cidr"] not in cidrs:
            update_id = self.update([*cidrs, lease["cidr"]])
            lease["openUpdateId"] = update_id
            self.save(lease)
            self.wait(update_id)
        print("Acesso do executor pronto; nenhuma regra preexistente foi removida.")

    def close(self):
        if not self.path.exists():
            print("Nenhum arquivo de acesso criado; nada a limpar.")
            return
        lease = json.loads(self.path.read_text(encoding="utf-8"))
        if lease["cluster"] != self.cluster or lease["region"] != self.region:
            raise RuntimeError("Arquivo de acesso pertence a outro cluster/regiao")
        if lease["closed"]:
            return
        cluster = self.cluster_info()
        if cluster["arn"] != lease["arn"] or cluster["createdAt"] != lease["createdAt"]:
            raise RuntimeError("Cluster foi substituido; nao alterar o novo cluster automaticamente")
        self.wait()  # Tambem cobre uma chamada open aceita cuja resposta se perdeu.
        cluster = self.cluster_info()
        cidrs = cluster["resourcesVpcConfig"]["publicAccessCidrs"]
        if lease["owns"] and lease["cidr"] in cidrs:
            update_id = self.update([item for item in cidrs if item != lease["cidr"]])
            self.wait(update_id)
            if lease["cidr"] in self.cluster_info()["resourcesVpcConfig"]["publicAccessCidrs"]:
                raise RuntimeError("CIDR temporario ainda presente; revisar limpeza")
        lease["closed"] = True
        self.save(lease)
        print("Acesso temporario encerrado; regras dos demais operadores preservadas.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "open", "close"))
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--lease", required=True)
    args = parser.parse_args()
    access = ClusterAccess(args.cluster, args.region, args.lease)
    if args.mode == "prepare":
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=15) as response:
            address = str(ipaddress.IPv4Address(response.read().decode().strip()))
        access.prepare(address + "/32")
    elif args.mode == "open":
        access.open()
    else:
        access.close()


if __name__ == "__main__":
    main()
