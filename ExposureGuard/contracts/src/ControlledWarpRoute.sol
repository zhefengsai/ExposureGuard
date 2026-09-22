// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

interface IHyperlaneMailbox {
    function dispatch(uint32 destinationDomain, bytes32 recipientAddress, bytes calldata messageBody)
        external
        payable
        returns (bytes32);

    function quoteDispatch(uint32 destinationDomain, bytes32 recipientAddress, bytes calldata messageBody)
        external
        view
        returns (uint256);
}

/// @notice Minimal author-driven Hyperlane workload endpoint for the public
/// testnet experiment. Authentication and admission remain in the recipient's
/// ISM; this contract only emits and records controlled value-bearing traffic.
contract ControlledWarpRoute {
    address public immutable mailbox;
    address public immutable owner;
    address public interchainSecurityModule;

    mapping(uint32 domain => bytes32 sender) public remote;
    uint256 public receivedCount;
    uint256 public receivedAmount;
    bytes32 public lastMessageId;

    event Sent(bytes32 indexed messageId, uint32 indexed destination, uint256 amount);
    event Received(uint32 indexed origin, bytes32 indexed sender, uint256 amount);

    error NotOwner();
    error NotMailbox();
    error UntrustedRemote();

    constructor(address _mailbox) {
        mailbox = _mailbox;
        owner = msg.sender;
    }

    function setRemote(uint32 domain, bytes32 sender) external {
        if (msg.sender != owner) revert NotOwner();
        remote[domain] = sender;
    }

    function setInterchainSecurityModule(address ism) external {
        if (msg.sender != owner) revert NotOwner();
        interchainSecurityModule = ism;
    }

    function body(uint256 amount) public view returns (bytes memory) {
        // Hyperlane TokenMessage layout used by ExposureBudgetIsm:
        // bytes32 recipient followed by uint256 amount.
        return abi.encode(bytes32(uint256(uint160(address(this)))), amount);
    }

    function quote(uint32 destination, bytes32 recipient, uint256 amount) external view returns (uint256) {
        return IHyperlaneMailbox(mailbox).quoteDispatch(destination, recipient, body(amount));
    }

    function send(uint32 destination, bytes32 recipient, uint256 amount)
        external
        payable
        returns (bytes32 id)
    {
        id = IHyperlaneMailbox(mailbox).dispatch{value: msg.value}(destination, recipient, body(amount));
        lastMessageId = id;
        emit Sent(id, destination, amount);
    }

    function handle(uint32 origin, bytes32 sender, bytes calldata message) external payable {
        if (msg.sender != mailbox) revert NotMailbox();
        if (remote[origin] != sender) revert UntrustedRemote();
        (, uint256 amount) = abi.decode(message, (bytes32, uint256));
        receivedCount += 1;
        receivedAmount += amount;
        emit Received(origin, sender, amount);
    }
}
