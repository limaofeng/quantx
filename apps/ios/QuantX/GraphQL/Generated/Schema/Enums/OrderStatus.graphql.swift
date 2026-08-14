// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) import ApolloAPI

extension QuantXAPI {
  nonisolated enum OrderStatus: String, EnumType {
    case unreported = "UNREPORTED"
    case waitReporting = "WAIT_REPORTING"
    case reported = "REPORTED"
    case reportedCancel = "REPORTED_CANCEL"
    case partsuccCancel = "PARTSUCC_CANCEL"
    case partCancel = "PART_CANCEL"
    case canceled = "CANCELED"
    case partSucc = "PART_SUCC"
    case succeeded = "SUCCEEDED"
    case junk = "JUNK"
    case unknown = "UNKNOWN"
  }

}