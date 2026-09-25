// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {ExposureBudgetIsm} from "../src/ExposureBudgetIsm.sol";

/// Mailbox delivery state is permanent: `delivered(id)` stays true forever
/// once a message has been processed, which is the property under test.
contract HistoryMailbox {
    mapping(bytes32 => bool) public delivered;
    mapping(bytes32 => uint48) public processedAt;
    address public defaultIsm;
    mapping(address => address) internal _ism;
    function setDefaultIsm(address a) external { defaultIsm = a; }
    function setRecipientIsm(address r, address a) external { _ism[r] = a; }
    function recipientIsm(address r) external view returns (address) {
        address a = _ism[r];
        return a == address(0) ? defaultIsm : a;
    }
    /// Mirrors Mailbox.process(): both fields are written before ISM.verify().
    function markDelivered(bytes32 id) external {
        delivered[id] = true;
        processedAt[id] = uint48(block.number);
    }
}

/// Does the deployed guard pair -- delivered(id) plus a once-only meter flag --
/// actually stop an external caller from consuming budget?
///
/// The claimed attack: a message delivered BEFORE this module was installed
/// has delivered(id) == true and messageMetered[id] == false. Nothing in the
/// guard distinguishes it from a message the Mailbox is processing right now.
contract P0HistoricalReplayTest is Test {
    ExposureBudgetIsm ism;
    HistoryMailbox mailbox;

    address gov = address(0x9001);
    address guard = address(0x6402);
    address victim = address(0xA1);
    address attacker = address(0xBAD);

    uint96 constant BUDGET = 150_000e18;
    uint16 constant ALPHA = 8_000;

    function setUp() public {
        vm.warp(1_700_000_000);
        mailbox = new HistoryMailbox();
        vm.roll(1000);
        ism = new ExposureBudgetIsm(address(mailbox), gov, guard, BUDGET,
                                    ALPHA, 1 days, 2 days);
        mailbox.setDefaultIsm(address(ism));
        vm.prank(gov);
        ism.setInstalledUnder(address(ism));

        address[] memory rs = new address[](1);
        uint256[] memory vals = new uint256[](1);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](1);
        rs[0] = victim; vals[0] = 1e18; ms[0] = ExposureBudgetIsm.Mode.METERED;
        vm.startPrank(gov);
        ism.setUnitValues(rs, vals);
        ism.setModes(rs, ms);
        vm.stopPrank();
        // The victim route inherits the default module tree, as 258 of 360
        // measured routes do.
        mailbox.setDefaultIsm(address(ism));
    }

    function _msg(address r, uint256 amt) internal pure returns (bytes memory) {
        return abi.encodePacked(
            uint8(3), uint32(0), uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(r))),
            bytes32(uint256(uint160(r))), bytes32(amt));
    }

    /// The route has no floor, so every admission draws the shared surplus.
    function test_HistoricalMessagesDrainSurplusByDirectCall() public {
        uint256 surplusBefore = ism.surplusAvailable();
        assertEq(surplusBefore, uint256(BUDGET) * 2_000 / 10_000,
                 "surplus should be (1-alpha)B");

        // Ten messages delivered long before this module existed. They were
        // never metered by it, because it was not installed yet.
        bytes[] memory history = new bytes[](10);
        for (uint256 i; i < 10; ++i) {
            history[i] = _msg(victim, 3_000e18 + i);
            mailbox.markDelivered(keccak256(history[i]));
        }
        // A year later: both the clock and the block height move, which is
        // what makes these messages historical.
        vm.warp(block.timestamp + 365 days);
        vm.roll(block.number + 2_600_000);

        // An attacker with no capital and no role calls verify() directly.
        uint256 drained;
        vm.startPrank(attacker);
        for (uint256 i; i < 10; ++i) {
            uint256 before = ism.surplusAvailable();
            try ism.verify("", history[i]) returns (bool ok) {
                if (ok) drained += before - ism.surplusAvailable();
            } catch {}
        }
        vm.stopPrank();

        emit log_named_decimal_uint("surplus before      ", surplusBefore, 18);
        emit log_named_decimal_uint("surplus after attack", ism.surplusAvailable(), 18);
        emit log_named_decimal_uint("drained by attacker ", drained, 18);

        assertEq(drained, 0,
            "an external caller consumed budget using historical messages");
    }
    /// A message the Mailbox is processing right now is chargeable exactly once.
    function test_CurrentDeliveryChargedExactlyOnce() public {
        bytes memory m = _msg(victim, 5_000e18);
        mailbox.markDelivered(keccak256(m));
        uint256 before = ism.surplusAvailable();

        assertTrue(ism.verify("", m), "genuine current delivery must admit");
        uint256 afterFirst = ism.surplusAvailable();
        assertEq(before - afterFirst, 5_000e18, "charged once");

        // Same block, same message, direct call: the meter flag rejects it.
        vm.prank(attacker);
        vm.expectRevert();
        ism.verify("", m);
        assertEq(ism.surplusAvailable(), afterFirst, "no second charge");
    }

    /// A message delivered in this block through a route's own ISM never
    /// reaches this contract, so the meter flag cannot have been set. The
    /// recipient-ISM guard is what rejects it.
    function test_SameBlockForeignIsmPathRejected() public {
        mailbox.setRecipientIsm(victim, address(0xFEED));   // custom module
        bytes memory m = _msg(victim, 5_000e18);
        mailbox.markDelivered(keccak256(m));                // same block

        uint256 before = ism.surplusAvailable();
        vm.prank(attacker);
        vm.expectRevert();
        ism.verify("", m);
        assertEq(ism.surplusAvailable(), before, "foreign-path message charged");
    }

    /// A fabricated message that the Mailbox never saw.
    function test_FabricatedMessageRejected() public {
        bytes memory m = _msg(victim, 20_000e18);
        uint256 before = ism.surplusAvailable();
        vm.prank(attacker);
        vm.expectRevert();
        ism.verify("", m);
        assertEq(ism.surplusAvailable(), before, "fabricated message charged");
    }

    /// Over-budget rejection must roll back the meter flag with everything
    /// else, so the same message is deliverable once capacity refills.
    function test_OverBudgetRevertsThenSucceedsAfterRefill() public {
        bytes memory big = _msg(victim, 29_000e18);
        mailbox.markDelivered(keccak256(big));
        assertTrue(ism.verify("", big), "drains most of the surplus");

        bytes memory m = _msg(victim, 5_000e18);
        mailbox.markDelivered(keccak256(m));
        vm.expectRevert();
        ism.verify("", m);

        // Reverting the call also reverts messageMetered[id]; re-present the
        // message in a later block once the bucket has refilled.
        vm.warp(block.timestamp + 12 hours);
        vm.roll(block.number + 3600);
        mailbox.markDelivered(keccak256(m));
        assertTrue(ism.verify("", m), "must be replayable after refill");
    }
}
