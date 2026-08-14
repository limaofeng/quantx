// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct StrategyInstanceParameterUpdateInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      parameters: JSON,
      applyImmediately: Bool? = nil,
      expectedVersion: GraphQLNullable<String> = nil
    ) {
      __data = InputDict([
        "parameters": parameters,
        "applyImmediately": applyImmediately ?? GraphQLNullable.none,
        "expectedVersion": expectedVersion
      ])
    }

    var parameters: JSON {
      get { __data["parameters"] }
      set { __data["parameters"] = newValue }
    }

    var applyImmediately: Bool? {
      get { __data["applyImmediately"] }
      set { __data["applyImmediately"] = newValue }
    }

    var expectedVersion: GraphQLNullable<String> {
      get { __data["expectedVersion"] }
      set { __data["expectedVersion"] = newValue }
    }
  }

}