// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct PushCategoryPreferenceInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      category: GraphQLEnum<PushCategory>,
      enabled: Bool
    ) {
      __data = InputDict([
        "category": category,
        "enabled": enabled
      ])
    }

    var category: GraphQLEnum<PushCategory> {
      get { __data["category"] }
      set { __data["category"] = newValue }
    }

    var enabled: Bool {
      get { __data["enabled"] }
      set { __data["enabled"] = newValue }
    }
  }

}