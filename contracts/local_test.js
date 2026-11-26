// Local end-to-end rehearsal for IndexFund — no testnet, no real funds.
//
// It compiles the contracts in this folder, starts a throwaway local chain
// (ganache, in-process), deploys MockUSD + IndexFund, and walks the full
// story: whitelist -> deposit -> set weights -> guardrail rejections ->
// valuation report -> redeem -> drawdown breaker -> redemption fee.
//
// Run from the repository root:
//   npm install --no-save solc ganache ethers
//   node contracts/local_test.js
//
// Every step prints PASS/FAIL; the script exits non-zero if any check fails.

const fs = require('fs');
const path = require('path');
const solc = require('solc');
const ganache = require('ganache');
const { ethers } = require('ethers');

const DIR = __dirname;
const U = 10n ** 6n; // one whole unit of a 6-decimal asset (1 USDC / 1 share)

let failures = 0;
function check(label, cond) {
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + label);
  if (!cond) failures++;
}
async function expectRevert(label, promise) {
  // Note: through ganache + BrowserProvider the revert *reason string* is
  // not propagated (errors surface as CALL_EXCEPTION / "missing revert
  // data"), so a revert is asserted by "the call threw" plus, at each call
  // site, a state-unchanged check — which is the stronger assertion anyway.
  try {
    await promise;
    check(label + ' (should have reverted)', false);
    return false;
  } catch (e) {
    check(label, true);
    return true;
  }
}

function compile() {
  const files = ['IndexFund.sol', 'IIndexFund.sol', 'MockUSD.sol'];
  const sources = {};
  for (const f of files) sources[f] = { content: fs.readFileSync(path.join(DIR, f), 'utf8') };
  const input = {
    language: 'Solidity',
    sources,
    // evmVersion 'paris': the newest compilers emit Cancun-era opcodes
    // (MCOPY) that the local ganache fork cannot execute — string reads
    // then fail with "missing revert data". Paris keeps the rehearsal
    // chain-compatible; deployment compiles can target a newer fork.
    settings: { evmVersion: 'paris', outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } } },
  };
  const out = JSON.parse(solc.compile(JSON.stringify(input), {
    import: (p) => ({ contents: fs.readFileSync(path.join(DIR, p.replace('./', '')), 'utf8') }),
  }));
  const errs = (out.errors || []).filter((e) => e.severity === 'error');
  if (errs.length) {
    for (const e of errs) console.log(e.formattedMessage);
    throw new Error('compilation failed');
  }
  return out.contracts;
}

async function main() {
  const contracts = compile();
  console.log('compiled: IndexFund, MockUSD');

  const provider = new ethers.BrowserProvider(ganache.provider({ logging: { quiet: true } }));
  const owner = await provider.getSigner(0);
  const agent = await provider.getSigner(1);
  const investor = await provider.getSigner(2);
  const agentAddr = await agent.getAddress();

  async function deploy(file, name, signer, args) {
    const c = contracts[file][name];
    const factory = new ethers.ContractFactory(c.abi, c.evm.bytecode.object, signer);
    const contract = await factory.deploy(...(args || []));
    await contract.waitForDeployment();
    return contract;
  }

  // --- Setup: test asset + fund, whitelist the demo universe ---
  const usd = await deploy('MockUSD.sol', 'MockUSD', owner);
  const fund = await deploy('IndexFund.sol', 'IndexFund', owner, [await usd.getAddress(), agentAddr]);
  console.log('fund deployed at', await fund.getAddress());

  await fund.connect(owner).setCoinAllowed('BTC', true);
  await fund.connect(owner).setCoinAllowed('ETH', true);
  await fund.connect(owner).setCoinAllowed('HYPE', true);

  // --- Act 1: investor deposits 1000, receives 1000 shares at NAV 1.0 ---
  await usd.connect(investor).faucet(1000n * U);
  await usd.connect(investor).approve(await fund.getAddress(), 1000n * U);
  await fund.connect(investor).deposit(1000n * U);
  check('deposit: investor holds 1000 shares', (await fund.balanceOf(await investor.getAddress())) === 1000n * U);
  check('deposit: NAV per share is 1.0', (await fund.navPerShare()) === 1000000n);

  // --- Act 2: agent records weights on-chain ---
  await fund.connect(agent).setWeights(['BTC', 'ETH'], [5000, 3000]);
  const w = await fund.getWeights();
  check('weights stored on-chain', w[0].length === 2 && w[1][0] === 5000n && w[1][1] === 3000n);

  // --- Act 3: guardrails reject bad weights (and weights stay unchanged) ---
  await expectRevert('guardrail: single asset above 50% cap rejected',
    fund.connect(agent).setWeights(['BTC'], [6000]));
  await expectRevert('guardrail: non-whitelisted coin rejected',
    fund.connect(agent).setWeights(['DOGE'], [1000]));
  await expectRevert('guardrail: non-agent cannot set weights',
    fund.connect(investor).setWeights(['BTC'], [1000]));
  const wAfter = await fund.getWeights();
  check('guardrail: stored weights unchanged after rejections',
    wAfter[0].length === 2 && wAfter[1][0] === 5000n && wAfter[1][1] === 3000n);

  // --- Act 4: agent reports new total value 1100 -> NAV 1.1, redeem half ---
  await fund.connect(agent).reportTotalAssets(1100n * U);
  check('valuation: NAV per share is 1.1', (await fund.navPerShare()) === 1100000n);
  await fund.connect(investor).redeem(500n * U);
  check('redeem: investor received 550 back', (await usd.balanceOf(await investor.getAddress())) === 550n * U);
  check('redeem: 500 shares remain', (await fund.balanceOf(await investor.getAddress())) === 500n * U);

  // --- Act 5: drawdown beyond 20% from peak trips the circuit breaker ---
  await fund.connect(agent).reportTotalAssets(400n * U); // NAV 1.1 -> 0.8
  check('breaker: tripped after >20% drawdown', (await fund.breakerTripped()) === true);
  await usd.connect(investor).approve(await fund.getAddress(), 10n * U);
  await expectRevert('breaker: deposits paused while tripped',
    fund.connect(investor).deposit(10n * U));
  check('breaker: investor balance unchanged after paused deposit',
    (await usd.balanceOf(await investor.getAddress())) === 550n * U);
  await fund.connect(investor).redeem(100n * U); // exits stay open
  check('breaker: redemption still works, +80 received',
    (await usd.balanceOf(await investor.getAddress())) === 630n * U);
  await fund.connect(owner).resetBreaker();
  check('breaker: owner reset clears the trip', (await fund.breakerTripped()) === false);

  // --- Act 6: redemption fee (the slippage guardrail) at 1% ---
  await fund.connect(agent).setRedemptionFee(100);
  await fund.connect(investor).redeem(100n * U); // gross 80, fee 0.8, payout 79.2
  check('fee: redeem of 100 shares pays out 79.2',
    (await usd.balanceOf(await investor.getAddress())) === 630n * U + 792n * U / 10n);

  console.log(failures === 0 ? '\nALL CHECKS PASSED' : '\n' + failures + ' CHECK(S) FAILED');
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
