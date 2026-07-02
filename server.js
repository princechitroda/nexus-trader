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
let lastGoodCandles = [];  // Cache last successful candle data (survives API rate limits)
let lastGoodPrice   = 0;   // Cache last known good price
let priceHistory    = [];  // Rolling price samples — builds our own candles when API is limited
let botStats     = { totalTrades:0, wins:0, losses:0, totalPnl:0.0 };
const CACHE_MS   = 5 * 60 * 1000; // 5 minutes

// ═══ RAZORPAY PAYMENTS (test mode by default) ═══
const crypto = require('crypto');
const RAZORPAY_KEY_ID     = process.env.RAZORPAY_KEY_ID     || 'rzp_test_XXXXXXXXXX';
const RAZORPAY_KEY_SECRET = process.env.RAZORPAY_KEY_SECRET || 'test_secret_XXXXXXXXXX';
// Plan prices in paise (₹1 = 100 paise). Pro ₹799, Elite ₹2,999
const PLANS = {
  Pro:   { amount: 79900,  name: 'NEXUS Pro'   },
  Elite: { amount: 299900, name: 'NEXUS Elite' }
};
// Simple in-memory record of paid users (use a real DB in production)
let paidUsers = {}; // { uid: { plan, orderId, paymentId, ts } }

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
  // Adapt period to available data (need at least 2 points)
  if (closes.length < 2) return 50;
  if (closes.length < period + 1) period = closes.length - 1;
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
    // Step 1: Fetch live price (with fallback to last good price)
    let price = 0;
    try {
      const priceData = await fetchData(`https://api.twelvedata.com/price?symbol=XAU/USD&apikey=${TD_KEY}`);
      price = parseFloat(priceData.price);
      if (price && !isNaN(price)) lastGoodPrice = price;
    } catch(e) { console.log('⚠️ Price fetch failed:', e.message); }
    // If Twelve Data price failed, try backup free gold API
    if (!price || isNaN(price)) {
      try {
        const backup = await fetchData('https://api.gold-api.com/price/XAU');
        if (backup && backup.price) {
          price = parseFloat(backup.price);
          if (price && !isNaN(price)) { lastGoodPrice = price; console.log('✅ Backup gold API price:', price); }
        }
      } catch(e) { console.log('⚠️ Backup price also failed:', e.message); }
    }
    // If still no price, use last known good price
    if (!price || isNaN(price)) {
      price = lastGoodPrice;
      console.log('📦 Using cached price:', price);
    }
    console.log('💰 Price:', price);

    // Step 2: Fetch 1H candles (with fallback to cached candles)
    let candles = [];
    try {
      const candleData = await fetchData(`https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1h&outputsize=30&apikey=${TD_KEY}`);
      candles = candleData.values || [];
      // If we got valid candles, cache them
      if (candles.length > 0 && candles[0].close) {
        lastGoodCandles = candles;
        console.log('✅ Fresh candles fetched:', candles.length);
      }
    } catch(e) { console.log('⚠️ Candle fetch failed:', e.message); }
    // If candles empty (rate limited), use last good candles
    if (candles.length === 0 || !candles[0] || !candles[0].close) {
      candles = lastGoodCandles;
      console.log('📦 Using cached candles:', candles.length);
    }

    // Record this price in rolling history (keep last 60 samples)
    if (price && !isNaN(price)) {
      priceHistory.push(price);
      if (priceHistory.length > 60) priceHistory.shift();
    }

    // If candles missing/corrupt but we have price history, SYNTHESIZE candles from it
    if ((candles.length === 0 || !candles[0] || !candles[0].close) && priceHistory.length >= 5) {
      console.log('🔧 Synthesizing candles from price history (' + priceHistory.length + ' samples)');
      candles = [];
      // Build newest-first synthetic candles from consecutive price samples
      for (let i = priceHistory.length - 1; i > 0; i--) {
        const c = priceHistory[i], o = priceHistory[i-1];
        candles.push({
          open:  o.toFixed(2),
          high:  Math.max(o, c).toFixed(2),
          low:   Math.min(o, c).toFixed(2),
          close: c.toFixed(2),
          datetime: new Date(Date.now() - (priceHistory.length-i)*60000).toISOString()
        });
      }
    }

    // If we STILL have no usable data at all, return safe HOLD (don't crash)
    if ((!price || isNaN(price)) && candles.length === 0) {
      console.log('❌ No price and no candles available — returning HOLD');
      const holdSig = { signal:'HOLD', confidence:0, price:0, sl:0, tp:0,
        reasoning:'Market data temporarily unavailable. Bot is collecting price samples and will resume shortly.',
        strategy:'WAITING', rsi:50, ma20:0, ma50:0, atr:0, ts: Date.now() };
      cachedSignal = holdSig;
      return holdSig;
    }

    const closes  = candles.map(c => parseFloat(c.close));
    const highs   = candles.map(c => parseFloat(c.high));
    const lows    = candles.map(c => parseFloat(c.low));

    // Step 3: Calculate indicators
    const rsi  = calcRSI(closes, 14);
    const ma20 = calcMA(closes, 20);
    const ma50 = calcMA(closes, 50);
    // ATR — adapt to available candles
    const atrN = Math.min(14, highs.length);
    const atr  = atrN > 0 ? parseFloat((highs.slice(0,atrN).map((h,i) => h - lows[i]).reduce((a,b)=>a+b,0)/atrN).toFixed(2)) : 0;

    // Recent highs and lows (support/resistance) — adapt to available data
    const hiN = Math.min(10, highs.length);
    const recentHigh = hiN > 0 ? parseFloat(Math.max(...highs.slice(0,hiN)).toFixed(2)) : price;
    const recentLow  = hiN > 0 ? parseFloat(Math.min(...lows.slice(0,hiN)).toFixed(2)) : price;

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
// ═══════════ PAYMENT ENDPOINTS ═══════════

// Return the public Razorpay key + plan info to frontend
app.get('/payment/config', (req, res) => {
  res.json({
    keyId: RAZORPAY_KEY_ID,
    testMode: RAZORPAY_KEY_ID.startsWith('rzp_test_'),
    plans: { Pro: PLANS.Pro.amount/100, Elite: PLANS.Elite.amount/100 }
  });
});

// Step 1: Create a Razorpay order (server-side, secret key stays here)
app.post('/payment/create-order', async (req, res) => {
  try {
    const { plan, uid, email } = req.body;
    if (!PLANS[plan]) return res.status(400).json({ error: 'Invalid plan' });

    const amount = PLANS[plan].amount;
    const receipt = 'nexus_' + Date.now();

    // Call Razorpay Orders API with Basic auth
    const auth = Buffer.from(`${RAZORPAY_KEY_ID}:${RAZORPAY_KEY_SECRET}`).toString('base64');
    const orderRes = await fetch('https://api.razorpay.com/v1/orders', {
      method: 'POST',
      headers: { 'Authorization': `Basic ${auth}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        amount: amount,
        currency: 'INR',
        receipt: receipt,
        notes: { plan, uid: uid||'', email: email||'' }
      })
    });
    const order = await orderRes.json();
    if (order.error) {
      console.log('Razorpay order error:', order.error.description);
      return res.status(400).json({ error: order.error.description });
    }
    console.log(`💳 Order created: ${order.id} for ${plan} (₹${amount/100})`);
    res.json({ orderId: order.id, amount: amount, currency: 'INR', plan, keyId: RAZORPAY_KEY_ID });
  } catch(err) {
    console.log('Create order error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// Step 2: Verify payment signature after user pays
app.post('/payment/verify', (req, res) => {
  try {
    const { razorpay_order_id, razorpay_payment_id, razorpay_signature, plan, uid } = req.body;

    // Verify the signature (proves payment is genuine, not faked)
    const body = razorpay_order_id + '|' + razorpay_payment_id;
    const expectedSig = crypto.createHmac('sha256', RAZORPAY_KEY_SECRET).update(body).digest('hex');

    if (expectedSig === razorpay_signature) {
      // Payment is genuine — upgrade the user
      if (uid) paidUsers[uid] = { plan, orderId: razorpay_order_id, paymentId: razorpay_payment_id, ts: Date.now() };
      console.log(`✅ Payment verified! ${uid||'user'} upgraded to ${plan}`);
      res.json({ ok: true, plan, message: 'Payment successful! Plan upgraded.' });
    } else {
      console.log('❌ Payment signature mismatch — possible fraud');
      res.status(400).json({ ok: false, error: 'Payment verification failed' });
    }
  } catch(err) {
    res.status(500).json({ error: err.message });
  }
});

// Check a user's current plan (from paid records)
app.get('/payment/status', (req, res) => {
  const { uid } = req.query;
  const record = uid && paidUsers[uid];
  res.json({ plan: record ? record.plan : 'Free', paid: !!record });
});

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
  // Seed price history FAST on startup — grab 10 samples quickly so bot works sooner
  (async () => {
    for (let i = 0; i < 10; i++) {
      try {
        const b = await fetchData('https://api.gold-api.com/price/XAU');
        if (b && b.price) {
          const p = parseFloat(b.price);
          if (p && !isNaN(p)) { lastGoodPrice = p; priceHistory.push(p); }
        }
      } catch(e) {}
      await new Promise(r => setTimeout(r, 1500)); // 1.5s between seed samples
    }
    console.log('🌱 Seeded price history with', priceHistory.length, 'samples');
    generateSmartSignal('XAUUSD');
  })();

  // Background price collector — samples gold price every 30s to build indicator history
  // This runs independently so we always have data even when Twelve Data candles are rate-limited
  setInterval(async () => {
    try {
      let p = 0;
      // Try backup free API first (no rate limit)
      try {
        const b = await fetchData('https://api.gold-api.com/price/XAU');
        if (b && b.price) p = parseFloat(b.price);
      } catch(e) {}
      // Fallback to Twelve Data price if backup failed
      if (!p || isNaN(p)) {
        try {
          const d = await fetchData(`https://api.twelvedata.com/price?symbol=XAU/USD&apikey=${TD_KEY}`);
          if (d && d.price) p = parseFloat(d.price);
        } catch(e) {}
      }
      if (p && !isNaN(p)) {
        lastGoodPrice = p;
        priceHistory.push(p);
        if (priceHistory.length > 60) priceHistory.shift();
        // Log every 10th sample to avoid spam
        if (priceHistory.length % 10 === 0) console.log('📈 Price history:', priceHistory.length, 'samples | latest $' + p.toFixed(2));
      }
    } catch(e) { console.log('Price collector error:', e.message); }
  }, 10000); // every 10 seconds — builds history 3x faster
});
