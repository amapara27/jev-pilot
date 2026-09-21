// Persists bounded run history off the main actor using atomic, ordered writes.
import Combine
import Foundation

/// Owns disk serialization; revisions prevent an older checkpoint replacing a newer one.
public actor RunArchive {
  private struct Envelope: Codable { var version = 1; var runs: [RunRecord] }
  private let url: URL
  private var revision = 0
  public init(url: URL) { self.url = url }
  public func load() throws -> [RunRecord] {
    guard FileManager.default.fileExists(atPath: url.path) else { return [] }
    let envelope = try JSONDecoder().decode(Envelope.self, from: Data(contentsOf: url))
    guard envelope.version == 1 else { throw CocoaError(.fileReadCorruptFile) }
    return Array(envelope.runs.prefix(100)).map { record in
      var record = record
      if record.outcome == nil {
        record.outcome = .interrupted
        record.endedAt = record.events.last?.timestamp ?? record.startedAt
        record.events.append(.init(kind: .status, title: "Interrupted", detail: "The app closed before this run finished."))
      }
      return record
    }
  }
  public func save(_ runs: [RunRecord], revision: Int) throws {
    guard revision > self.revision else { return }
    let data = try JSONEncoder().encode(Envelope(runs: Array(runs.prefix(100))))
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try data.write(to: url, options: .atomic)
    self.revision = revision
  }
}

/// Publishes history immediately while sending serialized checkpoints to a disk actor.
@MainActor
public final class RunStore: ObservableObject {
  @Published public private(set) var records: [RunRecord] = []
  @Published public private(set) var isLoaded = false
  @Published public private(set) var errorMessage: String?
  private let archive: RunArchive?
  private var revision = 0
  private var writeTask: Task<Void, Never>?
  public init(url: URL? = nil, inMemory: Bool = false) {
    if inMemory {
      archive = nil
      isLoaded = true
    } else {
      let location = url ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Jev Pilot/runs.json")
      archive = RunArchive(url: location)
    }
  }
  public func load() async {
    guard !isLoaded else { return }
    do {
      records = try await archive?.load() ?? []
      isLoaded = true
      checkpoint()
    } catch {
      // Keep an unreadable archive untouched until the user explicitly clears it.
      errorMessage = "Run history could not be read. Clear history in Settings to reset it."
      isLoaded = true
    }
  }
  public func upsert(_ record: RunRecord) {
    if let index = records.firstIndex(where: { $0.id == record.id }) { records[index] = record }
    else { records.insert(record, at: 0) }
    records = Array(records.sorted { $0.startedAt > $1.startedAt }.prefix(100))
    checkpoint()
  }
  public func appendMetric(_ metric: RequestMetric, to id: UUID) {
    guard let index = records.firstIndex(where: { $0.id == id }) else { return }
    if let metricIndex = records[index].requests.firstIndex(where: { $0.id == metric.id }) {
      if !records[index].requests[metricIndex].isComplete { records[index].requests[metricIndex] = metric }
    } else { records[index].requests.append(metric) }
    checkpoint()
  }
  public func delete(_ id: UUID) {
    records.removeAll { $0.id == id && $0.outcome != nil }
    checkpoint()
  }
  public func clear() {
    records.removeAll { $0.outcome != nil }
    errorMessage = nil
    checkpoint()
  }
  public func flush() async { await writeTask?.value }
  private func checkpoint() {
    guard let archive, errorMessage == nil else { return }
    revision += 1
    let revision = revision
    let snapshot = records
    let previous = writeTask
    writeTask = Task {
      await previous?.value
      do { try await archive.save(snapshot, revision: revision) }
      catch { errorMessage = "History could not be saved. Check available disk space and folder permissions." }
    }
  }
}
