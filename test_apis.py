# import requests

# BASE_ANALYZER = "http://139.59.74.2:8080"
# BASE_PULSE    = "http://159.89.225.5:8010"

# # Test analyzer
# resp = requests.get(f"{BASE_ANALYZER}/api/analyze/RELIANCE")
# print("Analyzer status:", resp.status_code)
# if resp.ok:
#     print(resp.json().keys())  # should show 'success' and 'data'

# # Test news
# resp2 = requests.get(f"{BASE_PULSE}/api/news/fetch?q=RELIANCE&time=24h")
# print("News status:", resp2.status_code)
# if resp2.ok:
#     print(resp2.json().keys())
    
    
    
# # output=
# # (venv) PS E:\API_connect\trading\v7> python test_apis.py                          
# # Analyzer status: 200                     
# # dict_keys(['data', 'success'])        
# # News status: 200
# # dict_keys(['articles', 'stock', 'success', 'time_filter', 'total'])


import requests
import json

# --- YOUR PUBLIC ENDPOINTS ---
BASE_ANALYZER = "http://139.59.74.2:8080"
BASE_PULSE   = "http://159.89.225.5:8010"

def inspect_json(data, name="Root", depth=0, max_depth=3):
    """Recursively prints the schema keys and types of a JSON object."""
    indent = "  " * depth
    if data is None:
        print(f"{indent}{name}: None")
        return
    elif isinstance(data, dict):
        print(f"{indent}{name}: {{")
        for k, v in data.items():
            if isinstance(v, (dict, list)) and depth < max_depth:
                # Recursive call for nested structures
                inspect_json(v, f"'{k}'", depth+1, max_depth)
            else:
                # Print primitive type
                print(f"{indent}  '{k}': {type(v).__name__}")
        print(f"{indent}}}")
    elif isinstance(data, list):
        print(f"{indent}{name}: [List of {len(data)} items]")
        if len(data) > 0:
            # Show structure of the first item (assuming homogeneous list)
            inspect_json(data[0], f"{name}[0]", depth+1, max_depth)
    else:
        print(f"{indent}{name}: {type(data).__name__}")

# ==========================================
# 1. INSPECT ANALYZER RESPONSE
# ==========================================
print("=== INSPECTING BASE_ANALYZER (/api/analyze/RELIANCE) ===")
try:
    resp1 = requests.get(f"{BASE_ANALYZER}/api/analyze/RELIANCE", timeout=10)
    if resp1.status_code == 200:
        data1 = resp1.json()
        print(f"Status: {resp1.status_code}")
        # We want to see the structure of 'data' specifically
        print("\nFULL RESPONSE KEYS:")
        inspect_json(data1, "Analyzer Root", max_depth=2) # Limit depth
        
        # If root has a 'data' field, inspect it deeper
        if isinstance(data1, dict) and "data" in data1:
            print("\n--- DEEP DIVE INTO 'data' FIELD ---")
            inspect_json(data1["data"], "Analyzer.data", max_depth=3)
        else:
            print("\nWARNING: No 'data' key found in root.")
    else:
        print(f"Error: Status {resp1.status_code}")
except Exception as e:
    print(f"Error: {e}")

# ==========================================
# 2. INSPECT NEWS RESPONSE (Specifically articles[0])
# ==========================================
print("\n\n=== INSPECTING BASE_PULSE (/api/news/fetch?q=RELIANCE&time=24h) ===")
try:
    resp2 = requests.get(f"{BASE_PULSE}/api/news/fetch?q=RELIANCE&time=24h", timeout=10)
    if resp2.status_code == 200:
        data2 = resp2.json()
        print(f"Status: {resp2.status_code}")
        
        print("\nFULL RESPONSE KEYS:")
        inspect_json(data2, "News Root", max_depth=2)
        
        # Look for articles list
        if isinstance(data2, dict) and "articles" in data2 and isinstance(data2["articles"], list):
            articles_list = data2["articles"]
            if len(articles_list) > 0:
                print(f"\n--- DEEP DIVE INTO articles[0] ---")
                inspect_json(articles_list[0], "articles[0]", max_depth=3)
            else:
                print("\nInfo: 'articles' list is empty.")
        else:
            print("\nWARNING: No 'articles' list found or it is empty.")
    else:
        print(f"Error: Status {resp2.status_code}")
except Exception as e:
    print(f"Error: {e}")
    
    
    
    
    
# output:
# python test_apis.py                                                                                                         
# === INSPECTING BASE_ANALYZER (/api/analyze/RELIANCE) ===
# Status: 200

# FULL RESPONSE KEYS:
# Analyzer Root: {
#   'data': {
#     'analysis': {
#       'current_price': float
#       'date': str
#       'fundamental_factors': list
#       'fundamental_score': int
#       'fundamentals': dict
#       'historical': dict
#       'historical_factors': list
#       'historical_score': int
#       'overall_score': float
#       'signal': str
#       'signal_strength': str
#       'symbol': str
#       'technical': dict
#       'technical_factors': list
#       'technical_score': int
#       'trading_plan': dict
#     }
#     'stock_info': {
#       'category': str
#       'market_cap': str
#       'name': str
#       'sector': str
#       'symbol': str
#     }
#     'symbol': str
#   }
#   'success': bool
# }

# --- DEEP DIVE INTO 'data' FIELD ---
# Analyzer.data: {
#   'analysis': {
#     'current_price': float
#     'date': str
#     'fundamental_factors': [List of 7 items]
#       'fundamental_factors'[0]: [List of 4 items]
#         'fundamental_factors'[0][0]: str
#     'fundamental_score': int
#     'fundamentals': {
#       'avg_volume': int
#       'avg_volume_10d': int
#       'book_value': float
#       'company_name': str
#       'current_price': float
#       'current_ratio': float
#       'day_high': float
#       'day_low': float
#       'debt_to_equity': float
#       'dividend_rate': float
#       'dividend_yield': float
#       'earnings_growth': float
#       'eps': float
#       'fifty_day_avg': float
#       'fifty_two_week_high': float
#       'fifty_two_week_low': float
#       'industry': str
#       'market_cap': int
#       'market_cap_formatted': str
#       'operating_margin': float
#       'pb_ratio': float
#       'pe_ratio': float
#       'peg_ratio': float
#       'previous_close': float
#       'profit_margin': float
#       'ps_ratio': float
#       'quick_ratio': float
#       'revenue_growth': float
#       'roa': float
#       'roe': float
#       'sector': str
#       'two_hundred_day_avg': float
#     }
#     'historical': {
#       'current_price': float
#       'pct_from_52w_high': float
#       'pct_from_52w_low': float
#       'price_position': float
#       'resistances': [List of 3 items]
#         'resistances'[0]: float
#       'returns_1m': float
#       'returns_1y': float
#       'returns_3m': float
#       'returns_6m': float
#       'sma_20': float
#       'sma_200': float
#       'sma_50': float
#       'supports': [List of 3 items]
#         'supports'[0]: float
#       'trend': str
#       'volatility': float
#       'week_52_high': float
#       'week_52_low': float
#     }
#     'historical_factors': [List of 5 items]
#       'historical_factors'[0]: [List of 4 items]
#         'historical_factors'[0][0]: str
#     'historical_score': int
#     'overall_score': float
#     'signal': str
#     'signal_strength': str
#     'symbol': str
#     'technical': {
#       'adx': float
#       'atr': float
#       'bb_lower': float
#       'bb_middle': float
#       'bb_upper': float
#       'current_price': float
#       'macd_hist': float
#       'macd_line': float
#       'macd_signal': float
#       'minus_di': float
#       'plus_di': float
#       'rsi': float
#       'sma_20': float
#       'sma_200': float
#       'sma_50': float
#       'stoch_d': float
#       'stoch_k': float
#       'volume_ratio': float
#     }
#     'technical_factors': [List of 4 items]
#       'technical_factors'[0]: [List of 4 items]
#         'technical_factors'[0][0]: str
#     'technical_score': int
#     'trading_plan': {
#       'buy_stop_loss': float
#       'buy_target_1': float
#       'buy_target_2': float
#       'buy_triggers': [List of 3 items]
#         'buy_triggers'[0]: str
#       'buy_zones': [List of 3 items]
#         'buy_zones'[0]: {
#           'price_high': float
#           'price_low': float
#           'reason': str
#           'zone': str
#         }
#       'exit_conditions': [List of 3 items]
#         'exit_conditions'[0]: str
#       'reward_amount': float
#       'risk_amount': float
#       'risk_reward': float
#       'sell_risk_reward': float
#       'sell_stop_loss': float
#       'sell_target_1': float
#       'sell_target_2': float
#       'sell_triggers': [List of 3 items]
#         'sell_triggers'[0]: str
#       'sell_zones': [List of 3 items]
#         'sell_zones'[0]: {
#           'price_high': float
#           'price_low': float
#           'reason': str
#           'zone': str
#         }
#     }
#   }
#   'stock_info': {
#     'category': str
#     'market_cap': str
#     'name': str
#     'sector': str
#     'symbol': str
#   }
#   'symbol': str
# }


# === INSPECTING BASE_PULSE (/api/news/fetch?q=RELIANCE&time=24h) ===
# Status: 200

# FULL RESPONSE KEYS:
# News Root: {
#   'articles': [List of 34 items]
#     'articles'[0]: {
#       'published_at': str
#       'sentiment': str
#       'snippet': str
#       'source': str
#       'sourceColor': str
#       'sourceIcon': str
#       'symbol': str
#       'time_filter': str
#       'title': str
#       'url': str
#       'verified': bool
#     }
#   'stock': {
#     'exchange': str
#     'name': str
#     'symbol': str
#   }
#   'success': bool
#   'time_filter': str
#   'total': int
# }

# --- DEEP DIVE INTO articles[0] ---
# articles[0]: {
#   'published_at': str
#   'sentiment': str
#   'snippet': str
#   'source': str
#   'sourceColor': str
#   'sourceIcon': str
#   'symbol': str
#   'time_filter': str
#   'title': str
#   'url': str
#   'verified': bool
# }
