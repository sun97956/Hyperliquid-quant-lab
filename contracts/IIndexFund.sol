// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IIndexFund — reusable interface for an on-chain index fund.
/// @notice Third-party strategies plug in by becoming the fund's authorized
///         "agent" (weights manager): the fund only requires that target
///         weights arrive through `setWeights` and pass the guardrails.
///         Any strategy — an AI agent, a rules engine, a human manager —
///         can drive any fund that implements this interface.
interface IIndexFund {
    /// @notice Emitted when an investor deposits the base asset and receives shares.
    event Deposit(address indexed investor, uint256 amount, uint256 shares);

    /// @notice Emitted when an investor redeems shares for the base asset.
    event Redeem(address indexed investor, uint256 shares, uint256 payout, uint256 fee);

    /// @notice Emitted when the authorized agent records new target weights on-chain.
    event WeightsUpdated(string[] coins, uint256[] weightsBps, uint256 timestamp);

    /// @notice Emitted when the agent reports the fund's total asset value.
    event ValuationReported(uint256 totalAssets, uint256 timestamp);

    /// @notice Emitted when the drawdown circuit-breaker trips.
    event BreakerTripped(uint256 navPerShare, uint256 peakNavPerShare);

    /// @notice Deposit base asset, receive fund shares at the current NAV.
    function deposit(uint256 amount) external returns (uint256 shares);

    /// @notice Burn fund shares, receive base asset at the current NAV minus the redemption fee.
    function redeem(uint256 shares) external returns (uint256 payout);

    /// @notice Record new target weights (in basis points, 10000 = 100%).
    ///         Only the authorized agent may call this. Guardrails are enforced on-chain.
    function setWeights(string[] calldata coins, uint256[] calldata weightsBps) external;

    /// @notice Report the fund's total asset value (base-asset units).
    ///         Only the authorized agent may call this.
    function reportTotalAssets(uint256 totalValue) external;

    /// @notice Total asset value backing the shares (base-asset units).
    function totalAssets() external view returns (uint256);

    /// @notice NAV per share, scaled by 1e6. Returns 1e6 (1.0) before any deposit.
    function navPerShare() external view returns (uint256);

    /// @notice The current on-chain target weights.
    function getWeights() external view returns (string[] memory coins, uint256[] memory weightsBps, uint256 updatedAt);
}
