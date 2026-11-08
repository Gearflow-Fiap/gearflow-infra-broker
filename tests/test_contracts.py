import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/cd"))
from sync_topology import render


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / "rabbitmq/contracts.json").read_text())
        self.definitions = json.loads((ROOT / "rabbitmq/definitions.json").read_text())
        self.users = {u["name"]: u for u in json.loads((ROOT / "rabbitmq/service-permissions.json").read_text())["users"]}

    def test_committed_resources_follow_catalog(self):
        definitions, permissions = render(self.contract)
        self.assertEqual(self.definitions, definitions)
        self.assertEqual(list(self.users.values()), permissions["users"])

    def test_permissions_fit_broker_limit_and_isolate_command_consumers(self):
        owners = {"os": "gearflow.os-service", "budget": "gearflow.billing.commands.v1",
                  "operation": "operation-start-diagnostic", "inventory": "inventory-reserve-stock"}
        for service, own in owners.items():
            rule = self.users["gearflow_" + service]
            for permission in ("configure", "write", "read"):
                self.assertLessEqual(len(rule[permission].encode()), 255)
                self.assertFalse(re.fullmatch(rule[permission], "unrelated.resource"))
            self.assertTrue(re.fullmatch(rule["read"], own))
            for other, queue in owners.items():
                if other != service:
                    self.assertFalse(re.fullmatch(rule["read"], queue))

    def test_os_can_declare_current_destinations_and_publish_default_exchange(self):
        os = self.users["gearflow_os"]
        for queue in self.contract["os"]["command_queues"]:
            self.assertTrue(re.fullmatch(os["configure"], queue))
            self.assertFalse(re.fullmatch(os["read"], queue))
        self.assertTrue(re.fullmatch(os["write"], "amq.default"))
        for user in ("budget", "operation", "inventory"):
            self.assertFalse(re.fullmatch(self.users["gearflow_" + user]["write"], "amq.default"))

    def test_framework_error_fault_and_temporary_resources_are_allowed(self):
        for service in ("operation", "inventory"):
            spec = self.contract[service]
            resources = ["machine_process_bus_abcdef"]
            resources += [queue + suffix for queue in spec["commands"].values() for suffix in ("_error", "_skipped")]
            resources += ["MassTransit:Fault--" + spec["namespace"] + ":" + message + "--" for message in spec["commands"]]
            for resource in resources:
                for permission in ("configure", "write", "read"):
                    self.assertTrue(re.fullmatch(self.users["gearflow_" + service][permission], resource), resource)


if __name__ == "__main__":
    unittest.main()
