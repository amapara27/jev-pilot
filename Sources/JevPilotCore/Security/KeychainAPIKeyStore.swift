// Stores and retrieves the TypeSafe API key using the macOS Keychain.
import Foundation
import Security

/// Provides the app's small, Keychain-backed API-key storage boundary.
public final class KeychainAPIKeyStore: @unchecked Sendable {
  public static let defaultService = "ai.typesafe.jev-pilot"

  private let service: String
  private let account: String

  public init(service: String = defaultService, account: String = "typesafe-api-key") {
    self.service = service
    self.account = account
  }

  /// Checks unencrypted item attributes without requesting the secret value.
  public func containsKey() throws -> Bool {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
      kSecReturnAttributes as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { return false }
    guard status == errSecSuccess else { throw KeychainError(status: status) }
    return true
  }

  /// Loads the stored key, returning nil when no key has been saved.
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

  /// Creates or replaces the stored key.
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

  /// Removes the stored key if it exists.
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

  /// Loads Keychain first, then the development-only environment fallback.
  public func loadFromKeychainOrEnvironment() throws -> String {
    if let key = try load(), !key.isEmpty { return key }
    if let key = ProcessInfo.processInfo.environment["TYPESAFE_API_KEY"], !key.isEmpty {
      return key
    }
    throw DecisionError.missingAPIKey
  }
}

/// Converts an OSStatus from Keychain services into a readable error.
public struct KeychainError: LocalizedError {
  public let status: OSStatus

  public var errorDescription: String? {
    SecCopyErrorMessageString(status, nil) as String? ?? "Keychain error \(status)"
  }
}
