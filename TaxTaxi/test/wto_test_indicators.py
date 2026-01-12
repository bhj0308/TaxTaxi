# all_indicators.py
import requests
import json

key = "7f66927fa0a14b1dbd3a41379d31bbd7"
url = "http://api.wto.org/timeseries/v1/indicators"
params = {"key": key, "fmt": "json", "max": 0}  # 0 = ALL records

print("Fetching ALL indicators (10K+)...")
response = requests.get(url, params=params)
data = response.json()

print(f"TOTAL indicators: {len(data)}")

# Save to file
with open("all_wto_indicators.json", "w") as f:
    json.dump(data, f, indent=2)
print("Saved to all_wto_indicators.json")

# Tariff summary
tariff_codes = []
for item in data:
    if any(word in str(item).lower() for word in ['tariff', 'duty']):
        tariff_codes.append({
            "code": item.get("code"),
            "name": item.get("name"),
            "category": item.get("categoryLabel")
        })

print(f"\nTariff/Duty indicators: {len(tariff_codes)}")
for t in tariff_codes[:10]:
    print(f"  i={t['code']} - {t['name'][:80]} ({t['category']})")
