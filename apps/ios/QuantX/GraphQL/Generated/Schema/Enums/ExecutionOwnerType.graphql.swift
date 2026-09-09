// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) import ApolloAPI

extension QuantXAPI {
  nonisolated enum ExecutionOwnerType: String, EnumType {
    case strategyRun = "STRATEGY_RUN"
    case tAssistantExecution = "T_ASSISTANT_EXECUTION"
    case entryPlan = "ENTRY_PLAN"
    case boardAssistantExecution = "BOARD_ASSISTANT_EXECUTION"
    case exitPlan = "EXIT_PLAN"
    case manualCommand = "MANUAL_COMMAND"
  }

}