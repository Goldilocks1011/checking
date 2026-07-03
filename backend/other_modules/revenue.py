#!/usr/bin/env python3
"""
NSE Stock Connection Analyzer - Streamlit Version with Database Integration
Analyzes sector connections, export/domestic orientation, and news sensitivity for Indian stocks
COMPLETE functionality preserved from original terminal version
"""

import streamlit as st
import yfinance as yf
import json
import re
import plotly.graph_objects as go
import pandas as pd
import sys
import os
from typing import Dict, List, Tuple, Set

# ==================== PATH SETUP ====================
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)
PROJECT_ROOT = os.path.dirname(current_dir)

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# Database imports
try:
    from db_helper import get_all_stocks, search_stocks as db_search_stocks
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# ==================== PAGE CONFIG ====================
st.set_page_config(
    page_title="NSE Stock Revenue Analyzer",
    page_icon="📊",
    layout="wide"
)

# ==================== STOCK CONNECTION ANALYZER CLASS (COMPLETE FROM ORIGINAL) ====================
class StockConnectionAnalyzer:
    def __init__(self):
        # Complete known_companies dictionary from original
        self.known_companies = {
            # IT Services & Software (85% export)
            'TCS.NS': {'sector': 'IT Services', 'name': 'Tata Consultancy Services'},
            'INFY.NS': {'sector': 'IT Services', 'name': 'Infosys'},
            'WIPRO.NS': {'sector': 'IT Services', 'name': 'Wipro'},
            'HCLTECH.NS': {'sector': 'IT Services', 'name': 'HCL Technologies'},
            'TECHM.NS': {'sector': 'IT Services', 'name': 'Tech Mahindra'},
            'LTI.NS': {'sector': 'IT Services', 'name': 'LTIMindtree'},
            'LTIM.NS': {'sector': 'IT Services', 'name': 'LTIMindtree'},
            'COFORGE.NS': {'sector': 'IT Services', 'name': 'Coforge'},
            'PERSISTENT.NS': {'sector': 'IT Services', 'name': 'Persistent Systems'},
            'MPHASIS.NS': {'sector': 'IT Services', 'name': 'Mphasis'},
            'HAPPSTMNDS.NS': {'sector': 'IT Services', 'name': 'Happiest Minds'},
            
            # NBFC (0-5% export)
            'BAJAJFINSV.NS': {'sector': 'NBFC', 'name': 'Bajaj Finserv'},
            'BAJFINANCE.NS': {'sector': 'NBFC', 'name': 'Bajaj Finance'},
            'CHOLAFIN.NS': {'sector': 'NBFC', 'name': 'Cholamandalam Investment'},
            'MUTHOOTFIN.NS': {'sector': 'NBFC', 'name': 'Muthoot Finance'},
            'LICHSGFIN.NS': {'sector': 'NBFC', 'name': 'LIC Housing Finance'},
            'SHRIRAMFIN.NS': {'sector': 'NBFC', 'name': 'Shriram Finance'},
            'PNBHOUSING.NS': {'sector': 'NBFC', 'name': 'PNB Housing Finance'},
            'IIFL.NS': {'sector': 'NBFC', 'name': 'IIFL Finance'},
            
            # Chemicals (60-70% export)
            'AARTIIND.NS': {'sector': 'Chemicals', 'name': 'Aarti Industries'},
            'ATUL.NS': {'sector': 'Chemicals', 'name': 'Atul Ltd'},
            'DEEPAKNTR.NS': {'sector': 'Chemicals', 'name': 'Deepak Nitrite'},
            'GNFC.NS': {'sector': 'Chemicals', 'name': 'Gujarat Narmada'},
            'NAVINFLUOR.NS': {'sector': 'Chemicals', 'name': 'Navin Fluorine'},
            'PIIND.NS': {'sector': 'Chemicals', 'name': 'PI Industries'},
            'SUMICHEM.NS': {'sector': 'Chemicals', 'name': 'Sumitomo Chemical'},
            'TATACHEM.NS': {'sector': 'Chemicals', 'name': 'Tata Chemicals'},
            'ALKYLAMINE.NS': {'sector': 'Chemicals', 'name': 'Alkyl Amines'},
            'VINATIORG.NS': {'sector': 'Chemicals', 'name': 'Vinati Organics'},
            'CLEAN.NS': {'sector': 'Chemicals', 'name': 'Clean Science'},
            'GALAXYSURF.NS': {'sector': 'Chemicals', 'name': 'Galaxy Surfactants'},
            'FLUOROCHEM.NS': {'sector': 'Chemicals', 'name': 'Gujarat Fluorochemicals'},
            'SRF.NS': {'sector': 'Chemicals', 'name': 'SRF Ltd'},
            
            # Pharmaceuticals (55-65% export)
            'SUNPHARMA.NS': {'sector': 'Pharmaceuticals', 'name': 'Sun Pharma'},
            'DRREDDY.NS': {'sector': 'Pharmaceuticals', 'name': 'Dr Reddy'},
            'CIPLA.NS': {'sector': 'Pharmaceuticals', 'name': 'Cipla'},
            'DIVISLAB.NS': {'sector': 'Pharmaceuticals', 'name': 'Divis Lab'},
            'BIOCON.NS': {'sector': 'Pharmaceuticals', 'name': 'Biocon'},
            'AUROPHARMA.NS': {'sector': 'Pharmaceuticals', 'name': 'Aurobindo Pharma'},
            'LUPIN.NS': {'sector': 'Pharmaceuticals', 'name': 'Lupin'},
            'TORNTPHARM.NS': {'sector': 'Pharmaceuticals', 'name': 'Torrent Pharma'},
            'ALKEM.NS': {'sector': 'Pharmaceuticals', 'name': 'Alkem Laboratories'},
            'GRANULES.NS': {'sector': 'Pharmaceuticals', 'name': 'Granules India'},
            'LALPATHLAB.NS': {'sector': 'Pharmaceuticals', 'name': 'Dr Lal PathLabs'},
            'ABBOTINDIA.NS': {'sector': 'Pharmaceuticals', 'name': 'Abbott India'},
            'GLENMARK.NS': {'sector': 'Pharmaceuticals', 'name': 'Glenmark Pharma'},
            'SYNGENE.NS': {'sector': 'Pharmaceuticals', 'name': 'Syngene International'},
            'LAURUSLABS.NS': {'sector': 'Pharmaceuticals', 'name': 'Laurus Labs'},
            
            # Automobiles (25-35% export)
            'TATAMOTORS.NS': {'sector': 'Automobiles', 'name': 'Tata Motors'},
            'MARUTI.NS': {'sector': 'Automobiles', 'name': 'Maruti Suzuki'},
            'M&M.NS': {'sector': 'Automobiles', 'name': 'Mahindra & Mahindra'},
            'BAJAJ-AUTO.NS': {'sector': 'Automobiles', 'name': 'Bajaj Auto'},
            'HEROMOTOCO.NS': {'sector': 'Automobiles', 'name': 'Hero MotoCorp'},
            'EICHERMOT.NS': {'sector': 'Automobiles', 'name': 'Eicher Motors'},
            'ASHOKLEY.NS': {'sector': 'Automobiles', 'name': 'Ashok Leyland'},
            'TVSMOTOR.NS': {'sector': 'Automobiles', 'name': 'TVS Motor'},
            'ESCORTS.NS': {'sector': 'Automobiles', 'name': 'Escorts Kubota'},
            'BALKRISIND.NS': {'sector': 'Automobiles', 'name': 'Balkrishna Industries'},
            
            # Auto Components (40-50% export)
            'MOTHERSON.NS': {'sector': 'Auto Components', 'name': 'Samvardhana Motherson'},
            'BOSCHLTD.NS': {'sector': 'Auto Components', 'name': 'Bosch India'},
            'BHARAT-FORGE.NS': {'sector': 'Auto Components', 'name': 'Bharat Forge'},
            'EXIDEIND.NS': {'sector': 'Auto Components', 'name': 'Exide Industries'},
            'MRF.NS': {'sector': 'Auto Components', 'name': 'MRF Ltd'},
            'APOLLOTYRE.NS': {'sector': 'Auto Components', 'name': 'Apollo Tyres'},
            'AMARAJABAT.NS': {'sector': 'Auto Components', 'name': 'Amara Raja'},
            'ENDURANCE.NS': {'sector': 'Auto Components', 'name': 'Endurance Technologies'},
            'SONA.NS': {'sector': 'Auto Components', 'name': 'Sona BLW Precision'},
            'ROLEXRINGS.NS': {'sector': 'Auto Components', 'name': 'Rolex Rings'},
            
            # Banking (0-2% export)
            'HDFCBANK.NS': {'sector': 'Banking', 'name': 'HDFC Bank'},
            'ICICIBANK.NS': {'sector': 'Banking', 'name': 'ICICI Bank'},
            'SBIN.NS': {'sector': 'Banking', 'name': 'State Bank of India'},
            'KOTAKBANK.NS': {'sector': 'Banking', 'name': 'Kotak Mahindra Bank'},
            'AXISBANK.NS': {'sector': 'Banking', 'name': 'Axis Bank'},
            'INDUSINDBK.NS': {'sector': 'Banking', 'name': 'IndusInd Bank'},
            'BANDHANBNK.NS': {'sector': 'Banking', 'name': 'Bandhan Bank'},
            'FEDERALBNK.NS': {'sector': 'Banking', 'name': 'Federal Bank'},
            'IDFCFIRSTB.NS': {'sector': 'Banking', 'name': 'IDFC First Bank'},
            'IOB.NS': {'sector': 'Banking', 'name': 'Indian Overseas Bank'},
            'EQUITASBNK.NS': {'sector': 'Banking', 'name': 'Equitas Small Finance Bank'},
            
            # FMCG (15-25% export)
            'HINDUNILVR.NS': {'sector': 'FMCG', 'name': 'Hindustan Unilever'},
            'ITC.NS': {'sector': 'FMCG', 'name': 'ITC Ltd'},
            'NESTLEIND.NS': {'sector': 'FMCG', 'name': 'Nestle India'},
            'BRITANNIA.NS': {'sector': 'FMCG', 'name': 'Britannia Industries'},
            'DABUR.NS': {'sector': 'FMCG', 'name': 'Dabur India'},
            'MARICO.NS': {'sector': 'FMCG', 'name': 'Marico'},
            'GODREJCP.NS': {'sector': 'FMCG', 'name': 'Godrej Consumer'},
            'COLPAL.NS': {'sector': 'FMCG', 'name': 'Colgate Palmolive'},
            'EMAMILTD.NS': {'sector': 'FMCG', 'name': 'Emami'},
            'TATACONSUM.NS': {'sector': 'FMCG', 'name': 'Tata Consumer'},
            'VBL.NS': {'sector': 'FMCG', 'name': 'Varun Beverages'},
            'BIKAJI.NS': {'sector': 'FMCG', 'name': 'Bikaji Foods'},
            
            # Power & Energy (5-10% export)
            'NTPC.NS': {'sector': 'Power', 'name': 'NTPC Ltd'},
            'POWERGRID.NS': {'sector': 'Power', 'name': 'Power Grid Corporation'},
            'NHPC.NS': {'sector': 'Power', 'name': 'NHPC Ltd'},
            'SJVN.NS': {'sector': 'Power', 'name': 'SJVN Ltd'},
            'TATAPOWER.NS': {'sector': 'Power', 'name': 'Tata Power'},
            'ADANIPOWER.NS': {'sector': 'Power', 'name': 'Adani Power'},
            'ADANIGREEN.NS': {'sector': 'Power', 'name': 'Adani Green Energy'},
            
            # Oil & Gas (20-30% export)
            'RELIANCE.NS': {'sector': 'Oil & Gas', 'name': 'Reliance Industries'},
            'ONGC.NS': {'sector': 'Oil & Gas', 'name': 'Oil and Natural Gas Corporation'},
            'IOC.NS': {'sector': 'Oil & Gas', 'name': 'Indian Oil Corporation'},
            'BPCL.NS': {'sector': 'Oil & Gas', 'name': 'Bharat Petroleum'},
            'GAIL.NS': {'sector': 'Oil & Gas', 'name': 'GAIL India'},
            'OIL.NS': {'sector': 'Oil & Gas', 'name': 'Oil India'},
            'MGL.NS': {'sector': 'Oil & Gas', 'name': 'Mahanagar Gas'},
            'IGL.NS': {'sector': 'Oil & Gas', 'name': 'Indraprastha Gas'},
            
            # Metals & Mining (30-40% export)
            'TATASTEEL.NS': {'sector': 'Metals & Mining', 'name': 'Tata Steel'},
            'HINDALCO.NS': {'sector': 'Metals & Mining', 'name': 'Hindalco Industries'},
            'JSWSTEEL.NS': {'sector': 'Metals & Mining', 'name': 'JSW Steel'},
            'VEDL.NS': {'sector': 'Metals & Mining', 'name': 'Vedanta'},
            'JINDALSTEL.NS': {'sector': 'Metals & Mining', 'name': 'Jindal Steel'},
            'SAIL.NS': {'sector': 'Metals & Mining', 'name': 'SAIL'},
            'NMDC.NS': {'sector': 'Metals & Mining', 'name': 'NMDC'},
            'COALINDIA.NS': {'sector': 'Metals & Mining', 'name': 'Coal India'},
            'HINDZINC.NS': {'sector': 'Metals & Mining', 'name': 'Hindustan Zinc'},
            'NATIONALUM.NS': {'sector': 'Metals & Mining', 'name': 'National Aluminium'},
            'NLCINDIA.NS': {'sector': 'Metals & Mining', 'name': 'NLC India'},
            
            # Insurance (0-5% export)
            'HDFCLIFE.NS': {'sector': 'Insurance', 'name': 'HDFC Life Insurance'},
            'SBILIFE.NS': {'sector': 'Insurance', 'name': 'SBI Life Insurance'},
            'ICICIPRULI.NS': {'sector': 'Insurance', 'name': 'ICICI Prudential Life'},
            'LICI.NS': {'sector': 'Insurance', 'name': 'Life Insurance Corporation'},
            'GICRE.NS': {'sector': 'Insurance', 'name': 'General Insurance Corporation'},
            'GODIGIT.NS': {'sector': 'Insurance', 'name': 'Go Digit General Insurance'},
            
            # Conglomerates
            'ADANIENT.NS': {'sector': 'Conglomerate', 'name': 'Adani Enterprises'},
            
            # Retail (5-10% export)
            'TRENT.NS': {'sector': 'Retail', 'name': 'Trent Ltd'},
            'ABFRL.NS': {'sector': 'Retail', 'name': 'Aditya Birla Fashion and Retail'},
            
            # Financial Services
            'BSE.NS': {'sector': 'Financial Services', 'name': 'BSE Ltd'},
            'CAMS.NS': {'sector': 'Financial Services', 'name': 'Computer Age Management Services'},
            'ABSLAMC.NS': {'sector': 'Financial Services', 'name': 'Aditya Birla Sun Life AMC'},
            'CDSL.NS': {'sector': 'Financial Services', 'name': 'Central Depository Services'},
            
            # Telecom Infrastructure (10-15% export)
            'INDUSTOWER.NS': {'sector': 'Telecom Infrastructure', 'name': 'Indus Towers'},
            
            # Tourism & Hospitality (30-40% export for hotels)
            'IRCTC.NS': {'sector': 'Travel & Tourism', 'name': 'Indian Railway Catering and Tourism'},
            'ITCHOTELS.NS': {'sector': 'Hotels', 'name': 'ITC Hotels'},
            'WONDERLA.NS': {'sector': 'Entertainment', 'name': 'Wonderla Holidays'},
            
            # Defense (15-25% export)
            'HAL.NS': {'sector': 'Defense & Aerospace', 'name': 'Hindustan Aeronautics'},
            'BEL.NS': {'sector': 'Defense & Aerospace', 'name': 'Bharat Electronics'},
            
            # Agrochemicals (70-80% export)
            'UPL.NS': {'sector': 'Agrochemicals', 'name': 'UPL Ltd'},
            'RALLIS.NS': {'sector': 'Agrochemicals', 'name': 'Rallis India'},
            
            # Housing Finance
            'BAJAJHFL.NS': {'sector': 'Housing Finance', 'name': 'Bajaj Housing Finance'},
            'HUDCO.NS': {'sector': 'Housing Finance', 'name': 'Housing and Urban Development Corporation'},
            
            # Asset Management
            'SBICARD.NS': {'sector': 'Financial Services', 'name': 'SBI Cards and Payment Services'},
        }
        
        # Export percentages by sector (FROM ORIGINAL)
        self.export_percentages = {
            'IT Services': 85,
            'Pharmaceuticals': 60,
            'Chemicals': 65,
            'Agrochemicals': 75,
            'Textiles': 55,
            'Auto Components': 45,
            'Gems & Jewellery': 90,
            'Engineering Goods': 40,
            'Leather Goods': 70,
            'Handicrafts': 80,
            'Marine Products': 95,
            'Automobiles': 30,
            'Defense & Aerospace': 20,
            'Metals & Mining': 35,
            'Petroleum Products': 25,
            'Organic Chemicals': 50,
            'FMCG': 20,
            'Cement': 10,
            'Banking': 1,
            'NBFC': 2,
            'Insurance': 3,
            'Telecom': 5,
            'Real Estate': 5,
            'Retail': 8,
            'Media & Entertainment': 15,
            'Hotels': 35,
            'Airlines': 25,
            'Power': 8,
            'Oil & Gas': 25,
            'Financial Services': 5,
            'Conglomerate': 25,
            'Telecom Infrastructure': 12,
            'Travel & Tourism': 20,
            'Entertainment': 10,
            'Housing Finance': 2,
        }
        
        # Sector connection relationships (FROM ORIGINAL)
        self.sector_connections = {
            'IT Services': {
                'supplies_to': ['Banking', 'Insurance', 'Retail', 'Manufacturing', 'Healthcare', 'Global Clients'],
                'depends_on': ['Telecom Infrastructure', 'Real Estate (for offices)', 'Education (talent)', 'Power'],
                'competes_with': ['Global IT firms (Accenture, IBM)', 'Domestic IT (Infosys, TCS, Wipro compete)']
            },
            'Pharmaceuticals': {
                'supplies_to': ['Hospitals', 'Pharmacies', 'Export markets (US, EU)', 'Government healthcare'],
                'depends_on': ['Chemicals (APIs)', 'Packaging', 'Logistics', 'R&D infrastructure'],
                'competes_with': ['Global pharma (Pfizer, Novartis)', 'Generic manufacturers', 'Chinese APIs']
            },
            'Chemicals': {
                'supplies_to': ['Pharmaceuticals', 'Agrochemicals', 'Paints', 'Textiles', 'Plastics', 'Detergents'],
                'depends_on': ['Petroleum refineries', 'Mining', 'Power', 'Logistics'],
                'competes_with': ['Chinese chemicals', 'Middle East petrochemicals', 'European specialty chemicals']
            },
            'Banking': {
                'supplies_to': ['All sectors (credit)', 'Retail customers', 'SMEs', 'Large corporates'],
                'depends_on': ['RBI policies', 'Government bonds', 'Deposit growth', 'IT Services'],
                'competes_with': ['Other banks', 'NBFCs', 'Fintech', 'Foreign banks']
            },
            'NBFC': {
                'supplies_to': ['Retail loans', 'Vehicle finance', 'Housing finance', 'SME lending', 'Gold loans'],
                'depends_on': ['Banks (for funding)', 'Credit rating agencies', 'RBI regulations'],
                'competes_with': ['Banks', 'Other NBFCs', 'Fintech lenders']
            },
            'Automobiles': {
                'supplies_to': ['Personal mobility', 'Commercial transport', 'Agricultural equipment', 'Export markets'],
                'depends_on': ['Auto Components', 'Steel', 'Electronics', 'Dealership network', 'Finance companies'],
                'competes_with': ['Other OEMs', 'Imports', 'EVs', 'Used car market']
            },
            'Auto Components': {
                'supplies_to': ['Auto OEMs', 'Aftermarket', 'Export to global OEMs'],
                'depends_on': ['Steel', 'Aluminum', 'Plastics', 'Electronics', 'R&D'],
                'competes_with': ['Imports', 'Chinese suppliers', 'Global tier-1 suppliers']
            },
            'Power': {
                'supplies_to': ['Households', 'Industries', 'Commercial establishments', 'Distribution companies'],
                'depends_on': ['Coal', 'Natural gas', 'Renewable equipment', 'Transmission infrastructure'],
                'competes_with': ['Renewable energy', 'Captive power plants', 'Rooftop solar']
            },
            'Oil & Gas': {
                'supplies_to': ['Transportation', 'Power generation', 'Petrochemicals', 'Households (LPG/PNG)'],
                'depends_on': ['Crude oil imports', 'Refineries', 'Pipelines', 'Storage infrastructure'],
                'competes_with': ['Renewable energy', 'EVs', 'International oil companies']
            },
            'Metals & Mining': {
                'supplies_to': ['Construction', 'Automobiles', 'Infrastructure', 'Manufacturing', 'Defense'],
                'depends_on': ['Mining equipment', 'Power', 'Logistics', 'Port infrastructure'],
                'competes_with': ['Imports', 'Scrap metal', 'Alternative materials']
            },
            'FMCG': {
                'supplies_to': ['Retail consumers', 'Modern trade', 'E-commerce', 'Kirana stores'],
                'depends_on': ['Packaging', 'Advertising', 'Distribution network', 'Agricultural commodities'],
                'competes_with': ['Local brands', 'Private labels', 'Unorganized sector']
            },
            'Insurance': {
                'supplies_to': ['Life insurance customers', 'Health insurance', 'Motor insurance', 'Corporate clients'],
                'depends_on': ['IRDAI regulations', 'Distribution channels', 'IT infrastructure', 'Investment markets'],
                'competes_with': ['Other insurers', 'Mutual funds (for ULIP)', 'Standalone health insurers']
            },
            'Retail': {
                'supplies_to': ['End consumers', 'Apparel', 'Electronics', 'Groceries', 'Lifestyle products'],
                'depends_on': ['Real estate', 'Supply chain', 'Brands', 'Consumer spending'],
                'competes_with': ['E-commerce', 'Other retailers', 'Unorganized retail', 'Direct-to-consumer brands']
            },
            'Agrochemicals': {
                'supplies_to': ['Farmers', 'Agricultural businesses', 'Export markets'],
                'depends_on': ['Chemicals', 'R&D', 'Distribution network', 'Monsoon patterns'],
                'competes_with': ['Imports', 'Organic farming movement', 'Generic agrochemicals']
            },
        }
        
        # NEWS IMPACT CATEGORIES (COMPLETE FROM ORIGINAL - THIS WAS MISSING!)
        self.news_impact_categories = {
            'MACROECONOMIC': {
                'description': 'Broad economic indicators affecting all stocks',
                'types': [
                    'Budget/Fiscal Policy (Tax changes, subsidies, infrastructure spending)',
                    'Monetary Policy (Interest rates, liquidity measures, RBI decisions)',
                    'GDP/Growth Data (Quarterly GDP, PMI, IIP, core sector)',
                    'Foreign Exchange (Rupee-Dollar, forex reserves, trade balance)',
                    'Inflation (CPI, WPI, food/fuel inflation)',
                ],
                'impact_level': 'UNIVERSAL - All stocks affected',
                'sensitivity_by_sector': {
                    'High': ['Banking', 'NBFC', 'Real Estate', 'Auto', 'FMCG'],
                    'Medium': ['Most other sectors'],
                    'Low': ['IT Services (insulated by exports)']
                }
            },
            
            'INTERNATIONAL_TRADE': {
                'description': 'Trade agreements, tariffs, and global trade dynamics',
                'types': [
                    'Trade Agreements (US-India, EU deals, RCEP, FTAs)',
                    'Tariff Changes (Import duties, export incentives, anti-dumping)',
                    'Global Sanctions (Russia, China, Middle East)',
                    'Trade Wars (US-China impact on Indian exports)',
                ],
                'impact_level': 'HIGH for export-oriented sectors',
                'most_affected_sectors': ['IT Services', 'Pharmaceuticals', 'Chemicals', 
                                         'Textiles', 'Auto Components', 'Agrochemicals', 
                                         'Gems & Jewellery'],
                'impact_details': {
                    '>60% export stocks': 'CRITICAL - Direct P&L impact',
                    '30-60% export stocks': 'HIGH - Significant impact',
                    '<30% export stocks': 'LOW - Indirect sentiment impact'
                }
            },
            
            'GEOPOLITICAL': {
                'description': 'Wars, conflicts, elections, and political tensions',
                'types': [
                    'Wars/Military Conflicts (Ukraine-Russia, Middle East)',
                    'Elections (Lok Sabha, State elections, US elections)',
                    'Border Tensions (India-Pakistan, India-China)',
                    'Regional Instability (Oil-producing regions)',
                ],
                'sector_impact': {
                    'Defense': '↑ Positive (increased orders)',
                    'Oil & Gas': '↑ Crude prices rise, ↓ OMCs margins',
                    'Airlines/Tourism': '↓ Negative (travel disruption)',
                    'Metals': '↑ Defense procurement',
                    'All sectors': 'Market volatility increases'
                }
            },
            
            'NATURAL_DISASTERS': {
                'description': 'Floods, droughts, earthquakes, extreme weather',
                'types': [
                    'Floods (Crop damage, infrastructure disruption)',
                    'Droughts (Agricultural impact, power demand)',
                    'Earthquakes (Insurance claims, reconstruction)',
                    'Cyclones/Storms (Coastal infrastructure, shipping)',
                ],
                'sector_impact': {
                    'Agrochemicals': '↓ Negative - crop damage reduces demand',
                    'FMCG': '↓ Rural volume impact',
                    'Insurance': '↓ Claims payouts',
                    'Cement': '↑ Reconstruction demand (delayed)',
                    'Power': '↑ AC demand in heat waves',
                }
            },
            
            'REGULATORY': {
                'description': 'Sector-specific regulations and compliance changes',
                'types': [
                    'SEBI Regulations (Listing rules, disclosure norms)',
                    'RBI Banking Rules (NPA norms, capital adequacy, digital banking)',
                    'Drug Pricing Control (Pharma margins)',
                    'Environmental Norms (Emission standards, pollution control)',
                    'Telecom Spectrum Pricing',
                    'E-commerce Regulations (FDI rules, marketplace policies)',
                ],
                'sector_specific': {
                    'Banking/NBFC': 'RBI guidelines on lending, NPA recognition',
                    'Pharmaceuticals': 'Drug price caps, quality standards',
                    'Auto': 'BS norms, safety regulations, EV mandates',
                    'Telecom': 'Spectrum allocation, tariff regulations',
                }
            },
            
            'TECHNOLOGY_AI': {
                'description': 'AI disruption, automation, and tech innovation',
                'types': [
                    'GenAI/AI Advancement (Job displacement, productivity gains)',
                    'Cybersecurity Threats (Data breaches, ransomware)',
                    'Tech Regulation (Data localization, privacy laws)',
                    'Cloud Adoption (Shift to SaaS, platform economy)',
                    'EV Technology (Battery breakthroughs, charging infra)',
                ],
                'sector_impact': {
                    'IT Services': '↑ AI services demand, ↓ traditional coding jobs',
                    'Banking': '↑ Digital adoption, ↓ branch footprint',
                    'Automobiles': '↓ ICE vehicles, ↑ EVs',
                    'Retail': '↑ E-commerce, ↓ Physical stores',
                }
            },
            
            'ENERGY_TRANSITION': {
                'description': 'Shift from fossil fuels to renewable energy',
                'types': [
                    'Renewable Energy Policy (Solar/wind subsidies, green hydrogen)',
                    'EV Adoption (EV subsidies, charging infrastructure)',
                    'Oil Price Movements (OPEC decisions, geopolitical events)',
                    'Carbon Taxes/Emission Trading',
                ],
                'sector_impact': {
                    'Coal India': '↓ Long-term decline',
                    'Power (Coal-based)': '↓ Transition pressure',
                    'Renewable Energy': '↑ Growth opportunity',
                    'Auto (ICE)': '↓ Declining demand',
                    'Oil & Gas': '↓ Peak oil concerns',
                }
            },
            
            'PANDEMIC_HEALTH': {
                'description': 'Disease outbreaks and health crises',
                'types': [
                    'COVID-like Pandemics',
                    'Disease Outbreaks (Regional epidemics)',
                    'Health Policy Changes (Universal health coverage, Ayushman Bharat)',
                ],
                'sector_impact': {
                    'Pharmaceuticals': '↑ Vaccine/drug demand',
                    'Airlines/Hotels': '↓ Travel restrictions',
                    'E-commerce': '↑ Online shopping surge',
                    'IT Services': '↑ Work-from-home tools',
                    'Insurance': '↑ Health insurance uptake',
                }
            },
            
            'COMMODITY_PRICES': {
                'description': 'Raw material price movements',
                'types': [
                    'Crude Oil (Transport costs, input for chemicals/plastics)',
                    'Steel/Aluminum (Auto, construction, manufacturing)',
                    'Cotton/Polyester (Textile industry)',
                    'Agricultural Commodities (Wheat, sugar, edible oils)',
                    'Rare Earths (Electronics, EV batteries)',
                ],
                'sector_impact': {
                    'OMCs': '↓ Margins when crude rises',
                    'Airlines': '↓ High fuel costs',
                    'Chemicals': '↓ Higher feedstock costs',
                    'Auto': '↓ Steel price increases',
                    'FMCG': '↓ Edible oil, packaging costs',
                }
            },
            
            'CORPORATE_ACTIONS': {
                'description': 'Company-specific events',
                'types': [
                    'Quarterly Earnings (Revenue, profit, guidance)',
                    'M&A Activity (Acquisitions, mergers, demergers)',
                    'Management Changes (CEO/CFO exits, promoter stake)',
                    'Capital Actions (Buybacks, dividends, QIPs)',
                    'Legal Issues (Regulatory fines, court cases, fraud)',
                ],
                'impact_level': 'STOCK-SPECIFIC',
                'note': 'Affects individual companies rather than sectors'
            },
            
            'CONSUMER_SENTIMENT': {
                'description': 'Demand-side factors affecting consumption',
                'types': [
                    'Festive Season (Diwali, Christmas sales peaks)',
                    'Rural Demand (Monsoon, MSP, farm loan waivers)',
                    'Urban Demand (Job market, salary hikes, real estate)',
                    'Luxury vs Value (Income inequality trends)',
                ],
                'sector_impact': {
                    'FMCG': '↑ Festive/rural demand',
                    'Auto': '↑ Festival sales, rural tractor demand',
                    'Retail': '↑ Seasonal shopping',
                    'Jewelry': '↑ Wedding season',
                }
            },
            
            'FINANCIAL_MARKETS': {
                'description': 'Market structure and flow-related events',
                'types': [
                    'FII/DII Flows (Foreign/domestic institutional money)',
                    'Index Rebalancing (Nifty/Sensex changes, MSCI)',
                    'Global Market Crash (US Fed decisions, recession fears)',
                    'Credit Ratings (Sovereign/corporate upgrades or downgrades)',
                ],
                'impact_level': 'MARKET-WIDE',
                'note': 'Affects all stocks through liquidity and sentiment'
            },
            
            'ESG_ENVIRONMENTAL': {
                'description': 'Environmental and sustainability factors',
                'types': [
                    'Carbon Emission Targets (Net-zero commitments)',
                    'Water Scarcity (Beverage, textile, paper companies)',
                    'Plastic Ban (FMCG packaging)',
                    'ESG Ratings (FII investment criteria)',
                ],
                'sector_impact': {
                    'Cement/Steel': '↓ High carbon footprint pressure',
                    'Power (Coal)': '↓ Emission penalties',
                    'Renewable Energy': '↑ ESG-driven capital',
                    'FMCG': '↓ Plastic packaging costs',
                }
            },
        }
    
    # COMPLETE METHODS FROM ORIGINAL
    def fetch_company_info(self, stock_symbol: str) -> Dict:
        """Fetch company information from yfinance or known database"""
        yf_symbol = f"{stock_symbol}.NS"
        
        if yf_symbol in self.known_companies:
            company_data = self.known_companies[yf_symbol]
            return {
                'symbol': stock_symbol,
                'yf_symbol': yf_symbol,
                'name': company_data['name'],
                'sector': company_data['sector'],
                'industry': company_data.get('industry', ''),
                'business_description': '',
                'source': 'Internal Database (High Accuracy)'
            }
        
        try:
            stock = yf.Ticker(yf_symbol)
            info = stock.info
            
            return {
                'symbol': stock_symbol,
                'yf_symbol': yf_symbol,
                'name': info.get('longName', info.get('shortName', stock_symbol)),
                'sector': info.get('sector', 'Unknown'),
                'industry': info.get('industry', ''),
                'business_description': info.get('longBusinessSummary', ''),
                'source': 'Yahoo Finance (Auto-classified)'
            }
        except Exception as e:
            return {
                'symbol': stock_symbol,
                'yf_symbol': yf_symbol,
                'name': stock_symbol,
                'sector': 'Unknown',
                'industry': '',
                'business_description': '',
                'source': 'Error fetching data'
            }
    
    def classify_sector(self, company_info: Dict) -> str:
        """Classify sector based on available information"""
        if company_info['source'] == 'Internal Database (High Accuracy)':
            return company_info['sector']
        
        # Fallback classification using keywords
        desc = (company_info['business_description'] + ' ' + 
                company_info['industry'] + ' ' + 
                company_info['sector']).lower()
        
        sector_keywords = {
            'Banking': ['bank', 'banking', 'financial services'],
            'IT Services': ['software', 'information technology', 'consulting', 'digital'],
            'Pharmaceuticals': ['pharmaceutical', 'drug', 'medicine', 'healthcare'],
            'Automobiles': ['automobile', 'vehicle', 'car', 'motorcycle'],
            'FMCG': ['consumer goods', 'fmcg', 'food', 'beverage'],
            'Chemicals': ['chemical', 'specialty chemical'],
            'Power': ['power', 'electricity', 'generation'],
            'Oil & Gas': ['oil', 'gas', 'petroleum', 'refinery'],
        }
        
        for sector, keywords in sector_keywords.items():
            if any(keyword in desc for keyword in keywords):
                return sector
        
        return company_info['sector']
    
    def get_export_domestic_split(self, sector: str) -> Dict:
        """Get export/domestic revenue split for a sector"""
        export_pct = self.export_percentages.get(sector, 15)
        domestic_pct = 100 - export_pct
        
        if export_pct >= 50:
            orientation = 'Export-Oriented'
        elif export_pct >= 30:
            orientation = 'Balanced (Mixed Export-Domestic)'
        else:
            orientation = 'Domestic-Focused'
        
        return {
            'export_percentage': export_pct,
            'domestic_percentage': domestic_pct,
            'orientation': orientation
        }
    
    def get_sector_connections(self, sector: str) -> Dict:
        """Get sector connections"""
        if sector in self.sector_connections:
            return self.sector_connections[sector]
        else:
            return {
                'supplies_to': ['Information not available in database'],
                'depends_on': ['Information not available in database'],
                'competes_with': ['Information not available in database']
            }
    
    def get_news_impact_for_sector(self, sector: str) -> List[str]:
        """Get sector-specific news impact items (FROM ORIGINAL)"""
        sector_news_detail = {
            'IT Services': [
                'US-India Trade Deals & Tariff Changes (85% export revenue)',
                'USD/INR Exchange Rate (stronger dollar = higher rupee revenue)',
                'AI/GenAI Disruption (threat to traditional services)',
                'H1B Visa Policy Changes (talent availability)',
                'US Fed Rate Decisions (client IT spending)',
            ],
            'Pharmaceuticals': [
                'USFDA Approvals/Warning Letters (export approval)',
                'Drug Pricing Control (NLEM additions impact margins)',
                'US-India Trade Agreements (60% export revenue)',
                'Pandemic/Health Crises (vaccine demand)',
                'Patent Expiries (generic opportunities)',
            ],
            'Banking': [
                'RBI Monetary Policy (repo rate, CRR, SLR)',
                'NPA Recognition Norms (asset quality)',
                'GDP Growth Data (credit growth)',
                'Real Estate Prices (loan book quality)',
                'Regulatory Changes (BASEL norms, digital banking)',
            ],
            'NBFC': [
                'RBI Interest Rates (borrowing cost)',
                'Credit Growth Data (demand indicator)',
                'Asset Quality Norms (NPA recognition)',
                'Funding Access (bank credit availability)',
                'Consumer Sentiment (retail loan demand)',
            ],
            'Automobiles': [
                'EV Policy & Subsidies (transition risk)',
                'Steel Prices (major input cost)',
                'Consumer Sentiment (discretionary purchase)',
                'Rural Demand/Monsoon (tractor sales)',
                'Emission Norms (BS standards compliance cost)',
            ],
            'Power': [
                'Coal Prices (major input for thermal)',
                'Renewable Energy Policy (transition pressure)',
                'Monsoon/Rainfall (hydro power generation)',
                'Industrial Growth (power demand)',
                'Environmental Regulations (emission norms)',
            ],
            'Oil & Gas': [
                'Crude Oil Prices (OPEC decisions, geopolitical events)',
                'Geopolitical Tensions (Middle East, Russia)',
                'Subsidy Policy (OMC margins)',
                'Renewable Energy Transition (long-term demand)',
                'Refinery Margins (GRM movements)',
            ],
            'Metals & Mining': [
                'Global Commodity Prices (steel, aluminum, copper)',
                'China Economic Data (major demand driver)',
                'Trade Wars/Tariffs (export competitiveness)',
                'Environmental Regulations (pollution control costs)',
                'Infrastructure Spending (domestic demand)',
            ],
            'FMCG': [
                'Monsoon Performance (rural demand - 40% revenue)',
                'Commodity Prices (edible oils, packaging)',
                'GST Rate Changes (price competitiveness)',
                'Consumer Sentiment (urban demand)',
                'Festive Season (Diwali, Christmas sales spike)',
            ],
            'Chemicals': [
                'Crude Oil Prices (feedstock cost)',
                'China Competition (pricing pressure)',
                'Anti-Dumping Duties (protection from imports)',
                'Environmental Regulations (compliance costs)',
                'USD/INR Rate (65% export revenue)',
            ],
            'Insurance': [
                'IRDAI Regulations (product approvals, commissions)',
                'Natural Disasters (claims payouts)',
                'Pandemic Events (health insurance claims)',
                'Interest Rates (investment income)',
                'Penetration Growth (government health schemes)',
            ],
            'Retail': [
                'Consumer Sentiment (discretionary spending)',
                'E-commerce Regulations (competition)',
                'Real Estate Rentals (store costs)',
                'Festive Season (seasonal demand spike)',
                'GST Rate Changes (pricing)',
            ],
            'Agrochemicals': [
                'Monsoon/Rainfall (farmer purchasing power)',
                'MSP Announcements (crop profitability)',
                'Pesticide Regulations (product approvals)',
                'Commodity Prices (raw materials)',
                'USD/INR Rate (75% export revenue)',
            ],
        }
        
        return sector_news_detail.get(sector, [
            'General economic news',
            'Policy changes',
            'Regulatory updates',
            'Sector trends'
        ])

# ==================== DATABASE FUNCTIONS ====================
@st.cache_data(ttl=300)
def load_database_symbols():
    """Load stock symbols from database"""
    if not DB_AVAILABLE:
        return []
    
    try:
        stocks = get_all_stocks()
        symbols = [str(stock.get('symbol', '')).strip().upper() 
                  for stock in stocks if stock.get('symbol')]
        return sorted(list(set(symbols)))
    except Exception as e:
        st.error(f"❌ Database error: {str(e)}")
        return []

# ==================== STREAMLIT UI ====================
def main():
    # Initialize session state
    if 'analysis_result' not in st.session_state:
        st.session_state.analysis_result = None
    if 'analysis_history' not in st.session_state:
        st.session_state.analysis_history = []
    
    # Header
    st.title("📊 NSE Stock Revenue & Sector Analyzer")
    st.markdown("**Enhanced with News Impact Analysis** - Analyzes sector connections, export/domestic split, and news sensitivity")
    
    # Sidebar
    with st.sidebar:
        st.header("🔍 Stock Selection")
        
        # Input method selection
        input_method = st.radio(
            "Choose input method:",
            ["Database Selection" if DB_AVAILABLE else "Manual Input", "Manual Input"],
            index=0
        )
        
        selected_symbol = None
        
        if input_method == "Database Selection" and DB_AVAILABLE:
            db_symbols = load_database_symbols()
            if db_symbols:
                selected_symbol = st.selectbox(
                    "Select stock from database:",
                    options=[""] + db_symbols,
                    format_func=lambda x: "-- Select a stock --" if x == "" else x
                )
            else:
                st.warning("No stocks found in database")
        else:
            selected_symbol = st.text_input(
                "Enter NSE Stock Symbol:",
                placeholder="e.g., TCS, INFY, RELIANCE, TIINDIA",
                help="Enter symbol without .NS suffix"
            ).strip().upper()
        
        st.markdown("---")
        analyze_btn = st.button("🔎 Analyze Stock", type="primary", use_container_width=True)
        
        if st.session_state.analysis_history:
            st.markdown("---")
            st.subheader("📜 Recent Analyses")
            for hist in st.session_state.analysis_history[-5:]:
                if st.button(f"📌 {hist}", use_container_width=True, key=f"hist_{hist}"):
                    selected_symbol = hist.replace('.NS', '')
                    analyze_btn = True
    
    # Main content
    if analyze_btn and selected_symbol:
        with st.spinner(f'🔄 Analyzing {selected_symbol}...'):
            analyzer = StockConnectionAnalyzer()
            company_info = analyzer.fetch_company_info(selected_symbol)
            
            if not company_info['name'] or company_info['name'] == selected_symbol:
                st.error(f"❌ Could not fetch detailed information for {selected_symbol}. Please verify the stock symbol is correct (NSE format)")
            else:
                primary_sector = analyzer.classify_sector(company_info)
                market_split = analyzer.get_export_domestic_split(primary_sector)
                connections = analyzer.get_sector_connections(primary_sector)
                sector_news = analyzer.get_news_impact_for_sector(primary_sector)
                
                result = {
                    'company_info': company_info,
                    'sector': primary_sector,
                    'market_split': market_split,
                    'connections': connections,
                    'sector_news': sector_news
                }
                
                st.session_state.analysis_result = result
                # Add to history
                symbol_with_ns = selected_symbol if selected_symbol.endswith('.NS') else f"{selected_symbol}.NS"
                if symbol_with_ns not in st.session_state.analysis_history:
                    st.session_state.analysis_history.append(symbol_with_ns)
                st.success(f"✅ Analysis complete for {selected_symbol}")
                st.rerun()
    
    # Display analysis results
    if st.session_state.analysis_result:
        result = st.session_state.analysis_result
        company_info = result['company_info']
        market_split = result['market_split']
        connections = result['connections']
        sector_news = result['sector_news']
        
        # Company Information Card
        st.markdown("### 📊 Company Information")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("Company Name", company_info['name'])
        with col2:
            st.metric("Symbol", company_info['symbol'])
        with col3:
            st.metric("Sector", result['sector'])
        
        col4, col5 = st.columns(2)
        with col4:
            if company_info['industry']:
                st.info(f"**Industry:** {company_info['industry']}")
        with col5:
            st.info(f"**Source:** {company_info['source']}")
        
        if company_info['business_description']:
            with st.expander("📝 Business Description"):
                desc = company_info['business_description']
                if len(desc) > 250:
                    st.write(desc[:250] + "...")
                else:
                    st.write(desc)
        
        st.markdown("---")
        
        # Market Orientation
        st.markdown("### 🌍 Market Orientation")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            # Create pie chart
            fig = go.Figure(data=[go.Pie(
                labels=['Export Revenue', 'Domestic Revenue'],
                values=[market_split['export_percentage'], market_split['domestic_percentage']],
                marker_colors=['#00D9FF', '#FF6B6B'],
                hole=.4,
                textinfo='label+percent',
                textposition='inside'
            )])
            
            fig.update_layout(
                title_text="Revenue Split",
                annotations=[dict(
                    text=market_split['orientation'].split(' ')[0], 
                    x=0.5, y=0.5, 
                    font_size=14, 
                    showarrow=False
                )],
                height=350,
                showlegend=True
            )
            
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            st.metric("Classification", market_split['orientation'])
            st.metric("Export Revenue", f"~{market_split['export_percentage']}%")
            st.metric("Domestic Revenue", f"~{market_split['domestic_percentage']}%")
            
            st.markdown("#### 💡 This company benefits from:")
            if market_split['export_percentage'] >= 50:
                st.success("• Lower US/EU tariffs (direct impact)\n• Strong USD/EUR (when rupee weakens)\n• Global demand trends")
            else:
                st.success("• Domestic GDP growth\n• Government infrastructure spending\n• Rising consumer demand in India")
        
        st.markdown("---")
        
        # Sector Connections
        st.markdown("### 🔗 Sector Connections")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("#### 📤 Supplies To")
            for idx, item in enumerate(connections['supplies_to'], 1):
                st.markdown(f"{idx}. {item}")
        
        with col2:
            st.markdown("#### 📥 Depends On")
            for idx, item in enumerate(connections['depends_on'], 1):
                st.markdown(f"{idx}. {item}")
        
        with col3:
            st.markdown("#### ⚔️ Competes With")
            for idx, item in enumerate(connections['competes_with'], 1):
                st.markdown(f"{idx}. {item}")
        
        st.markdown("---")
        
        # Trade Deal Impact
        st.markdown("### 📈 Trade Deal Impact")
        
        export_pct = market_split['export_percentage']
        
        if export_pct >= 50:
            st.success("✅ **HIGH IMPACT** - Significant export exposure")
            st.info("Direct beneficiary of tariff reductions")
        elif export_pct >= 30:
            st.warning("🟡 **MODERATE IMPACT** - Mixed business model")
        else:
            st.info("🔵 **LOW DIRECT IMPACT** - Primarily domestic")
        
        st.markdown("---")
        
        # NEWS IMPACT ANALYSIS (COMPLETE FROM ORIGINAL!)
        st.markdown("### 📰 News That Can Move This Stock")
        
        # Always Monitor section
        st.markdown("#### 🔴 ALWAYS MONITOR:")
        always_monitor = [
            "Quarterly Earnings & Corporate Actions",
            "RBI Interest Rate Changes (affects all sectors)",
            "Budget Announcements (tax, subsidies, spending)",
            "FII/DII Flows (market liquidity)"
        ]
        for item in always_monitor:
            st.markdown(f"• {item}")
        
        # Sector-specific high impact
        st.markdown(f"#### 🟠 HIGH IMPACT FOR THIS SECTOR ({result['sector']}):")
        for item in sector_news:
            st.markdown(f"• {item}")
        
        # What to track
        st.markdown("#### 💡 WHAT TO TRACK:")
        
        if export_pct >= 50:
            st.info("📊 **Key Metrics:** Export order data, USD/INR rate, freight costs\n\n"
                   "📰 **News Sources:** US Fed, trade policy, global demand indicators\n\n"
                   "⚠️ **Risk:** Global recession, trade wars, currency appreciation")
        elif export_pct >= 30:
            st.info("📊 **Key Metrics:** Mix of domestic GDP growth + export orders\n\n"
                   "📰 **News Sources:** Both RBI policy + US/global trade news\n\n"
                   "⚠️ **Risk:** Balanced - both domestic slowdown & global factors")
        else:
            st.info("📊 **Key Metrics:** Domestic GDP, credit growth, consumer confidence\n\n"
                   "📰 **News Sources:** RBI policy, Budget, monsoon, festive sales\n\n"
                   "⚠️ **Risk:** Domestic slowdown, inflation, interest rate hikes")
    
    else:
        # Welcome message
        st.info("👈 Select a stock from the sidebar to begin analysis")
        
        # Show examples
        with st.expander("💡 Example Stocks to Analyze"):
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.markdown("**Export-Oriented:**")
                st.markdown("- TCS (IT Services - 85%)")
                st.markdown("- SUNPHARMA (Pharma - 60%)")
                st.markdown("- AARTIIND (Chemicals - 65%)")
            
            with col2:
                st.markdown("**Balanced:**")
                st.markdown("- TATAMOTORS (Auto - 30%)")
                st.markdown("- RELIANCE (Oil & Gas - 25%)")
                st.markdown("- TATASTEEL (Metals - 35%)")
            
            with col3:
                st.markdown("**Domestic-Focused:**")
                st.markdown("- HDFCBANK (Banking - 1%)")
                st.markdown("- DLF (Real Estate - 5%)")
                st.markdown("- NTPC (Power - 8%)")
    
    # Footer
    st.markdown("---")
    st.caption(f"⚡ Powered by yfinance API | {'Database Integration Active' if DB_AVAILABLE else 'Manual Input Mode'} | Complete News Impact Analysis")

if __name__ == "__main__":
    main()