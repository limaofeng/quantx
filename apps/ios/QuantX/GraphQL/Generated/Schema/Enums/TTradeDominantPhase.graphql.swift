// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) import ApolloAPI

extension QuantXAPI {
  nonisolated enum TTradeDominantPhase: String, EnumType {
    case none = "NONE"
    case pullbackObserving = "PULLBACK_OBSERVING"
    case pullbackForming = "PULLBACK_FORMING"
    case pullbackLowStabilizing = "PULLBACK_LOW_STABILIZING"
    case pullbackReboundConfirming = "PULLBACK_REBOUND_CONFIRMING"
    case pullbackCandidateLatched = "PULLBACK_CANDIDATE_LATCHED"
    case pullbackSuppressed = "PULLBACK_SUPPRESSED"
    case momentumObserving = "MOMENTUM_OBSERVING"
    case momentumBaselining = "MOMENTUM_BASELINING"
    case momentumBuilding = "MOMENTUM_BUILDING"
    case momentumAccelerating = "MOMENTUM_ACCELERATING"
    case momentumOverextended = "MOMENTUM_OVEREXTENDED"
    case momentumCandidateLatched = "MOMENTUM_CANDIDATE_LATCHED"
    case momentumSuppressed = "MOMENTUM_SUPPRESSED"
  }

}