using System.Text.Json.Serialization;

namespace GearFlow.Contracts.Operation.V1;

[JsonConverter(typeof(CommercialItemTypeConverter))]
public enum CommercialItemType { Labor, Part, Consumable }
public sealed class CommercialItemTypeConverter() : JsonStringEnumConverter<CommercialItemType>(allowIntegerValues: false);
public sealed record RequestedJob(Guid LineId, Guid CatalogJobId);
public sealed record RequestedPart(Guid LineId, Guid PartId, int Quantity);
public sealed record CommercialLine(Guid LineId, CommercialItemType ItemType, Guid ItemId, string Name, decimal Quantity, int UnitPriceCents, int LineTotalCents);
public sealed record DiagnosticSnapshot(Guid DiagnosticId, string Currency, string PricingPolicyId, DateTimeOffset PricedAt, int TotalPriceCents, CommercialLine[] Items);
public sealed record ApprovedJob(Guid BudgetJobId, Guid DiagnosticLineId);

// CorrelationId identifica a OS. StartDiagnostic recebe o pedido; inÃ­cio fÃ­sico Ã© aÃ§Ã£o HTTP do funcionÃ¡rio.
public sealed record StartDiagnostic(Guid CorrelationId, Guid MessageId, DateTimeOffset OccurredOn, Guid VehicleId,
    int? ServiceOrderCode, RequestedJob[] RequestedJobs, RequestedPart[] RequestedParts);
public sealed record StartExecution(Guid CorrelationId, Guid MessageId, DateTimeOffset OccurredOn,
    Guid BudgetId, int BudgetRevision, DiagnosticSnapshot Snapshot, ApprovedJob[] Jobs);
public sealed record CancelOperation(Guid CorrelationId, Guid MessageId, DateTimeOffset OccurredOn, string Reason);
public sealed record UpdateOperationVehicle(Guid CorrelationId, Guid MessageId, DateTimeOffset OccurredOn, Guid VehicleId);
public sealed record DeactivateOperation(Guid CorrelationId, Guid MessageId, DateTimeOffset OccurredOn, string Reason);

public sealed record DiagnosticQueued(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId);
public sealed record VehicleUpdated(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid VehicleId);
public sealed record DiagnosticStarted(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId);
public sealed record DiagnosticFinished(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, DiagnosticSnapshot Snapshot);
public sealed record DiagnosticFailed(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId, string Code, string Reason);
public sealed record ExecutionAuthorized(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId, Guid BudgetId, int BudgetRevision, Guid AuthorizationMessageId);
public sealed record ExecutionStarted(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId, Guid BudgetId, Guid AuthorizationMessageId);
public sealed record JobExecuted(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId, Guid BudgetId, Guid BudgetJobId, Guid DiagnosticLineId, Guid CatalogJobId, DateTimeOffset ExecutedOn);
public sealed record ExecutionFinished(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, Guid DiagnosticId, Guid BudgetId, Guid AuthorizationMessageId, Guid[] ExecutedBudgetJobIds);
public sealed record OperationDeactivated(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, string Reason);
public sealed record OperationCanceled(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long OperationVersion, string ActorId, string Reason);
// OperationVersion pode ser null quando a rejeiÃ§Ã£o ocorre antes de existir operaÃ§Ã£o.
public sealed record OperationCommandRejected(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long? OperationVersion, string CommandName, string Code);
public sealed record OperationCancellationRejected(Guid CorrelationId, Guid MessageId, Guid CausationId, DateTimeOffset OccurredOn, long? OperationVersion, string Code);
