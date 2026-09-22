// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {IMessageLib, MessageLibType} from
    "@layerzerolabs/lz-evm-protocol-v2/contracts/interfaces/IMessageLib.sol";
import {SetConfigParam} from
    "@layerzerolabs/lz-evm-protocol-v2/contracts/interfaces/IMessageLibManager.sol";

/// Minimal LayerZero V2 protocol types used by the receive-library prototype.
struct LzOrigin {
    uint32 srcEid;
    bytes32 sender;
    uint64 nonce;
}

interface ILayerZeroEndpointV2Exposure {
    function eid() external view returns (uint32);
    function verify(LzOrigin calldata origin, address receiver, bytes32 payloadHash) external;
}

/// @notice Interface-complete LayerZero V2 receive-library prototype.
///
/// An Endpoint owner can register this library and select it as the default
/// receive library. Every OApp that inherits that default then shares one
/// budget. The packet is metered before Endpoint.verify commits it. This is an
/// existence prototype, not a wrapper around production ULN302: ULN302 exposes
/// only a payload hash at commit time, so a transparent value-aware wrapper
/// cannot recover the message amount. `validator` represents the verification
/// authority that a production port must replace with its DVN logic.
contract LayerZeroExposureReceiveLib is IMessageLib {
    uint8 internal constant PACKET_VERSION = 1;
    uint256 internal constant HEADER_LENGTH = 81;
    uint256 internal constant GUID_OFFSET = 81;
    uint256 internal constant MESSAGE_OFFSET = 113;

    address public immutable endpoint;
    uint32 public immutable localEid;
    address public owner;
    address public validator;
    uint96 public budget;
    uint96 public level;
    uint64 public immutable window;
    uint64 public updatedAt;
    bool public paused;

    mapping(address receiver => uint256 valuePerUnit) public unitValue;
    mapping(bytes32 payloadHash => bool) public committed;

    event PacketMetered(bytes32 indexed payloadHash, address indexed receiver, uint256 value, uint96 levelLeft);

    error NotOwner();
    error NotEndpoint();
    error NotValidator();
    error InvalidPacket();
    error UnsupportedReceiver(address receiver);
    error BudgetExceeded(uint256 need, uint256 available);
    error AlreadyCommitted();
    error Paused();

    constructor(address _endpoint, address _validator, uint96 _budget, uint64 _window) {
        endpoint = _endpoint;
        localEid = ILayerZeroEndpointV2Exposure(_endpoint).eid();
        owner = msg.sender;
        validator = _validator;
        budget = _budget;
        level = _budget;
        window = _window;
        updatedAt = uint64(block.timestamp);
    }

    function setUnitValue(address receiver, uint256 value) external {
        if (msg.sender != owner) revert NotOwner();
        unitValue[receiver] = value;
    }

    function setValidator(address next) external {
        if (msg.sender != owner) revert NotOwner();
        validator = next;
    }

    function setPaused(bool value) external {
        if (msg.sender != owner) revert NotOwner();
        paused = value;
    }

    /// Packet V1: version|nonce|srcEid|sender|dstEid|receiver|guid|message.
    /// The controlled token message begins with uint256 amount.
    function validatePacket(bytes calldata packet) external {
        if (msg.sender != validator) revert NotValidator();
        if (paused) revert Paused();
        if (packet.length < MESSAGE_OFFSET + 32 || uint8(packet[0]) != PACKET_VERSION) revert InvalidPacket();

        uint32 dstEid = uint32(bytes4(packet[45:49]));
        if (dstEid != localEid) revert InvalidPacket();
        address receiver = address(bytes20(packet[61:81]));
        uint256 price = unitValue[receiver];
        if (price == 0) revert UnsupportedReceiver(receiver);

        bytes32 payloadHash = keccak256(packet[GUID_OFFSET:]);
        if (committed[payloadHash]) revert AlreadyCommitted();
        uint256 amount = uint256(bytes32(packet[MESSAGE_OFFSET:MESSAGE_OFFSET + 32]));
        uint256 value = amount * price / 1e18;
        _consume(value);
        committed[payloadHash] = true;

        LzOrigin memory origin = LzOrigin({
            srcEid: uint32(bytes4(packet[9:13])),
            sender: bytes32(packet[13:45]),
            nonce: uint64(bytes8(packet[1:9]))
        });
        ILayerZeroEndpointV2Exposure(endpoint).verify(origin, receiver, payloadHash);
        emit PacketMetered(payloadHash, receiver, value, level);
    }

    function _consume(uint256 value) internal {
        uint256 elapsed = block.timestamp - updatedAt;
        uint256 refill = elapsed >= window ? budget : uint256(budget) * elapsed / window;
        uint256 available = uint256(level) + refill;
        if (available > budget) available = budget;
        if (value > available) revert BudgetExceeded(value, available);
        level = uint96(available - value);
        updatedAt = uint64(block.timestamp);
    }

    function supportsInterface(bytes4 interfaceId) external pure override returns (bool) {
        return interfaceId == type(IMessageLib).interfaceId || interfaceId == 0x01ffc9a7;
    }

    function setConfig(address, SetConfigParam[] calldata) external view override {
        if (msg.sender != endpoint) revert NotEndpoint();
    }

    function getConfig(uint32, address, uint32) external pure override returns (bytes memory) {
        return bytes("");
    }

    function isSupportedEid(uint32) external pure override returns (bool) { return true; }
    function version() external pure override returns (uint64, uint8, uint8) { return (1, 0, 2); }
    function messageLibType() external pure override returns (MessageLibType) { return MessageLibType.Receive; }
}
