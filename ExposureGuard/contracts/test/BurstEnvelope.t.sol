// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test, console} from "forge-std/Test.sol";
import {ExposureBudgetIsm} from "../src/ExposureBudgetIsm.sol";

contract MockMailbox {
    mapping(bytes32 => bool) public delivered;
    function markDelivered(bytes32 id) external { delivered[id] = true; }
}

/// Settles what the module actually admits over a detection gap [t0, t1].
/// The paper's Table II claims B*(t1-t0)/D. A token bucket that starts full
/// admits its whole capacity at once, so the envelope should be burst + refill.
contract BurstEnvelopeTest is Test {
    ExposureBudgetIsm ism;
    MockMailbox mailbox;
    address gov = address(0x9001);
    address route = address(0xA1);
    uint96 constant B = 100_000e18;
    uint64 constant D = 1 days;
    uint32 nonce;

    function setUp() public {
        vm.warp(1_700_000_000);
        mailbox = new MockMailbox();
        // alpha = 0: the whole budget is surplus, so one route can draw all of it
        ism = new ExposureBudgetIsm(address(mailbox), gov, gov, B, 0, D, 2 days);
        address[] memory rs = new address[](1);
        uint256[] memory v = new uint256[](1);
        ExposureBudgetIsm.Mode[] memory m = new ExposureBudgetIsm.Mode[](1);
        rs[0] = route; v[0] = 1e18; m[0] = ExposureBudgetIsm.Mode.METERED;
        vm.startPrank(gov);
        ism.setUnitValues(rs, v);
        ism.setModes(rs, m);
        vm.stopPrank();
    }

    function _take(uint256 amt) internal returns (bool) {
        bytes memory msg_ = abi.encodePacked(
            uint8(3), ++nonce, uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(route))), bytes32(uint256(uint160(route))),
            bytes32(amt));
        mailbox.markDelivered(keccak256(msg_));
        try ism.verify("", msg_) { return true; } catch { return false; }
    }

    /// Drain everything available at each retry tick over a gap of `gap`.
    function _drain(uint256 gap, uint256 step) internal returns (uint256 got) {
        uint256 end = block.timestamp + gap;
        while (block.timestamp <= end) {
            uint256 avail = ism.availableFor(route);
            if (avail > 0 && _take(avail)) got += avail;
            vm.warp(block.timestamp + step);
        }
    }

    function test_EnvelopeOverDetectionGap() public {
        uint256[4] memory gaps = [uint256(30 minutes), 6 hours, 1 days, 7 days];
        string[4] memory names = ["30 min", "6 h", "24 h", "7 d"];
        console.log("gap        paper B*dt/D      actual      burst+refill");
        for (uint256 i; i < gaps.length; ++i) {
            uint256 snap = vm.snapshotState();
            uint256 got = _drain(gaps[i], 1 minutes);
            uint256 paper = uint256(B) * gaps[i] / D;
            uint256 correct = uint256(B) + uint256(B) * gaps[i] / D;
            console.log(names[i]);
            console.log("   paper formula :", paper / 1e18);
            console.log("   measured      :", got / 1e18);
            console.log("   burst+refill  :", correct / 1e18);
            vm.revertToState(snap);
        }
    }
}
