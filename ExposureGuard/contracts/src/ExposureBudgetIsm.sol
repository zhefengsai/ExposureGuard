// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity ^0.8.24;

import {IInterchainSecurityModule} from "./IInterchainSecurityModule.sol";

interface IMailbox {
    function delivered(bytes32 messageId) external view returns (bool);
    function defaultIsm() external view returns (address);
}

interface IAggregationIsm {
    function modulesAndThreshold(bytes calldata message)
        external
        view
        returns (address[] memory, uint8);
}

/**
 * @title ExposureBudgetIsm
 * @notice A per-shared-root exposure budget, enforced on the destination chain.
 *
 * Every route that inherits a Mailbox's default ISM shares one verification
 * root, so a single root failure exposes all of them at once. Deployed limiters
 * are scoped per route (`RateLimitedIsm` binds `recipient` at construction and
 * is therefore unshareable), which bounds each route but leaves the aggregate
 * unbounded and growing with the route count. This module meters the shared
 * root instead.
 *
 * Design decisions, each forced by a measured constraint:
 *
 *  - Floors are USAGE-WEIGHTED, not equal-share. Under the measured demand skew
 *    (Gini 0.93) equal floors are strictly worse than no reservation at all,
 *    and they are Sybil-mintable: a freshly deployed route would be granted a
 *    floor on arrival. Usage-weighted floors are earned from history, so
 *    deploying N Sybil routes yields nothing.
 *
 *  - Value is priced from a GOVERNANCE-SET table, not an oracle. Most routes
 *    carry micro-cap tokens with no feed, and DEX spot for those is cheap to
 *    push. Accounting prices are policy parameters like the budget itself, so
 *    they add no new trust assumption and expose no manipulable price source.
 *    Over-pricing is safe (it consumes more budget); under-pricing is not.
 *
 *  - Budget changes are ASYMMETRIC. Lowering the budget or pausing takes effect
 *    immediately; raising it must sit in a timelock. A compromised governance
 *    key can then only make the system more conservative, not quickly relax it.
 *
 * Aggregate invariant: over any window `D`, the accounted value admitted across
 * all METERED routes sharing this module is at most `budget`.
 *
 * SCOPE. The bound is on value declared in the message, i.e. value crossing the
 * bridge. It is NOT a bound on realised loss: a forged mint that is subsequently
 * used as collateral downstream can extract more than it declared. Loss is
 * therefore bounded by `budget * A`, where `A` is the downstream amplification
 * available to the minted asset. EXEMPT recipients are outside the bound
 * entirely, which is why classification is a governance act and not a default.
 */
contract ExposureBudgetIsm is IInterchainSecurityModule {
    // ------------------------------------------------------------------ types

    /// @dev One slot. `level` is in budget units; `updatedAt` in seconds.
    ///      Floor capacity is NOT stored: it is derived from the route's weight
    ///      and the live budget, so a budget change rescales every floor in O(1)
    ///      and can never let the floors outgrow their share.
    struct Bucket {
        uint96 cap;
        uint96 level;
        uint64 updatedAt;
    }

    /// @notice How a recipient is treated. Both non-default states are set
    ///         explicitly by governance; UNSET fails closed.
    ///
    ///  UNSET   the recipient has never been classified -> reject. Failing open
    ///          here would let an adversary route value through any recipient
    ///          governance has not yet priced, which voids the budget entirely.
    ///  METERED value-bearing route: the body must parse as a TokenMessage and
    ///          the declared amount is charged against the budget.
    ///  EXEMPT  recipient carries no value legible in the message body (plain
    ///          GMP). Passed through unmetered; see the scope note below.
    enum Mode {
        UNSET,
        METERED,
        EXEMPT
    }

    /// @dev Floor state for one route: a weight (bps of the reserved pool) plus
    ///      the consumed-level bookkeeping.
    struct Floor {
        uint16 weightBps;
        uint96 level;
        uint64 updatedAt;
    }

    // ------------------------------------------------------------ immutables

    /// @notice Refill window in seconds; a bucket refills from 0 to `cap` over it.
    uint64 public immutable WINDOW;

    /// @notice The Mailbox whose delivery record gates metering. Without this
    ///         `verify` is world-callable and the budget can be zeroed for the
    ///         price of one transaction, with no capital at risk.
    address public immutable MAILBOX;

    // -------------------------------------------------------------- storage

    address public governance;
    address public guardian; // may pause and lower, may not raise

    /// @notice Total per-root budget per window, in budget units.
    uint96 public budget;
    /// @notice Reservation fraction in basis points (8000 = 0.8).
    uint16 public alphaBps;
    bool public paused;

    /// @notice Work-conserving surplus shared by every route.
    Bucket internal _surplus;

    /// @notice Reserved floor per route. The weight is set from measured
    ///         history; a route with no history gets zero, which is what makes
    ///         Sybil deployment worthless.
    mapping(address route => Floor) internal _floor;
    /// @notice Sum of floor weights; capped at 10_000 so the floors can never
    ///         claim more than `alphaBps` of the budget.
    uint32 public totalWeightBps;
    /// @notice Budget units per smallest token unit, 1e18 fixed point.
    mapping(address route => uint256) public unitValue;
    /// @notice Recipient classification. UNSET rejects.
    mapping(address recipient => Mode) public mode;
    /// @notice Replay guard: a message may only be metered once.
    mapping(bytes32 messageId => bool) public messageMetered;
    /// @notice Earliest time a queued EXEMPT classification may be applied.
    mapping(address recipient => uint64) public exemptEta;

    /// @notice Pending budget increase; increases are timelocked.
    uint96 public pendingBudget;
    uint64 public pendingEta;
    uint64 public immutable RAISE_DELAY;

    // --------------------------------------------------------------- events

    event Consumed(address indexed route, uint256 value, uint96 floorLeft, uint96 surplusLeft);
    event BudgetLowered(uint96 oldBudget, uint96 newBudget);
    event BudgetRaiseQueued(uint96 newBudget, uint64 eta);
    event BudgetRaised(uint96 oldBudget, uint96 newBudget);
    event FloorSet(address indexed route, uint16 weightBps);
    event UnitValueSet(address indexed route, uint256 value);
    event ModeSet(address indexed recipient, Mode mode);
    event ExemptQueued(address indexed recipient, uint64 eta);
    event PausedSet(bool paused);

    // --------------------------------------------------------------- errors

    error NotGovernance();
    error NotGuardianOrGovernance();
    error Paused();
    error BudgetExceeded(uint256 need, uint256 available);
    error NotAnIncrease();
    error TimelockPending();
    error NothingQueued();
    error AlphaTooLarge();
    error MalformedMessage();
    error WeightOverflow(uint32 total);
    error LengthMismatch();
    error UnclassifiedRecipient(address recipient);
    error UnpricedRoute(address route);
    error MessageNotDelivered(bytes32 messageId);
    error AlreadyMetered(bytes32 messageId);
    error ExemptMustBeQueued();
    error ExemptNotReady(address recipient);

    // ---------------------------------------------------------- constructor

    constructor(
        address _mailbox,
        address _governance,
        address _guardian,
        uint96 _budget,
        uint16 _alphaBps,
        uint64 _window,
        uint64 _raiseDelay
    ) {
        if (_alphaBps > 10_000) revert AlphaTooLarge();
        MAILBOX = _mailbox;
        governance = _governance;
        guardian = _guardian;
        budget = _budget;
        alphaBps = _alphaBps;
        WINDOW = _window;
        RAISE_DELAY = _raiseDelay;

        uint96 surplusCap = uint96((uint256(_budget) * (10_000 - _alphaBps)) / 10_000);
        _surplus = Bucket({cap: surplusCap, level: surplusCap, updatedAt: uint64(block.timestamp)});
        // Invariant: surplus cap + sum of floor caps == budget, because floor
        // weights are capped at 10_000 bps of alphaBps * budget.
    }

    // ------------------------------------------------------------- modifiers

    modifier onlyGovernance() {
        if (msg.sender != governance) revert NotGovernance();
        _;
    }

    // ------------------------------------------------------------------ ISM

    function moduleType() external pure returns (uint8) {
        return uint8(Types.NULL);
    }

    /**
     * @notice Meter one inbound message against the shared-root budget.
     * @dev Compose this alongside the real verifying module in an Aggregation
     *      ISM; this module authenticates nothing, it only bounds exposure.
     */
    function verify(bytes calldata, bytes calldata message)
        external
        returns (bool)
    {
        if (paused) revert Paused();

        // Two guards, both mirroring the deployed RateLimitedIsm. Without them
        // `verify` is world-callable: anyone could fabricate a message, meter an
        // arbitrary amount and zero the shared budget for the cost of gas, with
        // no capital at risk and no root compromise required.
        bytes32 id = keccak256(message);
        if (messageMetered[id]) revert AlreadyMetered(id);
        if (!IMailbox(MAILBOX).delivered(id)) revert MessageNotDelivered(id);
        messageMetered[id] = true;

        address recipient = _recipient(message);
        Mode m = mode[recipient];

        // Fail closed. An unclassified recipient is the one thing an adversary
        // holding the root would look for, so it must not pass.
        if (m == Mode.UNSET) revert UnclassifiedRecipient(recipient);
        if (m == Mode.EXEMPT) return true;

        uint256 price = unitValue[recipient];
        // A METERED route with no price would meter every transfer as zero.
        if (price == 0) revert UnpricedRoute(recipient);

        uint256 amount = _amount(message);
        uint256 value = (amount * price) / 1e18;
        if (value != 0) _consume(recipient, value);
        return true;
    }

    // ------------------------------------------------------------- internals

    /// @dev Hyperlane message: 1B version | 4B nonce | 4B origin | 32B sender |
    ///      4B destination | 32B recipient | body. Body is a TokenMessage:
    ///      32B recipient | 32B amount | metadata.
    function _recipient(bytes calldata message) internal pure returns (address) {
        // header is 77 bytes; recipient occupies [45, 77), address in the low 20
        if (message.length < 77) revert MalformedMessage();
        return address(bytes20(message[57:77]));
    }

    /// @dev Only called for METERED recipients, whose body must be a
    ///      TokenMessage: 32B recipient | 32B amount | metadata.
    function _amount(bytes calldata message) internal pure returns (uint256) {
        if (message.length < 77 + 64) revert MalformedMessage();
        return uint256(bytes32(message[77 + 32:77 + 64]));
    }

    function _consume(address route, uint256 value) internal {
        Floor memory f = _floor[route];
        uint256 taken;

        if (f.weightBps != 0) {
            (uint256 lvl, ) = _refilledFloor(f);
            taken = lvl < value ? lvl : value;
            f.level = uint96(lvl - taken);
            f.updatedAt = uint64(block.timestamp);
            _floor[route] = f;
        }

        uint96 surplusLeft;
        if (taken < value) {
            uint256 need = value - taken;
            Bucket memory s = _surplus;
            uint256 lvl = _refilled(s);
            if (lvl < need) revert BudgetExceeded(need, lvl);
            s.level = uint96(lvl - need);
            s.updatedAt = uint64(block.timestamp);
            _surplus = s;
            surplusLeft = s.level;
        } else {
            surplusLeft = _surplus.level;
        }

        emit Consumed(route, value, f.level, surplusLeft);
    }

    function _refilled(Bucket memory b) internal view returns (uint256) {
        if (b.cap == 0) return 0;
        uint256 elapsed = block.timestamp - b.updatedAt;
        uint256 lvl = uint256(b.level) + (elapsed * b.cap) / WINDOW;
        return lvl > b.cap ? b.cap : lvl;
    }

    /// @dev A route's reserved capacity, derived so budget changes rescale it.
    function _floorCap(uint16 weightBps) internal view returns (uint256) {
        if (weightBps == 0) return 0;
        return (uint256(budget) * alphaBps * weightBps) / 1e8;
    }

    function _refilledFloor(Floor memory f) internal view returns (uint256 lvl, uint256 cap) {
        cap = _floorCap(f.weightBps);
        if (cap == 0) return (0, 0);
        uint256 elapsed = block.timestamp - f.updatedAt;
        lvl = uint256(f.level) + (elapsed * cap) / WINDOW;
        if (lvl > cap) lvl = cap;
    }

    // ------------------------------------------------------------ views

    /// @notice Whether this module is installed such that its rejection can
    ///         actually stop delivery.
    /// @dev A StaticAggregationIsm with threshold < module count would let the
    ///      other sub-modules satisfy verification on their own, silently
    ///      demoting this one to advisory. Nothing else on-chain looks wrong
    ///      when that happens, so it is exposed here for monitoring.
    function installedSoundly() external view returns (bool) {
        address def = IMailbox(MAILBOX).defaultIsm();
        if (def == address(this)) return true;
        try IAggregationIsm(def).modulesAndThreshold("") returns (
            address[] memory mods, uint8 threshold
        ) {
            bool present;
            for (uint256 i; i < mods.length; ++i) {
                if (mods[i] == address(this)) {
                    present = true;
                    break;
                }
            }
            // every sub-module must be required, otherwise this one is optional
            return present && threshold == mods.length;
        } catch {
            return false;
        }
    }

    function surplusAvailable() external view returns (uint256) {
        return _refilled(_surplus);
    }

    function floorAvailable(address route) external view returns (uint256) {
        (uint256 lvl, ) = _refilledFloor(_floor[route]);
        return lvl;
    }

    function floorCapOf(address route) external view returns (uint256) {
        return _floorCap(_floor[route].weightBps);
    }

    /// @notice Total value admissible right now: this route's floor plus surplus.
    function availableFor(address route) external view returns (uint256) {
        (uint256 lvl, ) = _refilledFloor(_floor[route]);
        return lvl + _refilled(_surplus);
    }

    // -------------------------------------------------------- governance

    /// @notice Floors are provisioned from measured history; a route with no
    ///         history gets none, which is what makes Sybil deployment useless.
    function setFloors(address[] calldata routes, uint16[] calldata weightsBps)
        external
        onlyGovernance
    {
        if (routes.length != weightsBps.length) revert LengthMismatch();
        uint32 total = totalWeightBps;
        for (uint256 i; i < routes.length; ++i) {
            Floor storage f = _floor[routes[i]];
            uint16 old = f.weightBps;
            total = total - old + weightsBps[i];
            f.weightBps = weightsBps[i];
            // A newly reserved floor starts full, matching the convention of
            // the deployed RateLimited library. This cannot mint capacity
            // beyond the budget because the weights are capped at 10_000 bps
            // of the reserved pool, which is itself carved out of the budget.
            if (old == 0 && weightsBps[i] != 0) {
                f.level = uint96(_floorCap(weightsBps[i]));
            }
            f.updatedAt = uint64(block.timestamp);
            emit FloorSet(routes[i], weightsBps[i]);
        }
        if (total > 10_000) revert WeightOverflow(total);
        totalWeightBps = total;
    }

    function setUnitValues(address[] calldata routes, uint256[] calldata values)
        external
        onlyGovernance
    {
        if (routes.length != values.length) revert LengthMismatch();
        for (uint256 i; i < routes.length; ++i) {
            unitValue[routes[i]] = values[i];
            emit UnitValueSet(routes[i], values[i]);
        }
    }

    /// @notice Classify recipients. Every recipient on the chain must be
    ///         classified before it can receive, which is the governance cost
    ///         of failing closed; it is reported in the evaluation.
    function setModes(address[] calldata recipients, Mode[] calldata modes)
        external
        onlyGovernance
    {
        if (recipients.length != modes.length) revert LengthMismatch();
        for (uint256 i; i < recipients.length; ++i) {
            // EXEMPT removes a recipient from the bound, so it loosens the
            // guarantee and follows the same asymmetry as raising the budget.
            if (modes[i] == Mode.EXEMPT) revert ExemptMustBeQueued();
            mode[recipients[i]] = modes[i];
            emit ModeSet(recipients[i], modes[i]);
        }
    }

    /// @notice Queue an EXEMPT classification. Marking a recipient EXEMPT is an
    ///         assertion that it moves no value, which this contract cannot
    ///         check; the delay exists so the assertion can be reviewed.
    function queueExempt(address[] calldata recipients) external onlyGovernance {
        uint64 eta = uint64(block.timestamp) + RAISE_DELAY;
        for (uint256 i; i < recipients.length; ++i) {
            exemptEta[recipients[i]] = eta;
            emit ExemptQueued(recipients[i], eta);
        }
    }

    function executeExempt(address[] calldata recipients) external onlyGovernance {
        for (uint256 i; i < recipients.length; ++i) {
            uint64 eta = exemptEta[recipients[i]];
            if (eta == 0 || block.timestamp < eta) {
                revert ExemptNotReady(recipients[i]);
            }
            exemptEta[recipients[i]] = 0;
            mode[recipients[i]] = Mode.EXEMPT;
            emit ModeSet(recipients[i], Mode.EXEMPT);
        }
    }

    /// @notice Lowering the budget is immediate: it can only tighten the bound.
    function lowerBudget(uint96 newBudget) external {
        if (msg.sender != governance && msg.sender != guardian) {
            revert NotGuardianOrGovernance();
        }
        if (newBudget >= budget) revert NotAnIncrease();
        uint96 old = budget;
        budget = newBudget;
        _resizeSurplus(newBudget);
        emit BudgetLowered(old, newBudget);
    }

    /// @notice Raising the budget is timelocked: it can only loosen the bound.
    function queueBudgetRaise(uint96 newBudget) external onlyGovernance {
        if (newBudget <= budget) revert NotAnIncrease();
        pendingBudget = newBudget;
        pendingEta = uint64(block.timestamp) + RAISE_DELAY;
        emit BudgetRaiseQueued(newBudget, pendingEta);
    }

    function executeBudgetRaise() external onlyGovernance {
        if (pendingEta == 0) revert NothingQueued();
        if (block.timestamp < pendingEta) revert TimelockPending();
        uint96 old = budget;
        budget = pendingBudget;
        _resizeSurplus(pendingBudget);
        pendingBudget = 0;
        pendingEta = 0;
        emit BudgetRaised(old, budget);
    }

    /// @notice Pausing is immediate and available to the guardian.
    function setPaused(bool p) external {
        if (msg.sender != governance && msg.sender != guardian) {
            revert NotGuardianOrGovernance();
        }
        paused = p;
        emit PausedSet(p);
    }

    function _resizeSurplus(uint96 newBudget) internal {
        Bucket memory s = _surplus;
        uint96 newCap = uint96((uint256(newBudget) * (10_000 - alphaBps)) / 10_000);
        uint256 lvl = _refilled(s);
        s.cap = newCap;
        s.level = uint96(lvl > newCap ? newCap : lvl);
        s.updatedAt = uint64(block.timestamp);
        _surplus = s;
    }
}
