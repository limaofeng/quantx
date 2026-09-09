// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) import ApolloAPI

extension QuantXAPI {
  nonisolated enum AccountExecutionControlAction: String, EnumType {
    case beginControlledWindow = "BEGIN_CONTROLLED_WINDOW"
    case enableRiskIncrease = "ENABLE_RISK_INCREASE"
    case pauseRiskIncrease = "PAUSE_RISK_INCREASE"
    case killSwitch = "KILL_SWITCH"
    case clearKillSwitch = "CLEAR_KILL_SWITCH"
    case repairQuarantinedOrder = "REPAIR_QUARANTINED_ORDER"
  }

}