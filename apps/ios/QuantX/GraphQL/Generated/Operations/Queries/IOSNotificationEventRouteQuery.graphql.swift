// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSNotificationEventRouteQuery: GraphQLQuery {
    static let operationName: String = "IOSNotificationEventRoute"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSNotificationEventRoute($eventId: ID!) { notificationEventRoute(eventId: $eventId) { __typename eventId category routeType occurredAt expiresAt expired } }"#
      ))

    public var eventId: ID

    public init(eventId: ID) {
      self.eventId = eventId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["eventId": eventId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("notificationEventRoute", NotificationEventRoute?.self, arguments: ["eventId": .variable("eventId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSNotificationEventRouteQuery.Data.self
      ] }

      var notificationEventRoute: NotificationEventRoute? { __data["notificationEventRoute"] }

      /// NotificationEventRoute
      nonisolated struct NotificationEventRoute: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.NotificationEventRoute }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("eventId", QuantXAPI.ID.self),
          .field("category", GraphQLEnum<QuantXAPI.PushCategory>.self),
          .field("routeType", GraphQLEnum<QuantXAPI.NotificationRouteType>.self),
          .field("occurredAt", QuantXAPI.DateTime.self),
          .field("expiresAt", QuantXAPI.DateTime.self),
          .field("expired", Bool.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSNotificationEventRouteQuery.Data.NotificationEventRoute.self
        ] }

        var eventId: QuantXAPI.ID { __data["eventId"] }
        var category: GraphQLEnum<QuantXAPI.PushCategory> { __data["category"] }
        var routeType: GraphQLEnum<QuantXAPI.NotificationRouteType> { __data["routeType"] }
        var occurredAt: QuantXAPI.DateTime { __data["occurredAt"] }
        var expiresAt: QuantXAPI.DateTime { __data["expiresAt"] }
        var expired: Bool { __data["expired"] }
      }
    }
  }

}