// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test, console} from "forge-std/Test.sol";
import {ExposureBudgetIsm} from "../src/ExposureBudgetIsm.sol";

/// Minimal stand-in for the Hyperlane Mailbox delivery record.
contract MockMailbox {
    mapping(bytes32 => bool) public delivered;

    function markDelivered(bytes32 id) external {
        delivered[id] = true;
    }
}

contract ExposureBudgetIsmTest is Test {
    ExposureBudgetIsm ism;
    MockMailbox mailbox;

    address gov = address(0x9001);
    address guard = address(0x6402);
    address routeA = address(0xA1);
    address routeB = address(0xB2);
    address adversary = address(0xAD);

    uint96 constant BUDGET = 150_000e18;    // $150k/day, the recommended point
    uint16 constant ALPHA = 8_000;          // 0.8
    uint64 constant WINDOW = 1 days;
    uint64 constant RAISE_DELAY = 2 days;

    function setUp() public {
        vm.warp(1_700_000_000);
        mailbox = new MockMailbox();
        ism = new ExposureBudgetIsm(address(mailbox), gov, guard, BUDGET, ALPHA,
                                    WINDOW, RAISE_DELAY);

        // Floors are provisioned from measured history as weights over the
        // reserved pool alpha*B = 120k. A gets 75%, B gets 25%; the adversary
        // has no history and therefore no weight.
        address[] memory rs = new address[](2);
        uint16[] memory w = new uint16[](2);
        rs[0] = routeA; w[0] = 7_500;   // 0.75 * 120k = 90k
        rs[1] = routeB; w[1] = 2_500;   // 0.25 * 120k = 30k
        vm.prank(gov);
        ism.setFloors(rs, w);

        // 1 token unit == 1 budget unit for every route in these tests
        address[] memory rs3 = new address[](3);
        uint256[] memory vals = new uint256[](3);
        rs3[0] = routeA; rs3[1] = routeB; rs3[2] = adversary;
        vals[0] = 1e18; vals[1] = 1e18; vals[2] = 1e18;
        vm.prank(gov);
        ism.setUnitValues(rs3, vals);

        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](3);
        ms[0] = ExposureBudgetIsm.Mode.METERED;
        ms[1] = ExposureBudgetIsm.Mode.METERED;
        ms[2] = ExposureBudgetIsm.Mode.METERED;
        vm.prank(gov);
        ism.setModes(rs3, ms);
    }

    function _classify(address[] memory rs, uint256[] memory vals) internal {
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](rs.length);
        for (uint256 i; i < rs.length; ++i) ms[i] = ExposureBudgetIsm.Mode.METERED;
        vm.startPrank(gov);
        ism.setUnitValues(rs, vals);
        ism.setModes(rs, ms);
        vm.stopPrank();
    }

    /// A recipient governance has never classified must not pass. Failing open
    /// here would let an adversary route value through any unclassified
    /// recipient and void the budget completely.
    function test_UnclassifiedRecipientFailsClosed() public {
        address stranger = address(0xDEAD01);
        bytes memory m = _deliver(stranger, 1e18);
        vm.expectRevert(abi.encodeWithSelector(
            ExposureBudgetIsm.UnclassifiedRecipient.selector, stranger));
        ism.verify("", m);
    }

    /// A route marked METERED but left unpriced would meter every transfer as
    /// zero, so it must revert rather than admit.
    function test_MeteredButUnpricedFailsClosed() public {
        address r = address(0xDEAD02);
        address[] memory rs = new address[](1);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](1);
        rs[0] = r; ms[0] = ExposureBudgetIsm.Mode.METERED;
        vm.prank(gov);
        ism.setModes(rs, ms);
        bytes memory m = _deliver(r, 1e18);
        vm.expectRevert(abi.encodeWithSelector(
            ExposureBudgetIsm.UnpricedRoute.selector, r));
        ism.verify("", m);
    }

    /// Plain GMP carries no amount in the body. It must pass through rather
    /// than revert, otherwise installing this module in the default ISM would
    /// break every non-token message on the chain.
    function test_ExemptRecipientPassesShortMessage() public {
        address app = address(0xA99);
        address[] memory rs = new address[](1);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](1);
        rs[0] = app;
        vm.startPrank(gov);
        ism.queueExempt(rs);
        vm.warp(block.timestamp + RAISE_DELAY);
        ism.executeExempt(rs);
        vm.stopPrank();
        ms;  // silence unused

        // 77-byte header plus a 4-byte body: no TokenMessage present
        bytes memory m = abi.encodePacked(
            uint8(3), uint32(0), uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(app))), bytes4(0xdeadbeef));
        mailbox.markDelivered(keccak256(m));
        assertTrue(ism.verify("", m));
    }

    // ------------------------------------------------------------- helpers

    function _msg(address recipient, uint256 amount)
        internal
        pure
        returns (bytes memory)
    {
        // 1B version | 4B nonce | 4B origin | 32B sender | 4B dest | 32B recipient
        // body: 32B recipient | 32B amount
        return abi.encodePacked(
            uint8(3), uint32(0), uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(recipient))),
            bytes32(uint256(uint160(recipient))), bytes32(amount)
        );
    }

    /// An undelivered message must not be meterable: otherwise anyone can
    /// fabricate one and zero the shared budget for the price of gas.
    function test_UndeliveredMessageCannotMeter() public {
        bytes memory m = _msg(routeA, 50_000e18);
        vm.expectRevert(abi.encodeWithSelector(
            ExposureBudgetIsm.MessageNotDelivered.selector, keccak256(m)));
        ism.verify("", m);
    }

    /// The same delivered message may only be metered once.
    function test_ReplayCannotDoubleMeter() public {
        bytes memory m = _msg(routeA, 1_000e18);
        mailbox.markDelivered(keccak256(m));
        ism.verify("", m);
        vm.expectRevert(abi.encodeWithSelector(
            ExposureBudgetIsm.AlreadyMetered.selector, keccak256(m)));
        ism.verify("", m);
    }

    /// Free budget exhaustion: without the delivery guard an adversary needs no
    /// capital at all. With it, the budget survives an unbounded number of
    /// fabricated calls.
    function test_FabricatedMessagesCannotDrainBudget() public {
        uint256 before = ism.surplusAvailable();
        for (uint256 i; i < 20; ++i) {
            bytes memory m = _msg(routeA, BUDGET, uint32(i + 1000));
            vm.expectRevert();
            ism.verify("", m);
        }
        assertEq(ism.surplusAvailable(), before, "budget moved on undelivered msgs");
    }

    uint32 internal _nonce;

    /// Record delivery and hand back the message, so a test can place
    /// `vm.expectRevert` immediately before the ISM call itself.
    function _deliver(address r, uint256 amt) internal returns (bytes memory m) {
        m = _msg(r, amt, ++_nonce);
        mailbox.markDelivered(keccak256(m));
    }

    /// Mirrors the real flow: the Mailbox records delivery, then the ISM meters.
    /// The nonce keeps each message id unique so the replay guard does not fire.
    function _verify(address r, uint256 amt) internal {
        bytes memory m = _msg(r, amt, ++_nonce);
        mailbox.markDelivered(keccak256(m));
        ism.verify("", m);
    }

    function _msg(address recipient, uint256 amount, uint32 n)
        internal
        pure
        returns (bytes memory)
    {
        return abi.encodePacked(
            uint8(3), n, uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(recipient))),
            bytes32(uint256(uint160(recipient))), bytes32(amount)
        );
    }

    // ------------------------------------------------------- core invariant

    /// Aggregate admitted value over one window never exceeds the budget.
    function test_AggregateInvariant() public {
        uint256 admitted;
        // drain everything each party can possibly take
        admitted += _tryTake(routeA, 200_000e18);
        admitted += _tryTake(routeB, 200_000e18);
        admitted += _tryTake(adversary, 200_000e18);
        assertLe(admitted, uint256(BUDGET), "aggregate exceeded budget");
        assertEq(admitted, uint256(BUDGET), "should be able to reach the budget");
    }

    function _tryTake(address r, uint256 want) internal returns (uint256) {
        uint256 avail = ism.availableFor(r);
        uint256 take = want < avail ? want : avail;
        if (take > 0) _verify(r, take);
        return take;
    }

    /// A route's floor is unreachable by anyone else, even a fully draining
    /// adversary. This is the isolation guarantee alpha buys.
    function test_FloorIsolation() public {
        // adversary takes everything it can: it has no floor, so only surplus
        uint256 advTook = _tryTake(adversary, BUDGET);
        assertEq(advTook, uint256(BUDGET) * 2_000 / 10_000, "adversary got != surplus");

        // routeA can still spend its entire floor
        bytes memory over = _deliver(routeA, 90_001e18);
        vm.expectRevert(); // surplus is gone, so anything above the floor fails
        ism.verify("", over);
        _verify(routeA, 90_000e18);
        assertEq(ism.floorAvailable(routeA), 0);
    }

    /// The adversary's share is bounded by (1 - alpha), matching the analytical
    /// bound in DESIGN-BUDGET.md section 4.
    function test_AdversaryShareMatchesAnalyticalBound() public {
        uint256 took = _tryTake(adversary, BUDGET);
        uint256 predicted = uint256(BUDGET) * (10_000 - ALPHA) / 10_000;
        assertEq(took, predicted, "adversary share != (1-alpha)B");
    }

    /// Deploying N fresh routes yields no additional floor, so Sybils gain
    /// nothing over a single adversary route.
    function test_SybilGainsNothing() public {
        uint256 single = _tryTake(adversary, BUDGET);

        // reset the window and try again with 50 fresh routes
        vm.warp(block.timestamp + WINDOW);
        uint256 many;
        address[] memory rs = new address[](50);
        uint256[] memory vals = new uint256[](50);
        for (uint256 i; i < 50; ++i) {
            rs[i] = address(uint160(0x51B0 + i));
            vals[i] = 1e18;
        }
        _classify(rs, vals);
        for (uint256 i; i < 50; ++i) many += _tryTake(rs[i], BUDGET);

        assertEq(many, single, "50 Sybil routes beat 1 adversary route");
    }

    // -------------------------------------------------------- refill / window

    function test_RefillsOverWindow() public {
        _tryTake(adversary, BUDGET); // drain surplus
        assertEq(ism.surplusAvailable(), 0);
        vm.warp(block.timestamp + WINDOW / 2);
        uint256 half = uint256(BUDGET) * 2_000 / 10_000 / 2;
        assertApproxEqRel(ism.surplusAvailable(), half, 1e15);
        vm.warp(block.timestamp + WINDOW);
        assertEq(ism.surplusAvailable(), uint256(BUDGET) * 2_000 / 10_000);
    }

    // ---------------------------------------------------- asymmetric control

    /// Governance cannot hand out more reserved capacity than the pool holds.
    function test_FloorWeightsCannotExceedPool() public {
        address[] memory rs = new address[](1);
        uint16[] memory w = new uint16[](1);
        rs[0] = address(0xC0FFEE); w[0] = 1;   // total would be 10_001
        vm.prank(gov);
        vm.expectRevert(abi.encodeWithSelector(
            ExposureBudgetIsm.WeightOverflow.selector, uint32(10_001)));
        ism.setFloors(rs, w);
    }

    /// Floor caps track the budget, so lowering the budget rescales every floor
    /// in O(1) and the aggregate invariant survives the change.
    function test_LoweringRescalesFloors() public {
        assertEq(ism.floorCapOf(routeA), 90_000e18);
        vm.prank(guard);
        ism.lowerBudget(50_000e18);
        assertEq(ism.floorCapOf(routeA), 30_000e18);   // 0.75 * 0.8 * 50k
        assertEq(ism.floorCapOf(routeB), 10_000e18);
        assertEq(ism.surplusAvailable(), 10_000e18);   // 0.2 * 50k
    }

    /// EXEMPT loosens the guarantee, so it cannot be applied immediately.
    function test_ExemptRequiresTimelock() public {
        address[] memory rs = new address[](1);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](1);
        rs[0] = address(0xE1); ms[0] = ExposureBudgetIsm.Mode.EXEMPT;

        vm.prank(gov);
        vm.expectRevert(ExposureBudgetIsm.ExemptMustBeQueued.selector);
        ism.setModes(rs, ms);

        vm.prank(gov);
        ism.queueExempt(rs);
        vm.prank(gov);
        vm.expectRevert(abi.encodeWithSelector(
            ExposureBudgetIsm.ExemptNotReady.selector, rs[0]));
        ism.executeExempt(rs);

        vm.warp(block.timestamp + RAISE_DELAY);
        vm.prank(gov);
        ism.executeExempt(rs);
        assertEq(uint8(ism.mode(rs[0])), uint8(ExposureBudgetIsm.Mode.EXEMPT));
    }

    /// Tightening a recipient back to METERED stays immediate.
    function test_TighteningIsImmediate() public {
        address[] memory rs = new address[](1);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](1);
        rs[0] = address(0xE2); ms[0] = ExposureBudgetIsm.Mode.METERED;
        vm.prank(gov);
        ism.setModes(rs, ms);
        assertEq(uint8(ism.mode(rs[0])), uint8(ExposureBudgetIsm.Mode.METERED));
    }

    function test_LoweringIsImmediate() public {
        vm.prank(guard);
        ism.lowerBudget(50_000e18);
        assertEq(ism.budget(), 50_000e18);
    }

    function test_GuardianCannotRaise() public {
        vm.prank(guard);
        vm.expectRevert(ExposureBudgetIsm.NotGovernance.selector);
        ism.queueBudgetRaise(300_000e18);
    }

    function test_RaisingIsTimelocked() public {
        vm.prank(gov);
        ism.queueBudgetRaise(300_000e18);
        vm.prank(gov);
        vm.expectRevert(ExposureBudgetIsm.TimelockPending.selector);
        ism.executeBudgetRaise();

        vm.warp(block.timestamp + RAISE_DELAY);
        vm.prank(gov);
        ism.executeBudgetRaise();
        assertEq(ism.budget(), 300_000e18);
    }

    function test_GuardianCanPauseImmediately() public {
        vm.prank(guard);
        ism.setPaused(true);
        bytes memory m = _deliver(routeA, 1e18);
        vm.expectRevert(ExposureBudgetIsm.Paused.selector);
        ism.verify("", m);
    }

    // ------------------------------------------------------------------ gas

    function test_GasHotPath() public {
        // warm the slots first; report the steady-state cost
        _verify(routeA, 1e18);

        uint256 g0 = gasleft();
        _verify(routeA, 1e18);
        uint256 floorOnly = g0 - gasleft();

        // exhaust only the floor, then let the surplus refill so the next
        // verify exercises both buckets
        _verify(routeA, ism.floorAvailable(routeA));
        vm.warp(block.timestamp + WINDOW);
        g0 = gasleft();
        _verify(routeA, ism.floorAvailable(routeA) + 1e18);
        uint256 floorPlusSurplus = g0 - gasleft();

        console.log("verify(), floor hit only      :", floorOnly);
        console.log("verify(), floor + surplus     :", floorPlusSurplus);
    }

    function test_GasColdRoute() public {
        address fresh = address(0xFEED);
        address[] memory rs = new address[](1);
        uint256[] memory vals = new uint256[](1);
        rs[0] = fresh; vals[0] = 1e18;
        _classify(rs, vals);

        uint256 g0 = gasleft();
        _verify(fresh, 1e18);
        console.log("verify(), cold route (no floor):", g0 - gasleft());
    }

    // --------------------------------------------------------------- fuzzing

    /// No sequence of transfers can admit more than the budget in one window.
    function testFuzz_NeverExceedsBudget(uint96[8] calldata amounts) public {
        uint256 admitted;
        address[3] memory who = [routeA, routeB, adversary];
        for (uint256 i; i < amounts.length; ++i) {
            address r = who[i % 3];
            uint256 avail = ism.availableFor(r);
            uint256 amt = uint256(amounts[i]) % (BUDGET + 1);
            if (amt == 0 || amt > avail) continue;
            _verify(r, amt);
            admitted += amt;
        }
        assertLe(admitted, uint256(BUDGET));
    }
}
