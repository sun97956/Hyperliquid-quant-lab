// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IIndexFund.sol";

/// @notice Minimal ERC-20 interface for the fund's base asset (e.g. testnet USDC).
interface IERC20Minimal {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title IndexFund — a tokenized index fund driven by an authorized agent.
/// @notice The contract is the fund itself: it holds the base asset, issues and
///         redeems ERC-20 shares at NAV, stores the agent's target weights
///         on-chain, and enforces risk guardrails. Trading is NOT done by this
///         contract — the agent's wallet executes orders on the exchange side
///         (HyperCore) to match the weights recorded here (HyperEVM side).
///
///         Version 1 scope:
///         - deposit / redeem at NAV, with an adjustable redemption fee
///           (the hook for volatility-scaled fees: the agent raises the fee
///           in high-volatility periods, within the on-chain cap);
///         - on-chain target-weight registry with guardrails
///           (single-asset cap, weight-sum check, coin whitelist);
///         - drawdown circuit-breaker (pauses deposits and weight updates);
///         - NAV is based on the agent-reported total asset value once the
///           agent starts reporting (the trading account sits on HyperCore).
///           TODO(v2): read the HyperCore account value directly through
///           HyperEVM precompiles so NAV no longer relies on a reporter.
contract IndexFund is IIndexFund {
    // ---------- Share token (minimal self-contained ERC-20) ----------
    string public name = "AI Index Fund Share";
    string public symbol = "AIDX";
    uint8 public decimals = 6; // matches the base asset (USDC-style, 6 decimals)

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "allowance too low");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(to != address(0), "transfer to zero address");
        require(balanceOf[from] >= amount, "balance too low");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }

    function _mint(address to, uint256 amount) internal {
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function _burn(address from, uint256 amount) internal {
        require(balanceOf[from] >= amount, "balance too low");
        balanceOf[from] -= amount;
        totalSupply -= amount;
        emit Transfer(from, address(0), amount);
    }

    // ---------- Fund state ----------
    IERC20Minimal public immutable asset; // base asset held by the fund
    address public owner;                 // deployer; manages roles and guardrail parameters
    address public agent;                 // authorized weights manager (the AI agent's wallet)

    uint256 public reportedTotalAssets;   // last agent-reported total value (base-asset units)
    bool public valuationReported;        // true once the agent has reported at least once

    // Guardrail parameters (basis points, 10000 = 100%)
    uint256 public maxSingleWeightBps = 5000; // no coin may exceed 50% of the fund
    uint256 public maxDrawdownBps = 2000;     // 20% drawdown from peak NAV trips the breaker
    uint256 public redemptionFeeBps = 0;      // redemption fee; agent may raise it in high volatility
    uint256 public constant MAX_REDEMPTION_FEE_BPS = 500; // on-chain cap: 5%

    // Circuit-breaker state
    uint256 public peakNavPerShare; // highest NAV per share seen (scaled 1e6)
    bool public breakerTripped;

    // Coin whitelist (target weights may only reference these coins)
    mapping(string => bool) public coinAllowed;

    // Current on-chain target weights
    string[] private _weightCoins;
    uint256[] private _weightBps;
    uint256 public weightsUpdatedAt;

    event BreakerReset(uint256 navPerShare);
    event AgentUpdated(address indexed newAgent);
    event CoinWhitelistUpdated(string coin, bool allowed);
    event GuardrailsUpdated(uint256 maxSingleWeightBps, uint256 maxDrawdownBps);
    event RedemptionFeeUpdated(uint256 redemptionFeeBps);

    modifier onlyOwner() {
        require(msg.sender == owner, "only owner");
        _;
    }

    modifier onlyAgent() {
        require(msg.sender == agent, "only agent");
        _;
    }

    constructor(address assetAddress, address agentAddress) {
        require(assetAddress != address(0), "asset is zero address");
        asset = IERC20Minimal(assetAddress);
        owner = msg.sender;
        agent = agentAddress;
    }

    // ---------- Views ----------
    /// @notice Total assets: the agent-reported value once reporting has
    ///         started (it covers the trading account), otherwise the base
    ///         asset actually held by this contract.
    function totalAssets() public view returns (uint256) {
        if (valuationReported) return reportedTotalAssets;
        return asset.balanceOf(address(this));
    }

    /// @notice NAV per share scaled by 1e6; 1e6 (= 1.0) while no shares exist.
    function navPerShare() public view returns (uint256) {
        if (totalSupply == 0) return 1e6;
        return (totalAssets() * 1e6) / totalSupply;
    }

    function getWeights() external view returns (string[] memory coins, uint256[] memory weightsBps, uint256) {
        // Build the return arrays element by element: returning the storage
        // string array directly fails on the default code generator.
        coins = new string[](_weightCoins.length);
        weightsBps = new uint256[](_weightBps.length);
        for (uint256 i = 0; i < _weightCoins.length; i++) {
            coins[i] = _weightCoins[i];
            weightsBps[i] = _weightBps[i];
        }
        return (coins, weightsBps, weightsUpdatedAt);
    }

    // ---------- Investor actions ----------
    /// @notice Deposit base asset; receive shares at the current NAV.
    function deposit(uint256 amount) external returns (uint256 shares) {
        require(!breakerTripped, "circuit breaker tripped");
        require(amount > 0, "amount is zero");
        uint256 assetsBefore = totalAssets();
        require(asset.transferFrom(msg.sender, address(this), amount), "transferFrom failed");
        if (valuationReported) {
            // Keep the reported total in sync with money entering the fund.
            reportedTotalAssets = assetsBefore + amount;
        }
        if (totalSupply == 0) {
            shares = amount; // first deposit: 1 unit of asset = 1 share
        } else {
            shares = (amount * totalSupply) / assetsBefore;
        }
        require(shares > 0, "deposit too small");
        _mint(msg.sender, shares);
        _afterNavChange();
        emit Deposit(msg.sender, amount, shares);
    }

    /// @notice Redeem shares; receive base asset at NAV minus the redemption fee.
    ///         Redemptions stay open while the breaker is tripped, so investors
    ///         can always exit. The fee stays in the fund for remaining holders.
    function redeem(uint256 shares) external returns (uint256 payout) {
        require(shares > 0, "shares is zero");
        require(balanceOf[msg.sender] >= shares, "balance too low");
        uint256 gross = (shares * totalAssets()) / totalSupply;
        uint256 fee = (gross * redemptionFeeBps) / 10000;
        payout = gross - fee;
        _burn(msg.sender, shares);
        if (valuationReported) {
            // The payout leaves the fund's total value; the fee stays in.
            reportedTotalAssets -= gross;
            reportedTotalAssets += fee;
        }
        require(asset.transfer(msg.sender, payout), "transfer failed");
        emit Redeem(msg.sender, shares, payout, fee);
    }

    // ---------- Agent actions ----------
    /// @notice Record new target weights on-chain. Guardrails are enforced
    ///         here, so even a faulty agent cannot push the fund outside the
    ///         agreed bounds: whitelisted coins only, no coin above the
    ///         single-asset cap, weights may not sum to more than 100%
    ///         (any remainder is cash).
    function setWeights(string[] calldata coins, uint256[] calldata weightsBps) external onlyAgent {
        require(!breakerTripped, "circuit breaker tripped");
        require(coins.length == weightsBps.length, "length mismatch");
        require(coins.length > 0, "empty weights");
        uint256 sum = 0;
        for (uint256 i = 0; i < coins.length; i++) {
            require(coinAllowed[coins[i]], "coin not whitelisted");
            require(weightsBps[i] <= maxSingleWeightBps, "single-asset cap exceeded");
            sum += weightsBps[i];
        }
        require(sum <= 10000, "weights sum above 100%");
        // Copy element by element: assigning a calldata string array to
        // storage in one go is not supported by the default code generator.
        delete _weightCoins;
        delete _weightBps;
        for (uint256 i = 0; i < coins.length; i++) {
            _weightCoins.push(coins[i]);
            _weightBps.push(weightsBps[i]);
        }
        weightsUpdatedAt = block.timestamp;
        emit WeightsUpdated(coins, weightsBps, block.timestamp);
    }

    /// @notice Report the fund's total asset value (base-asset units), i.e.
    ///         the value of the trading account plus anything held here.
    function reportTotalAssets(uint256 totalValue) external onlyAgent {
        reportedTotalAssets = totalValue;
        valuationReported = true;
        emit ValuationReported(totalValue, block.timestamp);
        _afterNavChange();
    }

    // ---------- Circuit breaker ----------
    /// @dev After any change that can move the NAV: refresh the peak, and
    ///      trip the breaker if the drawdown from the peak exceeds the limit.
    function _afterNavChange() internal {
        uint256 nav = navPerShare();
        if (nav > peakNavPerShare) {
            peakNavPerShare = nav;
        } else if (peakNavPerShare > 0 && !breakerTripped) {
            uint256 floor = (peakNavPerShare * (10000 - maxDrawdownBps)) / 10000;
            if (nav < floor) {
                breakerTripped = true;
                emit BreakerTripped(nav, peakNavPerShare);
            }
        }
    }

    /// @notice Owner resets the breaker after reviewing the situation;
    ///         the current NAV becomes the new peak.
    function resetBreaker() external onlyOwner {
        breakerTripped = false;
        peakNavPerShare = navPerShare();
        emit BreakerReset(peakNavPerShare);
    }

    // ---------- Owner administration ----------
    function setAgent(address newAgent) external onlyOwner {
        agent = newAgent;
        emit AgentUpdated(newAgent);
    }

    function setCoinAllowed(string calldata coin, bool allowed) external onlyOwner {
        coinAllowed[coin] = allowed;
        emit CoinWhitelistUpdated(coin, allowed);
    }

    function setGuardrails(uint256 newMaxSingleWeightBps, uint256 newMaxDrawdownBps) external onlyOwner {
        require(newMaxSingleWeightBps > 0 && newMaxSingleWeightBps <= 10000, "bad single-asset cap");
        require(newMaxDrawdownBps > 0 && newMaxDrawdownBps < 10000, "bad drawdown limit");
        maxSingleWeightBps = newMaxSingleWeightBps;
        maxDrawdownBps = newMaxDrawdownBps;
        emit GuardrailsUpdated(newMaxSingleWeightBps, newMaxDrawdownBps);
    }

    /// @notice The redemption fee is the slippage guardrail: the agent raises
    ///         it (within the on-chain cap) when volatility is high, so
    ///         exiting investors bear the liquidation cost instead of the
    ///         investors who stay.
    function setRedemptionFee(uint256 newFeeBps) external onlyAgent {
        require(newFeeBps <= MAX_REDEMPTION_FEE_BPS, "fee above cap");
        redemptionFeeBps = newFeeBps;
        emit RedemptionFeeUpdated(newFeeBps);
    }
}
