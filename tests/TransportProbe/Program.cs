using System.Collections.Concurrent;
using System.Reflection;
using System.Text;
using System.Text.Json;
using MassTransit;
using RabbitMQ.Client;
using RabbitMQ.Client.Exceptions;

// Teste de transporte/ACL. Os consumidores confirmam desserializacao; nao executam regras de negocio.
// Credenciais temporarias chegam por stdin, nunca por argumentos nem arquivos versionados.
var input = JsonDocument.Parse(await Console.In.ReadToEndAsync()).RootElement;
var root = input.GetProperty("root").GetString()!;
var port = input.GetProperty("port").GetInt32();
var users = input.GetProperty("users").Deserialize<Dictionary<string, string>>()!;
var contract = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "rabbitmq/contracts.json"))).RootElement;
using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(100));
var token = deadline.Token;
var json = new JsonSerializerOptions(JsonSerializerDefaults.Web);
json.Converters.Add(new System.Text.Json.Serialization.JsonStringEnumConverter());
var seen = new ConcurrentDictionary<string, int>();
var failId = Guid.NewGuid();
var id = Guid.NewGuid();
var buses = new List<IBusControl>();

async Task<IConnection> Connect(string service) => await new ConnectionFactory {
    HostName = "127.0.0.1", Port = port, VirtualHost = "gearflow", UserName = "gearflow_" + service,
    Password = users["gearflow_" + service]
}.CreateConnectionAsync(token);

Type MessageType(string service, string name) => Assembly.GetExecutingAssembly().GetType(
    contract.GetProperty(service).GetProperty("namespace").GetString() + "." + name, true)!;

object? Make(Type type) {
    if (Nullable.GetUnderlyingType(type) is not null) return null;
    if (type == typeof(Guid)) return id;
    if (type == typeof(string)) return "sample";
    if (type == typeof(DateTime)) return DateTime.UtcNow;
    if (type == typeof(DateTimeOffset)) return DateTimeOffset.UtcNow;
    if (type.IsEnum) return Enum.GetValues(type).GetValue(0);
    if (type.IsValueType) return Activator.CreateInstance(type);
    if (type.IsArray) return Array.CreateInstance(type.GetElementType()!, 0);
    if (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(IReadOnlyList<>))
        return Array.CreateInstance(type.GenericTypeArguments[0], 0);
    var constructor = type.GetConstructors().First();
    return constructor.Invoke(constructor.GetParameters().Select(p => Make(p.ParameterType)).ToArray());
}

async Task Publish(IConnection connection, string exchange, string route, object body, string contentType = "application/json", Guid? messageId = null) {
    await using var channel = await connection.CreateChannelAsync(new CreateChannelOptions(true, true), token);
    await channel.BasicPublishAsync(exchange, route, true, new BasicProperties {
        ContentType = contentType, Persistent = true, MessageId = (messageId ?? id).ToString("D"), CorrelationId = id.ToString("D")
    }, JsonSerializer.SerializeToUtf8Bytes(body, json), token);
}

async Task<BasicGetResult> Get(IConnection connection, string queue) {
    while (!token.IsCancellationRequested) {
        await using var channel = await connection.CreateChannelAsync(cancellationToken: token);
        try {
            var delivery = await channel.BasicGetAsync(queue, false, token);
            if (delivery is not null) {
                await channel.BasicAckAsync(delivery.DeliveryTag, false, token);
                return delivery;
            }
        } catch (OperationInterruptedException error) when (error.ShutdownReason?.ReplyCode == 404) { }
        await Task.Delay(100, token);
    }
    throw new TimeoutException("Mensagem ausente: " + queue);
}

async Task Denied(string service, Func<IChannel, Task> action) {
    await using var connection = await Connect(service);
    await using var channel = await connection.CreateChannelAsync(new CreateChannelOptions(true, true), token);
    try { await action(channel); }
    catch (OperationInterruptedException error) when (error.ShutdownReason?.ReplyCode == 403) { return; }
    throw new Exception("ACL aceitou operacao indevida: " + service);
}

try {
    foreach (var service in new[] { "operation", "inventory" }) {
        var spec = contract.GetProperty(service);
        var events = spec.GetProperty("events").EnumerateObject().ToDictionary(p => MessageType(service, p.Name), p => p.Value.GetString()!);
        var bus = Bus.Factory.CreateUsingRabbitMq(cfg => {
            cfg.Host("127.0.0.1", (ushort)port, "gearflow", host => {
                host.Username("gearflow_" + service); host.Password(users["gearflow_" + service]);
            });
            cfg.MessageTopology.SetEntityNameFormatter(new EventNames(cfg.MessageTopology.EntityNameFormatter, events));
            cfg.ConfigureJsonSerializerOptions(options => { options.RespectRequiredConstructorParameters = true; return options; });
            foreach (var group in new[] { "commands", "event_consumers" })
                foreach (var item in spec.GetProperty(group).EnumerateObject()) {
                    var type = MessageType(service, item.Name);
                    cfg.ReceiveEndpoint(item.Value.GetString()!, endpoint => {
                        endpoint.UseMessageRetry(retry => retry.Immediate(1));
                        typeof(Handlers).GetMethod(nameof(Handlers.Register))!.MakeGenericMethod(type)
                            .Invoke(null, [endpoint, seen, failId]);
                    });
                }
        });
        buses.Add(bus);
        await bus.StartAsync(token);
    }
    await using var os = await Connect("os");
    await using var budget = await Connect("budget");
    // Declaracoes do RabbitTopology do Billing, com queue type do manifesto Kubernetes.
    await using (var channel = await budget.CreateChannelAsync(cancellationToken: token)) {
        foreach (var exchange in new[] { "gearflow.commands.v1", "gearflow.events.v1", "gearflow.deadletters.v1" })
            await channel.ExchangeDeclareAsync(exchange, "topic", true, false, cancellationToken: token);
        foreach (var queue in new[] { "gearflow.billing.commands.v1", "gearflow.billing.commands.v1.dlq", "gearflow.os.billing.events.v1" })
            await channel.QueueDeclareAsync(queue, true, false, false,
                new Dictionary<string, object?> { ["x-queue-type"] = "quorum" }, cancellationToken: token);
        foreach (var command in contract.GetProperty("budget").GetProperty("commands").EnumerateArray())
            await channel.QueueBindAsync("gearflow.billing.commands.v1", "gearflow.commands.v1", "billing." + command.GetString(), cancellationToken: token);
        await channel.QueueBindAsync("gearflow.billing.commands.v1.dlq", "gearflow.deadletters.v1", "gearflow.billing.commands.v1", cancellationToken: token);
        await channel.QueueBindAsync("gearflow.os.billing.events.v1", "gearflow.events.v1", "gearflow-budget-service.*", cancellationToken: token);
    }
    // Repetir declaracoes que o OS faz no startup e no envio de comandos.
    var defs = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "rabbitmq/definitions.json"))).RootElement;
    await using (var channel = await os.CreateChannelAsync(cancellationToken: token)) {
        foreach (var binding in defs.GetProperty("bindings").EnumerateArray().Where(b => b.GetProperty("destination").GetString() == "gearflow.os-service")) {
            var source = binding.GetProperty("source").GetString()!;
            var type = defs.GetProperty("exchanges").EnumerateArray().Single(e => e.GetProperty("name").GetString() == source).GetProperty("type").GetString()!;
            await channel.ExchangeDeclareAsync(source, type, true, false, cancellationToken: token);
            await channel.QueueDeclareAsync("gearflow.os-service", true, false, false, cancellationToken: token);
            await channel.QueueBindAsync("gearflow.os-service", source, binding.GetProperty("routing_key").GetString()!, cancellationToken: token);
        }
        foreach (var queue in contract.GetProperty("os").GetProperty("command_queues").EnumerateArray())
            await channel.QueueDeclareAsync(queue.GetString()!, true, false, false, cancellationToken: token);
    }
    foreach (var service in new[] { "operation", "inventory" }) {
        foreach (var item in contract.GetProperty(service).GetProperty("commands").EnumerateObject()) {
            var type = MessageType(service, item.Name);
            var body = new { messageId = id, correlationId = id, conversationId = id,
                sourceAddress = "queue:gearflow-os-service", destinationAddress = "queue:" + item.Value.GetString(),
                messageType = new[] { "urn:message:" + type.Namespace + ":" + type.Name }, message = Make(type) };
            // Somente os seis destinos usados pelo OS sao autorizados para declaracao. Publish default segue o codigo atual.
            await Publish(os, "", item.Value.GetString()!, body, "application/vnd.masstransit+json");
        }
    }
    while (seen.Count < 8) await Task.Delay(100, token);
    Console.WriteLine("PASS: oito comandos com envelope/URN do OS desserializados por MassTransit 8.5.11.");
    foreach (var command in contract.GetProperty("budget").GetProperty("commands").EnumerateArray()) {
        var name = command.GetString()!;
        object payload = name switch {
            "GenerateBudget" => new { customer = new { name = "Demo", email = "demo@example.invalid" }, items = new[] { new { referenceId = id, type = "Job", description = "Demo", unitPrice = 1m, quantity = 1m } }, expiresOn = DateTimeOffset.UtcNow.AddDays(3) },
            "RequestPayment" => new { budgetId = id, amount = 1m, currency = "BRL" },
            "CancelBudget" => new { budgetId = id, reason = "demo" },
            _ => new { budgetId = id, paymentId = id, reason = "demo" }
        };
        var envelope = new { messageId = id, messageType = name, schemaVersion = 1, occurredOn = DateTimeOffset.UtcNow, correlationId = id, serviceOrderId = id, causationId = (Guid?)null, producer = "gearflow-os-service", payload };
        await Publish(os, "gearflow.commands.v1", "billing." + name, envelope);
        var delivery = await Get(budget, "gearflow.billing.commands.v1");
        if (delivery.BasicProperties.ContentType != "application/json" || delivery.BasicProperties.MessageId != id.ToString("D") || JsonDocument.Parse(delivery.Body).RootElement.GetProperty("payload").GetRawText() != JsonSerializer.Serialize(payload, json)) throw new Exception("Envelope Billing alterado");
    }
    foreach (var name in contract.GetProperty("budget").GetProperty("events").EnumerateArray()) {
        var type = "gearflow-budget-service." + name.GetString();
        await Publish(budget, "gearflow.events.v1", type, new { messageId = id, messageType = type, schemaVersion = 1, correlationId = id, serviceOrderId = id, producer = "gearflow-budget-service", payload = new { budgetId = id } });
        await Get(os, "gearflow.os-service");
        await Get(budget, "gearflow.os.billing.events.v1");
    }
    await Publish(budget, "gearflow.deadletters.v1", "gearflow.billing.commands.v1", new { originalExchange = "gearflow.commands.v1", originalRoutingKey = "billing.GenerateBudget", bodyBase64 = "e30=", code = "demo", attempts = 4 });
    await Get(budget, "gearflow.billing.commands.v1.dlq");
    // O replay implementado no Billing republica no exchange de comandos.
    await Publish(budget, "gearflow.commands.v1", "billing.CancelBudget", new { replay = true });
    await Get(budget, "gearflow.billing.commands.v1");
    Console.WriteLine("PASS: quatro comandos Billing, nove eventos, DLQ explicita e permissoes de replay.");
    for (var index = 0; index < buses.Count; index++) {
        var service = index == 0 ? "operation" : "inventory";
        var subscribed = contract.GetProperty("os").GetProperty(service + "_subscriptions").EnumerateArray().Select(p => p.GetString()).ToHashSet();
        foreach (var item in contract.GetProperty(service).GetProperty("events").EnumerateObject()) {
            var type = MessageType(service, item.Name);
            await buses[index].Publish(Make(type)!, type, token);
            if (subscribed.Contains(item.Name)) {
                var received = await Get(os, "gearflow.os-service");
                if (received.Exchange != item.Value.GetString() || received.BasicProperties.ContentType != "application/vnd.masstransit+json") throw new Exception("Evento MassTransit divergente");
            }
        }
    }
    while (!seen.ContainsKey("LowStockAlert") || !seen.ContainsKey("StockInsufficient")) await Task.Delay(100, token);
    // Falha real no consumer e mensagem desconhecida exercitam _error, Fault<T> e _skipped.
    await Publish(os, "", "inventory-reserve-stock", new { messageId = failId, messageType = new[] { "urn:message:GearFlow.Contracts:ReserveStock" }, message = Make(MessageType("inventory", "ReserveStock")) }, "application/vnd.masstransit+json", failId);
    await using (var inventory = await Connect("inventory")) {
        await Get(inventory, "inventory-reserve-stock_error");
        await Publish(os, "", "inventory-reserve-stock", new { messageId = id, messageType = new[] { "urn:message:GearFlow.Contracts:ConsumeStock" }, message = Make(MessageType("inventory", "ConsumeStock")) }, "application/vnd.masstransit+json");
        await Get(inventory, "inventory-reserve-stock_skipped");
    }
    Console.WriteLine("PASS: 19 eventos, assinaturas OS, dois consumidores de email e recursos Fault/_error/_skipped.");
    foreach (var service in new[] { "os", "budget", "operation", "inventory" }) {
        var foreign = service == "os" ? "operation-start-diagnostic" : "gearflow.os-service";
        await Denied(service, async channel => { await channel.BasicGetAsync(foreign, true, token); });
        await Denied(service, async channel => { await channel.QueueDeclareAsync("unauthorized.queue", true, false, false, cancellationToken: token); });
        if (service != "os") await Denied(service, async channel => { await channel.BasicPublishAsync("gearflow.events", "unauthorized", Encoding.UTF8.GetBytes("denied"), token); });
    }
    Console.WriteLine("PASS: consumo de filas alheias, criacao fora dos contratos e publicacao em exchange alheia negados.");
} finally {
    foreach (var bus in buses) await bus.StopAsync(CancellationToken.None);
}

sealed class EventNames(IEntityNameFormatter fallback, Dictionary<Type, string> events) : IEntityNameFormatter {
    public string FormatEntityName<T>() => events.GetValueOrDefault(typeof(T)) ?? fallback.FormatEntityName<T>();
}
static class Handlers {
    public static void Register<T>(IRabbitMqReceiveEndpointConfigurator endpoint, ConcurrentDictionary<string, int> seen, Guid failId) where T : class =>
        endpoint.Handler<T>(context => {
            if (context.MessageId == failId) throw new InvalidOperationException("Falha simulada de transporte");
            seen.AddOrUpdate(typeof(T).Name, 1, (_, count) => count + 1);
            return Task.CompletedTask;
        });
}
