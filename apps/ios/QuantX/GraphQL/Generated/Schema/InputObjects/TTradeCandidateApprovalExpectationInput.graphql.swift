// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct TTradeCandidateApprovalExpectationInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      signalVersion: Int32,
      candidateId: ID,
      candidateFingerprint: String,
      candidateStateVersion: Int32,
      configVersion: Int32,
      policyVersion: String
    ) {
      __data = InputDict([
        "signalVersion": signalVersion,
        "candidateId": candidateId,
        "candidateFingerprint": candidateFingerprint,
        "candidateStateVersion": candidateStateVersion,
        "configVersion": configVersion,
        "policyVersion": policyVersion
      ])
    }

    var signalVersion: Int32 {
      get { __data["signalVersion"] }
      set { __data["signalVersion"] = newValue }
    }

    var candidateId: ID {
      get { __data["candidateId"] }
      set { __data["candidateId"] = newValue }
    }

    var candidateFingerprint: String {
      get { __data["candidateFingerprint"] }
      set { __data["candidateFingerprint"] = newValue }
    }

    var candidateStateVersion: Int32 {
      get { __data["candidateStateVersion"] }
      set { __data["candidateStateVersion"] = newValue }
    }

    var configVersion: Int32 {
      get { __data["configVersion"] }
      set { __data["configVersion"] = newValue }
    }

    var policyVersion: String {
      get { __data["policyVersion"] }
      set { __data["policyVersion"] = newValue }
    }
  }

}
