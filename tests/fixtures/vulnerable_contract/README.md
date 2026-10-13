# Test Fixture: ReentrancyVault

This fixture contains a deterministic, self-contained Solidity contract designed to validate the Slither static analysis pipeline.

## Contract Details
- **File:** `contracts/ReentrancyVault.sol`
- **Compiler Pragma:** `pragma solidity 0.8.20;`
- **Known Flaw:** Reentrancy (`reentrancy-eth`)
- **Severity:** High / Impact: High, Confidence: Medium/High

## Explanation of Detection
The contract function `withdraw(uint256)` performs an external ETH transfer (`msg.sender.call{value: amount}("")`) before updating the internal balance mapping (`balances[msg.sender] -= amount`). This violates the Checks-Effects-Interactions (CEI) design pattern.

When Slither analyzes this contract, its data-flow and CFG (Control Flow Graph) analysis detects that:
1. `balances[msg.sender]` is read and checked in the `require`.
2. An external call transfers control to `msg.sender`.
3. State variable `balances` is written afterwards.
4. An attacker receiving execution during step 2 can re-enter `withdraw` with the pre-update balance, draining contract funds.

## Expected Finding
- **Check / Rule:** `reentrancy-eth`
- **Impact:** High
- **Confidence:** Medium or High
- **Description:** `Reentrancy in ReentrancyVault.withdraw(uint256) (contracts/ReentrancyVault.sol#...)`
