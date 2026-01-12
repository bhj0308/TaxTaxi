import pandas as pd
from fastapi import FastAPI
import requests

app = FastAPI()

# 1. US HTS CSV (download hts.usitc.gov/export)
us_hts = pd.read_csv("hts_2026.csv")  # HTS,Duty,Desc

# 2. Canada CARM API
@app.post("/tariff")
async def get_tariff(hs10: str, origin: str = "US", dest: str = "CA"):
    if dest == "US":
        row = us_hts[us_hts['HTS'].str.contains(hs10, na=False)]
        return {"duty": row['Duty'].iloc}



