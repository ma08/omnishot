// swift-tools-version: 6.2

import PackageDescription

let package = Package(
    name: "DescribeImage",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(
            name: "DescribeImage",
            path: "Sources"
        ),
    ]
)
