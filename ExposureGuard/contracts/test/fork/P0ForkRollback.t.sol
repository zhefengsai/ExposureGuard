// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test, console} from "forge-std/Test.sol";
import {ExposureBudgetIsm} from "../../src/ExposureBudgetIsm.sol";

interface IMailboxFull {
    function VERSION() external view returns (uint8);
    function localDomain() external view returns (uint32);
    function owner() external view returns (address);
    function delivered(bytes32) external view returns (bool);
    function processedAt(bytes32) external view returns (uint48);
    function setDefaultIsm(address) external;
    function process(bytes calldata metadata, bytes calldata message) external payable;
}

contract RollbackRecipient {
    uint256 public calls;

    function handle(uint32, bytes32, bytes calldata) external payable {
        calls++;
    }
}

/// Final-build check: Mailbox.process() rejection rolls delivery, metering,
/// and budget back together, and the same message succeeds after refill.
contract P0ForkRollbackTest is Test {
    address constant MAILBOX = 0xc005dc82818d67AF737725bD4bf75435d065D239;
    uint256 constant FORK_BLOCK = 25_137_902;

    IMailboxFull mb;
    ExposureBudgetIsm ism;
    RollbackRecipient recipient;

    uint96 constant BUDGET = 150_000e18;
    uint256 constant DRAIN = 120_000e18;
    uint256 constant DEFERRED = 40_000e18;

    function setUp() public {
        vm.createSelectFork(vm.envString("ETH_RPC_URL"), FORK_BLOCK);
        mb = IMailboxFull(MAILBOX);
        recipient = new RollbackRecipient();
        ism = new ExposureBudgetIsm(
            MAILBOX, address(this), address(this), BUDGET, 8_000, 1 days, 2 days
        );

        address[] memory rs = new address[](1);
        uint16[] memory w = new uint16[](1);
        uint256[] memory v = new uint256[](1);
        ExposureBudgetIsm.Mode[] memory modes = new ExposureBudgetIsm.Mode[](1);
        rs[0] = address(recipient);
        w[0] = 10_000;
        v[0] = 1e18;
        modes[0] = ExposureBudgetIsm.Mode.METERED;
        ism.setFloors(rs, w);
        ism.setUnitValues(rs, v);
        ism.setModes(rs, modes);

        vm.prank(mb.owner());
        mb.setDefaultIsm(address(ism));
        ism.setInstalledUnder(address(ism));
    }

    function _message(uint32 nonce, uint256 amount) internal view returns (bytes memory) {
        return abi.encodePacked(
            mb.VERSION(),
            nonce,
            uint32(1234),
            bytes32(uint256(0xBEEF)),
            mb.localDomain(),
            bytes32(uint256(uint160(address(recipient)))),
            bytes32(uint256(uint160(address(recipient)))),
            bytes32(amount)
        );
    }

    function test_OverBudgetRollsBackAndRedeliversAfterRefill() public {
        mb.process("", _message(20, DRAIN));
        assertEq(recipient.calls(), 1, "drain delivery");

        bytes memory deferred = _message(21, DEFERRED);
        bytes32 id = keccak256(deferred);
        assertLt(ism.availableFor(address(recipient)), DEFERRED, "must be over budget");

        bool deliveredBefore = mb.delivered(id);
        uint48 processedBefore = mb.processedAt(id);
        bool meteredBefore = ism.messageMetered(id);
        uint256 floorBefore = ism.floorAvailable(address(recipient));
        uint256 surplusBefore = ism.surplusAvailable();
        uint256 callsBefore = recipient.calls();

        vm.expectRevert();
        mb.process("", deferred);

        assertEq(mb.delivered(id), deliveredBefore, "delivery record");
        assertEq(mb.processedAt(id), processedBefore, "processedAt");
        assertEq(ism.messageMetered(id), meteredBefore, "meter");
        assertEq(ism.floorAvailable(address(recipient)), floorBefore, "floor");
        assertEq(ism.surplusAvailable(), surplusBefore, "surplus");
        assertEq(recipient.calls(), callsBefore, "handle");
        assertFalse(mb.delivered(id));
        assertEq(processedBefore, 0);
        assertFalse(meteredBefore);

        vm.warp(block.timestamp + 1 days);
        vm.roll(block.number + 7200);

        uint256 floorAtRetry = ism.floorAvailable(address(recipient));
        uint256 surplusAtRetry = ism.surplusAvailable();
        mb.process("", deferred);
        uint256 gasUsed = vm.lastCallGas().gasTotalUsed;

        assertTrue(mb.delivered(id), "redelivered");
        assertEq(mb.processedAt(id), uint48(block.number), "processed in this block");
        assertTrue(ism.messageMetered(id), "metered once");
        assertEq(recipient.calls(), callsBefore + 1, "handle once");
        uint256 charged =
            (floorAtRetry + surplusAtRetry)
            - (ism.floorAvailable(address(recipient)) + ism.surplusAvailable());
        assertEq(charged, DEFERRED, "charged once");

        console.log("fork_block", FORK_BLOCK);
        console.log("retry_gas", gasUsed);
        console.log("floor_before_reject", floorBefore);
        console.log("surplus_before_reject", surplusBefore);

        vm.writeFile(
            string.concat(vm.projectRoot(), "/../data/p0_fork_rollback.json"),
            string.concat(
                "{\n",
                ' "verdict": "PASS",\n',
                ' "fork_block": ', vm.toString(FORK_BLOCK), ",\n",
                ' "retry_process_gas": ', vm.toString(gasUsed), ",\n",
                ' "delivered_after_reject": false,\n',
                ' "processed_at_after_reject": 0,\n',
                ' "metered_after_reject": false,\n',
                ' "floor_unchanged": true,\n',
                ' "surplus_unchanged": true,\n',
                ' "handle_on_reject": false,\n',
                ' "redelivery": "PASS",\n',
                ' "charged_once": true,\n',
                ' "handle_on_redelivery": 1\n',
                "}\n"
            )
        );
    }
}
