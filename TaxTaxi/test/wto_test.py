import requests

WTO_KEY = "7f66927fa0a14b1dbd3a41379d31bbd7"
url = "http://api.wto.org/timeseries/v1/data"



params = {
    "key": WTO_KEY,
    "i": "TP_A_0010",    # ✅ Simple average tariff
    "r": "124",           # ✅ Country name/ISO2 (not 842)
    "fmt": "json",
    "mode": "full"
    # No "p" param - this indicator is country-level only
}

print(f"Full URL: {requests.Request('GET', url, params=params).prepare().url}")

response = requests.get(url, params=params, timeout=10)
print(f"Status: {response.status_code}")
print(f"Response preview: {response.text[:500]}")

if response.status_code == 200:
    data = response.json()
    print("SUCCESS! Tariff data:", data[:2] if isinstance(data, list) else data)
else:
    print("ERROR details:", response.text)