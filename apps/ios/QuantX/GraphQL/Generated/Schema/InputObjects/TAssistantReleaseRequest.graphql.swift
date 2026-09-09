// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct TAssistantReleaseRequest: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      sourceExecutionId: String,
      configVersionId: String,
      expectedConfigHash: String,
      expectedHeadVersion: Int32,
      evaluationId: String,
      expectedReportHash: String,
      expectedPolicyHash: String,
      windowStart: DateTime,
      windowEnd: DateTime
    ) {
      __data = InputDict([
        "accountId": accountId,
        "sourceExecutionId": sourceExecutionId,
        "configVersionId": configVersionId,
        "expectedConfigHash": expectedConfigHash,
        "expectedHeadVersion": expectedHeadVersion,
        "evaluationId": evaluationId,
        "expectedReportHash": expectedReportHash,
        "expectedPolicyHash": expectedPolicyHash,
        "windowStart": windowStart,
        "windowEnd": windowEnd
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var sourceExecutionId: String {
      get { __data["sourceExecutionId"] }
      set { __data["sourceExecutionId"] = newValue }
    }

    var configVersionId: String {
      get { __data["configVersionId"] }
      set { __data["configVersionId"] = newValue }
    }

    var expectedConfigHash: String {
      get { __data["expectedConfigHash"] }
      set { __data["expectedConfigHash"] = newValue }
    }

    var expectedHeadVersion: Int32 {
      get { __data["expectedHeadVersion"] }
      set { __data["expectedHeadVersion"] = newValue }
    }

    var evaluationId: String {
      get { __data["evaluationId"] }
      set { __data["evaluationId"] = newValue }
    }

    var expectedReportHash: String {
      get { __data["expectedReportHash"] }
      set { __data["expectedReportHash"] = newValue }
    }

    var expectedPolicyHash: String {
      get { __data["expectedPolicyHash"] }
      set { __data["expectedPolicyHash"] = newValue }
    }

    var windowStart: DateTime {
      get { __data["windowStart"] }
      set { __data["windowStart"] = newValue }
    }

    var windowEnd: DateTime {
      get { __data["windowEnd"] }
      set { __data["windowEnd"] = newValue }
    }
  }

}