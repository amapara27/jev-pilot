// swift-tools-version: 6.0
// Declares the Swift package targets and required macOS frameworks.

import PackageDescription

/// Configures the reusable core, app, and offline pipeline/trust-boundary checks.
let package = Package(
  name: "JevPilot",
  platforms: [.macOS(.v14)],
  products: [
    .library(name: "JevPilotCore", targets: ["JevPilotCore"]),
    .executable(name: "JevPilot", targets: ["JevPilotApp"]),
  ],
  dependencies: [
    .package(
      url: "https://github.com/FluidInference/FluidAudio.git",
      exact: "0.15.5"
    ),
  ],
  targets: [
    .target(
      name: "JevPilotCore",
      dependencies: [
        .product(name: "FluidAudio", package: "FluidAudio"),
      ],
      linkerSettings: [
        .linkedFramework("ApplicationServices"),
        .linkedFramework("AppKit"),
        .linkedFramework("AVFoundation"),
        .linkedFramework("Security"),
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
