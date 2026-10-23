// Thin JSON CLI over the official @evedex/exchange-bot-sdk, so order signing
// stays in the exchange's own code. Called by cryptobot/evedex.py:
//
//   node bridge.js <command> '<json params>'
//
// Prints one JSON object to stdout; on failure prints {"error": ...} and exits 1.
// Env: EVEDEX_ENV=demo|prod, and either EVEDEX_PRIVATE_KEY, or
// EVEDEX_API_KEY + EVEDEX_OCT_PRIVATE_KEY (one-click-trading wallet; safer).
// CommonJS on purpose: the SDK's ESM build fails to resolve in Node 22.
const evedex = require("@evedex/exchange-bot-sdk");
const WebSocket = require("ws");

// The SDK logs to stdout in places; keep stdout clean for the JSON reply.
const reply = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
console.log = console.info = console.debug = (...args) => console.error(...args);

const env = (process.env.EVEDEX_ENV || "demo").toLowerCase();
if (!["demo", "prod"].includes(env)) throw new Error(`EVEDEX_ENV must be demo or prod, got ${env}`);

const wallets = {};
const apiKeys = {};
if (process.env.EVEDEX_OCT_PRIVATE_KEY) wallets.oct = { privateKey: process.env.EVEDEX_OCT_PRIVATE_KEY };
if (process.env.EVEDEX_PRIVATE_KEY) wallets.main = { privateKey: process.env.EVEDEX_PRIVATE_KEY };
if (process.env.EVEDEX_API_KEY) apiKeys.main = { apiKey: process.env.EVEDEX_API_KEY };

const Container = env === "prod" ? evedex.ProdContainer : evedex.DemoContainer;
const container = new Container({ centrifugeWebSocket: WebSocket, wallets, apiKeys });

async function walletAccount() {
  if (wallets.oct && apiKeys.main) {
    const apiKeyAccount = await container.apiKeyAccount("main");
    return new evedex.WalletAccount({
      gateway: apiKeyAccount.gateway,
      wallet: container.wallet("oct"),
      exchangeAccount: await apiKeyAccount.fetchMe(),
    });
  }
  if (wallets.main) return container.account("main");
  throw new Error("set EVEDEX_PRIVATE_KEY, or EVEDEX_API_KEY + EVEDEX_OCT_PRIVATE_KEY");
}

async function instrument(name) {
  const found = (await container.gateway().fetchInstrumentsWithMetrics()).find((i) => i.name === name);
  if (!found) throw new Error(`unknown instrument ${name}; run the 'instruments' command`);
  return found;
}

const roundTo = (value, step) => Math.round(value / step) * step;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isLive = (t) => ["active", "waitOrder", "process"].includes(t.status);

async function openPosition(account, name) {
  const positions = await account.fetchPositions();
  return positions.find((p) => p.instrument === name && Number(p.quantity) > 0) || null;
}

async function liveTpSl(account, name) {
  const { list } = await account.fetchTpSlList({ instrument: name });
  return list.filter(isLive);
}

const commands = {
  async instruments() {
    const list = await container.gateway().fetchInstrumentsWithMetrics();
    return {
      instruments: list.map((i) => ({
        name: i.name, markPrice: i.markPrice, maxLeverage: i.maxLeverage,
        minQuantity: i.minQuantity, quantityIncrement: i.quantityIncrement,
        priceIncrement: i.priceIncrement, minVolume: i.minVolume, trading: i.trading,
      })),
    };
  },

  async state({ instrument: name }) {
    const account = await walletAccount();
    const [inst, position, balance, tpsl] = await Promise.all([
      instrument(name), openPosition(account, name), account.fetchAvailableBalance(), liveTpSl(account, name),
    ]);
    return { markPrice: inst.markPrice, availableBalance: Number(balance.availableBalance), position, tpsl };
  },

  async open_long({ instrument: name, cash, leverage = 1, stop, takeProfit }) {
    const account = await walletAccount();
    const inst = await instrument(name);
    if (await openPosition(account, name)) throw new Error(`already have a position in ${name}`);
    if (!(stop > 0 && stop < inst.markPrice)) throw new Error(`stop ${stop} must be below mark ${inst.markPrice}`);

    try {
      await account.updatePosition({ instrument: name, leverage });
    } catch (e) {
      console.error("updatePosition:", e?.response?.data ?? e.message);
    }
    const order = await account.createMarketOrderV2({
      instrument: name, side: evedex.Side.Buy, timeInForce: evedex.TimeInForce.IOC,
      leverage, cashQuantity: cash,
    });

    let position = null;
    for (let i = 0; i < 20 && !position; i++) {
      await sleep(500);
      position = await openPosition(account, name);
    }
    if (!position) throw new Error(`market order ${order.id} sent but no position appeared`);

    // Protective orders live on the exchange, so they work even if the bot is down.
    const protect = [{ type: evedex.TpSlType.StopLoss, price: roundTo(stop, inst.priceIncrement) }];
    if (takeProfit) protect.push({ type: evedex.TpSlType.TakeProfit, price: roundTo(takeProfit, inst.priceIncrement) });
    const tpsl = [];
    for (const p of protect) {
      tpsl.push(await account.createTpSl({
        instrument: name, side: evedex.Side.Sell, quantity: position.quantity, price: p.price, type: p.type, order: null,
      }));
    }
    return { order, position, tpsl };
  },

  async close({ instrument: name }) {
    const account = await walletAccount();
    for (const t of await liveTpSl(account, name)) {
      await account.cancelTpSl({ instrument: name, id: t.id });
    }
    const position = await openPosition(account, name);
    if (!position) return { closed: false };
    const order = await account.createClosePositionOrderV2({
      instrument: name, leverage: position.leverage, quantity: position.quantity,
    });
    return { closed: true, order, position };
  },
};

async function main() {
  const [command, rawParams = "{}"] = process.argv.slice(2);
  try {
    if (!commands[command]) throw new Error(`unknown command ${command}; expected one of ${Object.keys(commands)}`);
    reply(await commands[command](JSON.parse(rawParams)));
    container.closeWsConnection();
    process.exit(0);
  } catch (e) {
    reply({ error: e?.response?.data ?? e.message ?? String(e) });
    process.exit(1);
  }
}

main();
