import Foundation

@MainActor
public protocol ActionExecuting: AnyObject {
  func execute(_ action: AutomationAction) async -> ExecutionResult
}
