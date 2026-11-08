"""Pika 1.3.2: contratos/ACL em vhost temporario; nunca consome filas de negocio."""
import json
import logging
import os
import socket
import time

logging.disable(logging.CRITICAL)
HOST = "rabbitmq.messaging.svc.cluster.local"
PHASE = os.environ["PHASE"]


def blocked_port(port):
    try:
        with socket.create_connection((HOST, port), timeout=3):
            pass
    except (TimeoutError, ConnectionRefusedError):
        return
    raise AssertionError(f"Porta {port} acessivel indevidamente")


if PHASE == "network-denied":
    blocked_port(5672)
    print("PASS: origem sem autorizacao de rede bloqueada", flush=True)
    raise SystemExit(0)

import pika

with open("/credentials/users.json", encoding="utf-8-sig") as source:
    SETTINGS = json.load(source)


def connect(service, password=None):
    user = SETTINGS["users"][service]
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=HOST, port=5672, virtual_host=SETTINGS["vhost"],
        credentials=pika.PlainCredentials(user["username"], password or user["password"]),
        heartbeat=30, socket_timeout=5, stack_timeout=12, blocked_connection_timeout=10, connection_attempts=1,
    ))


def publish(service, exchange, route, body):
    with connect(service) as connection:
        channel = connection.channel()
        channel.confirm_delivery()
        channel.basic_publish(exchange, route, body, mandatory=True,
                              properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"))


def consume(service, queue, expected):
    with connect(service) as connection:
        channel = connection.channel()
        for _ in range(30):
            method, properties, body = channel.basic_get(queue, auto_ack=False)
            if method:
                assert body == expected and properties.delivery_mode == 2
                channel.basic_ack(method.delivery_tag)
                return
            time.sleep(0.2)
    raise AssertionError("Mensagem de teste ausente: " + queue)


def denied(service, action):
    with connect(service) as connection:
        channel = connection.channel()
        channel.confirm_delivery()
        try:
            action(channel)
        except pika.exceptions.ChannelClosedByBroker as error:
            assert error.reply_code == 403
            return
    raise AssertionError("Operacao nao autorizada aceita: " + service)


if PHASE == "verify":
    publish("os", "gearflow.commands.v1", "billing.CancelBudget", b"billing-command-smoke")
    consume("budget", "gearflow.billing.commands.v1", b"billing-command-smoke")
    publish("budget", "gearflow.events.v1", "gearflow-budget-service.BudgetCanceled", b"billing-event-smoke")
    consume("os", "gearflow.os-service", b"billing-event-smoke")
    consume("budget", "gearflow.os.billing.events.v1", b"billing-event-smoke")
    publish("budget", "gearflow.deadletters.v1", "gearflow.billing.commands.v1", b"explicit-dlq-smoke")
    consume("budget", "gearflow.billing.commands.v1.dlq", b"explicit-dlq-smoke")
    for service, queue, event in (("operation", "operation-start-diagnostic", "operation.diagnostic-finished"),
                                  ("inventory", "inventory-reserve-stock", "inventory.stock-reserved")):
        publish("os", "", queue, b"direct-command-smoke")
        consume(service, queue, b"direct-command-smoke")
        publish(service, event, "", b"fanout-event-smoke")
        consume("os", "gearflow.os-service", b"fanout-event-smoke")
    for service in ("os", "budget", "operation", "inventory"):
        foreign = "operation-start-diagnostic" if service == "os" else "gearflow.os-service"
        denied(service, lambda channel: channel.basic_get(foreign))
        denied(service, lambda channel: channel.queue_declare("unauthorized.queue", durable=True))
    try:
        connection = connect("os", password="invalid-smoke-password")
    except pika.exceptions.ProbableAuthenticationError:
        pass
    else:
        connection.close()
        raise AssertionError("Senha incorreta aceita")
    blocked_port(5671)
    blocked_port(15672)
    print("PASS: rotas topic/fanout/default, DLQ explicita, ACL e senha incorreta; painel/TLS bloqueados", flush=True)
elif PHASE == "publish-persistence":
    publish("os", "", "inventory-consume-stock", b"persistent-across-pod-recreation")
    print("PASS: mensagem persistente publicada com confirm no vhost isolado", flush=True)
elif PHASE == "consume-persistence":
    consume("inventory", "inventory-consume-stock", b"persistent-across-pod-recreation")
    print("PASS: mensagem recuperada depois de recriar o Pod RabbitMQ", flush=True)
else:
    raise AssertionError("Fase desconhecida")
