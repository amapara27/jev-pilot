// Stores and retrieves the TypeSafe API key using the macOS Keychain.
import Foundation
import Security

/// Reads a development key only when the app was explicitly launched with a file path.
public enum DevelopmentAPIKey {
  public static let fileArgument = "--jev-dev-env-file"

  public static func loadOverride(
    arguments: [String] = ProcessInfo.processInfo.arguments,
    environment: [String: String] = ProcessInfo.processInfo.environment
  ) throws -> String? {
    if let index = arguments.firstIndex(of: fileArgument) {
      guard arguments.indices.contains(index + 1), !arguments[index + 1].isEmpty else {
        throw DevelopmentAPIKeyError.missingFilePath
      }
      return try loadFile(at: arguments[index + 1])
    }
    if let key = environment["TYPESAFE_API_KEY"], !key.isEmpty { return key }
    return nil
  }

  /// Parse a single dotenv assignment without evaluating the file as shell code.
  private static func loadFile(at path: String) throws -> String {
    let contents = try String(contentsOfFile: path, encoding: .utf8)
    for line in contents.split(whereSeparator: \.isNewline) {
      let entry = line.trimmingCharacters(in: .whitespaces)
      guard !entry.isEmpty, !entry.hasPrefix("#"), let separator = entry.firstIndex(of: "=") else { continue }
      let name = entry[..<separator].trimmingCharacters(in: .whitespaces)
      guard name == "TYPESAFE_API_KEY" || name == "export TYPESAFE_API_KEY" else { continue }
      var value = entry[entry.index(after: separator)...].trimmingCharacters(in: .whitespaces)
      if value.count >= 2,
        (value.hasPrefix("\"") && value.hasSuffix("\"")) || (value.hasPrefix("'") && value.hasSuffix("'")) {
        value.removeFirst()
        value.removeLast()
      }
      guard !value.isEmpty else { throw DevelopmentAPIKeyError.missingKey }
      return value
    }
    throw DevelopmentAPIKeyError.missingKey
  }

  /// Keep the keychain closure lazy so a development override never touches Keychain.
  static func resolve(
    developmentOverride: () throws -> String?,
    keychain: () throws -> String?
  ) throws -> String {
    if let key = try developmentOverride(), !key.isEmpty { return key }
    if let key = try keychain(), !key.isEmpty { return key }
    throw DecisionError.missingAPIKey
  }
}

/// Gives an actionable, non-secret error for an explicitly selected development file.
public enum DevelopmentAPIKeyError: LocalizedError, Equatable {
  case missingFilePath, missingKey

  public var errorDescription: String? {
    switch self {
    case .missingFilePath: "The development API key file path is missing."
    case .missingKey: "The development .env file has no TYPESAFE_API_KEY value."
    }
  }
}

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

  /// Explicit development overrides bypass Keychain; normal launches use the saved key.
  public func loadFromDevelopmentOverrideOrKeychain() throws -> String {
    try DevelopmentAPIKey.resolve(
      developmentOverride: { try DevelopmentAPIKey.loadOverride() },
      keychain: { try load() }
    )
  }
}

/// Converts an OSStatus from Keychain services into a readable error.
public struct KeychainError: LocalizedError {
  public let status: OSStatus

  public var errorDescription: String? {
    SecCopyErrorMessageString(status, nil) as String? ?? "Keychain error \(status)"
  }
}
