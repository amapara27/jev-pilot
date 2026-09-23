// Verifies explicit dotenv loading and key-source precedence without Keychain prompts.
import Foundation
import XCTest
@testable import JevPilotCore

final class DevelopmentAPIKeyTests: XCTestCase {
  func testExplicitDotenvFileWinsWithoutConsultingKeychain() throws {
    let file = FileManager.default.temporaryDirectory.appendingPathComponent("jev-key-\(UUID().uuidString).env")
    try "# Local test only\nTYPESAFE_API_KEY=\"dev-test-key\"\n".write(to: file, atomically: true, encoding: .utf8)
    defer { try? FileManager.default.removeItem(at: file) }

    let developmentKey = try DevelopmentAPIKey.loadOverride(
      arguments: ["JevPilot", DevelopmentAPIKey.fileArgument, file.path],
      environment: ["TYPESAFE_API_KEY": "environment-key"]
    )
    var keychainCalls = 0
    let selected = try DevelopmentAPIKey.resolve(
      developmentOverride: { developmentKey },
      keychain: { keychainCalls += 1; return "stored-key" }
    )
    XCTAssertEqual(selected, "dev-test-key")
    XCTAssertEqual(keychainCalls, 0)
  }

  func testOrdinaryLaunchUsesKeychainAndEnvironmentOverridePrecedesIt() throws {
    XCTAssertNil(try DevelopmentAPIKey.loadOverride(arguments: ["JevPilot"], environment: [:]))
    XCTAssertEqual(
      try DevelopmentAPIKey.resolve(
        developmentOverride: { nil }, keychain: { "stored-key" }
      ), "stored-key")

    var keychainCalls = 0
    let environmentKey = try DevelopmentAPIKey.loadOverride(
      arguments: ["JevPilot"], environment: ["TYPESAFE_API_KEY": "environment-key"]
    )
    XCTAssertEqual(
      try DevelopmentAPIKey.resolve(
        developmentOverride: { environmentKey },
        keychain: { keychainCalls += 1; return "stored-key" }
      ), "environment-key")
    XCTAssertEqual(keychainCalls, 0)
  }

  func testInvalidExplicitFileDoesNotFallBackToAnotherKeySource() throws {
    let file = FileManager.default.temporaryDirectory.appendingPathComponent("jev-key-\(UUID().uuidString).env")
    try "OTHER_VALUE=example\n".write(to: file, atomically: true, encoding: .utf8)
    defer { try? FileManager.default.removeItem(at: file) }

    XCTAssertThrowsError(try DevelopmentAPIKey.loadOverride(
      arguments: ["JevPilot", DevelopmentAPIKey.fileArgument, file.path],
      environment: ["TYPESAFE_API_KEY": "environment-key"]
    )) { error in
      XCTAssertEqual(error as? DevelopmentAPIKeyError, .missingKey)
    }
  }
}
