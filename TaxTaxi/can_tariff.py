from fastapi import FastAPI, Query
from typing import Optional
import requests
from datetime import date

app = FastAPI()
BASE_URL = "https://ccapi-ipacc.cbsa-asfc.cloud-nuage.canada.ca/v1/tariff-srv/"

@app.get("/ca-tariff")
async def get_ca_tariff(
    hs10: str = Query(..., description="HS10 code like 8471300000"),
    as_of: Optional[str] = Query(date.today().isoformat())
):
    url = f"{BASE_URL}tariffClassifications(TariffNumber='{hs10}',AsOfDate='{as_of}')/to_customsDuties"
    resp = requests.get(url)
    
    if resp.status_code == 200:
        data = resp.json()
        return {
            "hs10": hs10,
            "duty": data.get("dutyRate", "Free"),
            "type": data.get("rateTypeCode")  # 1=Dollar, 3=%, 4=Free
        }
    return {"error": resp.status_code}

# Test: GET /ca-tariff?hs10=8471300000?as_of=2024-01-01?
