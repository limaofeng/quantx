// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewTAssistantLiveReleaseMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewTAssistantLiveRelease"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewTAssistantLiveRelease($request: TAssistantReleaseRequest!) { previewTAssistantLiveRelease(request: $request) { __typename success code message preview { __typename challengeId confirmationToken expiresAt accountId sourceExecutionId configVersionId configSnapshotHash reportHash policyHash windowStart windowEnd } } }"#
      ))

    public var request: TAssistantReleaseRequest

    public init(request: TAssistantReleaseRequest) {
      self.request = request
    }

    @_spi(Unsafe) public var __variables: Variables? { ["request": request] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewTAssistantLiveRelease", PreviewTAssistantLiveRelease.self, arguments: ["request": .variable("request")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewTAssistantLiveReleaseMutation.Data.self
      ] }

      var previewTAssistantLiveRelease: PreviewTAssistantLiveRelease { __data["previewTAssistantLiveRelease"] }

      /// PreviewTAssistantLiveRelease
      nonisolated struct PreviewTAssistantLiveRelease: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantReleaseResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewTAssistantLiveReleaseMutation.Data.PreviewTAssistantLiveRelease.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewTAssistantLiveRelease.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantReleasePreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("expiresAt", QuantXAPI.DateTime.self),
            .field("accountId", String.self),
            .field("sourceExecutionId", String.self),
            .field("configVersionId", String.self),
            .field("configSnapshotHash", String.self),
            .field("reportHash", String.self),
            .field("policyHash", String.self),
            .field("windowStart", QuantXAPI.DateTime.self),
            .field("windowEnd", QuantXAPI.DateTime.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewTAssistantLiveReleaseMutation.Data.PreviewTAssistantLiveRelease.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var expiresAt: QuantXAPI.DateTime { __data["expiresAt"] }
          var accountId: String { __data["accountId"] }
          var sourceExecutionId: String { __data["sourceExecutionId"] }
          var configVersionId: String { __data["configVersionId"] }
          var configSnapshotHash: String { __data["configSnapshotHash"] }
          var reportHash: String { __data["reportHash"] }
          var policyHash: String { __data["policyHash"] }
          var windowStart: QuantXAPI.DateTime { __data["windowStart"] }
          var windowEnd: QuantXAPI.DateTime { __data["windowEnd"] }
        }
      }
    }
  }

}