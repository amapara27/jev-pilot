// swift-tools-version: 6.0

import PackageDescription

let package = Package(
  name: "JevPilot",
  platforms: [.macOS(.v14)],
  products: [
    .library(name: "JevPilotCore", targets: ["JevPilotCore"]),
    .executable(name: "JevPilot", targets: ["JevPilotApp"]),
  ],
  targets: [
    .target(
      name: "JevPilotCore",
      linkerSettings: [
        .linkedFramework("ApplicationServices"),
        .linkedFramework("AppKit"),
        .linkedFramework("AVFoundation"),
        .linkedFramework("Security"),
        .linkedFramework("Speech"),
      ]
    ),
    .executableTarget(
      name: "JevPilotApp",
      dependencies: ["JevPilotCore"]
    ),
    .testTarget(
      name: "JevPilotCoreTests",
      dependencies: ["JevPilotCore"]
    ),
  ]
)
