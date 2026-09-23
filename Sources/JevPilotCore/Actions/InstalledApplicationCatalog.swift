// Discovers locally installed application bundles for closed-set launch candidates.
import AppKit
import Foundation

/// Reads familiar application roots once; generated actions still resolve bundle IDs through NSWorkspace.
@MainActor
enum InstalledApplicationCatalog {
  private static var cached: [ValidActionGenerator.SupportedApplication]?
  private static var loadTask: Task<[ValidActionGenerator.SupportedApplication], Never>?
  static var applications: [ValidActionGenerator.SupportedApplication] { cached ?? [] }

  static func prepare() async {
    if cached != nil { return }
    if loadTask == nil { loadTask = Task.detached(priority: .utility) { scan() } }
    if let loadTask { cached = await loadTask.value }
  }

  nonisolated private static func scan() -> [ValidActionGenerator.SupportedApplication] {
    let roots = ["/Applications", "/System/Applications", NSHomeDirectory() + "/Applications"]
    var names: [String: Set<String>] = [:]
    for root in roots {
      guard let enumerator = FileManager.default.enumerator(at: URL(fileURLWithPath: root), includingPropertiesForKeys: nil, options: [.skipsHiddenFiles, .skipsPackageDescendants]) else { continue }
      for case let url as URL in enumerator {
        guard url.pathExtension.lowercased() == "app" else { continue }
        enumerator.skipDescendants()
        guard let bundle = Bundle(url: url), let identifier = bundle.bundleIdentifier else { continue }
        let name = (bundle.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String)
          ?? (bundle.object(forInfoDictionaryKey: "CFBundleName") as? String)
          ?? url.deletingPathExtension().lastPathComponent
        names[name, default: []].insert(identifier)
      }
    }
    return names.map { .init(name: $0.key, bundleIdentifiers: $0.value.sorted()) }
      .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
  }
}
