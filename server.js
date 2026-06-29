// ╔═══════════════════════════════════════════════════════════╗
// ║         NEXUS TRADER — Backend Server v2.0               ║
// ║         Bot Signals + MT5 Webhook + Live Prices          ║
// ╚═══════════════════════════════════════════════════════════╝
const express = require('express');
const https   = require('https');
const cors    = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

const TD_KEY = '6f127f83a2db4b1686d2926a7f35049f';
const BOT_KEY = 'NEXUS-BOT-KEY-001'; // Must match MT5 EA ApiKey

// ── In-memory state ──────────────────────────────────────────
let mtStatus  = { status: 'DISCONNECTED', balance: 0, equity: 0, symbol: '' };
let liveSignal = { signal: 'HOLD', confidence: 0, strategy: 'AI', ts: Date.now() };
let tradeLog  = [];

// ── Helper: fetch from Twelve Data ───────────────────────────
function fetchData(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { reject(e); } });
    }).on('error', reject);
  });
}

// ── Helper: generate AI signal ───────────────────────────────
function generateSignal(price, sym) {
  // Simple rule-based signal for demo (replace with real AI logic)
  const hour = new Date().getHours();
  const rand  = Math.random();
  let signal  = 'HOLD';
  let conf    = Math.floor(Math.random() * 30 + 55);

  if (rand > 0.65)      signal = 'BUY';
  else if (rand < 0.35) signal = 'SELL';

  return { signal, confidence: conf, price, symbol: sym, strategy: 'AI-Confluence', ts: Date.now() };
}

// ═══════════════════════════════════════════════════════════════
//  PRICE ENDPOINTS
// ═══════════════════════════════════════════════════════════════

// GET /price — live gold price
app.get('/price', async (req, res) => {
  try {
    const data = await fetchData(`https://api.twelvedata.com/price?symbol=XAU/USD&apikey=${TD_KEY}`);
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch price' });
  }
});

// GET /candles — gold candle data
app.get('/candles', async (req, res) => {
  try {
    const interval   = req.query.interval   || '1h';
    const outputsize = req.query.outputsize || '80';
    const url = `https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=${interval}&outputsize=${outputsize}&apikey=${TD_KEY}`;
    const data = await fetchData(url);
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch candles' });
  }
});

// ═══════════════════════════════════════════════════════════════
//  BOT SIGNAL ENDPOINTS
// ═══════════════════════════════════════════════════════════════

// GET /webhook/signal — MT5 EA polls this for trade signals
app.get('/webhook/signal', async (req, res) => {
  const { key, sym, balance } = req.query;

  if (key !== BOT_KEY) {
    return res.status(401).json({ error: 'Invalid API key' });
  }

  // Fetch live price for signal
  let price = 4080;
  try {
    const priceData = await fetchData(`https://api.twelvedata.com/price?symbol=XAU/USD&apikey=${TD_KEY}`);
    if (priceData.price) price = parseFloat(priceData.price);
  } catch(e) {}

  // Generate signal
  const signal = generateSignal(price, sym || 'XAUUSD');
  liveSignal = signal;

  console.log(`📡 Signal sent to MT5: ${signal.signal} (${signal.confidence}%) | ${sym} | Balance: $${balance}`);

  res.json(signal);
});

// POST /webhook/status — MT5 EA sends connection status
app.post('/webhook/status', (req, res) => {
  const { key, status, symbol, balance, equity } = req.body;

  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid API key' });

  mtStatus = { status, symbol, balance, equity, ts: Date.now() };
  console.log(`🔗 MT5 Status: ${status} | ${symbol} | Balance: $${balance}`);
  res.json({ ok: true });
});

// POST /webhook/trade — MT5 EA reports trade execution
app.post('/webhook/trade', (req, res) => {
  const { key, action, symbol, price, lot, status, balance } = req.body;

  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid API key' });

  const trade = { action, symbol, price, lot, status, balance, ts: Date.now() };
  tradeLog.unshift(trade);
  if (tradeLog.length > 100) tradeLog.pop(); // Keep last 100 trades

  console.log(`💰 Trade: ${action} ${symbol} @ ${price} | Lot: ${lot} | Status: ${status}`);
  res.json({ ok: true });
});

// GET /webhook/dashboard — NEXUS TRADER frontend polls this
app.get('/webhook/dashboard', (req, res) => {
  res.json({
    mt5: mtStatus,
    signal: liveSignal,
    trades: tradeLog.slice(0, 20),
    stats: {
      total: tradeLog.length,
      wins: tradeLog.filter(t => t.pnl > 0).length,
    }
  });
});

// ═══════════════════════════════════════════════════════════════
//  SERVE HTML FILES
// ═══════════════════════════════════════════════════════════════
app.use(express.static('.'));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log('');
  console.log('╔══════════════════════════════════════╗');
  console.log('║   NEXUS TRADER Server v2.0 Running   ║');
  console.log('║   Port: " + PORT + "                      ║');
  console.log('║   Bot Webhook: /webhook/signal       ║');
  console.log('╚══════════════════════════════════════╝');
  console.log('');
});
