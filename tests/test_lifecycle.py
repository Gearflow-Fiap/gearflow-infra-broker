import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/cd"))
import bootstrap_broker
from remove_broker import Removal, identify


def user(password):
    salt = b"test"
    return {"tags": [], "hashing_algorithm": "rabbit_password_hashing_sha256",
            "password_hash": base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()}


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return json.dumps(self.value).encode() if self.value is not None else b""


class FakeBootstrap:
    def __init__(self, root):
        self.root, self.secrets, self.users, self.created_users = root, {}, {}, []
        self.configmaps = {}
        self.credentials = None
        self.private_arguments = []
        self.retained_claim = False

    def run(self, *args, **options):
        self.private_arguments.append(args)
        if args[0] == "pwsh":
            raise AssertionError("Bootstrap AMQP nao deve gerar ou validar certificados")
        if "delete" in args:
            kind, name = args[args.index("delete") + 1:args.index("delete") + 3]
            if kind == "secret": self.secrets.pop(name, None)
            if kind == "configmap": self.configmaps.pop(name, None)
        if "get" in args and "pvc" in args:
            return "persistentvolumeclaim/data-rabbitmq-0" if self.retained_claim else ""
        if "get" in args and "secret" in args:
            name = args[args.index("secret") + 1]
            return json.dumps({"data": {key: base64.b64encode(value.encode()).decode() for key, value in self.secrets[name].items()}}) if name in self.secrets else ""
        if "create" in args:
            secret = json.loads(options["input"])
            self.secrets[secret["metadata"]["name"]] = secret["stringData"]
        if "apply" in args and options.get("input"):
            resource = json.loads(options["input"])
            if resource["kind"] == "ConfigMap": self.configmaps[resource["metadata"]["name"]] = resource["data"]
        if "apply" in args and "-k" in args and self.credentials is None:
            self.credentials = self.secrets["rabbitmq-credentials"].copy()
        return ""

    def api(self, request, timeout):
        expected = "Basic " + base64.b64encode(f"{self.credentials['username']}:{self.credentials['password']}".encode()).decode()
        if request.headers["Authorization"] != expected:
            raise HTTPError(request.full_url, 401, "denied", {}, None)
        path = request.full_url.split("/api/", 1)[1]
        if path == "overview": return Response({})
        name = path.split("/")[-1]
        if path.startswith("users/"):
            if request.method == "GET":
                if name not in self.users: raise HTTPError(request.full_url, 404, "missing", {}, None)
                return Response(self.users[name])
            self.users[name] = user(json.loads(request.data)["password"])
            self.created_users.append(name)
        return Response(None)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "rabbitmq").mkdir()
        source = Path(__file__).resolve().parents[1] / "rabbitmq/service-permissions.json"
        (self.root / "rabbitmq/service-permissions.json").write_text(source.read_text())
        self.fake = FakeBootstrap(self.root)
        self.addCleanup(patch.stopall)
        patch.object(bootstrap_broker, "ROOT", self.root).start()
        patch.object(bootstrap_broker.subprocess, "Popen").start()
        patch.object(bootstrap_broker.urllib.request, "urlopen", side_effect=self.fake.api).start()

    def bootstrap(self): bootstrap_broker.ensure_bootstrap("messaging", self.fake.run)

    def test_auto_mode_profile_used_for_validation_and_apply(self):
        bootstrap_broker.ensure_bootstrap("messaging", self.fake.run, "k8s-auto")
        applies = [args for args in self.fake.private_arguments if "apply" in args and "-k" in args]
        self.assertEqual(len(applies), 2)
        self.assertTrue(all(args[args.index("-k") + 1] == "k8s-auto" for args in applies))

    def test_fresh_install_and_repeat_preserve_all_passwords(self):
        self.bootstrap()
        original = copy.deepcopy(self.fake.secrets)
        self.bootstrap()
        self.assertEqual(len(original), 5)
        self.assertEqual(original, self.fake.secrets)
        self.assertEqual(len(self.fake.created_users), 4)
        self.assertEqual(self.fake.configmaps, {})
        self.assertFalse((self.root / ".local/tls").exists())
        for name, credential in original.items():
            if name.startswith("rabbitmq-") and "password" in credential:
                self.assertFalse(any(credential["password"] in str(args) for args in self.fake.private_arguments))

    def test_existing_user_without_password_is_not_rotated(self):
        self.fake.users["gearflow_os"] = user("existing-password")
        with self.assertRaisesRegex(RuntimeError, "recuperar"):
            self.bootstrap()
        self.assertEqual(self.fake.created_users, [])
        self.assertNotIn("rabbitmq-os-credentials", self.fake.secrets)

    def test_upgrade_removes_old_certificates_after_rollout_and_preserves_credentials(self):
        self.bootstrap()
        original = copy.deepcopy(self.fake.secrets)
        self.fake.secrets["rabbitmq-tls"] = {"tls.crt": "expired", "tls.key": "old", "ca.crt": "old"}
        self.fake.configmaps["rabbitmq-ca"] = {"ca.crt": "old"}
        self.fake.private_arguments.clear()
        self.bootstrap()
        self.assertEqual(original, self.fake.secrets)
        self.assertEqual(self.fake.configmaps, {})
        commands = self.fake.private_arguments
        rollout = next(i for i, args in enumerate(commands) if "rollout" in args)
        deletes = [i for i, args in enumerate(commands) if "delete" in args]
        self.assertEqual(len(deletes), 2)
        self.assertTrue(all(i > rollout for i in deletes))

    def test_existing_volume_without_admin_recovery_is_not_bootstrapped(self):
        self.fake.retained_claim = True
        with self.assertRaisesRegex(RuntimeError, "recuperar credenciais"):
            self.bootstrap()
        self.assertEqual(self.fake.secrets, {})

    def test_legacy_local_password_is_migrated_without_changing_user(self):
        directory = self.root / ".local"
        directory.mkdir()
        (directory / "service-credentials.json").write_text(json.dumps({"gearflow_os": "existing-password"}))
        self.fake.users["gearflow_os"] = user("existing-password")
        self.bootstrap()
        self.assertEqual(self.fake.secrets["rabbitmq-os-credentials"]["password"], "existing-password")
        self.assertNotIn("gearflow_os", self.fake.created_users)

    def test_wrong_legacy_password_blocks_before_creating_service_secrets(self):
        directory = self.root / ".local"
        directory.mkdir()
        (directory / "service-credentials.json").write_text(json.dumps({"gearflow_os": "wrong-password"}))
        self.fake.users["gearflow_os"] = user("existing-password")
        with self.assertRaisesRegex(RuntimeError, "divergentes"):
            self.bootstrap()
        self.assertEqual(self.fake.created_users, [])
        self.assertNotIn("rabbitmq-os-credentials", self.fake.secrets)


def objects():
    return [{"kind": "PersistentVolumeClaim", "metadata": {"name": "data-rabbitmq-0", "namespace": "messaging", "uid": "claim-uid"},
             "spec": {"volumeName": "pvc-claim-uid"}},
            {"kind": "PersistentVolume", "metadata": {"name": "pvc-claim-uid", "uid": "pv-uid"},
             "spec": {"claimRef": {"namespace": "messaging", "name": "data-rabbitmq-0", "uid": "claim-uid"},
                      "storageClassName": "gearflow-broker-gp3", "csi": {"driver": "ebs.csi.aws.com", "volumeHandle": "vol-0123"}}}]


class FakeRemoval(Removal):
    def __init__(self, path, execute):
        super().__init__("test", "us-east-1", path, execute)
        self.items, self.exists, self.deleted = objects(), True, []
        self.tags = [{"Key": "CSIVolumeName", "Value": "pvc-claim-uid"}, {"Key": "ebs.csi.aws.com/cluster", "Value": "true"}]
        self.lost_response = False
        self.created_at = "original"

    def aws(self, service, *args, **kwargs):
        if args[0] == "get-caller-identity": return {"Account": "test-account"}
        if args[0] == "list-clusters": return {"clusters": ["test"]}
        if args[0] == "describe-cluster": return {"cluster": {"createdAt": self.created_at, "endpoint": "https://test", "status": "ACTIVE"}}
        if args[0] == "describe-volumes":
            return {"Volumes": [{"VolumeId": "vol-0123", "State": "available", "Attachments": [], "Tags": self.tags}]} if self.exists else None
        if args[0] == "delete-volume":
            if any(item["kind"] == "PersistentVolumeClaim" for item in self.items): raise AssertionError("PVC ainda existe")
            self.exists = False
            self.deleted.append("ebs")
            if self.lost_response:
                self.lost_response = False
                raise RuntimeError("resposta perdida")
            return None
        raise AssertionError(args)

    def kube(self, *args):
        if args[:2] == ("config", "view"): return "https://test"
        if "pvc,pv,services,ingresses" in args: return json.dumps({"items": self.items})
        if "get" in args and "statefulset" in args: return json.dumps({"spec": {"selector": {"matchLabels": {"app": "rabbitmq"}}}})
        if "get" in args and "pods" in args: return '{"items":[]}'
        if "get" in args and "pv" in args: return json.dumps(next(item for item in self.items if item["kind"] == "PersistentVolume"))
        if "delete" in args:
            kind = args[args.index("delete") + 1]
            self.deleted.append(kind)
            if kind == "pvc": self.items = [item for item in self.items if item["kind"] != "PersistentVolumeClaim"]
            if kind == "pv":
                if self.exists: raise AssertionError("EBS ainda existe")
                self.items = []
            return ""
        raise AssertionError(args)


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.removal = FakeRemoval(Path(self.temporary.name) / "journal.json", True)
        self.sleep = patch("remove_broker.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_preview_never_deletes_or_creates_journal(self):
        self.removal.execute = False
        self.removal.remove()
        self.assertEqual(self.removal.deleted, [])
        self.assertFalse(self.removal.path.exists())

    def test_only_owned_volume_is_deleted_in_safe_order(self):
        self.removal.remove()
        self.assertEqual(self.removal.deleted, ["statefulset", "pvc", "ebs", "pv"])
        self.assertTrue(json.loads(self.removal.path.read_text())["completed"])

    def test_foreign_claim_blocks_before_any_deletion(self):
        foreign = copy.deepcopy(self.removal.items[0])
        foreign["metadata"]["namespace"] = "other-service"
        self.removal.items.append(foreign)
        with self.assertRaisesRegex(RuntimeError, "fora do RabbitMQ"):
            self.removal.remove()
        self.assertEqual(self.removal.deleted, [])

    def test_wrong_ebs_tags_block_before_stopping_broker(self):
        self.removal.tags[0]["Value"] = "another-volume"
        with self.assertRaisesRegex(RuntimeError, "comprovacao"):
            self.removal.remove()
        self.assertEqual(self.removal.deleted, [])

    def test_lost_delete_response_is_resumable_without_deleting_other_volume(self):
        self.removal.lost_response = True
        with self.assertRaisesRegex(RuntimeError, "resposta perdida"):
            self.removal.remove()
        self.assertTrue(self.removal.path.exists())
        self.removal.remove()
        self.assertEqual(self.removal.deleted.count("ebs"), 1)
        self.assertTrue(json.loads(self.removal.path.read_text())["completed"])

    def test_wrong_claim_uid_is_rejected(self):
        self.removal.items[0]["metadata"]["uid"] = "new-claim"
        with self.assertRaisesRegex(RuntimeError, "divergentes"):
            identify(self.removal.items)

    def test_pending_journal_does_not_target_recreated_cluster(self):
        record, _ = self.removal.prepare()
        self.removal.save(record)
        self.removal.created_at = "new-cluster"
        with self.assertRaisesRegex(RuntimeError, "recriado"):
            self.removal.remove()
        self.assertEqual(self.removal.deleted, [])

    def test_load_balancer_blocks_before_any_deletion(self):
        self.removal.items.append({"kind": "Service", "metadata": {"name": "public-service", "namespace": "other"}, "spec": {"type": "LoadBalancer"}})
        with self.assertRaisesRegex(RuntimeError, "fora do RabbitMQ"):
            self.removal.remove()
        self.assertEqual(self.removal.deleted, [])


if __name__ == "__main__":
    unittest.main()
