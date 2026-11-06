import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/cd"))
from cluster_access import ClusterAccess
from deploy_broker import topology_matches
from terraform_cluster import approve, fingerprint, sanitized, validate_config


class FakeAccess(ClusterAccess):
    def __init__(self, lease):
        super().__init__("test", "us-east-1", lease)
        self.info = {"arn": "test-arn", "createdAt": "2026-11-05", "status": "ACTIVE",
                     "resourcesVpcConfig": {"endpointPublicAccess": True, "endpointPrivateAccess": True,
                                            "publicAccessCidrs": ["198.51.100.10/32"]}}
        self.updates = []
        self.fail_after_update = False

    def aws(self, *arguments):
        if arguments[0] == "describe-cluster":
            return {"cluster": copy.deepcopy(self.info)}
        if arguments[0] == "describe-update":
            return {"update": {"status": "Successful"}}
        if arguments[0] == "update-cluster-config":
            cidrs = json.loads(arguments[arguments.index("--resources-vpc-config") + 1])["publicAccessCidrs"]
            self.info["resourcesVpcConfig"]["publicAccessCidrs"] = cidrs
            self.updates.append(cidrs)
            if self.fail_after_update:
                self.fail_after_update = False
                raise RuntimeError("resposta perdida depois de aceitar a alteracao")
            return {"update": {"id": "test-update"}}
        raise AssertionError("Chamada inesperada")


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.access = FakeAccess(Path(self.directory.name) / "lease.json")

    def test_prepare_does_not_change_endpoint(self):
        self.access.prepare("203.0.113.20/32")
        self.assertTrue(self.access.path.exists())
        self.assertEqual(self.access.updates, [])

    def test_cleanup_preserves_operator_and_new_concurrent_rules(self):
        self.access.prepare("203.0.113.20/32")
        self.access.open()
        self.access.info["resourcesVpcConfig"]["publicAccessCidrs"].append("198.51.100.30/32")
        self.access.close()
        self.assertEqual(self.access.updates[-1], ["198.51.100.10/32", "198.51.100.30/32"])
        self.access.close()
        self.assertEqual(len(self.access.updates), 2)

    def test_existing_access_is_not_removed(self):
        self.access.prepare("198.51.100.10/32")
        self.access.open()
        self.access.close()
        self.assertEqual(self.access.updates, [])

    def test_existing_subnet_is_not_removed(self):
        self.access.info["resourcesVpcConfig"]["publicAccessCidrs"] = ["198.51.100.0/24"]
        self.access.prepare("198.51.100.10/32")
        self.access.open()
        self.access.close()
        self.assertEqual(self.access.updates, [])

    def test_cleanup_after_lost_update_response(self):
        self.access.prepare("203.0.113.20/32")
        self.access.fail_after_update = True
        with self.assertRaises(RuntimeError):
            self.access.open()
        self.access.close()
        self.assertEqual(self.access.updates[-1], ["198.51.100.10/32"])

    def test_recreated_cluster_is_not_changed(self):
        self.access.prepare("203.0.113.20/32")
        self.access.info["createdAt"] = "new-cluster"
        with self.assertRaises(RuntimeError):
            self.access.close()
        self.assertEqual(self.access.updates, [])

    def test_refuses_empty_list_during_cleanup(self):
        self.access.prepare("203.0.113.20/32")
        self.access.open()
        self.access.info["resourcesVpcConfig"]["publicAccessCidrs"] = ["203.0.113.20/32"]
        with self.assertRaises(RuntimeError):
            self.access.close()

    def test_refuses_broad_access(self):
        with self.assertRaises(ValueError):
            self.access.prepare("0.0.0.0/0")
        self.access.info["resourcesVpcConfig"]["publicAccessCidrs"] = ["0.0.0.0/0"]
        with self.assertRaises(RuntimeError):
            self.access.prepare("203.0.113.20/32")


class TopologyTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.expected = json.loads((root / "rabbitmq/definitions.json").read_text())
        self.actual = {
            "exchanges": copy.deepcopy(self.expected["exchanges"]),
            "queues": copy.deepcopy(self.expected["queues"]),
            "policies": copy.deepcopy(self.expected["policies"]),
            "bindings": [{"source_name": item["source"], "destination_name": item["destination"],
                          "destination_kind": item["destination_type"], "routing_key": item["routing_key"],
                          "arguments": []} for item in self.expected["bindings"]],
        }
        for item in self.actual["queues"]:
            item["arguments"] = [["x-queue-type", "longstr", "classic"]]
        for item in self.actual["policies"]:
            item["definition"] = json.dumps(item["definition"])

    def test_matching_topology_with_extra_queue(self):
        self.actual["queues"].append({"name": "preserved-extra-queue"})
        self.assertTrue(topology_matches(self.expected, self.actual))

    def test_changed_queue_type_is_detected(self):
        self.actual["queues"][0]["arguments"] = [["x-queue-type", "longstr", "quorum"]]
        self.assertFalse(topology_matches(self.expected, self.actual))

    def test_missing_binding_is_detected(self):
        self.actual["bindings"].pop()
        self.assertFalse(topology_matches(self.expected, self.actual))

    def test_changed_dead_letter_policy_is_detected(self):
        self.actual["policies"][0]["definition"] = '{}'
        self.assertFalse(topology_matches(self.expected, self.actual))


class TerraformApprovalTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"variables": {"admin_cidrs": {"value": ["198.51.100.10/32"]}},
                     "resource_changes": [{"mode": "managed", "address": "aws_eks_cluster.this",
                                           "change": {"actions": ["update"], "before": {"version": "1.35"},
                                                      "after": {"version": "1.36"}}}]}
        self.commit = "a" * 40
        self.digest = fingerprint(self.plan, self.commit)

    def test_reviewed_update_is_approved(self):
        approve(self.plan, self.commit, self.digest, "gearflow-fase4", False)

    def test_new_commit_requires_new_approval(self):
        with self.assertRaises(ValueError):
            approve(self.plan, "b" * 40, self.digest, "gearflow-fase4", False)

    def test_changed_inputs_require_new_approval(self):
        self.plan["variables"]["admin_cidrs"]["value"] = ["198.51.100.20/32"]
        with self.assertRaises(ValueError):
            approve(self.plan, self.commit, self.digest, "gearflow-fase4", False)

    def test_replacement_requires_explicit_permission(self):
        self.plan["resource_changes"][0]["change"]["actions"] = ["delete", "create"]
        digest = fingerprint(self.plan, self.commit)
        with self.assertRaises(ValueError):
            approve(self.plan, self.commit, digest, "gearflow-fase4", False)
        approve(self.plan, self.commit, digest, "gearflow-fase4", True)

    def test_wrong_cluster_confirmation_is_rejected(self):
        with self.assertRaises(ValueError):
            approve(self.plan, self.commit, self.digest, "other-cluster", False)

    def test_unrelated_noop_and_plan_timestamp_do_not_change_receipt(self):
        self.plan["timestamp"] = "a different execution time"
        self.plan["resource_changes"].append({"mode": "managed", "address": "aws_vpc.this", "change": {"actions": ["no-op"]}})
        self.assertEqual(self.digest, fingerprint(self.plan, self.commit))

    def test_diagnostics_redact_arn_and_ip(self):
        message = sanitized('Resource arn:aws:eks:us-east-1:123456789012:cluster/example CIDR 198.51.100.10/32 failed')
        self.assertNotIn("123456789012", message)
        self.assertNotIn("198.51.100.10", message)

    def test_config_rejects_credentials(self):
        with self.assertRaises(ValueError):
            validate_config({"aws_access_key_id": "not-a-real-key"})


if __name__ == "__main__":
    unittest.main()
