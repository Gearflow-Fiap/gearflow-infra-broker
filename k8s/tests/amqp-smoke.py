"""Teste com Pika 1.3.2; filas sinteticas criadas pelo operador, sem declarar topologia."""
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
    USERS = json.load(source)
RUN_ID = os.environ["RUN_ID"]


def queue(service):
    return f"gearflow.{service}.smoke.{RUN_ID}"


def connect(service, password=None):
    user = f"gearflow_{service}"
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=HOST, port=5672, virtual_host="gearflow",
        credentials=pika.PlainCredentials(user, password if password is not None else USERS[user]),
        heartbeat=30,
        socket_timeout=5, stack_timeout=12, blocked_connection_timeout=10,
        connection_attempts=1,
    ))


def denied(service, action):
    with connect(service) as connection:
        channel = connection.channel()
        channel.confirm_delivery()
        try:
            action(channel)
        except pika.exceptions.ChannelClosedByBroker as error:
            assert error.reply_code == 403, f"Resposta inesperada: {error.reply_code}"
            return
    raise AssertionError("Operacao nao autorizada foi aceita")


def publish(channel, service, body):
    channel.confirm_delivery()
    channel.basic_publish(
        exchange="gearflow.events", routing_key=f"smoke.{RUN_ID}.{service}",
        body=body, mandatory=True,
        properties=pika.BasicProperties(delivery_mode=2, message_id=f"{RUN_ID}-{service}"),
    )


def get_message(channel, name):
    for _ in range(20):
        method, properties, body = channel.basic_get(name, auto_ack=False)
        if method:
            return method, properties, body
        time.sleep(0.25)
    raise AssertionError("Mensagem de teste nao encontrada")


if PHASE == "verify":
    for service in ("os", "budget", "operation", "inventory"):
        with connect(service) as connection:
            channel = connection.channel()
            publish(channel, service, f"smoke-{service}".encode())
            method, properties, body = get_message(channel, queue(service))
            assert body == f"smoke-{service}".encode() and properties.delivery_mode == 2
            channel.basic_ack(method.delivery_tag)
        other = "budget" if service != "budget" else "os"
        denied(service, lambda channel: channel.basic_get(queue(other)))
        denied(service, lambda channel: channel.basic_get(queue(service) + ".dlq"))
        denied(service, lambda channel: channel.queue_declare(queue(service) + ".forbidden", durable=True))
        denied(service, lambda channel: channel.basic_publish(exchange="", routing_key=queue(service), body=b"denied"))
        print(f"PASS: {service}: publish/consume/ack, isolamento de filas, DLQ e administracao negados", flush=True)
    try:
        connection = connect("os", password="invalid-smoke-password")
    except pika.exceptions.ProbableAuthenticationError:
        pass
    else:
        connection.close()
        raise AssertionError("Senha incorreta foi aceita")
    blocked_port(5671)
    blocked_port(15672)
    print("PASS: senha incorreta rejeitada; porta TLS e painel inacessiveis pela rede", flush=True)
    with connect("budget") as connection:
        channel = connection.channel()
        publish(channel, "budget", b"dead-letter-smoke")
        method, _, body = get_message(channel, queue("budget"))
        assert body == b"dead-letter-smoke"
        channel.basic_nack(method.delivery_tag, requeue=False)
    print("PASS: mensagem rejeitada sem requeue; verificar DLQ com operador", flush=True)
elif PHASE == "publish-persistence":
    with connect("os") as connection:
        publish(connection.channel(), "os", b"persistent-across-pod-recreation")
    print("PASS: mensagem persistente publicada com publisher confirm", flush=True)
elif PHASE == "consume-persistence":
    with connect("os") as connection:
        channel = connection.channel()
        method, properties, body = get_message(channel, queue("os"))
        assert body == b"persistent-across-pod-recreation" and properties.delivery_mode == 2
        channel.basic_ack(method.delivery_tag)
    print("PASS: mensagem persistente recuperada depois de recriar o Pod RabbitMQ", flush=True)
else:
    raise AssertionError("Fase de teste desconhecida")
