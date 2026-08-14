// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSStrategyInstanceMobileParametersQuery: GraphQLQuery {
    static let operationName: String = "IOSStrategyInstanceMobileParameters"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSStrategyInstanceMobileParameters($instanceId: String!) { strategyInstanceMobileParameters(instanceId: $instanceId) { __typename instanceId configVersion editable parameters { __typename key title description valueType currentValue unit minimum maximum step enumValues applyImmediately riskLevel } } }"#
      ))

    public var instanceId: String

    public init(instanceId: String) {
      self.instanceId = instanceId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["instanceId": instanceId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("strategyInstanceMobileParameters", StrategyInstanceMobileParameters.self, arguments: ["instanceId": .variable("instanceId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSStrategyInstanceMobileParametersQuery.Data.self
      ] }

      var strategyInstanceMobileParameters: StrategyInstanceMobileParameters { __data["strategyInstanceMobileParameters"] }

      /// StrategyInstanceMobileParameters
      nonisolated struct StrategyInstanceMobileParameters: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyInstanceMobileParameters }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("instanceId", String.self),
          .field("configVersion", String.self),
          .field("editable", Bool.self),
          .field("parameters", [Parameter].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSStrategyInstanceMobileParametersQuery.Data.StrategyInstanceMobileParameters.self
        ] }

        var instanceId: String { __data["instanceId"] }
        var configVersion: String { __data["configVersion"] }
        var editable: Bool { __data["editable"] }
        var parameters: [Parameter] { __data["parameters"] }

        /// StrategyInstanceMobileParameters.Parameter
        nonisolated struct Parameter: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyMobileParameter }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("key", String.self),
            .field("title", String.self),
            .field("description", String.self),
            .field("valueType", String.self),
            .field("currentValue", QuantXAPI.JSON.self),
            .field("unit", String?.self),
            .field("minimum", Double?.self),
            .field("maximum", Double?.self),
            .field("step", Double?.self),
            .field("enumValues", [String]?.self),
            .field("applyImmediately", Bool.self),
            .field("riskLevel", String.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSStrategyInstanceMobileParametersQuery.Data.StrategyInstanceMobileParameters.Parameter.self
          ] }

          var key: String { __data["key"] }
          var title: String { __data["title"] }
          var description: String { __data["description"] }
          var valueType: String { __data["valueType"] }
          var currentValue: QuantXAPI.JSON { __data["currentValue"] }
          var unit: String? { __data["unit"] }
          var minimum: Double? { __data["minimum"] }
          var maximum: Double? { __data["maximum"] }
          var step: Double? { __data["step"] }
          var enumValues: [String]? { __data["enumValues"] }
          var applyImmediately: Bool { __data["applyImmediately"] }
          var riskLevel: String { __data["riskLevel"] }
        }
      }
    }
  }

}