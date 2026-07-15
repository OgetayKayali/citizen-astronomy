import json
import os
import sys

# Ensure we can import photometry_app
sys.path.append(os.getcwd())

file_path = "hr_working_table.json"
if not os.path.exists(file_path):
    file_path = os.path.join("Exports", "M46", "hr_working_table.json")

with open(file_path, "r") as f:
    raw_data = json.load(f)

# Structure is clearly { "working_table": { "rows": [...] } }
rows = raw_data.get("working_table", {}).get("rows", [])

# Sort by gaia_g_mag
top_10 = sorted([r for r in rows if r.get("gaia_g_mag") is not None], key=lambda x: x.get("gaia_g_mag"))[:10]

print("Top 10 brightest stars:")
for row in top_10:
    print(f"ID: {row.get('source_id')}, Name: {row.get('source_name')}, RA: {row.get('ra_deg')}, Dec: {row.get('dec_deg')}, G Mag: {row.get('gaia_g_mag')}")

all_gaia_dr3 = all(row.get("catalog") == "gaia-dr3" for row in top_10)
print(f"\nAll rows gaia-dr3: {all_gaia_dr3}")

try:
    from photometry_app.core.catalogs import fetch_catalog_target_details
    print("\nFetching details for the first 5:")
    for row in top_10[:5]:
        details = fetch_catalog_target_details(row)
        print(f"Details for {row.get('source_id')}: {details}")
except ImportError as e:
    print(f"\nError importing photometry_app: {e}")
except Exception as e:
    print(f"\nError fetching details: {e}")
