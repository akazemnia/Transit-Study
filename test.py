
import pandas as pd
import folium
import geopandas as gpd
import os
import requests
import zipfile
import io
import shutil

url_shp = "https://www2.census.gov/geo/tiger/TIGER2024/BG/tl_2024_24_bg.zip"
r = requests.get(url_shp, verify=False)
z = zipfile.ZipFile(io.BytesIO(r.content))
z.extractall("md_bg")
gdf = gpd.read_file("md_bg/tl_2024_24_bg.shp")

import requests

api_url = (
    "https://api.census.gov/data/2023/acs/acs5?"
    "get=NAME,B01003_001E&for=block%20group:*&in=state:24&in=county:*"
)

response = requests.get(api_url)
print("Status code:", response.status_code)
print("Response text:", response.text[:500])

response.raise_for_status()  # stop if error

data = response.json()
print(f"Number of rows: {len(data)}")