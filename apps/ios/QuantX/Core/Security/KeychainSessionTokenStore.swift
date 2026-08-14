import Foundation
import Security

actor KeychainSessionTokenStore: SessionTokenStore {
  enum KeychainError: LocalizedError {
    case unexpectedStatus(OSStatus)
    case invalidPayload

    var errorDescription: String? {
      switch self {
      case .unexpectedStatus(let status): "Keychain 操作失败（\(status)）"
      case .invalidPayload: "Keychain 中的会话数据无效"
      }
    }
  }

  private let service: String
  private let account = "primary-session"
  private let encoder = JSONEncoder()
  private let decoder = JSONDecoder()

  init(service: String = Bundle.main.bundleIdentifier ?? "com.limaofeng.quantx") {
    self.service = service
  }

  func load() async throws -> SessionTokens? {
    var query = baseQuery
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne

    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound {
      return nil
    }
    guard status == errSecSuccess else {
      throw KeychainError.unexpectedStatus(status)
    }
    guard let data = result as? Data else {
      throw KeychainError.invalidPayload
    }
    do {
      return try decoder.decode(SessionTokens.self, from: data)
    } catch {
      throw KeychainError.invalidPayload
    }
  }

  func save(_ tokens: SessionTokens) async throws {
    let data = try encoder.encode(tokens)
    let attributes: [String: Any] = [
      kSecValueData as String: data,
      kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
    ]
    let updateStatus = SecItemUpdate(baseQuery as CFDictionary, attributes as CFDictionary)

    if updateStatus == errSecSuccess {
      return
    }
    guard updateStatus == errSecItemNotFound else {
      throw KeychainError.unexpectedStatus(updateStatus)
    }

    var insert = baseQuery
    insert.merge(attributes) { _, new in new }
    let insertStatus = SecItemAdd(insert as CFDictionary, nil)
    guard insertStatus == errSecSuccess else {
      throw KeychainError.unexpectedStatus(insertStatus)
    }
  }

  func delete() async throws {
    let status = SecItemDelete(baseQuery as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw KeychainError.unexpectedStatus(status)
    }
  }

  private var baseQuery: [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }
}
