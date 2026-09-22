// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {IInterchainSecurityModule} from "./IInterchainSecurityModule.sol";

interface IProcessorMailbox {
    function delivered(bytes32 id) external view returns (bool);
    function processor(bytes32 id) external view returns (address);
}

/// @notice Testnet-only trusted-relayer authenticator. It keeps the public
/// Hyperlane Mailbox/process path real while avoiding dependence on a broken
/// public-testnet validator checkpoint. It must never be described as the
/// production Hyperlane validator set.
contract AuthorRelayerIsm is IInterchainSecurityModule {
    address public immutable mailbox;
    address public immutable relayer;

    constructor(address _mailbox, address _relayer) {
        mailbox = _mailbox;
        relayer = _relayer;
    }

    function moduleType() external pure returns (uint8) {
        return uint8(Types.NULL);
    }

    function verify(bytes calldata, bytes calldata message) external view returns (bool) {
        bytes32 id = keccak256(message);
        IProcessorMailbox m = IProcessorMailbox(mailbox);
        return m.delivered(id) && m.processor(id) == relayer;
    }
}
