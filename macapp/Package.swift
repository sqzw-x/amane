// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Amane",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "AmaneShared"),
        .executableTarget(name: "Amane", dependencies: ["AmaneShared"]),
        .executableTarget(name: "AmaneUI", dependencies: ["AmaneShared"]),
    ]
)
