import Foundation

struct HealthSnapshot: Decodable, Equatable, Sendable {
  struct ComponentStatus: Decodable, Equatable, Sendable {
    let status: String
    let connectedDevices: Int?
    let onlineDevices: Int?
    let onlineWorkers: Int?

    enum CodingKeys: String, CodingKey {
      case status
      case connectedDevices
      case onlineDevices
      case onlineWorkers
    }

    var isReady: Bool {
      ["healthy", "ready"].contains(status.lowercased())
    }
  }

  struct MiniQMTStatus: Decodable, Equatable, Sendable {
    let available: Bool
    let connected: Bool
    let connectionState: String
    let accountConnected: Bool

    enum CodingKeys: String, CodingKey {
      case available
      case connected
      case connectionState = "connection_state"
      case accountConnected = "account_connected"
    }
  }

  let status: String
  let version: String?
  let apiType: String?
  let environment: String?
  let realtimeEnabled: Bool?
  let miniQMT: MiniQMTStatus?
  let profile: String?
  let requiredComponents: [String]
  let components: [String: ComponentStatus]
  let fetchedAt: Date

  var isReady: Bool {
    ["healthy", "ready"].contains(status.lowercased())
  }

  enum CodingKeys: String, CodingKey {
    case status
    case version
    case apiType = "api_type"
    case environment
    case realtimeEnabled = "realtime_enabled"
    case miniQMT = "miniqmt"
    case profile
    case requiredComponents
    case components
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    status = try container.decode(String.self, forKey: .status)
    version = try container.decodeIfPresent(String.self, forKey: .version)
    apiType = try container.decodeIfPresent(String.self, forKey: .apiType)
    environment = try container.decodeIfPresent(String.self, forKey: .environment)
    realtimeEnabled = try container.decodeIfPresent(Bool.self, forKey: .realtimeEnabled)
    miniQMT = try container.decodeIfPresent(MiniQMTStatus.self, forKey: .miniQMT)
    profile = try container.decodeIfPresent(String.self, forKey: .profile)
    requiredComponents =
      try container.decodeIfPresent([String].self, forKey: .requiredComponents) ?? []
    components =
      try container.decodeIfPresent([String: ComponentStatus].self, forKey: .components) ?? [:]
    fetchedAt = Date()
  }
}
