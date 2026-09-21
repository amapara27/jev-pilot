// Loads every persistent store before the application exposes interactive controls.
import Combine
import Foundation

/// A small boundary that lets startup use real stores in production and controlled fakes in tests.
public typealias StorageLoader = @MainActor @Sendable () async -> Void

/// Publishes one readiness state after all registered storage loaders finish in parallel.
@MainActor
public final class StorageStartupCoordinator: ObservableObject {
  public enum Phase: Equatable, Sendable {
    case loading
    case ready
  }

  @Published public private(set) var phase: Phase = .loading
  public var isReady: Bool { phase == .ready }

  private let loaders: [StorageLoader]
  private var loadTask: Task<Void, Never>?

  public init(loaders: [StorageLoader]) {
    self.loaders = loaders
  }

  /// Coalesces repeated callers and reveals the application only after every store has loaded.
  public func load() async {
    guard phase == .loading else { return }
    if let loadTask {
      await loadTask.value
      return
    }

    let loaders = loaders
    let task = Task { @MainActor in
      await withTaskGroup(of: Void.self) { group in
        for loader in loaders {
          group.addTask { await loader() }
        }
        await group.waitForAll()
      }
    }
    loadTask = task
    await task.value
    phase = .ready
    loadTask = nil
  }
}
