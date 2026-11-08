"""Renderiza topologia/ACL dos contratos extraidos; --check verifica sem alterar arquivos."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def pattern(names, extra=()):
    return "^(?:" + "|".join([re.escape(name) for name in sorted(set(names))] + list(extra)) + ")$"


def render(contract):
    vhost = contract["vhost"]
    os, billing = contract["os"], contract["budget"]
    definitions = {"vhosts": [{"name": vhost}], "exchanges": [], "queues": [], "bindings": [], "policies": []}
    exchanges = {}

    def exchange(name, kind):
        if name in exchanges:
            if exchanges[name] != kind:
                raise ValueError(f"Tipos conflitantes para {name}")
            return
        exchanges[name] = kind
        definitions["exchanges"].append(dict(name=name, vhost=vhost, type=kind, durable=True,
                                            auto_delete=False, internal=False, arguments={}))

    def queue(name, kind="classic"):
        definitions["queues"].append(dict(name=name, vhost=vhost, durable=True, auto_delete=False,
                                         arguments={"x-queue-type": kind}))

    def bind(source, destination, route="", kind="queue"):
        definitions["bindings"].append(dict(source=source, vhost=vhost, destination=destination,
                                           destination_type=kind, routing_key=route, arguments={}))

    topics = [os["events_exchange"], billing["commands_exchange"], billing["events_exchange"], billing["dead_letter_exchange"]]
    for name in topics:
        exchange(name, "topic")
    queue(os["queue"])
    for name in (billing["commands_queue"], billing["commands_queue"] + ".dlq", billing["events_queue"]):
        queue(name, billing["queue_type"])
    for command in billing["commands"]:
        bind(billing["commands_exchange"], billing["commands_queue"], "billing." + command)
    bind(billing["dead_letter_exchange"], billing["commands_queue"] + ".dlq", billing["commands_queue"])
    for destination in (billing["events_queue"], os["queue"]):
        bind(billing["events_exchange"], destination, billing["event_prefix"] + "*")
    for route in os["legacy_bindings"]:
        bind(os["events_exchange"], os["queue"], route)

    permissions = {"vhost": vhost, "users": []}

    def user(name, configure, write, read, extra=()):
        permissions["users"].append(dict(name="gearflow_" + name, configure=pattern(configure, extra),
                                          write=pattern(write, extra), read=pattern(read, extra)))

    for service in ("operation", "inventory"):
        spec = contract[service]
        events = list(spec["events"].values())
        for event in events:
            exchange(event, "fanout")
        for message, endpoint in {**spec["commands"], **spec["event_consumers"]}.items():
            exchange(endpoint, "fanout")
            queue(endpoint)
            bind(endpoint, endpoint)
            source = spec["events"].get(message) or spec["namespace"] + ":" + message
            exchange(source, "fanout")
            bind(source, endpoint, kind="exchange")
        for message in os[service + "_subscriptions"]:
            event = spec["events"][message]
            bind(event, os["queue"])
        # MassTransit gera nomes efemeros pelo host/processo, sem prefixo configurado pela aplicacao.
        temporary = r"[A-Za-z0-9_-]+_bus_[A-Za-z0-9]+"
        namespace = re.escape(spec["namespace"])
        scoped = pattern([], (
            service + r"[-.][a-z0-9-]+(?:_(?:error|skipped))?",
            namespace + r":[A-Za-z][A-Za-z0-9]*",
            r"MassTransit:(?:ReceiveFault|Fault(?:--" + namespace + r":[A-Za-z][A-Za-z0-9]*--)?)",
            temporary,
        ))
        permissions["users"].append(dict(name="gearflow_" + service, configure=scoped, write=scoped, read=scoped))

    os_events_pattern = (r"operation\.[a-z-]+", r"inventory\.stock-[a-z-]+")
    os_read = pattern([os["queue"], os["events_exchange"], billing["events_exchange"]], os_events_pattern)
    user("os", [],
         [os["events_exchange"], billing["commands_exchange"], os["queue"], "amq.default"],
         [])
    permissions["users"][-1]["configure"] = pattern([], (
        r"gearflow\.(?:events(?:\.v1)?|commands\.v1|os-service)", *os_events_pattern,
        r"operation-(?:start-(?:diagnostic|execution)|cancel-operation)",
        r"inventory-(?:reserve|release|consume)-stock",
    ))
    permissions["users"][-1]["read"] = os_read
    billing_resources = topics[1:] + [billing["commands_queue"], billing["commands_queue"] + ".dlq", billing["events_queue"]]
    # Replay operacional implementado pelo Billing le sua DLQ e republica comandos.
    user("budget", billing_resources, billing_resources, billing_resources)
    return definitions, permissions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = json.loads((ROOT / "rabbitmq/contracts.json").read_text())
    for name, value in zip(("definitions.json", "service-permissions.json"), render(source)):
        path = ROOT / "rabbitmq" / name
        if args.check:
            if json.loads(path.read_text()) != value:
                raise SystemExit(f"{name} desatualizado; execute python scripts/cd/sync_topology.py")
        else:
            path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Topologia e permissoes sincronizadas com contracts.json.")


if __name__ == "__main__":
    main()
