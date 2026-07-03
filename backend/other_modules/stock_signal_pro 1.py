import yfinance as yf
import pandas as pd
import warnings
from datetime import datetime
import time
import os
import sys
import subprocess
import platform

warnings.filterwarnings('ignore')


# ══════════════════════════════════════════════════════════════════════════════
# SYMBOL MAPPING: Stocks with different Yahoo Finance symbols
# ══════════════════════════════════════════════════════════════════════════════

SYMBOL_MAP = {
    # Large Cap - Symbol changes / mergers
    'TATAMOTORS': 'TATAMTRDVR',  # Tata Motors DVR
    'ZOMATO': 'ETERNAL',  # Zomato renamed to Eternal
    'ADANITRANS': 'ATGL',  # Renamed to Adani Total Gas
    
    # Mid Cap
    'GMRINFRA': 'GMRAIRPORT',  # Renamed to GMR Airports
    'ABBPOWER': 'ABB',  # ABB Power merged with ABB India
    'MAXFIN': 'MAXHEALTH',  # Max Financial → Max Healthcare
    'GSKPHARMA': 'GLAXO',  # GSK Pharma renamed
    'GUJGAS': 'GUJGASLTD',  # Gujarat Gas Ltd
    'SRTRANSFIN': 'SHRIRAMFIN',  # Shriram Transport → Shriram Finance
    
    # Small Cap - Various changes
    'WELSPUNIND': 'WELSPUNLIV',  # Welspun Living
    'SUVENPHARM': 'SUVENPHAR',  # Suven Pharma
    'EQUITAS': 'EQUITASBNK',  # Equitas Small Finance Bank
    'UJJIVAN': 'UJJIVANSFB',  # Ujjivan Small Finance Bank
    'IDFC': 'IDFCFIRSTB',  # IDFC First Bank
    'NIIT': 'NIITLTD',  # NIIT Ltd
    'PHILIPCARB': 'PCBL',  # Phillips Carbon renamed to PCBL
    'TATACOFFEE': 'TATACONSUM',  # Merged with Tata Consumer
    'NMDCSTEEL': 'NMDC',  # Part of NMDC
    'MINDAIND': 'UNOMINDA',  # Uno Minda
    'VALIANT': 'VALIANTORG',  # Valiant Organics
    'CENTURYTEX': 'CENTEXT',  # Century Textiles
}

# Stocks to skip (delisted, merged, or no data available)
SKIP_STOCKS = {
    'GET&D',  # Invalid symbol with special character
    'SUPPETRO',  # Delisted
    'WATERBASE',  # Very low volume / delisted
    'GARDENSILK',  # Delisted
    'SHANKARAA',  # Symbol changed significantly
    'TRANSPEK',  # Delisted
    'AMIORG',  # Delisted
    'MEGH',  # Use MEGHMANI instead
    'MEGHFINE',  # Use MEGHMANI instead
    'KALYANISL',  # Delisted
    'DFM',  # Delisted
    'ZUARIAGRO',  # Merged
    'AUTOLINEIND',  # Delisted
}


class StockAnalyzer:
    """Stock Signal Analyzer with Technical Indicators"""
    
    def __init__(self):
        self.indicators = {}
        self.score = 0
        self.signals = []
    
    def fetch_data(self, symbol, exchange="NSE"):
        """Fetch stock data from Yahoo Finance with retry logic"""
        
        # Check if symbol should be skipped
        if symbol in SKIP_STOCKS:
            return None
        
        # Get mapped symbol if available
        mapped_symbol = SYMBOL_MAP.get(symbol, symbol)
        
        # List of symbols to try
        symbols_to_try = [mapped_symbol]
        if mapped_symbol != symbol:
            symbols_to_try.append(symbol)
        
        # Try NSE first, then BSE
        suffixes = ['.NS', '.BO'] if exchange == "NSE" else ['.BO', '.NS']
        
        for sym in symbols_to_try:
            for suffix in suffixes:
                ticker = f"{sym}{suffix}"
                
                try:
                    stock = yf.download(ticker, period="6mo", progress=False, timeout=15)
                    if not stock.empty and len(stock) >= 30:
                        # Flatten MultiIndex columns if present
                        if isinstance(stock.columns, pd.MultiIndex):
                            stock.columns = stock.columns.get_level_values(0)
                        return stock
                except:
                    pass
        
        return None
    
    def calculate_rsi(self, data, period=14):
        """RSI - Relative Strength Index"""
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    def calculate_macd(self, data):
        """MACD - Moving Average Convergence Divergence"""
        ema12 = data['Close'].ewm(span=12, adjust=False).mean()
        ema26 = data['Close'].ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        histogram = macd - signal
        return macd, signal, histogram
    
    def calculate_bollinger(self, data, period=20):
        """Bollinger Bands"""
        sma = data['Close'].rolling(window=period).mean()
        std = data['Close'].rolling(window=period).std()
        upper = sma + (std * 2)
        lower = sma - (std * 2)
        return upper, sma, lower
    
    def calculate_stochastic(self, data, period=14):
        """Stochastic Oscillator"""
        low_min = data['Low'].rolling(window=period).min()
        high_max = data['High'].rolling(window=period).max()
        k = 100 * (data['Close'] - low_min) / (high_max - low_min)
        d = k.rolling(window=3).mean()
        return k, d
    
    def calculate_adx(self, data, period=14):
        """ADX - Average Directional Index"""
        high = data['High']
        low = data['Low']
        close = data['Close']
        
        plus_dm = high.diff()
        minus_dm = low.diff().abs() * -1
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()
        
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
        return adx, plus_di, minus_di
    
    def calculate_vwap(self, data):
        """VWAP - Volume Weighted Average Price"""
        typical_price = (data['High'] + data['Low'] + data['Close']) / 3
        vwap = (typical_price * data['Volume']).cumsum() / data['Volume'].cumsum()
        return vwap
    
    def analyze_stock(self, symbol, exchange="NSE", silent=False):
        """Analyze a single stock and return results"""
        
        # Fetch data
        data = self.fetch_data(symbol, exchange)
        if data is None or len(data) < 50:
            return None
        
        # Calculate all indicators
        data['RSI'] = self.calculate_rsi(data)
        data['MACD'], data['MACD_Signal'], data['MACD_Hist'] = self.calculate_macd(data)
        data['BB_Upper'], data['BB_Middle'], data['BB_Lower'] = self.calculate_bollinger(data)
        data['Stoch_K'], data['Stoch_D'] = self.calculate_stochastic(data)
        data['ADX'], data['Plus_DI'], data['Minus_DI'] = self.calculate_adx(data)
        data['VWAP'] = self.calculate_vwap(data)
        data['SMA_20'] = data['Close'].rolling(window=20).mean()
        data['SMA_50'] = data['Close'].rolling(window=50).mean()
        
        # Get latest values
        latest = data.iloc[-1]
        prev = data.iloc[-2]
        
        # Extract values safely
        try:
            price = float(latest['Close'])
            prev_close = float(prev['Close'])
            rsi = float(latest['RSI'])
            macd = float(latest['MACD'])
            macd_signal = float(latest['MACD_Signal'])
            stoch_k = float(latest['Stoch_K'])
            stoch_d = float(latest['Stoch_D'])
            adx = float(latest['ADX']) if not pd.isna(latest['ADX']) else 25
            plus_di = float(latest['Plus_DI']) if not pd.isna(latest['Plus_DI']) else 0
            minus_di = float(latest['Minus_DI']) if not pd.isna(latest['Minus_DI']) else 0
            sma_20 = float(latest['SMA_20'])
            sma_50 = float(latest['SMA_50'])
            bb_upper = float(latest['BB_Upper'])
            bb_lower = float(latest['BB_Lower'])
            vwap = float(latest['VWAP'])
        except:
            return None
        
        # Calculate score
        score = 0
        
        # 1. RSI Analysis (max: ±2)
        if rsi < 30:
            score += 2
            rsi_signal = "OVERSOLD ↑"
        elif rsi < 40:
            score += 1
            rsi_signal = "Mildly Oversold"
        elif rsi > 70:
            score -= 2
            rsi_signal = "OVERBOUGHT ↓"
        elif rsi > 60:
            score -= 1
            rsi_signal = "Mildly Overbought"
        else:
            rsi_signal = "Neutral"
        
        # 2. MACD Analysis (max: ±2)
        prev_macd = float(prev['MACD'])
        prev_macd_signal = float(prev['MACD_Signal'])
        
        if macd > macd_signal and prev_macd <= prev_macd_signal:
            score += 2
            macd_sig = "BULLISH CROSS ↑"
        elif macd > macd_signal:
            score += 1
            macd_sig = "Bullish"
        elif macd < macd_signal and prev_macd >= prev_macd_signal:
            score -= 2
            macd_sig = "BEARISH CROSS ↓"
        elif macd < macd_signal:
            score -= 1
            macd_sig = "Bearish"
        else:
            macd_sig = "Neutral"
        
        # 3. Moving Average Trend (max: ±2)
        if price > sma_20 > sma_50:
            score += 2
            ma_signal = "STRONG UPTREND ↑"
        elif sma_20 > sma_50:
            score += 1
            ma_signal = "Uptrend"
        elif price < sma_20 < sma_50:
            score -= 2
            ma_signal = "STRONG DOWNTREND ↓"
        elif sma_20 < sma_50:
            score -= 1
            ma_signal = "Downtrend"
        else:
            ma_signal = "Neutral"
        
        # 4. Bollinger Bands (max: ±1.5)
        bb_position = (price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper != bb_lower else 50
        if price < bb_lower:
            score += 1.5
            bb_signal = "BELOW LOWER ↑"
        elif price > bb_upper:
            score -= 1.5
            bb_signal = "ABOVE UPPER ↓"
        elif bb_position < 30:
            score += 0.5
            bb_signal = "Near Lower"
        elif bb_position > 70:
            score -= 0.5
            bb_signal = "Near Upper"
        else:
            bb_signal = "Middle"
        
        # 5. Stochastic (max: ±1.5)
        if stoch_k < 20 and stoch_k > stoch_d:
            score += 1.5
            stoch_signal = "OVERSOLD + Cross ↑"
        elif stoch_k < 20:
            score += 0.5
            stoch_signal = "Oversold"
        elif stoch_k > 80 and stoch_k < stoch_d:
            score -= 1.5
            stoch_signal = "OVERBOUGHT + Cross ↓"
        elif stoch_k > 80:
            score -= 0.5
            stoch_signal = "Overbought"
        else:
            stoch_signal = "Neutral"
        
        # 6. ADX Trend Strength (max: ±1)
        if adx > 25:
            if plus_di > minus_di:
                score += 1
                adx_signal = "Strong Bullish"
            else:
                score -= 1
                adx_signal = "Strong Bearish"
        else:
            adx_signal = "Weak Trend"
        
        # 7. VWAP (max: ±1)
        if price > vwap * 1.02:
            score += 1
            vwap_signal = "Above VWAP ↑"
        elif price < vwap * 0.98:
            score -= 1
            vwap_signal = "Below VWAP ↓"
        else:
            vwap_signal = "Near VWAP"
        
        # 8. Volume Analysis (max: ±0.5)
        avg_volume = data['Volume'].rolling(20).mean().iloc[-1]
        current_volume = float(latest['Volume'])
        vol_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        
        if vol_ratio > 1.5:
            if score > 0:
                score += 0.5
                vol_signal = "High Vol + Bullish"
            elif score < 0:
                score -= 0.5
                vol_signal = "High Vol + Bearish"
            else:
                vol_signal = "High Volume"
        else:
            vol_signal = "Normal"
        
        # Determine final signal
        if score >= 6:
            final_signal = "🟢🟢 STRONG BUY"
            signal_text = "STRONG BUY"
        elif score >= 3:
            final_signal = "🟢 BUY"
            signal_text = "BUY"
        elif score <= -6:
            final_signal = "🔴🔴 STRONG SELL"
            signal_text = "STRONG SELL"
        elif score <= -3:
            final_signal = "🔴 SELL"
            signal_text = "SELL"
        else:
            final_signal = "🟡 HOLD"
            signal_text = "HOLD"
        
        # Calculate change
        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close > 0 else 0
        
        return {
            'symbol': symbol,
            'exchange': exchange,
            'price': price,
            'change': change,
            'change_pct': change_pct,
            'rsi': rsi,
            'rsi_signal': rsi_signal,
            'macd_signal': macd_sig,
            'ma_signal': ma_signal,
            'bb_signal': bb_signal,
            'stoch_signal': stoch_signal,
            'adx_signal': adx_signal,
            'vwap_signal': vwap_signal,
            'vol_signal': vol_signal,
            'score': score,
            'signal': final_signal,
            'signal_text': signal_text
        }
    
    def analyze_single(self, symbol, exchange="NSE"):
        """Analyze and display single stock"""
        
        print(f"\n{'='*60}")
        print(f"  📊 ANALYZING: {symbol} ({exchange})")
        print(f"  📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")
        
        result = self.analyze_stock(symbol, exchange)
        
        if result is None:
            print("\n❌ Error: Could not fetch data. Check symbol name.")
            print("   Examples: RELIANCE, TCS, INFY, HDFCBANK")
            return None
        
        # Display results
        print(f"\n💰 Current Price: ₹{result['price']:.2f}")
        change_symbol = "↑" if result['change'] >= 0 else "↓"
        print(f"📈 Change: ₹{result['change']:.2f} ({result['change_pct']:+.2f}%) {change_symbol}")
        
        print(f"\n{'─'*60}")
        print(f"{'INDICATOR':<15} {'SIGNAL':<25} {'CONTRIBUTION':<15}")
        print(f"{'─'*60}")
        
        print(f"{'RSI':<15} {result['rsi_signal']:<25} {'±2 points':<15}")
        print(f"{'MACD':<15} {result['macd_signal']:<25} {'±2 points':<15}")
        print(f"{'MA Trend':<15} {result['ma_signal']:<25} {'±2 points':<15}")
        print(f"{'Bollinger':<15} {result['bb_signal']:<25} {'±1.5 points':<15}")
        print(f"{'Stochastic':<15} {result['stoch_signal']:<25} {'±1.5 points':<15}")
        print(f"{'ADX':<15} {result['adx_signal']:<25} {'±1 point':<15}")
        print(f"{'VWAP':<15} {result['vwap_signal']:<25} {'±1 point':<15}")
        print(f"{'Volume':<15} {result['vol_signal']:<25} {'±0.5 points':<15}")
        
        print(f"{'─'*60}")
        print(f"\n📊 TOTAL SCORE: {result['score']:.1f} (Range: -12 to +12)")
        
        print(f"\n{'='*60}")
        print(f"  🎯 SIGNAL: {result['signal']}")
        print(f"{'='*60}")
        
        # Score guide
        print(f"\n{'─'*60}")
        print("SCORE GUIDE:")
        print("  +6 to +12  →  STRONG BUY")
        print("  +3 to +6   →  BUY")
        print("  -3 to +3   →  HOLD")
        print("  -6 to -3   →  SELL")
        print("  -12 to -6  →  STRONG SELL")
        print(f"{'─'*60}")
        
        return result


class CategoryAnalyzer:
    """Analyze stocks by category from Excel file"""
    
    def __init__(self, excel_path):
        self.excel_path = excel_path
        self.stock_analyzer = StockAnalyzer()
        self.results = []
        self.failed_stocks = []
    
    def load_stocks(self, category):
        """Load stocks from Excel file"""
        
        sheet_map = {
            'large': 'Large Cap (100)',
            'mid': 'Mid Cap (150)',
            'small': 'Small Cap (250)'
        }
        
        if category == 'all':
            all_stocks = []
            for cat, sheet_name in sheet_map.items():
                stocks = self._load_sheet(sheet_name, cat)
                all_stocks.extend(stocks)
            return all_stocks
        else:
            sheet_name = sheet_map.get(category)
            if sheet_name:
                return self._load_sheet(sheet_name, category)
        
        return []
    
    def _load_sheet(self, sheet_name, category):
        """Load stocks from a specific sheet"""
        try:
            df = pd.read_excel(self.excel_path, sheet_name=sheet_name, skiprows=2)
            df.columns = ['Sr No', 'Company', 'Symbol', 'Sector', 'Market Cap']
            df = df.dropna(subset=['Symbol'])
            
            stocks = []
            seen_symbols = set()
            
            for _, row in df.iterrows():
                symbol = str(row['Symbol']).strip().upper()
                
                # Skip duplicates
                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                
                # Skip known bad symbols
                if symbol in SKIP_STOCKS:
                    continue
                
                stocks.append({
                    'symbol': symbol,
                    'company': str(row['Company']).strip(),
                    'sector': str(row['Sector']).strip(),
                    'market_cap': str(row['Market Cap']).strip(),
                    'category': category
                })
            
            return stocks
        except Exception as e:
            print(f"Error loading {sheet_name}: {e}")
            return []
    
    def print_progress_bar(self, current, total, stock_name, start_time, bar_length=30):
        """Print progress bar with ETA"""
        
        progress = current / total
        filled = int(bar_length * progress)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        elapsed = time.time() - start_time
        if current > 0:
            eta = (elapsed / current) * (total - current)
            eta_str = f"{int(eta//60)}m {int(eta%60)}s"
        else:
            eta_str = "calculating..."
        
        elapsed_str = f"{int(elapsed//60)}m {int(elapsed%60)}s"
        stock_display = stock_name[:12].ljust(12)
        
        sys.stdout.write('\r')
        sys.stdout.write(' ' * 85)
        sys.stdout.write('\r')
        sys.stdout.write(f"⏳ [{bar}] {current}/{total} ({progress*100:.0f}%) | {stock_display} | {elapsed_str} | ETA: {eta_str}")
        sys.stdout.flush()
    
    def open_excel_file(self, filepath):
        """Open Excel file automatically based on OS"""
        try:
            if platform.system() == 'Windows':
                os.startfile(filepath)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.call(['open', filepath])
            else:  # Linux
                subprocess.call(['xdg-open', filepath])
            return True
        except Exception as e:
            print(f"\n⚠️  Could not auto-open file: {e}")
            return False
    
    def analyze_category(self, category):
        """Analyze all stocks in a category"""
        
        stocks = self.load_stocks(category)
        
        if not stocks:
            print("❌ No stocks found. Check Excel file.")
            return None
        
        category_names = {
            'large': 'LARGE CAP',
            'mid': 'MID CAP',
            'small': 'SMALL CAP',
            'all': 'ALL STOCKS'
        }
        
        category_name = category_names.get(category, category.upper())
        total_stocks = len(stocks)
        
        print(f"\n{'═'*75}")
        print(f"              📊 ANALYZING {category_name} ({total_stocks} stocks)")
        print(f"              📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'═'*75}\n")
        
        self.results = []
        self.failed_stocks = []
        
        start_time = time.time()
        
        for i, stock in enumerate(stocks, 1):
            symbol = stock['symbol']
            self.print_progress_bar(i, total_stocks, symbol, start_time)
            
            result = self.stock_analyzer.analyze_stock(symbol, "NSE", silent=True)
            
            if result:
                result['company'] = stock['company']
                result['sector'] = stock['sector']
                result['market_cap'] = stock['market_cap']
                result['category'] = stock['category']
                self.results.append(result)
            else:
                self.failed_stocks.append(stock)
            
            time.sleep(0.05)
        
        print("\n\n")
        
        elapsed = time.time() - start_time
        elapsed_str = f"{int(elapsed//60)}m {int(elapsed%60)}s"
        
        self.display_results(category_name, total_stocks, elapsed_str)
        self.export_to_excel(category_name)
        
        return self.results
    
    def display_results(self, category_name, total_stocks, elapsed_time):
        """Display analysis results"""
        
        if not self.results:
            print("❌ No results to display.")
            return
        
        # Remove duplicates
        seen = set()
        unique_results = []
        for r in self.results:
            if r['symbol'] not in seen:
                seen.add(r['symbol'])
                unique_results.append(r)
        
        self.results = unique_results
        sorted_results = sorted(self.results, key=lambda x: x['score'], reverse=True)
        
        # Count signals
        strong_buy = len([r for r in self.results if r['score'] >= 6])
        buy = len([r for r in self.results if 3 <= r['score'] < 6])
        hold = len([r for r in self.results if -3 < r['score'] < 3])
        sell = len([r for r in self.results if -6 < r['score'] <= -3])
        strong_sell = len([r for r in self.results if r['score'] <= -6])
        
        analyzed = len(self.results)
        failed = len(self.failed_stocks)
        
        print(f"{'═'*75}")
        print(f"              ✅ ANALYSIS COMPLETE - {category_name}")
        print(f"              📊 {analyzed} analyzed | ❌ {failed} failed | ⏱️ {elapsed_time}")
        print(f"{'═'*75}")
        
        # Signal distribution
        print(f"\n┌{'─'*73}┐")
        print(f"│{'📊 SIGNAL DISTRIBUTION':^73}│")
        print(f"├{'─'*73}┤")
        
        total = max(analyzed, 1)
        
        def make_bar(count, total, max_len=40):
            pct = count / total
            filled = int(max_len * pct)
            return '█' * filled + '░' * (max_len - filled)
        
        print(f"│  🟢🟢 STRONG BUY   {make_bar(strong_buy, total)}  {strong_buy:3} ({strong_buy/total*100:5.1f}%) │")
        print(f"│  🟢 BUY           {make_bar(buy, total)}  {buy:3} ({buy/total*100:5.1f}%) │")
        print(f"│  🟡 HOLD          {make_bar(hold, total)}  {hold:3} ({hold/total*100:5.1f}%) │")
        print(f"│  🔴 SELL          {make_bar(sell, total)}  {sell:3} ({sell/total*100:5.1f}%) │")
        print(f"│  🔴🔴 STRONG SELL  {make_bar(strong_sell, total)}  {strong_sell:3} ({strong_sell/total*100:5.1f}%) │")
        print(f"└{'─'*73}┘")
        
        # TOP 10 BUY
        buy_stocks = [r for r in sorted_results if r['score'] >= 3][:10]
        
        print(f"\n📈 TOP 10 BUY OPPORTUNITIES (Highest Scores):")
        print(f"{'─'*75}")
        print(f" {'#':<3} {'SYMBOL':<12} {'COMPANY':<24} {'PRICE':>10} {'SCORE':>7}   {'SIGNAL':<15}")
        print(f"{'─'*75}")
        
        for i, r in enumerate(buy_stocks, 1):
            company = r['company'][:22] + '..' if len(r['company']) > 22 else r['company']
            print(f" {i:<3} {r['symbol']:<12} {company:<24} ₹{r['price']:>8,.0f} {r['score']:>+6.1f}   {r['signal']:<15}")
        
        if not buy_stocks:
            print(f" {'(No BUY signals found)':<73}")
        
        # TOP 10 SELL
        sell_stocks = sorted([r for r in sorted_results if r['score'] <= -3], key=lambda x: x['score'])[:10]
        
        print(f"\n📉 TOP 10 SELL WARNINGS (Lowest Scores):")
        print(f"{'─'*75}")
        print(f" {'#':<3} {'SYMBOL':<12} {'COMPANY':<24} {'PRICE':>10} {'SCORE':>7}   {'SIGNAL':<15}")
        print(f"{'─'*75}")
        
        for i, r in enumerate(sell_stocks, 1):
            company = r['company'][:22] + '..' if len(r['company']) > 22 else r['company']
            print(f" {i:<3} {r['symbol']:<12} {company:<24} ₹{r['price']:>8,.0f} {r['score']:>+6.1f}   {r['signal']:<15}")
        
        if not sell_stocks:
            print(f" {'(No SELL signals found)':<73}")
        
        # Sector Summary
        self.display_sector_summary()
        
        # Failed stocks
        if self.failed_stocks:
            print(f"\n⚠️  {len(self.failed_stocks)} stocks failed to fetch:")
            failed_symbols = [s['symbol'] for s in self.failed_stocks[:10]]
            print(f"   {', '.join(failed_symbols)}", end='')
            if len(self.failed_stocks) > 10:
                print(f" ... and {len(self.failed_stocks)-10} more")
            else:
                print()
    
    def display_sector_summary(self):
        """Display sector-wise summary"""
        
        if not self.results:
            return
        
        sectors = {}
        for r in self.results:
            sector = r['sector']
            if sector not in sectors:
                sectors[sector] = {'buy': 0, 'hold': 0, 'sell': 0, 'stocks': []}
            
            if r['score'] >= 3:
                sectors[sector]['buy'] += 1
            elif r['score'] <= -3:
                sectors[sector]['sell'] += 1
            else:
                sectors[sector]['hold'] += 1
            
            sectors[sector]['stocks'].append(r)
        
        sorted_sectors = sorted(sectors.items(), key=lambda x: x[1]['buy'], reverse=True)
        
        print(f"\n📊 SECTOR SUMMARY (Top 10 by BUY signals):")
        print(f"{'─'*75}")
        print(f" {'SECTOR':<30} {'🟢 BUY':>8} {'🟡 HOLD':>8} {'🔴 SELL':>8} {'BEST STOCK':<15}")
        print(f"{'─'*75}")
        
        for sector, data in sorted_sectors[:10]:
            sector_name = sector[:28] + '..' if len(sector) > 28 else sector
            best = max(data['stocks'], key=lambda x: x['score'])
            print(f" {sector_name:<30} {data['buy']:>8} {data['hold']:>8} {data['sell']:>8} {best['symbol']:<15}")
        
        print(f"{'─'*75}")
    
    def export_to_excel(self, category_name):
        """Export results to Excel file and auto-open it"""
        
        if not self.results:
            return None
        
        date_str = datetime.now().strftime('%Y-%m-%d_%H-%M')
        filename = f"{category_name.replace(' ', '_')}_Analysis_{date_str}.xlsx"
        filepath = os.path.join(os.getcwd(), filename)
        
        df_all = pd.DataFrame(self.results)
        
        columns = ['symbol', 'company', 'sector', 'price', 'change_pct', 'rsi', 
                   'macd_signal', 'ma_signal', 'score', 'signal_text']
        
        df_export = df_all[columns].copy()
        df_export.columns = ['Symbol', 'Company', 'Sector', 'Price (₹)', 'Change %', 
                            'RSI', 'MACD', 'Trend', 'Score', 'Signal']
        
        df_export = df_export.sort_values('Score', ascending=False)
        df_export['Price (₹)'] = df_export['Price (₹)'].round(2)
        df_export['Change %'] = df_export['Change %'].round(2)
        df_export['RSI'] = df_export['RSI'].round(1)
        df_export['Score'] = df_export['Score'].round(1)
        
        try:
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                df_export.to_excel(writer, sheet_name='All Stocks', index=False)
                
                df_buy = df_export[df_export['Score'] >= 3]
                df_buy.to_excel(writer, sheet_name='BUY Stocks', index=False)
                
                df_sell = df_export[df_export['Score'] <= -3].sort_values('Score')
                df_sell.to_excel(writer, sheet_name='SELL Stocks', index=False)
                
                df_hold = df_export[(df_export['Score'] > -3) & (df_export['Score'] < 3)]
                df_hold.to_excel(writer, sheet_name='HOLD Stocks', index=False)
                
                summary_data = {
                    'Metric': ['Total Analyzed', 'Strong Buy (≥6)', 'Buy (3 to 6)', 
                              'Hold (-3 to 3)', 'Sell (-6 to -3)', 'Strong Sell (≤-6)', 'Failed'],
                    'Count': [
                        len(self.results),
                        len(df_export[df_export['Score'] >= 6]),
                        len(df_export[(df_export['Score'] >= 3) & (df_export['Score'] < 6)]),
                        len(df_export[(df_export['Score'] > -3) & (df_export['Score'] < 3)]),
                        len(df_export[(df_export['Score'] > -6) & (df_export['Score'] <= -3)]),
                        len(df_export[df_export['Score'] <= -6]),
                        len(self.failed_stocks)
                    ]
                }
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)
            
            print(f"\n{'═'*75}")
            print(f"📁 RESULTS SAVED TO: {filename}")
            print(f"{'─'*75}")
            print(f"   Sheets: All Stocks | BUY Stocks | SELL Stocks | HOLD Stocks | Summary")
            print(f"{'─'*75}")
            
            # AUTO-OPEN THE EXCEL FILE
            print(f"\n📂 Opening Excel file automatically...")
            time.sleep(1)  # Small delay before opening
            
            if self.open_excel_file(filepath):
                print(f"✅ Excel file opened successfully!")
            else:
                print(f"   Please open manually: {filepath}")
            
            return filepath
            
        except Exception as e:
            print(f"\n⚠️  Could not save Excel file: {e}")
            return None


def main():
    """Main function"""
    
    excel_file = "Indian_Stocks_Complete_Market_Cap_Classification.xlsx"
    
    if not os.path.exists(excel_file):
        alt_path = "/mnt/user-data/uploads/Indian_Stocks_Complete_Market_Cap_Classification.xlsx"
        if os.path.exists(alt_path):
            excel_file = alt_path
        else:
            print(f"\n⚠️  Excel file not found: {excel_file}")
            print(f"   Place the file in the same folder as this script.")
            excel_file = None
    
    stock_analyzer = StockAnalyzer()
    category_analyzer = CategoryAnalyzer(excel_file) if excel_file else None
    
    while True:
        print("\n" + "═"*75)
        print("   🇮🇳 INDIAN STOCK SIGNAL ANALYZER (NSE/BSE) - v2.0")
        print("═"*75)
        
        print("\n📌 Choose an option:\n")
        print("  [1] 🔍 Analyze Single Stock (Enter symbol)")
        
        if category_analyzer:
            print("\n  [2] 📊 Analyze Stock Category:")
            print("      ├── Large Cap  (100 stocks) - ~2-3 min")
            print("      ├── Mid Cap    (150 stocks) - ~3-4 min")
            print("      ├── Small Cap  (250 stocks) - ~5-6 min")
            print("      └── ALL STOCKS (500 stocks) - ~8-10 min")
        else:
            print("\n  [2] 📊 Category Analysis (Excel file not found)")
        
        print("\n  [3] 🚪 Quit")
        
        choice = input("\n🔍 Enter your choice (1/2/3): ").strip()
        
        if choice == '1':
            symbol = input("\n📈 Enter Stock Symbol (e.g., RELIANCE): ").strip().upper()
            
            if not symbol:
                print("⚠️  Please enter a valid symbol.")
                continue
            
            exchange = input("📍 Exchange [NSE/BSE] (default: NSE): ").strip().upper()
            if exchange not in ["NSE", "BSE"]:
                exchange = "NSE"
            
            stock_analyzer.analyze_single(symbol, exchange)
        
        elif choice == '2':
            if not category_analyzer:
                print("\n❌ Excel file not found. Cannot perform category analysis.")
                continue
            
            print("\n📊 SELECT CATEGORY:\n")
            print("  [1] Large Cap (100 stocks)")
            print("  [2] Mid Cap (150 stocks)")
            print("  [3] Small Cap (250 stocks)")
            print("  [4] ALL STOCKS (500 stocks)")
            print("  [5] Back to main menu")
            
            cat_choice = input("\n🔍 Enter your choice (1-5): ").strip()
            
            if cat_choice == '1':
                category_analyzer.analyze_category('large')
            elif cat_choice == '2':
                category_analyzer.analyze_category('mid')
            elif cat_choice == '3':
                category_analyzer.analyze_category('small')
            elif cat_choice == '4':
                print("\n⚠️  This will take ~8-10 minutes. Continue? (y/n): ", end='')
                if input().strip().lower() == 'y':
                    category_analyzer.analyze_category('all')
                else:
                    print("   Cancelled.")
            elif cat_choice == '5':
                continue
            else:
                print("⚠️  Invalid choice.")
        
        elif choice == '3':
            print("\n👋 Goodbye! Happy Trading!")
            break
        
        else:
            print("⚠️  Invalid choice. Please enter 1, 2, or 3.")


if __name__ == "__main__":
    main()
