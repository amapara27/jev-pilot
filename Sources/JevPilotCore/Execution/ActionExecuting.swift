// Declares the boundary that performs approved desktop side effects.
import Foundation

/// Executes one locally approved action and reports its outcome.
@MainActor
public protocol ActionExecuting: AnyObject {
  func execute(_ action: AutomationAction) async -> ExecutionResult
}
