using System.Text.Json.Serialization;

namespace GearFlow.Contracts;

/// <summary>
/// Tipo de item de estoque. Trafega como <b>string</b> no JSON (número em contrato entre serviços
/// quebra em silêncio se alguém reordenar o enum).
/// </summary>
[JsonConverter(typeof(JsonStringEnumConverter))]
public enum InventoryItemType
{
    Part = 1,
    Consumable = 2,
}

/// <summary>Item a reservar. Peça exige quantidade inteira; insumo aceita fração (ex.: litros).</summary>
public sealed record StockItemLine(InventoryItemType ItemType, Guid ItemId, decimal Quantity);

/// <summary>Item que não pôde ser reservado. Item inexistente aparece com <c>Available = 0</c>.</summary>
public sealed record MissingStockItem(
    InventoryItemType ItemType, Guid ItemId, string ItemName, decimal Requested, decimal Available);

// ---------------------------------------------------------------------------------------------
// Comandos (orquestrador → Inventory). CorrelationId = ServiceOrderId.
// ---------------------------------------------------------------------------------------------

/// <summary>Reserva TODOS os itens ou nenhum. Responde <see cref="StockReserved"/> ou <see cref="StockInsufficient"/>.</summary>
public sealed record ReserveStock(
    Guid CorrelationId, Guid MessageId, DateTime OccurredOn, IReadOnlyList<StockItemLine> Items);

/// <summary>Compensação: devolve a reserva ao disponível. Idempotente. Responde <see cref="StockReleased"/>.</summary>
public sealed record ReleaseStock(Guid CorrelationId, Guid MessageId, DateTime OccurredOn, string Reason);

/// <summary>
/// Baixa definitiva do reservado (a OS foi executada). Ponto sem volta: não há compensação.
/// O Inventory usa as linhas da própria reserva. Responde <see cref="StockConsumed"/>.
/// </summary>
public sealed record ConsumeStock(Guid CorrelationId, Guid MessageId, DateTime OccurredOn);

// ---------------------------------------------------------------------------------------------
// Eventos (Inventory → orquestrador). CorrelationId = ServiceOrderId.
// ---------------------------------------------------------------------------------------------

public sealed record StockReserved(
    Guid CorrelationId, Guid MessageId, DateTime OccurredOn, IReadOnlyList<StockItemLine> Lines);

public sealed record StockInsufficient(
    Guid CorrelationId, Guid MessageId, DateTime OccurredOn, IReadOnlyList<MissingStockItem> Missing);

public sealed record StockReleased(Guid CorrelationId, Guid MessageId, DateTime OccurredOn);

public sealed record StockConsumed(Guid CorrelationId, Guid MessageId, DateTime OccurredOn);

// ---------------------------------------------------------------------------------------------
// Eventos que não pertencem a uma OS (sem CorrelationId).
// ---------------------------------------------------------------------------------------------

/// <summary>
/// Estoque reposto (<c>PATCH …/stock</c>). Era o <c>PartsReplenishedIntegrationEvent</c> do monólito.
/// O orquestrador reenvia <see cref="ReserveStock"/> para as OS que aguardam este item.
/// </summary>
public sealed record StockReplenished(Guid MessageId, DateTime OccurredOn, InventoryItemType ItemType, Guid ItemId);

/// <summary>Item no/abaixo do estoque mínimo após uma baixa (regra do <c>LowStockAlert</c> do monólito).</summary>
public sealed record LowStockAlert(
    Guid MessageId, DateTime OccurredOn, InventoryItemType ItemType, Guid ItemId, string ItemName,
    decimal Remaining, decimal Minimum);
