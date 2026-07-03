from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

supabase.table("FIFA World Cup Goalkeeping - Live").delete().neq("Name", "").execute()
print("Cleared bad data.")