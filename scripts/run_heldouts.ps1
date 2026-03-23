param(
    [string]$PytestArgs = ""
)

$targets = @(
    "tests/test_heldouts",
    "tests/test_budget/test_cost_guard.py",
    "tests/test_resilience/test_recovery.py",
    "tests/test_resilience/test_state_reset.py"
)

$joinedTargets = $targets -join " "
$command = "pytest $joinedTargets $PytestArgs"

Write-Host "Running held-out related checks..."
Write-Host $command
Invoke-Expression $command
"""
This script is designed to run a specific set of pytest tests that are related to held-out scenarios
and resilience checks for the agent. By defining a list of target test files and directories, the script constructs a pytest command that can be executed to run all the specified tests at once. The optional $PytestArgs parameter allows for additional arguments to be passed to pytest, such as verbosity or specific test markers. This centralized approach to running held-out related tests helps ensure that all relevant checks are performed consistently and efficiently, making it easier to validate the agent's performance under various conditions and constraints.
"""
