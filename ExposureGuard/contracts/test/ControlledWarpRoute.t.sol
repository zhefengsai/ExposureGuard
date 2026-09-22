// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ControlledWarpRoute} from "../src/ControlledWarpRoute.sol";

contract RouteMailboxFixture {
    bytes public lastBody;
    function quoteDispatch(uint32, bytes32, bytes calldata) external pure returns (uint256) { return 7; }
    function dispatch(uint32, bytes32, bytes calldata message) external payable returns (bytes32) {
        lastBody = message;
        return keccak256(message);
    }
    function deliver(address route, uint32 origin, bytes32 sender, bytes calldata message) external {
        ControlledWarpRoute(route).handle(origin, sender, message);
    }
}

contract ControlledWarpRouteTest is Test {
    RouteMailboxFixture mailbox;
    ControlledWarpRoute route;

    function setUp() public {
        mailbox = new RouteMailboxFixture();
        route = new ControlledWarpRoute(address(mailbox));
        route.setRemote(1, bytes32(uint256(9)));
    }

    function testControlledSendAndReceive() public {
        bytes32 id = route.send(2, bytes32(uint256(3)), 42);
        assertEq(id, keccak256(route.body(42)));
        mailbox.deliver(address(route), 1, bytes32(uint256(9)), route.body(42));
        assertEq(route.receivedCount(), 1);
        assertEq(route.receivedAmount(), 42);
    }

    function testRejectsUntrustedRemote() public {
        bytes memory message = route.body(42);
        vm.expectRevert(ControlledWarpRoute.UntrustedRemote.selector);
        vm.prank(address(mailbox));
        route.handle(1, bytes32(uint256(10)), message);
    }
}
