import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime

# Page config
st.set_page_config(
    page_title="Stock Analysis & Comparison",
    page_icon="📊",
    layout="wide"
)

# Enhanced CSS
st.markdown("""
<style>
    .company-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 30px;
        border-radius: 12px;
        color: white;
        margin: 20px 0;
        box-shadow: 0 8px 16px rgba(0,0,0,0.2);
    }
    .company-header h1 {
        margin: 0;
        font-size: 2.5rem;
        color: white;
        font-weight: 700;
    }
    .company-header a {
        color: #ffd700;
        text-decoration: none;
        font-weight: 600;
    }
    .section-title {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 15px 25px;
        border-radius: 8px;
        margin: 25px 0 15px 0;
        font-size: 1.4rem;
        font-weight: 700;
        box-shadow: 0 4px 8px rgba(0,0,0,0.15);
    }
    .metric-card {
        background: white;
        border-left: 4px solid #667eea;
        padding: 15px;
        border-radius: 8px;
        margin: 10px 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .metric-label {
        font-weight: 600;
        color: #555;
        font-size: 0.95rem;
        margin-bottom: 5px;
    }
    .metric-value {
        color: #667eea;
        font-weight: 700;
        font-size: 1.3rem;
    }
    .pros-section {
        background: linear-gradient(90deg, #66bb6a 0%, #4caf50 100%);
        color: white;
        padding: 15px 25px;
        border-radius: 8px;
        margin: 25px 0 10px 0;
        font-size: 1.3rem;
        font-weight: 700;
    }
    .cons-section {
        background: linear-gradient(90deg, #ff9800 0%, #f57c00 100%);
        color: white;
        padding: 15px 25px;
        border-radius: 8px;
        margin: 25px 0 10px 0;
        font-size: 1.3rem;
        font-weight: 700;
    }
    .pros-item, .cons-item {
        background: #f5f5f5;
        padding: 12px;
        margin: 8px 0;
        border-radius: 6px;
        color: #333;
    }
    .pros-item {
        border-left: 3px solid #4caf50;
    }
    .cons-item {
        border-left: 3px solid #ff9800;
    }
    .cap-badge {
        background: rgba(255,255,255,0.2);
        padding: 8px 20px;
        border-radius: 25px;
        display: inline-block;
        margin: 5px;
        font-size: 1.1rem;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

STOCK_DATABASE = {
    "ABSLAMC": {"symbol": "ABSLAMC", "yf_symbol": "ABSLAMC.NS", "sector": "Financial Services"},
    "ARE&M": {"symbol": "ARE&M", "yf_symbol": "ARE&M.NS", "sector": "Auto Components"},
    "ajanta pharma": {"symbol": "AJANTPHARM", "yf_symbol": "AJANTPHARM.NS", "sector": "Healthcare"},
    "Bajaj Consumer Care Ltd.": {"symbol": "BAJAJCON", "yf_symbol": "BAJAJCON.NS", "sector": "FMCG"},
    "BAJAJFINSV": {"symbol": "BAJAJFINSV", "yf_symbol": "BAJAJFINSV.NS", "sector": "Financial Services"},
    "BAJAJHFL": {"symbol": "BAJAJHFL", "yf_symbol": "BAJAJHFL.NS", "sector": "Financial Services"},
    "Bajaj Holdings & Investment Ltd.": {"symbol": "BAJAJHLDNG", "yf_symbol": "BAJAJHLDNG.NS", "sector": "Financial Services"},
    "Bandhan Bank": {"symbol": "BANDHANBNK", "yf_symbol": "BANDHANBNK.NS", "sector": "Financial Services"},
    "Bharti Hexacom": {"symbol": "BHARTIHEXA", "yf_symbol": "BHARTIHEXA.NS", "sector": "Telecommunication"},
    "Biocon": {"symbol": "BIOCON", "yf_symbol": "BIOCON.NS", "sector": "Healthcare"},
    "Bayer Crop": {"symbol": "BAYERCROP", "yf_symbol": "BAYERCROP.NS", "sector": "Chemicals"},
    "BEML Land Assets Ltd.": {"symbol": "BLAL", "yf_symbol": "BLAL.NS", "sector": "Realty"},
    "Bajaj finance": {"symbol": "BAJFINANCE", "yf_symbol": "BAJFINANCE.NS", "sector": "Financial Services"},
    "bikaji": {"symbol": "BIKAJI", "yf_symbol": "BIKAJI.NS", "sector": "FMCG"},
    "BSE Ltd.": {"symbol": "BSE", "yf_symbol": "BSE.NS", "sector": "Financial Services"},
    "CAMS": {"symbol": "CAMS", "yf_symbol": "CAMS.NS", "sector": "Financial Services"},
    "COAL india": {"symbol": "COALINDIA", "yf_symbol": "COALINDIA.NS", "sector": "Metals & Mining"},
    "City Union Bank Ltd.": {"symbol": "CUB", "yf_symbol": "CUB.NS", "sector": "Financial Services"},
    "cham bl fert": {"symbol": "CHAMBLFERT", "yf_symbol": "CHAMBLFERT.NS", "sector": "Chemicals"},
    "can fin home": {"symbol": "CANFINHOME", "yf_symbol": "CANFINHOME.NS", "sector": "Financial Services"},
    "Divi's Laboratories Ltd.": {"symbol": "DIVISLAB", "yf_symbol": "DIVISLAB.NS", "sector": "Healthcare"},
    "Dabur": {"symbol": "DABUR", "yf_symbol": "DABUR.NS", "sector": "FMCG"},
    "dhampur sugar": {"symbol": "DHAMPURSUG", "yf_symbol": "DHAMPURSUG.NS", "sector": "Consumer Services"},
    "Engineers India Ltd.": {"symbol": "ENGINERSIN", "yf_symbol": "ENGINERSIN.NS", "sector": "Construction"},
    "EIHOTEL": {"symbol": "EIHOTEL", "yf_symbol": "EIHOTEL.NS", "sector": "Consumer Services"},
    "Equitas Small Finance Bank Ltd.": {"symbol": "EQUITASBNK", "yf_symbol": "EQUITASBNK.NS", "sector": "Financial Services"},
    "General Insurance Corporation of India": {"symbol": "GICRE", "yf_symbol": "GICRE.NS", "sector": "Financial Services"},
    "Global Health Ltd.": {"symbol": "MEDANTA", "yf_symbol": "MEDANTA.NS", "sector": "Healthcare"},
    "GPiL": {"symbol": "GPIL", "yf_symbol": "GPIL.NS", "sector": "Metals & Mining"},
    "Godigit": {"symbol": "GODIGIT", "yf_symbol": "GODIGIT.NS", "sector": "Financial Services"},
    "Gujarat Pipavav Port Ltd.": {"symbol": "GPPL", "yf_symbol": "GPPL.NS", "sector": "Services"},
    "HDFC Bank Ltd.": {"symbol": "HDFCBANK", "yf_symbol": "HDFCBANK.NS", "sector": "Financial Services"},
    "hdfc life": {"symbol": "HDFCLIFE", "yf_symbol": "HDFCLIFE.NS", "sector": "Financial Services"},
    "HAL": {"symbol": "HAL", "yf_symbol": "HAL.NS", "sector": "Capital Goods"},
    "Housing and Urban Development Corporation Ltd.": {"symbol": "HUDCO", "yf_symbol": "HUDCO.NS", "sector": "Financial Services"},
    "HEMIPROP": {"symbol": "HEMIPROP", "yf_symbol": "HEMIPROP.NS", "sector": "Realty"},
    "HINDUNILVR": {"symbol": "HINDUNILVR", "yf_symbol": "HINDUNILVR.NS", "sector": "FMCG"},
    "ITC Ltd.": {"symbol": "ITC", "yf_symbol": "ITC.NS", "sector": "FMCG"},
    "IDEA": {"symbol": "IDEA", "yf_symbol": "IDEA.NS", "sector": "Telecommunication"},
    "idfcfirstb": {"symbol": "IDFCFIRSTB", "yf_symbol": "IDFCFIRSTB.NS", "sector": "Financial Services"},
    "IRCON": {"symbol": "IRCON", "yf_symbol": "IRCON.NS", "sector": "Construction"},
    "impal": {"symbol": "IMPAL", "yf_symbol": "IMPAL.NS", "sector": "Auto Components"},
    "indus tower": {"symbol": "INDUSTOWER", "yf_symbol": "INDUSTOWER.NS", "sector": "Telecommunication"},
    "India Shelter": {"symbol": "INDIASHLTR", "yf_symbol": "INDIASHLTR.NS", "sector": "Financial Services"},
    "india mart": {"symbol": "INDIAMART", "yf_symbol": "INDIAMART.NS", "sector": "Consumer Services"},
    "inox wind": {"symbol": "INOXWIND", "yf_symbol": "INOXWIND.NS", "sector": "Power"},
    "IRCTC": {"symbol": "IRCTC", "yf_symbol": "IRCTC.NS", "sector": "Services"},
    "Jio Financial Services Ltd.": {"symbol": "JIOFIN", "yf_symbol": "JIOFIN.NS", "sector": "Financial Services"},
    "kama holding": {"symbol": "KAMAHOLD", "yf_symbol": "KAMAHOLD.NS", "sector": "Financial Services"},
    "Kotak": {"symbol": "KOTAKBANK", "yf_symbol": "KOTAKBANK.NS", "sector": "Financial Services"},
    "laurus": {"symbol": "LAURUSLABS", "yf_symbol": "LAURUSLABS.NS", "sector": "Healthcare"},
    "Life Insurance Corporation of India": {"symbol": "LICI", "yf_symbol": "LICI.NS", "sector": "Financial Services"},
    "Maharashtra Scooters Ltd.": {"symbol": "MAHSCOOTER", "yf_symbol": "MAHSCOOTER.NS", "sector": "Financial Services"},
    "mahanagar gas": {"symbol": "MGL", "yf_symbol": "MGL.NS", "sector": "Oil, Gas & Consumable Fuels"},
    "medanta": {"symbol": "MEDANTA", "yf_symbol": "MEDANTA.NS", "sector": "Healthcare"},
    "MOIL": {"symbol": "MOIL", "yf_symbol": "MOIL.NS", "sector": "Metals & Mining"},
    "NHPC": {"symbol": "NHPC", "yf_symbol": "NHPC.NS", "sector": "Power"},
    "NLCINDIA": {"symbol": "NLCINDIA", "yf_symbol": "NLCINDIA.NS", "sector": "Power"},
    "NSLNISP": {"symbol": "NSLNISP", "yf_symbol": "NSLNISP.NS", "sector": "Metals & Mining"},
    "NMDC": {"symbol": "NMDC", "yf_symbol": "NMDC.NS", "sector": "Metals & Mining"},
    "nestle": {"symbol": "NESTLEIND", "yf_symbol": "NESTLEIND.NS", "sector": "FMCG"},
    "Noida Toll Bridge Company Ltd.": {"symbol": "NOIDATOLL", "yf_symbol": "NOIDATOLL.NS", "sector": "Construction"},
    "OIL": {"symbol": "OIL", "yf_symbol": "OIL.NS", "sector": "Oil, Gas & Consumable Fuels"},
    "ongc": {"symbol": "ONGC", "yf_symbol": "ONGC.NS", "sector": "Oil, Gas & Consumable Fuels"},
    "PIIND": {"symbol": "PIIND", "yf_symbol": "PIIND.NS", "sector": "Chemicals"},
    "PNB Housing Finance Ltd.": {"symbol": "PNBHOUSING", "yf_symbol": "PNBHOUSING.NS", "sector": "Financial Services"},
    "PTL": {"symbol": "PTL", "yf_symbol": "PTL.NS", "sector": "Auto Components"},
    "Reliance Industries Ltd.": {"symbol": "RELIANCE", "yf_symbol": "RELIANCE.NS", "sector": "Oil, Gas & Consumable Fuels"},
    "rolex": {"symbol": "ROLEXRINGS", "yf_symbol": "ROLEXRINGS.NS", "sector": "Auto Components"},
    "Satia": {"symbol": "SATIA", "yf_symbol": "SATIA.NS", "sector": "Forest Materials"},
    "Sheela Foam Ltd.": {"symbol": "SFL", "yf_symbol": "SFL.NS", "sector": "Consumer Durables"},
    "SBIN": {"symbol": "SBIN", "yf_symbol": "SBIN.NS", "sector": "Financial Services"},
    "sbi card and payment": {"symbol": "SBICARD", "yf_symbol": "SBICARD.NS", "sector": "Financial Services"},
    "sbi life insurance company": {"symbol": "SBILIFE", "yf_symbol": "SBILIFE.NS", "sector": "Financial Services"},
    "Swaraj Engines Ltd.": {"symbol": "SWARAJENG", "yf_symbol": "SWARAJENG.NS", "sector": "Auto Components"},
    "SJVN": {"symbol": "SJVN", "yf_symbol": "SJVN.NS", "sector": "Power"},
    "SCI": {"symbol": "SCI", "yf_symbol": "SCI.NS", "sector": "Services"},
    "sailife": {"symbol": "SAILIFE", "yf_symbol": "SAILIFE.NS", "sector": "Healthcare"},
    "SCILAL": {"symbol": "SCILAL", "yf_symbol": "SCILAL.NS", "sector": "Realty"},
    "syngene": {"symbol": "SYNGENE", "yf_symbol": "SYNGENE.NS", "sector": "Healthcare"},
    "SUNDARMHLD": {"symbol": "SUNDARMHLD", "yf_symbol": "SUNDARMHLD.NS", "sector": "Financial Services"},
    "Tata Consultancy Services Ltd.": {"symbol": "TCS", "yf_symbol": "TCS.NS", "sector": "Information Technology"},
    "Tata Motors Ltd.": {"symbol": "TATAMOTORS", "yf_symbol": "TATAMOTORS.NS", "sector": "Automobile"},
    "tata tech": {"symbol": "TATATECH", "yf_symbol": "TATATECH.NS", "sector": "Information Technology"},
    "UFO": {"symbol": "UFO", "yf_symbol": "UFO.NS", "sector": "Media & Entertainment"},
    "UPL": {"symbol": "UPL", "yf_symbol": "UPL.NS", "sector": "Chemicals"},
    "VMM": {"symbol": "VMM", "yf_symbol": "VMM.NS", "sector": "Consumer Services"},
    "TIINDIA": {"symbol": "TIINDIA", "yf_symbol": "TIINDIA.NS", "sector": "Auto Components"},
    "TNPL": {"symbol": "TNPL", "yf_symbol": "TNPL.NS", "sector": "Forest Materials"},
    "The United Nilgiri Tea Estates Company Ltd.": {"symbol": "UNITEDTEA", "yf_symbol": "UNITEDTEA.NS", "sector": "Consumer Goods"},
    "Wipro Ltd.": {"symbol": "WIPRO", "yf_symbol": "WIPRO.NS", "sector": "Information Technology"},
    "Wonderla Holidays Ltd.": {"symbol": "WONDERLA", "yf_symbol": "WONDERLA.NS", "sector": "Consumer Services"},
    "Bajaj Auto": {"symbol": "BAJAJ-AUTO", "yf_symbol": "BAJAJ-AUTO.NS", "sector": "Automobile"},
}


def get_market_cap_category(market_cap_cr):
    """
    Classifies company based on SEBI-style market cap standards.
    Large Cap: > ₹20,000 Cr
    Mid Cap: ₹5,000 - ₹20,000 Cr
    Small Cap: < ₹5,000 Cr
    """
    if market_cap_cr >= 20000:
        return "Large Cap 🐘"
    elif market_cap_cr >= 5000:
        return "Mid Cap 🐎"
    else:
        return "Small Cap 🐇"


def format_currency(value):
    """Format currency in Indian crores"""
    if value >= 10000000:
        return f"₹{value/10000000:.2f} Cr"
    elif value >= 100000:
        return f"₹{value/100000:.2f} L"
    else:
        return f"₹{value:.2f}"


def fetch_yfinance_data(yf_symbol):
    """Fetch comprehensive data from Yahoo Finance"""
    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info
        hist = ticker.history(period="1y")
        
        if not info or len(hist) == 0:
            return None
        
        # Calculate 52-week metrics
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        week_52_high = info.get('fiftyTwoWeekHigh', 0)
        week_52_low = info.get('fiftyTwoWeekLow', 0)
        
        # Calculate returns
        if len(hist) >= 252:  # 1 year
            year_ago_price = hist['Close'].iloc[0]
            year_return = ((current_price - year_ago_price) / year_ago_price * 100) if year_ago_price else 0
        else:
            year_return = 0
        
        # Size & Valuation metrics
        market_cap = info.get('marketCap', 0)
        market_cap_cr = market_cap / 10000000  # Convert to Crores
        cap_category = get_market_cap_category(market_cap_cr)
        
        data = {
            'size_valuation': {
                'Market Cap': format_currency(market_cap),
                'Category': cap_category,
                'Current Price': f"₹{current_price:.2f}",
                'P/E Ratio': f"{info.get('trailingPE', 0):.2f}",
                'P/B Ratio': f"{info.get('priceToBook', 0):.2f}",
                'Book Value': f"₹{info.get('bookValue', 0):.2f}",
                'Dividend Yield': f"{info.get('dividendYield', 0) * 100:.2f}%",
                'Beta': f"{info.get('beta', 0):.2f}",
            },
            'profitability': {
                'ROE': f"{info.get('returnOnEquity', 0) * 100:.2f}%",
                'ROA': f"{info.get('returnOnAssets', 0) * 100:.2f}%",
                'Profit Margin': f"{info.get('profitMargins', 0) * 100:.2f}%",
                'Operating Margin': f"{info.get('operatingMargins', 0) * 100:.2f}%",
                'Gross Margin': f"{info.get('grossMargins', 0) * 100:.2f}%",
                'EPS': f"₹{info.get('trailingEps', 0):.2f}",
                'EPS Growth': f"{info.get('earningsGrowth', 0) * 100:.2f}%",
            },
            'business': {
                'Revenue': format_currency(info.get('totalRevenue', 0)),
                'Revenue Growth': f"{info.get('revenueGrowth', 0) * 100:.2f}%",
                'Net Income': format_currency(info.get('netIncomeToCommon', 0)),
                'Total Debt': format_currency(info.get('totalDebt', 0)),
                'Total Cash': format_currency(info.get('totalCash', 0)),
                'Free Cash Flow': format_currency(info.get('freeCashflow', 0)),
                'Operating Cash Flow': format_currency(info.get('operatingCashflow', 0)),
            },
            'price_market': {
                'Current Price': f"₹{current_price:.2f}",
                '52 Week High': f"₹{week_52_high:.2f}",
                '52 Week Low': f"₹{week_52_low:.2f}",
                'Volume': f"{info.get('volume', 0):,}",
                'Avg Volume': f"{info.get('averageVolume', 0):,}",
                '1 Year Return': f"{year_return:.2f}%",
                'Day Range': f"₹{info.get('dayLow', 0):.2f} - ₹{info.get('dayHigh', 0):.2f}",
            },
            'raw_data': {
                'market_cap_raw': market_cap,
                'market_cap_cr': market_cap_cr,
                'cap_category': cap_category,
                'current_price': current_price,
                'pe_ratio': info.get('trailingPE', 0),
                'roe': info.get('returnOnEquity', 0) * 100,
                'revenue': info.get('totalRevenue', 0),
            }
        }
        
        return data
        
    except Exception as e:
        st.error(f"Error fetching Yahoo Finance data: {str(e)}")
        return None


def get_market_cap_peers(target_cap_cr, current_symbol):
    """
    Finds peers based on similar Market Cap (Size) instead of Sector.
    Search range: +/- 30% of current company's market cap
    """
    peers = []
    
    # Define the search range (±30% of current company's size)
    lower_bound = target_cap_cr * 0.7
    upper_bound = target_cap_cr * 1.3
    
    # Search Local Database
    for name, info in STOCK_DATABASE.items():
        if info['symbol'] == current_symbol:
            continue
            
        try:
            ticker = yf.Ticker(info['yf_symbol'])
            ticker_info = ticker.info
            mcap = ticker_info.get('marketCap', 0) / 10000000  # In Cr
            
            if lower_bound <= mcap <= upper_bound:
                peers.append({
                    'Name': name,
                    'Category': get_market_cap_category(mcap),
                    'Sector': info['sector'],
                    'CMP Rs.': ticker_info.get('currentPrice', 0),
                    'Mar Cap Rs.Cr.': mcap,
                    'P/E': ticker_info.get('trailingPE', 0),
                    'ROE %': ticker_info.get('returnOnEquity', 0) * 100 if ticker_info.get('returnOnEquity') else 0
                })
                
                if len(peers) >= 10:  # Limit to top 10 peers
                    break
        except:
            continue
            
    return peers


@st.cache_data
def get_nifty500_universe():
    """Downloads Nifty 500 symbols to create a search universe."""
    try:
        url = "https://raw.githubusercontent.com/pratiknabriya/NSE-Indices-Data/master/NIFTY%20500.csv"
        df = pd.read_csv(url)
        return df['Symbol'].tolist()
    except:
        # Fallback list
        return ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", 
               "SBIN", "BHARTIARTL", "BAJAJ-AUTO", "LICI", "LT", "HCLTECH", "MARUTI"]


def get_peers_from_online_universe(target_cap_cr, current_symbol):
    """
    Searches Nifty 500 for companies with similar market cap.
    """
    universe = get_nifty500_universe()
    peers = []
    
    lower_bound = target_cap_cr * 0.7
    upper_bound = target_cap_cr * 1.3
    
    status_text = st.empty()
    status_text.text(f"🔍 Searching Nifty 500 for similar-sized companies...")
    progress_bar = st.progress(0)
    
    import random
    random.shuffle(universe)
    
    count = 0
    for i, sym in enumerate(universe[:50]):
        if count >= 10:
            break
        if sym == current_symbol.replace('.NS', ''):
            continue
        
        try:
            progress_bar.progress((i + 1) / 50)
            ticker = yf.Ticker(f"{sym}.NS")
            info = ticker.info
            mcap = info.get('marketCap', 0) / 10000000
            
            if lower_bound <= mcap <= upper_bound:
                peers.append({
                    'Name': info.get('shortName', sym),
                    'Category': get_market_cap_category(mcap),
                    'Sector': info.get('sector', 'N/A'),
                    'CMP Rs.': info.get('currentPrice', 0),
                    'Mar Cap Rs.Cr.': mcap,
                    'P/E': info.get('trailingPE', 0),
                    'ROE %': info.get('returnOnEquity', 0) * 100 if info.get('returnOnEquity') else 0
                })
                count += 1
        except:
            continue
            
    status_text.empty()
    progress_bar.empty()
    return peers


def display_metrics_section(title, metrics, icon="📊"):
    """Display metrics in cards"""
    st.markdown(f'<div class="section-title">{icon} {title}</div>', 
                unsafe_allow_html=True)
    
    items = list(metrics.items())
    cols = st.columns(2)
    
    for i, (key, value) in enumerate(items):
        with cols[i % 2]:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">{key}</div>
                <div class="metric-value">{value}</div>
            </div>
            """, unsafe_allow_html=True)


# Main UI
st.title("📊 Stock Analysis & Market Cap Comparison")
st.markdown("### Comprehensive analysis using Yahoo Finance")
st.markdown("---")

# Company selection
selected = st.selectbox(
    "🔍 Select Company:",
    options=[""] + sorted(STOCK_DATABASE.keys()),
    index=0
)

if selected:
    stock_info = STOCK_DATABASE[selected]
    symbol = stock_info['symbol']
    yf_symbol = stock_info['yf_symbol']
    sector = stock_info['sector']
    
    st.info(f"📍 Selected: **{selected}** | Sector: {sector}")
    
    analyze_btn = st.button("📊 Analyze", type="primary", use_container_width=True)
    
    if analyze_btn:
        with st.spinner("🔍 Fetching comprehensive data..."):
            yf_data = fetch_yfinance_data(yf_symbol)
            
            if yf_data:
                # Get category
                category = yf_data['size_valuation']['Category']
                
                # Company Header with Cap Category
                st.markdown(f"""
                <div class="company-header">
                    <h1>🏢 {selected}</h1>
                    <div style="display: flex; gap: 15px; margin-top: 15px;">
                        <span class="cap-badge">🏷️ {category}</span>
                        <span class="cap-badge">🏭 {sector}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Display metrics
                col1, col2 = st.columns(2)
                
                with col1:
                    display_metrics_section("💰 Size & Valuation", 
                                          yf_data['size_valuation'], "💰")
                    display_metrics_section("📈 Business Performance", 
                                          yf_data['business'], "📈")
                
                with col2:
                    display_metrics_section("📊 Profitability & Returns", 
                                          yf_data['profitability'], "📊")
                    display_metrics_section("💹 Price & Market Data", 
                                          yf_data['price_market'], "💹")
                
                # Market Cap Peer Comparison
                st.markdown("---")
                st.markdown("## ⚖️ Market Cap Based Competition")
                st.markdown(f"**Showing companies with similar valuation to {selected}** (±30% range)")
                
                current_mcap = yf_data['raw_data']['market_cap_cr']
                
                # Try local database first
                cap_peers = get_market_cap_peers(current_mcap, symbol)
                
                if not cap_peers:
                    st.warning("⚠️ No similar-sized companies in local database. Searching online...")
                    cap_peers = get_peers_from_online_universe(current_mcap, symbol)
                
                if cap_peers:
                    peers_df = pd.DataFrame(cap_peers)
                    
                    # Sort by market cap
                    peers_df = peers_df.sort_values('Mar Cap Rs.Cr.', ascending=False)
                    
                    # Format for display
                    peers_df['Mar Cap Rs.Cr.'] = peers_df['Mar Cap Rs.Cr.'].apply(
                        lambda x: f"₹{x:,.0f} Cr" if pd.notnull(x) else "N/A"
                    )
                    peers_df['CMP Rs.'] = peers_df['CMP Rs.'].apply(
                        lambda x: f"₹{x:.2f}" if pd.notnull(x) and x > 0 else "N/A"
                    )
                    peers_df['P/E'] = peers_df['P/E'].apply(
                        lambda x: f"{x:.2f}" if pd.notnull(x) and x > 0 else "N/A"
                    )
                    peers_df['ROE %'] = peers_df['ROE %'].apply(
                        lambda x: f"{x:.2f}%" if pd.notnull(x) else "N/A"
                    )
                    
                    st.success(f"✅ Found {len(peers_df)} companies with similar market cap")
                    st.dataframe(peers_df, use_container_width=True, height=400)
                    
                    # Download button
                    csv = peers_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "📥 Download Peer Data",
                        csv,
                        f"{selected}_market_cap_peers.csv",
                        "text/csv"
                    )
                else:
                    st.warning("❌ Could not find companies with similar market cap.")
                    
else:
    st.info("👆 Select a company to begin comprehensive analysis")
    
    # Show stocks by category
    st.markdown("### 📋 Available Stocks")
    st.markdown("*Note: Stocks will be compared by Market Cap, not Sector*")

st.markdown("---")
st.markdown("""
<div style='text-align: center; color: gray; padding: 20px;'>
    <p>📊 Data Source: Yahoo Finance</p>
    <p>⚠️ For educational purposes only | Not investment advice</p>
    <p>💡 Companies are compared based on Market Cap (Large/Mid/Small Cap)</p>
</div>
""", unsafe_allow_html=True)