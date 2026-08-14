@_spi(Internal) @_spi(Execution) import ApolloAPI
import Foundation

/// A lossless, recursively typed value for the public GraphQL `JSON` scalar.
///
/// QuantX never sends JSON as a pre-encoded string. Keeping the scalar typed is
/// important for public contract fields that contain nested objects or arrays.
/// Feature mappers still decide which shapes are legal for their own domain;
/// for example, mobile strategy parameters reject collection-valued entries.
indirect enum GraphQLJSON: Hashable, Sendable, CustomScalarType {
  struct Field: Hashable, Sendable {
    let key: String
    let value: GraphQLJSON
  }

  case null
  case boolean(Bool)
  case integer(Int)
  case number(Double)
  case string(String)
  case array([GraphQLJSON])
  case object([Field])

  init(object values: [String: GraphQLJSON]) {
    self = .object(
      values
        .map { Field(key: $0.key, value: $0.value) }
        .sorted { $0.key < $1.key }
    )
  }

  init(_jsonValue value: JSONValue) throws {
    switch value {
    case is NSNull:
      self = .null
    case let value as Bool:
      self = .boolean(value)
    case let value as Int:
      self = .integer(value)
    case let value as Int32:
      self = .integer(Int(value))
    case let value as Int64:
      guard let converted = Int(exactly: value) else {
        throw JSONDecodingError.couldNotConvert(value: value, to: Int.self)
      }
      self = .integer(converted)
    case let value as Double:
      guard value.isFinite else {
        throw JSONDecodingError.couldNotConvert(value: value, to: Double.self)
      }
      self = .number(value)
    case let value as Float:
      let converted = Double(value)
      guard converted.isFinite else {
        throw JSONDecodingError.couldNotConvert(value: value, to: Double.self)
      }
      self = .number(converted)
    case let value as String:
      self = .string(value)
    case let values as [JSONValue]:
      self = .array(try values.map(GraphQLJSON.init(_jsonValue:)))
    case let values as JSONObject:
      self = .object(
        try values
          .map { Field(key: $0.key, value: try GraphQLJSON(_jsonValue: $0.value)) }
          .sorted { $0.key < $1.key }
      )
    default:
      throw JSONDecodingError.couldNotConvert(value: value, to: GraphQLJSON.self)
    }
  }

  var _jsonValue: JSONValue {
    switch self {
    case .null:
      NSNull()
    case .boolean(let value):
      value
    case .integer(let value):
      value
    case .number(let value):
      value
    case .string(let value):
      value
    case .array(let values):
      values._jsonValue
    case .object(let fields):
      Dictionary(
        uniqueKeysWithValues: fields.map {
          ($0.key, $0.value as any JSONEncodable)
        }
      )._jsonValue
    }
  }
}
