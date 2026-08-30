// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) import ApolloAPI

extension QuantXAPI {
  nonisolated enum TTradeSignalDataHealth: String, EnumType {
    case warming = "WARMING"
    case ready = "READY"
    case degraded = "DEGRADED"
    case stale = "STALE"
    case continuityLost = "CONTINUITY_LOST"
    case insufficient = "INSUFFICIENT"
  }

}
