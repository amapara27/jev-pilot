import Foundation
import Security

public final class KeychainAPIKeyStore: @unchecked Sendable {
  public static let defaultService = "ai.typesafe.jev-pilot"

  private let service: String
  private let account: String

  public init(service: String = defaultService, account: String = "typesafe-api-key") {
    self.service = service
    self.account = account
  }

  public func load() throws -> String? {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
      kSecReturnData as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let data = result as? Data,
      let key = String(data: data, encoding: .utf8)
    else {
      throw KeychainError(status: status)
    }
    return key
  }

  public func save(_ key: String) throws {
    let data = Data(key.utf8)
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
    let attributes = [kSecValueData as String: data]
    let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
    if updateStatus == errSecItemNotFound {
      var addQuery = query
      addQuery[kSecValueData as String] = data
      let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
      guard addStatus == errSecSuccess else { throw KeychainError(status: addStatus) }
    } else if updateStatus != errSecSuccess {
      throw KeychainError(status: updateStatus)
    }
  }

  public func delete() throws {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw KeychainError(status: status)
    }
  }

  public func loadFromKeychainOrEnvironment() throws -> String {
    if let key = try load(), !key.isEmpty { return key }
    if let key = ProcessInfo.processInfo.environment["TYPESAFE_API_KEY"], !key.isEmpty {
      return key
    }
    throw DecisionError.missingAPIKey
  }
}

public struct KeychainError: LocalizedError {
  public let status: OSStatus

  public var errorDescription: String? {
    SecCopyErrorMessageString(status, nil) as String? ?? "Keychain error \(status)"
  }
}
