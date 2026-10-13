// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/**
 * @title ReentrancyVault
 * @notice Controlled deterministic test contract containing an intentional reentrancy vulnerability.
 * @dev Used for deterministic testing and validation of the Slither static analysis runner.
 *
 * Vulnerability Details:
 * - Detector: reentrancy-eth
 * - Severity: High
 * - Check: Checks-Effects-Interactions pattern violation in `withdraw(uint256)`.
 * - State modification `balances[msg.sender] -= amount` occurs after the external call `msg.sender.call{value: amount}("")`.
 */
contract ReentrancyVault {
    mapping(address => uint256) public balances;

    event Deposited(address indexed sender, uint256 amount);
    event Withdrawn(address indexed to, uint256 amount);

    function deposit() external payable {
        require(msg.value > 0, "Zero deposit");
        balances[msg.sender] += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    // Vulnerable function: violates Checks-Effects-Interactions
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "Insufficient balance");

        // External call executed BEFORE balance update
        (bool success, ) = msg.sender.call{value: amount}("");
        require(success, "ETH transfer failed");

        // State update executed after external interaction
        balances[msg.sender] -= amount;
        emit Withdrawn(msg.sender, amount);
    }

    function getBalance(address user) external view returns (uint256) {
        return balances[user];
    }
}
