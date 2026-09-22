// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

/// Read-only-to-the-guard Mailbox fixture used only by the persistent local
/// operational devnet. It is not a Hyperlane testnet deployment.
contract DevnetMailbox {
    mapping(bytes32 => bool) public delivered;
    address public defaultIsm;

    function markDelivered(bytes32 id) external {
        delivered[id] = true;
    }

    function setDefaultIsm(address ism) external {
        defaultIsm = ism;
    }
}
