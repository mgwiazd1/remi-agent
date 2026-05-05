/**
 * dripstack_bridge.js — Node.js bridge for x402 payments to DripStack.
 * Uses @x402/axios + @x402/evm to handle 402 payment flow automatically.
 *
 * Usage: node dripstack_bridge.js <pub_slug> <post_slug>
 * Env:   REMI_WALLET_PRIVATE_KEY (required)
 *
 * Prints full article JSON to stdout on success.
 * Prints error to stderr and exits 1 on failure.
 */

const { wrapAxiosWithPayment, x402Client } = require("@x402/axios");
const { ExactEvmScheme, toClientEvmSigner } = require("@x402/evm");
const axios = require("axios");
const { privateKeyToAccount } = require("viem/accounts");
const { createPublicClient, http } = require("viem");
const { base } = require("viem/chains");

async function main() {
  const pk = process.env.REMI_WALLET_PRIVATE_KEY;
  if (!pk) {
    process.stderr.write("REMI_WALLET_PRIVATE_KEY not set\n");
    process.exit(1);
  }

  const pubSlug = process.argv[2];
  const postSlug = process.argv[3];
  if (!pubSlug || !postSlug) {
    process.stderr.write("Usage: node dripstack_bridge.js <pub_slug> <post_slug>\n");
    process.exit(1);
  }

  const key = pk.startsWith("0x") ? pk : "0x" + pk;
  const viemAccount = privateKeyToAccount(key);

  // Create a public client for Base mainnet (readContract for approvals)
  const publicClient = createPublicClient({
    chain: base,
    transport: http(),
  });

  // Build x402 client with EVM exact scheme registered
  const signer = toClientEvmSigner(viemAccount, publicClient);
  const scheme = new ExactEvmScheme(signer);
  const client = new x402Client();
  client.register("eip155:8453", scheme);

  const httpAxios = axios.create();
  const wrapped = wrapAxiosWithPayment(httpAxios, client);

  const url = `https://dripstack.xyz/api/v1/publications/${pubSlug}/${postSlug}`;

  try {
    const resp = await wrapped.get(url, { timeout: 30000 });
    process.stdout.write(JSON.stringify(resp.data));
  } catch (err) {
    const msg = err.response
      ? `HTTP ${err.response.status}: ${JSON.stringify(err.response.data || err.response.statusText)}`
      : err.message;
    process.stderr.write(`${msg}\n`);
    process.exit(1);
  }
}

main();
