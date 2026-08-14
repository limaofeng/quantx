import Foundation
import Security

protocol DeviceInstallIDStoring: Sendable {
  func loadOrCreate() async throws -> UUID
}

actor KeychainDeviceInstallIDStore: DeviceInstallIDStoring {
  enum StoreError: LocalizedError {
    case unexpectedStatus(OSStatus)
    case invalidPayload

    var errorDescription: String? {
      switch self {
      case .unexpectedStatus(let status):
        "无法读取本设备的通知安装标识（\(status)）"
      case .invalidPayload:
        "本设备的通知安装标识无效"
      }
    }
  }

  private let service: String
  private let account = "push-device-installation-id"

  init(service: String = Bundle.main.bundleIdentifier ?? "com.limaofeng.quantx") {
    self.service = "\(service).device-installation"
  }

  func loadOrCreate() async throws -> UUID {
    if let existing = try load() {
      return existing
    }
    let generated = UUID()
    let data = Data(generated.uuidString.lowercased().utf8)
    var insert = baseQuery
    insert[kSecValueData as String] = data
    insert[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
    let status = SecItemAdd(insert as CFDictionary, nil)
    if status == errSecDuplicateItem, let existing = try load() {
      return existing
    }
    guard status == errSecSuccess else {
      throw StoreError.unexpectedStatus(status)
    }
    return generated
  }

  private func load() throws -> UUID? {
    var query = baseQuery
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound {
      return nil
    }
    guard status == errSecSuccess else {
      throw StoreError.unexpectedStatus(status)
    }
    guard
      let data = result as? Data,
      let value = String(data: data, encoding: .utf8),
      let identifier = UUID(uuidString: value)
    else {
      throw StoreError.invalidPayload
    }
    return identifier
  }

  private var baseQuery: [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }
}
