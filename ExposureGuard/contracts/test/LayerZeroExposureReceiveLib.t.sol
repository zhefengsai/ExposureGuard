// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {LayerZeroExposureReceiveLib, LzOrigin} from "../src/LayerZeroExposureReceiveLib.sol";
import {IMessageLib, MessageLibType} from
    "@layerzerolabs/lz-evm-protocol-v2/contracts/interfaces/IMessageLib.sol";

contract EndpointV2Fixture {
    uint32 public constant eid = 40_161;
    bytes32 public lastPayloadHash;
    address public lastReceiver;
    function verify(LzOrigin calldata, address receiver, bytes32 payloadHash) external {
        lastReceiver = receiver;
        lastPayloadHash = payloadHash;
    }
}

contract LayerZeroExposureReceiveLibTest is Test {
    EndpointV2Fixture endpoint;
    LayerZeroExposureReceiveLib lib;
    address receiver = address(0xBEEF);

    function setUp() public {
        endpoint = new EndpointV2Fixture();
        lib = new LayerZeroExposureReceiveLib(address(endpoint), address(this), 100, 1 days);
        lib.setUnitValue(receiver, 1 ether);
    }

    function packet(uint256 amount) internal view returns (bytes memory) {
        return abi.encodePacked(
            uint8(1), uint64(7), uint32(30_101), bytes32(uint256(0xCAFE)),
            uint32(40_161), bytes32(uint256(uint160(receiver))), bytes32(uint256(9)), amount
        );
    }

    function testMetersBeforeEndpointCommit() public {
        bytes memory p = packet(20);
        lib.validatePacket(p);
        assertEq(lib.level(), 80);
        assertEq(endpoint.lastReceiver(), receiver);
        assertEq(endpoint.lastPayloadHash(), keccak256(abi.encodePacked(bytes32(uint256(9)), uint256(20))));
    }

    function testGasValidatePacket() public {
        bytes memory p = packet(20);
        LayerZeroExposureReceiveLib target = lib;
        uint256 startGas = gasleft();
        target.validatePacket(p);
        uint256 used = startGas - gasleft();
        emit log_named_uint("LAYERZERO_VALIDATE_PACKET_GAS", used);
        assertGt(used, 0);
    }

    function testImplementsPinnedOfficialMessageLib() public view {
        assertTrue(lib.supportsInterface(type(IMessageLib).interfaceId));
        assertEq(uint8(lib.messageLibType()), uint8(MessageLibType.Receive));
        (uint64 major, uint8 minor, uint8 endpointVersion) = lib.version();
        assertEq(major, 1);
        assertEq(minor, 0);
        assertEq(endpointVersion, 2);
    }

    function testSharedBudgetAcrossReceivers() public {
        address other = address(0xCAFE);
        lib.setUnitValue(other, 1 ether);
        lib.validatePacket(packet(60));
        bytes memory p2 = abi.encodePacked(
            uint8(1), uint64(8), uint32(30_101), bytes32(uint256(1)),
            uint32(40_161), bytes32(uint256(uint160(other))), bytes32(uint256(10)), uint256(50)
        );
        vm.expectRevert();
        lib.validatePacket(p2);
    }

    function testRejectsReplayAndUnpricedReceiver() public {
        bytes memory p = packet(1);
        lib.validatePacket(p);
        vm.expectRevert(LayerZeroExposureReceiveLib.AlreadyCommitted.selector);
        lib.validatePacket(p);

        bytes memory bad = abi.encodePacked(
            uint8(1), uint64(9), uint32(30_101), bytes32(uint256(1)),
            uint32(40_161), bytes32(uint256(uint160(address(2)))), bytes32(uint256(11)), uint256(1)
        );
        vm.expectRevert(abi.encodeWithSelector(LayerZeroExposureReceiveLib.UnsupportedReceiver.selector, address(2)));
        lib.validatePacket(bad);
    }
}
