// ╔═══════════════════════════════════════════════════════════╗
// ║      NEXUS TRADER — Smart AI Signal Server v3.0          ║
// ║      Real Claude AI Analysis → MT5 Auto Trading          ║
// ╚═══════════════════════════════════════════════════════════╝
const express = require('express');
const https   = require('https');
const cors    = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

// ── Config ────────────────────────────────────────────────────
const TD_KEY    = '6f127f83a2db4b1686d2926a7f35049f';
const BOT_KEY   = 'NEXUS-BOT-KEY-001';
const CLAUDE_KEY = process.env.CLAUDE_API_KEY || '';
const PORT      = process.env.PORT || 3000;

// ── Signal Cache (5 min) ──────────────────────────────────────
let cachedSignal = { signal:'HOLD', confidence:0, price:0, sl:0, tp:0, reasoning:'Starting...', strategy:'Claude AI', ts:0 };
let mtStatus     = { status:'DISCONNECTED', balance:0, equity:0, symbol:'' };
let tradeLog     = [];
let botEnabled   = false;  // Starts OFF — only NEXUS TRADER toggle turns it ON
let botStats     = { totalTrades:0, wins:0, losses:0, totalPnl:0.0 };
const CACHE_MS   = 5 * 60 * 1000; // 5 minutes

// ── HTTP Helper ───────────────────────────────────────────────
function fetchData(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { reject(e); } });
    }).on('error', reject);
  });
}

// ── POST Helper (for Claude API) ──────────────────────────────
function postData(hostname, path, body, headers) {
  return new Promise((resolve, reject) => {
    const postBody = JSON.stringify(body);
    const options = {
      hostname, path, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postBody), ...headers }
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { reject(e); } });
    });
    req.on('error', reject);
    req.write(postBody);
    req.end();
  });
}

// ── RSI Calculator ────────────────────────────────────────────
function calcRSI(closes, period = 14) {
  if (closes.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = 0; i < period; i++) {
    const diff = closes[i] - closes[i + 1]; // newest first
    if (diff > 0) gains += diff;
    else losses += Math.abs(diff);
  }
  const avgG = gains / period, avgL = losses / period;
  if (avgL === 0) return 100;
  const rs = avgG / avgL;
  return parseFloat((100 - (100 / (1 + rs))).toFixed(2));
}

// ── MA Calculator ─────────────────────────────────────────────
function calcMA(closes, period) {
  const slice = closes.slice(0, Math.min(period, closes.length));
  return parseFloat((slice.reduce((a, b) => a + b, 0) / slice.length).toFixed(2));
}

// ── Claude AI Signal Engine ───────────────────────────────────
async function generateSmartSignal(sym) {
  const now = Date.now();

  // Return cached signal if fresh (5 min cache)
  if (now - cachedSignal.ts < CACHE_MS && cachedSignal.ts > 0) {
    console.log('📦 Returning cached signal:', cachedSignal.signal, '| Expires in:', Math.round((CACHE_MS - (now - cachedSignal.ts))/1000)+'s');
    return cachedSignal;
  }

  console.log('\n🤖 Generating NEW Claude AI signal for', sym, '...');

  try {
    // Step 1: Fetch live price
    const priceData = await fetchData(`https://api.twelvedata.com/price?symbol=XAU/USD&apikey=${TD_KEY}`);
    const price = parseFloat(priceData.price);
    console.log('💰 Live price:', price);

    // Step 2: Fetch 1H candles
    const candleData = await fetchData(`https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1h&outputsize=30&apikey=${TD_KEY}`);
    const candles = candleData.values || [];
    const closes  = candles.map(c => parseFloat(c.close));
    const highs   = candles.map(c => parseFloat(c.high));
    const lows    = candles.map(c => parseFloat(c.low));

    // Step 3: Calculate indicators
    const rsi  = calcRSI(closes, 14);
    const ma20 = calcMA(closes, 20);
    const ma50 = calcMA(closes, 50);
    const atr  = parseFloat((highs.slice(0,14).map((h,i) => h - lows[i]).reduce((a,b)=>a+b,0)/14).toFixed(2));

    // Recent highs and lows (support/resistance)
    const recentHigh = parseFloat(Math.max(...highs.slice(0,10)).toFixed(2));
    const recentLow  = parseFloat(Math.min(...lows.slice(0,10)).toFixed(2));

    console.log(`📊 RSI: ${rsi} | MA20: ${ma20} | MA50: ${ma50} | ATR: ${atr}`);

    // Step 4: Build Claude AI prompt
    const prompt = `You are an expert XAUUSD (Gold) scalp/swing trader. Analyze this real market data and give a trading signal.

LIVE MARKET DATA — XAUUSD:
Current Price: $${price}
RSI(14): ${rsi} ${rsi > 70 ? '⚠️ OVERBOUGHT' : rsi < 30 ? '⚠️ OVERSOLD' : '✅ NEUTRAL'}
MA20: $${ma20} (Price is ${price > ma20 ? 'ABOVE ↑ bullish' : 'BELOW ↓ bearish'})
MA50: $${ma50} (Price is ${price > ma50 ? 'ABOVE ↑ bullish' : 'BELOW ↓ bearish'})
ATR(14): ${atr} (volatility)
10H High: $${recentHigh} | 10H Low: $${recentLow}

LAST 5 HOURLY CANDLES (newest first):
${candles.slice(0,5).map((c,i) => `${i+1}. O:${c.open} H:${c.high} L:${c.low} C:${c.close} | ${c.datetime}`).join('\n')}

ANALYSIS RULES:
- RSI > 70 + price near high = SELL signal
- RSI < 30 + price near low = BUY signal  
- Price above both MAs + RSI 50-65 = BUY signal
- Price below both MAs + RSI 35-50 = SELL signal
- Mixed signals = HOLD
- Always include tight SL and realistic TP

Respond ONLY with this exact JSON (no extra text, no markdown):
{"signal":"BUY","confidence":75,"sl":${(price-40).toFixed(2)},"tp":${(price+80).toFixed(2)},"reasoning":"brief reason"}`;

    // Step 5: Call Claude API
    if (!CLAUDE_KEY) {
      console.log('⚠️ No Claude API key — using indicator-based signal');
      return indicatorSignal(price, rsi, ma20, ma50, atr, recentHigh, recentLow);
    }

    console.log('🧠 Calling Claude AI...');
    const aiResponse = await postData(
      'api.anthropic.com',
      '/v1/messages',
      {
        model: 'claude-sonnet-4-6',
        max_tokens: 300,
        messages: [{ role: 'user', content: prompt }]
      },
      {
        'x-api-key': CLAUDE_KEY,
        'anthropic-version': '2023-06-01'
      }
    );

    const rawText = aiResponse.content?.[0]?.text || '';
    console.log('🤖 Claude response:', rawText);

    // Step 6: Parse Claude response
    const jsonMatch = rawText.match(/\{[\s\S]*\}/);
    if (!jsonMatch) throw new Error('No JSON in Claude response');

    const parsed = JSON.parse(jsonMatch[0]);
    const signal = {
      signal:     parsed.signal     || 'HOLD',
      confidence: parsed.confidence || 50,
      sl:         parsed.sl         || (price - 40),
      tp:         parsed.tp         || (price + 80),
      reasoning:  parsed.reasoning  || 'AI analysis',
      price,
      rsi, ma20, ma50, atr,
      symbol: sym,
      strategy: 'Claude AI',
      ts: now
    };

    cachedSignal = signal;
    console.log(`✅ Signal: ${signal.signal} (${signal.confidence}%) | SL:${signal.sl} | TP:${signal.tp}`);
    console.log(`💭 Reason: ${signal.reasoning}\n`);
    return signal;

  } catch (err) {
    console.error('❌ AI Signal error:', err.message);
    // Fallback to simple signal
    return { ...cachedSignal, signal:'HOLD', reasoning:'Error - holding position', ts: now };
  }
}

// ── Indicator-based fallback (no API key needed) ──────────────
function indicatorSignal(price, rsi, ma20, ma50, atr, high, low) {
  let signal = 'HOLD', confidence = 50, reasoning = '';

  if (rsi < 30 && price < ma20) {
    signal = 'BUY'; confidence = 72;
    reasoning = `RSI oversold (${rsi}) + price below MA20 — reversal likely`;
  } else if (rsi > 70 && price > ma20) {
    signal = 'SELL'; confidence = 70;
    reasoning = `RSI overbought (${rsi}) + price above MA20 — pullback likely`;
  } else if (price > ma20 && price > ma50 && rsi > 50 && rsi < 65) {
    signal = 'BUY'; confidence = 65;
    reasoning = `Price above MA20 & MA50, RSI neutral-bullish (${rsi})`;
  } else if (price < ma20 && price < ma50 && rsi < 50 && rsi > 35) {
    signal = 'SELL'; confidence = 63;
    reasoning = `Price below MA20 & MA50, RSI neutral-bearish (${rsi})`;
  } else {
    signal = 'HOLD'; confidence = 45;
    reasoning = `Mixed signals — RSI ${rsi}, price vs MA20: ${(price - ma20).toFixed(2)}`;
  }

  const now = Date.now();
  const sig = {
    signal, confidence,
    sl:   signal === 'BUY'  ? parseFloat((price - atr * 1.5).toFixed(2)) : parseFloat((price + atr * 1.5).toFixed(2)),
    tp:   signal === 'BUY'  ? parseFloat((price + atr * 2.5).toFixed(2)) : parseFloat((price - atr * 2.5).toFixed(2)),
    reasoning, price, rsi, ma20, ma50, atr,
    symbol: 'XAUUSD', strategy: 'Indicator-AI', ts: now
  };
  cachedSignal = sig;
  return sig;
}

// ═══════════════════════════════════════════════════════════════
//  API ENDPOINTS
// ═══════════════════════════════════════════════════════════════

// GET /price — live price
app.get('/price', async (req, res) => {
  try {
    const data = await fetchData(`https://api.twelvedata.com/price?symbol=XAU/USD&apikey=${TD_KEY}`);
    res.json(data);
  } catch(err) { res.status(500).json({ error: 'Price fetch failed' }); }
});

// GET /webhook/signal — MT5 EA polls this
app.get('/webhook/signal', async (req, res) => {
  const { key, sym } = req.query;
  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid API key' });
  // If bot disabled from NEXUS, return HOLD
  if (!botEnabled) return res.json({ signal: 'HOLD', confidence: 0, reasoning: 'Bot disabled from NEXUS TRADER', strategy: 'DISABLED', ts: Date.now() });
  try {
    const signal = await generateSmartSignal(sym || 'XAUUSD');
    res.json(signal);
  } catch(err) { res.status(500).json({ error: err.message }); }
});

// GET /signal/latest — dashboard polls this
app.get('/signal/latest', (req, res) => res.json(cachedSignal));

// POST /webhook/status — MT5 EA sends status
app.post('/webhook/status', (req, res) => {
  const { key, status, symbol, balance, equity } = req.body;
  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid key' });
  mtStatus = { status, symbol, balance, equity, ts: Date.now() };
  console.log(`🔗 MT5: ${status} | ${symbol} | $${balance}`);
  res.json({ ok: true });
});

// POST /webhook/trade — MT5 EA reports trade
app.post('/webhook/trade', (req, res) => {
  const { key, action, symbol, price, lot, status, balance } = req.body;
  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid key' });
  const pnl = parseFloat(req.body.pnl || 0);
  const trade = { action, symbol, price, lot, status, balance, pnl, ts: Date.now() };
  // Update stats
  if(status === 'CLOSED') {
    botStats.totalTrades++;
    if(pnl > 0) botStats.wins++;
    else botStats.losses++;
    botStats.totalPnl = parseFloat((botStats.totalPnl + pnl).toFixed(2));
  }
  tradeLog.unshift(trade);
  if (tradeLog.length > 100) tradeLog.pop();
  console.log(`💰 Trade: ${action} ${symbol} @ ${price} | ${status}`);
  res.json({ ok: true });
});

// GET /dashboard — full status
app.get('/dashboard', (req, res) => {
  res.json({ mt5: mtStatus, signal: cachedSignal, trades: tradeLog.slice(0,20), botEnabled, stats: botStats });
});

// Alias routes (handle trailing slash and alternate paths)
app.get('/signal', async (req, res) => {
  const { key, sym } = req.query;
  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid API key' });
  try {
    const signal = await generateSmartSignal(sym || 'XAUUSD');
    res.json(signal);
  } catch(err) { res.status(500).json({ error: err.message }); }
});


// POST /bot/control — NEXUS TRADER turns bot ON/OFF
app.post('/bot/control', (req, res) => {
  const { key, enabled } = req.body;
  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid key' });
  botEnabled = enabled === true || enabled === 'true';
  console.log(`🤖 Bot ${botEnabled ? 'ENABLED' : 'DISABLED'} from NEXUS TRADER`);
  res.json({ ok: true, botEnabled });
});

// GET /bot/status — MT5 EA checks if bot should trade
app.get('/bot/status', (req, res) => {
  const { key } = req.query;
  if (key !== BOT_KEY) return res.status(401).json({ error: 'Invalid key' });
  res.json({ botEnabled, signal: cachedSignal.signal, ts: Date.now() });
});

// Health check
app.get('/', (req, res) => {
  res.json({ status: 'NEXUS TRADER Server v3.0 LIVE', signal: cachedSignal.signal, price: cachedSignal.price });
});

app.use(express.static('.'));

app.listen(PORT, () => {
  console.log('');
  console.log('╔══════════════════════════════════════════╗');
  console.log('║  NEXUS TRADER Smart Signal Server v3.0  ║');
  console.log('║  Claude AI → Real Trading Signals       ║');
  console.log(`║  Port: ${PORT}                              ║`);
  console.log(`║  Claude AI: ${CLAUDE_KEY ? '✅ Connected' : '⚠️ No key — using indicators'}    ║`);
  console.log('╚══════════════════════════════════════════╝');
  console.log('');
  // Generate first signal on startup
  setTimeout(() => generateSmartSignal('XAUUSD'), 3000);
});
