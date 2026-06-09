import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TABLE_NAME   = "FIFA World Cup Schedule - Live"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

dummy_data = [
    {"Date": "Jun 12", "Group": "Group A", "Fixture": "Mexico vs South Africa",           "Short Name": "MEX VS RSA", "Kick-off Time (IST)": "12:30 AM", "Results": "2 - 1"},
    {"Date": "Jun 12", "Group": "Group A", "Fixture": "Korea Republic vs Czechia",         "Short Name": "KOR VS CZE", "Kick-off Time (IST)": "7:30 AM",  "Results": "1 - 1"},
    {"Date": "Jun 13", "Group": "Group B", "Fixture": "Canada vs Bosnia and Herzegovina",  "Short Name": "CAN VS BIH", "Kick-off Time (IST)": "12:30 AM", "Results": "3 - 0"},
    {"Date": "Jun 13", "Group": "Group D", "Fixture": "USA vs Paraguay",                   "Short Name": "USA VS PAR", "Kick-off Time (IST)": "6:30 AM",  "Results": "1 - 0"},
    {"Date": "Jun 14", "Group": "Group B", "Fixture": "Qatar vs Switzerland",              "Short Name": "QAT VS SUI", "Kick-off Time (IST)": "12:30 AM", "Results": "0 - 2"},
    {"Date": "Jun 14", "Group": "Group C", "Fixture": "Brazil vs Morocco",                 "Short Name": "BRA VS MAR", "Kick-off Time (IST)": "3:30 AM",  "Results": "4 - 1"},
    {"Date": "Jun 14", "Group": "Group C", "Fixture": "Haiti vs Scotland",                 "Short Name": "HAI VS SCO", "Kick-off Time (IST)": "6:30 AM",  "Results": "0 - 3"},
    {"Date": "Jun 14", "Group": "Group D", "Fixture": "Australia vs Türkiye",              "Short Name": "AUS VS TUR", "Kick-off Time (IST)": "9:30 AM",  "Results": "1 - 2"},
]

response = supabase.table(TABLE_NAME).upsert(
    dummy_data,
    on_conflict="Fixture"
).execute()

print(f"✓ Pushed {len(dummy_data)} dummy rows successfully!")