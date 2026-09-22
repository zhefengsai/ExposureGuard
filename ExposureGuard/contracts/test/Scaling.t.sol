// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test, console} from "forge-std/Test.sol";
import {stdJson} from "forge-std/StdJson.sol";
import {ExposureBudgetIsm} from "../src/ExposureBudgetIsm.sol";

contract MockMailbox {
    mapping(bytes32 => bool) public delivered;
    function markDelivered(bytes32 id) external { delivered[id] = true; }
}

/// Does cost grow with the number of routes sharing the root?
///
/// The paper claims verify() is O(1) in |R(S)| and that a budget change
/// rescales every floor in constant time. Both are claims about scaling that
/// a trace-driven simulation cannot check. This measures them, and also
/// reports the one operation that is NOT constant, so the disclosure is
/// complete rather than flattering.
contract ScalingTest is Test {
    using stdJson for string;

    struct Params { uint16 alphaBps; uint64 window; uint64 raiseDelay; }
    Params P = Params({alphaBps: 8_000, window: 1 days, raiseDelay: 2 days});

    address gov = address(0x9001);
    uint32 nonce;

    function _budget() internal view returns (uint96) {
        string memory j = vm.readFile("../data/fault_budget.json");
        uint256 b = j.readUint(".B_wei");
        require(b > 0 && b <= type(uint96).max, "B out of range");
        return uint96(b);
    }

    function _route(uint256 i) internal pure returns (address) {
        return address(uint160(0x10000 + i));
    }

    /// Deploy a module with `n` classified, floor-bearing routes.
    function _withRoutes(uint256 n)
        internal
        returns (ExposureBudgetIsm ism, MockMailbox mb)
    {
        mb = new MockMailbox();
        ism = new ExposureBudgetIsm(address(mb), gov, gov, _budget(),
                                    P.alphaBps, P.window, P.raiseDelay);
        address[] memory rs = new address[](n);
        uint256[] memory vs = new uint256[](n);
        uint16[] memory ws = new uint16[](n);
        ExposureBudgetIsm.Mode[] memory ms = new ExposureBudgetIsm.Mode[](n);
        for (uint256 i; i < n; ++i) {
            rs[i] = _route(i); vs[i] = 1e18;
            ms[i] = ExposureBudgetIsm.Mode.METERED;
            ws[i] = uint16(10_000 / n);           // equal split, sums <= 10_000
        }
        vm.startPrank(gov);
        ism.setUnitValues(rs, vs);
        ism.setModes(rs, ms);
        ism.setFloors(rs, ws);
        vm.stopPrank();
    }

    function _verifyGas(ExposureBudgetIsm ism, MockMailbox mb, address r)
        internal returns (uint256)
    {
        bytes memory m = abi.encodePacked(
            uint8(3), ++nonce, uint32(1), bytes32(0), uint32(2),
            bytes32(uint256(uint160(r))), bytes32(uint256(uint160(r))),
            bytes32(uint256(1e18)));
        mb.markDelivered(keccak256(m));
        uint256 g0 = gasleft();
        ism.verify("", m);
        return g0 - gasleft();
    }

    /// verify() must not grow with |R(S)|.
    function test_VerifyGasFlatInTenantCount() public {
        uint256[4] memory ns = [uint256(10), 100, 500, 1000];
        uint256 first; uint256 last;
        console.log("routes | verify gas");
        for (uint256 k; k < ns.length; ++k) {
            (ExposureBudgetIsm ism, MockMailbox mb) = _withRoutes(ns[k]);
            uint256 g = _verifyGas(ism, mb, _route(ns[k] / 2));
            console.log(ns[k], g);
            assertGt(g, 0, "vacuous: verify consumed no gas");
            if (k == 0) first = g;
            if (k == ns.length - 1) last = g;
        }
        // 100x more tenants must not move per-message cost by more than 5%.
        assertApproxEqRel(last, first, 0.05e18,
            "verify() is not O(1) in the number of routes sharing the root");
    }

    /// Changing the budget must not touch per-route storage.
    function test_BudgetChangeConstantInTenantCount() public {
        uint256[3] memory ns = [uint256(10), 500, 1000];
        uint256 first; uint256 last;
        console.log("routes | lowerBudget gas");
        for (uint256 k; k < ns.length; ++k) {
            (ExposureBudgetIsm ism,) = _withRoutes(ns[k]);
            uint96 lower = uint96(uint256(_budget()) / 2);
            vm.prank(gov);
            uint256 g0 = gasleft();
            ism.lowerBudget(lower);
            uint256 g = g0 - gasleft();
            console.log(ns[k], g);
            assertGt(g, 0, "vacuous: lowerBudget consumed no gas");
            if (k == 0) first = g;
            if (k == ns.length - 1) last = g;
        }
        assertApproxEqRel(last, first, 0.05e18,
            "budget change is not constant in the number of floors");
    }

    /// The operation that is NOT constant. Reported, not hidden.
    function test_SetFloorsIsLinearInBatch() public {
        // A bare module: no floors provisioned, so the whole weight pool is
        // free and successive batches do not trip the 10,000 bps cap.
        MockMailbox mb = new MockMailbox();
        ExposureBudgetIsm ism = new ExposureBudgetIsm(
            address(mb), gov, gov, _budget(), P.alphaBps, P.window, P.raiseDelay);
        uint256[3] memory ks = [uint256(10), 100, 1000];
        uint256 g10; uint256 g1000;
        console.log("batch | setFloors gas");
        for (uint256 i; i < ks.length; ++i) {
            uint256 n = ks[i];
            address[] memory rs = new address[](n);
            uint16[] memory ws = new uint16[](n);
            for (uint256 j; j < n; ++j) {
                rs[j] = address(uint160(0x900000 + i * 100000 + j));
                ws[j] = 1;                      // 1 bps each, sums <= 10_000
            }
            vm.prank(gov);
            uint256 g0 = gasleft();
            ism.setFloors(rs, ws);
            uint256 g = g0 - gasleft();
            console.log(n, g);
            assertGt(g, 0, "vacuous: setFloors consumed no gas");
            if (n == 10) g10 = g;
            if (n == 1000) g1000 = g;
            // undo so weights stay under the cap
            for (uint256 j; j < n; ++j) ws[j] = 0;
            vm.prank(gov);
            ism.setFloors(rs, ws);
        }
        // Negative control for the two tests above: this one really does grow,
        // which proves the flatness assertions are not vacuously satisfied by
        // a measurement harness that cannot detect growth at all.
        assertGt(g1000, g10 * 10,
            "setFloors should be linear in batch size; if it is flat the "
            "harness is not measuring growth and the O(1) claims are untested");
    }
}
