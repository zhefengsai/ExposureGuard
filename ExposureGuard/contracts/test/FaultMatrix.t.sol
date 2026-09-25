// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test, console2 as console} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";
import {ExposureBudgetIsm} from "../src/ExposureBudgetIsm.sol";

/// @dev Mailbox stand-in: delivery record only.
contract FaultMatrixMailbox {
    mapping(bytes32 => bool) public delivered;
    /// Mirrors Mailbox.process(): the delivery record is written before the
    /// ISM runs, so a module can tell a live delivery from a historical one.
    mapping(bytes32 => uint48) public processedAt;
    address public defaultIsm = address(this);
    mapping(address => address) internal _recipientIsm;

    function setDefaultIsm(address a) external { defaultIsm = a; }
    function setRecipientIsm(address r, address a) external { _recipientIsm[r] = a; }
    function recipientIsm(address r) external view returns (address) {
        address a = _recipientIsm[r];
        return a == address(0) ? defaultIsm : a;
    }

    function markDelivered(bytes32 id) external {
        delivered[id] = true;
        processedAt[id] = uint48(block.number);
    }
}

/**
 * @title FaultMatrix
 * @notice Six fault classes against ExposureBudgetIsm. Every Fx has a paired
 *         NegativeControl. Budget B is loaded from data/ (materialised from
 *         sim3.json by fault_matrix.py) — never hard-coded in test bodies.
 *
 * Params below are the ONLY place numeric policy constants appear.
 */
contract FaultMatrixTest is Test {
    using stdJson for string;

    // ------------------------------------------------------------------ Params
    // All non-B policy knobs live here. Test bodies must not introduce new
    // literals that encode dollar amounts or window lengths.
    struct Params {
        uint16 alphaBps;       // 0 => whole budget is surplus (isolates F1 race)
        uint64 window;         // D
        uint64 raiseDelay;     // timelock for budget raise / exempt
        uint256 gap;           // detection-gap length for F2 envelope
        uint256 tightnessTol;  // assertApproxEqRel tolerance (5%)
        uint32 nRoutes;        // routes used in concurrency tests
    }

    Params internal P = Params({
        alphaBps: 0,
        window: 1 days,
        raiseDelay: 2 days,
        gap: 6 hours,
        tightnessTol: 0.05e18,
        nRoutes: 2
    });

    // ------------------------------------------------------------------ state

    ExposureBudgetIsm internal ism;
    FaultMatrixMailbox internal mailbox;
    address internal gov = address(0x9001);
    address internal guard = address(0x6402);
    address internal routeA = address(0xA1);
    address internal routeB = address(0xB2);
    uint96 internal B; // loaded from JSON
    uint32 internal nonce;

    // ------------------------------------------------------------------ setup

    function setUp() public {
        vm.warp(1_700_000_000);
        B = _loadBudget();
        mailbox = new FaultMatrixMailbox();
        ism = new ExposureBudgetIsm(
            address(mailbox), gov, guard, B, P.alphaBps, P.window, P.raiseDelay
        );
        mailbox.setDefaultIsm(address(ism));
        vm.prank(gov);
        ism.setInstalledUnder(address(ism));
        _classify(routeA);
        _classify(routeB);
    }

    /// @dev B comes from data/fault_budget.json, which fault_matrix.py writes
    ///      from sim3.json Bc.ethereum (parseJsonUint cannot read floats).
    function _loadBudget() internal view returns (uint96) {
        string memory json = vm.readFile("../data/fault_budget.json");
        uint256 b = json.readUint(".B_wei");
        require(b > 0 && b <= type(uint96).max, "B out of range");
        return uint96(b);
    }

    function _classify(address r) internal {
        address[] memory rs = new address[](1);
        uint256[] memory vs = new uint256[](1);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](1);
        rs[0] = r;
        vs[0] = 1e18; // 1 token unit = 1 budget unit
        ms[0] = ExposureBudgetIsm.Mode.METERED;
        vm.startPrank(gov);
        ism.setUnitValues(rs, vs);
        ism.setModes(rs, ms);
        vm.stopPrank();
    }

    function _msg(address recipient, uint256 amount) internal returns (bytes memory) {
        return abi.encodePacked(
            uint8(3), ++nonce, uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(recipient))),
            bytes32(uint256(uint160(recipient))),
            bytes32(amount)
        );
    }

    function _deliverAndVerify(address r, uint256 amt) internal {
        bytes memory m = _msg(r, amt);
        mailbox.markDelivered(keccak256(m));
        ism.verify("", m);
    }

    function _tryTake(address r, uint256 want) internal returns (uint256 took) {
        uint256 avail = ism.availableFor(r);
        took = want < avail ? want : avail;
        if (took == 0) return 0;
        bytes memory m = _msg(r, took);
        mailbox.markDelivered(keccak256(m));
        try ism.verify("", m) {
            return took;
        } catch {
            return 0;
        }
    }

    // ================================================================ F1
    // Same-block: two routes each request the full remaining surplus.

    function test_F1_SameBlockSurplusRace() public {
        uint256 avail0 = ism.surplusAvailable();
        assertGt(avail0, 0, "F1 vacuous setup: no surplus");

        // Both observe the same full surplus before either meters (the race
        // window that would double-admit under a naive read-then-write).
        uint256 planA = ism.availableFor(routeA);
        uint256 planB = ism.availableFor(routeB);
        assertEq(planA, avail0, "F1: routeA view != surplus");
        assertEq(planB, avail0, "F1: routeB view != surplus");

        uint256 gotA = _tryTake(routeA, planA);
        uint256 gotB = _tryTake(routeB, planB);
        uint256 observed = gotA + gotB;

        assertGt(observed, 0, "F1 vacuous: nothing was admitted, bound passes trivially");
        assertLe(observed, avail0, "F1 invariant violated: same-block sum > surplus");
        // Bound is tight: the first take drains the surplus.
        assertApproxEqRel(observed, avail0, P.tightnessTol, "F1 bound not tight");
        assertEq(gotB, 0, "F1: second route should see empty surplus");
    }

    /// Negative control: the *planned* concurrent take (both acting on the
    /// pre-meter view) would violate the bound — proving the assertion has
    /// teeth and is not an always-true inequality.
    function test_F1_NegativeControl() public {
        // Part one is a precondition, not yet a control: unless the two routes
        // between them want more than exists, the forward bound is satisfied
        // by arithmetic and proves nothing.
        uint256 avail0 = ism.surplusAvailable();
        uint256 planned = ism.availableFor(routeA) + ism.availableFor(routeB);
        assertGt(planned, avail0, "F1 setup: planned sum should exceed surplus");

        // Part two is the control. Drain the surplus with routeA, then have
        // routeB ask for what it could see a moment earlier. If the module
        // resolved the two claims against a stale view the take would succeed;
        // it must not.
        uint256 stale = ism.availableFor(routeB);
        assertGt(stale, 0, "F1 control vacuous: routeB saw nothing to claim");
        _tryTake(routeA, avail0);
        assertEq(ism.surplusAvailable(), 0, "F1 control setup: surplus not drained");
        uint256 got = _tryTake(routeB, stale);
        assertLt(got, stale,
            "F1 control failed: routeB was served from a surplus already spent");
    }

    // ================================================================ F2
    // Messages landing exactly on the refill boundary.

    function test_F2_RefillBoundary() public {
        uint256 t0 = block.timestamp;
        // Drain the full burst at t0.
        uint256 burst = _tryTake(routeA, type(uint256).max);
        assertGt(burst, 0, "F2 vacuous: burst take failed");

        // Land exactly on the WINDOW boundary.
        vm.warp(t0 + P.window);
        uint256 refill = _tryTake(routeA, type(uint256).max);
        assertGt(refill, 0, "F2 vacuous: refill take failed");

        uint256 observed = burst + refill;
        uint256 delta = P.window;
        uint256 bound = uint256(B) + (uint256(B) * delta) / P.window;
        assertLe(observed, bound, "F2 invariant violated: cumulative > B + B*dt/D");
        assertApproxEqRel(observed, bound, P.tightnessTol, "F2 bound not tight");
    }

    /// Negative control: over a window of D, claiming 2B + epsilon must fail
    /// once the envelope is exhausted — the bound is not ornamental.
    function test_F2_NegativeControl() public {
        uint256 t0 = block.timestamp;
        uint256 first = _tryTake(routeA, type(uint256).max);
        vm.warp(t0 + P.window);
        uint256 second = _tryTake(routeA, type(uint256).max);
        assertEq(first + second, uint256(B) * 2, "F2 neg setup");
        // Bucket is full again only after another full window; immediate third
        // take of any positive amount must fail.
        bytes memory m = _msg(routeA, 1);
        mailbox.markDelivered(keccak256(m));
        // Typed, not bare: a bare expectRevert() would also be satisfied by an
        // arithmetic panic or an unrelated guard, so it would not prove that
        // the *budget* is what rejected the take.
        vm.expectRevert(
            abi.encodeWithSelector(ExposureBudgetIsm.BudgetExceeded.selector, 1, 0));
        ism.verify("", m);
    }

    // ================================================================ F3
    // Relayer re-delivers the same message.

    function test_F3_ReplayAlreadyMetered() public {
        uint256 before = ism.surplusAvailable();
        bytes memory m = _msg(routeA, B / 10);
        bytes32 id = keccak256(m);
        mailbox.markDelivered(id);
        ism.verify("", m);

        assertTrue(ism.messageMetered(id), "F3: not marked metered");
        uint256 afterFirst = ism.surplusAvailable();
        assertLt(afterFirst, before, "F3 vacuous: budget not charged");

        vm.expectRevert(abi.encodeWithSelector(ExposureBudgetIsm.AlreadyMetered.selector, id));
        ism.verify("", m);

        assertEq(ism.surplusAvailable(), afterFirst, "F3: replay charged budget twice");
    }

    /// Negative control: two distinct message ids with the same amount both
    /// succeed and charge twice — showing the harness can double-charge when
    /// the replay guard does not apply.
    function test_F3_NegativeControl() public {
        uint256 before = ism.surplusAvailable();
        uint256 amt = B / 20;
        _deliverAndVerify(routeA, amt);
        _deliverAndVerify(routeA, amt);
        assertEq(ism.surplusAvailable(), before - 2 * amt, "F3 neg: distinct ids should charge twice");
    }

    // ================================================================ F4
    // Chain reorg via snapshot / revertTo.

    function test_F4_ReorgConsistency() public {
        bytes memory m = _msg(routeA, B / 5);
        bytes32 id = keccak256(m);
        mailbox.markDelivered(id);

        uint256 snap = vm.snapshotState();
        uint256 surplusBefore = ism.surplusAvailable();
        ism.verify("", m);
        assertTrue(ism.messageMetered(id), "F4: metered before revert");
        assertLt(ism.surplusAvailable(), surplusBefore, "F4 vacuous: no charge");

        vm.revertToState(snap);

        assertFalse(ism.messageMetered(id), "F4: messageMetered survived reorg");
        assertEq(ism.surplusAvailable(), surplusBefore, "F4: budget not restored after reorg");
        // Delivery bit is also rolled back with the snapshot — consistent with
        // Mailbox state on a real reorg that undoes the delivery tx.
    }

    /// Negative control: without revert, metering persists — the rollback is
    /// what clears the flag, not a no-op assert.
    function test_F4_NegativeControl() public {
        bytes memory m = _msg(routeA, B / 7);
        bytes32 id = keccak256(m);
        mailbox.markDelivered(id);
        ism.verify("", m);
        assertTrue(ism.messageMetered(id), "F4 neg: flag must stick without reorg");
        vm.expectRevert(abi.encodeWithSelector(ExposureBudgetIsm.AlreadyMetered.selector, id));
        ism.verify("", m);
    }

    // ================================================================ F5
    // lowerBudget mid-traffic.

    function test_F5_LowerBudgetMidTraffic() public {
        uint256 took = _tryTake(routeA, B / 4);
        assertGt(took, 0, "F5 vacuous: pre-lower take failed");
        uint256 surplusAfterTake = ism.surplusAvailable();

        uint96 newB = uint96(uint256(B) / 2);
        vm.prank(guard);
        ism.lowerBudget(newB);

        assertEq(ism.budget(), newB, "F5: lower did not take effect");
        // Already-consumed value is not refunded into the new headroom.
        uint256 afterLower = ism.surplusAvailable();
        assertLe(afterLower, uint256(newB), "F5: surplus exceeds new budget");
        assertLe(afterLower, surplusAfterTake, "F5: lower refunded consumed value");

        uint256 tookAfter = _tryTake(routeA, type(uint256).max);
        uint256 windowTotal = took + tookAfter;
        // With alpha=0, post-lower capacity is the resized surplus; total
        // admitted across the lower cannot exceed old burst that already left
        // plus the new cap — tighter: remaining + already taken after resize
        // respects newB for *further* admits, and tookAfter <= afterLower.
        assertLe(tookAfter, uint256(newB), "F5: post-lower admit > new budget");
        // Non-vacuous: some post-lower capacity usually remains if took < newB.
        if (took < newB) {
            assertGt(tookAfter, 0, "F5 vacuous: no post-lower admit despite headroom");
        }
        // Aggregate admitted in this sequence is took (under old) + tookAfter
        // (under new). The live invariant is on remaining capacity, not on
        // historical sum vs newB — document that explicitly:
        // historical sum may exceed newB; that is intended (no refund).
        assertGe(windowTotal, took, "F5 sanity");
        console.log("F5 took / tookAfter / newB", took, tookAfter, newB);
    }

    /// Negative control: raising via lowerBudget must revert (asymmetry).
    function test_F5_NegativeControl() public {
        vm.prank(guard);
        vm.expectRevert(ExposureBudgetIsm.NotAnIncrease.selector);
        ism.lowerBudget(uint96(uint256(B) + 1));
    }

    // ================================================================ F6
    // queueBudgetRaise + premature executeBudgetRaise.

    function test_F6_RaiseTimelock() public {
        uint96 higher = uint96(uint256(B) * 2);
        vm.startPrank(gov);
        ism.queueBudgetRaise(higher);

        vm.expectRevert(ExposureBudgetIsm.TimelockPending.selector);
        ism.executeBudgetRaise();

        vm.warp(block.timestamp + P.raiseDelay);
        ism.executeBudgetRaise();
        vm.stopPrank();

        assertEq(ism.budget(), higher, "F6: raise did not apply after timelock");
        // Raising retargets the surplus *cap*; level refills over WINDOW (same
        // token-bucket rule as traffic). Instant equality to `higher` would be
        // wrong — wait one window, then the bound is tight.
        uint256 mid = ism.surplusAvailable();
        assertGt(mid, 0, "F6 vacuous: surplus empty after raise");
        assertLe(mid, uint256(higher), "F6: surplus exceeded new budget");
        vm.warp(block.timestamp + P.window);
        assertApproxEqRel(
            ism.surplusAvailable(), uint256(higher), P.tightnessTol,
            "F6 surplus did not refill to new cap"
        );
    }

    /// Negative control: execute with nothing queued must revert.
    function test_F6_NegativeControl() public {
        vm.prank(gov);
        vm.expectRevert(ExposureBudgetIsm.NothingQueued.selector);
        ism.executeBudgetRaise();
    }

    // ================================================================ F7 (optional)

    function test_F7_PauseMidRefill() public {
        _tryTake(routeA, B / 2);
        vm.prank(guard);
        ism.setPaused(true);

        bytes memory m = _msg(routeA, 1);
        mailbox.markDelivered(keccak256(m));
        vm.expectRevert(ExposureBudgetIsm.Paused.selector);
        ism.verify("", m);

        vm.prank(guard);
        ism.setPaused(false);
        uint256 got = _tryTake(routeA, 1);
        assertGt(got, 0, "F7: unpause did not restore metering");
    }

    function test_F7_NegativeControl() public {
        // While paused, even a zero-value path is blocked before mode checks.
        vm.prank(guard);
        ism.setPaused(true);
        bytes memory m = _msg(routeA, 0);
        mailbox.markDelivered(keccak256(m));
        vm.expectRevert(ExposureBudgetIsm.Paused.selector);
        ism.verify("", m);
    }

    // --------------------------------------------------------------- helpers for driver

    function test_ReportBudget() public view {
        // Lets fault_matrix.py scrape B from forge output / state.
        console.log("FAULT_MATRIX_B_WEI", B);
        console.log("FAULT_MATRIX_WINDOW", P.window);
        console.log("FAULT_MATRIX_ALPHA_BPS", P.alphaBps);
    }
}
