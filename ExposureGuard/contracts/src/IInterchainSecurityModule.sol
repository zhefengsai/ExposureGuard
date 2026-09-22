// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

/// @dev Minimal restatement of Hyperlane's ISM interface so this prototype
///      builds standalone. Types mirror the upstream enum.
interface IInterchainSecurityModule {
    enum Types {
        UNUSED, ROUTING, AGGREGATION, LEGACY_MULTISIG, MERKLE_ROOT_MULTISIG,
        MESSAGE_ID_MULTISIG, NULL, CCIP_READ
    }

    function moduleType() external view returns (uint8);

    function verify(bytes calldata metadata, bytes calldata message)
        external
        returns (bool);
}
