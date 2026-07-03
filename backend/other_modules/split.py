import yfinance as yf
import pandas as pd
import requests
import io

# ---------------------------------------------------------
# STEP 1: Dynamically fetch ALL stock symbols from the NSE
# ---------------------------------------------------------
print("Fetching the master list of all NSE stocks directly from NSE India...")

# The official NSE URL for all listed equities
nse_url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

# We must use a User-Agent header, or the NSE website will block the request
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

response = requests.get(nse_url, headers=headers)

if response.status_code == 200:
    # Read the downloaded CSV into Pandas
    csv_data = io.StringIO(response.text)
    nse_df = pd.read_csv(csv_data)
    
    # Extract the 'SYMBOL' column and append '.NS' for Yahoo Finance
    tickers = nse_df['SYMBOL'].astype(str) + ".NS"
    tickers = tickers.tolist()
    print(f"Success! Found {len(tickers)} stocks listed on the NSE.\n")
else:
    print("Failed to connect to NSE. The NSE website might be blocking the request.")
    exit()

# ---> IMPORTANT TESTING LINE <---
# Remove the '#' on the line below to test the script on just 30 stocks first!
# tickers = tickers[:30] 

# ---------------------------------------------------------
# STEP 2: Fetch split data for all fetched tickers
# ---------------------------------------------------------
split_records = []

print(f"Checking {len(tickers)} stocks for splits. This will take a while. Go grab a coffee ☕...")

for i, ticker in enumerate(tickers):
    # Print a progress update every 50 stocks so you know it hasn't frozen
    if i % 50 == 0 and i > 0:
        print(f"Processed {i} out of {len(tickers)} stocks...")
        
    try:
        stock = yf.Ticker(ticker)
        splits = stock.splits
        
        if not splits.empty:
            for date, ratio in splits.items():
                # Format the ratio nicely (e.g., 5:1 or 1:2)
                if ratio >= 1:
                    split_ratio_str = f"{int(ratio)}:1"
                else:
                    split_ratio_str = f"1:{int(1/ratio)}"
                    
                split_records.append({
                    "Stock Symbol": ticker.replace('.NS', ''),
                    "Split Date": date.strftime('%Y-%m-%d'),
                    "Year": date.year, # We need this to create the Excel tabs
                    "Split/Bonus Ratio": split_ratio_str,
                    "Raw Mathematical Ratio": ratio
                })
    except Exception:
        # We silently pass here so the console isn't flooded with errors 
        # for newly listed stocks that lack historical data.
        pass

# ---------------------------------------------------------
# STEP 3: Generate the Multi-Tab Excel File
# ---------------------------------------------------------
if split_records:
    print("\nAll data fetched! Generating your multi-tab Excel file...")
    
    # Convert our data into a DataFrame
    df = pd.DataFrame(split_records)
    
    # Sort everything by Date (newest first)
    df = df.sort_values(by="Split Date", ascending=False)
    
    excel_filename = "NSE_Historical_Splits_By_Year.xlsx"
    
    # Use ExcelWriter to handle multiple sheets
    with pd.ExcelWriter(excel_filename, engine='openpyxl') as writer:
        
        # Find all the unique years where splits happened
        unique_years = sorted(df['Year'].unique(), reverse=True)
        
        for year in unique_years:
            # Filter the dataframe for just that specific year
            df_year = df[df['Year'] == year].copy()
            
            # Drop the 'Year' column since it's redundant (it's the name of the tab)
            df_year = df_year.drop(columns=['Year'])
            
            # Write this year's data to its own tab
            sheet_name = f"Year_{year}"
            df_year.to_excel(writer, sheet_name=sheet_name, index=False)
            
    print(f"\n✅ COMPLETE! Found {len(df)} total split/bonus events.")
    print(f"Your file has been saved as: {excel_filename}")
else:
    print("\nNo split data was found.")